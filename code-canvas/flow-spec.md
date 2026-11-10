# 逻辑线图（meta.mode: "flow"）

**可视化的伪代码。** 用户提一个问题（"一个请求从进来到返回经过了什么"），你顺着这条逻辑线把代码读完，交出一棵"步骤树"：

- 每一层是一页，**5-9 张步骤卡**，按逻辑顺序排；一页只回答一个问题。
- 每张卡先说**人话**（做了什么 / 决定了什么），再给关键的函数名或变量名，最后是 `文件:行号`。
- 点一张卡钻进去 = 换成下一页：这一步内部的 5-9 步。钻到底是源码。
- **逻辑线是骨架，不是文件。** 同一页相邻的两步经常来自不同文件、不同类——真实的执行路径就是这样的。文件只是卡片上的一个小标注。
- 用一个**具体例子**把整条线跑一遍：每步写出此刻的值，旁边的状态跟着变。

伪代码的要义：去掉语法、套路和管道，**保留全部核心逻辑**——顺序、分支、循环、谁改了什么状态、在哪做决定。你做的每一次"合并成一步"和"省略"都是判断，都要能对回行号。

渲染器 `template/flow.html`，构建 `build_flow.py`，截图 `flow_shot.js`，事实核对 `verify_flow.py`。

---

## 1. 你要做的判断

写进 `meta.judgment`，每条带理由。

1. **主线**：用户的问题对应哪一条执行路径？从哪一行开始算、到哪一行结束？一张图只走一条线；路过的别的线做成黑盒。
2. **主角和例子**：谁在这条线上"活着"（一个请求、一个 fiber、一拍调度、一条命令）？选一个具体实例，给出具体的值（`SET k v`、`prompt 1000 token、前 480 已缓存`），要选能走到关键分支的例子。
3. **顶层怎么切**：5-9 个阶段。切点选在**所有权交接**的地方（换线程、过队列、跨进程、从"收"变成"算"变成"发"），不是文件边界。
4. **每一步是什么**：一步 = 一个读者需要知道的动作或决定。判据："它改状态吗？它做决定吗？去掉它后面的事还说得通吗？"都不是 → 归入 `omitted`。
5. **钻多深**：复杂的阶段钻两三层；简单的一层到源码。一个叶子步骤对应 ≤ 45 行源码，读者能一眼看完。
6. **什么做成黑盒**：主线调用了、但展开会离题的函数（另一个子系统、通用工具、别的逻辑线）。只写契约：吃什么、吐什么、副作用。
7. **每页的版式**：默认是自上而下的一条线（伪代码的阅读方向）。只有代码真是那个形状时才换：每圈固定几站的循环 → `loop`；带分支汇合的判断 → `flow`；一个判断点分出几支 → `dispatch`；状态枚举 → `statemachine`。

## 2. 格式

```jsonc
{
 "meta": {
   "mode": "flow",
   "title": "Redis：一条 SET 命令的一生",          // 2-6 个词的名字
   "question": "Redis 收到一条 SET 命令到回复客户端，中间经过了什么？",   // 用户的问题，原样或略整理
   "answer": "一句话回答：……",                     // 顶层页头，≤ 80 字
   "overview": ["背景段落（面板里显示）"],
   "judgment": { "line": "", "protagonist": "", "stages": "为什么这样切", "boxes": ["哪些做成黑盒、为什么"],
                 "example": "为什么选这个例子", "omitted": "省略的标准", "revisions": ["核对/读者反馈后改了什么"] }
 },
 "lanes": [ {"id": "io", "title": "主线程 · 事件循环", "kind": "thread"} ],     // 谁在执行：线程 / 进程 / 组件。卡片左侧色条
 "state": [ {"id": "qb", "name": "c->querybuf", "type": "sds", "shape": "queue", "note": "读进来还没解析的字节"} ],
 "root": {
   "id": "root", "title": "一条 SET 命令的一生",
   "children": [
     {"id": "read", "title": "把 socket 里的字节读进查询缓冲区", "code": "readQueryFromClient()", "kind": "step",
      "lane": "io", "file": "src/networking.c", "lines": [2650, 2760],
      "problem": "字节可能分几次到，也可能一次到好几条命令。这一步只管读，不管解析。",   // 钻进去后的页头
      "note": "面板里的补充说明：细节、为什么这么做、容易误解的地方。",
      "reads": [], "writes": ["qb"],
      "omitted": [[2655, 2672, "主从复制的特殊路径"], [2731, 2740, "统计与日志"]],
      "layout": "line",
      "children": [ /* 下一层的 5-9 步；没有 children 就是叶子，点开是源码 */ ]},
     {"id": "exec", "title": "查命令表，执行 SET", "code": "processCommand() → call()", "kind": "step", "lane": "io",
      "layout": "flow", "edges": [["chk", "acl", "通过"], ["chk", "rej", "不通过"]],
      "children": [
        {"id": "chk", "title": "命令存在吗？参数个数对吗？", "kind": "decision", "file": "src/server.c", "lines": [3980, 4010]},
        {"id": "rej", "title": "回一个错误，结束", "kind": "return", "file": "src/server.c", "lines": [4011, 4018]},
        {"id": "aof", "title": "把这次写入传播到 AOF 和从库", "code": "propagateNow()", "kind": "box",
         "contract": {"in": "命令和参数", "out": "无", "effect": "追加到 AOF 缓冲区和复制缓冲区"},
         "file": "src/server.c", "lines": [3301, 3301]}
      ]}
   ]
 },
 "example": {"title": "client 7 发来 SET greeting hello", "token": "SET", "setup": "开头交代的前提：……"},
 "walk": [
   {"node": "read", "text": "client 7 的 socket 可读，事件循环调到 readQueryFromClient。", "value": "nread = 33",
    "state": {"qb": "*3\\r\\n$3\\r\\nSET…"}},
   {"node": "chk", "line": 3992, "text": "在命令表里查到 setCommand，参数 3 个，符合 arity=-3。", "value": "c->cmd = setCommand"}
 ]
}
```

### 节点字段

| 字段 | 说明 |
|---|---|
| `id` | 全图唯一 |
| `title` | **人话**，一个动作或一个问题，≤ 26 字。不要复述函数名。判断卡写成问句 |
| `code` | 关键标识符（函数 / 变量 / 条件），≤ 40 字符，等宽小字显示 |
| `kind` | `step` 普通步骤 · `decision` 判断 · `loop` 循环（钻进去是每一圈做什么）· `write` 以改状态为主 · `wait` 挂起 / 入队 / 等别的线程 · `return` 出口 · `box` 黑盒 · `external` 外部角色（客户端、内核、GPU），无代码 |
| `file` + `lines` / `ranges` | 这一步对应的源码。`lines: [a, b]`；不连续用 `ranges: [[a,b],[c,d]]`。除 `external` 外都要有 |
| `lane` | 谁在执行（`lanes` 里的 id） |
| `problem` | 钻进这一步之后的页头：这一段要解决什么问题（≤ 90 字）。有 `children` 的节点必填 |
| `note` | 面板里的补充（≤ 200 字） |
| `reads` / `writes` | 读 / 写了哪些 `state` |
| `omitted` | `[[a, b, "原因"]]`：这一步的行段里，子步骤没有覆盖、你判断不属于核心逻辑的行 |
| `children` | 下一层的步骤，按逻辑顺序。**3-9 个** |
| `layout` | `line`（默认）· `flow` · `loop` · `dispatch` · `statemachine` |
| `edges` | `[[from, to, "标签"]]`，都是本层的兄弟 id。`line` 版式不写就是依次相连，写了的是额外的跳转（跳过 / 回头）；`flow` 和 `statemachine` 必写；`dispatch` 自动从第一个子节点连到其余 |
| `contract` | 黑盒的契约 `{in, out, effect}` |
| `ev` | `"infer"` 推断的 · `"unknown"` 没把握（面板里会标出来）。核实过的不写 |

### 例子（walk）

- `walk[].node`：这一步落在哪个节点（任意深度）。播放器会带读者进到它所在的那一页，把它点亮。
- `text`：此刻发生了什么，用例子里的具体值讲。`value`：这一步最关键的一个值。`line`：可选，具体到哪一行。
- `state`：这一步之后发生变化的状态（`state` 的 id → 显示的值）。没变的不写，播放器会沿用之前的值。
- 15-40 步；顶层每个阶段至少经过一次；要走到至少一个真正的分支点，并说出为什么走这边。

## 3. 机械检查（build_flow.py）

```bash
python3 build_flow.py flow.json --repo <仓库根> --out flow.html     # 读代码 + 校验 + 渲染；0 ERROR 才算过
```

- **行号对得上**：每个节点的文件和行段存在；`code` 里的标识符在它的行段里能找到（找不到报 WARN——多半是行号错了）。
- **覆盖**：父节点有行段时，同一文件里落在它范围内的子节点行段 + `omitted`，必须盖住父节点的每一行代码（空行、纯注释、纯括号不算）。**漏掉的行报 ERROR 并列出行号**——要么补一步，要么写进 `omitted` 并说明原因。这是伪代码"保留全部核心逻辑"的底线：读者能看到你省略了什么。
- 子节点跨文件（调进了别的函数）不参与覆盖计算，这是正常的。
- 叶子 ≤ 45 行（硬上限 80）；每层 3-9 步（硬上限 12）。

## 4. 核对与自查

```bash
node flow_shot.js flow.html shots/                      # 每一页一张截图，逐张看
python3 verify_flow.py flow.data.json --repo <仓库根>   # 另起一个能读仓库的检查者，逐步核对"这几行真的在做这件事吗"
python3 reader_check.py flow.data.json --out reader_report.md   # 只看图的新读者能不能复述出来
```

- `verify_flow.py` 报出的每一条事实错误都要处理：改掉，或在 `meta.judgment.revisions` 里写明为什么它是对的。
- 看截图：标题有没有被截断、每页是否一屏看得完、分支标签是否可读。

**不可商量**：build 0 ERROR；代码由脚本读取（你只写文件和行号）；verify 报的错误逐条处理；`meta.judgment` 写实话。
