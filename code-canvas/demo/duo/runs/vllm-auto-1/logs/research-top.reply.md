底稿写完了，三类机械核对全部通过。

**1. 路径和规模**

`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research/manuscript.md`

约 4.4 万字符，其中汉字约 5900，其余大多是锚点路径和英文引文，在 3 万字以内。共 10 节：一屏结论、主路选择、9 个部件总览、交接表、线程清单、带具体数值的 10 步推演、9 张部件说明卡、旁路、没查到为什么、困惑点汇总。核对脚本和草稿在同目录的 `work-research-top/` 下。

**2. 研究范围**

- **读过的文件**：
  - 入口：`cli/serve.py`、`launchers/api_server/entry.py`
  - HTTP 层：chat 的 `api_router.py` / `serving.py` / `protocol.py`、`serve/utils/api_utils.py`
  - 渲染：`renderers/{online_renderer,base,hf}.py`
  - 前端引擎：`v1/engine/{async_llm,input_processor,core_client,core,output_processor,detokenizer,utils,__init__}.py`
  - 调度：`v1/core/sched/{scheduler,async_scheduler,utils}.py`
  - 执行：`v1/executor/{abstract,uniproc_executor}.py`、`v1/worker/gpu_worker.py`、`v1/worker/gpu/{model_runner,async_utils}.py`
  - 配置：`config/{vllm,scheduler,parallel,cache}.py`
  - 设计文档：`arch_overview.md`、`multiprocessing.md`、`model_runner_v2.md`
  - 两个测试文件
- **git 历史**：详细看了约 12 个提交，另有几个只看了标题。
- **从 git 历史挖到的三个最有价值的"为什么"**：
  - `dc264bcea1` 解释了 abort 为什么同时写进两个队列，以及为什么每一步先处理 abort、但不先处理新请求（提交说明原话是"preferable for latency to process model outputs before new incoming requests"）。
  - `cbc77173ed` 说明"等 GPU 时继续收请求"这套逻辑是为多进程执行器做的。由此确认：默认单卡路径用不上它，会直接阻塞等结果。
  - `f790068600` 和 `79c7e09235` 是请求 ID 加 8 位随机后缀、以及关闭开关的来历。不过两者正文都是空的，只能引标题。

**3. 机械核对**

| 项目 | 总数 | 通过 |
|---|---|---|
| 锚点（出现次数，不重复的 300 个） | 326 | 326 |
| commit 哈希 | 11 | 11 |
| 引文 | 59 | 59 |

- **锚点**：先自动从锚点前文提取标识符去目标行匹配，匹配不上的约 105 个改成人工写的必含字符串清单再核。
- **哈希**：都存在；凡写了"改动某文件"的 9 处，也确认该提交确实改过这个文件。
- **引文**：每条都在同一行所引的文件行段或提交说明里逐字找到了。为避免误核，非原文的「」已全部改成中文引号“”。

**4. 来源等级（"为什么"条目，人工计数）**

| 等级 | 条数 |
|---|---|
| 【注释】 | 约 27 |
| 【提交】 | 8 |
| 【文档】 | 7 |
| 【测试】 | 2 |
| 【推断】 | 7（另有 2 处【推断】是对"怎么做"的推断） |
| 【观点】 | 3（可改进处） |

【推断】约占"为什么"条目的 14%。

**5. 没查到为什么**

- 为什么把 API Server 和 EngineCore 拆成两个进程：文档只说"separate concerns and maximize throughput"，"避免 GIL 争用"是我的推断。
- 为什么默认开启 async scheduling：对应提交正文为空。
- 为什么 `async_scheduling_depth` 默认是 2 而不是更大。
- 为什么输出信箱从队列改成"单槽 + 合并"：提交只有标题。
- 输出处理分块大小怎么定的：提交正文为空。
- 请求 ID 随机化是为了修哪类具体故障：代码只说"ensure uniqueness"。
- 为什么 stop string 放在前端判：没有找到注释或提交说明。

**6. 我认为最弱的地方**

- **很多"为什么"只有半截**：大量关键提交正文为空，宏观层面（拆进程、默认开 async scheduling、信箱设计）的理由只能停在文档里的笼统说法或推断上。
- **推演靠代码推读，没有跑过**：例子里的 token 数和生成文字都是设的。"第 2 轮待算量 = 20 + 1 − 20 = 1"、"第 N 步的结果在第 N+1 步发射后才取出"这些时序是读代码推出来的。
- **请求结束那一刻没讲透**：async scheduling 下请求停止时，已经排进 GPU 的多余那一步怎么被丢弃，我只给了推断，没有逐行追。
- **底稿和官方文档有冲突**：我判断单卡时 Worker 住在 EngineCore 进程里（`uni` 后端），这与 `arch_overview.md` 的进程表矛盾。代码依据是清楚的，但讲解员需要知道两边说法不一致。