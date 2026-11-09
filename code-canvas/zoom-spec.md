# 缩放画布（meta.mode: "zoom"）

一张无限画布，读者从系统全局**连续放大**一路进到源码行，中间没有翻页、没有跳转：

| 层 | 读者看到什么 | 由什么数据画出来 |
|---|---|---|
| L0 系统 | 框架图：模块方框 + 通道连线（带流动粒子）+ 虚线分组；每个方框里已经能看到它内部结构的缩影 | `modules[].pos/span/title/oneliner`、`channels`、`groups` |
| L1 模块 | 方框放大，变成这个模块的**内部结构图**：一个循环 / 一条流水线 / 一个分派 / 一个状态机 / 一张流程图 / 分层 / 网格，旁边是它持有的数据结构（队列、映射、时间轮……的图形） | `modules[].anatomy` |
| L2 函数 | 结构图里的每个部件**原地变形**成一张函数卡，卡上是按代码块分段的一句话摘要 | `cards[]` 挂在 item / field / center / module 上，`cards[].blocks` |
| L3 代码 | 摘要条溶解成真实源码，函数名可点、飞到它的定义 | `cards[].file + lines/ranges`（构建脚本从仓库读原文） |
| 故事线 | 一个主角（请求 / fiber / 一批数据 / 一次调用……）的一生，逐步把镜头带过上面每一层，最后停在具体行 | `stories[].steps` |

**每一层都占满屏幕核心**：滚轮/双指缩放停下后，镜头会滑到最近的一层（系统 / 模块 / 函数卡 / 代码块），让那一层铺满屏幕中间；卡片在快铺满屏幕时才完成变形。所以长函数的 blocks 要切成 5-20 行一段：太长的卡在整卡视图里代码看不清，读者会按块放大。

右侧说明面板跟随当前所在模块：职责、"它不是什么"、函数卡目录、关键状态、配置、坑。

渲染器 `template/zoom.html`，构建 `build_zoom.py`，截图自查 `zoom_shot.js`。

---

## 1. 你要做的判断（没有固定答案）

这些都由你看过代码后决定，并写进 `meta.judgment`。

1. **范围**：这张图讲哪一块？一个仓库可能有几十个子系统，一张图只讲一条主线能说清的范围（6-10 个模块）。范围由用户的关注点和代码里真正的主路径决定。
2. **主角与形状**：什么东西"活过"这些模块？请求流经（server）、一个任务的一生（fiber / coroutine / future）、一个循环的每一圈（event loop / scheduler step）、一次调用穿过的层（library API）、一批数据被逐级变换（编译器 / 模型前向）。主角决定故事线和通道方向。
3. **模块怎么切**：按**职责和线程/所有权**切，不按目录。一个模块 = 一个读者能用一句话说清"它负责什么、它不负责什么"的部件。判据：它有自己的状态吗？它做决定吗？换一个线程/进程吗？——都不是的薄包装并进相邻模块。
4. **每个模块的内部结构用哪种原语**（下表）——选**代码真实的形状**：有 `while(running)` 且每圈固定几站就是 loop；`switch/match` 分派就是 dispatch；有状态枚举和转换就是 statemachine；一串有先后的阶段就是 pipeline；带判断分支的过程是 flow；上下叠的抽象层是 layers；几个并列的子部件是 grid。
5. **哪些函数值得一张卡**：主角经过的、改变状态或做决定的函数。纯转发、getter、日志不值得。一个模块 1-7 张卡，全图 10-30 张。
6. **故事线**：选一个具体的主角实例（写出它的名字/值，比如 `PRODUCE A5`、`fiber F1`、`batch=8 tokens`），15-35 步，一半以上的步落到具体代码行，每步写**此刻的值**。

## 2. 解剖原语（anatomy.primitive）

| primitive | 画法 | 什么时候用 | 要点 |
|---|---|---|---|
| `loop` | items 排成一圈，箭头顺时针；`center` 放循环函数本身 | 线程主循环、事件循环、每圈固定几站 | 3-8 站，按代码里的执行顺序；center 挂 run()/loop() 的卡 |
| `pipeline` | items 一行（多了就蛇形），依次连箭头 | 有先后的阶段 | 不写 edges 时自动顺序连 |
| `dispatch` | `center` 在上（分派点），items 是分支 | switch / match / 按类型路由 / 线程池取活后分派 | center 写判据，比如 `match apiKey` |
| `statemachine` | items 是状态，排成椭圆；edges 是转换（带标签） | 有显式状态枚举 | 每条 edge 的 label 写触发事件 |
| `flow` | 按 edges 分层（左→右或上→下），`kind:"decision"` 画菱形 | 带判断分支的过程 | edges 必填，label 写条件 |
| `layers` | items 上下叠放 | 抽象层 / 调用栈 / 协议栈 | 第一个在最上 |
| `grid` | items 网格 | 几个并列的子部件 | 也是只有 fields 时的默认 |

`fields`：这个模块**持有的数据结构**，画在旁边（宽方框在右侧一列，方/高方框在底部），每个带一个形状图标：

`queue`（槽位一排）· `list` · `stack` · `map`/`table`（键→值）· `set`/`pool`（一排圆点）· `ring`/`wheel`（时间轮）· `tree` · `bitmap`/`array` · `lock`/`flag` · `scalar`（一个值框，可写 `value`）

字段可选 `slots`（格子数）、`fill`（已占几个）、`value`、`note`。

`edges` 可以连 item ↔ item，也可以连 item ↔ field（比如"② 取响应"读 `responseQueue`），第四个元素 `"dash"` 画虚线、`"curve"`/`"back"` 画弯线。

## 3. 格式

```jsonc
{
 "meta": {
   "mode": "zoom",
   "title": "Kafka Broker 缩放图",          // 顶栏
   "short": "Kafka broker",                 // 面包屑第一格
   "subtitle": "一个请求怎样穿过 broker：…",  // 一句话主线
   "overview": ["段落1", "段落2"],           // 系统层说明面板
   "judgment": { "scope": "", "shape": "", "protagonist": "",
     "parts": [{"name":"", "owns":"", "not":"", "why":""}],
     "cut": "", "primitives": {"<module id>": "为什么用这个原语"},
     "cards": "选卡标准", "skipped": ["没画的东西和理由"], "revisions": ["看截图/读者反馈后改了什么"] }
 },
 "layout": {"colw": 330, "rowh": 260, "gap": 44},   // 可省；网格单位
 "groups": [{"title": "网络层 · num.network.threads", "modules": ["proc"]}],
 "modules": [
  {"id": "clients", "title": "客户端", "kind": "external", "pos": [0, 0.45], "span": [0.5, 0.8],
   "oneliner": "每条 TCP 连接对应 broker 上的一个 KafkaChannel"},
  {"id": "proc", "title": "Processor（网络线程）", "kind": "thread", "pos": [0.6, 0], "span": [1.15, 1.6],
   "oneliner": "一个 selector 循环：读字节、拼请求、写响应",          // ≤ 44 字，L0 方框里只显示一行
   "anatomy": {
     "primitive": "loop",
     "center": {"label": "run()", "sub": "while(shouldRun)", "card": "run"},
     "items": [
       {"id": "c1", "label": "新连接", "sub": "configureNewConnections", "card": "c1"},
       {"id": "c4", "label": "收请求", "sub": "processCompletedReceives", "card": "c4", "kind": "thread"}
     ],
     "fields": [{"name": "responseQueue", "type": "LinkedBlockingDeque[Response]", "shape": "queue",
                 "note": "私有信箱：I/O 线程把响应投到这里"}],
     "edges": [["c2", "responseQueue", "", "dash"]],
     "caption": "每一圈依次走 6 站"                                   // 可选，图下小字
   },
   "info": {
     "kicker": "kafka.network.SocketServer.Processor",
     "role": "负责一批连接的全部网络 I/O……",
     "not": "不解析业务、不碰日志……",                                  // 对比：它不是谁
     "fields": [["responseQueue", "LinkedBlockingDeque", "I/O 线程投递响应的信箱"]],
     "methods": [["processCompletedReceives()", "→ Unit", "把读满的字节变成 Request 入队"]],
     "config": [["num.network.threads", "3", "每个 listener 的网络线程数"]],
     "pitfalls": ["put() 满时阻塞的是整个网络线程，不是一条连接"]
   }},
  {"id": "rc", "title": "RequestChannel", "kind": "queue", "pos": [1.85, 0], "span": [0.85, 1.1],
   "oneliner": "两条有界队列 + processor 映射",
   "card": "rc",                                                      // 模块级单卡：整个方框变成这张卡
   "anatomy": {"primitive": "grid", "fields": [ ... ]}}
 ],
 "channels": [
   {"from": "clients", "to": "proc", "label": "字节", "kind": "flow"},
   {"from": "apis", "to": "proc", "label": "sendResponse → 信箱", "kind": "return", "sides": "tt"}
 ],
 "cards": [
  {"id": "c4", "title": "processCompletedReceives()", "file": "core/src/main/scala/kafka/network/SocketServer.scala",
   "lines": [982, 1039],                                   // 或 "ranges": [[50,52],[76,79],[157,171]]，中间显示 ⋯
   "blocks": [[983, 985, "遍历本轮读满的每个请求"], [986, 989, "找到连接，解析请求头"]],
   "links": {"handle": "apis"}}                            // 可选：这个词点了飞到哪张卡
 ],
 "stories": [{"title": "一个 PRODUCE 请求", "token": "A5", "steps": [
   {"at": {"module": "clients"}, "text": "客户端 A 发出 A5（PRODUCE）……"},
   {"at": {"item": "proc/c3"}, "text": "网络线程走到第 ③ 站 poll()"},
   {"at": {"card": "c4", "line": 1007}, "text": "new Request(id, …)：第一个参数 id 就是 P0 的编号……", "value": "processor = 0"},
   {"at": {"card": "c4", "line": 1019, "line2": 1021}, "text": "……", "kind": "return"}
 ]}]
}
```

- `modules[].kind`：`thread` `queue` `component` `store` `api` `state` `loop` `external`（只决定左侧色条颜色）。`external` 或没有 `anatomy` 的模块是外部角色：虚线小方框，不能放大。
- `channels[].kind`：`flow` 主流程 · `call` 调用 · `queue` 入队出队 · `return` 回程（虚线）· `async` 异步回调（点线）· `data` 读写数据 · `control` 控制 · `error`。`sides` 两个字母强制出入边（`l r t b`），比如 `"tt"` 从上边出、上边进，绕过中间的方框；`dy1/dy2/dx1/dx2` 微调端点。
- 卡挂在哪里决定它从哪里长出来：`items[].card`（可以是数组，多张卡在部件格子里排开）、`fields[].card`、`anatomy.center.card`（loop/dispatch）、`modules[].card`（整块变成一张卡，结构图淡出）。**每张卡必须恰好挂一次**，否则读者到不了。
- `cards[].title` 末尾的函数名会自动变成全图的链接（代码里出现 `name(` 就可点）；`links` 补自动识别不到的。
- `stories[].steps[].at`：`{"card", "line", "line2"?}` 落到代码行；`{"item": "模块id/部件id"}` 落到结构图部件；`{"module"}` 落到方框。`kind:"return"` 让令牌变成回程色，`value` 写此刻的具体值。

## 4. 布局（L0）

网格单位：一格 330×260，方框占 `span` 格减去 44 的间隙。`pos` 可以是小数。

- 主角的方向就是阅读方向：入口在左，出口在右（或上→下）；回程走 `return` 通道、用 `sides` 绕开。
- 内部结构复杂的模块给大一点（loop 6 站 + 4 个字段：`span ≈ [1.15, 1.6]`；分派 5 支：`[1, 1.5]`；单卡模块 `[0.85-1, 0.75-1]`）。
- 外部角色（客户端、磁盘、GPU、OS）小方框 `[0.5, 0.5-0.8]`。
- 相关模块用 `groups` 框起来（同一个线程池、同一个进程、同一层）。
- 别让方框重叠（构建会报错），通道尽量别穿过方框。
- 全图 4-10 个可放大的模块；超过 10 个就是范围太大，拆成两张图。

## 5. 预算

| 项 | 预算 |
|---|---|
| 模块 title | ≤ 22 字 |
| oneliner | ≤ 44 字（L0 只显示一行） |
| item label | ≤ 12 字（中文短语或函数短名）；`sub` 放函数名/判据 |
| 每张卡 | ≤ 70 行（硬上限 90）；长函数用 `ranges` 只截主干，中间 ⋯ |
| 每行 | ≤ 130 列（更长会被截断） |
| blocks | 覆盖卡里 ≥ 60% 的行，每块一句话写**做了什么/为什么**，不是复述代码 |
| 故事 | 15-35 步，≥ 一半落到代码行 |

## 6. 构建、自查、交付

```bash
python3 build_zoom.py zoom.json --repo <仓库根> --out zoom.html      # 填代码 + 校验 + 渲染；0 ERROR 才算过
node zoom_shot.js zoom.html shots/                                    # 系统层 / 每个模块 / 几张卡 / 代码层 截图
python3 reader_check.py zoom.data.json --zoom --out reader_report.md  # 新读者只看画布能不能懂
```

`build_zoom.py` 会写出 `zoom.data.json`（代码已填好），给 reader_check 用。

**看截图要查的**：L0 每个方框里能看出结构缩影、标签没被截成"…"；模块层部件和字段不重叠、标签可读；通道不穿方框；卡片层摘要条能读；代码层高亮行就是故事说的那行。

**不可商量**：build 0 ERROR；卡内代码是仓库原文（脚本读取，不要手抄）；故事落点行号在卡的行段内；每个判断写进 `meta.judgment`，包括看截图和读者报告后改了什么。
