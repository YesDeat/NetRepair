# NetRepair

自治式网络修复工具：一键全面检查并自动修复常见断网问题，不需要你懂网络原理。Python + tkinter 实现
An autonomous network repair tool: one-click full check and **automatic fix** for common "no internet" problems — no networking knowledge needed. Built with Python + tkinter, UI similar to security suites' network-repair feature.

## 检查项 / Check items

| 项目 / Item | 说明 / Description | 自动修复 / Auto fix |
| --- | --- | --- |
| 网络硬件配置 / Hardware | 网线是否插好、网卡是否启用 | 启用被禁用的网卡 |
| 网络连接配置 / IP Config | 是否获取到有效 IP（检测 169.254 自分配地址） | 重新 DHCP 获取 IP |
| DHCP 服务 / DHCP | Dhcp 服务是否运行/被禁用 | 自动启动并设为自动启动 |
| DNS 服务 / DNS | Dnscache 服务 + 域名解析测试 | 刷新缓存，失败则自动换公共 DNS（223.5.5.5 / 114.114.114.114） |
| HOSTS 文件 / HOSTS | 是否丢失、异常增大、被劫持屏蔽常见网站 | 备份后重置为默认 hosts |
| LSP 协议 / LSP | Winsock 目录是否异常 | `netsh winsock reset`（需重启电脑生效） |
| IE 代理 / IE Proxy | 代理是否设置、代理服务器是否能连上 | 自动关闭失效代理 |
| 环境变量 / Env Vars | 系统关键变量与 PATH 是否完整 | 自动补全缺失的 PATH 项 |

## 依赖 / Dependencies

仅 Python 标准库（tkinter / ctypes / winreg），无需安装任何第三方包。

Stdlib only (tkinter / ctypes / winreg), no third-party packages.

## 用法 / Usage

1. 图形界面 / GUI：`py net_repair.py`
2. 命令行（全检 + 自动修复）/ CLI：`py net_repair.py cli`

> 注：本机 `python` 可能指向 Store 存根，用 `py` 启动器即可。/ Use the `py` launcher.

- 启动时会自动请求管理员权限（UAC），拒绝则只能检查不能修复。
  The app requests admin (UAC) on launch; without it, checks run but fixes are disabled.
- 界面中每一行都可以**单独点击**，只检查/修复那一项。
  Each row in the GUI is clickable to check/fix that single item.
- 「全面检查」跑完 8 项后会在底部给出汇总和最终的外网连通判定。
  After the full check, a summary and final internet verdict appear at the bottom.

## 注意 / Notes

- DNS 被劫持时会自动改写活动网卡的 DNS 为公共 DNS；如需自定义，请先手动记录原配置。
  If DNS is hijacked, the active adapter's DNS is switched to public DNS; note your original settings first if you need them.
- LSP 重置后需要重启电脑才能生效。
  After `winsock reset`, a reboot is required.
- HOSTS 重置前会自动备份为 `hosts.netrepair.bak`。
  The original hosts file is backed up as `hosts.netrepair.bak` before reset.
