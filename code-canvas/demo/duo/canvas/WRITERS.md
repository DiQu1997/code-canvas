# 内容作者指南（只需读这份 + cv.py；可运行的全套示例见 _off/content_demo.py——它把池子的空格画在了左边，与源码相反，别照抄）

## 0 规矩
- 你只写自己的 `content_<名字>.py`。**不许改** `cv.py / build.py / template.html`，也不改别人的模块。
- 发现框架 bug 或缺功能：写到本目录 `framework-issues-<名字>.md`（现象 + 最小复现），自己先绕开。
- 内容文件里**不写代码正文**：只写「文件:行号」，代码由 build.py 从仓库 HEAD 取；引文 `q` 必须是原文照抄（不翻译）。
- 每个「为什么」都必须是一条带等级的 `note` / `at`；`p()` 只讲「是什么」。没证据就标「推断」，不要硬找依据。

## 1 模块约定
```python
from cv import *          # 或按名导入
def nodes():              # 返回 {槽位id: 节点}；键必须等于该节点自己的 id
    return {'pool': node('pool', '候选池', '候选池：16 个格子，跨调用保留', '一句话摘要', blocks=[...], children=[...])}
```
- 节点 id 只能用英文字母/数字/下划线，用点分层：`pool`、`pool.insert`、`pool.insert.shift`。子孙必须以 `槽位id.` 开头。
- 一个模块可以填多个槽位。总架构是 `content_a.py`（导出 `META` 和 `root()`，树里用 `slot('pool')` 占位）。
- 槽位表（id → 默认标题）：`gate` 安全闸 · `acct` 算账 · `resize` 旁路：哈希表扩容 · `policy` 策略位标志 · `clock` 对象头 24 位 ·
  `pool` 候选池 · `loop` 主循环怎么用池 · `random` 随机策略 · `bless` BLESS · `delete` 删 key 并通知 · `tick16` 每删 16 个停一下 ·
  `budget` 时间预算 · `exit` 出口 · `fail` EVICT_FAIL 之后 · `why` 宏观为什么 · `improve` 可改进处 · `unknown` 没查到为什么
- 没人填的槽位自动变成「未展开」占位页。**指向任何槽位 id 的内链永远合法**；指向别人子页（如 `[[pool.insert]]`）在对方没写时会
  变成红色断链并 WARN——跨模块只链槽位 id，除非约好。

## 2 node(id, nav, title, short, lead=None, src=None, blocks=None, children=None)
| 字段 | 出现在哪 | 建议 |
|---|---|---|
| `nav` | 面包屑、侧边地图、上层的下钻卡片标题、`[[id]]` 的默认文字 | ≤8 个汉字（超 12 字宽 WARN）；名词短语 |
| `title` | 本页大标题 | ≤24 字，可以是一句话 |
| `short` | 上层页「从这里往下」卡片里的摘要：告诉读者为什么值得点进来 | ≤60 字，可用迷你语法 |
| `lead` | 本页标题下的导语（不给就用 short） | 1–2 句：这页回答什么问题 |
| `src` | 导语下一行灰字「代码位置：…」 | 纯文本，如 `src/evict.c performEvictions()` |
| `blocks` | 正文，自上而下 | 一页一个重点；一屏半以内，多了就拆子节点 |
| `children` | 「从这里往下」卡片，按顺序；也是「下一页」的阅读顺序（深度优先） | 2–5 个 |
层级条、上一层/同层按钮、反向链接「别处提到这里」、翻页、地图、图例都是自动生成的，不用写。
分层原则：越上层细节越少（图、流程、一句话结论）；叶子页才放几行到几十行真实代码。

## 3 文本迷你语法
适用：`p`、`note/at` 的 text 与 more、`short/lead`、表格单元格、flow 的 title/sub/ref、frames 的 say、所有 `cap`。
（`h()`、`nav/title/src`、图里的 label、格子内容、bar 的 label 是纯文本。）
| 写法 | 效果 |
|---|---|
| `` `src/evict.c:546` `` `` `src/evict.c:538-546` `` | 锚点小签，点开就地展开原文（≤30 行才能展开）；build 核对行号存在。认 c h conf tcl md json py sh txt lua yml yaml |
| `` `c:b9800e06b` `` | 提交小签（7–40 位哈希），点开显示日期和标题；build 核对提交存在 |
| `` `maxmemory` `` | 其余反引号内容 = 行内代码 |
| `[[pool]]` / `[[pool.insert|看插入过程]]` | 内链；不写文字就显示目标页的 nav |
| `**粗体**` | 粗体（截图环境的中文字体没有粗体字形，别只靠粗体表达重点） |

## 4 note 与 at
`note(lv, text, q=None, ref=None, also=None, tag=None, more=None, who=None)` —— 独立的解释卡片。
`at(line, lv, text, q=…, ref=…, also=…, tag=…, more=…, who=…, has=None)` —— 贴在代码某行下面的批注，只能放进 `code(..., notes=[...])`。
- `lv` 证据等级（以 content_a.py 的 META.levels 为准）：`'注释' '提交' '文档' '测试'` = 实线彩色左边、白底（有证据）；
  `'推断'` = 琥珀色虚线框 + 米黄底 + ≈；`'观点'` = 洋红虚线框 + 粉底 + ※；`None` = 灰边无徽标，只用于「是什么」的说明。
- `text` 结论本身，一两句，≤160 字（超了 WARN）；长的细节放 `more`（折叠，读者点「展开」）。
- `q` 原文引文（英文照抄）。build 会在 `ref` 指的行 / 提交说明里核对它确实出现（忽略空白和注释符号 `* // #`；中间可用 `…` 省略），对不上 WARN。
- `ref` 依据在哪：`'src/evict.c:528-529'` 或 `'c:哈希'`，也可以是列表。`at` 里给了 q 没给 ref 时，默认就是批注所在的行。
- `also=[(lv, q, ref), ...]` 同一结论的其它等级旁证（如注释说了、测试也固定了）。
- `tag` 一个标签（META.tags）：`'反直觉'`(多数人会猜错) `'易混'`(长得像其实不同) `'隐含前提'`(没明说但少了不成立) `'历史'`(演变留下的) `'后果'`(使用者会踩到)。
  **只管打标签**：`collect('tag', [...])` 会把全画布带这些标签的批注自动收成「易困惑点」索引，每条带「去看」链接。
- `who='me'` 标明「这条是呈现者自己加的、底稿里没有」，页面显示小标签。底稿里有的不用写。
- `at` 的 `line`：行号或 `(a, b)`，必须落在该 code 块展示的范围内；`has='子串'`：这几行里必须出现的字样，防行号漂移（不中 WARN，并打印实际内容）。
- 标了注释/提交/文档/测试却既没 q 也没 ref 的独立 note 会 WARN。

## 5 各种块
| 块 | 页面长相 / 何时用 | 极短示例 |
|---|---|---|
| `p(text)` | 普通段落。衔接、讲「是什么」 | `p('先看 [[gate]]。')` |
| `h(text)` | 页内小标题 | `h('两种情况')` |
| `note(...)` | 带等级徽标的卡片，见 §4 | `note('注释', '只求改善，不求解决', q='not to immediately resolve it', ref='src/evict.c:528-529')` |
| `code(file, parts, notes=None, cap=None)` | 带行号的真实代码；有批注的行标黄，批注卡片紧跟在该行下面；右上「只看代码」可收起批注。`parts` 单段用元组 `(a, b)`，多段用列表 `[(a,b),(c,d)]`，段间自动显示「省略第 x–y 行」。一块 ≤40 行为宜（>120 行 WARN）；每块 1–4 条 at | `code('src/evict.c', [(543,546),(552,559)], notes=[at(546, '注释', '假的 OK', q='fake EVICT_OK', has='isSafeToPerformEvictions')])` |
| `table(head, rows, cap=None)` | 对照表；单元格可用迷你语法。**手机上每行变成一张卡片**（表头变成每格上方小字），所以 ≤4 列、第一列放「名字」 | `table(['返回值','含义'], [['`EVICT_OK`','没问题']])` |
| `flow(steps, cap=None)` | 竖排编号步骤，适合顶层「先后顺序」。`step(k, title, sub=None, to=None, out=None, loop=False, ref=None)`：`k` 圆圈里的序号；`sub` 小字；`to='节点id'` 整块可点下钻；`out=[out('ok'/'run'/'fail', 文字)]` 这一步的出口药丸（绿/黄/红）；`loop=True` 显示「这一步会重复」；`ref` 放锚点小签 | `flow([step('1','先过安全闸', to='gate', out=[out('ok','假的 OK')], ref='`src/evict.c:546`')])` |
| `diagram(...)` | 框线图，见 §6。适合「谁调谁、数据放哪」 | 见 §6 |
| `frames(steps, cap=None, left=None, right=None)` | 一排格子的逐帧演示（上一步/下一步），适合「池子怎么变」。`frame(title, cells, say=None, inc=None, incsay=None)`：`cells` 一排 `cell(k, v, s)`（`cell()` = 空格）；`s` 状态：`None` 普通 / `'new'` 新进 / `'pick'` 被选中 / `'mv'` 挪了位置 / `'out'` 被挤出 / `'ghost'` 已不存在 / `'empty'` 空；`inc`+`incsay` 在格子上方画一个「新来的」；`say` 这一步的解说（迷你语法）；`left/right` 两端的方向说明。各步格子数要相同 | `frames([frame('空池', [cell() for _ in range(6)], say='全空'), frame('进来一个', [cell()]*5+[cell('k9','95s','new')])], left='空闲短', right='空闲长')` |
| `bar(segs, cap=None)` | 一条按比例分段的横条 + 编号图例，适合「哪些算、哪些不算」。`seg(w, label, kind='in'/'out', tag=None)`：`w` 相对宽度；`in` 实心、`out` 斜纹灰；`tag` 图例里的小药丸 | `bar([seg(5,'数据','in','计入'), seg(1,'AOF 缓冲','out','不计入')])` |
| `collect(by, keys)` | 自动汇总全画布（不含本页）的批注：`by='tag'` 按标签、`by='lv'` 按等级；每条带「去看」跳到原处并闪一下 | `collect('lv', ['推断','观点'])` |
| `commits()` | 全画布引用过的提交一览（哈希/日期/标题），自动生成 | `commits()` |
| `slot(id)` | 只有总架构用：在 children 里给别的模块留位 | `slot('pool')` |

## 6 diagram：两套坐标
```python
diagram(860, 250,                      # 桌面画布 宽×高（px）。桌面正文栏约 870 宽，w ≤ 860 就不缩放
  boxes=[box('cmd', 'processCommand', [20, 95, 150, 60], m=[95, 14, 150, 50], kind='ext', sub='每条命令'),
         box('pe', 'performEvictions', [230, 95, 170, 60], m=[85, 110, 170, 54], to='loop', flag='543 行')],
  edges=[edge('cmd', 'pe', '命令执行前', ml='执行前')],
  frames=[group('一次调用之内', [215, 14, 430, 224], m=[4, 96, 332, 182])],
  mw=340, mh=180, cap='带 › 的框可以点进去')   # 手机画布 宽×高
```
- 坐标一律 `[x, y, 宽, 高]`，原点在画布左上角。`d`（第三个参数）= 桌面位置，`m` = 手机位置。
- **手机布局**：给了 `mw/mh`，且可用宽度 < `w×0.86` 时启用（手机、窄窗口）。390 宽的手机上可用宽约 350，所以 `mw` 取 340 左右，
  纵向排（一行最多两个框）。启用后：没给 `m` 的框**不显示**（连着它的线也不画，build 会 WARN 提醒）；`msub` 替换小字；
  `edge(..., ml=)` 换短标签，`m=False` 这条线手机上不画。
- 不给 `mw/mh` 时手机上是整张桌面图等比缩小：`w` > 420 就看不清了，所以**宽图必须给手机布局**。
- `box`：`label` 粗体主名，`sub` 小字；`kind`：`'act'` 动作(蓝) / `'store'` 数据结构(黄，粗顶边) / `'ext'` 外部(灰虚线) / `'bg'` 后台(紫)；
  `to='节点id'` 可点下钻（右下角出现 ›）；`flag` 贴在右上角的小徽标（会压到从右上角出去的线标签；框的 y < 12 时会被裁掉）。
- 框的大小：一个汉字约 14px、英文字母约 8px；带 sub 的框高 54–60，宽 = 字宽 + 30。
- `edge(f, t, label=None, ml=None, cls=None, d=None, m=None, lp=None, mlp=None)`：默认从 f 框中心到 t 框中心画直线（裁到框边）带箭头；
  `cls='dash'` 虚线（不一定发生）；`d` / `m` = 自己给折线点 `[[x,y],[x,y],...]`（桌面 / 手机）；`lp` / `mlp` = 标签位置 `[x,y]`。
  标签默认压在线的中点：**两框间距要 ≥ 标签宽 + 40**，否则标签盖住箭头（content_demo 里「命令执行前」就挤了，别学）。
- `group(label, d, m=None)` 放进 `frames=[...]`：画一个带标题的虚线分组框。
- 框或分组超出画布会被裁掉，build 会 WARN。

## 7 构建、截图、读输出
```bash
C=<本目录绝对路径>; R=<redis 仓库绝对路径>            # 不要 cd 进仓库目录；Python 一律 -I
python3 -I $C/build.py $R $C/out_<名字>               # 只写 out_<名字>/canvas.html 和 anchors.json，互不覆盖
node $C/shot.js $C/out_<名字>/canvas.html $C/out_<名字>/shots pool pool.insert --sheet --nav
```
- build 读本目录**全部** `content_*.py`。别人的模块坏了不影响你：它的槽位变成占位页，输出里有 `ERROR [模块名] …`，不用管。
  你自己的槽位出错（坏锚点、id 前缀不对、数据结构不对…）也只是该槽位变占位，输出 `ERROR [你的模块] [槽位] 节点 X 第 N 个块(类型): 原因`。
- 输出依次是：节点数（含占位数）/ 最大深度 / 代码块数 / 批注数；已填槽位（`槽位←模块`）；未填槽位；ERROR；全部 WARN。
- WARN 格式 `WARN [模块] [节点id] 说明`，只看自己的：`… | grep content_<名字>`。**交付前自己的 WARN 和 ERROR 要清零**
  （确有理由保留的写进交付说明）。常见：引文对不上（q 没照抄，或 ref 行号偏了）、has 不中（行号漂了）、批注不在所示范围、
  断链、等级/标签拼错、解释偏长、nav 偏长、框超出画布、框没给 m。
- `--strict`：有任何未填槽位 / ERROR / WARN 时退出码 1（最终装配用，平时不用加）。
- shot.js：每个节点 id 各出 `<id>.desktop.png`（1440×900 视口，整页）和 `<id>.mobile.png`（390×844，整页），并打印每张的
  **横向溢出**和**控制台错误**；节点 id 不存在会报出来。`--sheet` 把很长的手机图切成几栏并排存成 `<id>.mobile.sheet.png`（看这张省事）；
  `--nav` 再跑一遍交互自测（地图、面包屑、上一层、锚点展开、逐帧、下钻…），逐项 PASS/FAIL。只截自己的节点即可。
  若报找不到 playwright：命令前加 `NODE_PATH=/opt/node-tools/node_modules`。不要安装任何东西。
- 看图自查：手机图里有没有挤爆/看不清；图的手机布局有没有丢框；推断和观点是不是虚线框；每页是否一个重点。

## 8 已知限制（框架负责人没修的）
- frames 一排超过 8 格在手机上没验证过（候选池 16 格请自己截图确认；不行就分两排讲或写 framework-issues）。
- frames 的截图只能拍到第 1 步；后面几步靠 `--nav` 的翻页自测，样式要看得自己另想办法或在说明里交代。
- 手机上「图例」展开后几乎盖满正文，再点一次「图例」收起。
- 锚点超过 30 行点不开（只是个标签）；要展示长代码请用 code 块。
