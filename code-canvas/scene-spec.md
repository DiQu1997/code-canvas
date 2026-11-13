# 动画场景（scene.json）——缩放画布的最上层

`zoom.json` 旁边放一个 `scene.json`，画布打开时最上层就是一个**会动的系统图**，而不是静态框图：

- **实体有状态**：线程在跑还是阻塞在哪、队列里此刻有哪些条目、日志的游标在哪、预算被谁吃掉多少、块池里哪些格子归谁。
- **主角是同一个物体**，从出现到结束一直在画面上：沿连线移动、停在队列里、被线程取走。读者跟的是它。
- **每一步都从上一个画面动画过去**，并且带着这一步的代码：侧栏显示对应的伪代码和源码，可以一键放大到那几行。
- **放大某个实体** → 进到它对应模块的内部结构图 → 函数卡 → 伪代码 / 源码。

渲染器 `template/zoom.html`（场景引擎与 `template/scene.html` 共用），构建和检查 `build_zoom.py`，截图 `zoom_shot.js`。完整例子：`scenes/kafka.json`、`scenes/vllm.json`——**先读其中一个再动手**。

---

## 1. 你要做的判断

1. **画哪些实体（6–11 个）**。只画主角会碰到的、而且有状态可看的东西：线程 / 进程 / 循环（谁在执行）、队列和表（东西停在哪）、外部角色（请求从哪来、结果到哪去）、资源（预算、批次、块池、日志）。一个函数不是实体；"谁在执行它"才是。
2. **主角是谁**。一个请求、一个任务、一条记录。它派生出来的东西（闭包、响应、子任务）也是 token，在派生的那一步 `spawn`。
3. **每种关系怎么动**。调用 = 过去再回来（`pulse` + `back`）；交接 = 放进队列、停留、被另一个执行者取走（两次 `move`，分在两步或两拍）；回调 = 闭包被存起来，之后由**另一个**执行者调用（`cb` 连线先隐藏，被调用的那一步才 `wire` 画出来）；跨进程 = `ipc` 连线。
4. **一步讲一件事（10–20 步）**。一步 = 读者能用一句话复述的一个变化。同一步里先后发生的两件事分成两拍（`beats` 的两项）。
5. **每一步对应哪几行代码**。`at: {card, line, line2}` 指向 zoom.json 里的一张卡。画面上发生的事必须就是这几行做的事。发生在仓库之外的步骤（客户端发请求）可以不写 `at`。
6. **实体放大到哪个模块**。`module` 填 zoom.json 的模块 id；多个实体可以指向同一个模块。外部角色不填。
7. **数值**。画面上每个数都要能在代码或例子的设定里找到出处。为演示做的假设（"假设池子此刻满了"）写在那一步的 `text` 里，说明这是设定。

## 2. 格式

坐标是一张约 1300 × 650 的虚拟画布，渲染器整体缩放到屏幕。实体之间留 ≥ 50 的空隙给连线和标签；实体不能重叠（`group` 除外，它是包住别的实体的虚线框）。

```jsonc
{
 "title": "Kafka：P7 的一生", "subtitle": "一句话说清这场动画讲什么",
 "intro": "开始之前显示的一段话：画面上是谁、主角是谁（≤ 120 字）",
 "tokens": {
   "P7": {"label": "P7", "color": "c1"},                       // 普通胶囊
   "cb": {"label": "λ 回调", "color": "c3", "kind": "closure"}, // 闭包：虚线胶囊
   "A":  {"label": "A", "color": "c2", "kind": "req"},          // 带进度条的请求：用 tok 设置 done / total
   "g1": {"kind": "gray", "color": "c6"}                        // 无名的"别的请求"
 },
 "entities": [ /* 见下表 */ ],
 "wires": [
   {"id": "w_enq", "a": ["proc", "r", 0.45], "b": ["reqq", "l"], "kind": "queue", "label": "put"},
   {"id": "w_cb", "a": ["purg", "b"], "b": ["h6", "b", 0.6], "kind": "cb", "label": "onComplete → 调用闭包", "hidden": true}
 ],
 "setup": [ /* 开场前就成立的状态：一组 op，不动画 */ ],
 "steps": [
   {"text": "放进全局的 requestQueue，然后 mute 这条连接……", "value": "conn muted", "tick": "第 N 拍",
    "focus": ["proc", "reqq"],
    "at": {"card": "proc_recv", "line": 1019, "line2": 1021},
    "beats": [
      [{"op": "move", "tok": "P7", "to": ["reqq"], "via": "w_enq"}],
      [{"op": "var", "ent": "proc", "k": "连接", "v": "muted"}, {"op": "state", "ent": "proc", "s": "idle", "on": "poll(300)"}]
    ]}
 ]
}
```

颜色：`c1` 琥珀 · `c2` 蓝 · `c3` 紫 · `c4` 青绿 · `c5` 红 · `c6` 灰。主角用 `c1`。

### 实体

所有实体都有 `id` `type` `x` `y` `w` `h` `title`，可选 `sub`（一行小字）、`kind`（左上角的小标签，如 `network thread`）、`module`、`vars`（初始的 名: 值）。

| type | 画成什么 | 特有字段 | 常用大小 |
|---|---|---|---|
| `thread` | 执行者：线程 / 进程 / 主循环。底边有状态徽标（running / blocked on X / 自定义），中间一个"手里拿着什么"的槽 | `state`: `run` `blocked` `idle`；`on`: 阻塞在哪 / 空闲时在干什么 | 200 × 135 |
| `ext` | 外部角色（客户端、别的机器、内核）。虚线框，也有一个槽 | | 150 × 120 |
| `queue` | 队列 / 列表。横向时队首在右；`"dir": "v"` 竖向时队首在上 | `cap`: 容量（显示 n / cap 和填充条），`capShow`: 填充条按多少算满；`count: false` 不显示个数 | 横 210 × 46；竖 180 × 150–190 |
| `store` | 表 / 登记处：几行，每行一个 key，后面放 token | `rows: [{"id": "w", "label": "orders-0"}]` | 235 × 150 |
| `log` | 一条刻度轴上的几个游标（offset、水位） | `range: [min, max]`，`markers: [{"id","label","color","v","side":"b"}]`（`side: "b"` 画在轴下方，避免两个游标同值时重叠） | 235 × 150 |
| `grid` | 一格一格的资源：GPU 批次、块池、槽位 | `cols` `rowsN`；`count: false` 不显示空闲数 | 330 × 90–120 |
| `bar` | 一条会被分段吃掉的预算 | `total` | 330 × 30 |
| `group` | 包住几个实体的虚线框：线程池、进程边界 | | 比里面的实体每边多 20，上边多 30 |

`queue` / `grid` / `bar` 的标题画在框的**上方**（占 ~34 高），`sub` 和 `vars` 画在框的**下方**——排位置时把这两条空间留出来。`thread` 的 `vars` 也在框下方。

token 可以放在 `thread` `ext`（槽里）、`queue`、`store`（要写行：`["purg", "w"]`）里；不能放在 `log` `grid` `bar` `group` 里。

### 连线

`a` / `b`：`[实体, 边, 位置]`，边是 `l r t b`，位置 0–1（默认 0.5）。`kind`：`call` 调用 · `queue` 入队 / 出队 · `ret` 回程（虚线）· `cb` 回调（点线）· `ipc` 跨进程 · `data` 数据。`label` ≤ 12 字；`lt` 标签在线上的位置（0–1）；`bend` 弯曲程度（默认 0.4）；`hidden: true` 初始不显示。

### 状态变化（op）

一步的 `beats` 是一个数组，每项是一拍；一拍是一组**同时**发生的 op。

| op | 作用 |
|---|---|
| `{"op":"spawn","tok":T,"at":[E]}` | token 出现在实体 E |
| `{"op":"move","tok":T,"to":[E],"via":W}` | 移到 E；`via` 沿这条连线走（`"rev": true` 反向走）；队列里 `"front": true` 插到队首 |
| `{"op":"gone","tok":T}` | 消失 |
| `{"op":"tok","tok":T,"set":{"done":480,"total":1000,"tag":"PREEMPTED"}}` | `req` 类 token 的进度和头顶的标签 |
| `{"op":"state","ent":E,"s":"run｜blocked｜idle","on":"requestQueue"}` | 线程状态 |
| `{"op":"var","ent":E,"k":"连接","v":"muted"}` | 实体下方的 名: 值（数字会滚动过去） |
| `{"op":"mark","ent":E,"k":"hw","v":1005}` | `log` 的游标滑到新值 |
| `{"op":"segs","ent":E,"segs":[["A",1],["B",2047]]}` | `bar` 被谁占了多少（`[]` 清空） |
| `{"op":"batch","ent":E,"segs":[["A",1],["B",63]]}` | `grid` 从头按顺序填格子（`[]` 清空） |
| `{"op":"alloc","ent":E,"who":"C","n":8}` | `grid` 里给 who 分 n 个空格；`"hashed": true` 只取带标记的空格；`"hash": true` 分到的格子打上标记 |
| `{"op":"free","ent":E,"who":"C"}` / `{"op":"hash","ent":E,"who":"C"}` | 释放 who 的格子（标记保留）/ 给 who 的格子打标记 |
| `{"op":"pulse","wire":W,"back":true}` | 一个点沿线过去（`back` 再回来）= 一次调用 |
| `{"op":"wire","id":W}` / `{"op":"wire","id":W,"show":false}` | 画出 / 隐藏一条连线 |
| `{"op":"flash","ent":E,"color":"c5"}` | 实体闪一下（判断发生在这里、失败、满足） |
| `{"op":"run","ent":E}` | `grid` 上扫过一道光（GPU 在跑） |

`who` 可以是 token id 或 `"other"`（灰色，别的请求）。

### 一步（step）

`text`：这一步发生了什么，用例子的具体值讲，≤ 120 字。`value`：最关键的一个值。`tick`：阶段标签（"第 N 拍"）。`focus`：这一步点亮哪些实体（其余变淡；手机上镜头对准它们）——写上这一步涉及的 2–4 个。`at`：对应的代码。`beats`：画面上的变化。

## 3. 检查

```bash
python3 build_zoom.py zoom.json --repo <仓库根> --out zoom.html   # scene.json 自动并入；0 ERROR
node zoom_shot.js zoom.html shots/                                # s-step-N.png：每一步结束时的画面，逐张看
```

`build_zoom.py` 会重放整个场景：未知的实体 / 连线 / token、还没 spawn 就用的 token、实体重叠、`at` 指向不存在的卡或行，都报 ERROR。

看截图时查：token 是不是停在该停的地方；连线和标签有没有压在实体上或互相重叠（调 `a` / `b` 的边和位置、`lt`、实体间距）；`vars` 有没有和下方的实体撞上；每一步点亮的实体是不是就是文字在讲的那几个；线程状态和文字是否一致（文字说"阻塞在队列上"，徽标就得是 blocked）。

**不可商量**：build 0 ERROR；每一步的画面和它 `at` 指向的代码说的是同一件事；设定和示例值在 `intro` 或 `text` 里说明。
