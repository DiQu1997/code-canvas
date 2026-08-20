---
name: code-canvas
description: Turns code reading, change planning, or diff review into an interactive 2D canvas instead of a linear document. Produces a self-contained HTML canvas of function-level code cards laid out spatially, with typed wires (call vs data flow) anchored to specific lines, line-attached intent notes, teal background notes, colored storyline regions, and a step-by-step guided camera. Long functions render as a fold-out outline of named logical blocks. Use when the user wants to understand a codebase, walk through code, or review changes visually — triggers include "walk me through", "explain this code", "how does X work", "make a canvas/walkthrough of", or dissatisfaction with reading long diffs top-to-bottom.
---

# code-canvas skill (v0.1)

代码是源码，图是投影。本 skill 读一段代码（或一个变更），产出一张
**可平移缩放的 2D 画布**：函数级代码卡片 + 策展过的连线 + 行级注释 +
故事线镜头。设计决定见 `DESIGN.md`，数据格式见 `schema.md`。

## 管线

0. **确认读者画像**：问（或从对话中提取）这张图为谁生成——经验水平、
   关注点、语言、讲法偏好。写进 `meta.audience`，所有文字字段按它来写。
   用户没说就用默认："有经验的工程师；中文；准确优先"。
   注意：这只是默认视角——读者还能在页面上设置自己的画像，对说明和
   变元注释做运行时的「按我的画像重讲」（见 schema.md）
1. **确定范围**（同旧 skill 的 Mode A/B：给了文件直接读；给了主题先提
   scope 提案，用户确认后继续）
2. **结构层先行（Python 仓库必须走这条路）**：

   ```bash
   python3 extract.py <范围目录> --repo-root <仓库根> --out structure.json
   # diff 画布加 --diff [BASE]；更新已有画布加 --merge 旧.json
   ```

   毫秒级机械产出：函数/方法卡（原文逐字）、call 线（调用点行锚）、
   diff 行映射、粗布局。加 `--embed-context` 把引用文件全文嵌入 files
   映射——渲染后每张卡可就地展开上下文（读者不离开画布看前后文）。**结构层的事实字段（code/file/行号/diff）一律
   不许手改**——你的全部工作是叙事层：
   - **策展**：删掉与故事无关的卡和线（提取是全量的，画布不是）
   - 写 regions / notes / blocks / terms / steps，改 meta 与卡片 name
   - `--merge` 增量更新时，stderr 的「待重讲」列表就是你的工作清单
   非 Python 语言暂无后端：退回手工摘选，行号锚点仍用脚本算
3. **读全文**，识别故事线（region）；**策展连线**：结构层给了全量
   call 线，删到只剩故事在场的；data 线手工补——只画故事需要的
   "值的旅程"（值产生行 → 消费卡片，带值名 label）
4. **分层细节**：重点卡 `collapsed:false` + 行级 note；次要卡折叠。
   长函数（约 15 行以上或嵌套深）写 `blocks` 行段树——按"一句话说得清
   功能"分段，不按 AST；嵌套结构套 children。值得多讲两句的块加
   `explain`（≤120 字，回答"为什么这么写/坑在哪"，不复述代码）。
   不好懂的标识符加 `terms` 变元注释（≤60 字，回答"这个变量装的是什么"）
5. **背景三层**：画布级 bg note（corner nw，step 0 点亮）、region blurb、
   概念级 bg note（锚到行，在相关 step 点亮）。字数硬上限见 schema.md
5b. **数据结构四件套**（算法讲"怎么动"，结构讲"动的是什么"，缺一半图就瘸）：
   - 形状：class/struct 定义做原文卡 + terms 逐字段注解
   - 关系：`kind:"struct"` 线（含/引用/索引，label 必带，照旧策展不画全量 ER）
   - 不变量：intent note 锚到字段行（note 是断言）
   - **实例快照卡**（`kind:"state"`，见 schema.md）：核心机制的结构配一张
     ——具象例子（真实感的值）随步进演化，渲染器自动高亮"这步改了哪个格子"。
     状态要和 step 叙事逐步咬合：代码行高亮（谁干的）/ 格子变色（干了什么）/
     caption（为什么）同屏成三角。值是你编的**好例子**：小到一眼看全
     （数组 ≤6 格），又足以展示机制（前缀命中就要有能命中的 hash）
6. **写 steps**：第 0 步总览（fit），后续每步 = 点亮的线 + 高亮的行 +
   focus 取景元素 + ≤80 字 caption。步子按"读者的问题"排，不按文件序。
   **focus 的书写顺序 = 该步的阅读顺序**（渲染器据此钉 ①②③ 序号标）——
   先读的元素写前面；取景与阅读顺序冲突时用 `order` 显式覆盖。
   信息量大的步配 `detail`（≤300 字，「详解」点开才显示）：讲这步代码
   在干什么、为什么这么设计、容易误解什么——**不复述 caption、不贴代码、
   不抢行级 note 的活**（贴着具体行的断言仍写 note）。caption 永远要能
   独立成立，detail 只是自愿加深
7. **产出 JSON，先过验证器再渲染**（不可跳过）：

   ```bash
   python3 validate.py canvas.json   # ERROR 必须清零；warn 逐条自查
   python3 render.py canvas.json output.html
   ```

   验证器抓机械错误（悬空引用、行号越界、块区间重叠、token 不在行上）
   和预算超限（每步线数/行数、各类文字上限）。ERROR 不清零的图是坏的。

8. **截图自检**（有 headless chromium 时，逐项过下面的清单）：
   总览 + 每个 step 各截一张；`#s2` 直达步骤，调试尾缀 `x` 全展开、
   `e` 开说明、`t` 开变元注释、`q` 开问答抽屉
9. **（可选）开启块级问答**：`python3 serve.py output.html --repo <仓库路径>`，
   从 localhost 打开——每个块的「问」变成真问答（桥接 `claude -p`）。
   静态打开时「问」降级为复制上下文提问到剪贴板

## Diff 画布（第三种画布类型）

输入是一个 diff / PR 时（`meta.mode: "diff"`，参考 `demo/cache-diff.json`）：

- 卡片 = 被改动的函数的 **head 全文** + `diff` 标记（added 行绿、removed
  原文红删除线）；未改动但受影响的邻居做上下文卡（折叠）
- 故事线 = **变更意图**（"本次变更" + "未改动的邻居"是最小形态），不按文件分
- step 顺序 = 评审者的问题序列：**这个 PR 治什么病 → 核心改动（逐个）→
  连锁影响 → 回归风险**。风险步是必选项——没有风险判断的 diff 画布只是
  上色的 diff
- removed 只放读懂变更所必需的原文，不搬运整个旧版本

## 计划画布（第四种画布类型：改码之前）

agent 打算修改代码时，**动手前**先出计划画布（`meta.mode: "plan"`，参考
`demo/cache-plan.json`），作者在画布上审批计划，而不是读一段计划文字：

- 将改/将删的卡 = **现状原文** + `plan: "modify" | "remove"`（卡头出徽标）
- 将新建的函数 = **幽灵卡** `plan: "add"`：虚线框，code 是计划中的签名/
  骨架草图（此函数尚不存在，原文规则自然不适用）
- 计划加/删的线打 `wire.plan: "add" | "remove"`（虚绿/虚红）
- 计划不动的邻居做上下文卡，不打 plan 标记
- step 序 = 作者审批时的问题序列：**治什么病 → 每处计划变更（逐个，说清
  为什么）→ 风险预判。风险预判步必选**——没有风险预判的计划不配开工
- **同底图铁律**：卡 id 与 layout 会被事后的 diff 画布复用。改完后跑
  `python3 compare.py plan.json diff.json --annotate checked.json`，
  机械对出「计划内 / ⚠ 计划外 / ○ 计划未动」，annotate 后的 diff 画布
  卡头带对照徽标。**对照结论必须如实汇报，计划外的改动不许瞒**
- 通过 Claude Code hooks 强制这个闭环：见 `hooks/README.md`
  （ExitPlanMode 前逼计划图，Stop 前逼 diff 图 + 对照）

## 规模闸门：大仓库先领航，后深潜

动笔前先估计**这张图要讲的东西**的代码量（不是仓库总行数）：

- **≤ ~2000 行**（一个模块 / 明确主题的切片）→ 直接出细粒度画布
  （函数级卡片 + 完整故事线，`demo/nano-vllm.json` 的形态）
- **更大，或用户就说"给我讲讲这个仓库"** → 先出**领航图**：
  - 卡片 = 子系统/模块（≤9 张）。卡内放"名片代码"：该子系统入口函数或
    核心类型定义的**原文连续摘选**（≤12 行），`file` 字段标 `路径:起始行`
  - 线 = 子系统间的调用/数据关系，每种关系一根代表线，不穷举
  - 故事线 = 架构关切（主流程 / 安全边界 / 状态与存储…），2-3 条
  - 块和变元注释从简；bg note 讲清"这个仓库是什么、怎么分层"
  - **最后一步的 caption 列出 3-5 个值得深潜的主题**，邀请用户挑
  - 用户挑了主题 → 按细粒度管线另出一张深潜画布（一个主题一张）

## 布局：agent 只给粗位置

每张卡片给 `layout: {col, band}`——列（0 起，左→右，按调用方向排）与
行带（0 起，上→下）。渲染器测量代码行宽定列宽、自动下推解决同列冲突、
用成员卡片 bbox 实时包住 region、根据 focus 列表自动计算镜头。
**不要**试图给像素坐标。

经验规则：入口函数 col 0；被调的下一层 col+1；同一 region 的卡尽量占
连续的列；一列不超过 3 张卡。

## 硬约束（写 JSON 时自查）

- 每步：线 ≤ 5、focus 里的卡 ≤ 4、高亮行 ≤ 6
- 每卡 intent note ≤ 3；bg 画布级 ≤80 字、blurb ≤20 字、概念 note ≤60 字
- **行宽**：目标 ≤90 字符，>100 会触发 validate 警告。允许对超宽行做
  **保持 token 顺序的换行重排**，且必须在交付报告中披露。重排的全部
  合法手段：注释续行补 `//`（Rust/JS）或 `#`（Python）；代码行用该语言
  的合法续行（Python 反斜杠 `\` 或括号内断行）。**边界**：token 一个不
  能少、顺序不能变——砍掉行尾内容、跳过中间的注释行都是改写，不是重排。
  除此之外卡片代码 = 原文，不改写、不省略中段；重排后所有行号锚点
  必须程序化重算
- **长函数可拆多卡**：连续摘选 + `file` 标 `路径:起始行`（此格式适用于
  一切卡片，不限领航图），卡名可带 ①②。**路径相对仓库根**，不要相对
  包目录或当前工作目录——溯源核查按仓库根解析
- **行号锚点用脚本算，不要手数**：块区间 / 线锚 / term 行号 / step 高亮
  一律在最终卡文上按子串搜索程序化计算（尤其做过换行重排时）
- 块必须能用一句话说清功能；深嵌套才用 children，不为分块而分块
- 不确定的意图不写进 note——note 是断言，不是猜测

## 布局与摆位的渲染器现实（评测实测出的规则）

- **宽度预算**：≥6 列的画布总览会小到难读，**≤5 列封顶**；行宽收窄可救列宽
- 左置 note 指向非 0 列的卡时，该列前会加一条 ~320px 的 note 车道（加宽画布）
- note **每次重绘跟随目标卡**：above 位被上方展开的卡挤压时自动下滑，
  实在没空间会退化成挂在目标卡左侧；同一张卡多条 `above` 仍会原地重叠，避免
- 步骤状态是**累积**的（`expand`/`unfold` 只加不减）：直达 `#s5` 和顺序走到
  第 5 步画面不同；截图自检以顺序走为准
- 调试尾缀 `q` 依赖块条的「问」按钮，无 blocks 的画布上无效果

## 截图自检清单

每张截图对照检查，任何一条不过就改 JSON 重渲染：

- [ ] 没有元素互相压盖（note 压卡、卡压卡、note 压区域标签）
- [ ] 每步的 focus 镜头框住了 caption 里提到的所有东西（尤其行注释）
- [ ] 点亮的线两端都可见，line 锚点落在正确的行上
- [ ] 折叠卡的大纲（块色条）在总览缩放下可读
- [ ] 每张卡至少被一个 step 聚焦过；没有 step 聚焦不存在的重点
- [ ] caption 说"注意 X"时，X 确实在画面里且处于点亮态

## 失败模式

- **大仓库直接细讲**：在十万行仓库上硬出函数级画布 = 随机切片，读者不知道
  自己在整体的哪里。先领航图，后深潜
- **全量连线**：把静态分析能找到的边都画上 = 毛线球。线是叙事的一部分
- **AST 粒度分块**：每个 for/if 都成块 = Blueprint 噪音。逻辑粒度才对
- **背景膨胀**：背景写成段落 = 退回线性文档。写不进上限说明没想清楚
- **step 即文件序**：步子要回答读者的问题（怎么进、怎么出、坏了会怎样），
  不是按文件逐个介绍
- **卡片改写代码**：卡内必须是原文。信任建立在"我看到了真实的行"上

## 文件

- `SKILL.md` — 本文件
- `DESIGN.md` — 设计决定与理由
- `schema.md` — canvas JSON 格式
- `template/canvas.html` — 数据驱动的单文件渲染模板（布局引擎在里面）
- `validate.py` — canvas JSON 验证器：机械错误 + 预算超限（渲染前必过）
- `render.py` — JSON → HTML 注入脚本
- `serve.py` — 块级问答服务：serve HTML + `/ask` 桥接 claude/codex CLI
- `tests/` — Playwright 交互回归 + 个性化端到端测试
- `demo/cache-demo.json` / `.html` — 参考示例（缓存中间件，4 步故事线）
- `mock/canvas-mock-v1.html` — 手工排版的形态原型（历史参考，勿再改）

## 与旧 skill 的关系

取代 `code-review-skill` 与 `code-reading-walkthrough` 的线性 HTML 文档
形态。旧 skill 的分析管线思想（storyline 策展、重要性取舍、意图注释）
保留，表达层全部换成本 canvas。
