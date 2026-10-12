# 第三方依赖

游戏裁判、房间服务和前端使用 Python 标准库与浏览器内置能力，无第三方运行库。

PVE 的 **Enoch 桌规版**参考并移植 [tgoh-ots/shengji-tractor](https://github.com/tgoh-ots/shengji-tractor) 的 MIT 开源策略，固定参考提交 `21aa754d5128da8f62a7218c00d97715757240d0`。主要来源为 `core/src/bot/heuristics.rs`、`policy.rs`、`search.rs` 和 `determinize.rs`。原始许可全文保留在 [licenses/enoch-MIT.txt](licenses/enoch-MIT.txt)，Copyright (c) 2020 Robert Ying。Python 移植及本桌规修改见 [docs/ai.md](docs/ai.md)。不打包、运行原项目的 Rust 裁判或训练权重，不沿用其全知 AI 模式。参考源文件保存在 `D:/Research/Tractor/dependencies/enoch-source/`，游戏运行不依赖此目录。

公网启动器使用 Cloudflare 官方维护的 [cloudflared](https://github.com/cloudflare/cloudflared)，采用 [Apache License 2.0](https://github.com/cloudflare/cloudflared/blob/master/LICENSE)。当前固定版本为 2026.9.3，Windows x64 文件 SHA256 为 `f096265ec2fcbe9bb6e2d64268db167ced3fcbb83d894bdb9e2fcdb26f2ea7e2`，对应 GitHub 官方 release asset digest。首次启动下载并校验后才执行。

发布 ZIP 不含该二进制、隧道 token 或其他运行日志。依赖下载到解压目录的上一层 `dependencies/cloudflared`；原始工作目录下对应 `D:/Research/Tractor/dependencies/cloudflared`。不会安装 Windows 服务、修改注册表、修改系统防火墙或在 C 盘安装软件。

Cloudflare 临时隧道属于在线服务，应按其服务条款使用。公网启动需要服务器能够连接 Cloudflare；玩家只需普通浏览器。
