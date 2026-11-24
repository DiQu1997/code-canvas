# 调度员实录（canvas-duo skill 实测，2026-11-23 ~ 11-24）

调度员是一个全新的 `claude -p` 会话（opus），工作目录是 LMCache 克隆的根，skill 装在 `~/.claude/skills/canvas-duo`（来源提交 `0ba1408`）。
允许工具：Skill / Bash / Read / Glob / Grep / Write / Edit；禁用 Agent / Task / WebFetch / WebSearch。
下面是两轮里它的**全部工具调用**（截到前 400 字）和**全部文字回复**（原文），由 stream-json 日志机械抽出；只把临时目录路径换成 `<scratch>`。

## 第一轮

**我发的**（预算上限 $3）：用 canvas-duo 讲讲：LMCache 是怎么把 KV cache 存下来、下一个请求又怎么取回来复用的，一步步讲

- 工具 `Skill`：`{"skill": "canvas-duo", "args": "LMCache 是怎么把 KV cache 存下来、下一个请求又怎么取回来复用的，一步步讲"}`
- 工具 `Bash`：`{"command": "REPO=$(git rev-parse --show-toplevel) && echo $REPO && OUT=~/canvas-duo-runs/LMCache-$(date +%Y%m%d-%H%M) && echo $OUT && python3 -I /root/.claude/skills/canvas-duo/preflight.py --repo \"$REPO\" --out \"$OUT\" --probe 2>&1 | tail -40", "description": "Run canvas-duo preflight checks with probe", "timeout": 300000}`

**回复：**

预检通过了：8 项 OK，0 WARN，0 FAIL。仓库工作区是干净的（HEAD 在 `b27f6bd9e2`）。顺带试起了一次 claude，花了 $0.144，确认它在无人值守模式下能读到仓库。

**还没开跑，要等你点头**，因为这一单比较贵：

- **问题**：照你的原话——「LMCache 是怎么把 KV cache 存下来、下一个请求又怎么取回来复用的，一步步讲」
- **之前三次真跑的花费**（默认模型，最多往下钻 3 个框）：
  - sglang：$45.69，中途断过一次又接着跑
  - vllm：$32.64，87 分钟
  - code-canvas：$25.17，78 分钟
- **预计**：一般 $25–45，60–90 分钟。默认预算上限 $55，超过就不再起新 agent，已经在跑的会跑完。
- **计费**：走本机 claude 的登录，订阅额度或 API 计费都会扣。这比本会话 $3 的预算高出一个量级，请确认这部分钱能用。
- **数据**：仓库代码会发给 Claude 处理，和平时用 Claude Code 读这个仓库一样。成品 HTML 里嵌着源码，请按仓库本身的保密级别对待，不要上传或公开分享。
- **产出位置**：`/root/canvas-duo-runs/LMCache-20261123-2359/LMCache-canvas.html`

**想省钱可以这样调**：
- `--max-dives 1` 或 `2`：少往下钻几个框
- `--budget <美元>`：压低预算上限
- `--models research_top=sonnet`：顶层研究员换便宜一档的模型。这个组合还没真跑过，质量没验证。

回「跑」就按默认配置开始；要省钱的话告诉我用哪种。


> 结果：success，累计花费 $0.370（resume 时 total_cost_usd 是累计数），本段 4 轮、25 秒

## 第二轮（`--resume` 接着第一轮）

**我发的**（预算上限 $6）：跑

- 工具 `Bash`：`{"command": "OUT=/root/canvas-duo-runs/LMCache-20261123-2359; mkdir -p \"$OUT\" && cd \"$OUT\" && nohup python3 -I /root/.claude/skills/canvas-duo/run.py --repo <scratch>/corpus5/LMCache --question \"LMCache 是怎么把 KV cache 存下来、下一个请求又怎么取回来复用的，一步步讲\" --out \"$OUT\" --name LMCache-canvas --budget 55 > \"$OUT/run.log\" 2>&1 &\nsleep 5; cat /root/canvas-duo-runs/LMCache-20261123-2359/progress.txt 2>/dev`
- 工具 `Bash`：`{"command": "while pgrep -f \"canvas-duo/run.py\" >/dev/null; do sleep 60; done; tail -30 /root/canvas-duo-runs/LMCache-20261123-2359/progress.txt", "description": "Wait for canvas-duo run to finish", "timeout": 7200000, "run_in_background": true}`

**回复：**

已经开跑了，23:59 启动，在后台运行。

- **运行目录**：`/root/canvas-duo-runs/LMCache-20261123-2359/`
- **模型**：顶层的研究员和讲解员用 opus，下钻那层研究员用 sonnet、讲解员用 opus
- **设置**：最多往下钻 3 个框，预算上限 $55
- **现在在哪一步**：第一步，顶层研究员在读代码、写底稿，大约 10 分钟

整单预计 60–90 分钟。我在后台等它跑完，跑完会读报告、抽查几条说法对不对得上源码，再把结果告诉你。中途想知道进度随时问，我去看 `progress.txt`。


> 结果：success，累计花费 $0.428（resume 时 total_cost_usd 是累计数），本段 3 轮、16 秒

- 工具 `Bash`：`{"command": "cd /root/canvas-duo-runs/LMCache-20261123-2359; tail -40 progress.txt; echo ----; ls; ls logs content 2>/dev/null; echo ----; tail -20 run.log", "description": "Read run progress and outputs"}`
- 工具 `Bash`：`{"command": "cd /root/canvas-duo-runs/LMCache-20261123-2359; cat report.md; echo =====SHOTS; tail -15 logs/final-shots.txt; echo =====SPOT; for f in logs/*.spot.txt; do echo \"## $f\"; tail -4 $f; done", "description": "Read report and check results"}`

**回复：**

Now the agents' self-reported shortcomings and framework issues.

- 工具 `Bash`：`{"command": "cd /root/canvas-duo-runs/LMCache-20261123-2359; for f in logs/present-top.reply.md logs/present-xfer.reply.md logs/present-l1.reply.md logs/present-sched.reply.md logs/present-top.fix.reply.md; do echo \"######## $f\"; cat $f | head -c 5000; echo; done; echo \"######## FRAMEWORK\"; for f in content/framework-issues-*.md; do echo \"## $f\"; head -c 1800 $f; echo; done", "description": `

**回复：**

Next, I'll spot-check a few claims against the source.

- 工具 `Bash`：`{"command": "cd <scratch>/corpus5/LMCache; grep -rn --include=*.py -iE \"0\\.8\\b|watermark|eviction_ratio|0\\.2\\b\" lmcache/v1/distributed 2>/dev/null | grep -iE \"evict|water|ratio\" | head; echo ---; grep -rnE \"300|600\" --include=*.py lmcache/v1/distributed 2>/dev/null | grep -iE \"ttl|lock|timeout\" | head; echo ---; grep -rn \"32 \\?\\* \\?1024\\|32768\\|32 KiB\\|32KiB\" --include=*.py lmc`
- 工具 `Bash`：`{"command": "cd <scratch>/corpus5/LMCache; grep -rniE \"direct.*(threshold|min|bytes)|(threshold|min).*direct\" --include=*.py --include=*.cu --include=*.cpp --include=*.h lmcache csrc 2>/dev/null | head -8", "description": "Check direct copy path size threshold"}`

**回复：**

跑完了。整单花了 **$39.33**（上限 $55），用时 104 分钟。中间没断过，各项机械检查都通过了，我抽查的 4 条说法也都和源码对得上。

**画布**：`/root/canvas-duo-runs/LMCache-20261123-2359/LMCache-canvas.html`

这是一个文件，浏览器直接打开就行，桌面和手机都能看。**它里面嵌着仓库源码**，按仓库本身的保密级别对待，不要上传或公开分享。

## 每步花费（照 report.md 抄）

| 步骤 | 模型 | 花费 | 用时 |
|---|---|---|---|
| 顶层研究员 | opus | $10.30 | 20 分 |
| 顶层讲解员 | opus | $3.54 | 14 分 |
| 传输模块：研究 / 讲解 | sonnet / opus | $3.71 / $4.24 | 15 / 17 分 |
| L1 存储：研究（含一次 $0.12 返工）/ 讲解 | sonnet / opus | $3.62 / $3.99 | 11 / 18 分 |
| 调度侧连接器：研究 / 讲解 | sonnet / opus | $3.30 / $4.79 | 12 / 20 分 |
| 顶层按下层意见返工 | opus | $1.84 | 1 分 |

## 画布里讲了什么

**顶层**是一张机器图，7 个框分两组：
- vLLM 那边：调度侧连接器、worker 侧连接器、GPU 显存
- LMCache 服务进程：查找模块、滚动哈希、传输模块、L1 存储

推演一共 12 步，用一组具体的数从头走到尾：
- 请求 A（1000 token）进来，只查前 768 个 token，全部没命中；把这 768 个 token 的 KV 存下来，分成 3 个块、共约 108 MiB。
- 请求 B（1200 token，前 900 和 A 一样）进来，命中前 3 块，每块上一把读锁；取回这 768 个 token 的 KV，拷完就放锁；B 自己只需要算剩下的 432 个 token。

**往下钻了 3 个框**：
- **传输模块**：讲 KV 真正怎么被搬：服务端自己的 CUDA 流、跨进程 event、亲和线程、拷完后的回调收尾。
- **L1 存储**：讲存下来的对象怎么从暂存转成常驻、读锁为什么是带期限的租约、淘汰怎么跑、内存不够时为什么整批失败。
- **调度侧连接器**：讲每个请求的账本怎么跨好几个调度步接力、「能取多少 / 要存多少」怎么算、查询的回复怎么异步回来。

另外有几个框直接挂了带批注的源码页，包括 worker 侧、查找模块和滚动哈希。

## 机械检查

- **底稿核对**：4 份全部通过。L1 那份第一次没过，自动返工一次后通过。
- **构建**：`build --strict` 有 0 条 WARN，没有空着的槽位。
- **交互自检**：`shot_scene --all` 是 596 PASS / 0 FAIL，排版问题 0 条。

## 我自己的抽查（4 条，都对上了）

- **读锁 300 秒、写锁 600 秒**：对上，见 `lmcache/v1/distributed/config.py:429-433`。
- **淘汰在用量到 80% 时触发，删掉约 20%**：对上，见 `config.py:491`，以及 `l2_adapters/config.py:242-243`。
- **「转正」就是 `base.py:649` 那一行**：对上，`lmcache/v1/distributed/l1_manager/base.py:649` 是 `self._objects[key] = entry`。
- **小于 32 KiB 的块不走直接拷贝**：对上，见 `object_group_transfer/direct_copy.py:42`，`_DIRECT_COPY_MIN_BLOCK_BYTES = 32 << 10`。

## 讲解员自己承认的短处

**手机上不好读**：
- L1 那张图很长（1185 高），要拖很多次才看完。
- 线上的「为什么 / 易混点」短句被压到 3–8 个字，比如「缺≠败」「成功≠留下我的」，读者得点开卡片才看得懂。
- 调度侧那张图的第一屏不是中间的账本，读者一开始可能抓不住重点。

**顶层**：
- 一把锁从上到放，分散在 3 条线、2 步推演里，没有一处集中讲清。
- 「为什么」15 条、「易混点」14 条，信息密度偏高。

**推演里容易绊住读者的地方**：
- **L1**：
  - 第 8 步「B 崩溃、锁自己失效」是另一条时间线，读者可能以为它接在第 7 步后面发生。
  - 「B 缺口」那一步的设定和 LRU 的淘汰顺序不太自然。
  - 「池里只剩 2 个槽」是讲解员为了演示自己补设的值，图上标了「设」。
- **调度侧**：15 步偏长。图上默认显示的值是推演中间某一刻的快照，这一点只写在图注里，不看图注容易误会。

**推断**：
- 很多「为什么」标的是「推断」。其中 3 条原本有设计文档（`.rst`）作依据，但舞台不认 `.rst` 锚点，只好降成推断，出处写在文字里。
- 有几件事底稿也没查到，比如 300 秒 / 600 秒为什么这么定、淘汰为什么是每秒轮询一次。画布上照实写了「没查到」。

**顶层被纠正过 11 处**，三个下层讲解员都报了问题，例如：
- 传输模块那张卡里，RETRIEVE 的先后顺序写反了：代码里是先等 event，再读对象。
- 查找 L1 时说「逐个上锁」，实际是按行批量调用。

顶层讲解员逐条核实后全部改了。改完 WARN 0，交互 11 PASS / 0 FAIL。

## 舞台问题（要回 code-canvas 仓库修）

这些写在 `content/framework-issues-*.md`，是工具本身的问题，不是 LMCache 的：
- 锚点不认 `.rst`、`.cpp`、`.cu`、`.cuh`、`.pyi`，C++ 实现和设计文档在说明卡里没法直接引。
- 连线不会绕开挡在中间的框，手机上跨框的线只能走屏幕两侧，挤掉了框宽和线上文字的空间。
- 线上的英文标签会从词中间断行，比如 `QUER / Y`。
- 想表达「同一个框被反复调用」时，没地方画回环。
- 没有办法在图上标明「全景显示的值对应推演的哪一刻」。
- 页面底部「下一页」那张卡有错位：上半是个空的蓝色描边条，标题落在它下面。

如果你对画布有改版意见，告诉我原话，我记下来，由你带回 code-canvas 仓库处理。改版在这里跑不了。


> 结果：success，累计花费 $1.078（resume 时 total_cost_usd 是累计数），本段 6 轮、41 秒

## 合计

- 调度员两轮累计 **$1.08**；预检 probe 另花 $0.144（独立进程，不在上面的累计里）。
- run.py 整单 $39.33、104 分钟，见 `report.md`。
