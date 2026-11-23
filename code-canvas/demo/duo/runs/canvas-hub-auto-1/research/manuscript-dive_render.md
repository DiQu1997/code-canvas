# 底稿（补充）：“读者浏览器”这个框里面——像素是怎么排出来的

> 研究对象：code-canvas 仓库，HEAD = `79153e7`。路径相对仓库根；除特别注明外，`code-canvas/template/canvas.html:行号` 指 `code-canvas/template/canvas.html`。
> 承接上一趟底稿 §2.7、§3 第 7 步、§5 第 1 条（下称"上一趟"）。例子沿用上一趟的 nano-vllm 画布、`/c/nano-vllm/`。
> 标【设】的值是我为了让机制"看得见"而设的；"为什么"后的【…】是来源等级。引文逐字照抄，写成“…”（用于需核对的原话时是「」）。

---

## 0. 先说结论

**一句话**：浏览器拿到的 html 里已经有整份画布数据 `DATA`（`code-canvas/template/canvas.html:740`）。页面脚本自己把数据变成 DOM 卡片（每张卡、每一行代码都有固定 id），然后**只做三件事**：

1. **`layout()`，只在页面加载时跑一次**：量每张卡的自然宽度 → 按 `col` 算每一列的 x → 按 `band` 算每张卡的基准 y（`code-canvas/template/canvas.html:1034-1081`）。
2. **`drawWires()`，每次有变化都整体重画**：先把同列的卡从上往下"推开"防重叠，再按每根线的锚点算贝塞尔曲线，全量重建 SVG（`code-canvas/template/canvas.html:1287-1293`）。
3. **`setStep(i)`，按 ▶ 时跑**：展开卡、展开块、点亮行、变暗无关的卡、算镜头，最后再调一次 `drawWires()`（`code-canvas/template/canvas.html:1689-1726`）。

agent 交的 JSON 里只有 `col / band` 这种粗位置，没有像素。服务端也不排版（只在 html 比模板旧时重新 render，见上一趟 §2.7）。**像素是读者的浏览器在读者的屏幕上排出来的。**

**对上一趟顶层图的一处需要修正的地方**：顶层图/说明里写的"col 0 → x = 40"只在**没有左侧 note、没有角落 note** 的画布成立。仓库里所有深潜样例（nano-vllm 等）都有一张 `corner: nw` 的背景 note，所以它们的 col 0 的 x 是 **370**，不是 40（§2.2）。`band 2 → y = 720` 则没问题，但那只是**基准** y，实际 y 可能被推下去（§2.3）。

**读者最容易误会的三点**（后文展开）：
- 宽度只在加载时量一次，之后卡片宽度固定；展开/折叠只改高度，所以**列的 x 永远不动，动的只有 y**。
- “展开了什么”是**只增不减**的：`setStep` 只会去掉 `collapsed` / `folded`，从不重新折上。往回按 ◀ 并不会把后面步骤展开的东西收回去（§2.5）。
- 变暗哪些卡由**这一步点亮的线的两端**决定，不是由 `focus` 决定（§2.5）。

---

## 1. 这个框里有哪几样东西

按"谁持有什么状态"画（讲解员画框线用）：

| # | 部件 | 它是什么 / 状态放哪 | 谁写、谁读 |
|---|---|---|---|
| ① | **`DATA`** | 注入进来的整份画布 JSON，只读。`const DATA = /*CANVAS_DATA*/null;`（`code-canvas/template/canvas.html:740`）；服务端/渲染脚本把占位符换成 JSON（`code-canvas/render.py:22-25`） | 所有部件只读它 |
| ② | **DOM 舞台** | `#world` 下面的 `.card`、`.note`、`.region` 元素，加两层 SVG（`#wires`、`#routes`）。卡片 id 是 `card-<卡id>`（`code-canvas/template/canvas.html:952`），每一行代码的 id 是 `<卡id>-L<行号>`（`code-canvas/template/canvas.html:946`，行号是**卡内**行号，不是源文件行号） | 页面加载时一次性建好 |
| ③ | **折叠状态** | 没有 JS 变量，直接存在 DOM 的 class 里：整卡 `.collapsed`，块 `.blk.folded`（`code-canvas/template/canvas.html:1047-1048`） | `layout()` 设初值；`setStep`、点击改它 |
| ④ | **布局数据** | 挂在 `DATA.cards[i]` 上的 `_w`（宽）、`_x`、`_baseY`（基准 y）、`_y`（实际 y）；常量表 `L`（`code-canvas/template/canvas.html:1031-1032`） | `layout()` 写 `_w/_x/_baseY`；`relayoutColumns()` 写 `_y` 和 `style.top` |
| ⑤ | **步进状态** | 模块级变量 `step` 和 `STEPS`（`code-canvas/template/canvas.html:1240-1241`）；没有 `steps` 时退化成只有一步的"总览" | `setStep` 写；`drawWires` 读 |
| ⑥ | **相机** | `cam = { x: 0, y: 0, s: .8 }`，`apply()` 把它变成 `#world` 的 CSS `transform`（`code-canvas/template/canvas.html:1376-1380`）。拖动、滚轮、`centerOn`、`fitAll`、`focusRead` 都在改它 | 见 §2.6 |
| ⑦ | **手机卡片流** | `<700px` 时换一种"投影"：同一批卡片 DOM 被**搬进** `#mstream` 竖排，不是复制（`code-canvas/template/canvas.html:2103-2184`） | 见 §2.7 |
| ⑧ | **问答开关** `QA.live` | 页面加载时 `fetch('__alive')` 探测 hub 在不在（`code-canvas/template/canvas.html:1801`） | 见 §2.8 |

**时间线（一次打开）**：
1. 脚本执行时就把卡片、note、region 的 DOM 建好（顶层循环，如 `code-canvas/template/canvas.html:894-960`）。
2. `requestAnimationFrame` 里依次：`layout()` → `setStep(#sN 或 0, false)` → `syncStreamMode()`（`code-canvas/template/canvas.html:2895-2906`）。
3. 之后一切靠事件：▶/◀/方向键 → `go()` → `setStep`（`code-canvas/template/canvas.html:1560-1568`、`code-canvas/template/canvas.html:1785-1795`）；点卡头/块条 → 切 class → `drawWires()`（`code-canvas/template/canvas.html:2799-2811`）；窗口 resize → `syncStreamMode()` + `drawWires()`（`code-canvas/template/canvas.html:2884-2888`）。

> 小贴士：URL 末尾加 `#s3` 能直接跳到第 3 步——「#s2 jumps to step 2」（`code-canvas/template/canvas.html:2891`）【注释】。讲解员做演示/验证时可用。

---

## 2. 读者会依次冒出的问题

### 2.1 卡片的"自然宽度"怎么量？800px 上限碰到会怎样？

**先量宽，后定宽**（`code-canvas/template/canvas.html:1035-1052`）：

1. 第一个循环：把卡**展开**（去掉 `collapsed`）、把所有块**摊开**（去掉 `folded`），设 `style.width = 'max-content'`，读 `offsetWidth`，`c._w = Math.min(el.offsetWidth + 2, 800)`（`code-canvas/template/canvas.html:1038-1041`）。
2. 第二个循环：把 `style.width` 固定成 `c._w`，再套回默认折叠状态：`c.collapsed` 的卡折上、所有块折上、再按 `folded:false` 例外打开（`code-canvas/template/canvas.html:1046-1051`）。

要点：
- 卡里每行代码是 `white-space: pre`（`code-canvas/template/canvas.html:43`），所以"最大内容宽度"就是**最长那一行**加上行号、内边距、块的缩进等装饰。没有任何换行。
- 量的是**展开 + 摊开**状态，所以即使一张卡默认折着（如 nano-vllm 的 `run`、`sampler`），它的宽度也按"全展开时最宽"定。这样后面展开它不会让它变宽，列的 x 不用重排。【推断】：代码里只有注释「measure natural card widths (expanded, unfolded)」（`code-canvas/template/canvas.html:1035`）说"量什么"，没写"为什么"；"宽度固定→列 x 稳定"是我从"之后没有任何地方重算 `_w` 或 `colX`"推出来的（`layout()` 全文件只在 `code-canvas/template/canvas.html:2896` 被调一次，我用 grep 核过）。
- `+ 2` 没有注释。因为全局 `* { box-sizing: border-box; margin: 0; padding: 0; }`（`code-canvas/template/canvas.html:8`），`offsetWidth` 已含边框，所以多出来的 2px 看起来是留的余量。【推断】（依据：border-box 下不加也够；没查到原因，见 §7）。

**碰到 800 会怎样**：宽度被钉死在 800；而卡片 `overflow: hidden`（`code-canvas/template/canvas.html:29`）、行是 `white-space: pre`，所以超出的那一截代码**被裁掉，桌面端没有横向滚动条**（手机卡片流里才给 `.body` 加 `overflow-x: auto`，`code-canvas/template/canvas.html:614`）。【推断】：这是我从两条 CSS 规则推出的，没在浏览器里点过。
旁证：validate 对超过 100 字符的行报 warn，措辞就是「卡片会到测量上限被截断」（`code-canvas/validate.py:202-203`）【注释】；SKILL 要求「行宽目标 ≤90 字符」（`code-canvas/SKILL.md:143`）。

**为什么上限是 800**：「真实代码带来的渲染器修正：卡片宽度上限放宽到 800px（真实行」（`code-canvas/DESIGN.md:147`）【文档】——也就是原来更窄，拿 nano-vllm 真实代码试时发现真实行常见 90+ 字符，才放宽的。

**例子（设）**：nano-vllm 的 `sampler` 最长行 104 字符，`schedule` 101 字符（我从 `demo/nano-vllm.json` 里数的）。按等宽字体 12px（`code-canvas/template/canvas.html:43`）每字符约 7.2px【设】加上行号列、内边距共约 60–70px，这两张卡的自然宽度都在 800 上下，我取 `w0 = 800`、`w1 = 800`【设】；col 2 最长 `hash_blocks` 79 字符，取 `w2 = 640`【设】。**我没有在真实浏览器里量过**，真实数字随字体回退会变。

### 2.2 col 怎么变成 x？col 1 的 x 等于多少？

**公式**（`code-canvas/template/canvas.html:1053-1069`），从左到右累加：

```
x = 40 (L.margin)
    + (有"左侧 note 区"? 250 + 80 : 0)         ← hasLeftNotes
对每一列 k（从小到大）：
    如果这一列的卡有 side:'left' 的 note：x += 250 + 70   ← leftNoteCols
    colX[k] = x
    x += 这一列最宽卡的 _w + 230 (L.gutter)
```

- `hasLeftNotes` 为真的条件：有 note 的 `place.corner`（任何角落 note），**或**有 `side:'left'` 的 note 指向 **col 0** 的卡（`code-canvas/template/canvas.html:1054-1055`）。
- `leftNoteCols`：`side:'left'` 的 note 指向 **col > 0** 的卡时，那一列前面多留一条 note 车道（`code-canvas/template/canvas.html:1056-1060`、`code-canvas/template/canvas.html:1066`）。SKILL 也提到这条：「左置 note 指向非 0 列的卡时会加一条 ~320px 的 note 车道」（`code-canvas/SKILL.md:191`）【文档】（320 = 250 + 70）。
- **列宽 = 本列最宽的那张卡**（`code-canvas/template/canvas.html:1068`），所以一张超宽卡会把整列后面所有列推远。SKILL 的建议「行宽收窄可救列宽」（`code-canvas/SKILL.md:190`）【文档】就是这个道理。

**nano-vllm 里的实际情况**：
- 画布里有 `bg0`，`place: {corner: 'nw'}`（`code-canvas/demo/nano-vllm.json:656-661`）→ `hasLeftNotes = true`。所以 **col 0 的 x = 40 + 250 + 80 = 370**，不是 40（**纠正上一趟 §3 第 7 步的"x = 40"**——那一句限定了"没有左侧 note 时"，但这张画布并不属于那种情形）。
- `n-chunk`、`n-preempt` 是 `side:'left'`、`of:'schedule'`（col 1）→ `leftNoteCols = {1}`。
- 所以 **col 1 的 x = 370 + w0 + 230 + 320 = 920 + w0**。设 `w0 = 800`【设】→ 1720。
- 同理（设 `w1 = 800`、`w2 = 640`）：col 2 的 x = 1720 + 800 + 230 = 2750；col 3（只有 `kv-pool` 一张）的 x = 2750 + 640 + 230 = 3620【设】。

**"col 1 的 x 跟哪些卡有关"**：只和 col 0 最宽的那张卡的 `_w`、以及有没有左侧 note 有关；跟 col 1 自己的卡宽无关（那决定的是 col 2 的 x）。

### 2.3 band 怎么变成 y？同一列撞上了怎么推？

**基准 y**：`c._baseY = L.top + c.layout.band * L.bandH`，即 `160 + band × 280`（`code-canvas/template/canvas.html:1075`）。nano-vllm 里 `step` band 0 → 160；`run` band 2 → 720；`sampler` band 3 → 1000。

**这只是"想放的位置"。实际 y 在 `relayoutColumns()` 里定**（`code-canvas/template/canvas.html:1136-1151`）：把一列的卡按 `_baseY` 排序，从上往下走，`y = max(_baseY, 上一张的底边 + 60)`，把结果写进 `style.top` 和 `c._y`（`code-canvas/template/canvas.html:1141-1148`）。

- 这个函数在 `layout()` 末尾调一次（`code-canvas/template/canvas.html:1077`），**之后每次 `drawWires()` 开头也调**（`code-canvas/template/canvas.html:1290`），所以每次展开/折叠/换步，都会重新推一遍。
- 为什么是"每次重画都推"而不是"布局时一次定"：卡会随展开折叠变高，布局时的高度和后来不一样。注释：「cards grow and shrink as they expand/fold; keep columns overlap-free」（`code-canvas/template/canvas.html:1137`）【注释】；DESIGN 里也写「同列冲突下推改为每次重绘动态」（`code-canvas/DESIGN.md:148`）【文档】，并且是**因为**在 nano-vllm 真实样例上发现「展开的卡会撞到下方卡」（`code-canvas/DESIGN.md:149`）【文档】才改的。测试佐证：`note-follow.mjs` 断言 'bottom card was pushed below expanded top'（`code-canvas/tests/note-follow.mjs:43-44`）【测试】。
- 间距 **60 是写死的魔数**。`L` 里确实有一个 `vGap: 90`（`code-canvas/template/canvas.html:1031`），但全文件没有任何地方用到它——它是个**名不副实的残留常量**（我 grep 过，只出现一次）。别被它骗了。

**例子（设，只演示机制）**：col 1 有 `schedule`（band 0）、`preempt`（band 2，基准 720）、`postprocess`（band 3，基准 1000）。
- 初始（块都折着）：`schedule` 很矮，设约 300 高 → 底边 460 → `preempt` 的 720 > 460+60，不推。
- 到第 ③ 步，`unfold` 摊开 `schedule` 的 `Prefill 批次`（行 5–31，一块 27 行；每行 21px，因为 `12px/1.75`，`code-canvas/template/canvas.html:43`）。设 `schedule` 变成约 900 高 → 底边 1060 → `preempt.y = max(720, 1060+60) = 1120`；`preempt` 折着、设 34 高 → 底边 1154 → `postprocess.y = max(1000, 1154+60) = 1214`。**一张卡展开，会把它下面整列都推下去。**
- 以上高度都是【设】，只有 `+60`、`160`、`280`、`21` 来自代码。

**其他东西跟着动**：`placeNotes()`（note 跟着目标卡走，空间不够时从"上方"退到"左侧"，`code-canvas/template/canvas.html:1085-1135`）、`fitRegions()`（故事线的色块实时包住成员卡 + 内边距，`code-canvas/template/canvas.html:1152-1176`）、`sizeWorld()`（世界尺寸 = 内容包围盒 + 右 200、下 260，`code-canvas/template/canvas.html:1177-1185`）都在 `drawWires()` 里跟着重来（`code-canvas/template/canvas.html:1290`）。note 不会和卡重叠由 `note-follow.mjs` 断言（`code-canvas/tests/note-follow.mjs:45-47`）【测试】。

### 2.4 连线怎么锚到某张卡的某一行？

数据里每根线有 `from: {card, line, side?}` 和 `to: {card, line?, side?}`（例：`w-sched` 是 `step` 第 2 行 → `schedule` 整卡）。`drawWires()` 对每根非 route 线做（`code-canvas/template/canvas.html:1317-1320`）：

```
a = resolveAnchor(w.from, null)
b = resolveAnchor({ side: 'left', ...w.to }, w.kind === 'data' ? 'data' : 'call')
控制点偏移 k = clamp(max(|dx|×0.55, |dy|×0.25), 30, 170)
```

`resolveAnchor`（`code-canvas/template/canvas.html:1208-1234`）的分支，这是最值得读的一段：

1. 有 `line`：按 id `卡id-L行号` 找到那一行的 DOM（`code-canvas/template/canvas.html:1212`），再过 **`visibleProxy`**。
2. **`visibleProxy`（`code-canvas/template/canvas.html:1198-1207`）**：如果卡整个折着 → 锚到卡本身；如果那行当前可见（`offsetParent !== null`）→ 锚到行；否则沿祖先找**最外层折着的块**，锚到它的标题条 `.bbar`；都不是就退回整卡。注释：「line hidden? cascade: outermost folded block bar -> card」（`code-canvas/template/canvas.html:1199`）【注释】。→ **线锚永远不会指向看不见的东西，行藏起来了就"降级"到包住它的块条/卡头。**
3. 内部元素（行/块条）：`y = 元素中线`；若那行在"一体滚动窗"里被滚出可视区，把 y 夹在窗口边缘 ±6px（`code-canvas/template/canvas.html:1219-1226`）。注释：「行在一体滚动窗里被滚出视野时，锚点夹在窗口边缘（线不飘出卡外）」（`code-canvas/template/canvas.html:1221`）【注释】。
4. 整卡（没写 line 的端点，通常是 `to`）：`slot` 为 `'call'` 时 y = 卡顶 + 16（贴着卡头）；折着时是卡中线 − 5；`'data'`（数据线）时落在卡高的 62% 处（折着时中线 + 7），这样 call 线和 data 线进同一张卡不会重叠（`code-canvas/template/canvas.html:1228-1231`）。【推断】：用 -5/+7 错开的理由是我从数值推的，代码没写。
5. x：`side` 默认——起点 `'right'`，终点 `'left'`（`code-canvas/template/canvas.html:1216-1217`；`to` 在 `code-canvas/template/canvas.html:1319` 被默认成 `side:'left'`）。**同列两张卡之间的线**（如 nano-vllm 的 `w-run`：`step` 第 4 行 → `run`）数据里把两端都写成 `side:'left'`，曲线就从左边绕出去再绕回来，不穿过中间的卡（`code-canvas/demo/nano-vllm.json` 里 `w-run`）。

**坐标怎么取**：`wpos(el)` 沿 `offsetParent` 链一路累加 `offsetLeft/offsetTop` 直到 `#world`，再**减去沿途滚动容器的 `scrollTop/scrollLeft`**（`code-canvas/template/canvas.html:1186-1197`）。用 offset 而不是 `getBoundingClientRect`，所以结果是"世界坐标"，不受相机缩放/平移影响。【推断】（依据：offset 系列不含 CSS transform；代码没明说）。减滚动位移这一段是后来加的：提交 398eb5c「线锚随滚动重钉：wpos 减滚动容器位移；行滚出窗口时锚点夹在窗缘；滚动事件 rAF 节流重绘」【提交】。

**别的线**：`route`（故事线路）不走端口，而是在"底层 SVG"上画站心到站心的浅弧：「故事线路：底层 SVG、站心浅弧——不走端口贝塞尔（那是绕圈的元凶）」（`code-canvas/template/canvas.html:1296`）【注释】。其余细节（箭头、标签、leader 线）不展开。

### 2.5 按 ▶ 时 `setStep` 到底做了什么？

▶ 按钮 → `go(1)`（`code-canvas/template/canvas.html:1560-1568`；有"带读"分段的步会先在步内推进）→ `setStep(step+1)`。`setStep` 的顺序（`code-canvas/template/canvas.html:1689-1726`）：

| 序 | 做什么 | 锚点 |
|---|---|---|
| 1 | `step = clamp(i)`，取 `s = STEPS[step]` | 1690-1691 |
| 2 | `s.expand`：去掉这些卡的 `collapsed` | 1695-1699 |
| 3 | `s.unfold`：按**块名**在该卡的 `.blk` 里找到，去掉 `folded`（只匹配 `.bname` 文本，找不到就静默跳过） | 1700-1704 |
| 4 | 清掉所有 `.ln.hot`，再给 `s.lines` 里的行加 `hot`（蓝条） | 1705-1709 |
| 5 | **变暗**：收集 `s.wires` 里每根线的两端卡，**不在其中的卡**加 `dim`（透明度 .32，`code-canvas/template/canvas.html:68`）；这一步没有 `wires` 字段就一张都不暗 | 1710-1715 |
| 6 | note：`n.step === 当前步` 的亮（`hot`），其余变暗（总览步除外） | 1716-1720 |
| 7 | 状态卡（`kind: 'state'`）按当前步选快照并做 diff | 1721 |
| 8 | **镜头**：手机流 → `buildStream()`；带读步 → 交给 `showSeg`；`s.fit` 或没有 `focus` → `fitAll`；否则 `focusRead` | 1723-1725 |
| 9 | `drawWires()`：**同列重推 + 重建所有线，有 `wires` 的步里 `on`/`off` 区分亮暗** | 1726 |
| 10 | 刷新标题、说明、预告、详解、进度点 | 1727-1760 |

几个**容易看错**的地方：
- **展开是只增不减**。第 2、3 步只会 `remove('collapsed'/'folded')`，整个 `setStep` 里没有任何代码把它们加回去（折叠初值只在 `layout()` 设一次，`code-canvas/template/canvas.html:1047-1048`）。所以：先看完第 ④ 步再点 ◀ 回第 ③ 步，第 ④ 步展开的 `preempt` 仍是展开的。这是我读代码得到的结论；没在浏览器里点过。**这是有意设计还是副作用，我没查到**（§7）。
- **暗不暗看线不看 focus**（第 5 步）。例：nano-vllm 第 ② 步 `wires: ['w-sched']`、`focus: ['schedule','n-chunk','step']` → 亮的是 `step` 和 `schedule`；第 ③ 步 `wires` 是 `w-canalloc / w-alloc / d-cached / s-pool` → 亮的是 `schedule / can_allocate / allocate / kv-pool`，其余 6 张卡变暗。`focus` 的实际用处是镜头（`focusRead` 的兜底）、读序徽标 ①②（`code-canvas/template/canvas.html:1360-1373`）和手机上列哪些卡（`code-canvas/template/canvas.html:2125`）。为什么暗只看线、不看 focus：**没查到**（§7）。
- 总览步（第 0 步）没有 `wires` 字段、`fit: true` → 所有卡不暗、所有线用默认样式、镜头 `fitAll`。
- 第 5 步的 `dim`、第 4 步的 `hot` 每步重算（是"纯"的）；只有展开状态带着历史。
- 镜头（第 8 步）**在 `drawWires()`（第 9 步）之前**算，用的是推开之前的位置。如果这一步展开的卡把另一张被聚焦的卡推下去了，镜头会略有偏差。【推断】：纯从代码顺序看出来；nano-vllm 的几步里被框的行都在被展开的那张卡本身，应该不触发。

**例子（接上一趟第 7 步，nano-vllm 第 ① 步）**：
`STEPS[1]` 是 `wires: [w-sched, w-run, w-post, d-token]`，`lines: [[step,2],[step,4],[step,5]]`，`focus: [step, schedule, run, postprocess]`，没有 `expand`（`code-canvas/demo/nano-vllm.json` 的 steps[1]）。所以：
- `run`、`postprocess` **保持折叠**（它们默认 `collapsed`），线 `w-run`、`w-post` 的终点锚到折着的卡头；
- `step` 的第 2、4、5 行亮蓝条；
- 变暗：`sampler`、`preempt`、`can_allocate`、`allocate`、`hash_blocks`、`kv-pool`（不是这 4 根线的端点）；
- 镜头：`readRect` 只收 `lines`（以及 unfold 块、本步 note），所以框的是 `step` 卡里第 2–5 行这一小条，而不是 4 张 focus 卡（§2.6）。

### 2.6 镜头怎么算？

相机是 `cam = {x, y, s}`，`centerOn(wx, wy, s)` 把世界坐标 `(wx, wy)` 放到屏幕中心：`cam.x = innerWidth/2 − wx·s`，`cam.y = (innerHeight−70)/2 − wy·s`（`code-canvas/template/canvas.html:1381-1386`；那 70 大概是底栏的高度，【推断】）。`#world` 上有 `transition: transform .55s`，所以步进是"飞过去"；`animate=false` 时加 `notrans`（`code-canvas/template/canvas.html:15-18`、`code-canvas/template/canvas.html:1378`）。

每步的镜头选择（`code-canvas/template/canvas.html:1725`）：
- **总览** `fitAll`：包围盒缩放到刚好装下，`s` 最大 1.15、最小 0.1（`code-canvas/template/canvas.html:1387-1392`）。注释：「never crop the overview — wide canvases fit at whatever scale it takes」。
- **聚焦步** `focusRead(s)`（`code-canvas/template/canvas.html:1444-1458`）：
  1. `readRect(s)` 收集"读点"：`lines` 里的行（经 `visibleProxy`）∪ `unfold` 的块 ∪ 本步 note 及其锚点；求包围盒（`code-canvas/template/canvas.html:1412-1442`）。
  2. 算能装下读点的比例 `sc = min(宽/(w+140), (高−170)/(h+140), 1.15)`；**`sc ≥ 可读下限`** 就居中到包围盒，否则**改为怼住第一个读点**，用可读下限做缩放，其余靠读者平移（`code-canvas/template/canvas.html:1455-1457`）。
  3. 可读下限 `READ_FLOOR`：桌面 **.85**、手机 **.5**（`code-canvas/template/canvas.html:1411`）。
  4. 没有任何读点（老数据）：不整卡缩小，对准第一张 focus 卡的**顶部**（`code-canvas/template/canvas.html:1446-1454`）。
- 老的 `focusOn`（整卡包围盒，下限 .35，手机 .12）现在只是兜底（`code-canvas/template/canvas.html:1393-1406`）。

**为什么这么做**：注释：「长函数整卡取景会缩成看不清（作者反馈）」「缩到可读比例以下就改为怼住阅读起点（第一个读点），其余靠平移」（`code-canvas/template/canvas.html:1408-1410`）【注释】。提交 4b9fa78：「读点取景：步进镜头对准本步要读的行/块，缩不下就怼阅读起点」；ROADMAP 记原话「作者反馈：进一步时画面太大，要直接 zoom 到这一节该读的那几行」（`ROADMAP.md:236-237`）【文档】，并说黄金样本各步比例从整卡取景的 .4–.6 变成 .85–1.15（`ROADMAP.md:240-241`）。测试：`walk.mjs` 断言镜头缩放 ≥ 0.85（`code-canvas/tests/walk.mjs:85`）【测试】。

**例子（设）**：视口 1400×900【设，测试里用的就是这个尺寸，`code-canvas/tests/walk.mjs:60`】。
- 第 ① 步：读点 = `step` 卡的第 2、4、5 行。设 `step` 卡宽 720、三行占 4 行高约 84 → `sc = min(1400/860, 730/224, 1.15) = 1.15` ≥ .85 → 居中到这几行，缩放 1.15。注意此时 `schedule`（x ≈ 1720）在屏幕外，只看得到起点和线探出去的方向。
- 第 ③ 步：读点 = `schedule` 行 12、21，加上 unfold 的 `Prefill 批次`、`前缀缓存探测`、`认领命中块`（`allocate` 卡里，在 col 2）。包围盒横跨 col 1 到 col 2，宽约 1670【设】→ `sc ≈ 1400/(1670+140) ≈ 0.77` < .85 → **走"怼住阅读起点"分支**：以第一个读点（`schedule` 行 12）为中心、缩放 .85。`allocate` 只露出一部分，要读者自己拖。这就是"缩不下就怼阅读起点"在这个画布上的真实样子。

### 2.7 手机上是同一套排法吗？

**不是。同一份数据、同一批 DOM 节点，换一种"投影"。**

- 触发：`innerWidth < 700` 且没按"2D 画布"逃生钮（`code-canvas/template/canvas.html:2174`）；窗口 resize 越过 700 会重新判定（`code-canvas/template/canvas.html:2884-2886`）。
- 样式：`body.mstream` 下 `#vp` 隐藏，卡片 `position: static !important; width: auto !important`（`code-canvas/template/canvas.html:606`、`code-canvas/template/canvas.html:612-613`），所以**桌面端量出来的固定宽度在手机上被覆盖**。
- 内容：`buildStream()`（`code-canvas/template/canvas.html:2103-`）把当前步 `order || focus` 的卡**按阅读序竖排**，卡的 DOM 节点被 `appendChild` 进 `#mstream`（`msMoveCard`，`code-canvas/template/canvas.html:2092-2101`），切回 2D 时 `restoreFromStream` 把它们搬回 `#world`（`code-canvas/template/canvas.html:2079-2084`）；点亮的线变成可点的"关系条"（`code-canvas/template/canvas.html:2152-2165`）；总览步变成按故事线分节的目录。
- **没有线、没有 x/y**：`drawWires()` 一进来就返回：「卡片流没有空间布线；精读时卡在阅读容器里，布线等它归位」（`code-canvas/template/canvas.html:1289`）【注释】。
- **初次加载顺序**：`layout()` → `setStep(…)` → `syncStreamMode()`（`code-canvas/template/canvas.html:2896`、`2905-2906`）。也就是手机上第一次排版和量宽**还是按桌面 2D 做**，然后才切到流。【推断】：先量再切，才能量到不被 `width: auto !important` 干扰的自然宽度；代码没写这个理由。
- 若手机上强行切回 2D：`focusOn` 的缩放下限降到 .12，`READ_FLOOR` 降到 .5（`code-canvas/template/canvas.html:1403`、`1411`），注释「手机上宁可小也不裁切 focus 内容」（`code-canvas/template/canvas.html:1403`）【注释】。

**为什么**：提交 51b075e：「作者裁决：0.12 缩放的 2D 全景在手机上不可读，resize 不是答案。」【提交】；ROADMAP 同样记「作者裁决"大图手机看不了，resize 救不了"」（`ROADMAP.md:36`）【文档】。也就是之前先试过"缩小 + HUD 收纳"（`ROADMAP.md:32`：「focus 缩放下限 0.12」），不行，才换成流。测试：`mobile.mjs` 断言 <700px 自动进流、`#vp` 隐藏（`code-canvas/tests/mobile.mjs:19-21`）【测试】。

### 2.8 页面怎么知道 hub 在不在线？问答怎么接上的？

- **探测**：页面加载时 `fetch('__alive').then(r => r.json()).then(() => { QA.live = true; }).catch(() => {});`（`code-canvas/template/canvas.html:1801`）。**只要请求成功并且是合法 JSON 就算在线，内容不看**；失败（比如把 html 下载下来离线打开、`file://`）就保持 `QA.live = false`。
- **URL 是相对的**：画布页的地址是 `/c/nano-vllm/`，所以 `__alive` 解析成 `/c/nano-vllm/__alive`，hub 在 `serve.py:1055-1056` 回 `{"ok": true, "html": …, "cli": …}`。这也是为什么 hub 对 `/c/<name>`（无斜杠）要 301 补斜杠：「/c/<name> → 补斜杠，保证页面内相对路径成立」（`code-canvas/serve.py:1037`）【注释】。
- **发问**：`sendQA` 在 `QA.live` 为真时 `fetch('ask', {method:'POST', body: {prompt, card, block, question}})`（`code-canvas/template/canvas.html:2050-2053`），落到 hub 的 `POST /c/<name>/ask` → `_ask`（`code-canvas/serve.py:1080-1081`、`code-canvas/serve.py:1219`），它调 `run_cli` 并把问答记入 sidecar（`code-canvas/serve.py:1226`、`1255`）。这条链不属于本框，只给入口。
- **降级**：`QA.live` 为假时，把完整上下文复制到剪贴板，提示"粘贴给你的 agent"；剪贴板也失败就提示"启动 serve.py 使用在线问答"（`code-canvas/template/canvas.html:2034-2041`）。同样，`patchProtocol()` 在非在线时返回空串：「剪贴板模式的外部 agent 改不了图，不给协议」（`code-canvas/template/canvas.html:1924`）【注释】。
- 历史记录存在读者自己的 `localStorage`（`code-canvas/template/canvas.html:1799-1800`），键是 `canvas-qa:<标题>`。

---

## 3. 例子串起来：`/c/nano-vllm/` 被打开后

沿用上一趟：画布 `nano-vllm`，10 张卡、3 条故事线、6 步。视口 1400×900【设】。

1. 服务返回 html（上一趟 §2.7）；浏览器执行脚本，建好 10 张卡 + 5 条 note + 3 个 region 的 DOM。
2. `layout()`：量宽（设 `w0=800, w1=800, w2=640`）→ `colX = {0:370, 1:1720, 2:2750, 3:3620}`【设】；`step.y=160`、`run.y=720`、`sampler.y=1000`、`schedule.y=160`、`can_allocate.y=160`、`kv-pool`（band 1）基准 `160+280=440`。
3. `setStep(0)`：总览，`fitAll`。包围盒从最左的 note 车道（x=40）到 col 3 右边，设约 3900 宽、1200 高【设】→ `s = min(1400/3960, 790/1260)×.98 ≈ 0.35`，宏观图。
4. 读者按 ▶ → 第 ① 步：见 §2.5 的例子（只亮 4 根线、`run/postprocess` 仍折着、镜头 1.15 怼在 `step` 的第 2–5 行）。
5. ▶ → 第 ② 步：`wires=[w-sched]`，暗掉除 `step/schedule` 之外的卡；没有 `lines`/`unfold`，但有 `n-chunk` 这张 note 绑在 `schedule` 第 18 行，第 18 行在折着的 `Prefill 批次` 块里 → `visibleProxy` 退到该块的标题条；读点 = 这个标题条 + note。
6. ▶ → 第 ③ 步：展开 `can_allocate`、`allocate`；摊开三个块；`preempt`/`postprocess` 被 `schedule` 撑开后往下推（§2.3 例子）；镜头走"怼阅读起点"分支（§2.6 例子）。
7. 读者点 `schedule` 的卡头 → `.collapsed` 切换 → `drawWires()` → 下面的卡立刻上移、线重画。没有任何"重新排版"的东西发生过，只是同一个 `relayoutColumns()` 又跑了一遍。

---

## 4. 值得做成源码页的代码段（6 段）

> 每段给文件、起止行、做什么、逐处批注。"为什么"带来源等级。

### 源码页 A：`layout()` —— 量宽 → 定列 x → 定基准 y
`code-canvas/template/canvas.html:1031-1081`（51 行）

做什么：一次性的量宽 + 摆列。
批注：
- `1035`「measure natural card widths (expanded, unfolded)」：先展开再量（`code-canvas/template/canvas.html:1035`）。**顺序依赖**：先量再折，两个循环不能合并——块一折（`.blk.folded > .bcode { display: none; }`，`code-canvas/template/canvas.html:150`），里面的长行就不参与 max-content，量出来会偏窄。【推断】
- `1041` `Math.min(el.offsetWidth + 2, 800)`：魔数 800（【文档】`code-canvas/DESIGN.md:147`）；`+ 2` 原因没查到。
- `1046` 把宽度**固定**成像素值：后面展开不再变宽。
- `1054-1055` `hasLeftNotes`：任何角落 note 都算（`n.place.corner`），所以绝大多数画布 col 0 不在 40。
- `1066` `x += L.noteW + 70`：给"左侧 note 的目标列"在前面留车道（320 = 250+70）。
- `1068` 列宽取本列**最宽**的卡。
- `1075` `160 + band × 280`；此时只是**基准**。
- `1070` 注释「x + base y from coarse hints; dynamic push-down happens on every draw」（`code-canvas/template/canvas.html:1070`）【注释】：直接告诉读者 y 以后还会变。
- `1077-1080`：定完立刻调 `relayoutColumns / placeNotes / fitRegions / sizeWorld`，这四个也是 `drawWires` 每次开头跑的那四个。

### 源码页 B：`relayoutColumns()` + `drawWires()` 的开头
`code-canvas/template/canvas.html:1136-1151` 和 `code-canvas/template/canvas.html:1287-1293`（共约 22 行）

做什么：每次重画先"同列推开"，再画线。
批注：
- `1141` 按 `_baseY` 排序，不按当前 `_y`：所以展开又折回，卡会**回到原位**（不会"推了就回不去"）。【推断】
- `1145` `Math.max(c._baseY, prevBottom + 60)`：只会向下推，不会向上拉。
- `1148` `prevBottom` 用的是 `offsetHeight`——**必须在 DOM 已经展开/折叠之后读**，所以 `setStep` 里一定先改 class、最后 `drawWires`。
- `1289-1291`：手机流和精读模式直接 return（注释【注释】）。
- `1290` 四连：`relayoutColumns(); placeNotes(); fitRegions(); sizeWorld();`——**线是最后才画的**，因为线的端点依赖卡已经到位。【推断】

### 源码页 C：`wpos / visibleProxy / resolveAnchor`
`code-canvas/template/canvas.html:1186-1234`（49 行）

做什么：把"某卡某行"变成世界坐标里的一个点。
批注：
- `1188` 沿 `offsetParent` 累加：世界坐标，与相机无关。
- `1189-1195` 减去滚动容器位移：「滚动容器（一体滚动窗/横向代码区）的位移要减掉，线锚才钉得住」（`code-canvas/template/canvas.html:1189`）【注释】；来源 398eb5c【提交】。
- `1200-1206` 三级降级：折着的卡 → 可见的行 → 最外层折着的块条 → 整卡。**"最外层"**：`chain.unshift` 让外层在前（`1204`）。
- `1216` 默认 side：起点右、终点左；同列连线靠数据里显式写 `side:'left'`。
- `1223-1225` 夹在窗口边缘 ±6px。
- `1230-1231` 魔数：62%、−16、+7、−5、+16。理由代码没写。

### 源码页 D：`setStep(i)` 的前半
`code-canvas/template/canvas.html:1689-1726`（38 行）

做什么：改 DOM 状态，再定镜头，最后重画。
批注：
- `1695-1704`：只 `remove`、不 `add`——展开是单向的。
- `1701-1702`：块靠**名字**找，同名块只取第一个；找不到静默忽略（`if (blk)`）。
- `1711-1715`：暗 = 不在这步的线的端点里。`s.wires` 缺失时一张不暗（`s.wires ? !activeCards.has(c.id.slice(5)) : false`）。
- `1719`：`step > 0 && !mine`：总览步里所有 note 都不暗。
- `1723-1725`：三条镜头分支（流 / 带读 / fit 或 focus），注释说带读步的镜头「放在 HUD 更新之后」（`code-canvas/template/canvas.html:1724`）【注释】。
- `1726`：`drawWires()` 在镜头之后，见 §2.5 的顺序提示。

### 源码页 E：`readRect` + `focusRead`
`code-canvas/template/canvas.html:1408-1458`（51 行）

做什么：读点取景。
批注：
- `1411` `READ_FLOOR`：函数而不是常量，因为要在每次调用时看当前 `innerWidth`（resize 后会变）。【推断】
- `readRect` 里的 `visibleProxy` 过滤：已折叠的卡的行不算读点（`!c.classList.contains('collapsed')`，`1416`）。
- `1455` 比例公式：`+140`、`−170`、`1.15` 三个魔数（上下留白给 HUD 面板，【推断】）。
- `1456-1457`：**两条分支**——装得下就居中；装不下就以第一个读点为中心、用 `READ_FLOOR`。
- `1446-1454` 无读点分支（老数据兜底）。

### 源码页 F：手机分支 + 初始化 + 问答探测
`code-canvas/template/canvas.html:2173-2188`（`syncStreamMode`）、`code-canvas/template/canvas.html:2884-2906`（resize 与 init）、`code-canvas/template/canvas.html:1798-1801`（`QA`）

批注：
- `2174` 700px 与 `forceCanvas`：逃生钮语义。
- `2179` `if (want === streamOn())`：已在目标模式就只重建，避免 resize 每个像素都搬一次 DOM。
- `2183` 切回 2D：`restoreFromStream(); setStep(step, false); drawWires();`——卡片搬回后要重算。
- `2885-2887` resize 只调 `syncStreamMode / drawWires / fitAll`，**不会重新 `layout()`，不重新量宽**。
- `2895-2906` 顺序：`layout()` → `setStep` → `syncStreamMode`。
- `1801` 一行探测：不看内容，`.catch(() => {})`。

---

## 5. 宏观"为什么"汇总

1. **为什么排版放浏览器而不是 agent / 服务端**：agent 只给 `{col, band}`，「agent 给 `{col, band}` 粗位置，渲染器测行宽定列宽、同列冲突下推、region 实时包住成员、镜头由 focus bbox 自动计算」（`code-canvas/DESIGN.md:143-144`）【文档】；HANDOFF 把它升成原则「布局哲学：agent 只给粗位置 {col, band}，渲染器精排」「**不要给像素坐标**」（`HANDOFF.md:164-166`）【文档】。像素要用字体量字宽、要看展开状态，这些只有浏览器知道。【推断】：这是我对"为什么不让 agent 给像素"的补充理由；文档只说了"不要给"。
2. **为什么每次重画都推列**：卡会展开折叠（§2.3，【注释】【文档】【测试】）。
3. **为什么镜头对准读点而不是整卡**：作者反馈（§2.6，【注释】【提交】【文档】）。
4. **为什么手机换成流**：作者裁决（§2.7，【提交】【文档】）。
5. **为什么线锚要降级到块条/卡头**：行会被折起，也会被滚出窗口；线不能指向"不存在"的位置。（注释写了做法；"为什么"是我的推断。【推断】）
6. **为什么没有服务端渲染好的像素**：同一份 JSON 在不同屏幕上排出不同像素，是被允许的；上一趟 §5 的"agent 画了画布"误会就在这里。

---

## 6. 读者最容易看错的地方（本框）

1. “col 0 → x = 40”：只在没有 note 车道的画布成立；nano-vllm 里是 370（§2.2）。
2. “band → y”：只是**基准**；展开一张卡会推下面一整列（§2.3）。
3. “`focus` 里的卡会亮”：不对。亮暗看这步的线的端点（§2.5）。
4. “▶ 回 ◀ 会还原”：展开不会还原（§2.5）。
5. “800px 以内都不会截断”：超过的行被裁掉，桌面端没有横向滚动（§2.1）。
6. “resize 会重排”：只会重画线（和在 700px 处切换投影），不会重新量宽（§2.7、源码页 F）。
7. “`L.vGap = 90` 是卡间距”：它没被用；真正的间距是写死的 60（§2.3）。
8. “线锚的行号 = 源文件行号”：是**卡内**行号（`<卡id>-L<n>` 从 1 数起，`code-canvas/template/canvas.html:946`）；卡的 `file` 字段（如 `engine/llm_engine.py:49`）才是源文件起始行。

---

## 7. 没查到为什么

- **`el.offsetWidth + 2` 的 `+ 2`**：只找到 border-box 下它是多余的 2px，没有注释、没有提交说明（见下一条的原因）。
- **布局核心（`layout`、`relayoutColumns`、`wpos`、`drawWires`、`bow` 等）的提交史**：这些代码在仓库最早的提交 `51148ef` 里就已经存在，而该提交的说明是「事故修复：服务重启不再杀生成任务」，与排版无关——说明历史在入库前被压平了，"为什么这么写"的提交证据**拿不到**。我能引的只有注释、DESIGN/HANDOFF 文档和后来的增量提交。
- **`setStep` 展开只增不减**是有意还是副作用：没有注释、没有测试断言回退行为。
- **暗不暗只看线、不看 `focus`**的理由：没找到。
- **`L.vGap: 90` 为什么留着不用**：没找到。
- **`resolveAnchor` 里 62%、−16、+7、−5 这些偏移的理由**：没找到。
- **镜头在 `drawWires()` 之前算**是不是有意：没找到。
- **读点取景里 `+140 / −170` 的含义**：没找到注释。`−70`（`centerOn` 中的 `innerHeight − 70`）同样。
- 没有在真实浏览器里跑过页面，所以所有"设"的像素数字（宽度、高度）都只是演示机制。

---

## 8. 可改进处【观点】

- 【观点】把"展开状态"变成**每步显式的完整描述**（或让 ◀ 能还原），当前的只增不减会让"同一步"在不同浏览路径下看到不同东西。
- 【观点】`L.vGap` 删掉或真的拿来用，免得下一个读者像我一样白找。
- 【观点】镜头应当在 `relayoutColumns()` 之后算，避免"被推下去"的卡导致构图偏差。
- 【观点】800px 截断目前是静默的；至少在卡上给个"本行被截断"的标记，而不是只在 validate 里报 warn。
