# 底稿（下一层）：「生成 agent」里面是怎么把 JSON 想出来的

> 研究对象：code-canvas 仓库，HEAD = `79153e7`。路径相对仓库根（与上一趟一致，写作 `code-canvas/xxx`）。
> 接着上一趟底稿（`manuscript.md`）：它已经讲了 prompt 怎么拼、脚本怎么串、收尾怎么入库（§2.2、§2.5），这里不重复，只说 claude -p 这个框里面。
> 标【设】的值是我为了演示设的；【…】是「为什么」的来源等级。
> 约定：本稿里的 「…」 只用来放**逐字引文**。

---

## 0. 先说结论

**这个框里没有一段"驱动 agent 走流程"的 Python 代码。** 它的"程序"是一份自然语言文件 `SKILL.md`，加上 4 个小脚本（`extract.py`、`validate.py`、`check_refs.py`、`embed_context.py` / `render.py`）和 1 个会再起一个 claude 的脚本（`reader_check.py`）。代码只管三件事：把提示词拼好、把工具放在那儿、agent 退出以后收尾。中间怎么走，是模型自己的判断。

框里的东西，按"谁规定的"分三层：

1. **规定死的**（提示词和 SKILL 第 6 节）：交付文件的路径、校验通过的标准、卡里代码必须是原文、必须写 `meta.judgment`、**不许写入画布库**。
2. **要求做、但做什么由 agent 答的**：SKILL 第 2 节的 8 个判断，答案写进 `meta.judgment` 并附理由。
3. **完全自由的**：读哪些文件、先后顺序、用不用 `extract.py`、返工几轮、什么时候停。

**最值得读者记住的三点**：
- "不可商量"不等于"机器强制"。SKILL 说必须的事里，只有一部分有机器闸门（见 §2.7 的对照表）。
- agent 的"做完了"没有机器判据。它自己决定停，打印一句 `DONE`，没有任何代码读这个词（§2.5）。
- 新读者挑错的结果**不会自动回到 agent 手里**：只是一个文件，agent 要自己去读、自己决定改不改（§2.6）。

---

## 1. 框内全貌：几样东西、谁读谁写

### 1.1 部件（8 个）

| # | 部件 | 是什么 | 谁写 / 谁读 | 锚点 |
|---|---|---|---|---|
| ① | **提示词** | 三段拼起来的文本，落盘成 `<id>.prompt`，用 `$(cat …)` 喂给 claude | spawn_generate 写；agent 读 | `code-canvas/serve.py:762`、`code-canvas/serve.py:648-651` |
| ② | **SKILL.md（及 schema.md 等规格文档）** | 只读的规程。目标 + 8 个判断 + 推荐循环 + 工具 + 契约 | agent 读 | `code-canvas/SKILL.md:11-14`；目录是 serve.py 所在目录 `code-canvas/serve.py:64` |
| ③ | **仓库缓存** | claude 进程的 cwd；约定只读 | 上一趟 §2.3；agent 读 | `code-canvas/serve.py:740` |
| ④ | **工作目录** `<hub>/.jobs/<id>/` | agent 的全部产物：`canvas.json`（必交）、`canvas.html`、`structure*.json`、截图 `*.png`、`reader_report.md`（文件名 agent 自取） | agent 写；监视器按文件名猜进度 | `code-canvas/serve.py:598`、`code-canvas/serve.py:512-520` |
| ⑤ | **extract.py** | 零 LLM 的 AST 提取器；读缓存里的 `.py`，写 `structure.json` | agent 调；结果 agent 自己读 | `code-canvas/extract.py:2-9` |
| ⑥ | **校验工具环** | `validate.py`（只读）、`check_refs.py`（**就地改写** canvas.json）、`embed_context.py`（**就地改写**）、`render.py` | agent 调；退出码和 stdout 回到 agent | `code-canvas/SKILL.md:115-123` |
| ⑦ | **reader_check.py → 第二个 claude** | 临时目录里的"新读者" | agent 调；读者读临时副本，写报告文件 | `code-canvas/reader_check.py:50-75` |
| ⑧ | **agent 自己的上下文** | `meta.judgment` 草稿、假设、已读的文件——除了落进 JSON，没有别处存 | — | `code-canvas/SKILL.md:78-81` |

### 1.2 交接（画线用）

1. spawn_generate → agent：提示词文件。agent 一启动就读到 `阅读 {skill}/SKILL.md`（`code-canvas/serve.py:668`）。
2. agent → 仓库缓存：读代码（只读约定）。
3. agent → extract.py → `structure.json`：可选（§2.3）。
4. agent → `canvas.json`：agent 自己写。
5. agent ⇄ 工具环：`validate.py` 等。**返回通道只有 stdout 和退出码**，agent 自己读。
6. agent → reader_check → 报告文件 → agent：见 §2.6。
7. agent 进程退出 → 服务端收尾（上一趟 §2.5）。

**读者最容易困惑的一点**：agent 的 cwd 是**缓存**，产物却要写进**工作目录**（`code-canvas/serve.py:659-667` 给的是绝对路径）。所以"agent 自己渲染的 `canvas.html`"在工作目录里，不在缓存里。缓存下次刷新时还会被 `git clean -fdx`（`code-canvas/serve.py:727`），写在缓存里的东西会丢。

---

## 2. 逐个问题走下去

### 2.1 agent 拿到提示词后先做什么？哪些规定死、哪些自己判断？

提示词只有三块：`head + task_line + tail`（`code-canvas/serve.py:736`）。

- **head**（`code-canvas/serve.py:668-669`）：读 SKILL.md。原话「它不是一条要逐步执行的管线」。
- **task_line**（`code-canvas/serve.py:713`）：「任务：为仓库 {w} 生成一张 Code Canvas，主题/关注点：{a}」。
- **tail**（`code-canvas/serve.py:659-667`）：交付路径、契约、完成标准、自查建议、「不要写入画布库」、打印 DONE。

对照着看，哪些是死的、哪些是活的：

| 事项 | 谁定 | 锚点 |
|---|---|---|
| 交付文件在哪 | 死：绝对路径 `<workdir>/canvas.json` | `code-canvas/serve.py:659` |
| 校验标准 | 死：validate 无 ERROR、check_refs 全过、embed 嵌入（仅有仓库时） | `code-canvas/serve.py:660-662` |
| 不许写画布库 | 死 | `code-canvas/serve.py:666` |
| 读什么、先后 | 活：「顺序和轮数由你决定」 | `code-canvas/SKILL.md:71` |
| 做不做 extract | 活（SKILL 只说"可用"） | `code-canvas/SKILL.md:74-77` |
| 做几轮 reader_check | 半死：提示词说"用 reader_check.py 让新读者挑错"，SKILL 说「强烈建议至少一轮」，没有任何东西检查 | `code-canvas/serve.py:664`、`code-canvas/SKILL.md:83` |
| 什么时候停 | 活：没有 `--max-turns`，没有超时 | `code-canvas/serve.py:648-651` |

**为什么交给 agent 判断，而不是写成编号步骤**：
- 提交 `f4bbe29`（标题「SKILL v0.2 agent 版：目标+判断标准+决策清单取代编号管线；reader_check 新读者挑错；meta.judgment 判断记录」）的 diff 里，原来提示词的 tail 是「步骤：1) 产出 {w}；」「2) python3 {skill}/embed_context.py {w} <仓库根>」，head 是「严格按其管线执行」；新版把它们换成了现在的样子。提交说明只有标题没有正文，**没写为什么换**。
- 我的推断：SKILL 第 10 节把「填表」（原文：「为了消掉 warn 而写出空洞的 overview/scenarios/trace」）列为失败模式（`code-canvas/SKILL.md:200`），说明改版之前观察到 agent 按清单填空【推断】。
- 另有一条旁证：评测方法论要求「方法论必须完全由 skill 文件承载」（`code-canvas/eval/README.md:9`）【文档】。也就是说，SKILL.md 本身就是被考核的"程序"，提示词里没必要再复述一遍步骤。
- 同一个设计冲动在后面的文档里被明确否决过一次：11 月 15 日"形状问卷"想把"画什么"固定成封闭维度，被作者判「做太细」（`ROADMAP.md:358`）【文档】。这说明作者选择把"画什么"留给模型判断，而不是穷举规则。

### 2.2 SKILL 里那 8 个判断是什么？nano-vllm +「讲讲调度器怎么工作」会怎么答？

8 个判断在 `code-canvas/SKILL.md:42-67`，要求「答案写进 `meta.judgment`」，「每一条都要写理由」（`code-canvas/SKILL.md:37-38`）。要求先答再动笔：「动笔写 JSON 之前，先读代码」（`code-canvas/SKILL.md:37`）。

下表第 4 列是**我设的、一个可能的答案**（agent 实际每次答得不同，从代码推不出来；材料来自仓库自带的 nano-vllm 样板 `code-canvas/demo/nano-vllm.json`）：

| # | 判断 | SKILL 锚点 | 写进 judgment 的键 | nano-vllm + 调度器，一种可能答案【设】 | 答案会引出什么分支 |
|---|---|---|---|---|---|
| 1 | 范围与规模 | `SKILL.md:42-46` | `scope` | 样板副标题写「~1200 行的 vLLM 教学重写」（`demo/nano-vllm.json:4`），不到 2000 行 → 深潜；收窄到 step / Scheduler / BlockManager | ≤约 2000 行深潜；更大先出预览地图 |
| 2 | 故事形态 | `SKILL.md:47-53` | `shape` | 循环（引擎一拍一拍）叠加一生（seq 在 waiting/running 间进出） | 决定 steps 怎么排 |
| 3 | 主角 | `SKILL.md:54-55` | `protagonist` | `Sequence`（请求） | 决定"一生"这条线的主语 |
| 4 | 部件怎么切 | `SKILL.md:56-58` | `parts` + `handoffs` | Scheduler 拥有 waiting/running 队列；BlockManager 拥有块账本；两者交接在 `schedule()` 里 | 决定 regions 和卡的分组 |
| 5 | 剪什么 | `SKILL.md:59-61` | `cut` | ModelRunner、Sampler 折叠成上下文卡 | 折叠卡 `collapsed:true` |
| 6 | 每个机制用什么表达 | `SKILL.md:62-64` | `tools` | 块池变化 → 状态快照卡；prefill 预算切分 → 块沙盘；抢占 → 步卡 trace | 决定填哪些可选字段 |
| 7 | 和谁对比 | `SKILL.md:65` | **没有单独的键**，落在 `parts[].not` | "Scheduler 不是 BlockManager：它决定谁跑，不管块怎么记账" | 写进 about / note |
| 8 | 演示哪些情况 | `SKILL.md:66-67` | `scenarios` | 预算够 / 第二条被挡 / 单条超预算被切片 / 抢占（样板里沙盘就是这四种输入） | 决定 `scenarios` / `block.run` 的输入 |

**容易看错的两点**：
- 8 个判断对应 `judgment` 的 **11 个键**，不是 8 个（`handoffs` 与 `parts` 同属判断 4；多出 `skipped`、`revisions`、`reader_rounds`；"和谁对比"又没有自己的键）（`code-canvas/SKILL.md:150-164`）。
- 判断 8 的依据要求「从配置项、错误分支和测试用例里找，不要凭空编」（`code-canvas/SKILL.md:67`）。样板沙盘里的 `MAX_NUM_BATCHED_TOKENS = 16`、`MAX_NUM_SEQS = 3` 就是这么来的（`code-canvas/demo/nano-vllm.json:91` 起的 schedule 卡，`run.harness` 字段）。

**`meta.judgment` 长什么样**（格式见 `code-canvas/SKILL.md:150-164`；下面是我设的内容，值取自样板）：

```json
"judgment": {
  "scope": "读者问调度器怎么工作。约 1200 行，走深潜；收窄到 step / Scheduler / BlockManager，ModelRunner 只留一根线",
  "shape": "循环（step 每拍）叠加一生（seq 在 waiting/running 间进出），在 schedule() 交汇",
  "protagonist": "Sequence。被调度器改状态最多，被到处传递",
  "parts": [{"name": "Scheduler", "owns": "waiting / running 队列",
             "not": "不是 BlockManager：它决定谁跑，不管块怎么记账",
             "why": "按谁拥有哪份状态切，不按文件切"}],
  "handoffs": ["step 第 2 行 → schedule：取出本拍的一批 seq"],
  "cut": "ModelRunner.run、Sampler 折叠：不改调度状态",
  "tools": [{"mechanism": "块池随步变化", "means": "state 快照卡", "why": "读者看不见哪块被谁占着"}],
  "scenarios": "预算够 / 被挡 / 切片 / 抢占——来自 schedule 的分支",
  "skipped": "overview.example 没写：数字要与代码一致，来不及复核",
  "revisions": ["我原以为一批里 prefill 与 decode 可混排，代码表明 is_prefill 是整批标志"],
  "reader_rounds": [{"found": "读者不懂 can_allocate 返回 -1", "fix": "补一条 term 注释"}]
}
```

**这份 judgment 之后去哪**：它会跟着 canvas.json 入库，但**没有任何人读它**：
- 渲染器不显示：「渲染器目前不显示 judgment；它是给作者复查的，所以写实话，不写套话」（`code-canvas/SKILL.md:166`）。我在 `code-canvas/template/canvas.html` 里也没搜到这个词【推断：基于 grep 无结果】。
- validate 只在整体缺失时 warn，以及 `scope/shape/protagonist/parts/cut` 五项为空时 warn（`code-canvas/validate.py:306-313`）；`tools/scenarios/revisions/reader_rounds` 等不检查。
- 新读者看不到它（`code-canvas/reader_check.py:61`，§2.6）。

**文档自相矛盾的地方**：SKILL.md:38-39 说它「既是给读者的导读，也是给作者复查你判断的依据」，SKILL.md:166 又说渲染器不显示。两处都在，我没找到哪一句是后改的。

### 2.3 extract.py 机械产出什么？agent 照抄还是只拿来当材料？

**产出**：一个"结构层"canvas：每个顶层函数和类方法一张卡（原文逐字），一根 call 线 = 调用点所在行 → 被调卡，一个粗布局，一条"总览（结构层）"步（`code-canvas/extract.py:46-76`、`code-canvas/extract.py:104-133`、`code-canvas/extract.py:181-219`、`code-canvas/extract.py:357-360`）。叙事层一概没有：「叙事层（故事线/note/explain/terms/steps）」「一概不产出——那是 agent 的层」（`code-canvas/extract.py:5-6`）【注释】。

**三条规则决定它"能看见什么"**：
- 只认顶层函数和类的直接方法；函数里嵌套的 def 算父函数的代码（`code-canvas/extract.py:47`、`code-canvas/extract.py:68-75`）。
- 只扫 `.py`（`code-canvas/extract.py:38-42`；注释 `code-canvas/extract.py:8` 写明 v1 只支持 Python）。非 Python 仓库只能 agent 手摘。
- call 线**只按名字最后一段匹配，且被调名在全仓只对应一张卡时才画**（`code-canvas/extract.py:111-120`）。注释原话「线是断言不是猜测」（`code-canvas/extract.py:105`）【注释】。所以 `self.scheduler.schedule()` 能连，而重名方法一律没线。它不做类型解析，所以也可能连错——是否连错取决于仓库，我没在 nano-vllm 上跑过，没法说。

**布局**：入度 0 的卡为根 → col 0，沿 call 线广度优先，被调者 col+1，最多 5 列；同列按 (file, 起始行) 排成 band 0,1,2…（`code-canvas/extract.py:181-219`）。每张卡 `collapsed: True`（`code-canvas/extract.py:330`）。

**agent 是照抄还是当材料**：SKILL 说得很明白：「它是研究材料，不是答案」（`code-canvas/SKILL.md:75-76`）【文档】。分两层：
- **事实字段**（code/file/行号/diff）不许手改；
- 删哪些、留哪些、怎么讲，全是 agent 的判断（`code-canvas/SKILL.md:76-77`）。

对照样板能看出"材料≠答案"：样板的列是按故事线放的（A 线 step/run/sampler 在 col 0，B 线 schedule/preempt/postprocess 在 col 1，C 线在 col 2，快照卡 col 3，见 `code-canvas/demo/nano-vllm.json` 里各卡的 `layout`），不是 extract 那种纯调用深度。样板是旧格式（见上一趟 §3 第 5 步），所以这只能说明"agent 可以覆盖"，不能说明每次都覆盖。

**为什么 extract 存在而且不强制**：
- 存在：它是「毫秒级」产出行号精确的原文卡（`code-canvas/SKILL.md:75`）。SKILL 要求行号锚点用脚本算而不是手数（`code-canvas/SKILL.md:138`）。
- 不强制：hub 的监视器只是**找工作目录里有没有 `structure*.json`** 来显示"结构层"这一阶段，没有就不亮（`code-canvas/serve.py:514`）。上一趟 §5 第 8 条已经讲过。
- 脚本用标准库、兼容 Python 3.8（`code-canvas/extract.py:20`）：因为 hub 主机是 3.8（`AGENTS.md:14`）【文档】。

**`--merge` 增量协议**（`code-canvas/extract.py:222-291`）：代码没变的卡保留旧叙事和布局，变了的丢弃行锚叙事并列「待重讲」。这是升级 / 重生成的通道，**新生成不会用到**，这里不展开。

### 2.4 canvas.json 里一张卡、一根线、一步推演各长什么样？col / band 怎么定？

下面三段都取自仓库自带样板 `code-canvas/demo/nano-vllm.json`（旧格式，**不是**这次 agent 的产出）。

- **一张卡**（schedule，`demo/nano-vllm.json:91`）：`id`、`name: "Scheduler.schedule()"`、`file` 写成「路径:起始行」的形式（这张卡是 engine/scheduler.py 的第 25 行起）、`layout: {col:1, band:0}`、`collapsed:false`、`code`（原文 49 行）、`blocks`（"Prefill 批次" 5–31、其中"前缀缓存探测" 11–17；"Decode 批次" 33–46、其中"抢占腾块" 36–41；"收尾" 47–49）。块是**行段树，不是 AST**（`code-canvas/schema.md:51`）。
- **一根线**（`demo/nano-vllm.json:523`）：`w-sched`，`kind:"call"`，`from: {card:"step", line:2}` → `to: {card:"schedule"}`。线的起点钉在代码行上（行号是卡内行号）。
- **一步**（`demo/nano-vllm.json:733`）：①「一次 step 的骨架」，`focus: [step, schedule, run, postprocess]`，`wires: [w-sched, w-run, w-post, d-token]`，`lines: [[step,2],[step,4],[step,5]]`，`storyline: "A"`。`focus` 的书写顺序就是阅读顺序（`code-canvas/schema.md:179-182`）。

**"一步推演"在 schema 里有两种东西**，不要混：
- `steps` = 镜头路径，一步一个 caption；
- `scenarios` = 拿一组具体数沿代码**逐跳**走（每跳 ≤12 行 + 此刻变量值），3–40 跳，渲染器统一标「推演 · 非实测」（`code-canvas/schema.md` 的"情境推演"一节；校验在 `code-canvas/validate.py:627-654`）。

**col / band 怎么定**：
- schema 的定义是"粗位置"：列左→右，行带上→下（`code-canvas/schema.md:42`）。像素排版是浏览器做的（上一趟 §2.4、§2.7）。
- SKILL 给的是**经验规则**：入口 col 0，被调者 col+1，≤5 列，一列 ≤3 张（`code-canvas/SKILL.md:144-145`）。
- **机器只检查两件事**：每张卡必须有 `layout.col` 和 `layout.band`（ERROR，`code-canvas/validate.py:199-200`）；一列超过 3 张卡 warn（`code-canvas/validate.py:238-243`）。"≤5 列"和"不要像素坐标"，我在 validate.py 里没找到检查。

**预算类硬约束，谁在管**：SKILL 第 6 节的数字（`code-canvas/SKILL.md:142-143`）里，**validate 有 warn 的**：每步点亮线 >5（`code-canvas/validate.py:529-530`）、高亮行 >6（`code-canvas/validate.py:531-532`）、每卡 intent note >3（`code-canvas/validate.py:300-301`）、单行 >100 字符（`code-canvas/validate.py:202-203`）。**我没找到检查的**："focus 卡 ≤4"。
- 这些 validate 里全是 warn，不拦收：文件头自述「Warnings are curation-budget violations from schema.md's hard limits」（`code-canvas/validate.py:6`）【注释】。
- 行宽有三个数：SKILL 写"目标 ≤90"（`SKILL.md:143`）、validate >100 才 warn、schema 写"代码行 ≤ 60 字符"（`code-canvas/schema.md:378`）。我没找到为什么三处不一样。

### 2.5 agent 自己跑 validate / check_refs 时报错会怎样？它怎么判断"做完了"？

**三个工具回给 agent 的信号各不相同**：

| 工具 | 报错时 | agent 看到什么 | 对文件的影响 |
|---|---|---|---|
| `validate.py` | `ERROR` 行 + 退出码 1；warn 不影响退出码（除非 `--strict`） | 每条 `ERROR  …` / `warn   …`，末行 `N errors, M warnings`（`code-canvas/validate.py:657-662`） | 只读 |
| `check_refs.py` | **退出码永远 0**（剔除也算正常完成），只有参数错才 1（`code-canvas/check_refs.py:11`）。agent 必须读 stdout 里「依据核对：{} 条，通过 {}，剔除 {}」（格式串） | `剔除 … — 符号「…」不在该行 ±3 行内` 等（`code-canvas/check_refs.py:77-81`） | **就地改写 canvas.json**：删假依据；`ev` 缺省或 fact 的步卡依据被剔光 → 降级 infer（`code-canvas/check_refs.py:72-75`） |
| `embed_context.py` | 一张卡都嵌不进 → 退出码 2；其余跳过的卡只在 stderr 列出（`code-canvas/embed_context.py:85-87`、`code-canvas/embed_context.py:94-95`） | "嵌入 N 文件 / M 卡"、"行漂移修正"、"跳过: id — 原因" | **就地改写**：写 `files`；对不上时**改卡的 `file` 行号**（`code-canvas/embed_context.py:79`） |

**因此"check_refs 全过"没有退出码可看**：agent 要读输出，看剔除是不是 0。tail 里写的是「check_refs.py <仓库根> 全过」（`code-canvas/serve.py:661`）。这是纯靠读输出判断。

**为什么 check_refs 是"剔除+降级"而不是"报错"**：它的文件头写「读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底」（`code-canvas/check_refs.py:8`）【注释】。同一批脚本，agent 跑一遍当自查，服务端再跑一遍当闸门。

**为什么 embed 是必选项**：提交 `40781a8` 写：「根因：--embed-context 只是 SKILL 命令注释里的可选项，生成 agent 从不执行」【提交】。教训是：**可选项 agent 不会自己做**，所以升成必选步，同时服务端也兜底（`code-canvas/serve.py:627-633`）。

**validate 在 agent 眼里是"ERROR 清零"的游戏，而 warn 有设计过的含义**：SKILL 说 warn 要当作"你考虑过了吗"的提醒，「确实用不上的，在 judgment.skipped 里写一句为什么」（`code-canvas/SKILL.md:112-113`）【文档】。这里有来历：
- 提交 `aa37529` 写「实案：vllm-kv / vllm-sched 从零重生成后可信层比升级版还薄」【提交】：agent 完全自由时，选择性偷懒是真实发生过的，于是加了 warn。
- 与之对冲的是 SKILL 第 10 节：「宁可不写并说明」（`code-canvas/SKILL.md:200`）【文档】，防止 agent 为消 warn 填空。
- 我的理解（推断）：这两条拉扯的结果就是"warn 不拦收，但要在 judgment 里交代"【推断】。

**"做完了"的判据有三层，只有第一层是机器的**：
1. **机械层**：validate 0 ERROR；check_refs 输出无剔除。服务端之后会再检一遍，validate 不过 → rc=4，画布**不入库**（`code-canvas/serve.py:633-636`；测试 `code-canvas/tests/hub.mjs:630-631` 断言 status=4 且库里没有这份 json）【测试】。
2. **判断层**：SKILL 第 1 节的六条——读者只看画布能不能做到（`code-canvas/SKILL.md:18-33`）。提示词也把它写成完成标准（`code-canvas/serve.py:663`）。
3. **看图层**：截图自己看，问"一个第一次看的人，跟着步子走完，能做到第 1 节的六条吗？"（`code-canvas/SKILL.md:131`）。

**agent 什么时候停？** 没有机制。提示词让它「完成后打印 DONE」（`code-canvas/serve.py:667`）。我在 serve.py 里搜 `DONE`，只出现在三处提示词里，没有任何代码读它。agent 退出才是信号：退出码为 0 → 看 canvas.json 在不在 → 不在则 rc=5（`code-canvas/serve.py:633-636`；测试 `code-canvas/tests/hub.mjs:339-341` 的注释断言：没有产出 canvas.json 的任务必须是 failed(5)，不能是 done）【测试】。

### 2.6 reader_check：怎么起第二个 claude、只给它看什么、问题怎么回到 agent 手里？

**机制**（`code-canvas/reader_check.py:50-75`，没几行，值得从头读）：
1. 读 canvas.json 到内存（58）；
2. **去掉 `files`**（59-60；`flow` 模式例外）；**去掉 `meta.judgment`**（61）——这两处只改内存里的副本，原文件不动；
3. `mkdtemp` 一个临时目录，把剩下的写成 `canvas.json`（62-63）；
4. 命令：`claude -p <提示词> --model sonnet --allowedTools Read --output-format text`（69），cwd = 临时目录，超时 900 秒（70）；默认模型来自 `--model` 或环境变量 `READER_MODEL`，默认 `sonnet`（54）；
5. stdout 写到 `--out`（默认 `reader_report.md`，在 agent 当前目录）；stdout 为空则写「(读者没有输出) 」加 stderr 末 2000 字（71-72）；删临时目录（73）；退出码 = claude 的退出码（75）。

**读者被要求做什么**：提示词让它扮"有经验的工程师，第一次接触这个代码库"，「不要读任何其他文件，不要找仓库」（`code-canvas/reader_check.py:15`），写四节：主角的一生（5–10 步）、每个部件一句话（含"它不是什么"）、3–8 个读不懂的具体问题（带 step 序号或卡 id）、可疑或矛盾处，末行自评理解度 X/10（`code-canvas/reader_check.py:18-34`）。四节与 SKILL 第 1 节的六条**对应**，agent 拿报告对照第 1 节逐条看（`code-canvas/SKILL.md:88`）。

**为什么这样设计**（逐处）：
- **看不到仓库、没有 files**：「和真实读者在画布上看到的内容一致」（`code-canvas/reader_check.py:6`）【注释】；提示词里写着「你要检验的正是"只看这张画布能不能读懂"」（`code-canvas/reader_check.py:16`）【注释】。
- **去掉 judgment**：「读者不看作者的自述，只看画布本身」（`code-canvas/reader_check.py:61`）【注释】。否则作者的理由会替画布补全信息，测试就失真。
- **为什么是另一个 claude 而不是 agent 自己再读一遍**：SKILL 的理由是「读者说错的地方往往不是他笨，而是画布没讲清」（`code-canvas/SKILL.md:90`）【文档】。推断：作者读自己写的东西会脑补缺失的上下文，只有切断上下文的读者能测出缺口【推断，依据：提示词强调"没有任何上下文"，`code-canvas/SKILL.md:87-88`】。
- **`--model` 默认 sonnet，而生成 agent 默认 opus**：没找到理由（见 §7）。
- **提示词紧跟 `-p`，`--allowedTools` 放后面**：我没在 reader_check 里找到注释。但同仓库另一处 `run_cli` 有一句「prompt 必须紧跟 -p：--allowedTools 是可变参数，放它后面会被吞成工具名（实案）」（`code-canvas/serve.py:89`）【注释】；reader_check 同样的顺序大概率是同一个原因【推断】。

**问题怎么回到 agent 手里：没有自动通道。** 报告只是一个文件，agent 要自己 `Read`。之后：
- 「对照第 1 节逐条看他哪里做不到，回去补；补完可以再测一轮」；
- 「把每轮发现了什么、你改了什么记进 judgment.reader_rounds」（`code-canvas/SKILL.md:88-89`）。
- `reader_rounds` 不被校验（见 §2.2）。轮数没有上限，也没有任何东西强制至少一轮。

**两个边界要知道**：
- 隔离靠 cwd + 提示词，不靠沙盒。`--allowedTools Read` 是"自动放行 Read"，我没在代码里看到限制 Read 的路径，所以"读者不看仓库"依赖它遵守提示词【推断】。
- `subprocess.run(..., timeout=900)` 超时会抛异常（`code-canvas/reader_check.py:70`），而 `rmtree` 不在 `finally` 里（73）：超时时 agent 看到的是一段 traceback，没有报告，临时目录残留【推断，依据：Python 语义，代码里没有 try】。
- 这第二个 claude 的开销不在 `result.json` 的指标里：上一趟 §5 第 9 条。

### 2.7 为什么交付契约里有些事"不可商量"，其它交给 agent？

**先看契约是什么**（`code-canvas/SKILL.md:133-146`），再看**谁在执法**。这是整个框里最值得对照看的一张表：

| 契约条目 | 机器强制吗 | 怎么强制 / 没强制 |
|---|---|---|
| validate 无 ERROR | **是，硬闸门** | 服务端不过则 rc=4，不入库（`code-canvas/serve.py:633-636`） |
| check_refs 全过 | 兜底，不拒收 | 服务端再跑，剔除假依据（`code-canvas/serve.py:620`）。退出码被忽略（上一趟 §5 第 3 条） |
| 嵌入上下文 | 兜底，不拒收 | 失败只是画布没上下文 |
| 卡内代码是原文 | **弱** | 唯一机械对照是 `embed_context`：对不上的卡「如实跳过并列出」（`code-canvas/embed_context.py:9`、`code-canvas/embed_context.py:76-78`），画布照常入库。validate 只会 warn"file:line 卡无 files 映射"，而且只要同一个文件有别的卡嵌进去了，就不 warn（`code-canvas/validate.py:128-137`） |
| 行号锚点用脚本算 | 只查越界 | 越界是 ERROR（如 `code-canvas/validate.py:33`、`code-canvas/validate.py:262`），算没算对不查 |
| 推断标 ev / unknown 写 need | 只查格式 | ev 枚举和 need 必填是 ERROR（`code-canvas/validate.py:383-386`）；**该不该标**查不了 |
| 布局只给 `{col, band}` | 只查有没有 | `code-canvas/validate.py:199-200` |
| 预算数字（≤5 线等） | warn | 见 §2.4 |
| `meta.judgment` 必须存在 | **只是 warn** | `code-canvas/validate.py:309` |

**分界线在哪**：validate 文件头自己说了：Errors 是「structural breakages the renderer cannot survive gracefully」，Warnings 是预算超限（`code-canvas/validate.py:4-6`）【注释】。所以：
- **能让渲染器崩的** = ERROR = 机器强制（悬空引用、越界行号、坏块树）；
- **只影响质量的** = warn，或者干脆没有机器检查。

**为什么服务端不信任 agent**：`post_pass` 的 docstring：「服务端入库（不信任 agent 守规程）」（`code-canvas/serve.py:609`）【注释】。`40781a8` 的事故（agent 从不执行可选项）和 `aa37529` 的事故（agent 偷懒）是它的实例【提交】。

**为什么"不可商量"一节里有些没人执法**（比如 judgment 只 warn）：我没找到明说。`validate.py` 的 judgment 检查是跟 SKILL v0.2 同一个提交 `f4bbe29` 一起加的，当时就写成了 warn 而不是 err，且对 `plan`/`diff` 模式豁免（`code-canvas/validate.py:307`）。可能是为了兼容旧画布，但提交没说【推断，不确定】。

---

## 3. 贯穿例子：nano-vllm +「讲讲调度器怎么工作」，这个框里发生了什么

> 承接上一趟 §3：job id `j20261123-101530-482`，cwd 是 `/home/ubuntu/hub/.repos/nano-vllm-155c1586`，agent 的交付文件是 `/home/ubuntu/hub/.jobs/j20261123-101530-482/canvas.json`。**下面是我设想的一趟**【设】：agent 每次走法不同，但工具输出的格式是从代码来的。

1. **读 SKILL.md**，再读 schema.md 里要用的部分。
2. **先答判断 1**：这仓库不大（样板副标题 `~1200 行`）→ 深潜；问题是调度器 → 收窄到 step / Scheduler / BlockManager。
3. **跑 extract**【设】：`python3 extract.py <缓存里的源码目录> --repo-root <缓存> --out <工作目录>/structure.json`，stderr 回 `wrote …  (N cards, M wires)`（格式见 `code-canvas/extract.py:363-364`）。它得到的是一堆折叠卡 + 按名字匹配的 call 线。注意：SKILL 要求 `file` 相对**仓库根**（`code-canvas/SKILL.md:139`）；样板里写的是 `engine/scheduler.py`。如果真实仓库在更深一层目录，卡的 `file` 必须带上那层，否则 `check_refs` 用 `repo / ref[0]` 找不到文件（`code-canvas/check_refs.py:22,27-28`）。`embed_context` 会自动找子目录根，`check_refs` 不会（`code-canvas/embed_context.py:35-51`）。
4. **读真实代码、答完 8 个判断**，写 judgment 草稿（§2.2 那段）。
5. **写 canvas.json**：10 张卡，`step/run/sampler` 放 col 0，`schedule/preempt/postprocess` 放 col 1，`can_allocate/allocate/hash_blocks` 放 col 2，一张块池快照卡放 col 3。这与上一趟 §3 一致。
6. **第一次 validate**【设】输出形如：
   ```
   ERROR  线 w-hash: to.card hash_blocks 不存在
   warn   第 1 列有 4 张卡（>3）: schedule, preempt, postprocess, x
   
   1 errors, 1 warnings
   ```
   格式依据 `code-canvas/validate.py:258`、`code-canvas/validate.py:242`、`code-canvas/validate.py:658-661`。agent 修 id、把一张卡挪到别的列，再跑一遍，直到 0 errors。
7. **check_refs**【设】：`依据核对：6 条，通过 5，剔除 1`，并列出被剔的那条（格式 `code-canvas/check_refs.py:77-78`）。因为文件已被改写，agent 要重新读 canvas.json 再继续。
8. **embed_context**：`嵌入 4 文件 / 9 卡，共 NN KB`（`code-canvas/embed_context.py:90-91`）。
9. **render + 截图自看**：渲染到 `<工作目录>/canvas.html`（`code-canvas/serve.py:664`）。agent 会看每步的镜头是否框住了 caption 提到的东西（`code-canvas/SKILL.md:125-131`）。
10. **reader_check**【设】：新读者报告里写"第③步 `can_allocate` 返回 -1 没解释"。agent 在那张卡补一条 term 注释，把这一轮记进 `judgment.reader_rounds`，可能再测一轮。
11. **打印 DONE，退出**。进入上一趟的 §2.5。

---

## 4. 值得做成源码页的代码段（6 段）

**页 1：`code-canvas/serve.py:658-669`（提示词的 tail 和 head，12 行）** —— 契约的"原件"。
- `658`：`has_repo = src["kind"] != "code"`。粘贴代码源没有仓库，所以 tail 里 check_refs / embed 两项要**有条件拼接**（`661`）。
- `660-662`：「SKILL 第 6 节的交付契约不可商量」后面列了 5 样（后两项「卡内代码是原文」「meta.judgment」没有机械手段，见 §2.7）；其余 SKILL 第 6 节的（预算数字、布局等）不在提示词里。
- `663`：「其余怎么做由你判断。」和完成标准——把"做完了"定义为 SKILL 第 1 节六条。
- `664-665`：自查（渲染、截图、reader_check）以**建议**口吻写。
- `666`：「不要写入画布库」。为什么：提交 `4b43d42` 写「agent 只产工作目录 canvas.json，服务端 embed + check_refs + validate」「全过才入库（done ⇔ 已入库」【提交】。
- `667`：`DONE`。没有读者。
- `668-669`：head。对比 `f4bbe29` 前的版本（「严格按其管线执行」），可以直接看到"管线→判断"的改动【提交】。

**页 2：`code-canvas/SKILL.md:35-67`（8 个判断，33 行）**
- `37-41`：先答后画；「每一条都要写理由」；「没有标准答案，但"照目录结构切""每张卡都画"这类不经思考的答案是错的」——给了反例而不是正例。
- `43-46`：约 2000 行以内做深潜、更大先出预览。（是 agent 自己估，没有代码在量行数。）
- `56-58`：部件按「职责和执行归属」切；部件之间的交接点「是故事的分章处」。
- `59-61`：「全量的卡和线等于毛线球」——为什么要剪。
- `64`：「不要因为某个字段存在就填它」。
- `67`：「不要凭空编」。

**页 3：`code-canvas/SKILL.md:133-164`（契约 + judgment 格式，32 行）**
- `133-134`：标题就叫"不可商量"，对照 §2.7 的表看谁在执法。
- `136-137`：超宽行只许「保持 token 顺序的换行重排」，要披露。
- `138`：行号锚点「一律用脚本在最终卡文上计算，不要手数」。
- `142-145`：数字预算和 `{col, band}`。
- `146`：`meta.judgment` 必须存在（对照 `validate.py:309` 只是 warn）。
- `150-164`：judgment 的 11 个键。

**页 4：`code-canvas/extract.py:104-133` + `181-219`（call 线和布局，共约 70 行）**
- `105`：「线是断言不是猜测」。
- `111-116`：只取调用名的最后一段（`Name.id` 或 `Attribute.attr`）。
- `120`：`len(targets) != 1 or targets[0] is f` 同名不画、自调不画。
- `123-125`：同一对卡只画一根（`seen_pair`）。
- `190`：入度 0 的卡为根；没有就全部当根（环）。
- `194`：`depth < 4`，最多到 col 4；`204`、`208`：没连上的卡丢到 col 4（`min(col[i], 4)`）——"≤5 列"在这里是硬编码。

**页 5：`code-canvas/reader_check.py:58-75`（主流程，18 行）**
- `59-60`：去 `files`；`61`：去 `judgment`。
- `63`：`indent=1` 写临时副本。
- `69`：命令行。提示词必须紧跟 `-p`（对照 `serve.py:89` 的注释）。
- `70`：`timeout=900`，没有 `try`。
- `71`：空输出也要写一份报告，让 agent 能知道"读者没出声"。
- `75`：退出码透传。

**页 6：`code-canvas/check_refs.py:18-36` + `59-81`（依据核对，约 45 行）**
- `19-21`：格式检查。
- `22-26`：`lstrip("./")` + `resolve()` + `relative_to(repo)`，拦路径逃逸。
- `32-35`：符号必须在行号 **±3 行**窗口里（`ref[1]-4` 到 `ref[1]+3`），容忍 agent 数偏几行。
- `72`：`h["refs"] = good` 就地改写。
- `73-75`：依据被剔光的 fact 步卡降级 infer。
- `76`：无论有没有剔除都回写文件。
- 备选：`code-canvas/validate.py:564-566`（`corpus`：卡代码 ∪ 引用文件全文）和 `649-651`（推演里的变量名必须出现在 corpus 里，否则 ERROR）——"不许编造"的核心。

---

## 5. 宏观"为什么"汇总

| 问题 | 答案 | 等级 |
|---|---|---|
| 为什么让 agent 来写 JSON，而不是脚本直接生成 | 脚本只能产事实层（卡/线/粗布局），叙事层「那是 agent 的层」 | 【注释】`code-canvas/extract.py:5-6` |
| 为什么 agent 只给粗位置 | 上一趟 §2.4 已讲 | 见上一趟 |
| 为什么规程是一份 SKILL.md | 评测的前提：「方法论必须完全由 skill 文件承载」 | 【文档】`code-canvas/eval/README.md:9` |
| 为什么需要 validate 和自检循环 | 零上下文新会话能做到「接近手作质量」，「质量地板由 validate + 自检清单托底」 | 【文档】`code-canvas/DESIGN.md:136` |
| 为什么服务端再跑一遍工具 | 「不信任 agent 守规程」 | 【注释】`code-canvas/serve.py:609` |
| 为什么有"warn 不拦" | validate 区分"渲染器撑不住的"和"策展预算" | 【注释】`code-canvas/validate.py:4-6` |
| 为什么需要独立的新读者 | 作者读自己的东西会脑补 | 【推断】 |
| 为什么 judgment 要写理由 | 给作者复查 agent 的判断 | 【文档】`code-canvas/SKILL.md:38-39` |
| 为什么工具脚本用标准库、兼容 3.8 | hub 主机是 Python 3.8 | 【文档】`AGENTS.md:14` |

---

## 6. 读者最容易看错的地方（框内）

1. **"agent 按 SKILL 走 7 步"**。不对。`SKILL.md` 第 3 节的 5 步只是「推荐的循环（顺序和轮数由你决定）」（`code-canvas/SKILL.md:71`）。
2. **"8 个判断 = 8 个字段"**。对应 11 个键，且"对比"没有自己的键（§2.2）。
3. **"judgment 会显示在画布上 / 新读者会看到"**。都不会（§2.2、§2.6）。
4. **"check_refs 失败 agent 会收到非零退出码"**。不会，退出码永远 0（§2.5）。
5. **"reader_check 的结果会自动让 agent 返工"**。不会，要 agent 自己读（§2.6）。
6. **"不可商量 = 机器会拦"**。只有 validate 的 ERROR 会拦（§2.7）。
7. **"extract.py 的列就是最终的列"**。extract 的 BFS 和 SKILL 的经验规则相近，但 agent 可以按故事线重排；validate 只查有没有 col/band（§2.4）。
8. **"agent 在缓存里工作，产物也在缓存里"**。产物在工作目录（§1.2）。
9. **SKILL 里的命令是相对路径**（如 `python3 validate.py canvas.json`，`code-canvas/SKILL.md:118`），而 agent 的 cwd 是**仓库缓存**。`f4bbe29` 之前提示词里写的是 `{skill}/embed_context.py` 这样的绝对路径，之后的提示词没有了。agent 要从"SKILL.md 的位置"自己推断工具在哪。实际会不会出错，我没找到日志或测试能证明【推断，只看代码】。

---

## 7. 没查到为什么

- **为什么生成 agent 没有 `--max-turns` / 超时**。代码和提交里都没找到。
- **为什么 reader_check 默认用 sonnet，而生成默认 opus**。没找到。
- **为什么 SKILL 说"不可商量"的 `meta.judgment` 在 validate 里只是 warn**。没找到说明（只有 §2.7 里的推断）。
- **为什么行宽有三个数**（90 / 100 / 60）。没找到。
- **为什么 SKILL 第 6 节的"focus 卡 ≤4"没有机械检查**。没找到。
- **`DONE` 是给谁的**。没有代码读它；也许是留给人读日志。没找到说明。
- **SKILL v0.2 为什么从管线换成判断清单**。提交只有标题，见 §2.1，那里的动机是推断。
- **reader_check 为什么选"文本输出"而不是结构化输出**。没找到。
- **`--dangerously-skip-permissions` 的理由**：上一趟 §8 已记，此处不重复。

---

## 8. 可改进处【观点】

- 【观点】让 `reader_check` 的退出码和"读者自评理解度"联动，或者让提示词的完成标准里明写"至少一轮"，目前完全靠 agent 自觉。
- 【观点】`check_refs` 退出码永远 0，会让"全过"没有机器可读的信号。可以加一个 `--strict`，剔除时返回非零。
- 【观点】`judgment` 既不显示也不校验，agent 填多少都没有后果。要么在渲染器里展示（SKILL.md:38 说是"给读者的导读"），要么在文档里统一说成"只给作者"。
- 【观点】`reader_check` 的 `rmtree` 放进 `finally`，超时后给 agent 一份明确的"读者超时"报告而不是 traceback。
- 【观点】SKILL 命令里的相对路径在 cwd 是缓存时容易错，提示词可以把工具目录再写一次。
