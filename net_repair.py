#!/usr/bin/env python3
# net_repair.py
# 网络修复工具（自治式）：一键全面检查并自动修复常见断网问题。
# Network Repair Tool (autonomous): one-click full check + auto fix for common
# "no internet" problems. Inspired by the "network repair" feature of security suites.
#
# 用法 / Usage:
#   py net_repair.py            # 图形界面 / GUI
#   py net_repair.py cli        # 命令行全检+自动修复 / CLI full check + auto fix
#
# 依赖 / Dependencies: 仅标准库 / stdlib only.

import os
import re
import sys
import time
import queue
import socket
import ctypes
import shutil
import winreg
import threading
import subprocess

# ----------------------------- 基础工具 / Basics -----------------------------

HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "drivers", "etc", "hosts")

DNS_PRIMARY = "223.5.5.5"      # 阿里公共 DNS / AliDNS
DNS_SECONDARY = "114.114.114.114"


def run(cmd, timeout=30):
    """执行命令（忽略编码差异）/ Run a command, GBK-decoded output."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        r.text = r.stdout.decode("gbk", "replace")
        return r
    except Exception as e:
        class R:
            returncode, text = 1, str(e)
        return R()


def service_running(name):
    """服务是否在运行 / Is the service running."""
    return "RUNNING" in run(["sc", "query", name]).text


def service_start_type(name):
    """服务启动类型代码 / Service start type code."""
    m = re.search(r"START_TYPE\s*:\s*(\d+)\s+(\S+)", run(["sc", "qc", name]).text)
    return (int(m.group(1)), m.group(2)) if m else (None, "")


def get_adapters():
    """枚举网卡 → [(名称, 状态, 管理状态)] / Enumerate adapters."""
    ps = ("Get-NetAdapter | ForEach-Object { '{0}|{1}|{2}' -f "
          "$_.Name, $_.Status, $_.AdminStatus }")
    r = run(["powershell", "-NoProfile", "-Command", ps], timeout=45)
    ads = []
    for line in r.text.splitlines():
        p = line.strip().split("|")
        if len(p) == 3:
            ads.append((p[0], p[1], p[2]))   # (name, Status, AdminStatus)
    return ads


def enable_adapter(name):
    """启用网卡 / Enable a network adapter."""
    r = run(["netsh", "interface", "set", "interface",
             f"name={name}", "admin=enabled"])
    return r.returncode == 0


def has_internet():
    """能否直连外网（DNS 解析 + TCP 连接）/ Direct internet reachable."""
    try:
        infos = socket.getaddrinfo("www.baidu.com", 443, proto=socket.IPPROTO_TCP)
        addr = infos[0][4][0]
        socket.create_connection((addr, 443), timeout=4).close()
        return True
    except OSError:
        return False


def resolve_ok():
    """DNS 能否解析域名 / Does DNS resolution work."""
    try:
        socket.getaddrinfo("www.baidu.com", None)
        return True
    except OSError:
        return False


# ----------------------------- 八项检查 / 8 checks -----------------------------
# 每项返回 (是否正常, 说明文字) / Each check returns (ok, detail).

def check_nic():
    ads = get_adapters()
    if not ads:
        return False, "未检测到任何网卡 / no adapter detected"
    up = [a for a in ads if a[1].lower() == "up"]
    disabled = [a for a in ads if a[2].lower() == "down"]
    if up:
        return True, f"网卡已连接 / connected: {up[0][0]}"
    if disabled:
        return False, f"网卡被禁用 / adapter disabled: {disabled[0][0]}"
    return False, "网线未插好或未连接 / cable unplugged or disconnected"


def fix_nic():
    fixed = []
    for name, status, admin in get_adapters():
        if admin.lower() == "down":
            if enable_adapter(name):
                fixed.append(name)
    time.sleep(3)
    return (bool(fixed), f"已启用 / enabled: {', '.join(fixed)}" if fixed
            else "无可启用的网卡 / nothing to enable")


def check_ip():
    r = run(["ipconfig"])
    ips = re.findall(r"IPv4[^\n:]*:\s*([0-9.]+)", r.text)
    good = [ip for ip in ips
            if not ip.startswith("169.254.") and ip != "0.0.0.0"]
    if good:
        return True, f"有效 IP / valid IP: {good[0]}"
    if ips:
        return False, f"仅获取到无效 IP（DHCP 可能失败）/ bad IP only: {ips[0]}"
    return False, "未获取到任何 IP / no IP obtained"


def fix_ip():
    run(["ipconfig", "/release"], timeout=40)
    run(["ipconfig", "/renew"], timeout=60)
    time.sleep(2)
    return check_ip()[:1] + ("已重新获取 IP / IP renewed",)


def check_dhcp():
    if service_running("Dhcp"):
        return True, "DHCP 服务运行中 / Dhcp service running"
    code, tag = service_start_type("Dhcp")
    if code == 4:
        return False, "DHCP 服务被禁用 / Dhcp service disabled"
    return False, "DHCP 服务未运行 / Dhcp service not running"


def fix_dhcp():
    run(["sc", "config", "Dhcp", "start=", "auto"])
    run(["net", "start", "Dhcp"], timeout=30)
    time.sleep(2)
    return check_dhcp()[:1] + ("已尝试启动 Dhcp 服务 / Dhcp start attempted",)


def check_dns():
    if not service_running("Dnscache"):
        return False, "DNS 缓存服务未运行 / Dnscache service not running"
    if not resolve_ok():
        return False, "无法解析域名 / cannot resolve domains"
    return True, "DNS 服务正常 / DNS service OK"


def fix_dns():
    run(["net", "start", "Dnscache"], timeout=30)
    run(["ipconfig", "/flushdns"])
    time.sleep(1)
    if resolve_ok():
        return True, "已刷新 DNS 缓存，解析恢复 / DNS cache flushed, OK"
    # 解析仍失败 → 换成公共 DNS / still failing → set public DNS
    ads = [a for a in get_adapters() if a[1].lower() == "up"]
    if not ads:
        return False, "无活动网卡，无法设置 DNS / no active adapter"
    name = ads[0][0]
    run(["netsh", "interface", "ip", "set", "dns",
         f"name={name}", "source=static", f"addr={DNS_PRIMARY}"])
    run(["netsh", "interface", "ip", "add", "dns",
         f"name={name}", f"addr={DNS_SECONDARY}", "index=2"])
    time.sleep(2)
    return (resolve_ok(),
            f"已设置公共 DNS {DNS_PRIMARY} / public DNS set" if resolve_ok()
            else "设置公共 DNS 后仍无法解析 / still failing")


HOSTS_DEFAULT = (
    "# Copyright (c) 1993-2009 Microsoft Corp.\n"
    "#\n"
    "# 本文件由系统保留，勿删除 / This file is kept by the system.\n"
    "127.0.0.1 localhost\n"
    "::1 localhost\n"
)

HOSTS_BLOCKED = re.compile(
    r"^\s*(?:0\.0\.0\.0|127\.0\.0\.1)\s+\S*(baidu|qq\.com|taobao|jd\.com|"
    r"microsoft|windowsupdate|github|apple)\S*", re.I)


def check_hosts():
    if not os.path.isfile(HOSTS_PATH):
        return False, "HOSTS 文件丢失 / hosts file missing"
    try:
        data = open(HOSTS_PATH, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        return False, f"HOSTS 无法读取 / unreadable: {e}"
    if len(data) > 100 * 1024:
        return False, f"HOSTS 异常增大 / oversized: {len(data)} bytes"
    bad = [ln for ln in data.splitlines() if HOSTS_BLOCKED.match(ln)]
    if bad:
        return False, f"HOSTS 疑似劫持 {len(bad)} 条 / {len(bad)} hijack lines"
    return True, "HOSTS 文件正常 / hosts file OK"


def fix_hosts():
    bak = HOSTS_PATH + ".netrepair.bak"
    try:
        shutil.copy2(HOSTS_PATH, bak)
        open(HOSTS_PATH, "w", encoding="utf-8").write(HOSTS_DEFAULT)
        run(["ipconfig", "/flushdns"])
        return True, f"已重置 HOSTS（备份: {os.path.basename(bak)}）/ hosts reset"
    except OSError as e:
        return False, f"重置失败 / reset failed: {e}"


def check_lsp():
    r = run(["netsh", "winsock", "show", "catalog"])
    if r.returncode != 0 or "TCP" not in r.text.upper():
        return False, "Winsock 目录异常 / winsock catalog broken"
    return True, "LSP 协议正常 / LSP OK"


def fix_lsp():
    r = run(["netsh", "winsock", "reset"])
    if r.returncode == 0:
        return True, "Winsock 已重置，需重启电脑 / reset OK, reboot required"
    return False, "重置失败 / reset failed"


def check_proxy():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion"
                             r"\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
    except OSError:
        return True, "未使用代理 / no proxy"
    if not enable:
        return True, "未使用代理 / no proxy"
    try:
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        server = ""
    if server and ":" in server:
        host, _, port = server.rpartition(":")
        try:
            socket.create_connection((host, int(port)), timeout=3).close()
            return True, f"代理可用 / proxy alive: {server}"
        except OSError:
            return False, f"代理无法连接 / proxy dead: {server}"
    return False, "代理配置异常 / bad proxy config"


def fix_proxy():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion"
                             r"\Internet Settings", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        return True, "已关闭 IE 代理 / IE proxy disabled"
    except OSError as e:
        return False, f"关闭代理失败 / disable failed: {e}"


REQUIRED_PATH = [
    r"%SystemRoot%\system32",
    r"%SystemRoot%",
    r"%SystemRoot%\System32\Wbem",
    r"%SYSTEMROOT%\System32\WindowsPowerShell\v1.0" + "\\",
]
REQUIRED_VARS = ["SystemRoot", "windir", "ComSpec", "PATHEXT", "PATH"]


def _system_path():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control"
                             r"\Session Manager\Environment")
        v, _ = winreg.QueryValueEx(key, "Path")
        return v
    except OSError:
        return ""


def check_env():
    missing = [v for v in REQUIRED_VARS
               if not os.environ.get(v)]
    sp = _system_path()
    if not sp:
        return False, "读不到系统 PATH / system PATH unreadable"
    miss_path = [p for p in REQUIRED_PATH
                 if p.lower().strip("%").replace("%systemroot%", "c:\\windows")
                 not in sp.lower().replace("%%", "%")]
    # 简化判定：展开后逐项比对 / simple expanded comparison
    exp = sp.replace("%SystemRoot%", os.environ.get("SystemRoot", "C:\\Windows"))
    exp = exp.replace("%SYSTEMROOT%", os.environ.get("SystemRoot", "C:\\Windows"))
    miss_path = [p for p in REQUIRED_PATH
                 if p.replace("%SystemRoot%", os.environ.get("SystemRoot", "C:\\Windows"))
                 .replace("%SYSTEMROOT%", os.environ.get("SystemRoot", "C:\\Windows"))
                 .rstrip("\\").lower() not in [x.rstrip("\\").lower() for x in exp.split(";")]]
    if missing or miss_path:
        msg = []
        if missing:
            msg.append("缺失变量 / missing vars: " + ",".join(missing))
        if miss_path:
            msg.append("PATH 缺失 / missing PATH: " + ",".join(miss_path))
        return False, "; ".join(msg)
    return True, "环境变量正常 / environment OK"


def fix_env():
    sp = _system_path()
    if not sp:
        return False, "无法读取系统 PATH / cannot read system PATH"
    exp = sp.replace("%SystemRoot%", os.environ.get("SystemRoot", "C:\\Windows"))
    exp = exp.replace("%SYSTEMROOT%", os.environ.get("SystemRoot", "C:\\Windows"))
    have = [x.rstrip("\\").lower() for x in exp.split(";")]
    add = [p for p in REQUIRED_PATH
           if p.replace("%SystemRoot%", os.environ.get("SystemRoot", "C:\\Windows"))
           .replace("%SYSTEMROOT%", os.environ.get("SystemRoot", "C:\\Windows"))
           .rstrip("\\").lower() not in have]
    if not add:
        return True, "系统 PATH 已完整 / system PATH complete"
    new = sp.rstrip(";") + ";" + ";".join(add)
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control"
                             r"\Session Manager\Environment", 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new)
        return True, f"已补全 {len(add)} 项 PATH / PATH fixed"
    except OSError as e:
        return False, f"修复失败（需管理员）/ fix failed: {e}"


# 任务表 / registry: (中文名, 英文名, 描述, 检查, 修复)
ITEMS = [
    ("网络硬件配置", "Hardware",    "检查网线是否插好、网卡是否启用",        check_nic,   fix_nic),
    ("网络连接配置", "IP Config",   "检查网卡相关设置是否正确",              check_ip,    fix_ip),
    ("DHCP 服务",    "DHCP",        "检查 DHCP 服务是否正常",                check_dhcp,  fix_dhcp),
    ("DNS 服务",     "DNS",         "检查 DNS 服务是否正常",                 check_dns,   fix_dns),
    ("HOSTS 文件",   "HOSTS",       "检查 HOSTS 文件配置是否正常",           check_hosts, fix_hosts),
    ("LSP 协议",     "LSP",         "检查 LSP 是否正确，避免 LSP 协议被劫持", check_lsp,   fix_lsp),
    ("IE 代理",      "IE Proxy",    "若设置了 IE 代理服务器，检查是否能访问网络", check_proxy, fix_proxy),
    ("环境变量",     "Env Vars",    "检查系统环境变量是否正常",              check_env,   fix_env),
]

# 状态常量 / status constants
PENDING, CHECKING, OK, FIXED, FAILED, REBOOT = (
    "待检查 Pending", "检查中 Checking...", "正常 OK",
    "已修复 Fixed", "修复失败 Fix failed", "需重启 Reboot")

COLORS = {OK: "#4caf50", FIXED: "#ffb74d", FAILED: "#ef5350",
          CHECKING: "#ffee58", PENDING: "#9e9e9e", REBOOT: "#42a5f5"}


# ----------------------------- 权限 / Admin -----------------------------

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def try_elevate():
    """尝试以管理员重启自身；成功则退出当前进程 / Relaunch as admin."""
    if is_admin():
        return True
    args = " ".join(f'"{a}"' for a in sys.argv[1:])
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            f'"{os.path.abspath(__file__)}" {args}', None, 1)
    except Exception:
        ret = -1
    if ret > 32:
        sys.exit(0)
    return False


# ----------------------------- 检查引擎 / Engine -----------------------------

def run_item(idx, report, admin):
    """单项：检查 → (必要时)自动修复 → 复检 / Check, auto-fix, re-check."""
    name_zh, name_en, _, check, fix = ITEMS[idx]
    report(("status", idx, CHECKING))
    ok, detail = check()
    if ok:
        report(("status", idx, OK, detail))
        return "ok"
    report(("log", f"  [!] {name_zh} / {name_en}: {detail}"))
    if not admin:
        report(("status", idx, FAILED, "需管理员修复 / admin required"))
        return "fail"
    report(("log", f"  -> 自动修复 {name_zh} / auto-fixing {name_en} ..."))
    try:
        fixed, msg = fix()
    except Exception as e:
        fixed, msg = False, f"修复异常 / fix error: {e}"
    ok2, detail2 = check()
    if fixed and ok2:
        report(("status", idx, FIXED, f"{detail2}"))
        return "fixed"
    if fixed and not ok2 and "reboot" in msg.lower():
        report(("status", idx, REBOOT, msg))
        return "fixed"
    if fixed:
        report(("status", idx, FIXED, msg))
        return "fixed"
    report(("status", idx, FAILED, f"{msg}"))
    return "fail"


def run_all(report, admin):
    """全检 + 自动修复 / Full check with autonomous fix."""
    report(("busy", True))
    report(("log", "=== 全面检查开始 / Full check started ==="))
    counts = {"ok": 0, "fixed": 0, "fail": 0}
    for i in range(len(ITEMS)):
        counts[run_item(i, report, admin)] += 1
    online = has_internet()
    verdict = ("外网连通 / internet reachable" if online
               else "外网仍不通（部分问题需重启后复查）/ still offline")
    summary = (f"正常 {counts['ok']} · 已修复 {counts['fixed']} · "
               f"失败 {counts['fail']} | {verdict} / "
               f"OK {counts['ok']} · Fixed {counts['fixed']} · "
               f"Failed {counts['fail']} | {verdict}")
    report(("log", f"=== 检查完成 / finished: {summary} ==="))
    report(("done", summary, online))
    report(("busy", False))


# ----------------------------- 图形界面 / GUI -----------------------------

def hide_console():
    """隐藏控制台黑窗（仅 GUI 模式调用）/ Hide console window (GUI mode only)."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def gui():
    hide_console()  # GUI 模式隐藏黑窗 / hide console in GUI mode
    import tkinter as tk
    from tkinter import font as tkfont

    BG, CARD, FG = "#1e1f22", "#2b2d30", "#e6e6e6"
    root = tk.Tk()
    root.title("网络修复工具 - Network Repair Tool")
    root.configure(bg=BG)
    root.geometry("780x560")
    root.minsize(720, 500)

    q = queue.Queue()
    busy = {"v": False}
    admin = {"v": is_admin()}

    def report(msg):
        q.put(msg)

    # 顶部 / header
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=20, pady=(16, 8))
    tk.Label(head, text="检测并修复断网问题",
             font=(tkfont.nametofont("TkDefaultFont").actual()["family"], 16, "bold"),
             fg=FG, bg=BG).pack(side="left")
    tk.Label(head, text="  Detect & fix network issues (autonomous)",
             fg="#9e9e9e", bg=BG).pack(side="left", pady=(6, 0))
    btn = tk.Button(head, text="全面检查", command=lambda: start_all(),
                    bg="#e6a23c", fg="#1e1f22", relief="flat",
                    font=("", 11, "bold"), padx=18, pady=4, cursor="hand2")
    btn.pack(side="right")

    tk.Label(root, text="自动检测并修复；修复动作需要管理员权限。/ "
                        "Auto check & fix; repairs require administrator.",
             fg="#9e9e9e", bg=BG).pack(anchor="w", padx=22)

    # 状态行 / rows
    rows = []
    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=16, pady=8)
    for i, (zh, en, desc, _, _) in enumerate(ITEMS):
        card = tk.Frame(body, bg=CARD)
        card.pack(fill="x", pady=3)
        tk.Label(card, text=f"{zh}  {en}", width=22, anchor="w",
                 fg=FG, bg=CARD, font=("", 10, "bold")).pack(side="left", padx=(12, 4), pady=8)
        tk.Label(card, text=desc, anchor="w", fg="#b0b0b0",
                 bg=CARD).pack(side="left", fill="x", expand=True)
        st = tk.Label(card, text=PENDING, width=22, anchor="e",
                      fg=COLORS[PENDING], bg=CARD)
        st.pack(side="right", padx=12)
        rows.append(st)

        def on_click(event, idx=i):
            if not busy["v"]:
                threading.Thread(target=run_item,
                                 args=(idx, report, admin["v"]),
                                 daemon=True).start()

        for w in (card,):
            w.bind("<Button-1>", on_click)

    # 日志 / log
    logbox = tk.Text(root, height=7, bg="#141517", fg="#c8c8c8",
                     relief="flat", state="disabled", font=("Consolas", 9))
    logbox.pack(fill="x", padx=16, pady=(0, 6))

    statusbar = tk.Label(root, text="就绪 / Ready", anchor="w",
                         fg="#9e9e9e", bg=BG)
    statusbar.pack(fill="x", padx=20, pady=(0, 10))

    def set_status(i, text, color, detail=""):
        st = rows[i]
        st.config(text=text if not detail else f"{text}  ·  {detail[:40]}",
                  fg=COLORS.get(text, color))

    def poll():
        try:
            while True:
                msg = q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    _, i, st = msg[0], msg[1], msg[2]
                    detail = msg[3] if len(msg) > 3 else ""
                    set_status(i, st, "#ffffff", detail)
                elif kind == "log":
                    logbox.config(state="normal")
                    logbox.insert("end", msg[1] + "\n")
                    logbox.see("end")
                    logbox.config(state="disabled")
                elif kind == "done":
                    statusbar.config(text=msg[1],
                                     fg="#4caf50" if msg[2] else "#ef5350")
                elif kind == "busy":
                    busy["v"] = msg[1]
                    btn.config(state="disabled" if msg[1] else "normal")
        except queue.Empty:
            pass
        root.after(120, poll)

    def start_all():
        for st in rows:
            st.config(text=PENDING, fg=COLORS[PENDING])
        threading.Thread(target=run_all, args=(report, admin["v"]),
                         daemon=True).start()

    if not admin["v"]:
        statusbar.config(text="⚠ 未以管理员运行，只能检查不能修复 / not admin, fixes disabled",
                         fg="#ef5350")
    poll()
    root.mainloop()


# ----------------------------- 命令行 / CLI -----------------------------

def cli():
    admin = is_admin()

    def report(msg):
        if msg[0] == "status":
            _, i, st = msg[0], msg[1], msg[2]
            detail = msg[3] if len(msg) > 3 else ""
            zh, en = ITEMS[i][0], ITEMS[i][1]
            line = f"{zh} / {en}: {st}"
            if detail:
                line += f"  ({detail})"
            print("  " + line)
        elif msg[0] == "log":
            print(msg[1])
        elif msg[0] == "done":
            print(msg[1])

    if not admin:
        print("⚠ 未以管理员运行，只能检查不能修复 / not admin, fixes disabled")
    run_all(report, admin)


def main():
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("cli", "--cli", "auto"):
        if not is_admin():
            try_elevate()          # CLI 也请求提权 / CLI asks elevation too
        cli()
    else:
        if not is_admin():
            try_elevate()          # 修复需要管理员 / repairs need admin
        gui()


if __name__ == "__main__":
    main()
