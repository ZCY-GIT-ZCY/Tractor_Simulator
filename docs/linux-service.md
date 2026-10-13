# Linux 常驻游戏与免域名公网入口

Desk 没有公网 IP 也能使用：`cloudflared` 从机器主动连接 Cloudflare，只转发本机游戏 HTTP 服务，不公开 SSH。用户保持机器开机联网即可，玩家使用同一 HTTPS 入口，在 PC 或手机浏览器中进入房间。

## 安装

需要 Linux、Python 3.10+、git 和可用的 systemd 用户会话。

```bash
git clone https://github.com/ZCY-GIT-ZCY/Tractor_Simulator.git ~/tractor
cd ~/tractor
python3 -m unittest discover -s tests -q
python3 tools/install_services.py
```

安装器只写入本用户的 `~/.config/systemd/user/`，不需要 sudo。默认游戏监听 `127.0.0.1:8765`，可加 `--host 0.0.0.0` 同时提供局域网访问。首次从 Cloudflare 官方 GitHub 下载固定版本 Windows/Linux x64 二进制并核对 SHA256，依赖保存在项目上一层 `dependencies/cloudflared/`，不安装到系统目录。安装器会重启本项目服务，先结束未完成的对局再运行。

`loginctl enable-linger 用户名` 允许开机启动和退出 SSH 后继续运行；安装器尝试启用，无权限时需管理员启用。在 Desk 上已启用。

## 两个独立服务

- `tractor.service`：游戏进程。开机启动，异常退出自动恢复。
- `tractor-tunnel.service`：独立隧道。掉线由 cloudflared 重连，进程退出后按 2～60 秒退避重试；不重启游戏服务。只有 HTTPS 健康检查返回当前游戏实例 ID，才写入可分享入口。

```bash
systemctl --user status tractor.service tractor-tunnel.service
cat ~/tractor/runtime/public-url.txt
journalctl --user -u tractor-tunnel.service -n 40 --no-pager
```

`runtime/public-url.txt` 是当前入口；游戏页面复制邀请链接会采用这一入口并加房间号。隧道日志 `runtime/tunnel.log` 超过 5 MiB 保留一份轮转备份。公网未验证成功或隧道停止时移除入口文件，避免把旧链接当成可用。`runtime/public-entry.json` 带游戏实例 ID，后端忽略与当前进程不匹配的旧入口。

只重启隧道不会清空服务端牌局，但新建 Quick Tunnel 后网址会改变。浏览器凭据不会跨域自动搬迁，新网址不能自动恢复原座位；实际有玩家时应避免主动重启隧道。稳定网址需要固定 Tunnel。普通网络掉线由 cloudflared 在同一进程内重连，通常保留原入口。

```bash
systemctl --user restart tractor-tunnel.service
systemctl --user restart tractor.service  # 会清空未完成牌局，保留已保存回放
systemctl --user stop tractor-tunnel.service tractor.service
```

免域名 Quick Tunnel 不保证可用率，每条隧道最多 200 个并发请求，重建后域名改变；这是 Cloudflare 官方的临时入口限制。长轮询适用于当前功能。需要固定网址时再配置有域名的固定 Tunnel，见 [公网说明](internet.md) 和 [Cloudflare 官方文档](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

## 更新流程

本地修改 → Python/前端测试与手机、PC 浏览器检查 → GitHub push → Desk `git pull --ff-only` → 远程回归 → 重启游戏服务。保留 `runtime/history/` 和所有私人配置。

```bash
cd ~/tractor
git pull --ff-only
python3 -m unittest discover -s tests -q
systemctl --user restart tractor.service
```

通常无须重启隧道：独立隧道会重新验证新的游戏实例，继续使用原来的公网网址。游戏房间目前保存在内存中，更新游戏进程需结束原来的未完成牌局。服务退出后已保存回放仍然保留；玩家浏览器能识别失效会话并重新入座。
