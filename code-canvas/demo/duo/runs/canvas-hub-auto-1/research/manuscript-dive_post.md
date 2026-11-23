# 补充底稿：「服务端收尾」`post_pass` 里面是什么

> 研究对象：code-canvas 仓库，HEAD = `79153e7`。路径相对仓库根。「为什么」后的【…】是来源等级；标【设】的值是我补设的，其余数字都能从代码或样板算出；「」里一律是逐字原文（非原文我用“”）。
> 承接上一趟 §2.5 / §3 第 6 步 / §5 第 2–3 条；退出码表、“服务端不信任 agent”的提交出处、`.src.json` 入库时才写，上一趟已讲。

---

## 0. 先说结论

这个框是**一条 shell 命令串**，不是一个 Python 进程。`serve.py` 起任务时把它拼成字符串嵌进任务脚本（`code-canvas/serve.py:633-637`）。顺序跑四个零件：

| 顺序 | 零件 | 干什么 | 能拦下画布吗 |
|---|---|---|---|
| ① | `embed_context.py` | 卡里的代码对到源文件，把**源文件全文**写进 `files`；行号不对就改行号 | **不能**（退出码被忽略） |
| ② | `check_refs.py` | 核每条“依据”，假的就地删，删光的 fact 步卡降为 infer | **不能**（剔除也返回 0，出错的 1 也被忽略） |
| ③ | `validate.py` | 结构检查 + 沙盘真跑 | **能**：有 ERROR 或脚本崩了 → `rc=4` |
| ④ | `cp` + `render.py` + 写来源记录 | 入库 | 失败 → `rc=6` |

三句话：
1. **只有 validate 能拦。** embed 和 check_refs 是“修补工”，修不了也不拦，失败只进任务日志。
2. **embed 和 check_refs 在工作目录那份 `canvas.json` 上原地改写**，validate 只读，通过才 `cp` 进画布库。被拦下的画布，库里**什么都没动**，工作目录那份却已被改过。
3. **validate 从不读仓库。** “卡里的代码是不是源码原文”只有 embed 在比，结论只是日志里一行“跳过”，没人读它。

最易看错：以为 embed 失败无所谓（其实会让 validate 更严，§2.5）；以为 embed 和 check_refs 用同一个仓库根（§3 变体 B）；以为沙盘在沙箱里跑（§2.4）。

---

## 1. 框里有哪几样东西，状态放在哪

**没有独立进程 / 线程。** 同一个 `bash -c` 顺序拉起 4 个短命 `python3` 子进程（`code-canvas/serve.py:605` 的 `tool` 拼出 `python3 <脚本>`），**没有并发**。

| 状态 | 在哪 | 谁写 → 谁读 |
|---|---|---|
| 工作目录 `canvas.json`（记 `w`） | `code-canvas/serve.py:598` | agent 写；embed、check_refs 原地改写；三个零件读；`cp` 拷走 |
| 仓库根（记 `R`） | git 源 = 共享缓存（`code-canvas/serve.py:735`）；路径源 = `--repo`（`code-canvas/serve.py:749`） | 只读，给 embed、check_refs |
| 画布库 `<name>.json/.html`（`hj`/`hh`） | `code-canvas/serve.py:599` | `cp`、`render.py` 写 → 读者浏览器读 |
| 来源记录 `<name>.src.json` | `code-canvas/serve.py:629-632` | 入库最后一步写 |
| shell 变量 `rc` | 任务脚本 | 先存 claude 退出码（`code-canvas/serve.py:652`），收尾可改成 4/5/6，最后把 `rc` 写进 `.status`（`code-canvas/serve.py:654`） |
| 任务日志 `.log` | `>> {lg} 2>&1`（`code-canvas/serve.py:627`） | 三个零件的输出都进这里 |

**零件间唯一的数据交接**：embed 往 `w` 写顶层 `files`（`{卡里写的路径: 源文件全文}`，`code-canvas/embed_context.py:88`），validate 和浏览器读它。

**整串守卫**（`code-canvas/serve.py:633-635`）：claude 退出码为 0 才进来；`w` 不存在 → `rc=5`；粘贴代码源没有仓库，embed 和 refs 两段是空串（`code-canvas/serve.py:621`、`code-canvas/serve.py:628`），只剩 validate + 入库。退出码表注释：「服务端入库（不信任 agent 守规程）」「4 validate 未过」「6 渲染失败」（`code-canvas/serve.py:609-610`）。

---

## 2. 顺着读：每个零件冒出的问题

### 2.1 embed_context：卡里的代码怎么对到源文件

对每张有 `file: "路径:起始行"` 和 `code` 的卡：拿 `code` 和仓库文件从起始行起的一段比；比得上，**整个文件**存进 `files`；比不上，找新行号。

**比较规则 `norm`**：删掉所有空白、`//`、`#`、`\`，看“卡代码是不是文件那段的前缀”，窗口取 `n + 40` 行（`code-canvas/embed_context.py:22-23`、`code-canvas/embed_context.py:69-70`）。
- 为什么忽略 `//` `#` `\`：SKILL 允许超宽行“保持 token 顺序的换行重排”（`code-canvas/SKILL.md:136`），重排会插续行符；评分器同款函数注释：「两侧等量删除，比较意义不变。」（`code-canvas/eval/run.py:41`），embed 文件头说它「与评分器同款」（`code-canvas/embed_context.py:6`）。【注释】ROADMAP 说它是真实案例逼出来的：「评分器容忍面已补：`#`/`\` 续行（sglang 审计与考试各暴露一处）」（`ROADMAP.md:52`）。【文档】
- `n + 40` 里的 40：没有出处（§5）。

**行漂移修正**（`code-canvas/embed_context.py:72-81`）：起始行对不上，就在全文里找“首行规范化后相同、且整段 match”的行，改写 `c["file"]`（`code-canvas/embed_context.py:79`），日志打 `行漂移修正: preempt 72→75`（`code-canvas/embed_context.py:92-93`）。
- 文件头：「行漂移修正：对不上时在全文里搜索唯一匹配点，机械修正 file 行号」，场景是「（仓库比画布新时的常见情形）」（`code-canvas/embed_context.py:7-8`）。【注释】
- **注释与代码有出入**：注释说“唯一”，代码 `next(...)` 取**第一个**命中（`code-canvas/embed_context.py:74-75`），不查有没有第二个，也不从原行号附近找而是从第 1 行扫；文件里有两段相同代码时会改到前一段。测试只测了唯一匹配（`code-canvas/tests/embed_test.py:33-41`）。【推断】
- hub 里漂移多半不是“仓库变了”：缓存只在无同仓任务时刷新（`code-canvas/serve.py:722-728`），我认为更常见的是 agent 报的起始行本身有偏差。【推断，无频率数据】

**对不上就“跳过”**：这张卡不贡献 `files`，`file` 不动（`code-canvas/embed_context.py:76-78`）；文件有任一张卡对上就整个嵌入（`code-canvas/embed_context.py:82`）。

**一张都没嵌成** → 打印「没有任何卡可嵌入」并 `exit 2`，且**不写文件**（`code-canvas/embed_context.py:85-87`）。服务端用 `;` 连接，这个 2 被吞（`code-canvas/serve.py:627-628`）。为什么被吞：提交 `690022d`「预览图等无代码卡时自动跳过不碰状态码」——预览地图本来就没代码卡。【提交】有代码卡但全对不上也一并被吞，是同一写法的副作用。【推断】

**卡路径相对子目录，怎么猜仓库根**（`code-canvas/embed_context.py:35-51`）：算每个候选根“命中多少条卡路径”；**只有根没全中**才扫一级子目录（跳过 `.` 开头，按名字排序），严格更高才换，平手先到先得；换了就打印“仓库根自动定位到子目录”。
- 为什么有：`690022d`「仓库根自动定位（monorepo 子目录如 codex-rs，」「按命中最多卡路径挑根）」；ROADMAP：「考生违规但可用子目录根兜住」（`ROADMAP.md:144`）。SKILL 要求路径相对仓库根（`code-canvas/SKILL.md:139`），agent 没守，系统兜底。【提交】【文档】
- **只有 embed 猜根，check_refs 不猜**（直接用传入的 `R`，`code-canvas/check_refs.py:43`、`code-canvas/check_refs.py:22`）。最容易让人意外的不对称，见 §3 变体 B。
- `files` 的键是卡里写的路径（`code-canvas/embed_context.py:82`），validate 的缺上下文检查用同样的路径比（`code-canvas/validate.py:134`）。

### 2.2 check_refs：一条依据怎么判真假

依据 `[文件, 行号, 符号]` 出现在 `steps[].trace`、`gaps`、`objects`、`boxes` 的 `refs`（`code-canvas/check_refs.py:49-56`）。`ref_ok` 依次问（`code-canvas/check_refs.py:18-36`）：格式 → 路径有没有越出仓库 → 文件在不在 → 行号越没越界 → 有符号的话，符号这个**子串**在 `[行-3, 行+3]` 窗口里（`code-canvas/check_refs.py:33-34`）。

**判不过**：从 `refs` 剔除并报原因（`code-canvas/check_refs.py:72`）。**只有步卡 trace** 会降级：`ev` 缺省或 fact，且剩余依据为空 → `infer`（`code-canvas/check_refs.py:73-74`）。

**为什么降级而不是拒收**：
- 文件头：「读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。」（`code-canvas/check_refs.py:8`）；「exit 0（剔除也算正常完成）；1 参数/格式错。」（`code-canvas/check_refs.py:11`）。【注释】
- `4b43d42`：「须在仓库里成立，核不过剔除，依据剔光」，下一行接「的 fact 步卡降级 infer」。【提交】
- 为什么是降成 infer 而不是整步拒收：证据分级本有三档（「证据分级：fact（缺省）/ infer 有依据的推断 / unknown 尚未确认（必须写 need）」，`code-canvas/validate.py:378`）；依据全假说明“没证据”，不说明结论错。这是我从分级设计推的，提交没单独讨论。【推断】
- 降级有测试：`code-canvas/tests/evidence.mjs:80-86`。【测试】

**这个检查有多强（观察，别高估）**：
- 符号是子串匹配，`allocate` 会被 `deallocate` 命中；只证明“附近出现这个词”，不证明“支撑了结论”。
- **没有 `refs` 的 fact 步卡不会降级**（`code-canvas/check_refs.py:61-62`）。
- `lstrip("./")` 去的是字符集合不是前缀（`code-canvas/check_refs.py:22`）：`.github/x.yml` 会变成 `github/x.yml`（我对字符串验证过），点目录里的依据会被误判不存在。【推断：无测试覆盖】

### 2.3 validate：ERROR 和 warn

契约：「Errors are structural breakages the renderer cannot survive gracefully」「Warnings are curation-budget violations from schema.md's hard limits.」（`code-canvas/validate.py:4`、`code-canvas/validate.py:6`）。【注释】收尾只看退出码：有 ERROR → 1，warn 不影响（除非 `--strict`，`code-canvas/validate.py:662`；服务端没传，`code-canvas/serve.py:636`）。

**ERROR 的主要类别**（完整清单见 validate.py 里全部 `err(` 调用）：画布空 / id 撞（`code-canvas/validate.py:122`）；引用悬空——线端、note、步 focus 指向不存在的卡（`code-canvas/validate.py:258`）；行号越界 / 块重叠（`code-canvas/validate.py:262`）；缺必填——`layout.col/band`、overview 的 `problem/idea/example/flow/vars`（`code-canvas/validate.py:200`）；步卡结构（`code-canvas/validate.py:503`）；**编造**——overview 变量、黑盒函数、推演变量在“卡片代码 ∪ files 全文”里找不到（`code-canvas/validate.py:366`、`code-canvas/validate.py:582`）；**沙盘**——缺 expected、超时 / 报错 / 输出不符（`code-canvas/validate.py:87`、`code-canvas/validate.py:102`）。

**warn 只是提醒**：字数预算、缺层（`meta.judgment`、`overview`、`gaps`、`boxes`、`scenarios`，如 `code-canvas/validate.py:309`、`code-canvas/validate.py:318`）、某列超 3 张卡（`code-canvas/validate.py:242`）、有 file:line 却无 `files`（`code-canvas/validate.py:135-138`）。所以上一趟 §3 里那份旧格式样板能过：缺层只是 warn。

**还有一种拦下不是 ERROR**：字段类型意外时 validate 直接抛异常（如对没有 `code` 的 state 卡取 `cards[cid]["code"]`，`code-canvas/validate.py:260`），退出码 1，同样 `rc=4`，日志里是 traceback。没构造用例跑过。【推断】`rc=4` 的原因要去 `.log` 找（`code-canvas/serve.py:634`）。

**validate 读不读仓库？不读。** `main` 只打开 `args[0]` 那个 JSON（`code-canvas/validate.py:116-118`）。后果：一张卡的 `code` 被整段编造，embed 记一行“跳过”，validate 不知道，**画布照样入库**；只有某文件**没有任何一张**卡对上时才有 warn（`code-canvas/validate.py:135-138`）。SKILL 把“卡内代码是原文”列为要求（`code-canvas/SKILL.md:136`），但没有机械闸门；缺口来自分工（embed 当初是补上下文，不是执法）。【推断】

### 2.4 “沙盘真跑”：hub 机器上实际运行了什么

**是什么**：块沙盘（`block.run`）是 agent 蒸馏的自包含 Python 脚本 `harness`（留一个 `__INPUT__` 占位）+ 1–5 组输入，每组带预录输出 `expected`，静态页上读者看到的就是它。

**validate 怎么核**（`code-canvas/validate.py:64-102`）：对每组输入，`harness.replace("__INPUT__", x["value"])`（`code-canvas/validate.py:92`）→ `subprocess.run([sys.executable, "-I", "-c", code], …, timeout=8)`（`code-canvas/validate.py:94-95`）→ 超时 / 退出码非 0 / `r.stdout.strip() != x["expected"].strip()` 各是一种 ERROR（`code-canvas/validate.py:96-102`）。

**所以 hub 上实际发生的是**：以 hub 用户的权限，顺序起若干个 `python -I -c <agent 写的脚本>`，每个最多 8 秒。服务端没带 `--no-exec`（`code-canvas/serve.py:636`），`NO_EXEC` 默认假（`code-canvas/validate.py:61`）。上一趟只点了“会真跑”，这里补两点：
- `-I` 只是 Python 隔离模式（不读 `PYTHON*` 环境变量、不把当前目录放进 `sys.path`），**不是文件系统 / 网络沙箱**（`code-canvas/validate.py:94-95` 无别的限制）。
- 这次 `subprocess.run` **没设 `cwd`**，继承任务脚本的目录：git 源是共享缓存（`code-canvas/serve.py:740` 的 `cd {d}`），路径源是 `--repo`（`code-canvas/serve.py:751`）。对比读者点“试”走的 `/run`：显式 `cwd="/tmp"`（`code-canvas/serve.py:1212`）。观察，没找到为什么（§5）。

**例子（样板真实数字）**：`demo/nano-vllm.json` 有两个沙盘——卡 `schedule` 的块“Prefill 批次”（4 组输入）、卡 `allocate` 的块“认领命中块”（3 组）。`lang` 缺省为 `"py"`（`code-canvas/validate.py:68`），所以收尾**共真跑 7 次**，最坏 7 × 8 秒。`__INPUT__` 会被换成 `tokens = [1, 2, 3, 4, 5, 6, 7, 8]` 这样的赋值语句。

**为什么真跑**：`1c9fabe`：「validate：结构规则 + 逐输入真跑对照 expected——不符即 ERROR，」「掐死编造输出（--no-exec 逃生口）」【提交】；注释「块沙盘：结构 + 真跑核验（expected 必须是实际运行的产物，不许想象）。」（`code-canvas/validate.py:65`）【注释】；SKILL 常见错误里列「沙盘的 expected 是手写的」（`code-canvas/SKILL.md:203`）【文档】；8 秒：「沙盘必须小而确定」（`code-canvas/validate.py:97`）【注释】。

**`--no-exec`**：「跳过沙盘真跑核验（无解释器的环境用）」（`code-canvas/validate.py:115`）；问答补丁路径用它（`code-canvas/serve.py:135`、`code-canvas/serve.py:138`），**生成收尾不用**，为什么分开没查到（§5）。

### 2.5 为什么 embed 排在 validate 前面

**没有注释或提交直说“必须先 embed”。** 但 validate 有三处明文依赖 `files`：
1. 「上下文全文：有 file:line 的代码卡应有 files 映射兜底（embed_context.py）」（`code-canvas/validate.py:128`）；`40781a8`：「validate：file:line 代码卡无 files 映射 → 警告」。【注释】【提交】
2. 变量核对：「卡片没截到的行里的变量也算真」（`code-canvas/validate.py:356`）；`79cbff8`：「corpus 含 files 全文；剔除不存在的变量并记录」。【注释】【提交】
3. 「只对"卡片代码 ∪ 引用文件全文"核对名字，不许编造」（`code-canvas/validate.py:563`），`corpus` 由卡代码 + `files` 值拼成（`code-canvas/validate.py:564-565`）。【注释】

所以“必须先于”是我从这三处推的；依赖本身是明文的。【推断】

**一个间接的“拦”**：embed 自己不拦，但它失败会让 validate 的语料变小。样板 `files` 里，`llm_engine.py` 的第 58、73 行调用了 `is_finished(`，但**没有一张卡的代码里调用它**（卡 `step` 覆盖 `llm_engine.py` 第 49 行起的 7 行，其中第 54 行只是属性 `seq.is_finished`，无括号，我机械查过）。设 agent 在 `boxes` 里写了黑盒 `is_finished`【设】：有 `files` 时通过；若 embed 一张都没嵌成（exit 2 被吞），语料只剩卡代码 → `ERROR boxes[0]: is_finished( 在卡片代码与引用文件里都没被调用——不许编造`（格式 `code-canvas/validate.py:582`）→ `rc=4`。

**其它顺序**：check_refs 和 validate 互不依赖（validate 只验 refs 的形状，`code-canvas/validate.py:396-404`），先后无说明【推断】；`cp`/`render` 在 validate 后就是“全过才入库”：`4b43d42`「全过才入库（done ⇔ 已入库；rc 4/5/6 如实失败；来源记录入库时才写）」。【提交】

### 2.6 入库、render，以及 render 失败时库里留什么

入库那一句（`code-canvas/serve.py:634`）：`cp {w} {hj} && {render} {hj} {hh} >> {lg} 2>&1 && {{ {sidecar}:; }} || rc=6`。
- `render.py` 读的是**库里刚拷进去的 json**。它只把模板里唯一的占位符 `/*CANVAS_DATA*/null` 换成 JSON（`code-canvas/render.py:10`、`code-canvas/render.py:24-25`；模板里只出现一次，`code-canvas/template/canvas.html:740`），并把 `</` 换成 `<\/`：「</script> inside string data must not terminate the script tag」（`code-canvas/render.py:23`）。【注释】
- sidecar 末尾的 `:;` 让它永远算成功，所以 `rc=6` 只可能来自 `cp` 或 `render`。来源记录排在 render 后，失败任务不留记录：`4b43d42`「来源记录入库时才写」；测试：被拦的任务既无 json 也无 sidecar（`code-canvas/tests/hub.mjs:630-632`）。【提交】【测试】

**render 失败时库里（按代码推）**：首次生成 → 有 `<name>.json`，**无** `.html`、无 sidecar，`.status=6`；同名画布已存在 → `.json` 已是新的，`.html` 还是**旧的**（新旧不一致）；`cp` 自己失败 → 库里没变（`&&` 短路）。首页只列 `*.html`（`code-canvas/serve.py:282`），半成品不出现在列表，也不会被清理。没找到 `rc=6` 的测试。

---

## 3. 贯穿例子：nano-vllm +“讲讲调度器怎么工作”走过收尾

> 承接上一趟 §3：job `j20261123-101530-482`，w 指 /home/ubuntu/hub/.jobs/j20261123-101530-482/canvas.json，R 指 /home/ubuntu/hub/.repos/nano-vllm-155c1586，库路径 `/home/ubuntu/hub/nano-vllm.{json,html}`。agent 交付物仍用 `demo/nano-vllm.json` 当替身（9 张代码卡 + 1 张 `kv-pool` 状态卡）。
> **与 dive_agent 底稿的出入**：它 §3 第 8 步【设】写“嵌入 4 文件 / 9 卡”；样板真实是 **5 文件 / 9 卡**，与上一趟 §3 一致，请统一按 5。

**主线（agent 已自查过，收尾是再来一遍）**
1. `rc=0`，`w` 存在。
2. embed → 日志 `嵌入 5 文件 / 9 卡，共 23 KB`（格式 `code-canvas/embed_context.py:90-91`；5 个文件合计 23.44K 字符，我从样板 `files` 算的）。agent 自己跑过一次，行号都已对上，无“行漂移修正”行。
3. check_refs → `依据核对：6 条，通过 6，剔除 0`【设：6 条沿用 dive_agent §3 第 7 步，agent 已修过】。
4. validate → 7 次沙盘真跑，0 个 ERROR、若干 warn（数量不详）。
5. `cp` → `nano-vllm.json`；render → `nano-vllm.html`；写 `nano-vllm.src.json`；`.status` 写 `0`。

**变体 A：行号数错**【设】卡 `preempt` 的 `file` 写成 engine/scheduler.py 的第 72 行（真实 75：样板里 `scheduler.py` 第 75 行就是 `def preempt(self, seq: Sequence):`，我用等价函数核过）。`match(72)` 假；首行规范化为 `defpreempt(self,seq:Sequence):`，全文只有第 75 行同时满足首行相同和整段匹配 → `file` 改成 engine/scheduler.py 的第 75 行，日志 `行漂移修正: preempt 72→75`。

**变体 B：卡路径相对子目录**【设】设真实仓库代码在 `nanovllm/` 包下（**我的设想，离线无法核实**；样板的卡路径是 `engine/…`、`layers/sampler.py`）。
- embed：`score(R)=0 < 5` → 扫子目录，`nanovllm` 得 5 → 日志“仓库根自动定位到子目录: nanovllm”，照常嵌入。
- check_refs：用 `R`，`R/engine/scheduler.py` 不存在 → 每条依据“文件不存在”→ 全剔，fact 步卡全降级 infer（日志形如 `依据核对：6 条，通过 0，剔除 6`）。
- 结果：**上下文完好，依据全灭**。

**变体 C：沙盘 expected 被手改**【设】卡 `allocate` 沙盘第 1 组“完全命中”真实第一行输出是 `命中前缀块数: 2 → 免算 token 数: 8`，设被改成 `3`。validate：`ERROR  卡 allocate › 认领命中块 run.inputs[0]: expected 与实际输出不符——预录输出必须是真跑的产物`（格式 `code-canvas/validate.py:26`、`code-canvas/validate.py:78`、`code-canvas/validate.py:102`）→ `rc=4`。首页 `failed(4)`，库里没有新的 json / html / sidecar（同名重生成则**旧画布原样保留**），原因只在 `.log`。

**变体 D：依据全假（agent 没自查）**【设】步 ④（样板 `steps[4]`）的 trace 依据是 engine/scheduler.py 的 94 行与 88 行两条，符号都是 preempt。样板 `scheduler.py` 全文 93 行；`preempt` 出现在第 62、64、75 行，第 88 行的窗口 85–91 里没有。日志（格式 `code-canvas/check_refs.py:68`、`code-canvas/check_refs.py:79`、`code-canvas/check_refs.py:81`）：
```
剔除 steps[4].trace ["engine/scheduler.py", 94, "preempt"] — 行号越界（全文 93 行）
剔除 steps[4].trace ["engine/scheduler.py", 88, "preempt"] — 符号「preempt」不在该行 ±3 行内
降级 steps[4].trace ：依据全部核不过，ev → infer
```
画布**照常入库**，只是这一步成了 infer。

---

## 4. 值得做成源码页的代码段（6 段）

（“行 N”指该页范围内的文件行号。）

**页 1：`code-canvas/serve.py:627-637`（post_pass 默认分支，11 行）** —— 整个框的骨架。
- 行 627-628 embed，`if repo_dir else ""`；命令后是 **`;`** 不是 `&&`，退出码被吞——读者最易误以为“embed 失败会停”。【提交】690022d
- 行 629-632 sidecar；行 633 三层守卫 `rc==0` → `-f w` → validate；行 634 关键行 `if {val} {w} >> {lg} 2>&1; then cp {w} {hj}`，其后 `else rc=4; fi; else rc=5; fi; fi;`（`|| rc=6` 管整条 `&&` 链）；行 635 `else rc=5`。上方 `code-canvas/serve.py:609-610` 是退出码表，`code-canvas/serve.py:620-621` 是 check_refs（同样 `;`）。

**页 2：`code-canvas/embed_context.py:35-51`（猜仓库根，17 行）**：行 43 `if bs < len(paths)` 才找子目录；行 44-48 只一级、跳 `.`、严格大于才换；行 49-51 换根只影响读文件。

**页 3：`code-canvas/embed_context.py:53-88`（逐卡核对 + 行漂移 + 写回，约 35 行）**：行 56-57 无 code / file 的卡静默略过；行 69-70 `n+40` 窗口 + `startswith`；行 72-81 漂移修正（第一个命中就用，与注释“唯一”不符）；行 82 对上才嵌整个文件；行 85-87 `exit 2` 在写文件之前。

**页 4：`code-canvas/check_refs.py:18-36` 与 `code-canvas/check_refs.py:59-75`（约 33 行）**：行 22-26 越界防护；行 33-34 符号窗口（`lines[max(0, ref[1] - 4):ref[1] + 3]`，下标从 0，所以前 3 后 3）；行 61-62 无 refs 直接跳过；行 73-74 降级条件。

**页 5：`code-canvas/validate.py:64-102`（沙盘真跑，约 39 行）**：行 69-76 结构；行 89-90 非 Python 或 `NO_EXEC` 只验结构；行 92 value 直接替换进脚本（输入值就是代码）；行 94-95 `-I`、`timeout=8`、无 `cwd`；行 96-102 三种 ERROR，比较前都 `.strip()`。

**页 6：`code-canvas/validate.py:563-593`（语料 = 卡代码 ∪ files 全文，31 行）** —— embed → validate 唯一的数据耦合点：行 564-565 语料拼法；行 581-582 找 `name(`，找不到 ERROR；行 590-592 已展开的不算黑盒。搭配 `code-canvas/validate.py:128-138`（缺 files 的 warn）读。

---

## 5. 没查到为什么

- 生成收尾为什么真跑沙盘（不带 `--no-exec`），而问答补丁带了（`code-canvas/serve.py:138`）。`1c9fabe` 只说“--no-exec 逃生口”。
- validate 里沙盘为什么不设 `cwd`，而 `/run` 设了 `/tmp`（`code-canvas/serve.py:1212`）。
- `n + 40`（`code-canvas/embed_context.py:70`）为什么是 40。
- 符号窗口为什么是 ±3（`code-canvas/check_refs.py:33`）：文件头只写了做法，没写原因。
- 服务端 validate 为什么不传 `--strict`：`f4bbe29` 说明只有标题；dive_agent 底稿也记了同一个未解之处。
- embed “注释说唯一、代码取第一个”是有意还是疏漏：无提交说明。
- check_refs 为什么不跟 embed 一样猜子目录根：两者是不同提交（`4b43d42` 与 `690022d`）加的，没看到合并考虑的文字。

## 6. 可改进处【观点】

- 【观点】把 embed 的 skip 数、check_refs 的剔除数写进任务 meta；现在只在 `.log` 里，首页 `done` 看不出。
- 【观点】check_refs 复用 embed 的根定位，避免变体 B“上下文好、依据灭”。
- 【观点】embed 行漂移多处命中时拒绝（与注释“唯一”一致），或选离原行号最近的。
- 【观点】沙盘真跑给空临时目录作 `cwd` 并加资源限制；现在它能读写“约定只读”的共享缓存。
- 【观点】render 失败后清掉刚 `cp` 的 json，或先渲染到临时文件再原子替换。
