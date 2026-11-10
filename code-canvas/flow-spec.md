# 结构图画布（meta.mode: "flow"）

用户提一个问题（"一个请求从进来到返回经过了什么"）。你交出一组**逐层放大的架构图**：

- **每一层是一张结构图**：方框是这一层的部件，连线是它们之间的关系（谁调谁、数据往哪流、谁往哪个队列放东西），线上的**编号**是这件事发生的先后。读者看一眼就知道这一层有哪些东西、怎么连着、事情按什么顺序走。
- 点一个部件 → 进到**它内部的结构图**（它自己的部件和关系）。一层层点下去。
- 一直到**单个函数**——里面已经没有值得画成图的结构了——才换成**伪代码 + 源码**：左边几步人话，右边对应的原文。
- **按逻辑切，不按文件切。** 一个部件是"一件事由谁负责"，它的代码可以散在几个文件里；同一张图上的部件来自哪些文件无所谓。
- 用一个**具体例子**把整件事跑一遍：播放时沿着连线一个部件一个部件点亮，写出此刻的值。

渲染器 `template/flow.html`，构建 `build_flow.py`，截图 `flow_shot.js`，事实核对 `verify_flow.py`。

---

## 1. 你要做的判断

写进 `meta.judgment`，每条带理由。

1. **范围**：回答这个问题需要哪些部件？只画这条线上的；路过的别的子系统画成一个不展开的方框。
2. **这一层有哪些部件**（每张图 4-8 个）：部件 = 一个读者能用一句话说清"它负责什么、它不负责什么"的东西。判据：它有自己的状态吗？它做决定吗？换线程 / 过队列 / 跨进程吗？**数据结构本身也是部件**——队列、表、缓冲区、状态字段要单独画出来（`queue` / `store` / `state`），因为"谁写它、谁读它"正是结构关系。
3. **关系**：每条线回答"A 对 B 做了什么"。标签 ≤ 7 个字（`put 请求`、`读回调`、`写 key`），细节写在 `note`。主线上的线按发生顺序编号 1, 2, 3…；旁支和读写数据的线可以不编号。
4. **怎么摆**：位置表达结构。主线的方向保持一致（左→右或上→下）；数据结构放在读写它的部件旁边（通常在下方）；同一个线程 / 进程 / 层的部件用 `groups` 框在一起；回程的线绕开，不要穿过方框。**不超过 4 列 × 3 行。**
5. **在哪停下画图**：一个部件内部如果还有"几个东西 + 它们的关系"（子循环、几个阶段、几个函数互相调用、有自己的数据结构），就继续画下一层的图。只有当它就是**一个函数**、内部是顺序 / 分支 / 循环的语句时，才写成伪代码。不要把三四个函数压成一段伪代码，也不要给一个 20 行的函数画图。
6. **伪代码怎么写**：一步 = 一个动作或一个判断，人话，≤ 22 字。保留全部核心逻辑（顺序、分支、循环、改了什么状态）；日志、统计、防御性检查、与本例无关的特殊路径进 `omitted` 并说明原因。
7. **例子**：选一个能走到关键分支的具体实例，给出具体的值。

## 2. 格式

```jsonc
{
 "meta": {
   "mode": "flow",
   "title": "Kafka：一个请求穿过 broker",       // 2-6 个词
   "short": "broker",                            // 面包屑第一格
   "question": "Kafka broker 收到一个请求到发回响应，中间经过了哪些线程和队列？",
   "answer": "一句话回答（≤ 80 字），显示在顶层图的上方",
   "overview": ["面板里的背景段落"],
   "judgment": { "scope": "", "parts": "顶层为什么这样切", "stop": "在哪些地方停止画图、为什么",
                 "example": "", "omitted": "省略的标准", "revisions": ["核对 / 读者反馈后改了什么"] }
 },
 "state": [ {"id": "rq", "name": "requestQueue", "note": "共享的有界请求队列"} ],   // 例子播放时要显示当前值的状态
 "root": {
   "id": "root",
   "groups": [ {"title": "网络层 · num.network.threads", "nodes": ["acceptor", "proc"]} ],
   "edges": [
     ["client", "acceptor", "建连接", "flow", 1],                     // [from, to, 标签, 类型, 编号]
     {"from": "apis", "to": "proc", "label": "响应", "kind": "return", "n": 7, "sides": "tt",
      "note": "按 request.processor 找回原来的网络线程，放进它私有的响应队列"}   // 对象写法：可加 note、sides
   ],
   "children": [
     {"id": "client", "title": "客户端", "kind": "external", "pos": [0, 0.5], "desc": "生产者 / 消费者 / 跟随者"},
     {"id": "proc", "title": "网络线程", "kind": "thread", "pos": [1, 0], "span": [1, 1],
      "desc": "一个 selector 循环：读字节、拼请求、写响应",              // 卡片上 ≤ 2-3 行
      "code": "Processor.run()",                                        // 关键标识符，小字
      "problem": "钻进去后的页头：这个部件要解决什么问题（≤ 90 字）",
      "note": "面板里的补充（≤ 200 字）", "not": "它不是什么 / 不负责什么",
      "file": "core/src/main/scala/kafka/network/SocketServer.scala", "lines": [871, 898],   // 可选：它的主体代码
      "groups": [], "edges": [ /* 它内部那张图的连线 */ ],
      "children": [
        {"id": "o_rq", "kind": "outside", "ref": "rq", "pos": [3, 0.2]},     // 这一层之外的邻居：虚线小胶囊，点了跳过去
        {"id": "recv", "title": "收满的请求入队", "kind": "function", "pos": [2, 0],
         "desc": "解析请求头，放进请求队列，静音这条连接", "code": "processCompletedReceives()",
         "file": "core/src/main/scala/kafka/network/SocketServer.scala", "lines": [982, 1039],
         "steps": [                                                     // 函数层：伪代码
           {"title": "遍历本轮读满的每个请求", "kind": "loop", "lines": [983, 985]},
           {"title": "解析请求头", "code": "parseRequestHeader", "lines": [986, 989], "depth": 1},
           {"title": "认证会话过期了吗？", "kind": "decision", "lines": [994, 995], "depth": 1},
           {"title": "过期：关连接", "kind": "return", "lines": [996, 1000], "depth": 2},
           {"title": "放进请求队列", "kind": "call", "call": "rq_send", "lines": [1019, 1019], "depth": 1,
            "note": "队列满时这一行会阻塞整个网络线程"},
           {"title": "静音这条连接", "kind": "write", "lines": [1020, 1021], "depth": 1}
         ],
         "omitted": [[990, 992, "SASL 重新认证的特殊路径"], [1009, 1018, "记录客户端版本（KIP-511）"], [1024, 1038, "异常处理"]]}
      ]},
     {"id": "rq", "title": "请求队列", "kind": "queue", "pos": [2, 1], "desc": "所有网络线程共用，有界", "code": "requestQueue",
      "file": "core/src/main/scala/kafka/network/RequestChannel.scala", "lines": [50, 52]}
   ]
 },
 "example": {"title": "生产者发一条 acks=all 的 Produce", "token": "P7", "setup": "前提：……"},
 "walk": [
   {"node": "client", "text": "生产者发出 P7。", "value": "acks=-1"},
   {"node": "recv", "step": 5, "text": "放进请求队列。", "value": "requestQueue: 3 → 4", "state": {"rq": "4 / 500"}}
 ]
}
```

### 部件（node）

| 字段 | 说明 |
|---|---|
| `kind` | `thread` 线程 / `loop` 循环 · `component` 部件 · `function` 单个函数 · `queue` 队列 · `store` 表 / 缓冲 / 存储 · `state` 状态字段或状态机 · `external` 外部角色（客户端、内核、GPU）· `outside` 本层之外的邻居（`ref` 指向那个部件的 id） |
| `title` | 人话，≤ 14 字。`desc`：卡片上的一句话，≤ 36 字 |
| `pos` / `span` | 网格位置 `[列, 行]`（可用小数）和大小（默认 `[1,1]`，`outside` 默认小胶囊）。一格 272×176 |
| `children` + `edges` | 有它们 = 这个部件点进去是一张结构图。`edges` 必填 |
| `steps` | 有它 = 这个部件是一个函数，点进去是伪代码 + 源码。必须有 `file` + `lines`/`ranges` |
| 两者都没有 | 叶子：数据结构、外部角色、或不展开的子系统。有 `file` + `lines` 时点开看那段源码（比如队列的声明） |

### 连线（edge）

类型：`flow` 主流程 · `call` 调用 · `queue` 入队 / 出队 · `return` 回程（虚线）· `async` 异步 / 回调（点线）· `data` 读写数据 · `error`。
`sides`：两个字母强制出入边（`l r t b`），比如 `"tt"` 从上边出、上边进，用来让回程线绕开中间的方框。

### 伪代码步骤（step）

`title` 人话 · `code` 关键标识符 · `kind`：`step` `decision` `loop` `write` `wait` `return` `call` · `lines: [a, b]` · `depth` 缩进层级（在循环 / 分支里面的 +1）· `note` · `call`：这一步调用的部件 id（显示成可点的"进入 ×× ›"）。

### 例子（walk）

`node`：落在哪个部件（任意层）。`step`：落在函数的第几步伪代码（从 1 起）；不写就是点亮这个部件本身。`text` 用例子的具体值讲此刻发生了什么；`value` 最关键的一个值；`state` 这一步之后变化的状态。15-40 步；相邻两步落在同一张图的两个部件上时，它们之间最好有一条线（播放时会点亮）。

## 3. 机械检查（build_flow.py）

```bash
python3 build_flow.py flow.json --repo <仓库根> --out flow.html     # 0 ERROR 才算过
```

- 每张图：部件不重叠；连线两端都在本图；每个部件至少有一条线（否则 WARN：它和别人什么关系？）；主线要编号。
- 每个函数：伪代码步骤的行段 + `omitted` 必须盖住函数的每一行代码（空行、注释、纯括号不算）。**漏的行报 ERROR 并列出行号**——补一步，或写进 `omitted` 并说明原因。
- 卡片上的 `code` 标识符要能在它的行段里找到（找不到报 WARN，多半是行号错了）。
- 既没有 children 也没有 steps 的 `component` / `thread` / `function` 报 WARN：读者点不进去。

## 4. 核对与自查

```bash
node flow_shot.js flow.html shots/                      # 每一页一张截图，逐张看
python3 verify_flow.py flow.data.json --repo <仓库根>   # 能读仓库的检查者逐项核对事实
python3 reader_check.py flow.data.json --out reader_report.md   # 只看图的新读者能不能复述
```

看截图时查：连线有没有穿过方框、标签有没有被挡住或重叠、编号顺着读是否就是事情发生的顺序、图是不是太挤（拆一层）或太空（合并）。

**不可商量**：build 0 ERROR；代码由脚本读取（你只写文件和行号）；verify 报的事实错误逐条处理（改掉，或在 `meta.judgment.revisions` 写明为什么图是对的）；`meta.judgment` 写实话。
