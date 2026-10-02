# 双升 · Tractor Simulator

本地和服务器均可运行的双升仿真器。唯一桌规是 [双升游戏规则.md](rules/双升游戏规则.md)，原录音整理稿备份在 `docs/双升游戏规则.录音原版.md`。没有使用 Botzone 的裁判，也没有复用其他仓库的环境代码。牌面、桌面和动画直接用 HTML/CSS/SVG 绘制，无外部字体、图片或 CDN。

## 启动

需要 Python 3.10 或更新版本，无第三方运行依赖。已使用本机 conda base 的 `C:\Miniconda\python.exe` 验证。游戏本身没有第三方运行库；公网模式的隧道工具保存在 D 盘依赖目录。请从源代码目录启动，无须 pip 安装。

```powershell
Set-Location 'D:\Research\Tractor\Simulator'
& 'C:\Miniconda\python.exe' -m tractor_sim
```

默认在 `http://127.0.0.1:8765` 启动并打开浏览器。Ctrl+C 停止服务。也可运行 `./run.ps1`，或双击 `run.bat`。

在其他电脑上，下载并解压仓库或 `git clone` 后进入项目目录，运行 `python -m tractor_sim` 即可。网页和规则已随仓库收录；Windows 启动脚本优先使用已有 conda base，否则使用 PATH 上的 Python。开发目录旁有原始 `双升游戏规则.md` 时优先读取它，否则读取 `rules/` 内的副本。

重复运行或双击启动脚本时，会先识别占用该端口的服务：若是同一监听地址的双升服务，则打开已有牌桌并保留现有牌局；若是其他程序，则提示换端口，不停止任何进程。另起一桌服务可传 `--port 8766`。

纯仿真、完整升级比赛及可验证重放：

```powershell
& 'C:\Miniconda\python.exe' -m tractor_sim --headless --episodes 100 --seed 42
& 'C:\Miniconda\python.exe' -m tractor_sim --headless --episodes 3 --full-match --seed 42 --output docs/run.json
& 'C:\Miniconda\python.exe' -m tractor_sim --replay docs/run.json
```

`--level 11` 表示初始 J；`--no-auction --banker 2` 表示不抢庄、2 座坐庄。网页对局的配置在入座面板选择，CLI 的这三个参数只配置 headless rollout。

摸完 25 张后进入拿底前确认，询问一圈是否反主或加固；成功亮出更强牌者直接拿底。扣底后的反底则始终拿最近一次扣下的八张牌。

## 牌桌操作

- **自对弈**：四家明牌，底部显示当前行动者的可操作手牌，侧面与上方显示另外三家。可替所有人亮主、扣底、出牌；侧面长手牌可滚动。“随机一步”执行当前行动，“托管本局”让四家随机走完，随时可暂停。跟牌只有一种合法牌面组合时自动预选，玩家点击“出牌”确认；有多种选择则手动选牌。悬停手牌只上移，保留原来的叠放顺序。副牌花色尽量黑红交替，主花色在右侧、常主更靠右；出完某门后自动按剩余花色重排。摸牌时右侧显示本次摸到的单张，窄屏移到手牌旁；PVE/PVP 只显示本人的摸牌，其他人摸牌时清空。
- **PVE**：选择自己座位，只看本人手牌；其他三家由 RandomPolicy 操作，轮到自己自动停下。随机策略是可替换的占位策略，不代表训练后的 AI 水平。
- **PVP**：相同房间号在**同一服务器**进入同桌，通过邀请链接或相同房间号，按 0、1、2、3 入座，第四人加入自动发牌。0/2 与 1/3 同队；首位入座者配置本场，后进入者沿用该配置。房主负责开始下一局。

选牌点击一次选中，再点击取消；Enter 提交，Esc 清空。发牌中“自动摸牌”只自动选择不亮，选牌会暂停它，随时可以亮主。扣底须选八张。

所有出牌都由服务端预校验。非法跟门、跟型、张数、混门领出、非法亮主直接禁用提交。**甩牌没有成败预测**：同门甩牌允许提交，甩错时才罚本次尝试总张数 ×10，并强制实际出最小单张或对子。预校验和合法动作枚举不查看其他隐藏手牌来过滤甩牌。

底牌不明示，自对弈和局末也一样；仅无人亮主、翻底定主时公开。拿底时八张牌收入手中，玩家仍能知道自己拿过和扣过的牌。局末显示抓分、底分倍率、甩牌罚分净值、两队升级、J/A 降级和下一位庄家。必打记录按队永久保留。关闭结算后可点击牌桌左上“本局结算”再次打开。

## 网页多人联机

双击 `run-public.bat` 启动本地服务与 HTTPS 公网隧道，朋友无需下载，在手机或 PC 浏览器打开同一入口即可。好友房间留空创建新房间，点击“邀请牌友”分享带房间号的链接，满四人自动开始。

临时入口每次重建会变化；固定域名模式也已接入 `internet.json`。详细启动、重连、域名配置和故障处理见 [公网使用说明](docs/internet.md)。发布包位于 `dist/TractorWeb.zip`，包含前后端和准确规则副本；用 `python tools/package_web.py` 重建。

## 服务器运行

在 Linux 或 Windows 的源目录运行：

```bash
python -m tractor_sim --serve --headless --host 0.0.0.0 --port 8765
```

局域网玩家访问 `http://服务器局域网IP:8765`。公网使用反向代理提供 HTTPS，再将流量转发到本机监听端口；同桌玩家共享同一个服务器进程。标准库多线程 HTTP + 最长 5 秒的长轮询，不需要 WebSocket、数据库或 GPU。不要把四位玩家分配到互不共享状态的多个服务进程。

房间号是入场口令，不是用户账号。客户端用随机 Bearer token 保留座位，页面刷新可恢复，重新打开同一浏览器也可点击回到上次牌桌；PVP 断线 25 秒后暂停，重连继续。主动离开释放座位，新人可接手该座位。房间和会话保存在内存，**服务器重启后清空**；本版未实现账号、永久战绩或跨进程房间。已有临时 HTTPS 公网联机实测；固定域名配置需自行提供 domain 与 tunnel token。

## Python 接口

```python
from tractor_sim import TractorEnv, GameConfig, RandomPolicy

env = TractorEnv(GameConfig(initial_level=6, auction=False, banker=0), seed=42)
policy = RandomPolicy(seed=43)
while env.phase not in ("round_end", "match_end"):
    actor = env.current_player
    observation = env.observe(actor)
    action = policy.act(observation)
    assert env.validate_action(action, actor)["valid"]
    transition = env.step(action, actor)
print(env.result)
```

这是四人轮流行动接口，不会暗中执行对手。每次 `step` 后的 observation 属于**下一位当前行动者**；训练时应先存当前行动者的 observation/action，再按玩家整理轨迹，不能直接把连续两步视为同一玩家的 MDP 转移。

`StepResult.rewards` 为四个固定座位的原始即时得分奖励：本步闲家得分变化 ΔS，闲家两座各 +ΔS，庄家两座各 −ΔS；包括甩错和末轮底分。开始下一局为零奖励。它不是胜率或升级奖励；未来训练可在外层定义自己的终局回报。`terminated` 仅整场 A 级坐庄获胜时为真；一局结束看 `info["round_ended"]`，使用 `next_round` 接着打，或 reset 开新样本。`truncated` 固定为假。

更多动作、牌编号、观察字段、快照与 HTTP 规范见 [接口文档](docs/api.md)。`iter_legal_actions` 是按相同牌面组合去重的惰性穷举，扣底和大手甩牌的组合数很大；没有截断或假装全部枚举完。RandomPolicy 用合法跟牌构造器，不枚举整张动作表；领出随机选择单、对、拖拉机，**不是全部合法动作的均匀采样**。

替换网页 AI 只需实现 `act(observation) -> action`，再注入策略工厂：

```python
from tractor_sim.server import TractorServer
from my_policy import MyPolicy

server = TractorServer(("127.0.0.1", 8765), policy_factory=lambda seed: MyPolicy(seed))
try:
    server.serve_forever()
finally:
    server.server_close()
```

每个房间创建一个策略实例；通过 observation 的 `player` 区分三位 AI。策略只收到该座位可知信息。异常策略会暂停牌桌并记录服务端日志，不会反复尝试错误动作。

## 验证

```powershell
& 'C:\Miniconda\python.exe' -m unittest discover -s tests -v
node --test tests/*.cjs
```

测试覆盖主副牌排序和连续性、优先跟拖与对子、完整杀牌、只比较最大牌型、甩牌原子校验与罚分、反底庄权、扣底倍率、得分边界、必打记录、J/A 降级、可见信息、快照重放，以及 HTTP 四人房间、鉴权、旧版本动作、断线恢复和路径隔离。另执行了随机完整升级比赛与浏览器实玩；验证记录在 [validation.md](docs/validation.md)。

当前是 CPU 参考裁判，便于逐条验证规则；没有实现 GPU 仿真，也没有固定任何模型、牌型 mask 或训练框架。以后优化应与这套裁判做差分验证。
