Verification is done and everything passes; I'm assembling the final report.

**1. 底稿**：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research/manuscript-sched.md`，约 5900 个汉字。去掉代码和锚点后全文约 1.9 万字符，含英文原话。脚本和草稿在同目录下的 `work-research-sched/`。

**2. 研究范围**
- 读了 `scheduler.py` 的 `schedule`、`_preempt_request`、`_update_after_schedule`、`update_from_output`、`_free_request` 等主干，以及 `async_scheduler.py`、`utils.py`、`request_queue.py`、`output.py`。
- 读了 `kv_cache_manager.py`、`block_pool.py`、`single_type_kv_cache_manager.py` 的相关段，以及 config、`arg_utils.py` 的默认值。
- 读了 `arch_overview.md`、`optimization.md`、`prefix_caching.md`，以及 `test_scheduler.py`、`test_async_scheduler.py` 的相关用例。
- 查了约 20 个提交（翻了 478 条涉及调度器的提交，筛正文），其中 11 个写进了底稿。**绝大多数提交正文是空的，只能看 diff 和标题。**
- 从 git 历史里挖出的三条最有价值的：
  1. `732e49d442`：默认 block_size 已从 16 改成 64。旧资料里的"默认 16"在这个 HEAD 不成立；真实值还会被后端覆盖。
  2. `f0d610a8ae`：前缀命中"最后一个 token 必须重算"，原来是一个特判，被换成通用的 `max_cache_hit_length`。
  3. `6c5af09b39`：V1 首个提交就已有"抢占 = 释放、归零、放回队首"和 `if not preempted_reqs` 守卫，没有换出路径。这是历史事实，不是理由。
- 另有 `c64b15cde5`（长 prefill 单请求不封顶）、`a0c092ee72`（占位符下溢崩溃）等提交的变更点也写进了底稿。

**3. 机械核对**（脚本在 `work-research-sched/`，已用 `python3 -I` 跑过）
- 锚点：308 处提及（252 个不同锚点），308 通过。
  - 其中 74 处靠我手写的"必含子串"表核对，其余按上下文里的标识符自动匹配。
  - 这是"锚点所在行含有所述符号"的检查，不能证明我对该行含义的解读是对的。
- 提交哈希：17 处提及（11 个不同哈希）全部存在，文中声称改动的文件都在该提交的改动列表里，声称"正文为空"的都确实为空。
- 引文：92 条英文原话全部能在邻近锚点所指的文件（或提交标题）里逐字找到。
- 非原文的强调用语我已全部改用 “ ” 引号，「」只留给逐字引文。

**4. "为什么"的来源等级**（第 2–10 节）：【注释】43、【文档】18、【测试】11、【提交】7、【推断】11，【观点】4（在可改进处一节）。【推断】约占 12%。

**5. 没查到为什么**（见底稿 §10）
- "本步抢占过就不再排 waiting"的理由。
- waiting 队首分不到 block 就整体 `break` 的理由。
- V1 里重算为何比换出开销低（文档只有一句"开销更低"）。
- FCFS 为什么踢最晚进入的而不是已算最少的。
- 8192 / 16384 / 2048 三档默认值的依据。
- EOS 导致多排一步是否有意保留。
- 三个较新旋钮的设计动机：`max_num_active_seqs`、`scheduler_reserve_full_isl`、`cache_aware_admission_window`。
- 另外明说没读的：KV 连接器 / P-D、Mamba / 混合 KV、滑窗、多模态编码预算、DP 节流、流式输入、投机解码的接受逻辑。

**6. 最弱的地方**
- **§8 的逐步推演表是我按代码公式手算的。** 测试只断言了总排出 token 数（`prompt + max_tokens − 1`），没有逐步断言，我在文中已标明。"EOS 请求的多排一步被丢弃"是我读代码推出的，EOS 和 abort 走同一条分支这一点没有注释或测试直接证明。
- **本例的 block 数取决于 block_size。** 文中设 16 才能看到 2 个 block，默认 64 则只有 1 个。我没法确认实际机器上最终用哪个值，只指到了启动日志。
- **"为什么先排 running"、"重算为何更便宜"主要靠【推断】。** 文档只给了结论，没给机制。
- **`schedule()` 里连接器、Mamba、SWA、编码器等旁支我没读透**，只在源码页建议里标了折叠范围。