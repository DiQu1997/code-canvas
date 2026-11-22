# 调度器（Scheduler）内部：一步是怎么排出来的（第二层底稿）

> 仓库 vllm，HEAD = `0e3c8dbdae`。锚点均为仓库相对路径 `文件:行号`，已在 HEAD 上核对。
> 来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。「」内为逐字原文。
> 接着上一趟（顶层）底稿；顶层已讲清的（EngineCore 忙循环、batch_queue 深度 2、`update_from_output` 处理上上一轮）直接写“见上一趟 §x”。
> 例子沿用上一趟：prompt 20 token、生成 7 个（最后一个是 `<eos>`）、`max_tokens=8`。新补的值一律标“设”。

---

## 0. 一屏结论

调度器是 EngineCore 进程里的一个**普通 Python 对象**，由忙循环的同一个线程调用，所以它内部的状态不需要锁（见上一趟 §4「谁拥有什么」：调度器状态只被 EngineCore 主线程改）。它对外只有三个动作：

1. **`add_request`**：新请求进 `waiting` 队列。
2. **`schedule()`**：每步一次，决定“每个请求这一步算几个 token、KV 放哪几个 block”，返回 `SchedulerOutput`。
3. **`update_from_output()`**：GPU 结果回来后，把 token 追加到请求上、判停、释放 KV、生成 `EngineCoreOutput`。

核心思想只有一句：**每个请求只有两个数——“已有 token 数”和“已算 token 数”，调度器每步给每个请求分配 token，让后者追上前者**。没有 prefill / decode 两个阶段。分块 prefill、前缀缓存、投机解码、重算式抢占，都只是这个“追赶”循环的不同取值。

一步 `schedule()` 的固定顺序：

```
设预算 → 先排 RUNNING（每个请求：算要几个 token → 要 KV block → 不够就抢占别人）
      → 若本步没有发生抢占，再排 WAITING（查前缀缓存 → 算要几个 token → 分 block → 进 running）
      → 打包 SchedulerOutput → 推进“已算”并记“占位符”
```

---

## 1. 框里有什么（供画机器图）

### 1.1 部件与它们持有的状态

| 部件 | 是什么 | 持有 / 读写的状态 | 锚点 |
|---|---|---|---|
| `Scheduler`（实际是子类 `AsyncScheduler`） | 决策者 | `requests`（id→Request 总表）、`waiting`、`running`、两个标量预算 | `vllm/v1/core/sched/scheduler.py:231`、`vllm/v1/core/sched/scheduler.py:240`、`vllm/v1/core/sched/scheduler.py:251` |
| `waiting` 队列 | 还没进 running 的请求（新来的 + 被抢占的） | FCFS 时是 deque，PRIORITY 时是堆 | `vllm/v1/core/sched/request_queue.py:75`、`vllm/v1/core/sched/request_queue.py:131` |
| `running` 列表 | 已拿到 KV、正在生成的请求，**按进入顺序排** | `list[Request]` | `vllm/v1/core/sched/scheduler.py:251` |
| `Request` | 一个请求的全部可变状态 | `num_computed_tokens`（已算）、`num_tokens`（已有）、`num_output_placeholders`（占位符）、`num_in_flight_tokens`（在飞）、`status` | `vllm/v1/request.py:196`、`vllm/v1/request.py:172`、`vllm/v1/request.py:185` |
| `KVCacheManager` | 账房：按请求管 block 表、查前缀缓存；自己只做门面，具体账由下面的 `coordinator`（`vllm/v1/core/kv_cache_manager.py:164`）和各 `SingleTypeKVCacheManager` 记 | 每请求的 block 列表 `req_to_blocks`（在 `SingleTypeKVCacheManager` 里，`vllm/v1/core/single_type_kv_cache_manager.py:234`）；`watermark_blocks` | `vllm/v1/core/kv_cache_manager.py:278`、`vllm/v1/core/kv_cache_manager.py:391`、`vllm/v1/core/kv_cache_manager.py:200` |
| `BlockPool` | 仓库：所有物理 block 的 `ref_cnt`、空闲队列、“哈希→block”表 | `free_block_queue`、`null_block`（block 0 占位）、`cached_block_hash_to_block` | `vllm/v1/core/block_pool.py:107`、`vllm/v1/core/block_pool.py:156`、`vllm/v1/core/block_pool.py:164` |
| `SchedulerOutput` | 交给 GPU 侧的“本步工单” | `scheduled_new_reqs`（首次发完整数据）、`scheduled_cached_reqs`（只发增量）、`num_scheduled_tokens` | `vllm/v1/core/sched/output.py:241`、`vllm/v1/core/sched/output.py:245`、`vllm/v1/core/sched/output.py:249` |

另有两条旁路队列 `kv_holding_waiting` / `async_kv_waiting`（给 KV 传输 / P-D 分离用，默认空），点名即可：`vllm/v1/core/sched/scheduler.py:244`、`vllm/v1/core/sched/scheduler.py:247`。

### 1.2 谁把什么交给谁（连线）

- 上游 `EngineCore.add_request` → `Scheduler.add_request` → `waiting`（`vllm/v1/core/sched/scheduler.py:3112`、`vllm/v1/core/sched/scheduler.py:3132`，走 `_enqueue_waiting_request`，`vllm/v1/core/sched/scheduler.py:2826`）。
- `schedule()` →（查）`KVCacheManager.get_computed_blocks`（`vllm/v1/core/kv_cache_manager.py:278`）→（要）`allocate_slots`（`vllm/v1/core/kv_cache_manager.py:391`）→ `BlockPool.get_new_blocks` / `touch`（`vllm/v1/core/block_pool.py:681`、`vllm/v1/core/block_pool.py:774`）。
- `schedule()` → `SchedulerOutput` → 上一趟的 `execute_model`；回头 `update_from_output(scheduler_output, model_runner_output)`（`vllm/v1/core/sched/scheduler.py:2334`）。
- 请求结束 → `_free_request` → `KVCacheManager.free` → `BlockPool.free_blocks`（`vllm/v1/core/sched/scheduler.py:3220`、`vllm/v1/core/sched/scheduler.py:3279`、`vllm/v1/core/block_pool.py:798`）。

### 1.3 一个关键的“时间不连续”

`schedule()` 在 **GPU 结果回来之前**就把 `num_computed_tokens` 加上去了（`vllm/v1/core/sched/scheduler.py:1892`）。所以调度器看到的“已算”其实是“已排”。同一个请求，下一步 `schedule()` 可能在上一步的结果还没回来时就发生（见上一趟 §5 步骤 7）。后面“占位符”“EOS 多排一步”“抢占”都由这一点派生。

---

## 2. 问题一：一步的 token 预算是多少？满了怎么办？

**怎么做**

- 预算是 `schedule()` 开头的两个局部变量：`token_budget = self.max_num_scheduled_tokens` 和 `input_budget = ...max_num_batched_tokens`（`vllm/v1/core/sched/scheduler.py:728`、`vllm/v1/core/sched/scheduler.py:731`）。`max_num_scheduled_tokens` 没单独配时就等于 `max_num_batched_tokens`（`vllm/v1/core/sched/scheduler.py:147-150`）。无投机解码时 `draft_slots=0`，两个数完全一样（`vllm/v1/core/sched/scheduler.py:730`）。
- 每排一个请求就扣：`token_budget -= num_new_tokens`（running 在 `vllm/v1/core/sched/scheduler.py:991`，waiting 在 `vllm/v1/core/sched/scheduler.py:1621`）。
- 结尾有硬断言：`assert total_num_scheduled_tokens <= self.max_num_scheduled_tokens`（`vllm/v1/core/sched/scheduler.py:1674`）。
- **满了之后**：running 循环的条件是 `token_budget > 0`（`vllm/v1/core/sched/scheduler.py:780`）；waiting 循环里 `token_budget <= 0` 就不再取新请求（`vllm/v1/core/sched/scheduler.py:1075`）。没排上的请求**原地不动**，下一步再来——running 里没排上的仍留在 `running`，waiting 里的仍在队里。每个请求单次的 token 数也被预算截断：`min(num_new_tokens, token_budget, input_budget - draft_slots)`（`vllm/v1/core/sched/scheduler.py:831-833`）。
- **默认值**（按设备分档，`vllm serve` 即 `OPENAI_API_SERVER` 场景）：显存 ≥160GB → 16384（`vllm/engine/arg_utils.py:3004`）；≥70GB 且不是 A100/GB10 → 8192（`vllm/engine/arg_utils.py:3018`）；其余 → 2048（`vllm/engine/arg_utils.py:3028`）。`max_num_seqs`：≥70GB 的两档都是 1024（`vllm/engine/arg_utils.py:3008`、`vllm/engine/arg_utils.py:3022`），其余 256（`vllm/engine/arg_utils.py:3032`）。TPU / CPU 另有各自的值（`vllm/engine/arg_utils.py:3036-3062`），不展开。config 里的类默认 2048 / 128 只是“方便测试”，真正值在 `create_engine_config` 里按上面填（`vllm/config/scheduler.py:40-42`、`vllm/engine/arg_utils.py:3193-3206`）。
- **running 数量上限**是另一个旋钮：`max_num_seqs`（`vllm/v1/core/sched/scheduler.py:139`），waiting 循环入口检查 `num_running >= self.max_num_active_reqs`（`vllm/v1/core/sched/scheduler.py:1075`）。`max_num_active_seqs` 是后加的、可以比前者更小的“准入上限”，默认 `None`＝等于前者（`vllm/v1/core/sched/scheduler.py:142-146`）。

**为什么**

- 【文档】预算是调度器的主线：「The scheduler is **token-budget-based**: each step, it fills a budget of」（`docs/design/arch_overview.md:140`）。
- 【文档】预算大小是延迟/吞吐旋钮：「Smaller values (e.g., 2048) achieve better ITL because there are fewer prefills slowing down decodes.」与「Higher values achieve better time to first token (TTFT) as you can process more prefill tokens in a batch.」（`docs/configuration/optimization.md:102-103`）。
- 【注释】A100 单列一档：「NOTE(Kuntai): Setting large `max_num_batched_tokens` for A100 reduces」（`vllm/engine/arg_utils.py:2997`）；【提交】对应 `9112155283`「[Perf] Use small max_num_batched_tokens for A100 (#17885)」，改动 `vllm/engine/arg_utils.py`，提交正文为空，没有更细的数据。
- 【注释】其余硬件的数是没调过的：「TODO(woosuk): Tune the default values for other hardware.」（`vllm/engine/arg_utils.py:3025`）。
- 【文档】`max_num_seqs` 的 config 注释说它「sizes the model runner」（`vllm/config/scheduler.py:71`），而 `max_num_active_seqs` 是“准入”（`vllm/v1/core/sched/scheduler.py:140`：「Admission into RUNNING. Separate from max_num_running_reqs, which」）。提交 `f730a93d2b`「[Scheduler] Add --max-num-active-seqs to cap RUNNING admission (#56758)」，正文为空。
- 【推断】为什么预算用“token 数”而不是“请求数”：GPU 一步的计算量约正比于 token 数，prefill 一个请求可能几千 token、decode 只有 1 个，按请求数限不住一步耗时。依据：文档把 ITL / TTFT 都挂在 `max_num_batched_tokens` 上（`docs/configuration/optimization.md:102-103`）。

---

## 3. 问题二：running 和 waiting 各放什么？为什么先排 running？

**各放什么**

- `waiting`：**还没有 KV 的请求**——新来的（`add_request`，`vllm/v1/core/sched/scheduler.py:3132`）和被抢占的（`_preempt_request` 末尾放回队首，`vllm/v1/core/sched/scheduler.py:1876`）。
- `running`：**已分到 KV block 的请求**，不管它是在 decode 还是 prefill 到一半（分块）。进入 running 的那一刻在 waiting 循环里：`pop_request` + `self.running.append(request)`（`vllm/v1/core/sched/scheduler.py:1596`、`vllm/v1/core/sched/scheduler.py:1598`），同时 `status = RUNNING`（`vllm/v1/core/sched/scheduler.py:1623`）。
- 所以 running 是**按进入顺序**排的：越靠后越晚进。FCFS 抢占就取 `self.running[-1]`（`vllm/v1/core/sched/scheduler.py:923`）。

**为什么先排 running**

- 【文档】「With chunked prefill enabled, the scheduling policy prioritizes decode requests. It batches all pending decode requests before scheduling any prefill operations.」（`docs/configuration/optimization.md:91`），并给出两个好处：「It improves inter-token latency (ITL) and generation decode because decode requests are prioritized.」（`docs/configuration/optimization.md:95`）、「It helps achieve better GPU utilization by locating compute-bound (prefill) and memory-bound (decode) requests to the same batch.」（`docs/configuration/optimization.md:96`）。
- 【推断】结构上的第二个理由：running 请求已经占着 KV，把它们先排完，剩下的预算和 KV 才轮到新请求；反过来则新请求会把老请求饿住、或逼老请求被抢占。依据：waiting 循环读的 `token_budget` 是 running 循环扣过的（`vllm/v1/core/sched/scheduler.py:991`、`vllm/v1/core/sched/scheduler.py:1075`）。没有注释或提交明说这一点。
- 顺序本身从 V1 第一个提交就是这样：`6c5af09b39`「[V1] Implement vLLM V1 [1/N] (#9289)」的 diff（文件当时在旧路径 vllm/v1/core/scheduler.py，HEAD 上已改名为 sched/ 目录）里已经是先 running、后 waiting，并且已有 `if not preempted_reqs:`；该提交正文为空。这是历史事实，不是理由。

**一个容易错过的守卫：本步发生过抢占，就不再排 waiting**

- `if not preempted_reqs and self._pause_state != PauseState.PAUSED_ALL:`（`vllm/v1/core/sched/scheduler.py:1041`）。
- 【推断】理由：刚因为 KV 不够踢了人，说明显存是紧的；这时再把 waiting 里的请求放进来，要么分配失败，要么又逼出下一次抢占（抖动）。依据：守卫的位置恰在“抢占”之后。仓库里没有注释说明，见 §10。

**两个循环对“排不上”的态度不同**（读者会困惑）

- running 里某请求 `num_new_tokens == 0`：用 `continue`，跳过它去排后面的——【注释】「NOTE(woosuk): Here, by doing `continue` instead of `break`,」「we do not strictly follow the FCFS scheduling policy and」「allow the lower-priority requests to be scheduled.」（`vllm/v1/core/sched/scheduler.py:889-892`）。
- waiting 里队首分不到 block：用 `break`，**后面的请求也不排**（`vllm/v1/core/sched/scheduler.py:1508-1515`）——严格的 FCFS，防止小请求一直插队让大请求饿死。【推断】依据：与上面 running 的注释形成对照；仓库没有对 waiting 侧 `break` 的注释。
- waiting 里“这一步确实排不了、但不是显存问题”的（如多模态编码预算用尽）则被 `skip_request` 暂放，循环结束时放回队首，注释「The request cannot be scheduled. Requeue it so it does」（`vllm/v1/core/sched/scheduler.py:1449`）、「re-queue requests skipped in this pass ahead of older skipped items.」（`vllm/v1/core/sched/scheduler.py:1656`）。

**队列的两种策略**：`--scheduling-policy` 默认 `fcfs`（`vllm/config/scheduler.py:195`），另有 `priority`。FCFS 队列是 deque、`prepend_request` = `appendleft`（`vllm/v1/core/sched/request_queue.py:75`、`vllm/v1/core/sched/request_queue.py:94`）；PRIORITY 是堆，抢占取 `max(running, key=(priority, arrival_time))`（`vllm/v1/core/sched/scheduler.py:917-920`）。【文档】`docs/design/arch_overview.md:149-155` 对两种策略的抢占对象有同样说明。

---

## 4. 问题三：KV block 怎么分配？本例各占几个？

**机制（分页 + 按需要）**

- KV 按固定大小的 block 分页。每次给请求排 `num_new_tokens` 个 token 前，先调 `allocate_slots(request, num_new_tokens, ...)`（running：`vllm/v1/core/sched/scheduler.py:899`；waiting：`vllm/v1/core/sched/scheduler.py:1489`）。
- `allocate_slots` 内部：`num_tokens_need_slot = min(已算 + 新排 + lookahead, max_model_len)`（`vllm/v1/core/kv_cache_manager.py:562`）→ 算还差几个 block（`vllm/v1/core/single_type_kv_cache_manager.py:322` 用 `cdiv(num_tokens, self.block_size)`）→ 空闲块不够就**返回 `None`**（`vllm/v1/core/kv_cache_manager.py:597-599`）→ 够就从空闲队列头部弹出（`vllm/v1/core/block_pool.py:696`）。
- 返回 `None` 对调度器就是“分不到”：running 里触发抢占循环（§6），waiting 里 `break`。
- 分到之后，只把**已满且已确认**的 block 登记进前缀缓存表：「Therefore, we cap the number at `request.num_tokens`, ensuring only」（`vllm/v1/core/kv_cache_manager.py:631`），`cache_blocks` 在 `vllm/v1/core/kv_cache_manager.py:637`；只算整块：`min(num_tokens, num_hashed_tokens) // self.block_size`（`vllm/v1/core/single_type_kv_cache_manager.py:633`）。
- 默认全局有一个 `null_block`（block 0），不分给任何请求，用作占位（【注释】「The ref_cnt of null_block is not maintained, needs special care to」，`vllm/v1/core/block_pool.py:162`；【测试】`tests/v1/core/test_scheduler.py:2495-2496`「NOTE(woosuk): The actual number of available blocks is 10 instead of 11」「because block 0 is reserved as the null block.」）。

**block 大小——这里有一个版本陷阱**

- `CacheConfig.DEFAULT_BLOCK_SIZE = 64`（`vllm/config/cache.py:185`）。【提交】`732e49d442`「[Core] Change default --block-size from 16 to 64 (#63231)」，改动 `vllm/config/cache.py`，正文为空。旧资料里的“默认 16”在这个 HEAD 不成立。
- 但最终值可能被后端覆盖：用户没指定 `--block-size` 时，平台层按后端偏好重选，并打日志 `Setting kv cache block size to %d for %s backend(s).`（`vllm/platforms/interface.py:800-812`）。所以“本机实际 block_size”要看启动日志，不能从 config 默认值断言。

**本例（20 prompt + 7 生成）——设 `block_size=16` 以便看得见；若按 config 默认 64，全程只要 1 个 block**

| 时刻 | 需要的 token 槽位 | block 数（bs=16） | 说明 |
|---|---|---|---|
| 第 1 步排 20 个 prompt token | 20 | 2 | block 0 装 16 个，已满 → 立刻登记进前缀缓存；block 1 装 4 个，不满、不登记 |
| 之后每步 +1 token | 21 … 27 | 2 | 到第 32 槽前不需要新 block |
| 本例总共排出的 token | 20 + 7 = 27（含 §8 的“多排一步”） | 2 | 峰值 27 < 32，不会触发第 3 个 block |

（数字推导见 §8；总排出 token 数 = prompt + max_tokens − 1 由【测试】`tests/v1/core/test_async_scheduler.py:44` 与 `tests/v1/core/test_async_scheduler.py:65` 的断言 `expected_total_num_scheduled_tokens += req0.num_prompt_tokens + max_tokens - 1` / `assert total_num_scheduled_tokens == expected_total_num_scheduled_tokens` 佐证。）

**释放顺序：为什么要倒着放**

- 请求结束，`free` 把 block **逆序**还给空闲队列：【注释】「We free the blocks in reverse order so that the tail blocks are evicted」（`vllm/v1/core/kv_cache_manager.py:643`）、「Free blocks in reverse order so that the tail blocks are freed first.」（`vllm/v1/core/single_type_kv_cache_manager.py:736`）。
- 【文档】理由：「This is because the last block of a request must hash more tokens and is less likely to be reused by other requests. As a result, it should be evicted first.」（`docs/design/prefix_caching.md:198`）。
- 空闲队列两端的用法不同：带哈希的（可被前缀缓存命中）放队尾、最后才被复用——【注释】「FIFO reuse of cached blocks for LRU eviction behavior.」（`vllm/v1/core/block_pool.py:831`）；没哈希的放队头、最先被复用——【注释】「LIFO reuse of non-cached blocks for better GPU locality,」（`vllm/v1/core/block_pool.py:827`）。
- 本例结束时：block 1（最多 11 个 token、没装满、无哈希）放队头；block 0（有哈希）放队尾，**仍留在前缀缓存里**，直到被后来者“弹出并驱逐”（`vllm/v1/core/block_pool.py:711-714`：弹出时才摘掉旧哈希）。所以“释放”不是“清空”——这是前缀缓存能在请求结束后继续命中的原因。

---

## 5. 问题四：前缀缓存命中时，少排了哪些 token？block 怎么复用？

前缀缓存默认开（`enable_prefix_caching = True`，`vllm/config/cache.py:267`；是否真开还要看模型支持，`vllm/engine/arg_utils.py:3072`）。

**怎么做（仅在 `num_computed_tokens == 0` 的新 / 被抢占请求上做，`vllm/v1/core/sched/scheduler.py:1132`）**

1. 查：`_get_local_prefix_cache_hit` → `KVCacheManager.get_computed_blocks`（`vllm/v1/core/sched/scheduler.py:1149`、`vllm/v1/core/kv_cache_manager.py:278`）。它拿请求的**块哈希链**去 `BlockPool` 找，找到第一个缺失就停：【注释】「A missing block implies every later block misses too (chained hashes).」（`vllm/v1/core/single_type_kv_cache_manager.py:921-922`）。
2. 命中长度有上限 `num_tokens - 1`：【注释】「The last token is always recomputed to produce logits.」（`vllm/v1/request.py:359`）；`vllm/v1/core/kv_cache_manager.py:314`、`vllm/v1/core/kv_cache_manager.py:316` 把它传给 `find_longest_cache_hit`。只在整块上命中：「Note that the computed blocks must be full.」（`vllm/v1/core/kv_cache_manager.py:282`）。
3. 调度器把“命中长度”当作这个请求已经算过的 token：`num_computed_tokens = 本地命中 + 外部命中`，再 `num_new_tokens = request.num_tokens - num_computed_tokens`（`vllm/v1/core/sched/scheduler.py:1239`、`vllm/v1/core/sched/scheduler.py:1348`），最后 `request.num_computed_tokens = num_computed_tokens`（`vllm/v1/core/sched/scheduler.py:1624`）。**少排的就是命中的那些 token，既不占预算，也不过 GPU。**
4. 复用 block：在 `allocate_slots` 里先把命中的 block `touch`（`ref_cnt += 1`，并从空闲队列摘出，防止被驱逐），再挂到这个请求的 block 表头，之后才给剩余 token 分新 block（`vllm/v1/core/kv_cache_manager.py:607`、`vllm/v1/core/single_type_kv_cache_manager.py:416`、`vllm/v1/core/block_pool.py:786-790`）。【注释】「Touch the computed blocks to make sure they won't be evicted.」（`vllm/v1/core/single_type_kv_cache_manager.py:414`）。**两个请求共享的是同一个物理 block**，由 `ref_cnt` 记有几个请求在用。
5. 哈希是“链式”的：每个块哈希含父块哈希 + 块内 token + 额外键（`vllm/v1/core/kv_cache_utils.py:726`），所以命中一定是**前缀**命中，中间相同不算。【文档】`docs/design/prefix_caching.md:5-15` 有图解；多租户隔离用 `cache_salt` 注入首块哈希（`docs/design/prefix_caching.md:87`）。
6. 记账：命中统计“在入场时才记”：【注释】「Record at admission so unscheduled lookups are not counted.」（`vllm/v1/core/sched/scheduler.py:1562`）。

**补设场景 A（接本例）**：第二个客户端发来**完全相同**的 20 token prompt（设），`block_size=16`（设），第一个请求的 block 0 还在缓存里（设）。

- 命中上限 `20 - 1 = 19`，`19 // 16 = 1` 块 → 命中 16 token；本步只排 `20 - 16 = 4` 个 token，分 1 个新 block（装这 4 个）。预算消耗 4，而不是 20。
- 若 prompt 恰好是 32 token 全命中（设）：上限 31 → 只能命中 1 块（16 token），**要重算整整一块 16 token**，而不是 1 个。【注释】「This can trigger recomputation of an entire block, rather than just」（`vllm/v1/core/kv_cache_manager.py:310`），原因是 `allocate_slots` 要求已算 token 数按块对齐。

**为什么是“`num_tokens - 1` 的通用上限”而不是特判**

- 【提交】`f0d610a8ae`「[v1][KVCacheManager] Avoid full cache hit by controlling max_length (#17999)」，改动 `vllm/v1/core/kv_cache_manager.py`：把原来“当 prompt 长度恰为块大小整数倍且全命中时，弹出最后一个哈希再放回”的特判，换成传给查找函数一个 `max_cache_hit_length`。正文为空，替换关系来自 diff；写到这里的注释即 `vllm/v1/core/kv_cache_manager.py:308-312`（【注释】「to obtain logits. Thus, set max_cache_hit_length to prompt_length - 1.」`vllm/v1/core/kv_cache_manager.py:309`）。
- 为什么要重算最后一个 token：采样需要最后一个位置的 logits，缓存里只有 KV，没有 logits。

**抢占后重来时也会命中**：被抢占的请求 `num_computed_tokens` 归 0，重新入场时又走这一套查找，所以它自己刚释放的 block 若还没被别人弹走，会被“重算”变成几乎免费（`vllm/v1/core/sched/scheduler.py:1854`；【文档】`docs/design/arch_overview.md:166-168`：「Prefix caching or a KV connector can restore part of its progress」）。

---

## 6. 问题五：显存不够时谁被抢占？为什么重算而不是换回？

**触发点**：running 循环里 `allocate_slots` 返回 `None`（`vllm/v1/core/sched/scheduler.py:899-912`）。进入一个 `while True`，**反复踢人直到分得到或者没人可踢**：

1. **选谁**：FCFS → `self.running[-1]`（最晚进入 running 的）；PRIORITY → `max(running, key=(priority, arrival_time))`（`vllm/v1/core/sched/scheduler.py:916-923`）。
2. 若受害者的块此刻不能立刻还（仅 KV 连接器 + 多批次在飞的特殊情形）则放弃（`vllm/v1/core/sched/scheduler.py:925-928`）。【注释】「A deferred free will not help with immediate allocation.」（`vllm/v1/core/sched/scheduler.py:925`）。
3. 把它从 running 弹出（FCFS：`running.pop()`，`vllm/v1/core/sched/scheduler.py:959`）；PRIORITY 还要把它**本步已经排好的**部分撤销、把预算退回（`vllm/v1/core/sched/scheduler.py:937-956`）。
4. `_preempt_request`（`vllm/v1/core/sched/scheduler.py:1831`）：释放它全部 block（`vllm/v1/core/sched/scheduler.py:1849`）→ `status = PREEMPTED`、`num_computed_tokens = 0`（`vllm/v1/core/sched/scheduler.py:1853-1854`）→ 清占位符（`vllm/v1/core/sched/scheduler.py:1868-1869`）→ 放回 `waiting` **队首**（`vllm/v1/core/sched/scheduler.py:1876`）。
5. 如果受害者正是当前请求自己（它是最后一个）→ `break`，本步 running 循环到此结束（`vllm/v1/core/sched/scheduler.py:967-969`、`vllm/v1/core/sched/scheduler.py:971-985`）。

**被抢占者“重算”的含义**：重新入场时它的 `num_tokens` 包含已经生成的输出，所以要重算的是 **prompt + 已生成 token**。【注释】「We use `request.num_tokens` instead of」「`request.num_prompt_tokens` to consider the resumed」「requests, which have output tokens.」（`vllm/v1/core/sched/scheduler.py:1345-1347`）；并且「A resumed request is re-sent in full, like a new one: its」「blocks were freed and its worker state dropped at preemption.」（`vllm/v1/core/sched/scheduler.py:1606-1607`）——它会作为 `scheduled_new_reqs` 再发一次完整数据。

**为什么重算而不是换到 CPU 内存**

- 【文档】「In vLLM V1, the default preemption mode is `RECOMPUTE` rather than `SWAP`, as recomputation has lower overhead in the V1 architecture.」（`docs/configuration/optimization.md:85`）；【文档】`docs/design/arch_overview.md:166-167`：「When re-admitted, it is recomputed rather than swapped back from CPU」。
- 文档只说“开销更低”，**没有解释为什么在 V1 架构下更低**。【推断】可能的原因：前缀缓存使重算常被部分抵消、分块 prefill 把重算摊进多步、不用维护 CPU 换出/换入的 block 映射与拷贝。这三点都是我的推测，没有直接出处，也没查到这个决定的原始讨论（见 §10）。
- 历史：V1 的第一个提交 `6c5af09b39` 的 diff 就已经是“free → `PREEMPTED` → `num_computed_tokens = 0` → `appendleft` 回 waiting”，没有换出路径；这是事实，不是理由。

**异步调度下抢占的细节（容易看漏）**

- 被抢占请求可能还有“在飞”的输出。设计是**照样收下它**，只是不能动已重置的计数器：【注释】「Async scheduling: mark all in-flight output as stale. Its tokens are」「still delivered on return (dropping them would perturb spec-decode」（`vllm/v1/core/sched/scheduler.py:1857-1858`）。`num_stale_output_tokens = num_in_flight_tokens`（`vllm/v1/core/sched/scheduler.py:1867`），之后每步在 `update_from_output` 里逐步“排干”（`vllm/v1/core/sched/scheduler.py:2406-2411`）。【文档】`docs/design/arch_overview.md:181-184` 同说：「If a request is preempted while it still has output in flight, that output is」「normally still delivered when it arrives.」。
- 【测试】`tests/v1/core/test_scheduler.py:2548`：被抢占的第 2 个请求之后仍收到了 token 42（`assert requests[1].output_token_ids[0] == 42`）。
- 这块逻辑是被一个 EngineCore 崩溃逼出来的（回归测试的 docstring 明说）：【测试】`tests/v1/core/test_async_scheduler.py:568-580` 的 docstring：「Regression for the num_output_placeholders underflow EngineCore crash:」；修复提交 `a0c092ee72`「[BugFix] Fix `num_output_placeholders` preemption underflow (#48245)」，改动 `vllm/v1/core/sched/scheduler.py`、`vllm/v1/core/sched/async_scheduler.py`，提交正文为空。

**补设场景 C（抢占）**：`block_size=16`、池里共 10 个可用 block（设；与测试 `tests/v1/core/test_scheduler.py:2499-2500` 的 `block_size=16`、`num_blocks=11` 同尺寸）。A、B 各 80 token prompt，各占 5 块；A 的下一个 decode 需要第 6 块，池已空 → `allocate_slots` 返回 `None` → 抢占 `running[-1]`＝B → B 的 5 块全部还回、B 进 waiting 队首 → A 拿到第 6 块；本步 `preempted_reqs` 非空，**不再排 waiting**，B 下一步起才重新入场（那时若 A 已结束，B 重算 80 + 已生成的 token，其中前缀缓存里还在的块可免算）。【测试】`tests/v1/core/test_scheduler.py:2534` 断言 `requests[1].status == RequestStatus.PREEMPTED`。

**抢占之外的闸门（预防抢占）**

（另有两个默认关的新旋钮，只点名：`cache_aware_admission_window`，默认 0＝严格按到达顺序，开了会把前缀已在缓存里的 waiting 请求提前，`vllm/config/scheduler.py:247`；`prefill_schedule_interval`，默认 1＝每步都允许入场 prefill，`vllm/config/scheduler.py:239`。）

- `scheduler_reserve_full_isl`（默认 `True`）：waiting 入场时要求**整条 prompt** 的 block 都放得下，才让第一个 chunk 进来（`vllm/v1/core/sched/scheduler.py:1503`、`vllm/v1/core/kv_cache_manager.py:542-558`）。【文档】「checks whether the full input sequence length fits in the KV cache before admitting a new request, rather than only checking the first chunk. Prevents over-admission and KV cache thrashing」（`vllm/config/scheduler.py:226-230`）。running 循环**不**传这个参数（`vllm/v1/core/sched/scheduler.py:899-903`）。【提交】`dc78c2c933`「[Core] add option to schedule requests based on full ISL (#37307)」，改动 `vllm/v1/core/kv_cache_manager.py`、`vllm/v1/core/sched/scheduler.py`，正文为空。
- `watermark`（默认 `0.0`＝关）：对 waiting / 被抢占的请求额外留一部分空闲 block（`vllm/config/scheduler.py:232-237`，`vllm/v1/core/kv_cache_manager.py:530-536`），running 请求不受影响。【注释】「The watermark is applied to waiting/preempted requests only, and only」（`vllm/v1/core/kv_cache_manager.py:530`）。

---

## 7. 问题六：长 prompt 怎么分块，和别人的 decode 怎么挤在一步？

**怎么做**

- 没有“分块”这个特殊分支，就是 `num_new_tokens = min(想要的, 预算)`：waiting 里 `num_new_tokens = request.num_tokens - num_computed_tokens`，再 `min(num_new_tokens, request_token_budget)`（`vllm/v1/core/sched/scheduler.py:1348`、`vllm/v1/core/sched/scheduler.py:1388`）。剩下的下一步由 **running 循环**继续排（因为它已在 running 里，`num_tokens_with_spec - num_computed_tokens` 仍大于 0，`vllm/v1/core/sched/scheduler.py:824-828`）。
- 一步里同时有别人的 decode：running 循环先把每个 decode 请求排 1 个 token，再把剩余预算给 prefill（§3）。
- 分块 prefill 默认开：config 类默认 `True`（`vllm/config/scheduler.py:160`），实际由模型能力决定（`vllm/engine/arg_utils.py:3070-3075`）。【文档】`docs/configuration/optimization.md:91`。
- **关掉分块时**：预算装不下就整体 `break`，不会跳过它去排后面的短请求——【注释】「If chunked_prefill is disabled,」「we can stop the scheduling here.」（`vllm/v1/core/sched/scheduler.py:1384-1385`）；【测试】`tests/v1/core/test_scheduler.py:2467-2490`（`test_schedule_order`：关分块时只排进 1 个）。【推断】这就是关分块时 `max_num_batched_tokens` 必须 ≥ `max_model_len` 的原因（`vllm/config/scheduler.py:396-404`；【文档】`docs/configuration/optimization.md:106-108`）；依据：配置校验的报错文字「This effectively limits the maximum sequence length to」（`vllm/config/scheduler.py:403`），即超过预算的 prompt 永远排不进一步。
- **`long_prefill_token_threshold`**（默认 0＝关，`vllm/config/scheduler.py:104`）：给单个请求一步内的 prefill 封顶，`if 0 < long_prefill_token_threshold < num_new_tokens`（running：`vllm/v1/core/sched/scheduler.py:829`；waiting：`vllm/v1/core/sched/scheduler.py:1374`）。**只有一个请求时不封顶**：【注释】「`long_prefill_token_threshold` exists to stop a long prefill from」「starving other requests of the token budget. When it is the only」「request there is nobody to starve, so let it use the whole budget.」（`vllm/v1/core/sched/scheduler.py:761-763`），条件 `enable_chunked_prefill and num_eligible_reqs > 1`（`vllm/v1/core/sched/scheduler.py:768`）。【提交】`c64b15cde5`「[Scheduler] Soften Long Prefill Tokens Threshhold (#57951)」，改动 `vllm/v1/core/sched/scheduler.py`、`vllm/config/scheduler.py`、`tests/v1/core/test_scheduler.py`；【测试】`tests/v1/core/test_scheduler.py:1963`、`tests/v1/core/test_scheduler.py:1989` 两个用例：「A lone long prefill is not capped by the threshold: it has no other」（`tests/v1/core/test_scheduler.py:1964`）。

**补设场景 B：我们的请求在 decode 时，来了一个 5000 token 的长 prompt**（`max_num_batched_tokens=2048`，设；无前缀命中，设）

| 步 | running 里我们的请求 | 长请求 L | 本步总 token | L 的已算 |
|---|---|---|---|---|
| a | decode 1 | waiting → 入场，排 `min(5000, 2047)=2047` | 2048 | 2047 |
| b | decode 1 | 在 running，排 `min(2953, 2047)=2047` | 2048 | 4094 |
| c | decode 1 | 排剩下 906 | 907 | 5000，本步末采样出 L 的第 1 个 token |

- 我们的请求每步都不断流，只是每步和 L 的一个块共用 GPU。
- L 的 a、b 两步的结果**不产生输出**：【注释】「Invariant: EngineCore returns no partial prefill outputs.」（`vllm/v1/core/sched/scheduler.py:2649`）——`update_from_output` 里拿不到 token 就不发 `EngineCoreOutput`（`vllm/v1/core/sched/scheduler.py:2548-2550`）。
- 异步调度下，非最后一块**不记占位符**：`if request.is_prefill_chunk: continue`（`vllm/v1/core/sched/async_scheduler.py:31-32`），因为这一步不会采样。
- 示例数值（2048、5000）是我设的；表中算式可直接由 `vllm/v1/core/sched/scheduler.py:1388` 推出。测试里有同构的数字推演：【测试】`tests/v1/core/test_scheduler.py:1917-1921`，预算 1024、阈值 400 时三个 800-token 请求依次拿到 400 / 400 / 224（`1024 - 400 - 400`）；第三轮前两个已进入 decode（各 1 token）、第三个继续 prefill（`tests/v1/core/test_scheduler.py:1960`：`== 800 - 224 - 224`）。

---

## 8. 问题七：占位符与 `num_computed_tokens` 怎么配合？EOS 时多排的那一步怎么办？

**三个数的关系**（`Request` 上）

- `num_tokens`＝prompt + **已确认**输出（`vllm/v1/request.py:338`）；`num_computed_tokens`＝**已排**的 token 数（调度时就加，`vllm/v1/core/sched/scheduler.py:1892`）；`num_output_placeholders`＝**在飞、值未知**的输出个数（`vllm/v1/request.py:172`）。
- running 请求这一步要排的量：`num_tokens_with_spec + num_output_placeholders - num_computed_tokens`（`vllm/v1/core/sched/scheduler.py:824-828`）。占位符的作用就是把“已经排出去、但 token 值还没回来”的那一步**算进已有长度**，使这个差值保持为 1，从而不用等结果也能排下一个 decode。
- 记占位符在 `AsyncScheduler._update_after_schedule`：`request.num_output_placeholders += self.num_sampled_tokens_per_step + cur_num_spec_tokens`（`vllm/v1/core/sched/async_scheduler.py:45-48`）。前提是这一步**会采样**（不是非最后一块 prefill，`vllm/v1/core/sched/async_scheduler.py:31-32`）。基类先 `num_computed_tokens += 本步排的`（`vllm/v1/core/sched/scheduler.py:1892`）并按 `num_computed_tokens < num_tokens + num_output_placeholders` 判 `is_prefill_chunk`（`vllm/v1/core/sched/scheduler.py:1897`）。
- 消占位符在 `_update_request_with_output`：每回来 N 个 token，`num_output_placeholders -= N`（`vllm/v1/core/sched/scheduler.py:2957`），并追加 token、调 `check_stop`（`vllm/v1/core/sched/scheduler.py:2946`）。
- 【注释】为什么 `num_computed_tokens` 要在调度时就加：「Advance the number of computed tokens for the request AFTER」「the request is scheduled.」（`vllm/v1/core/sched/scheduler.py:1880-1881`）；其后第 2 条：「schedule the prefill request again immediately in the next」（`vllm/v1/core/sched/scheduler.py:1885`）；第 3 条：「If some tokens (e.g. spec tokens) are rejected later, the number of」「computed tokens will be adjusted in update_from_output.」（`vllm/v1/core/sched/scheduler.py:1887-1888`）。
- 【文档】`docs/design/arch_overview.md:177-178`：「overlaps CPU scheduling with GPU execution: while the GPU executes step *N*,」「the CPU schedules step *N+1*, using placeholders for the tokens that step *N*」。

**用本例逐步推演**（`async_scheduling_depth` 默认 2，`vllm/config/scheduler.py:275`；`schedule` 第 k 次发生在第 1…k−2 个输出已处理、第 k−1 个未处理时，见上一趟 §5 步骤 6-7）。下表按代码公式手算，对应顶层图的三个数（本步排 / 已算 / 占位）：

| 第 k 次 schedule | 本步排 | 排完后“已算” | 排完后占位符 | 排之前已处理的输出 |
|---|---|---|---|---|
| S1（整段 prefill） | 20 | 20 | 1 | 无 |
| S2 | 1 | 21 | 2 | 无（输出 1 还在飞） |
| S3 | 1 | 22 | 2（输出 1 回来 −1，S3 再 +1） | 输出 1 |
| S4 … S7 | 1 | 23 … 26 | 2 | 输出 1…k−2 |
| S8 | 1 | 27 | 2 | 输出 1…6 |
| （没有 S9） | — | — | — | 输出 7 = `<eos>` 回来，请求结束 |

占位符“1”指 S1 之后；稳态下每步“−1（上一个输出回来）+1（本步新记）”，所以排之前恒为 1、排之后为 2。S2…S8 各排 1 个 token，所以总共排出 `20 + 7 = 27`。

**`max_tokens` 的“多排一步”被预先挡住了**：running 循环开头有守卫：`num_output_placeholders > 0 and num_computed_tokens + (1 + 在飞最少产出) - num_output_placeholders >= num_prompt_tokens + max_tokens` 就跳过这个请求（`vllm/v1/core/sched/scheduler.py:785-799`）。代入本例（`max_tokens=8`，所以右边是 `20 + 8 = 28`）：

- 排 S8 前：已算 26、占位符 1、在飞步数 1 → `26 + (1+1) − 1 = 27 < 28` → 照排。
- 若没遇到 EOS、要排 S9：S8 已排，输出 1…7 已处理 → 已算 27、占位符 1、在飞步数 1 → `27 + 2 − 1 = 28 >= 28` → **跳过**。所以不会排出第 9 步。

总排出 token 数与测试一致：`prompt + max_tokens − 1`（【测试】`tests/v1/core/test_async_scheduler.py:44`、`tests/v1/core/test_async_scheduler.py:65`；更强的版本【测试】`tests/v1/core/test_async_scheduler.py:539-565` 的 `test_no_steps_past_max_tokens_with_deep_batch_queue`：「No step is scheduled past max_tokens with several steps in flight, even」「when every draft is rejected.」）。【注释】`vllm/v1/core/sched/scheduler.py:795-797`：「Async scheduling: Avoid scheduling an extra step when we are sure that」「the in-flight steps reach request.max_tokens. We don't schedule」「partial draft tokens since this prevents uniform decode optimizations.」【提交】`44822d7ff2`「[BugFix] Preserve spec decoding uniform decode when scheduling (#29759)」，改动 `vllm/v1/core/sched/scheduler.py`、`vllm/v1/core/sched/async_scheduler.py`，正文为空。以上代入计算是我手算，表中数值没有被测试逐行断言（测试只断言总数）。

**EOS 却躲不掉：本例的 S8 会白算，结果被丢掉**（本例 EOS 是第 7 个 token，S8 是为第 8 个 token 排的）

- 因为调度器排 S8 时还不知道输出 7 是 `<eos>`（它在飞）。EOS 是**数据决定的**，不可预测，所以守卫只能挡“长度”，挡不住“EOS”。【推断】依据：守卫只比较 `max_tokens`（`vllm/v1/core/sched/scheduler.py:793`），代码里没有任何对 EOS 的预测。
- 输出 7 回来：`check_stop` 命中 `eos_token_id` → `FINISHED_STOPPED`（`vllm/v1/core/sched/utils.py:105-107`）；`update_from_output` 立刻 `_free_request`（`vllm/v1/core/sched/scheduler.py:2587`）→ `finished_req_ids.add`、`_free_blocks` 里释放 block 并 `del self.requests[...]`（`vllm/v1/core/sched/scheduler.py:3244`、`vllm/v1/core/sched/scheduler.py:3254-3256`）→ 请求退出 running（`vllm/v1/core/sched/scheduler.py:2655-2656`）。
- 之后 S8 的输出回来：`request = self.requests.get(req_id)` 已是 `None`（`vllm/v1/core/sched/scheduler.py:2403`）→ `if request is None or request.is_finished(): continue`（`vllm/v1/core/sched/scheduler.py:2415-2423`），整条输出被丢弃。这条分支的注释只写了 abort 的情形：【注释】「The request is already finished. This can happen if the」「request is aborted while the model is executing it (e.g.,」「in pipeline parallelism or in async scheduling).」（`vllm/v1/core/sched/scheduler.py:2416-2418`）；EOS 也走这个分支是**我的推断**（依据：两种情形都表现为“结果回来时请求已不在 `requests` 里”，没有为 EOS 单写的分支，也没找到相关注释或测试）。
- 代价：每个以 EOS 结束的请求多算一个 token 的前向，与同批其他请求共用一次 GPU 步，边际成本小（【推断】）。
- 为什么释放 block 时不怕 S8 还在 GPU 上写：【注释】「We must defer freeing blocks if an async kv connector may」「write to them immediately (not ordered with GPU stream).」（`vllm/v1/core/sched/scheduler.py:3268-3269`）说明只有**连接器**的写入才需要推迟释放；普通前向写入按同一 stream 的步骤顺序执行，新占用者晚于 S8 才会运行。“同一 stream 保证顺序”是我的推断（依据：该注释的反面；没有在这里直接看到 stream 的代码）。默认 `defer_block_free=False`（`vllm/v1/core/sched/scheduler.py:177`），仅 KV 消费者 + 多批次在飞时置真（`vllm/v1/core/sched/scheduler.py:203`）。

**判停顺序**：EOS → `stop_token_ids` → 长度 → `min_tokens` 门 → 重复检测（`vllm/v1/core/sched/utils.py:98-140`）；【注释】「Order of checks is important for min_tokens」（`vllm/v1/core/sched/utils.py:122`）。截断：若一次回来多个 token（投机解码）且中途停止，后面的丢掉并回退已算：「num_computed_tokens covers all new tokens but the last one.」（`vllm/v1/core/sched/scheduler.py:2948`，`vllm/v1/core/sched/scheduler.py:2947-2952`）。

---

## 9. 值得做成源码页的片段（建议 6 段）

每段列出：位置 / 在做什么 / 值得批注的行（括号里是“为什么”的来源等级）。

### 片段 1：`schedule()` 开头 + running 循环

位置：`vllm/v1/core/sched/scheduler.py:704-715`（注释）、`vllm/v1/core/sched/scheduler.py:728-731`（预算）、`vllm/v1/core/sched/scheduler.py:778-846`（循环）。做什么：设预算，遍历 `running`，给每个请求算本步 token 数。

- `vllm/v1/core/sched/scheduler.py:705`：「There's no "decoding phase" nor "prefill phase" in the scheduler.」（【注释】）——整个设计的钥匙，放在这页开头。
- `vllm/v1/core/sched/scheduler.py:728`、`vllm/v1/core/sched/scheduler.py:731`：两个预算；无投机时等值（§2）。
- `vllm/v1/core/sched/scheduler.py:780`：`while ... and token_budget > 0` —— 预算用尽即停。
- `vllm/v1/core/sched/scheduler.py:785-799`：占位符 / `max_tokens` 守卫。**顺序依赖**：必须在算 `num_new_tokens` 之前，否则会白排一步（【注释】`vllm/v1/core/sched/scheduler.py:795`；【提交】`44822d7ff2`）。
- `vllm/v1/core/sched/scheduler.py:824-828`：本步要排的量 = 已有 + 占位符 − 已算（§8）。
- `vllm/v1/core/sched/scheduler.py:829`、`vllm/v1/core/sched/scheduler.py:831-833`：先按 `long_prefill_token_threshold` 封顶，再按预算封顶（【注释】`vllm/v1/core/sched/scheduler.py:761-763`）。
- `vllm/v1/core/sched/scheduler.py:835-840`：`max_model_len` 封顶，注释「Make sure the input position does not exceed the max model len.」（`vllm/v1/core/sched/scheduler.py:835`，【注释】），另一行「This is necessary when using spec decoding.」（`vllm/v1/core/sched/scheduler.py:836`）。
- `vllm/v1/core/sched/scheduler.py:889-893`：`num_new_tokens == 0` 用 `continue` 不是 `break`（【注释】`vllm/v1/core/sched/scheduler.py:889-892`）。

### 片段 2：分配 KV + 抢占循环

位置：`vllm/v1/core/sched/scheduler.py:896-985`。做什么：要 block；没有就踢人，直到成功或无人可踢。

- `vllm/v1/core/sched/scheduler.py:899`：`allocate_slots` 返回 `None` 即失败（§4）。
- `vllm/v1/core/sched/scheduler.py:915-923`：选受害者：FCFS 取 `running[-1]`；PRIORITY 取 `max(priority, arrival_time)`（【文档】`docs/design/arch_overview.md:149-155`）。
- `vllm/v1/core/sched/scheduler.py:925-928`：「A deferred free will not help with immediate allocation.」（【注释】）。
- `vllm/v1/core/sched/scheduler.py:930-935`：PRIORITY 分支删受害者后若 `victim_index < req_index` 则 `req_index -= 1`——不然会漏排一个请求（【推断】依据：`req_index` 是对 `running` 的下标，删前面的元素必然使后面的左移；没有注释）。
- `vllm/v1/core/sched/scheduler.py:937-956`：撤销受害者本步已排的份额、退还预算。
- `vllm/v1/core/sched/scheduler.py:959-969`：FCFS 直接 `pop()`；`_preempt_request`；受害者是自己就 `break`。
- `vllm/v1/core/sched/scheduler.py:971-985`：`new_blocks is None` 后：PRIORITY 且自己被踢 → `continue`（后面的优先级更高，可能还放得下，【注释】`vllm/v1/core/sched/scheduler.py:974-981` 所在注释块）；否则 `break`。

### 片段 3：waiting 入场——查缓存、排 token、分块

位置：`vllm/v1/core/sched/scheduler.py:1132-1152`、`vllm/v1/core/sched/scheduler.py:1344-1392`。做什么：对队首请求，查前缀缓存、算本步 token 数。

- `vllm/v1/core/sched/scheduler.py:1132`：`if request.num_computed_tokens == 0:` —— 只有从零开始的才查缓存（被抢占的也是 0）。
- `vllm/v1/core/sched/scheduler.py:1149`：`_get_local_prefix_cache_hit(...)`。
- `vllm/v1/core/sched/scheduler.py:1345-1348`：`request.num_tokens` 而非 `num_prompt_tokens`（【注释】，为了被抢占后的重算）。
- `vllm/v1/core/sched/scheduler.py:1374`：长 prefill 封顶；`vllm/v1/core/sched/scheduler.py:1384-1385`：关分块则 `break`（【注释】）；`vllm/v1/core/sched/scheduler.py:1388`：`min(num_new_tokens, request_token_budget)`；`vllm/v1/core/sched/scheduler.py:1389`：`assert num_new_tokens > 0`。
- 注：这一段夹着大量 KV 连接器、Mamba、SWA 重放、投机填充的旁支（`vllm/v1/core/sched/scheduler.py:1155-1343`、`vllm/v1/core/sched/scheduler.py:1350-1372`、`vllm/v1/core/sched/scheduler.py:1393-1450`）；做源码页时建议折叠，只留上面这几行。

### 片段 4：`get_computed_blocks`

位置：`vllm/v1/core/kv_cache_manager.py:278-340`。做什么：用块哈希链找最长命中。

- `vllm/v1/core/kv_cache_manager.py:308-312`：【注释】last token 必须重算，且会重算整块（§5）。
- `vllm/v1/core/kv_cache_manager.py:314`：`get_max_cache_hit_length` 把上限定为 `num_tokens - 1`（`vllm/v1/request.py:357-361`）。
- `vllm/v1/core/kv_cache_manager.py:316`：`find_longest_cache_hit`，具体循环在 `vllm/v1/core/single_type_kv_cache_manager.py:921-929`，哈希链断了即停（【注释】）。
- 其余（事件、`shared_prefix_boundary`）是 Mamba / 外部消费者的旁支。

### 片段 5：`allocate_slots` 主干

位置：`vllm/v1/core/kv_cache_manager.py:562-637`（准入闸门在 `vllm/v1/core/kv_cache_manager.py:529-558`）。做什么：先算再分，分不到返回 `None`，分到后登记前缀缓存。

- `vllm/v1/core/kv_cache_manager.py:529-536`：`watermark`（【注释】`vllm/v1/core/kv_cache_manager.py:530`；【文档】`vllm/config/scheduler.py:233-237`）。
- `vllm/v1/core/kv_cache_manager.py:542-558`：`full_sequence_must_fit` 闸门（【注释】`vllm/v1/core/kv_cache_manager.py:543`；【文档】`vllm/config/scheduler.py:226-230`）。
- `vllm/v1/core/kv_cache_manager.py:562-564`：`num_tokens_need_slot` 含 lookahead 并封顶 `max_model_len`。
- `vllm/v1/core/kv_cache_manager.py:566-568`：先释放窗口外 block 再算——「We can do this even if we cannot schedule this request due to」（【注释】`vllm/v1/core/kv_cache_manager.py:568`）。
- `vllm/v1/core/kv_cache_manager.py:593-599`：「Keep `reserved_blocks` free for other in-flight sequences, and an」→ 不够返回 `None`（【注释】`vllm/v1/core/kv_cache_manager.py:593`）。
- `vllm/v1/core/kv_cache_manager.py:605-616`：**顺序依赖**：先挂命中块，再申请新块，「avoid the case where the new blocks cannot be allocated.」（【注释】`vllm/v1/core/kv_cache_manager.py:606`）；先 `touch` 才不会把刚命中的块当成可驱逐的空闲块弹走，这一层是【推断】，依据 `vllm/v1/core/block_pool.py:786-790`（`ref_cnt==0` 即在空闲队列）。
- `vllm/v1/core/kv_cache_manager.py:631-637`：只缓存“已确认”的块（【注释】`vllm/v1/core/kv_cache_manager.py:631`）。

### 片段 6：占位符的一生

位置：`vllm/v1/core/sched/scheduler.py:1879-1905`、`vllm/v1/core/sched/async_scheduler.py:18-52`、`vllm/v1/core/sched/scheduler.py:2933-2959`。做什么：调度后推进“已算”并加占位符；结果回来时追加 token、消占位符、判停。

- `vllm/v1/core/sched/scheduler.py:1892`、`vllm/v1/core/sched/scheduler.py:1897`：`num_computed_tokens += ...`；`is_prefill_chunk`。
- `vllm/v1/core/sched/async_scheduler.py:31-32`：prefill 块不记占位符；`vllm/v1/core/sched/async_scheduler.py:45-48`：记占位符；`vllm/v1/core/sched/async_scheduler.py:51`：`request.spec_token_ids = spec_token_placeholders`（投机时的占位用 `-1`，`vllm/v1/core/sched/async_scheduler.py:21`）。
- `vllm/v1/core/sched/scheduler.py:2945-2946`：`check_stop` 之前的注释「This must be called before we make the EngineCoreOutput.」（【注释】）；`vllm/v1/core/sched/scheduler.py:2947-2952`：停止时截断多余 token；`vllm/v1/core/sched/scheduler.py:2956-2958`：消占位符，`assert >= 0`——这条断言正是 `a0c092ee72` 修的崩溃点（【测试】`tests/v1/core/test_async_scheduler.py:576-580`）。

（可选第 7 段：`vllm/v1/core/block_pool.py:798-838` `free_blocks`，40 行，LRU 与 LIFO 的两端放法，§4。）

---

## 10. 没查到为什么

1. **“本步抢占过就不排 waiting”的理由**（`vllm/v1/core/sched/scheduler.py:1041`）：无注释；该行自 V1 首个提交 `6c5af09b39` 就在，正文为空。§3 给的是【推断】。
2. **waiting 队首分不到 block 就 `break`**（`vllm/v1/core/sched/scheduler.py:1508-1515`）：没有注释解释。
3. **V1 里为什么重算比换出开销低**：只有文档一句「as recomputation has lower overhead in the V1 architecture」（`docs/configuration/optimization.md:85`），没有解释；没有 V1 swap 的历史提交可查。
4. **FCFS 为什么踢“最晚进入”的而不是“已算最少”的**：文档只描述行为（`docs/design/arch_overview.md:151`），没有理由；我倾向的解释是“公平 + 浪费的重算少”，但这是推测，未标成结论。
5. **`max_num_batched_tokens` 的 8192 / 16384 / 2048 三档数值的依据**：只有 A100 的一条注释指向 PR #17885，提交正文为空，PR 正文读不到（不访问外网）。
6. **EOS 引起的多排一步是否有意保留**：没找到注释或测试专门讨论；§8 的结论是由代码路径推出的。
7. **这个 HEAD 上 `max_num_active_seqs`、`scheduler_reserve_full_isl`、`cache_aware_admission_window` 等较新旋钮的设计动机**：对应提交（`f730a93d2b`、`dc78c2c933`、`2a777ce26e`）正文都为空，只能看 config 里的 docstring（已引在对应处），看不到当时的讨论。
8. 本稿**没有读**：KV 连接器 / P-D 分离、Mamba / 混合 KV、滑窗、多模态编码预算、DP prefill 节流、流式输入、投机解码的具体接受逻辑。它们在 `schedule()` 里占了很多行，但不影响上面主线；只点名入口。

---

## 11. 读者最容易困惑的地方（供讲解员挑选）

1. **`schedule()` 里有大量与主线无关的分支**（连接器、Mamba、编码器、DP 节流）。主线只有：预算 → running 循环 → 抢占 → waiting 循环 → 打包。建议源码页折叠旁支。
2. **“已算”其实是“已排”**：`num_computed_tokens` 在 GPU 还没算时就加了。占位符/多排一步/抢占都由此而来（§1.3）。
3. **block 数取决于 block_size，默认是 64 不是 16**（§4）；例子里为了看得见才设 16。
4. **running 先、waiting 后，而且抢占过就不排 waiting**——两个条件叠在一起。
5. **FCFS 与 priority 抢占对象不同**，一个是 `running[-1]`，一个是 `max(priority, arrival_time)`。
6. **“被抢占 = 重算”不等于“全部重来”**：前缀缓存可能把大部分补回来；被抢占者的在飞输出仍会被收下。
7. **EOS 后调度器还会有一次 token 被白算**，但长度结束不会（§8）。
8. **分块 prefill 的中间块没有输出**，也不记占位符。

---

## 12. 可改进处【观点】

- 【观点】`schedule()` 一个函数 ~1100 行，旁支（连接器、Mamba、SWA、编码器、DP 节流）与主线交织，初读极难；把“主线”与“旁支钩子”分层（例如每个旁支一个小函数/策略对象）会好很多。
- 【观点】“本步抢占过就不排 waiting”“waiting 队首 `break`”这类影响公平性与吞吐的守卫都没有注释，应补一行理由。
- 【观点】EOS 多排一步可以考虑在批内用“结果回来前已知停止”做早退，但会引入同步，代价要和一个 token 的边际成本比较；目前取舍合理。
- 【观点】文档 `docs/design/arch_overview.md` 的抢占部分可补上“重算为何更便宜”的一两句依据。
