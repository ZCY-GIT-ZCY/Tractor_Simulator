# 仿真与房间接口 · schema_version 1

## 牌与座位

固定座位南 0、西 1、北 2、东 3，逆时针下一家 `(p-1)%4`；0/2、1/3 同队。牌用实体编号 **0..107**，不能重复同一实体；牌面编号 `id % 54`。第一副 0..53，第二副 54..107。观察中的 `turn_direction=-1` 表示新局的逆时针行动；载入旧离线快照可保留其历史方向。

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

不能把 `export_state()`、`omniscient=True` 或内部 `hands/deck/kitty` 当作 PVE/PVP 策略输入。`kitty` 仅在无人亮主、翻底定主时为牌列表，其他情况为 null，包括自对弈与局末。`result.kitty` 和 round_ended 事件也遵守这一限制。网页在终局单独通过房间快照的 `settlement.kitty` 公布最终底牌，训练策略仍只接收上述引擎观察。`known_kitties` 是该玩家实际拿过、扣过底牌的历史知识，属于私人记忆；反底后其他人扣的新底牌不会泄露。

## 阶段与动作

所有动作是 JSON 对象。默认 player 为当前行动者；显式 player 必须等于当前座位。

| 阶段 | 接受动作 | 下一步 |
|---|---|---|
| `dealing` | `{"type":"pass"}` / `{"type":"bid","cards":[...]}` | 当前摸牌者 pass 发下一张；任意座位 bid 实时亮主且不发牌。在线服务第 100 张发出后自动等待 6 秒，期间仅接受 bid，到时自动确认／翻底／重洗；离线核心最终 pass 直接结束，不占用训练墙钟时间 |
| `final_bidding` | pass / bid | 打 2 抢庄成功亮牌后继续问其余三家，三家 pass 或大王对定主才拿底；非抢庄成功亮牌后由庄家立即拿原始底牌，主牌改变；其他抢庄流程保持原设定 |
| `burying` | `{"type":"bury","cards":[八张实体id]}` | 从下一家开始反底；大王对定主则直接开牌 |
| `countering` | pass / bid | 成功反底者拿底；本轮有资格座位均 pass 后开牌（跳过扣底者和当前亮／反主者） |
| `playing` | `{"type":"play","cards":[...]}` | 其他玩家跟牌，第四人后结算轮胜；末轮结算一局 |
| `round_end` | `{"type":"next_round"}` | 按结果开始下一局 |
| `match_end` | 无 | reset / 新建比赛 |

`bid.cards` 是所展示的全部牌：加固时传两张同花色级牌，不能只传新收到那张。亮出的牌不从手里移除。禁止反自己：当前亮／反主者只有在 dealing／final_bidding 且此前为单级牌时，才能加固为同花色级牌对；换门、反王对或扣底后加固均返回 SELF_COUNTER。摸牌阶段 `step(action, player=座位)` 接受非当前摸牌者的 bid；pass 仍只能由当前摸牌者提交。

`play` 领出允许同门单张、对子、拖拉机、甩牌。合法但甩错仍接受该动作，返回 `throw_failed` 事件，其中 `attempted` 是尝试牌，`cards` 是强制实际出牌，`penalty` 为正数处罚量，`score_delta` 为计入闲家分的有符号量。

## 观察与 step 结果

观察主要字段：`schema_version/revision/round/phase/player/current_player`；`level/levels/passed_levels/banker/trump/auction`；`hand/hand_counts/forced_play/drawn_card`；`bid/bid_history/bid_options/counter_players/final_bid_players`；`kitty/kitty_owner/kitty_public/known_kitties`；`trick/last_trick/trick_no`；`score/captured_score/penalty_score/bottom_score`；`result/match_winner/events/action_types`。

`forced_play` 只给当前跟牌者，领出、非行动者或有多种合法牌面组合时为 null。同牌面两副牌的实体替换视作一种选择；判断使用本人的手牌和公开领出，不使用其他隐藏牌，也不消耗环境 RNG。前端在轮到玩家时自动预选一次，仍须点击出牌；清空或手动改选后不会被轮询重新覆盖。

`drawn_card` 仅在 dealing 且观察座位正是本次摸牌者时返回实体牌 id，否则为 null。进入 final_bidding／拿底／出牌后清空，教学四家手牌也不覆盖这一字段。

`played_cards` 为本局已公开出过的全部实体牌，含当前轮的实际出牌。`public_history` 为本局公开的 bid、kitty_picked_up、kitty_buried、play、throw_failed 事件；拿底/扣底事件只有操作玩家，不含底牌。它不会随 `events` 的最近 80 条窗口截断，用于策略追踪未知牌及公开缺门。下局及重新洗牌时清空；这两项为 schema 1 的兼容新增字段。

PVE 默认策略为 `EnochPolicy`，详细预算、输入隔离与适配说明见 [AI 文档](ai.md)。网页房间快照新增 `ai`（PVE 的 `{id, name}`，其他模式 null）与 `bot_thinking`（后台决策进行中）。这些字段不含采样手牌和搜索评分。显式 `policy_factory` 为各座分别创建实例，可覆盖默认策略；计算结果通过 revision 检查后才转移环境。

`final_bid_players` 为拿底前确认仍待询问的座位，从庄家起逆时针排列，允许本人加固。打 2 抢庄成功更强亮牌后，重新从该玩家下一家排列其余三家并继续确认。非抢庄局成功亮牌后结束确认，由庄家立即拿底；无论摸牌阶段谁反主、确认整圈均不亮或已反至大王对，原始底牌也始终由庄家拿。初始等级非 2 的抢庄流程保留：成功反主者拿底但不接庄；整圈均不亮时，有摸牌阶段后续反主／加固则最后成功者拿底，否则庄家拿底。已是大王对时省略确认。`final_bidding_continued` 事件标记打 2 抢庄确认继续，包含下一行动者、庄家和亮牌强度。

`realtime_bidding=true` 表示服务支持实时亮主，实际只在 dealing 生效。`bid_options` 在 dealing 给观察座位自己的全部合法亮法，其他亮牌阶段仅给当前行动者。`iter_legal_actions(player)` 对摸牌阶段非当前座位只列 bid，不列 pass。`counter_players` 为当前反底轮次尚待询问的座位顺序，首位即 current_player；跳过刚扣底者和当前亮／反主者，其他阶段为空。拿底前确认也跳过已亮级牌对／王对的当前定主者，单级牌定主者仍可同门加固。大王对之后扣底直接转 playing。`events` 是最近 80 个公开事件，事件有递增 seq；完整日志保存在离线状态。`result` 是最近已结算局结果，新一局期间仍保留；须结合 phase 使用。抢庄阶段还无人亮主时 `banker=null`。

末轮底牌倍率使用首家实际领出牌型的对子数 p（包括拖拉机内对子），为 2^(p+1)，而不是赢家偶然跟出的对子数。甩错后按强制实际领出的牌型。J／A 特殊扣底仍检查最后胜者实际那手牌是否含本级 J／A。

在线快照 `state.bidding_window={complete,remaining_seconds,duration_seconds:6}` 在第 100 张发出后标记完整手牌与剩余实时亮主时间。期限由房间的 monotonic 时钟管理，任意成功亮主不重新计时；断线暂停时冻结剩余时间。服务器调度器和所有牌局 HTTP 请求在同一房间锁下检查截止时间，只执行一次核心最终 pass。窗口内真人提前 pass／random 返回 BIDDING_WAIT；截止后的 bid 不会绕过后续轮询，也不能串到下一次重洗或新一局。AI 在窗口内可从任意 AI 座位亮主，pass 仅表示该策略本次不亮，不结束窗口；超时或版本变化后的异步决策丢弃。

`StepResult` 是 dataclass：`observation`（下一行动者）、`rewards`（四个固定座位）、`terminated`（整场结束）、`truncated`（false）、`info`（本步事件、round_ended、局末结果）。回报和轮转训练注意事项见 README。

`Policy` 协议只有 `act(observation: dict) -> dict`。RandomPolicy 可独立实例化并指定 seed；未来 SFT 或 RL 策略可以直接实现这个接口。

## HTTP

`POST` 请求体必须为 JSON 对象，Content-Type 为 application/json，最大 64 KiB。除创建会话、health、site、静态文件、规则外，均带 `Authorization: Bearer <token>`。token 不放 URL，每个标签存 sessionStorage，并在本浏览器 localStorage 保存最近会话以便重新打开后主动恢复。邀请链接只有房间号，没有座位凭据。

| 请求 | 请求内容 | 返回 |
|---|---|---|
| `POST /api/session` | `{mode:"self"|"pve"|"pvp",name,room?,seat?,config?,seed?}` | token + 房间快照 |
| `GET /api/state?since=版本&wait=4` | 无；wait 0..5 秒 | 房间快照，版本无变化时最多等待指定时间 |
| `POST /api/seat/confirm` | `{seat:0..3}` | PVP 原子确认空席；已占用返回 `SEAT_TAKEN` |
| `POST /api/seat/cancel` | `{}` | PVP 开局前释放自己的确认座位，不退出房间 |
| `POST /api/room/ai` | `{seat?:0..3,enabled:true|false}` | PVP 房主添加／移除本地 AI；省略 seat 且 enabled=true 补齐全部空席 |
| `POST /api/room/start` | `{}` | PVP 房主在四席已就绪、已确认真人全部在线时开局 |
| `POST /api/validate` | `{version?,round?,redeals?,context?:{level,trump},action}` | 预校验及 `version/level/trump/round/redeals/phase`；摸牌 bid 必须带 round/redeals，允许旧版本／旧主门，并按最新强度检查；其他动作旧 version 或页面级牌／主门不同返回 409 STALE；不预测甩牌成败 |
| `POST /api/action` | `{version,action}` | 接受后快照；旧 version 返回 409，不执行 |
| `POST /api/bid` | `{round,redeals,action:{type:"bid",cards:[...]}}` | 仅摸牌阶段实时亮主／反主；按房间锁串行裁判，不因其他人摸牌而拒绝旧版本；后到的同强／较弱亮法返回 422 BID_TOO_WEAK，提示亮主无效 |
| `POST /api/confirm-result` | `{version}` | 终局由本局庄家确认底牌和计分；非庄家返回 `BANKER_ONLY`，旧 version 返回 409 |
| `POST /api/autoplay` | `{enabled:true|false}` | self 四家随机托管 / PVE 四家 Enoch 托管；PVP 只开关请求者本人座位的 Enoch AI 托管 |
| `POST /api/leave` | `{}` | `{ok:true}`，释放会话和座位 |
| `GET /api/health` | 无 | `{ok:true,schema_version:1,service:"tractor_sim",bind_host:监听地址,instance_id:进程随机标识,pve_ai:"enoch-table-v1"}` |
| `GET /api/site` | 无 | `{public_url:HTTPS入口或null,disconnect_seconds:25,room_idle_seconds:7200}` |
| `GET /rules` | 无 | 唯一规则文件 UTF-8 文本 |

网页辅助动作 `{"type":"random"}` 仅 /api/action 识别，表示让当前座位执行随机策略的一步；仅 self/PVE 可以使用，PVP 的真人自行选择行动，AI 席位由服务器执行策略。Python 核心 step 不接受 random。

实时 bid 与普通动作共享房间锁和同一裁判，服务按实际处理顺序接受请求。摸牌期间其他人发牌／亮牌导致版本变更不使 /api/bid 失效，但 round/redeals 必须匹配，防止旧请求跨局或重洗后生效；摸牌已结束返回 BIDDING_CLOSED，不能用此接口越过后续轮询。座位由 token 确定，不接受客户端冒用其他人的 player；教学自对弈继续代当前摸牌者亮牌。网页仅在观察含 realtime_bidding 时使用新接口，已运行的旧服务仍使用原回合接口，直到用户自行重新启动。

房间快照包括：`version`（房间版本）、`room/mode/seat/name/host`、`players`（昵称/座位/occupied/online/bot/takeover）、`controlled`、`started/paused/pause_reason/autoplay/config`、`state`（该客户端的环境观察，未开局或未确认座位时为 null）。新增 `members`（已入房者的 name/seat/self/online，不包含其他人的 token）、`ai_seats`（AI 座位列表）、`can_start`（该客户端可否开始）、`takeover`（本人是否主动交给 AI 托管）。`players[].takeover` 对全桌公开；`bot` 仍只表示无人席位的 AI，不把托管真人改成机器人账号。未选座者 `seat=null,controlled=[]`，不能取得任何手牌或执行游戏动作；`ai` 元数据也覆盖含 AI 补位或真人 AI 托管的 PVP 房间。pause_reason 为 null、disconnected 或 policy_error。房间 version 与环境 revision 不同：加入、选座、AI 配置、托管开关、离线等也改变房间版本。出牌和结算确认使用 **version**。

PVP 的 `/api/autoplay` 无须提交全桌 version，仅操作当前 token 的已确认座位，不能替其他玩家开关。未选座返回 `SEAT_REQUIRED`，尚未开局返回 `LOBBY`，enabled 必须是 JSON 布尔值。托管不改变手牌可见范围，保留 seat/name/controlled；托管时手动游戏动作返回 `AI_CONTROLLED`，取消后恢复。开启、取消、离开或换控制者均改变该席控制代次，异步 AI 结果同时核对环境 revision 和控制代次，取消后立即重开也不能执行旧结果。开关在同一房间跨局保留，离开后不继承给新玩家；房主仍负责开始下一局。已托管的真人断线不触发暂停，其他真人仍保留原来的 25 秒断线暂停规则。

PVP 房间号 3..24 位 ASCII 字母、数字、下划线或短横线，区分大小写；省略 room 或留空时创建随机 8 位房间号。后进入者采用第一人配置；最多 16 名房间成员、4 个可玩席位。创建 PVP 会话始终返回未选座，session 请求中的 seat 不会占位；南/西/北/东对应 0/1/2/3，0/2 与 1/3 同队。预选仅是前端状态，确定才请求服务器占座，取消后释放。四席就绪也不自动开始，由已入座房主显式开始；房主配置 AI 前也须先确认自己的座位。

选座与房间配置接口在房间锁下原子处理，无须全局 version，以免不同席位同时确认互相干扰。同席竞争只允许一人成功。已确认者须先取消才能改座；开局后 cancel 返回 `SEATS_LOCKED`。开局后不能移除 AI 或覆盖任何已确认真人（包括离线真人），但真人主动离开的空席可由新人确认或房主补 AI，使用该席现有手牌继续。AI 与 PVE 共用本地策略运行池，每席仅接收自己的观察并经过同一规则裁判。新一局只有房主可以触发，普通出牌只能是自己座位。网络重试先取快照、核对 version，再提交；避免自动重复出牌。

错误：无会话 GET 返回 403；动作不合法返回 422 `{valid:false,code,message}`；结构或 JSON 错误返回 400；旧版本返回 409 `{error:"STALE",message}`；未知静态路径 404。422 不改变牌局或扣分。校验接口里的游戏规则错误也可能以 200 `{valid:false,...}` 返回。

网页预校验同时发送当前显示的 `context.level/trump`，服务端先核对再判牌；不一致时前端同步权威快照，保持提交按钮禁用。手牌标题、PC 与移动端本局等级使用 `state.level`，不取房间初始设置或另一队等级；跟牌提示列出首家实际牌面及有效门。

`FOLLOW_SUIT` / `FOLLOW_STRUCTURE` 的失败选牌另存服务端私有 `runtime/diagnostics/validation-errors-日期.jsonl`（自定义 history_dir 时在其父目录的 diagnostics 下）。包含房间、版本、轮次、裁判级牌与主花色、公开领出、本人的手牌与选择，以及页面所报级牌／主花色；不含凭据、其他手牌和底牌。每次环境修订去重，最多存 32 种选择；文件不通过静态或历史 API 发布。保存失败不影响校验或游戏状态。正式对局回放仍仅记录已接受动作。

默认最多 32 个房间，达到上限返回 422 ROOM_LIMIT；仍可加入未满的现有房间。全体客户端两小时未访问则统一释放该房间及 token，单家断线不释放座位。请求读取超时 20 秒，最大 160 个同时处理的 HTTP 请求。公网启动器将入口传给 site；邀请链接在本机访问时也可复制为公网入口。

## 不包含的功能

没有 GPU 后端、Gymnasium/PettingZoo 包装、训练算法、已训练模型、持久数据库。规则引擎和策略接口不依赖 HTTP，可以直接接向量环境和多进程 rollout；添加这些功能时应保持默认观察不泄露信息及甩牌尝试合法性。

## 历史回放

`GET /api/history` 自动列出已完成局的元数据，`GET /api/history/<id>` 一次读取完整回放帧。未完成局不会发布。实时状态新增 `replay_id` 和 `history_error`。保存目录、帧格式和可见范围详见 [回放说明](replay.md)。

### 网页终局核对

房间快照新增 `settlement`：非终局为 `null`；`round_end` / `match_end` 时包含 `round`、`banker`（本局原坐庄者）、`kitty`（最后一次扣下的八张）、`kitty_points`、`confirmed`、`confirmed_by`、`can_confirm`、`ai_banker`、`ai_confirm_seconds`。其余分数取 `state.result`。确认只是房间展示状态，不执行引擎动作、不产生训练奖励、不增加回放步数。自对弈可代庄家确认，PVE/PVP 的 AI 庄家至少等待六秒后确认；真人庄家必须点击。PVP 所有客户端通过版本更新同步结果。确认前房主请求 `next_round` 返回 `REVIEW_PENDING`；确认后仍只有房主可开下一局。新局清空核对状态。


### 必打记录与升级阻挡

`passed_levels[team]` 只在该队作为庄家方打完相应等级的一局时登记，胜负均算，记录不因特殊降级而清除；配置初始等级不会自动登记较低级。升级检查包含当前等级本身：未坐庄打过 2 的闲家即使得 120/160/200 分上台，也先在 2 坐庄，不能升至 3/4/5。`result.upgrade` 是分数对应的名义升级数，实际等级以 `levels_after` 为准；新增 `result.mandatory_stop` 表示实际被哪个未完成的必打级阻挡，无阻挡时为 null。
