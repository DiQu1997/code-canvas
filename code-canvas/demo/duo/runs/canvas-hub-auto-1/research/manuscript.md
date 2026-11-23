# 底稿：给一个 repo 链接和一个问题，画布是怎么一步步生成的

> 研究对象：code-canvas 仓库，HEAD = `79153e7`。路径都相对仓库根。
> 锚点写成 `文件:行号`；"为什么"后面的【…】是来源等级。

---

## 0. 先说结论

**一句话**：网页表单把 `{git_url, ask}` POST 给常驻的 Python 服务 `serve.py --hub`；服务给这一单拼好一段 **bash 脚本**和一段**提示词**，起一个脱离自己的后台进程。脚本依次做三件事：

1. 准备仓库（多单共用一份浅克隆）；
2. 跑一个**无人值守的 `claude -p` agent**。agent 读 `SKILL.md`，自己研究代码，在任务的工作目录里写出 **`canvas.json`**（只有数据，没有画面）；
3. **服务端机械收尾**：嵌源码全文 → 核依据 → 过 validate → 拷进画布库 → 把 JSON 注入 HTML 模板 → 写来源记录 → 写状态码。

读者打开画布时，**真正的排版发生在浏览器里**：模板里的 JS 量出代码行宽，再排卡片、画连线、算镜头。

**最容易误会的三点**（后文分别展开）：
- 画布不是 agent "画"出来的。agent 只交一份 JSON，而且只给粗位置 `{col, band}`；像素级布局是浏览器里的 JS 做的。
- 有两层校验：agent 在自己的循环里会跑 validate / check_refs / embed；服务端收尾时**不信任 agent**，再跑一遍，全过才入库。
- 服务进程和生成任务之间**没有队列，也没有回调**。交接全靠文件：`.jobs/<id>.*`、`.repos/*.ready`、画布库里的 `<name>.json/.html/.src.json`。

### 主路是哪条，为什么选它

仓库里能"从代码生成画布"的入口不止一个。本稿的**主路**是：

> hub 首页表单 → 默认选项：**git 地址 + 深潜画布 + Claude 引擎** → `POST /generate` → `spawn_generate` → `claude -p` → 服务端收尾入库 → 读者 `GET /c/<name>/`。

理由：
- 用户的问题是"给一个 repo **link** + query"。只有 hub 的 `/generate` 接受 `git_url`（`code-canvas/serve.py:25-29`）。较新的 duo 两人组调度 `run.py` 只接受本地 `--repo <仓库路径>`（`code-canvas/demo/duo/run.py:8-9`），而且 ROADMAP 明说它"没接进 hub"（`ROADMAP.md:507`）。
- 表单默认勾选的就是 git 地址、深潜画布、Claude（`code-canvas/serve.py:830`、`code-canvas/serve.py:837`、`code-canvas/serve.py:842`）。

其它模式（预览地图、动画缩放图、升级、codex 引擎、盒子路径 / 粘贴代码、duo）放在第 6 节，只点名。

---

## 1. 全貌：参与者和它们之间的交接

### 1.1 参与者（9 个）

| # | 参与者 | 是什么（进程 / 线程 / 文件） | 拿到什么 → 交出什么 |
|---|---|---|---|
| ① | **hub 首页**（浏览器里的表单 + 轮询脚本） | `serve.py` 里内嵌的 HTML/JS 字符串 `GEN_FORM`（`code-canvas/serve.py:827-959`） | 用户输入 → `POST /generate` 的 JSON；之后每 5 秒拉一次 `/jobs` |
| ② | **hub 服务** `serve.py --hub` | 一个常驻 Python 进程，`ThreadingHTTPServer`，每个请求一个线程（`code-canvas/serve.py:1293`） | 请求 → 校验、起任务、立即返回 job meta |
| ③ | **任务发射器** `spawn_generate` | ② 里的一个函数，持 `SPAWN_LOCK` 执行（`code-canvas/serve.py:1160-1161`） | src/ask/name → 写 prompt 文件 + meta 文件，`Popen(["bash","-c",script])`（`code-canvas/serve.py:762-763`） |
| ④ | **任务脚本**（bash 进程） | 独立会话的后台 bash（`start_new_session=True`，`code-canvas/serve.py:763`） | 串起：准备仓库 → 跑 agent → 服务端收尾 → 写 `.status` |
| ⑤ | **共享仓库缓存** | 目录 `<repos_dir>/<尾名>-<url md5 前 8 位>` + 标记文件 `.ready`（`code-canvas/serve.py:352-355`） | git_url → 一份浅克隆，同一 URL 的多单共用 |
| ⑥ | **生成 agent** `claude -p` | ④ 起的子进程，cwd 是仓库缓存，`--dangerously-skip-permissions`，输出 stream-json（`code-canvas/serve.py:648-651`） | prompt（让它读 SKILL.md）→ 工作目录里的 `canvas.json` |
| ⑦ | **工具链**（agent 和服务端共用的 Python 脚本） | `extract.py` / `validate.py` / `check_refs.py` / `embed_context.py` / `render.py` / `reader_check.py` | 读写 canvas.json；render 产出 HTML |
| ⑧ | **画布库**（文件） | `<hub>/<name>.json`、`<name>.html`、`<name>.src.json` | 收尾入库的终点；首页列表和 `/c/<name>/` 的数据源 |
| ⑨ | **渲染器模板** `template/canvas.html` | 一个约 2900 行的单文件 HTML+JS；`render.py` 把 JSON 塞进占位符 `/*CANVAS_DATA*/null`（`code-canvas/template/canvas.html:740`） | 在读者浏览器里：量宽 → 布局 → 连线 → 按步走镜头；还能连回 hub 问答 |

另外有两类"看不见的存储单元"，读者最好知道：

- **任务账本** `<hub>/.jobs/`：每单 6 个文件 + 1 个工作目录，`<id>.prompt / .log / .status / .meta.json / .result.json / .timing.json` 和 `<id>/`（`code-canvas/serve.py:594-604`）。服务端判断"这单在不在跑"、监视器显示"走到哪一阶段"，全靠读这些文件。
- **核心数据结构 canvas JSON**：顶层主要有 `meta / regions / cards / wires / notes / steps / files`，新规程还有 `overview / scenarios / boxes / gaps / objects` 和 `meta.judgment`。格式见 `code-canvas/schema.md`。

### 1.2 交接（画线用）

| 从 → 到 | 交接方式 | 谁等谁 |
|---|---|---|
| ① → ② | HTTP `POST /generate`，JSON body（`code-canvas/serve.py:888-889`） | 浏览器等 HTTP 响应。响应**立即**返回（只含 job meta），不等生成 |
| ② → ③ | 同一线程里的函数调用，包在 `with SPAWN_LOCK` 里（`code-canvas/serve.py:1160-1161`） | 同步 |
| ③ → ④ | `subprocess.Popen(["bash", "-c", script], cwd=cwd, start_new_session=True)`（`code-canvas/serve.py:763`），prompt 走文件 `<id>.prompt`（`code-canvas/serve.py:762`） | **谁也不等**：Popen 之后服务端不 wait，只把 pid 写进 meta（`code-canvas/serve.py:764-770`） |
| ④ ↔ ⑤ | 文件系统：`git clone/fetch` 写缓存，`touch .ready` 当"就绪"信号（`code-canvas/serve.py:722-734`） | 同仓库的后到者轮询 `.ready`，每 2 秒一次、最多 900 次（`code-canvas/serve.py:731`） |
| ④ → ⑥ | 子进程，`{cli} -p{m} \"$(cat {pf})\"`（`{pf}` 即 `<id>.prompt`），stdout 重定向到 `<id>.result.json`（`code-canvas/serve.py:648-652`） | bash 同步等 claude 退出，拿 `rc=$?` |
| ⑥ → ⑦ | agent 用 Bash 工具自己跑 `python3 validate.py` 等 | agent 等工具 |
| ⑥ → ④（产物） | 文件：`<id>/canvas.json`（`code-canvas/serve.py:598`） | bash 在 claude 退出后看文件在不在 |
| ④ → ⑦ → ⑧ | `post_pass` 拼出的一串 shell：embed → check_refs → validate → `cp` → render → 写 sidecar（`code-canvas/serve.py:627-637`） | 顺序执行 |
| ④ → ② | 文件 `<id>.status`，内容是退出码（`code-canvas/serve.py:654`） | ② 每次被问 `/jobs` 时现读（`code-canvas/serve.py:446-453`） |
| ① ↔ ② | 浏览器轮询：有任务在跑时每 5 秒 `GET /jobs`（`code-canvas/serve.py:953`）；点开任务行拉 `/jobs/<id>/monitor` | 拉取，无推送 |
| 读者浏览器 → ② → ⑧ → ⑨ | `GET /c/<name>/`：先 `maybe_rerender`，再把 HTML 原样返回（`code-canvas/serve.py:1042-1044`） | 同步 |

> **读者提示**：③→④ 这条线是"断开"的。之后所有回流（状态、进度、产物）都是"写文件 → 下次被问时读文件"。读者如果期待找到队列或回调，会找不到。

---

## 2. 逐段走一遍

### 2.1 表单 → `POST /generate`：服务端先把关什么

用户在首页填 git 地址和"主题 / 关注点"，点「生成画布」。前端拼 body：`{ask, name?, git_url}`；选了别的类型 / 引擎才加 `preview / zoom / engine / model`（`code-canvas/serve.py:874-884`）。`ask` 为空，前端直接拦下（`code-canvas/serve.py:885`）。

服务端 `do_POST` 的 `/generate` 分支（`code-canvas/serve.py:1112-1162`）：
- `ask` 必填，后端再查一遍（`code-canvas/serve.py:1118-1119`）。
- `git_url` 只收 `http(s)://`（`code-canvas/serve.py:1123-1124`）。
- 画布名默认取 URL 最后一段、去掉 `.git`（`code-canvas/serve.py:1126`），只允许 `[A-Za-z0-9._-]`（`code-canvas/serve.py:1152-1153`）。
- 引擎只收 claude / codex，模型 id 也有白名单（`code-canvas/serve.py:1154-1159`）。
- 然后在 `SPAWN_LOCK` 里调 `spawn_generate`，把返回的 job meta 原样回给浏览器（`code-canvas/serve.py:1160-1162`）。

**为什么 ask 是必填**：前端的报错原话是「主题/关注点是必填的——这张图为谁、讲什么」（`code-canvas/serve.py:885`）【注释】。SKILL 第 1 节说，画布好不好，是按"这位读者能做到什么"来判断的（`code-canvas/SKILL.md:18-21`）【文档】。没有 ask，就没有判断标准。

**为什么名字只许这几个字符**：名字会直接拼进文件路径 `<hub>/<name>.html`（`code-canvas/serve.py:599`），也会拼进 URL `/c/<name>/`（`code-canvas/serve.py:1013-1014`）。测试里专门有"路径穿越的名字返回 404"一项（`code-canvas/tests/hub.mjs:473`）【测试】。

### 2.2 `spawn_generate`：拼两样东西，然后放手

入口 `code-canvas/serve.py:584`。它做四件事。

**(a) 起 job id 和任务账本**。id 是 `jYYYYMMDD-HHMMSS-毫秒后三位`（`code-canvas/serve.py:592`）。账本文件和工作目录随之确定（`code-canvas/serve.py:594-604`）。agent 要交的文件是 `<workdir>/canvas.json`（`code-canvas/serve.py:598`），入库目标是 `<hub>/<name>.json` 和 `.html`（`code-canvas/serve.py:599`）。

- **为什么要毫秒后缀**：注释原话「毫秒后缀去重：同一秒下多单会共享 id，prompt/status/workdir 互相踩（实案）」（`code-canvas/serve.py:591`）【注释】。提交 `305a312` 写明了怎么踩到的：「同一秒多个任务共享 id，prompt/status/workdir 互相覆盖（连点两次生成即可踩中）」【提交】。

**(b) 拼提示词**：`head + task_line(...) + tail`（`code-canvas/serve.py:736`）。
- `head`：让 agent 阅读 SKILL.md，并说明「它不是一条要逐步执行的管线」，给的是判断标准、要自己做的判断、可用工具和不可商量的交付契约（`code-canvas/serve.py:668-669`）
- `task_line`，深潜默认分支：「任务：为仓库 {w} 生成一张 Code Canvas，主题/关注点：{a}」（`code-canvas/serve.py:713`）。git 源时 `{w}` 填的是"缓存路径（共享缓存，clone 自 URL）"（`code-canvas/serve.py:736`）。
- `tail`：交付物路径、不可商量的契约（validate 无 ERROR、check_refs 全过、embed 嵌入、代码是原文、写 judgment）、完成标准（SKILL 第 1 节六条）、"用 render.py 渲染、截图自己看、用 reader_check.py 让新读者挑错"，以及「**不要写入画布库**」（`code-canvas/serve.py:659-667`）。

**(c) 拼 bash 脚本**，分三段：
1. **prep**：准备仓库，见 2.3。
2. **gen**：先记 `T1`，跑 claude，记 `rc=$?` 和 `T2`，写 `timing.json`（`clone_s`、`claude_s`），然后插入 `{post}`，最后 `echo $rc > {st}`（`code-canvas/serve.py:652-657`）。
3. **post_pass**：收尾入库，见 2.5。它只在 `rc` 为 0 时才执行（`code-canvas/serve.py:633`）。

git 源时，整段包成 `T0=$(date +%s); {{ {prep}; }} >> {log} 2>&1 && cd {d} && {{ {gen}; }}`，失败分支再记 clone 耗时并把 rc 写进 status（`code-canvas/serve.py:740-745`）。

**(d) 放手**：`prompt_f.write_text(prompt, encoding="utf-8")`，`subprocess.Popen(["bash", "-c", script], cwd=cwd, start_new_session=True)`，写 meta（含 pid、mode="deep"、cache 路径），返回（`code-canvas/serve.py:762-771`）。

**为什么起的是 `claude -p` 子进程，而不是在服务里调 API**：注释原话「引擎二选一，都走各自的订阅登录，不走 API」（`code-canvas/serve.py:639`）【注释】。首页的成本显示也标着「*成本为 API 价折算参考（订阅不按量计费）」（`code-canvas/serve.py:946`）【注释】。

**为什么用 stream-json**：提交 `d58259b`：「生成任务 claude 改 --output-format stream-json 落盘（NDJSON；尾行 result 事件即指标信封，指标解析兼容两种形态）」【提交】。这样监视器能边跑边读 agent 的活动。

**为什么 `start_new_session=True`，以及它其实不够**：提交 `51148ef`（仓库第一条提交）说「systemd 默认 KillMode=control-group 会在 restart 时杀掉整个 cgroup——作者在跑的生成任务被部署重启连带杀死（start_new_session 逃不出 cgroup）」，修法是「盒子服务单元加 KillMode=process」【提交】。也就是说，"服务重启不杀任务"这件事一半靠代码里的新会话，一半靠仓库外的 systemd 配置。仓库里看不到那份 unit 文件。

### 2.3 共享仓库缓存：四种取用姿势

缓存目录名 = URL 尾名 + URL 的 md5 前 8 位（`code-canvas/serve.py:352-355`），根目录可以用 `--repos-dir` 指到大盘（`code-canvas/serve.py:345-349`）。`spawn_generate` 根据两件事选姿势（`code-canvas/serve.py:722-734`）：**`.ready` 在不在**，以及**有没有同 URL 的任务在跑**（`_running_git_job`，`code-canvas/serve.py:358-370`）。

| `.ready` 在？ | 同仓任务在跑？ | 做什么 |
|---|---|---|
| 在 | 是 | 只 `touch .ready`，照用不动（`code-canvas/serve.py:723-724`） |
| 在 | 否 | 删 `.ready` → `fetch --depth 1` + `reset --hard` + `clean -fdx`，失败就用旧缓存 → 再 `touch .ready`（`code-canvas/serve.py:726-728`） |
| 不在 | 是（别人正在 clone） | 每 2 秒看一次 `.ready`，最多 900 次 ≈ 30 分钟（`code-canvas/serve.py:731`） |
| 不在 | 否 | `rm -rf` 后 `clone --depth 1`，成功才 `touch .ready`（`code-canvas/serve.py:733-734`） |

- **为什么共用一份**：注释「每单各自 clone 曾攒 21G 写爆磁盘」（`code-canvas/serve.py:716-717`）【注释】。ROADMAP 记了这次磁盘事故：「.jobs 攒 21G 写爆 45G 盘」（`ROADMAP.md:222`）【文档】。先上的修法是"收尾即删"，第二天被共享缓存取代：「昨天的"收尾即删"随之移除」（`ROADMAP.md:229`）【文档】。提交 `1baf906` 也写了「实测 linux 7 单共用一份克隆」【提交】。
- **为什么有同仓任务在跑时不刷新**：注释「刷新只在无同仓任务在跑时做，防止把文件从读者脚下抽走」（`code-canvas/serve.py:718`）【注释】。这里的"读者"指正在读这份缓存的另一个 agent。
- **为什么判定要上锁**：注释「缓存取用模式的判定+meta 落盘要原子，防同仓并发单互踩」（`code-canvas/serve.py:1160`）【注释】。`_running_git_job` 靠扫 meta 文件判断有没有同仓任务；如果两单的"判定"和"写 meta"交错，两单都会以为自己是第一个，然后都去 `rm -rf` 再 clone。
- **为什么整段要包 `{ }`**：注释「gen 是分号串，必须包成 {} 命令组，否则 && 只约束它第一条命令」，后果是「取缓存失败后 agent 照样在错误目录开跑（存量 bug，测试抓出）」（`code-canvas/serve.py:738-739`）【注释】。
- **缓存清理**：hub 启动时清掉 30 天没 touch 过 `.ready` 的缓存（`code-canvas/serve.py:425-436`、`code-canvas/serve.py:1289`）。

四种姿势都有测试覆盖：首单 clone、再下单走 fetch 而不是再 clone、同仓在跑时零 git 调用、等 `.ready` 后继续（`code-canvas/tests/hub.mjs:114-166`）【测试】。

### 2.4 生成 agent：它被要求做什么、实际怎么干

claude 进程的 cwd 是仓库缓存（`code-canvas/serve.py:740` 里的 `cd {d}`）。它拿到的只有那段 prompt。prompt 让它先读 `SKILL.md`，剩下的全靠它自己判断。

**SKILL.md 给的是"目标 + 判断 + 工具 + 契约"，不是步骤清单**（`code-canvas/SKILL.md:11-14`）：
- **目标**（第 1 节）：读者只看画布就能做到六条：说出主角的一生、一句话说清每个部件和它"不是什么"、讲出核心机制、预测变化的后果、分清事实和推断、决定下一步读哪里（`code-canvas/SKILL.md:18-33`）。
- **必须自己做的 8 个判断**（第 2 节）：范围与规模（约 2000 行以内做深潜，再大先出预览地图）、故事形态、主角、部件怎么切（按职责和执行归属，不按文件夹）、剪什么、每个机制用什么表达、和谁对比、演示哪些情况。答案写进 `meta.judgment`（`code-canvas/SKILL.md:35-67`）。
- **推荐循环**（第 3 节）：探索 → 假设 → 验证 → 画 → 让新读者挑错（`code-canvas/SKILL.md:69-90`）。
- **交付契约**（第 6 节）：validate 无 ERROR、check_refs 全过、卡内代码是原文、行号用脚本算、布局只给 `{col, band}`、必须有 `meta.judgment`（`code-canvas/SKILL.md:133-146`）。

**agent 在这个循环里会碰到的工具**（`code-canvas/SKILL.md:115-123`）：
- `extract.py`：零 LLM 的 Python AST 提取器。机械产出函数卡（原文）、call 线（行锚）和粗布局，是"研究材料，不是答案"（`code-canvas/SKILL.md:74-77`、`code-canvas/extract.py:2-9`）。v1 只支持 `.py`。
- `validate.py` / `check_refs.py` / `embed_context.py` / `render.py`：和服务端收尾用的是同一批脚本（见 2.5）。
- `reader_check.py`：**再起一个 `claude -p`**（默认 sonnet，`code-canvas/reader_check.py:54`），只给它去掉 `files` 和 `judgment` 的 canvas.json（`code-canvas/reader_check.py:60-61`），工具只开 `Read`（`code-canvas/reader_check.py:69`），让它复述主角的一生，并列出读不懂的地方。所以一单生成可能有**两层 claude 进程**。

**为什么是"判断清单"而不是"编号管线"**：提交 `f4bbe29` 把 v0.1 的编号管线换成了 v0.2，提交标题是「SKILL v0.2 agent 版：目标+判断标准+决策清单取代编号管线；reader_check 新读者挑错；meta.judgment 判断记录」【提交】。同一提交 `f4bbe29` 的 diff 把 prompt 从「严格按其管线执行」改成了现在的「它不是一条要逐步执行的管线」（`code-canvas/serve.py:668`）。这个提交的说明里**没有写为什么换**。SKILL 正文第 10 节列的失败模式，可以看作动机：「**填表**：为了消掉 warn 而写出空洞的 overview/scenarios/trace——宁可不写并说明」（`code-canvas/SKILL.md:200`）【文档】。把"填表"失败模式和改版联系起来是我的推断【推断】。

**为什么 agent 只给粗位置**：HANDOFF 的原话是「布局哲学：agent 只给粗位置 {col, band}，渲染器精排」，又写「**不要给像素坐标**」（`HANDOFF.md:164-166`）【文档】。理由在资产表里：「布局引擎在 JS 里（浏览器能真实测文本宽度，Python 只能估——这是架构决定）」（`HANDOFF.md:177`）【文档】。

**为什么 `--dangerously-skip-permissions`**：代码和提交说明都没有直接写理由。我的推断是：这是无人值守的后台进程，没有人能点"允许"，不跳过权限就会卡死【推断】。

### 2.5 服务端收尾 `post_pass`：不信任 agent，再来一遍

claude 退出码为 0 时，任务脚本接着执行 `post_pass` 拼出的这一串（`code-canvas/serve.py:627-637`）。按顺序：

1. `if [ -f {w} ]`（`{w}` 即工作目录里的 canvas.json），不在就 `rc=5`。
2. `embed_context.py canvas.json <仓库根>`：把每张代码卡引用的**源文件全文**写进顶层 `files`。退出码被忽略，后面接的是 `;`（`code-canvas/serve.py:627-628`）。
3. `check_refs.py canvas.json <仓库根>`：依据核对，核不过的就地剔除。退出码同样被忽略（`code-canvas/serve.py:620-621`）。
4. `validate.py canvas.json`，不过就 `rc=4`。
5. `cp` 到 `<hub>/<name>.json`，再 `render.py` 生成 `<hub>/<name>.html`，然后写 `<name>.src.json`（内容是 `{"git_url": ...}`）。这一串任一步失败就 `rc=6`（`code-canvas/serve.py:634`、`code-canvas/serve.py:629-632`）。
6. 外层 `echo $rc > {st}`（`{st}` 即 `<id>.status`）（`code-canvas/serve.py:654`）。

函数注释给的退出码表：「rc：3 升级被拒 / 4 validate 未过 / 5 agent 没产出 canvas.json / 6 渲染失败」（`code-canvas/serve.py:609-610`）。

**为什么在服务端再做一遍**：
- 提交 `690022d`：「作者裁决：保障不该压在 agent 守 SKILL 规程上——service 能机械做的就在 service 做。」【提交】
- 起因在提交 `40781a8`：「作者报新生成画布无上下文——根因：--embed-context 只是 SKILL 命令注释里的可选项，生成 agent 从不执行」【提交】。
- 提交 `4b43d42` 把收尾扩成了"全过才入库"：「agent 只产工作目录 canvas.json，服务端 embed + check_refs + validate 全过才入库（done ⇔ 已入库；rc 4/5/6 如实失败；来源记录入库时才写）」【提交】。
- 测试佐证：假 agent "忘了嵌入"，服务端补上并重渲染（`code-canvas/tests/hub.mjs:604-624`）；validate 不过的不入库，status 为 4，也不写 sidecar（`code-canvas/tests/hub.mjs:630-632`）【测试】。

**几个零件各干什么**：

- **embed_context.py**（`code-canvas/embed_context.py`）：
  - 对每张 `file: "路径:起始行"` 的卡，拿卡内 code 去对仓库文件那一行起的窗口。比较时忽略空白、`//`、`#`、`\`（`code-canvas/embed_context.py:22-23`、`code-canvas/embed_context.py:69-70`）。
  - 对不上时，在全文里找首行相同、整段也匹配的地方，**改写卡的行号**（"行漂移修正"，`code-canvas/embed_context.py:72-81`）。
  - 卡路径可能是相对某个子目录写的，所以它会挑"命中卡路径最多"的子目录当仓库根（`code-canvas/embed_context.py:35-51`）。
  - 一张都对不上就 `exit 2`（`code-canvas/embed_context.py:85-87`）。但服务端忽略这个退出码，所以画布照样往下走，只是没有上下文。
- **check_refs.py**：依据格式是 `[文件, 行号, 符号]`。文件要存在、行号不越界、符号在该行 ±3 行内（`code-canvas/check_refs.py:18-35`）。核不过的剔除；某条步卡的依据如果全被剔光、它又自称 fact，就降级成 `infer`（`code-canvas/check_refs.py:72-74`）。它总是 `return 0`（`code-canvas/check_refs.py:82`）。设计意图是：「读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。」（`code-canvas/check_refs.py:8`）【注释】
- **validate.py**：
  - ERROR 表示结构坏了（悬空引用、行号越界、块重叠……），退出码 1；warn 只是预算 / 缺层的提醒（`code-canvas/validate.py:2-7`、`code-canvas/validate.py:658-662`）。
  - 有个容易漏掉的点：服务端调用时**没带 `--no-exec`**，所以画布里的"块沙盘"脚本会在 hub 机器上用 `python -I` 真跑一遍，和 `expected` 对比（`code-canvas/validate.py:88-101`）。注释原话「expected 必须是实际运行的产物，不许想象」（`code-canvas/validate.py:65`）【注释】。
- **render.py**：只有 30 行。读模板，把 JSON 序列化后替换占位符 `/*CANVAS_DATA*/null`，并把 `</` 转义成 `<\/`（`code-canvas/render.py:10`、`code-canvas/render.py:19-25`）。转义的理由：「</script> inside string data must not terminate the script tag」（`code-canvas/render.py:23`）【注释】。

**为什么 embed 在 validate 前面**：没有直接说明。代码上有两处依赖：
- validate 会对"有 file:line 却没有 files 映射"的卡报 warn（`code-canvas/validate.py:126-136`）。
- validate 核对 boxes 和 scenarios 里的名字时，用的语料是"卡片代码 ∪ files 全文"（`code-canvas/validate.py:563-565`）。

所以先嵌入，validate 才能拿全文去核"黑盒函数名 / 推演变量名是不是编的"。这条顺序理由是我从代码推出来的【推断】。

**为什么来源记录（`.src.json`）只在入库时才写**：提交 `4b43d42` 写了「来源记录入库时才写」【提交】。测试也断言入库前不会出现 sidecar（`code-canvas/tests/hub.mjs:414`）【测试】。sidecar 决定了首页按仓库分组（`code-canvas/serve.py:285-299`），也决定了问答时去哪找仓库（`code-canvas/serve.py:389-412`）。我推断：提前写的话，失败的任务会留下"孤儿来源记录"【推断】。

### 2.6 状态怎么回到首页

服务端从不主动推送。浏览器问 `/jobs` 时，`list_jobs` 当场扫 `.jobs/*.meta.json`（`code-canvas/serve.py:439-465`）：
- 有 `.status`：内容为 `0` 显示 done，否则显示 `failed(<码>)`（`code-canvas/serve.py:447-449`）；
- 没有 `.status`、pid 也死了：显示 `failed(中断)`（`code-canvas/serve.py:450-451`）；
- 其余显示 running。

同时附上 `timing.json` 和 claude 的指标信封（token、成本），信封取自 `result.json` 的最后一条 `type:"result"` 事件（`code-canvas/serve.py:468-490`）。

**为什么要有"中断"这个状态**：提交 `51148ef`：「job meta 记 pid；list_jobs 对 无 status + pid 已死 的任务如实标 failed(中断)，不再永远显示 running」【提交】。测试见 `code-canvas/tests/hub.mjs:350`【测试】。

**监视器**：`/jobs/<id>/monitor` 的阶段进度完全靠**看工作目录里有哪些文件**来推断（`code-canvas/serve.py:510-521`）：
- 仓库缓存：`.ready` 在；
- 结构层：有 `structure*.json`；
- 画布 JSON：有 `canvas.json`；
- 渲染 HTML：有 `canvas.html`；
- 截图自检：有 `*.png`；
- 入库：库里的 html 比任务开始时间新。

agent 活动流则是数 `result.json` 里 `assistant` 事件的条数，并取最近 3 个工具调用（`code-canvas/serve.py:523-548`）。提交 `d58259b` 的说法是「阶段进度从工作目录文件推断（克隆→结构层→画布 JSON→渲染→截图→入库，零 token 成本）」【提交】。

### 2.7 读者打开画布：排版在浏览器里才发生

`GET /c/<name>/`：
1. `_canvas_path` 先在画布库找，再去 `examples/` 找（`code-canvas/serve.py:1011-1020`）。
2. 调 `maybe_rerender`：如果库里的 html **比模板旧**，就用旁边的 json 重新 render（`code-canvas/serve.py:236-262`、`code-canvas/serve.py:254-260`）。
3. 把 html 原样返回（`code-canvas/serve.py:1042-1044`）。

AGENTS.md 的说法是「模板变更**无需**重渲染存量画布（serve 时自动重渲染）」（`AGENTS.md:17`）【文档】。测试把 html 的时间回拨到 2000 年，断言它会被刷新（`code-canvas/tests/hub.mjs:451-456`）【测试】。

页面加载后，模板里 `const DATA = /*CANVAS_DATA*/null;` 已经被替换成整份画布数据（`code-canvas/template/canvas.html:740`）。下一帧调 `layout()`（`code-canvas/template/canvas.html:2895-2896`）：
- 先把每张卡展开，量出自然宽度，上限 800px（`code-canvas/template/canvas.html:1036-1042`）；
- 按 `layout.col` 分列，列的 x 坐标 = 前面各列最宽卡的宽度 + 间距 230（`code-canvas/template/canvas.html:1031`、`code-canvas/template/canvas.html:1064-1068`）；
- 卡的基准 y：`c._baseY = L.top + c.layout.band * L.bandH`，其中 top=160、bandH=280，即 160 + band × 280（`code-canvas/template/canvas.html:1075`）；
- 再做同列下推、摆 note、包 region（`code-canvas/template/canvas.html:1077-1080`）。

之后 `setStep(i)` 按 `steps[i]` 展开指定的卡和块、点亮行、只亮这一步的连线两端的卡（`code-canvas/template/canvas.html:1689-1721`）。页面还会 `fetch('__alive')` 探测 hub 在不在线；在线就打开问答（`code-canvas/template/canvas.html:1801`）。

---

## 3. 贯穿例子：带具体值走一遍

> 标【设】的值是我为了讲解设的；其余值能从代码算出来或在仓库里找到。

**输入**
- git 地址：`https://github.com/GeeeekExplorer/nano-vllm`【设】。选它是因为仓库自带这个仓库的"黄金样本"画布 `code-canvas/demo/nano-vllm.json`（`HANDOFF.md:186`）。
- 主题 / 关注点：`讲讲调度器怎么工作`。这是表单 ask 输入框的占位示例（`code-canvas/serve.py:847`）。
- 画布名：留空。
- 其它选项：保持默认（git / 深潜 / Claude）。
- hub 目录：`/home/ubuntu/hub`【设】；`--repos-dir` 未设。
- 下单时刻：2026-11-23 10:15:30.482【设】。

**第 1 步：浏览器发出的 body**
```json
{"ask": "讲讲调度器怎么工作", "git_url": "https://github.com/GeeeekExplorer/nano-vllm"}
```
`name` 是 `undefined`，`JSON.stringify` 会丢掉它。没有 `preview / zoom / engine`，因为都是默认（`code-canvas/serve.py:876-883`）。

**第 2 步：服务端校验后得到**
- `src = {"kind": "git", "git_url": ".../nano-vllm"}`；
- `name = "nano-vllm"`：取 URL 尾段，去 `.git`（`code-canvas/serve.py:1126`、`code-canvas/serve.py:1151`）；
- `preview = False`、`zoom = False`、`engine = "claude"`、`model = ""`。

**第 3 步：spawn_generate 定下的名字和路径**
- `job_id = j20261123-101530-482`（格式见 `code-canvas/serve.py:592`）。
- 账本：`/home/ubuntu/hub/.jobs/j20261123-101530-482.{prompt,log,status,meta.json,result.json,timing.json}`，工作目录 `/home/ubuntu/hub/.jobs/j20261123-101530-482/`。
- agent 交付文件：`/home/ubuntu/hub/.jobs/j20261123-101530-482/canvas.json`。
- 缓存目录：`/home/ubuntu/hub/.repos/nano-vllm-155c1586`。末尾的 155c1586（不是提交哈希）是这个 URL 的 md5 前 8 位，是我在自己目录里算出来的。
- 首单：缓存和 `.ready` 都不存在、也没有同仓任务，走 `rm -rf; git clone --depth 1 ... && touch nano-vllm-155c1586.ready`。
- 模型：没传，用 `claude-opus-5-5`（`code-canvas/serve.py:83`、`code-canvas/serve.py:642`）。
- meta.json 的内容形如：`{"id": "j20261123-101530-482", "name": "nano-vllm", "ask": "讲讲调度器怎么工作", "source": "https://github.com/GeeeekExplorer/nano-vllm", "mode": "deep", "pid": <bash 的 pid>, "engine": "claude", "model": "", "started": "2026-11-23T10:15:30", "cache": ".../nano-vllm-155c1586"}`（`code-canvas/serve.py:764-769`）。
- 浏览器拿到 `{"ok": true, "job": {...}}`，1.2 秒后刷新页面（`code-canvas/serve.py:891-892`）。

**第 4 步：agent 的 prompt**（三段拼接，`{skill}` 是 code-canvas 目录）
```
阅读 {skill}/SKILL.md。它不是一条要逐步执行的管线：…
任务：为仓库 /home/ubuntu/hub/.repos/nano-vllm-155c1586（共享缓存，clone 自 https://github.com/GeeeekExplorer/nano-vllm）生成一张 Code Canvas，主题/关注点：讲讲调度器怎么工作
交付：/home/ubuntu/hub/.jobs/j20261123-101530-482/canvas.json（canvas JSON）。
SKILL 第 6 节的交付契约不可商量：…**不要写入画布库**…完成后打印 DONE。
```

**第 5 步：agent 产出什么**

agent 的行为每次都不同，没法从代码推出。所以这里用仓库里现成的 `demo/nano-vllm.json` 举"一份合格的 canvas.json 长什么样"。注意：仓库没说明这份文件是不是经 hub 生成的；它也是旧格式，没有 `meta.judgment / overview / scenarios / boxes / gaps / objects`，现在的 validate 会对这些缺项报 warn，但不报 ERROR（`code-canvas/validate.py:309`、`code-canvas/validate.py:318`、`code-canvas/validate.py:410`）。

它的数字：
- **10 张卡**：
  - col 0：`step` / `run` / `sampler`；
  - col 1：`schedule`（49 行）/ `preempt` / `postprocess`；
  - col 2：`can_allocate` / `allocate` / `hash_blocks`；
  - col 3：一张 `kv-pool` 快照卡。
- **3 条故事线**：A 引擎循环、B 调度决策、C 显存账本。
- **11 根线**：8 根 call、2 根 data、1 根 struct。
- **6 步**：总览 + ①–⑤。
- **files 里嵌了 5 个源文件**。

举一张卡（`code-canvas/demo/nano-vllm.json:47-49`）：id 是 `"step"`，name 是 `"LLMEngine.step()"`，file 字段指向 llm_engine.py 的第 49 行起（JSON 里写作"路径:行号"），layout 为 col 0、band 0，code 第 2 行是 `seqs, is_prefill = self.scheduler.schedule()`。

线 `w-sched` 从 `step` 卡第 2 行（`seqs, is_prefill = self.scheduler.schedule()`）连到 `schedule` 卡。

第 ① 步：`focus = [step, schedule, run, postprocess]`，`wires = [w-sched, w-run, w-post, d-token]`，`lines = [[step,2],[step,4],[step,5]]`。

**第 6 步：服务端收尾**（假设 claude 退出码为 0）
- `embed_context.py`：卡路径写的是 `engine/llm_engine.py`，不带上层目录。如果仓库根下直接找不到，它会去子目录里挑命中最多的那个当根（`code-canvas/embed_context.py:35-51`）。nano-vllm 的代码在哪个子目录，我没法在本地核实【推断】。
- `check_refs.py`：这份旧画布没有 refs，所以是"0 条"。
- `validate.py`：0 ERROR，过。
- `cp` 得到 `/home/ubuntu/hub/nano-vllm.json`，`render.py` 得到 `/home/ubuntu/hub/nano-vllm.html`。demo 的 json 序列化后约 4.7 万字符，HTML 就是"模板 + 这份数据"。
- 写 `/home/ubuntu/hub/nano-vllm.src.json`，内容 `{"git_url": "https://github.com/GeeeekExplorer/nano-vllm"}`。
- `.status` 写入 `0`，首页显示 done。监视器的六个阶段：仓库缓存 ✓、结构层（看 agent 有没有在工作目录写 structure*.json）、画布 JSON ✓、渲染 HTML（agent 自查时渲染的 canvas.html）、截图自检（工作目录里有没有 png）、入库 ✓。

**第 7 步：读者打开** `/c/nano-vllm/`
- html 比模板新，不重渲染。
- 浏览器 `layout()`：col 0 的 x = 40（没有左侧 note 时，`L.margin` 是 40，`code-canvas/template/canvas.html:1031`）；col 1 的 x = 40 + col 0 最宽卡的宽度 + 230。
- `step` 卡（band 0）的基准 y = 160；`run` 卡（band 2）的基准 y = 160 + 2 × 280 = 720。之后还会被同列下推动态调整。
- 读者按 ▶，进入第 ① 步：4 张卡聚焦，4 根线点亮，其余卡变暗。

hint 里写一张画布约 20–40 分钟（`code-canvas/serve.py:849`）【文档】。这个时长主要花在第 5 步。

---

## 4. 宏观"为什么"汇总

**为什么首页是生成表单，而不是画布列表**：HANDOFF 原话「作者裁决记录：hub 的意义是"take 我给的 repo/代码然后生成"，固定画布库只是沉淀不是主角——首页必须是生成入口」（`HANDOFF.md:180`）【文档】。

**为什么只绑 tailscale 地址**：docstring 原话「不要绑 0.0.0.0——/ask 和 /generate 都会花钱」（`code-canvas/serve.py:41-42`）【注释】。hub 没有任何鉴权，网络边界就是全部的访问控制。

**为什么产物是自包含的单文件 HTML，而且库里同时留着 JSON**：
- render 是把整份数据塞进模板（`code-canvas/render.py:24-25`），所以 `/download` 下载到的就是能离线打开的成品（`code-canvas/serve.py:1045-1054`）。
- 留 JSON 是为了模板升级时能重渲染（2.7），也是问答补丁要改的那份数据（`code-canvas/serve.py:134-147`）。
- "离线可用"作为目标，我只在 ROADMAP 会话日志里看到「下载离线版」几个字（`ROADMAP.md:74`），没看到更多论证【推断】。

**为什么事实和叙事分两层管**：
- extract.py 只产出结构层："叙事层…一概不产出——那是 agent 的层"（`code-canvas/extract.py:4-6`）。
- SKILL 要求「结构层的事实字段（code/file/行号/diff）不许手改」（`code-canvas/SKILL.md:76-77`）。

服务端那几道收尾都在守事实层：embed 核代码原文、check_refs 核依据、validate 核引用和变量名。我的理解是："写得好不好"交给 agent 和读者测试，"是不是真的"交给机械核对。这是我对整体分工的概括，不是哪处原文【推断】。

**为什么 Python 3.8 兼容**：AGENTS.md 写着「Python 3.8！代码须兼容」（`AGENTS.md:14`）【文档】。

---

## 5. 读者最容易看错的地方

1. **"agent 画了画布"**。不对。agent 交的是 JSON，像素布局在读者浏览器里算（2.7）。agent 在工作目录里渲染的 `canvas.html` 只给它自己截图用；入库的 html 是服务端从库里的 json 重新 render 的（`code-canvas/serve.py:634`）。
2. **"validate 跑了一次"**。至少两次，可能更多：agent 自查若干次，服务端收尾一次。两边用的是同一份脚本。
3. **"embed / check_refs 失败会拦下画布"**。不会。服务端忽略它们的退出码（`;` 连接，`code-canvas/serve.py:620`、`code-canvas/serve.py:627`），能拦下画布的只有 validate（rc=4）。check_refs 采取"删掉假依据、降级 ev"的方式，不拒收。
4. **"任务完成会通知首页"**。不会。完成只是写了一个 `.status` 文件；首页靠 5 秒一次的轮询发现（`code-canvas/serve.py:953`）。
5. **"服务端会等任务、会超时杀任务"**。生成任务**没有超时**：bash 里的 claude 调用没有 timeout，Popen 之后也不 wait。`TIMEOUT_S = 300`（`code-canvas/serve.py:59`）只用于问答的 `run_cli`（`code-canvas/serve.py:100`）。唯一有上限的等待是"等别人 clone"的 30 分钟。
6. **"缓存是只读的"**。注释说「任务全程只读缓存」（`code-canvas/serve.py:717-718`），但这只是约定：agent 带着 `--dangerously-skip-permissions` 跑在缓存目录里（`code-canvas/serve.py:649`），代码没有强制只读。
7. **名不副实的 `deep`**。meta 里的 `mode: "deep"`（`code-canvas/serve.py:765`）只是任务类型标签；画布 JSON 里的 `meta.mode` 对深潜画布是**省略**的（`code-canvas/schema.md:6-7`）。两个 "mode" 不是一回事。
8. **监视器的"结构层"阶段**。只有 agent 恰好把 `structure*.json` 写在工作目录里时才会打勾（`code-canvas/serve.py:514`）；agent 不跑 extract.py 也完全合规。
9. **一单里有几个 claude 进程**。生成 agent 一个；它跑 `reader_check.py` 时会再起一个（默认 sonnet）。后者的花费不在 `result.json` 的指标信封里（信封只是外层 claude 的 stdout）。这一点是我根据进程结构推断的【推断】。

---

## 6. 旁路（只点名，不展开）

都从主路的某个分叉点岔出去：

- **预览地图**（`preview: true`）：在 `task_line` 换成"研究型预览地图"任务（`code-canvas/serve.py:705-710`），名字加 `-map` 后缀（`code-canvas/serve.py:1149-1150`）。规程是 `preview-spec.md`。
- **动画缩放图**（`zoom: true`）：prompt 换成读 `zoom-spec.md` / `scene-spec.md`（`code-canvas/serve.py:670-685`），交付 `zoom.json`。收尾改用 `build_zoom.py`，它从仓库读代码、校验、渲染（`code-canvas/serve.py:612-619`）。名字加 `-flow` 后缀（`code-canvas/serve.py:1144-1145`）。
- **升级已有画布**（`POST /c/<name>/upgrade`）：复制库里的 json 给 agent 改；收尾用 `merge_upgrade.py` 锁死事实层，动了事实层就整份拒收，rc=3（`code-canvas/serve.py:600-601`、`code-canvas/serve.py:622-626`、`code-canvas/serve.py:1090-1111`）。
- **codex 引擎**：`core` 换成 `codex exec --json`，指标和轮数解析不适用（`code-canvas/serve.py:640-646`）。
- **盒子路径 / 粘贴代码**：不走缓存；粘贴代码时不嵌上下文、不核依据，只过 validate（`code-canvas/serve.py:748-760`）。
- **生成之后的问答**：`POST /c/<name>/ask` 用一次性 `claude -p`（只读工具，cwd 是仓库），回答里可以带 `canvas-patch` 回写画布，回写前过 validate 闸门（`code-canvas/serve.py:1219-1256`、`code-canvas/serve.py:134-147`）。
- **单画布模式**：`serve.py canvas.html`，只做问答，不生成（`code-canvas/serve.py:4-6`）。
- **duo 两人组**（`demo/duo/run.py`）：最新的实验管线。研究员写底稿 → 讲解员做机器图 → 按申请往下钻，每个角色一次 `claude -p`（`code-canvas/demo/duo/run.py:1-28`）。它只收本地 `--repo`，没接进 hub（`ROADMAP.md:507`）。
- **结构图 flow 模式**：SKILL 第 8 节提到（`code-canvas/SKILL.md:183-186`），hub 表单里没有这个选项；`maybe_rerender` 对 `mode` 为 flow / zoom 的 json 有单独的重注入分支（`code-canvas/serve.py:244-253`）。

---

## 7. 每个部件里面有没有值得单独钻的机制

| 部件 | 里面有什么值得钻 | 入口锚点 |
|---|---|---|
| hub 服务 / spawn_generate | 有。一段 shell 字符串拼出来的"小状态机"：rc 如何在 prep / gen / post 之间传递，5 种退出码，`{}` 分组的坑 | `code-canvas/serve.py:584`、`code-canvas/serve.py:608-657`、`code-canvas/serve.py:740-745` |
| 共享仓库缓存 | 有，但小。四种姿势已在 2.3 讲完；还剩问答线程 `_bg_clone` 的"临时目录 + 原子改名"，以及 30 天清扫 | `code-canvas/serve.py:373-412`、`code-canvas/serve.py:425-436` |
| 生成 agent（SKILL.md 规程） | 有，最值得。8 个判断怎么做、表达工具箱各手段的适用条件、meta.judgment 格式 | `code-canvas/SKILL.md:35-67`、`code-canvas/SKILL.md:92-113`、`code-canvas/SKILL.md:148-164` |
| canvas JSON 格式 | 有。卡、块树、线锚、steps、scenarios、state 快照卡、证据分级 ev | `code-canvas/schema.md` |
| extract.py | 有。AST 提取函数卡、call 线行锚、BFS 粗布局、`--merge` 增量协议 | `code-canvas/extract.py:1-20` |
| validate.py | 有。约 660 行的规则集，含沙盘真跑和"不许编造变量名"的语料核对 | `code-canvas/validate.py:111`、`code-canvas/validate.py:563-565` |
| embed_context / check_refs | 小，本稿基本讲完 | `code-canvas/embed_context.py:26`、`code-canvas/check_refs.py:18` |
| reader_check | 小。一个去上下文的读者 prompt | `code-canvas/reader_check.py:12-35` |
| 渲染器模板 | 有，很大。测量布局、同列下推、note 摆位、连线锚点、步进镜头、情境推演、手机卡片流、问答抽屉 | `code-canvas/template/canvas.html:1034`、`code-canvas/template/canvas.html:1287`、`code-canvas/template/canvas.html:1689` |
| 任务监视 | 小。2.6 已讲 | `code-canvas/serve.py:493` |

---

## 8. 没查到为什么

- **为什么生成任务不设超时**。问答有 300 秒超时，生成没有任何上限。代码和提交里都没找到解释。
- **为什么服务端收尾的 validate 不带 `--no-exec`**（也就是在 hub 机器上真跑 agent 写的沙盘脚本）。我没找到专门讨论服务端执行这件事的说明；只有 validate 自己的注释说 `--no-exec` 是「无解释器的环境用」（`code-canvas/validate.py:115`）。
- **为什么用 `--dangerously-skip-permissions`**。这一行在仓库首个提交 `51148ef` 里就已经存在，此前的历史不在仓库里（历史从 2026-08-19 的导入提交开始）。
- **SKILL v0.2 为什么从编号管线改成判断清单**。提交 `f4bbe29` 的说明只有标题，没有正文；ROADMAP 里也没找到这一天的条目。2.4 里的动机是推断。
- **为什么 reader_check 默认用 sonnet 而不是生成用的 opus**。没找到说明。
- **hub 机器上 systemd unit 的 `KillMode=process`**。只在提交说明和 ROADMAP 里提到，仓库里没有 unit 文件，无法核实现状。

---

## 9. 可改进处【观点】

- 【观点】生成任务没有超时，也没有取消入口。一个卡住的 agent 会一直显示 running，还会让同仓后来者一直"照用缓存"、无法刷新。
- 【观点】embed 失败（exit 2）被静默忽略：画布能入库，但没有上下文。可以至少在 `.log` 之外给 job meta 记一个告警字段。
- 【观点】render 失败时 rc=6，但 json 已经 `cp` 进库了（`cp {w} {hj} && {render} {hj} {hh} >> {lg} 2>&1 && {{ {sidecar}:; }} || rc=6`）。库里会留下一个只有 json、没有 html 的半成品。列表页只认 `*.html`，所以它看不见，但也不会被清理。
- 【观点】缓存"只读"是约定，不是强制。在缓存上带 `--dangerously-skip-permissions` 运行，一个手滑的 agent 可能弄脏同仓的其他任务。ROADMAP 在 2026-10-19 那条会话日志里也记录了"共享缓存已前进到新 HEAD，而卡片代码是旧快照——"画布钉 commit"更迫切"（`ROADMAP.md:342`），说明缓存漂移已经被注意到。
