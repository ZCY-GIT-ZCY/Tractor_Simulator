# 本地托管的双升网页应用

服务运行在你的电脑，朋友使用手机、平板或 PC 浏览器访问同一个 HTTPS 入口。玩家无需安装 Python、APP 或隧道客户端。自对弈、PVE、四人 PVP 共用原裁判；PVE 仍使用随机策略占位。

## 最快启动：临时公网入口

1. 双击项目目录中的 `run-public.bat`。也可在 PowerShell 运行：

   ```powershell
   Set-Location 'D:\Research\Tractor\Simulator'
   .\run-public.bat
   ```

2. 首次启动会从 Cloudflare 官方 GitHub release 下载约 53 MB 的 `cloudflared`，SHA256 校验通过后执行。它保存在 `D:\Research\Tractor\dependencies\cloudflared`；没有系统安装步骤。
3. 等窗口显示 **公网入口已验证**。首次域名生效可能需数十秒，启动器最多等待 120 秒。入口同时保存到 `runtime/public-url.txt`，浏览器自动打开。
4. 选择“好友房间”，填写名字。房间号留空创建新房间；入座后点击“邀请牌友”，复制链接发给另外三人。邀请链接会自动填入房间号，三人只需填名字并入座。
5. 第四人入座自动发牌。0/2 为青队、1/3 为金队。第一个入座者决定初始等级和抢庄设置。

双击启动器时，如果已有同目录的公网服务，继续使用现有入口。已有 8765 本地教学牌桌时，临时公网模式会选择空闲端口，保留旧牌局；公网朋友应打开窗口给出的 HTTPS 入口。手动指定被占用端口则明确报错，可用 `run-public.bat --port 8766`。

服务器需要一直开机、联网并保持启动窗口运行。正常停止按 Ctrl+C；也可双击 `stop-public.bat`，它核对 PID、启动时间和 Python 程序路径后只停止本项目记录的公网服务。Windows job 确保隧道子进程也退出，关掉启动窗口不会留下独立的公网入口。

启动器只监听 `127.0.0.1`，通过 Cloudflare 的出站隧道转发这一个游戏端口，不需要公网 IPv4、路由器端口映射或玩家安装软件。临时域名每次重建隧道会变化；短期好友对局可用。Cloudflare 将 Quick Tunnel 定位为测试用途，没有可用性保证，并限制 200 个同时进行的请求。本应用使用普通 HTTP 长轮询，不使用临时隧道不支持的 SSE。见 [官方 Quick Tunnel 文档](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

## 固定入口：绑定自己的域名

如果希望网址始终相同，可以沿用同一个应用和启动器，换成固定 Cloudflare Tunnel：

1. 按 [Cloudflare 官方创建隧道流程](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/) 创建账户、添加自己的域名，并创建 remotely managed tunnel。
2. 添加 published application route，例如 `tractor.example.com`，Service URL 设为 `http://127.0.0.1:8765`。该端口必须与下方配置相同。
3. 将该 tunnel 的 **token 本身**存到 `runtime/tunnel-token.txt`，文件中只有 token 一行。不要存整条安装命令，也不要把 token 放进网址或公开发布包。UTF-8 有无 BOM 均可，启动器会移除 BOM 和首尾换行，为隧道创建临时副本，结束后删除副本。
4. 将 `internet.json` 复制为 `internet.local.json` 并编辑后者（Git 会忽略本地配置）：

   ```json
   {
     "mode": "named",
     "port": 8765,
     "max_rooms": 32,
     "protocol": "http2",
     "public_url": "https://tractor.example.com",
     "token_file": "runtime/tunnel-token.txt"
   }
   ```

5. 关闭原 8765 服务，再运行 `run-public.bat --config internet.local.json`。它用 `--token-file` 启动，不安装 Windows 服务；HTTPS 验证必须抵达**这个进程的实例**才会宣布入口可用。固定模式不自动换端口，避免与 Cloudflare route 不一致。

固定模式的登录、域名购买及 token 由服务器主人设置。它已接入启动器，但此次没有提供域名或 token，因此未实测这一模式。域名注册费用取决于所选域名；不需要租另一台机器。

## 手机与重连

- 手机竖屏显示完整牌桌、自己的手牌、当前级牌、主牌和闲家分数。长手牌可横向滑动；选牌后点击出牌，非法动作仍由原规则校验拒绝。桌规与邀请按钮可直接触摸。
- 页面刷新保留原座位和手牌；关闭后重新打开同一域名、同一浏览器，可点击“回到上次牌桌”。凭据保存在该浏览器，不跟着邀请链接传给朋友。
- 切到手机后台可能使网络暂停。PVP 玩家离线 25 秒后整桌暂停，原玩家重连后继续。主动“离开牌桌／离开房间”会释放座位，其他人可以接手。
- 同一浏览器同时打开四个标签用于测试时，各标签拥有独立会话；“回到上次牌桌”指本浏览器最近入座的那个会话。实际建议每人使用自己的浏览器。
- 所有人都停止访问超过两小时，房间及会话被清理；某一玩家在线则保留整桌。默认最多 32 个房间。房间号／邀请链接用于入场，知道它的人可占空位，请只发给预期牌友。
- 房间目前存于服务端内存。**服务器重启、停机或启动器关闭会丢失牌局**。重新打开浏览器可恢复的是仍运行的服务器中的座位；跨设备、跨临时域名不能自动恢复凭据。

## 局域网与其他部署

同一 Wi-Fi 内可双击 `run-lan.bat`，访问 `http://服务器局域网IP:8765`。Windows 防火墙如询问，应只允许所需的网络；脚本不会自动更改防火墙。局域网浏览器的剪贴板可能受 HTTP 限制，邀请弹窗允许直接选择和复制完整链接。

已有反向代理、FRP 或其他隧道时，也可以独立启动：

```powershell
& 'C:\Miniconda\python.exe' -m tractor_sim --headless --serve --host 127.0.0.1 --port 8765
```

将 HTTPS 域名的所有路径转发到这一个服务进程。保留 `Authorization` 头，支持至少 5 秒 HTTP 长轮询；静态资源与 API 均不要缓存。不同进程不共享房间，不能用多个独立 worker 随机分发同一房间请求。

源代码部署包为 `dist/TractorWeb.zip`。解压整个 `TractorWeb` 文件夹后从其中启动即可；包含 web 资源和本次准确规则副本，**不包含 Python、隧道二进制、任何玩家凭据或日志**。服务器需要已有 Python 3.10+。原目录优先使用已有 conda base，其他机器可用 PATH 上的 Python。建议解压到 D 盘；首次依赖下载仍位于解压目录上一层的 `dependencies`。

## 故障定位与验证

- `runtime/tunnel.log`：公网隧道日志；没连通不会显示“公网入口已验证”。连接可能受 DNS、代理或网络出站策略影响。启动器不关闭 TLS 验证。
- `runtime/public-url.txt`：本次验证通过的入口。正常停止后删除；强制终止时可能留下旧文件，以正在运行的窗口或重复启动检查为准。
- 若已有固定服务占端口，修改本地 `port` 和固定 tunnel route，或关闭相应服务。
- Python 和前端检查：`python -m unittest discover -s tests -q`、`node --check web/app.js`、`node --test tests/*.cjs`。
- 实际四客户端 HTTP 冒烟检查：`python tools/verify_web.py https://你的入口 --output docs/internet-validation.json`。它新建测试房间，验证四人入座、私牌、回合权限、旧版本拒绝与同步，最后主动释放测试席位。不会操作其他房间。
- 重新生成发布包：`python tools/package_web.py`。每个文件的 SHA256 保存在 ZIP 内 `manifest.json`，方便核对部署版本。
