# 部署与更新

游戏后端只需要 Python 3.10+，不需要 pip 安装任何库。HTML/CSS/JavaScript 资源、桌规副本、Enoch 桌规版 AI 和回放接口均随代码提供；运行时无需 Node、Rust、GPU、模型权重或上游 AI 源码。Node 只用于前端测试。

## 获取完整项目

```bash
git clone https://github.com/ZCY-GIT-ZCY/Tractor_Simulator.git
cd Tractor_Simulator
python -m tractor_sim
```

服务默认监听 `127.0.0.1:8765` 并打开浏览器，按 Ctrl+C 结束。Linux/macOS 上如命令是 `python3`，用它替换 `python`。也可在 [Releases](https://github.com/ZCY-GIT-ZCY/Tractor_Simulator/releases) 下载 `TractorWeb.zip`，完整解压后从 `TractorWeb` 目录启动。不要仅复制 `tractor_sim/`：`web/` 和 `rules/` 是网页运行所需资源。

Windows 本地练习可双击 `run.bat`。启动脚本优先使用已存在的 `C:\Miniconda\python.exe`，否则使用 PATH 上的 Python；项目本身可以放在任意有写入权限的目录，不要求开发者原来的 D 盘路径。

## 给异地玩家提供入口

Windows x64 双击 `启动双升联网游戏.bat`。首次启动会下载并校验 Cloudflare 官方隧道工具，保存到项目上一层的 `dependencies/cloudflared`，无需系统安装。连通验证后打开游戏首页，窗口显示 HTTPS 入口；没有域名也可以使用。

进入「好友房间」，确认座位后分享「邀请牌友」链接。其他人用手机或 PC 浏览器即可加入；人数不足可补本地 AI。服务电脑和启动窗口须持续运行。双击 `停止双升联网服务.bat` 或按 Ctrl+C 结束。临时入口在重建隧道后可能变化，固定域名及其他系统的隧道用法见 [internet.md](internet.md)。

## 部署到服务器

```bash
python -m tractor_sim --serve --headless --host 127.0.0.1 --port 8765
```

让现有 HTTPS 反向代理将所有路径转发到这个端口，保留 `Authorization` 请求头，不缓存 API，长轮询超时设置为至少 10 秒。前端、API 和邀请链接共用同一个域名。每个实例只运行一个游戏服务进程；房间状态在内存里，多个独立 worker 不共享同一桌。

只用于局域网时可改用 `--host 0.0.0.0`，同一网络内的玩家访问 `http://服务器IP:8765`；Windows 也可使用 `run-lan.bat`。服务器常驻运行时，可由现有 systemd、容器或进程管理器启动上述命令，将工作目录设为项目根目录，停止信号交由进程管理器处理。

## 存储与升级

- 已完成对局保存在 `runtime/history/`，历史列表会自动加载。可通过 `--history-dir /你的持久化目录` 指定其他位置。
- 房间、席位和未完成牌局只存在内存里，重启会清空。更新前先结束正在玩的对局。
- 固定隧道的私人配置放在 `internet.local.json`，凭据放在 `runtime/tunnel-token.txt`；这些运行文件不在源码或发布包中。
- Git 部署：停止该实例，备份 `runtime/` 和私人配置，执行 `git pull --ff-only`，再运行原启动命令。ZIP 部署：将新包解压到新目录，迁移历史目录和私人配置后启动。保留整个 `web/` 和 `rules/`。

## 验证与构建

```bash
python -m unittest discover -s tests -q
node --test tests/*.cjs
python tools/package_web.py
```

最后一个命令生成 `dist/TractorWeb.zip`，其中 `manifest.json` 记录每个文件的 SHA256。部署测试会将源码包解压到独立目录，验证页面、全部前端资源、规则、健康检查和历史接口，不依赖开发目录的其他文件。GitHub Actions 同时检查 Windows/Linux 的 Python 3.10/3.13 和前端测试；其中 Ubuntu/Python 3.13 构建的 ZIP 也会作为工作流产物保存。
