底稿已写完：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research/manuscript-gpu.md`。约 5900 个汉字，连代码和引文共约 3.5 万字符，不到 1.5 万字的上限。

**1. 底稿规模**
- 结构：§0 一屏结论，§1 部件与状态（供画图），§2–§8 七个问题，§9 六段源码页，§10 没查到为什么，§11 读者困惑点，§12 可改进处。
- 例子沿用上一趟和调度器底稿：`chatcmpl-9f1c…-a1b2c3d4`、20 个 prompt token、`block_size=16`、S1–S8 的步数都没动。
- 新补的值都标了“设”：槽 1023、block 编号 `[5,6]`、邻居请求 B 的场景、结构化输出场景。
- 本例 S1 和 S2 各一张形状表，覆盖 input_ids、positions、seq_lens、slot_mapping、CUDA graph 档位、`post_update` 前后的状态。

**2. 研究范围**
- 读了 `model_runner.py` 的状态同步、`prepare_inputs`、`execute_model`、`sample_tokens`。
- 读了 `states.py`、`input_batch.py`（几个 Triton 内核）、`block_table.py`、`buffer_utils.py`、`async_utils.py`、`cudagraph_utils.py`、`dp_utils.py`、`sample/sampler.py`。
- 读了 `core.py` 的 `step_with_batch_queue`、`async_scheduler.py`、`uniproc_executor.py`、`worker_base.py`、`model_runner_v2.md`。
- 看了约 3900 行 git 日志，细看了 10 个提交，其中 `62095e82c1`、`ae0ce1be27`、`d3e69fd671` 三个读了 diff。
- 仓库里大部分相关提交正文为空，所以多数【提交】只有标题。

**从 git 历史挖出的最有价值的三条“为什么”**
- **每步独有的 `copy_event`**：`62095e82c1` 的 diff 显示，以前 runner 上有一个共享的 `output_copy_event`，传给每个 `AsyncOutput`；修复后每个 `AsyncOutput` 自己新建。队列里同时有 2 个在飞的输出，共用一个 event 会让后一步的 record 覆盖前一步，这个原因是我从 diff 推的，已标【推断】。
- **`blocking=True` 的 event**：`d3e69fd671` 加了 `blocking=True` 和「Blocking (sleep) event…」那条注释。
- **前向和采样拆开**：`sample_tokens` 是 `0cdbe7b744`（async scheduling 与结构化输出兼容）引入的。`core.py:727-732` 的代码顺序还显示，即使不开 async，语法掩码也能和 GPU 前向重叠。

**3. 机械核对**
- 锚点 207 个（去重 191），全部通过。
- 提交哈希 11 个，全部存在；我声称有改动的文件，核对结果都在改动列表里。
- 引文 68 条，全部能在所引文件或提交标题里逐字找到。
- 引文检查只验证“引文出现在某个被引文件里”，不验证它在不在你标的那一行。这一点靠我对关键锚点人工逐条看过。

**4. “为什么”的来源等级**
- 【注释】约 30 条，【提交】约 11 条，【文档】约 7 条，【测试】0 条，【推断】约 13 条，【观点】4 条。
- 【推断】约占“为什么”总数的 20%。

**5. 没查到为什么**
- `free_indices.pop()` 为什么弹末尾：首个请求拿到最大槽号，只能从代码推出，没找到是否有意。
- `copy_stream.wait_stream(main_stream)` 为什么必须：无注释。
- 前向结果为什么藏在 runner 的单槽字段里，而不是直接返回：只找到结构化输出的动机。
- S1 padding 到 24：这是我按网格公式手算的，没有测试逐值断言。
- `seq_lens_cpu_upper_bound` 里“上界”的精确语义：相关提交正文为空。
- `_WRITE_BLOCK_SIZE=1024` 的取值理由：没找到。

**6. 底稿最弱的地方**
- 本例最后那个 `total_num_scheduled_tokens == 0` 的“空步”（把 finished id 送到 worker 并释放槽位），是我从 `has_requests()` 的定义加 worker 的早退分支拼出来的，标了【推断】，没有注释或测试直接描述它。
- 槽位复用不怕 GPU 还在读旧行，依赖“同一条 stream 内核按序执行”这条 CUDA 语义。这条也是【推断】，代码里没有对应注释。
- 例子里的 CUDA graph 档位（S1 走 PIECEWISE、padding 到 24，S2 走 FULL）基于“默认配置、单卡”的设定。实际抓取网格受平台默认影响，我没逐平台核对。
- 流水线并行、投机解码、DP、LoRA、多模态只在附录点名，没有展开。
- 我对 `all_token_ids` 默认放在 pinned 主机内存（GPU 经 UVA 访问）的描述，依据是 `states.py` 的注释和构造参数，没有实测。