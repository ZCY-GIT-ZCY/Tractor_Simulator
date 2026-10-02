# 仿真与房间接口 · schema_version 1

## 牌与座位

固定座位 0、1、2、3，下一家 `(p+1)%4`；0/2、1/3 同队。牌用实体编号 **0..107**，不能重复同一实体；牌面编号 `id % 54`。第一副 0..53，第二副 54..107。

| 牌面编号 | 牌面 |
|---|---|
| 0..12 | 黑桃 2..A |
| 13..25 | 红桃 2..A |
| 26..38 | 方片 2..A |
| 39..51 | 草花 2..A |
| 52 / 53 | 小王 / 大王 |

花色参数 `S/H/D/C`，无主 `None`（JSON null）。等级整数 2..14，J=11、Q=12、K=13、A=14。

## 环境 API

`TractorEnv(config=None, seed=None)`：GameConfig 或配置 dict；初始化即 reset。config 字段：`initial_level` 默认 2，`auction` 默认 true，`banker` 默认 0。初始等级共同适用于两队。

| 方法 / 属性 | 含义 |
|---|---|
| `reset(seed=None)` | 重开整场、重置 RNG 和两队等级，返回当前玩家观察；首次牌已经发出 |
| `current_player` / `phase` | 当前唯一行动座位 / 阶段 |
| `observe(player, omniscient=False)` | 返回深拷贝观察；默认只给该座位可知信息；omniscient 仅增加四家手牌，不公开隐藏底牌 |
| `validate_action(action, player=None)` | 无副作用预校验，返回 `{valid, kind, message, ...}` 或 `{valid:false, code, message}` |
| `step(action, player=None)` | 合法动作才转移；非法抛 IllegalAction，状态、日志、RNG 不变 |
| `forced_play(player=None)` | 当前跟牌者只有一种合法牌面组合时返回实体牌列表，其他情况返回 None；不自动出牌 |
| `iter_legal_actions(player=None)` | 惰性合法动作枚举，同牌面实体替代组合去重；包含所有合法甩牌尝试 |
| `export_state()` / `from_state(state)` | 完整可 JSON 序列化快照与恢复，保留 RNG，包含隐藏信息，仅供可信离线使用 |
| `assert_invariants()` | 检查 108 张实体守恒、得分分账和结束手牌为空 |

`seed` 明确指定才保证初始发牌可复现。策略 RNG 与环境 RNG 独立；同初始 seed、config 和接受动作顺序可以精确重放。`tractor_sim.replay.replay_episode` 校验 CLI JSON 的一个 episode，并返回恢复环境；CLI `--replay` 检查文件全部 episode。

不能把 `export_state()`、`omniscient=True` 或内部 `hands/deck/kitty` 当作 PVE/PVP 策略输入。`kitty` 仅在无人亮主、翻底定主时为牌列表，其他情况为 null，包括自对弈与局末。`result.kitty` 和 round_ended 事件也遵守这一限制。`known_kitties` 是该玩家实际拿过、扣过底牌的历史知识，属于私人记忆；反底后其他人扣的新底牌不会泄露。

## 阶段与动作

所有动作是 JSON 对象。默认 player 为当前行动者；显式 player 必须等于当前座位。

| 阶段 | 接受动作 | 下一步 |
|---|---|---|
| `dealing` | `{"type":"pass"}` / `{"type":"bid","cards":[...]}` | 发下一张；100 张发完后拿底前确认或翻底；无人抢庄重新洗牌 |
| `final_bidding` | pass / bid | 四家均 pass 原庄家拿底；成功反主／本人加固者立即拿底，首局抢庄同时取得庄权 |
| `burying` | `{"type":"bury","cards":[八张实体id]}` | 从下一家开始反底；大王对定主则直接开牌 |
| `countering` | pass / bid | 成功反底者拿底；本轮有资格座位均 pass 后开牌（跳过扣底者和当前亮／反主者） |
| `playing` | `{"type":"play","cards":[...]}` | 其他玩家跟牌，第四人后结算轮胜；末轮结算一局 |
| `round_end` | `{"type":"next_round"}` | 按结果开始下一局 |
| `match_end` | 无 | reset / 新建比赛 |

`bid.cards` 是所展示的全部牌：加固时传两张同花色级牌，不能只传新收到那张。亮出的牌不从手里移除。

`play` 领出允许同门单张、对子、拖拉机、甩牌。合法但甩错仍接受该动作，返回 `throw_failed` 事件，其中 `attempted` 是尝试牌，`cards` 是强制实际出牌，`penalty` 为正数处罚量，`score_delta` 为计入闲家分的有符号量。

## 观察与 step 结果

观察主要字段：`schema_version/revision/round/phase/player/current_player`；`level/levels/passed_levels/banker/trump/auction`；`hand/hand_counts/forced_play/drawn_card`；`bid/bid_history/bid_options/counter_players/final_bid_players`；`kitty/kitty_owner/kitty_public/known_kitties`；`trick/last_trick/trick_no`；`score/captured_score/penalty_score/bottom_score`；`result/match_winner/events/action_types`。

`forced_play` 只给当前跟牌者，领出、非行动者或有多种合法牌面组合时为 null。同牌面两副牌的实体替换视作一种选择；判断使用本人的手牌和公开领出，不使用其他隐藏牌，也不消耗环境 RNG。前端在轮到玩家时自动预选一次，仍须点击出牌；清空或手动改选后不会被轮询重新覆盖。

`drawn_card` 仅在 dealing 且观察座位正是本次摸牌者时返回实体牌 id，否则为 null。进入 final_bidding／拿底／出牌后清空，教学四家手牌也不覆盖这一字段。

`final_bid_players` 为拿底前确认仍待询问的座位，从庄家起排列，允许本人加固；成功更强亮牌后立即拿底。当前已是大王对时省略确认。

`bid_options` 只给当前行动者可亮的组合。`counter_players` 为当前反底轮次尚待询问的座位顺序，首位即 current_player；跳过刚扣底者和当前亮／反主者，其他阶段为空。大王对之后扣底直接转 playing。`events` 是最近 80 个公开事件，事件有递增 seq；完整日志保存在离线状态。`result` 是最近已结算局结果，新一局期间仍保留；须结合 phase 使用。抢庄阶段还无人亮主时 `banker=null`。

`StepResult` 是 dataclass：`observation`（下一行动者）、`rewards`（四个固定座位）、`terminated`（整场结束）、`truncated`（false）、`info`（本步事件、round_ended、局末结果）。回报和轮转训练注意事项见 README。

`Policy` 协议只有 `act(observation: dict) -> dict`。RandomPolicy 可独立实例化并指定 seed；未来 SFT 或 RL 策略可以直接实现这个接口。

## HTTP

`POST` 请求体必须为 JSON 对象，Content-Type 为 application/json，最大 64 KiB。除创建会话、health、site、静态文件、规则外，均带 `Authorization: Bearer <token>`。token 不放 URL，每个标签存 sessionStorage，并在本浏览器 localStorage 保存最近会话以便重新打开后主动恢复。邀请链接只有房间号，没有座位凭据。

| 请求 | 请求内容 | 返回 |
|---|---|---|
| `POST /api/session` | `{mode:"self"|"pve"|"pvp",name,room?,seat?,config?,seed?}` | token + 房间快照 |
| `GET /api/state?since=版本&wait=4` | 无；wait 0..5 秒 | 房间快照，版本无变化时最多等待指定时间 |
| `POST /api/validate` | `{action}` | 预校验结果，不预测甩牌成败 |
| `POST /api/action` | `{version,action}` | 接受后快照；旧 version 返回 409，不执行 |
| `POST /api/autoplay` | `{enabled:true|false}` | 开关 self/PVE 四家随机托管；PVP 拒绝 |
| `POST /api/leave` | `{}` | `{ok:true}`，释放会话和座位 |
| `GET /api/health` | 无 | `{ok:true,schema_version:1,service:"tractor_sim",bind_host:监听地址,instance_id:进程随机标识}` |
| `GET /api/site` | 无 | `{public_url:HTTPS入口或null,disconnect_seconds:25,room_idle_seconds:7200}` |
| `GET /rules` | 无 | 唯一规则文件 UTF-8 文本 |

网页辅助动作 `{"type":"random"}` 仅 /api/action 识别，表示让当前座位执行随机策略的一步；仅 self/PVE 可以使用，PVP 自行选择行动。Python 核心 step 不接受 random。

房间快照包括：`version`（房间版本）、`room/mode/seat/host`、`players`（昵称/座位/在线/AI 标志）、`controlled`、`started/paused/pause_reason/autoplay/config`、`state`（该客户端的环境观察，等待满人时为 null）。pause_reason 为 null、disconnected 或 policy_error。房间 version 与环境 revision 不同：加入、托管开关、离线等也改变房间版本。HTTP 动作使用 **version**。

PVP 房间号 3..24 位 ASCII 字母、数字、下划线或短横线，区分大小写；省略 room 或留空时创建随机 8 位房间号。后入座者采用第一人配置；满员拒绝第五人。新一局只有房主可以触发，普通出牌只能是自己座位。网络重试先取快照、核对 version，再提交；避免自动重复出牌。

错误：无会话 GET 返回 403；动作不合法返回 422 `{valid:false,code,message}`；结构或 JSON 错误返回 400；旧版本返回 409 `{error:"STALE",message}`；未知静态路径 404。422 不改变牌局或扣分。校验接口里的游戏规则错误也可能以 200 `{valid:false,...}` 返回。

默认最多 32 个房间，达到上限返回 422 ROOM_LIMIT；仍可加入未满的现有房间。全体客户端两小时未访问则统一释放该房间及 token，单家断线不释放座位。请求读取超时 20 秒，最大 160 个同时处理的 HTTP 请求。公网启动器将入口传给 site；邀请链接在本机访问时也可复制为公网入口。

## 不包含的功能

没有 GPU 后端、Gymnasium/PettingZoo 包装、训练算法、已训练模型、持久数据库。规则引擎和策略接口不依赖 HTTP，可以直接接向量环境和多进程 rollout；添加这些功能时应保持默认观察不泄露信息及甩牌尝试合法性。
