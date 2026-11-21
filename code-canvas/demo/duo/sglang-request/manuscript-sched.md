# 调度循环内部：补充研究底稿

> 仓库 HEAD = `c569c43446`。承接上一趟底稿 `manuscript.md`（下称“上一趟”）。本稿只研究顶层图上的框 ③ “调度循环”：一圈里做哪几件事、“这一圈跑谁”怎么定出来。
> 锚点写成 `仓库相对路径:行号`，均在 HEAD 上核对过。"为什么"统一标来源：【注释】【提交】【文档】【测试】【推断】；"可改进处"标【观点】。补设的值写"（设）"。
> 本仓库提交说明几乎都只有标题（PR 正文不在 git 里，我没访问外网），所以很多"为什么"只能落到【注释】或【推断】，这一点如实写在 §11。

---

## 0. 例子与补设值

沿用上一趟：请求 A（prompt 18 个 token，`max_tokens=64`，前缀命中 0，设），已有请求 B 在 decode。为了让调度机制"看得见"，补设：

- B：prompt 40 个 token，已生成 5 个，`max_new_tokens=200`（设）。
- 服务器全默认启动，GPU 为 80GB 档：`chunked_prefill_size` 解析为 8192（`python/sglang/srt/arg_groups/memory_hook.py:136-143`），`max_prefill_tokens` 默认 16384（`python/sglang/srt/arg_groups/fields/schedule.py:77-87`），`schedule_policy` 默认 `fcfs`（`python/sglang/srt/arg_groups/fields/schedule.py:92-109`）。页大小取 1（设，只为算术简单；真实值随注意力后端而定，我没追）。
- KV 池"可用 + 可驱逐"共 100000 个 token（设）。
- 请求 C：20000 个 token 的长 prompt，前缀不命中，`max_new_tokens=64`（设）。
- 请求 E：265 个 token（设），出现在显存吃紧时（见 §7）。

---

## 1. 一圈的全貌

**一句话：调度进程每转一圈，只做一件"选批"的决定——要么这圈跑一批 prefill，要么跑一步 decode；默认两者不混在同一次前向里。** 选批前先把新请求收进来、把上一圈 prefill 完的请求并进在跑的批。

主循环 `event_loop_overlap`（`python/sglang/srt/managers/scheduler.py:2235-2305`，回程和"晚一拍"见上一趟 §4 站 7）里，和"跑谁"有关的是第一、二步：

1. `ingest_requests()` 收请求，入 `waiting_queue`（`python/sglang/srt/managers/scheduler.py:2253`，见上一趟站 5）。
2. `get_next_batch_to_run(running_batch, last_batch)`（`python/sglang/srt/managers/scheduler.py:2272-2275`）——**本稿的主角**，返回 `NextBatchPlan(batch_to_run, running_batch)`（`python/sglang/srt/managers/scheduler.py:4339`）。
3. 拿到批后 `run_batch` 发射 GPU（上一趟站 7）。

`get_next_batch_to_run` 内部按顺序做这些事（读者画框可以照这个顺序）：

| # | 部件 | 做什么 | 读写的状态 | 交给谁 | 锚点 |
|---|---|---|---|---|---|
| ① | 收请求 | 取管道里新请求，建 `Req`，入队 | 写 `waiting_queue` | ② | `python/sglang/srt/managers/scheduler.py:2253` |
| ② | 并入与清点 | 把上一圈的 prefill 批并进 `running_batch`；剔除已结束的；清 `batch_is_full` | 读 `last_batch`、`chunked_req`；写 `running_batch`、`running_reqs` | ④ | `python/sglang/srt/managers/scheduler.py:4383-4420` |
| ③ | 排队序 | 给 `waiting_queue` 排序（默认 FCFS 基本不动），并对每条等待请求做前缀匹配 | 读写 `waiting_queue` 顺序；读 radix 树；写 `req.prefix_len` | ④ | `python/sglang/srt/managers/scheduler.py:4651-4661` |
| ④ | 装入 | `PrefillAdder` 按几样预算逐条试装 | 读 KV 池余量、`running_batch`、`new_token_ratio`；写 `can_run_list`、预算计数、`req.extend_end`、树节点锁 | ⑤ | `python/sglang/srt/managers/scheduler.py:4686-4705` |
| ⑤ | 建批 | `ScheduleBatch.init_new` + `prepare_for_extend`，给要算的 token 分配 KV 槽位 | 写 KV 池、`req_to_token_pool` | 发射 | `python/sglang/srt/managers/scheduler.py:4955-4978` |
| ⑥ | decode 一步 | ⑤ 没装出任何请求时，对 `running_batch` 做一步 decode；KV 不够就撤回请求 | 读写 `running_batch`；写 `waiting_queue`（撤回的请求）、`new_token_ratio` | 发射 | `python/sglang/srt/managers/scheduler.py:4446-4456` |

**状态放在哪**（都是 `Scheduler` 的成员，单线程独占，无锁）：

- `waiting_queue`：`IndexedReqList(ListKind.WAITING)`（`python/sglang/srt/managers/scheduler.py:1406`）。
- `running_batch`：在跑的 decode 批，一个 `ScheduleBatch`，带一个 `batch_is_full` 标志（`python/sglang/srt/managers/scheduler.py:1410`）。
- 三张登记表 `running_reqs`（已并入在跑批）、`prefill_launched_reqs`（最后一块已进批的 prefill）、`chunked_reqs`（两块之间的长 prompt）（`python/sglang/srt/managers/scheduler.py:1413`、`python/sglang/srt/managers/scheduler.py:1417`、`python/sglang/srt/managers/scheduler.py:1467`）。
  - 为什么要分这么多表：【注释】`python/sglang/srt/managers/scheduler_components/request_list.py:69`「A list of live requests. Every live request is in at most one list,」——每条请求同一时刻只在一张表里，靠 `move` 换表（`python/sglang/srt/managers/scheduler_components/request_list.py:229-248`）。读者看到 `move([req], …)` 就读成"这条请求换了个位置"。
- `new_token_ratio_tracker`：一个浮点估计，装入和撤回都要用（§7）（`python/sglang/srt/managers/scheduler.py:1603`）。
- `last_batch`：上一圈发射的批（`python/sglang/srt/managers/scheduler.py:1421`）。

**最容易困惑的一点（效果在别处）**：`waiting_queue` 里的请求被装入后，并不是"出队"这个动作——是 `move(..., self.prefill_launched_reqs)` 或 `move([new_chunked_req], self.chunked_reqs)` 换表（`python/sglang/srt/managers/scheduler.py:4935-4942`）。

---

## 2. 第一问：这一圈先跑 prefill 还是 decode？

规则一行：**有能装的 prefill 就先 prefill，装不出才 decode**（`python/sglang/srt/managers/scheduler.py:4443-4456`）。

- 【注释】`python/sglang/srt/managers/scheduler.py:4444`「Run prefill first if possible」。来源提交 `c211e7b669`，见上一趟 §4 站 6。
- 默认不混批：`enable_mixed_chunk` 默认 `False`（`python/sglang/srt/arg_groups/fields/schedule.py:227-230`）。
- 另一个旋钮 `prefill_decode_interval`：prefill 批之后强制先跑 N 步 decode 再考虑下一次 prefill；默认关（【文档】`python/sglang/srt/arg_groups/fields/schedule.py:62-68`「By default, this is disabled except for profiled Qwen3-VL serving configurations on Hopper.」）。生效处是 `_should_defer_prefill`（`python/sglang/srt/managers/scheduler.py:4422`、`python/sglang/srt/managers/scheduler.py:1493-1509`）。

**读者容易看错的后果**：prefill 优先 + 默认不混批 ⇒ 来了长 prompt 的那几圈，**在跑的 B、A 一步 decode 都不走**（见 §9 推演）。这正是 `enable_mixed_chunk` 和 `prefill_decode_interval` 存在的原因，但代码没有明说动机，这里是【推断】（依据：两个旋钮的文档串描述的就是"让 prefill 与 decode 交错"）。

### 选批前先清点（②）

读者会问：为什么选批之前先动 `running_batch`？因为后面所有"满不满、有多少在跑"的判断都读它。三件事：

1. **长 prompt 的中间块不并入**：`chunked_req` 被排除在合并之外，并把它已算出的 KV 登记进 radix 树（`python/sglang/srt/managers/scheduler.py:4355-4363`，`stash_chunked_request` 在 `python/sglang/srt/managers/scheduler.py:4224-4228`，核心是 `checkpoint_kv_cache`：`python/sglang/srt/mem_cache/common.py:235-244`）。
2. **上一圈的 prefill 批并进 `running_batch`**：`running_batch.merge_batch(last_batch)`，若在跑批是空的就直接把它当作在跑批（`python/sglang/srt/managers/scheduler.py:4403-4411`）。
3. **每圈都剔除已结束的请求**，一旦批变小就把 `batch_is_full` 清零（`python/sglang/srt/managers/scheduler.py:4413-4418`）。
   - 【注释】`python/sglang/srt/managers/scheduler.py:4413`「Every step, not only after a merge: overlap merges a prefill's requests」，下一行接着说 overlap 下并入发生在结果处理之前。这就是上一趟"晚一拍"在这里的具体后果：A 被并入 `running_batch` 时，它的首个 token 还没被处理。

---

## 3. 第二问：等待队列里先挑谁？

**默认答案：先来先服务，而且基本不动队列。** `fcfs` 是默认（`python/sglang/srt/arg_groups/fields/schedule.py:109`）；FCFS 下只有开了优先级调度才会排序（`python/sglang/srt/managers/schedule_policy.py:355-359`），否则队列保持入队顺序（`move` 默认入队尾：`python/sglang/srt/managers/scheduler_components/request_list.py:175-181`）。

调用点：`self.waiting_queue.reorder(self.policy.calc_priority(...))`（`python/sglang/srt/managers/scheduler.py:4651-4661`），`reorder` 只改顺序，改了成员会抛错（`python/sglang/srt/managers/scheduler_components/request_list.py:183-191`）。`SchedulePolicy` 构造在 `python/sglang/srt/managers/scheduler.py:1550`。

**可选策略**（【文档】`python/sglang/srt/arg_groups/fields/schedule.py:92-108` 的 `choices`；分类见 `python/sglang/srt/managers/schedule_policy.py:187-203`）：

- 看缓存的：`lpm`（最长前缀优先）、`dfs-weight`、`hrrn`、`shortest-prefill-first`、`uniboost`。
- 不看缓存的：`fcfs`、`lof`（最长输出优先）、`random`、`routing-key`。

### 3.1 LPM 怎么排（以它为例，因为它最能体现"缓存让谁先跑"）

- 排序键：已匹配的前缀越长越靠前（`python/sglang/srt/managers/schedule_policy.py:735-746`，`-r.num_matched_prefix_tokens`）。
- **同批共享前缀只放行一个**（in-batch prefix caching）：匹配前缀 ≤ 32 的请求，取前 32 个 token 在一棵"模拟树"里互相比；已有同前缀者的被降到队尾（`python/sglang/srt/managers/schedule_policy.py:585-646`，阈值 `python/sglang/srt/managers/schedule_policy.py:117-127`，环境变量可调，设 -1 关闭）。
  - 为什么：【注释】`python/sglang/srt/managers/schedule_policy.py:591-592`「In-batch prefix caching: of the requests with almost nothing cached that」，下一行接「share a prefix, only one runs, so the shared prefix is computed once.」
  - 来历：【提交】`9208618b3e`「[Core] in batch prefix caching by delay scheduling (#2442)」（只有标题）。
  - 读者会困惑"为什么把请求往后放"：它被推迟一圈，是为了等前一个把共享前缀算出来写进树，之后它就变成缓存命中。【推断】（依据：上面的注释 + 降到队尾而不是丢弃）。

### 3.2 队列太长时 LPM 会被关掉

`_determine_active_policy`：LPM/HRRN 且等待队列超过 128 条时，退化成 FCFS（`python/sglang/srt/managers/schedule_policy.py:537-548`）。

- 【注释】`python/sglang/srt/managers/schedule_policy.py:546`「Turn off the expensive prefix matching and sorting when the #queue is large.」
- 128 这个数：【注释】`python/sglang/srt/managers/schedule_policy.py:143`「Arbitrary; bounds per-round prefix work on the waiting queue.」——注释自己承认是拍的。
- 新近的补丁：提交 `f76ac26543`「[Feat] scheduler: add fixed-batch bounded LPM policy (#48409)」（2026-11-16，只有标题）给 LPM 加了有界模式：队列超过 128 且通过一个门控判断（`_should_start_bounded_batch`）时，只对前 64 条做 LPM（`python/sglang/srt/managers/schedule_policy.py:180`、`python/sglang/srt/managers/schedule_policy.py:272-305`）。默认策略是 FCFS，所以默认路径不走它；我没有深挖这条路径。

### 3.3 读者会问：FCFS 为什么还要对每条等待请求做前缀匹配？

- 【注释】`python/sglang/srt/managers/schedule_policy.py:345`「Cache-agnostic policies sort without matching; match anyway so the load」，下一行（`python/sglang/srt/managers/schedule_policy.py:346`）「snapshot sees num_matched_prefix_tokens. Decode never prefills.」
- 来历：【提交】`f6cd1a9822`「Add num_waiting_uncached_tokens load metric (#27174)」——这个"匹配一遍"是为一个负载指标加的，**不是为排序**。排序后什么都没变，但 `req.num_matched_prefix_tokens` 被填上了。
- 另有一件周期性小事：每 32 次调度把前 128 条等待请求的前缀"摸"一遍，让 LRU 淘汰顺序和调度顺序一致（`python/sglang/srt/managers/schedule_policy.py:501-535`；【注释】`python/sglang/srt/managers/schedule_policy.py:507`「Head last, so LRU evicts in reverse admission order; cache-aware policies」；间隔 `python/sglang/srt/managers/schedule_policy.py:146`；开关默认开 `python/sglang/srt/environ.py:705`；来历【提交】`20d1f3f56a`「[Scheduler] Refresh waiting requests' cached prefixes under FCFS-like policies with LRU eviction (#41593)」）。

---

## 4. 第三问：逐条装入，查哪几样预算，什么时候停？

入口 `get_new_batch_prefill` → `_get_new_batch_prefill_raw`（`python/sglang/srt/managers/scheduler.py:4506`、`python/sglang/srt/managers/scheduler.py:4586-4591`）。分三步：**前置闸门 → 逐条装入循环 → 建批**。

### 4.1 前置闸门：根本不开始装的情形

`_get_new_batch_prefill_raw` 开头就返回 `None` 的情形（`python/sglang/srt/managers/scheduler.py:4591-4646`）：

- `running_batch.batch_is_full` 为真，或 `waiting_queue` 为空，且没有在分块的请求 ⇒ 直接返回（`python/sglang/srt/managers/scheduler.py:4606-4609`）。
- **请求槽用完**：`get_num_allocatable_reqs ≤ 0` ⇒ 置 `batch_is_full = True` 并返回（`python/sglang/srt/managers/scheduler.py:4636-4645`）。可分配请求数 = `min(pp_budget, row_budget)`：`pp_budget = pp_max_micro_batch_size − 在跑数`，`row_budget` 来自 `req_to_token_pool` 的空闲行（`python/sglang/srt/managers/scheduler.py:4478-4504`）。单卡 `pp_size=1` 时 `pp_max_micro_batch_size` 默认就是 `max_running_requests`（`python/sglang/srt/managers/scheduler.py:1316-1322`）——所以"最多同时跑多少请求"这个上限从这里进来。`max_running_requests` 的默认值怎么定的，我没追。
- 【注释】`python/sglang/srt/managers/scheduler.py:4647`「After the early returns, so a full-batch pass does not scan the queue.」——**排序在闸门之后**，批满了就不白白排一次队。

**读者会困惑：`batch_is_full` 是个"粘住"的标志。** 它不是每圈重算，而是：装入失败时置位；有请求离开批时（剔除结束的、撤回、批变小）才清（`python/sglang/srt/managers/scheduler.py:4395`、`python/sglang/srt/managers/scheduler.py:4416-4418`）。
- 对应的防死锁补丁：【注释】`python/sglang/srt/managers/scheduler.py:4602`「An empty batch has no request left to leave, and only a leaving」（下接"request clears the flag elsewhere, so admission would stay shut."）——批空了就强制清标志，否则没有任何请求离开，标志永远清不掉。

### 4.2 装入器 `PrefillAdder` 的四样预算

构造时一次性给出（`python/sglang/srt/managers/scheduler.py:4686-4705`；类 `python/sglang/srt/managers/schedule_policy.py:1009`）。一圈的预算，用 A、B 的值来看：

| 预算 | 初值 | 每装一条扣多少 | 耗尽返回 | 锚点 |
|---|---|---|---|---|
| ⓐ KV"总量"`remaining_total` | 池可用+可驱逐−`total_offset` | 扣 extend(取整到页)+1 页+`max_new_tokens` | `NO_TOKEN` | `python/sglang/srt/mem_cache/prefill_budget.py:144-157` |
| ⓑ KV"当前"`remaining_current` | 同上−`current_offset` | 只扣 extend(取整)+1 页 | `NO_TOKEN` | `python/sglang/srt/mem_cache/prefill_budget.py:159-171` |
| ⓒ 输入 token `rem_input_tokens` | `max_prefill_tokens`=16384 | 扣 extend（取整） | `OTHER` | `python/sglang/srt/managers/schedule_policy.py:1050`、`python/sglang/srt/managers/schedule_policy.py:1385` |
| ⓓ 块大小 `rem_chunk_tokens` | `chunked_prefill_size`=8192 | 同 ⓒ | `OTHER` | `python/sglang/srt/managers/schedule_policy.py:1390-1391` |

另有两个"个数"上限：请求槽（§4.1）、`prefill_max_requests`（默认不限，【文档】`python/sglang/srt/arg_groups/fields/schedule.py:88-91`「If not specified, there is no limit.」，检查在 `python/sglang/srt/managers/schedule_policy.py:2033`）。

**ⓐ 的"偏移"是调度器最重要的一笔账**：`total_offset` 初值 = 在跑请求**还要生成的 token 估计**之和（`python/sglang/srt/managers/schedule_policy.py:1095-1100`、`python/sglang/srt/managers/schedule_policy.py:1174-1185`）：

> 每条在跑请求的预留 = `min(max_new_tokens − 已生成, 4096) × new_token_ratio`；`ignore_eos` 的请求比例取 1。

例子：B 的预留 = min(200−5, 4096) × 0.7 = 136.5（设）。`remaining_total` = 100000 − 136.5 = 99863.5。

- 为什么给在跑请求预留空间：【注释】`python/sglang/srt/mem_cache/prefill_budget.py:91`「Fixed token pool. Offsets include pending allocations and decode headroom.」；【测试】`test/registered/unit/managers/test_prefill_adder.py:436` 的文档串「A chunked continuation must not take KV reserved for running decodes;」。读法：新请求不能把在跑请求将来要用的 KV 抢光，否则 decode 中途没地方放新 token。
- 为什么乘一个小于 1 的比例：【推断】绝大多数请求不会真生成到 `max_new_tokens`（常提前遇到结束符），全按上限预留会让并发上不去。代码里的间接证据：【注释】`python/sglang/srt/managers/schedule_policy.py:92-93`「Clip the estimation of max_new_tokens for the request whose max_new_tokens is very large.」，下一行「This can prevent the server from being too conservative.」（"太保守"是被当作问题来对待的）。
- 估错了怎么办：走 §7 的撤回；比例初值 0.7（`python/sglang/srt/environ.py:708`），可用 `schedule_conservativeness` 放大（默认 1.0，【文档】`python/sglang/srt/arg_groups/fields/schedule.py:158-161`「Use a larger value if you see requests being retracted frequently.」）。
- 4096 这个截断来历：【提交】`d785412077`「Fix the case when max_new_tokens is too large (#1025)」，diff 里加的注释「Clip the max new tokens for the request whose max_new_tokens is very large.」，由 Lianmin 在 2024-08；当前常量 `python/sglang/srt/managers/schedule_policy.py:96`，环境变量 `python/sglang/srt/environ.py:712`。**只截断估计，不截断真正的停止条件**（【注释】`python/sglang/srt/managers/schedule_policy.py:94`「Note that this only clips the estimation in the scheduler but does not change the stop」）。

**ⓒ 与 ⓓ 的关系（读者会糊涂）**：每装一条，两个计数器被扣同样的量（`python/sglang/srt/managers/schedule_policy.py:1385-1391`）。默认配置下块大小 8192 < 16384，所以 ⓓ 先耗尽，`max_prefill_tokens` 实际松着——【推断】（依据：同减同量，小的先到零）。`max_prefill_tokens` 真正当主角的是**关掉分块**（`chunked_prefill_size=-1`）时：此时 `rem_chunk_tokens` 为 `None`，由 ⓒ 兜底（`python/sglang/srt/managers/schedule_policy.py:2272-2277`；显存预留也是这样切换的，【注释】`python/sglang/srt/arg_groups/memory_hook.py:310`「Tokens the activation working set scales with (per serving mode).」，分支见 `python/sglang/srt/arg_groups/memory_hook.py:318-320`）。

### 4.3 逐条装入循环

`for req in candidates:`（`python/sglang/srt/managers/scheduler.py:4772`）。每个候选依次过这些关，**任何一关不过就停（`break`）或跳过（`continue`）**：

1. 预算已耗尽（块大小或输入 token 为 0）⇒ `break`（`python/sglang/srt/managers/scheduler.py:4775-4778`）。
2. 请求槽满 ⇒ `batch_is_full = True` ⇒ `break`（`python/sglang/srt/managers/scheduler.py:4800-4813`；开了优先级抢占才改为尝试抢占）。
3. **每个候选在装入前重新匹配一次前缀**：`req.init_next_round_input(self.tree_cache, ...)`（`python/sglang/srt/managers/scheduler.py:4845`）——树在两圈之间会变（别的请求结束写入、淘汰），所以不能信排序时那次匹配。【推断】
4. `adder.add_one_req(...)` 返回四种结果之一（`python/sglang/srt/managers/schedule_policy.py:944-951`；循环里的处理 `python/sglang/srt/managers/scheduler.py:4867-4895`）：
   - `CONTINUE`：装进去了，且预算还有，继续下一个；
   - `NO_TOKEN`：KV 不够；`OTHER`：别的预算耗尽 / 个数上限 / 延迟器——都 `break`；
   - `UNSCHEDULABLE`：这条**永远装不下**（没有任何持有显存的请求会还内存），加入 `unschedulable` 列表、`continue`，循环后直接中止它并返回错误（`python/sglang/srt/managers/scheduler.py:4893-4894`、`python/sglang/srt/managers/scheduler.py:4897-4911`）。
5. `add_one_req` 末尾返回 `budget_state()`，"这条装完之后还能不能装下一条"的判断就由它给出：KV 没了 ⇒ `NO_TOKEN`，输入或块预算 ≤0 ⇒ `OTHER`，否则 `CONTINUE`（`python/sglang/srt/managers/schedule_policy.py:2210-2213`、`python/sglang/srt/managers/schedule_policy.py:1304-1319`）。

**注意"遇到第一个装不下的就停"，不是跳过它去找更小的。** 队首的大请求会挡住后面的小请求。
- 【注释】`python/sglang/srt/managers/scheduler.py:4920`「Empty passes are the key signal for head-of-line blocking.」——作者知道这个现象并专门为它打日志（`python/sglang/srt/managers/scheduler.py:4533-4570`，由 `log_schedule_decisions` 控制）。
- 为什么不跳过：代码没写。【推断】跳过会让大请求在小请求持续到来时饿死，停下来等则保证队首最终有机会（这与 FCFS 的公平语义一致）。

### 4.4 `add_one_req` 里怎么判一条（一次 `PrefillAdder` 内部的核心）

（源码页，见 §10 页 4。）顺序：

1. 个数上限 → `OTHER`（`python/sglang/srt/managers/schedule_policy.py:2033`）；若有一条分块请求在等，`NO_TOKEN`（`python/sglang/srt/managers/schedule_policy.py:2036`）。
2. 算这条要占的总量 `total_tokens = (整段待填长度 − 命中前缀) + 预留 max_new(截断 4096) + 每请求额外一页`（`python/sglang/srt/managers/schedule_policy.py:2215-2229`）。
   - 为什么多一页：【注释】`python/sglang/srt/managers/schedule_policy.py:2217-2218`「Reserve page_size for page-alignment overhead: the paged allocator may」，接「consume one extra page per request (see alloc_extend), which」。
   - 来历：【提交】`25549433e8`「Fix prefill OOM due to wrong token calculation when page > 1」——这个页取整是为修"页大小>1 时预填 OOM"加的；同一处留了一条 TODO：【注释】`python/sglang/srt/managers/schedule_policy.py:1351`「TODO(lsyin): check this workaround logic, which only ensures the prefill will not out of memory, and may be too conservative」。作者自己承认这是偏保守的权宜之计。
3. 给匹配到的树节点临时加锁，使这段前缀不被算进"可驱逐"（【注释】`python/sglang/srt/managers/schedule_policy.py:2052`「The temporary pin excludes this prefix from the evictable budget.」）。**顺序依赖**：必须先锁再判，否则会把"自己要复用的前缀"当成可被淘汰的空间，算出虚高的余量。【推断】
4. `_select_prefill_admission`（`python/sglang/srt/managers/schedule_policy.py:2231-2353`）：先 `check_prefill`：`total_tokens < remaining_total` 才放行（`python/sglang/srt/mem_cache/prefill_budget.py:211-215`）。不行 ⇒ 被拒（`NO_TOKEN`，或 `UNSCHEDULABLE`，`python/sglang/srt/managers/schedule_policy.py:1239-1244`）。
5. 不分块时的特例：【注释】`python/sglang/srt/managers/schedule_policy.py:2272`「Without chunking, allow the first request even above the input cap.」——没有分块时，批里第一条请求哪怕超过输入上限也放行，否则一个超长 prompt 永远排不上。
6. 分块的判断见 §6。
7. 通过后 `_commit_prefill_admission`：`req.extend_end`、锁树节点、入 `can_run_list`、扣预算（`python/sglang/srt/managers/schedule_policy.py:2356-2396`；扣账细节 `python/sglang/srt/managers/schedule_policy.py:1321-1392`）。

其余会在这条路径上出现的分支——LoRA、HiCache 存储预取、延迟器（`PrefillDelayer`）、优先级抢占、PD 分离、DLLM、beam、混合 SWA/Mamba 的状态槽——都在代码里，**不在本稿展开**。它们多半让一条请求"这圈先跳过 / 等预取"（`python/sglang/srt/managers/scheduler.py:4779-4793`、`python/sglang/srt/managers/scheduler.py:4850-4866`），而不改变上面的主线。

---

## 5. 第四问：已经缓存的前缀怎么让要算的量变少？

**一句话：匹配到多长前缀，这条请求这一次就少算多长；预算和 GPU 输入都只数"没命中的那一段"。**

链条：

1. `Req.init_next_round_input` → `match_kv_cache(...)`（`python/sglang/srt/managers/schedule_batch.py:1202-1215`、`python/sglang/srt/mem_cache/common.py:184-224`）：在 radix 树里找最长前缀，写回 `req.prefix_len = device_prefix_len`（`python/sglang/srt/mem_cache/common.py:209`）。
2. 要算的量 `extend_len = 整段 − prefix_len`（`python/sglang/srt/managers/schedule_policy.py:2244`；`Req.extend_len` 属性 `python/sglang/srt/managers/schedule_batch.py:1135-1136`）。
3. 预算只扣这一段（取整到页）（§4.2 表）。
4. 建批时只把没命中的 token 喂给 GPU：`input_ids = [r.get_fill_ids()[r.prefix_len :] ...]`（`python/sglang/srt/managers/schedule_batch.py:2406`）；KV 槽位也只给这些新 token 分配：`python/sglang/srt/managers/schedule_batch.py:2464` 调 `alloc_for_extend`，按 `prefix_lens` / `extend_lens` 分配，池子不够就直接抛错（【注释】`python/sglang/srt/mem_cache/allocation.py:363-371`「Allocate KV cache for extend batch and write to req_to_token_pool.」，接着说「if the pool can't satisfy the batch (fail-loud」——**所以装入阶段的预算必须事先把关**，这一点是我的推断【推断】）。

**容易看错的守卫：命中长度最多比输入少 1。** 【注释】`python/sglang/srt/managers/schedule_batch.py:1239`「NOTE: the matched length is at most 1 less than the input length to enable logprob computation」（`python/sglang/srt/managers/schedule_batch.py:1238-1240`）。读法：哪怕整段 prompt 都在缓存里，也至少要算最后 1 个 token，否则没有输出可采样。注释只提了 logprob；"必须有至少一个 token 的前向才有下一个 token 的分布"是我的补充【推断】。

例子：如果有一条请求 A′ 和 A 共享 12 个 token 的系统提示（设），树里命中 12 个，则 A′ 的 `extend_len = 18 − 12 = 6`，预算只扣 6（取整到页后 6 + 1 页）。上一趟的 A 命中 0，所以 `extend_len = 18`。

**命中还有"锁"这一层**：装入时给匹配的树节点加引用锁（`python/sglang/srt/managers/schedule_policy.py:2361`），防止在这条请求用它的时候被淘汰；请求结束才释放并把新前缀写回树（上一趟站 8 末）。

---

## 6. 第五问：长 prompt 怎么分几圈跑？

**开关**：`chunked_prefill_size`（【文档】`python/sglang/srt/arg_groups/fields/schedule.py:58-61`「The maximum number of tokens in a chunk for the chunked prefill. Setting this to -1 means disabling chunked prefill.」）。默认按 GPU 显存档位解析：显存小于 20GB 的卡 2048，20–60GB 档 4096，60–160GB 档（H100/H200 级）8192，更大（B200 级）16384（`python/sglang/srt/arg_groups/memory_hook.py:80-181`）。

**为什么分块**：代码没有一句话直接说动机。证据是：块大小被用来估激活内存——开了分块时按块大小预留，没开才按 `max_prefill_tokens`（【注释】`python/sglang/srt/arg_groups/memory_hook.py:310`「Tokens the activation working set scales with (per serving mode).」，`python/sglang/srt/arg_groups/memory_hook.py:318-320`）。所以【推断】：分块把"一次前向最多处理多少 token"封顶，使激活内存和单次前向时长有上界，一个超长 prompt 不会让整台机器停摆或 OOM。历史上：【提交】`7cd4f244a4`「Chunked prefill (#800)」（2024-07-29；先前的 `2ec39ab712`「Chunked prefill support (#797)」被 `98111fbe3e` 回滚后重提，只有标题）。

### 6.1 第一块：在 `add_one_req` 里被截断

`_select_prefill_admission` 里：若整段 extend（取整）大于允许的块（`chunk_fit_tokens > chunk_tokens_limit`），就截成一块——页取整并对齐（`python/sglang/srt/managers/schedule_policy.py:2291-2320`），标 `is_chunked = True`，`max_new_tokens` 记 0（这一块还不生成）。
- 已经有一条在分块时，另一条也要分块 ⇒ 拒绝（`OTHER`）。【注释】`python/sglang/srt/managers/schedule_policy.py:2293-2294`「Only one unfinished chunked request can be tracked; a」，下一行「continuation cut short by memory leaves chunk budget to spare.」——**任何时刻最多一条分块中的请求**。为什么只一条：注释只说"只能跟踪一条"，设计动机没写。
- 装入后 `adder.new_chunked_req` 被 `move` 进 `chunked_reqs`（`python/sglang/srt/managers/scheduler.py:4934-4938`）。

### 6.2 后续每圈：先续这条，再看别人

`_get_new_batch_prefill_raw` 在构造完 adder、进入候选循环**之前**，先处理"两块之间"的请求：`resumed_req = self.chunked_req`；`resumed_req.init_next_round_input()`（**不传树**，即不重新匹配前缀）；`adder.add_chunked_req(resumed_req)`（`python/sglang/srt/managers/scheduler.py:4707-4720`）。

- 上一块算出的 KV 已在选批开头登记进树，`prefix_len` 被推进到 `extend_end`（`python/sglang/srt/mem_cache/common.py:242-244`），所以续块不需要再匹配：它的"已缓存前缀"就是前几块自己。
- `add_chunked_req` 里的块大小：`min(块预算, 内存允许, 策略限额)`，再对 KV 做 `fit_chunk`（`python/sglang/srt/managers/schedule_policy.py:1675-1797`）。若内存连一块都装不下，这条被"停靠"（`park`）：不进批，且这一圈**谁都不放行**。
  - 【注释】`python/sglang/srt/mem_cache/prefill_budget.py:267`「Park the continuation rather than take running decodes' headroom.」；调度器一侧【注释】`python/sglang/srt/managers/scheduler.py:4751`「A parked chunk found no budget in this pass: admit no one ahead of it,」。
  - 【测试】`test/registered/unit/managers/test_prefill_adder.py:436`的文档串写明同一语义。
- 最后一块：`add_chunked_req` 返回 `None`，请求换表进 `prefill_launched_reqs`，剩余预算还可以装别的请求（`python/sglang/srt/managers/scheduler.py:4718-4720`）。

**读者会困惑（效果在别处）**：中间块的前向结果**不产出 token**。`is_middle_chunk = req is batch.chunked_req`，该块既不 commit 输出，也不 stream（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:447-449`、`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:587-590`）。用户直到最后一块 prefill 完才看到首字。

**另一个容易忽略的副作用**：C 的前几块把 `rem_chunk_tokens` 吃光，`budget_state()` 变成 `OTHER`，本圈候选循环的第一关就 `break`（`python/sglang/srt/managers/scheduler.py:4775-4778`），因此分块期间排在后面的新请求也上不了车。

---

## 7. 第六问：显存不够怎么办？

显存不够有**两个**不同的时间点，处理完全不同：

### 7.1 装入时不够：请求留在队里等

- `check_prefill` 不过 ⇒ `NO_TOKEN` ⇒ 循环 `break`，该请求原样留在 `waiting_queue`（`python/sglang/srt/managers/scheduler.py:4876-4895`）。
- 置 `batch_is_full = adder.batch_is_full_after_rejection(req)`：只要这圈已经装了别人，或在跑批非空，就置真（`python/sglang/srt/managers/scheduler.py:4877-4880`、`python/sglang/srt/managers/schedule_policy.py:2025-2028`）。语义是"在跑的有请求结束、空出内存之前，不要再试"。之后每圈会在 §4.1 的闸门被挡掉，直到有请求离开批把标志清掉。
- 同时，装入时的"总量"账已经给在跑请求预留了 decode 空间（§4.2），所以正常情况下**装入通过就意味着在跑请求大概率能跑完**。

### 7.2 decode 时不够：撤回（retract）部分请求

装入时用的是估计（`max_new × ratio`），估低了就会在 decode 中途没地方放新 token。`update_running_batch`（`python/sglang/srt/managers/scheduler.py:5194-5282`）每步检查：

1. 先剔除"在途步将按长度结束"的请求（`python/sglang/srt/managers/scheduler.py:5198-5200`）。
2. `kv_full_retract_flag = not batch.check_decode_mem()`（`python/sglang/srt/managers/scheduler.py:5207`；`check_decode_mem` 在 `python/sglang/srt/managers/schedule_batch.py:2996-3016`，把"下一步需要多少新 token"交给分配器的容量判断）。
3. 若不够，且有刚好要结束的请求：**这一步不撤回，跳过 decode**，下一步再来（【注释】`python/sglang/srt/managers/scheduler.py:5209-5210`「The queued result frees the finishing requests' KV at the end of」，接「this step; decode the rest next step instead of retracting them.」）。
4. 否则 `batch.retract_decode(...)`（`python/sglang/srt/managers/scheduler.py:5229`；实现 `python/sglang/srt/managers/schedule_batch.py:3019-3115`）：

   - **挑谁**：按 `(已生成 token 数, −输入长度)` 从大到小排，从队尾开始弹——即**已生成最少的先撤，同样少时输入最长的先撤**（`python/sglang/srt/managers/schedule_batch.py:3116-3155`，键 `python/sglang/srt/managers/schedule_batch.py:3127`）。【推断】：已生成最少 = 重算损失最小；输入最长 = 一次释放最多。作者自己标了不满意：【注释】`python/sglang/srt/managers/schedule_batch.py:3124`「TODO(lsyin): improve retraction policy for radix cache」（这条 TODO 的现有措辞出自【提交】`4cd64b8ee6`「Auto adjust new ratio」，diff 里写作「TODO(lsyin): improve retraction policy for radix cache」，取代了更早的「TODO(lsyin): improve the priority of retraction」）。
   - **撤到什么程度**：循环直到 `check_decode_mem` 通过（`python/sglang/srt/managers/schedule_batch.py:3029-3031`）；**至少留一个**（【注释】`python/sglang/srt/managers/schedule_batch.py:3033`「Always keep at least one request」；来历【提交】`fb17845723`「fix: unreachable error check in retraction (#15433)」，把旧的"只剩一个时断言还有空间"改成这句，diff 里「Always keep at least one request」）。最后一个都放不下才中止它（`python/sglang/srt/managers/schedule_batch.py:3082-3103`）。
   - **怎么撤**：`release_req` 释放 KV，**不**把它写进树（【注释】`python/sglang/srt/managers/schedule_batch.py:3057`「release memory and don't insert into the tree because we need the space instantly」；落到 `release_kv_cache(..., checkpoint=False)`，`python/sglang/srt/managers/scheduler_components/request_retirer.py:134`），并立刻驱逐"剩余请求数×20"个 token，马上用上新变得可驱逐的内存（`python/sglang/srt/managers/schedule_batch.py:1845-1847`；【注释】`python/sglang/srt/managers/schedule_batch.py:1845`「NOTE(lsyin): we should use the newly evictable memory instantly.」；20 = `SGLANG_RETRACT_DECODE_STEPS`，`python/sglang/srt/environ.py:711`）。
   - **撤回的请求去哪**：`req.reset_for_retract()`（前缀长度清零、`is_retracted=True`、**`output_ids` 不清**，`python/sglang/srt/managers/schedule_batch.py:1530-1557`），回到 `waiting_queue`（`python/sglang/srt/managers/scheduler.py:5270-5271`）；FCFS 下进队尾（§3）。重新 prefill 时输入 = 原 prompt + 已生成的 token（`python/sglang/srt/mem_cache/common.py:194-195` 的缺省 `token_ids`、`python/sglang/srt/managers/schedule_batch.py:1141-1160`）。也就是说**撤回 = 丢掉 KV、保留已生成的 token、之后重新 prefill 它们**。撤回期间客户端的流会怎样，我没追。
   - 有些请求不能撤回（beam 组、PD 解码侧无备份等），直接中止并回错（`python/sglang/srt/managers/schedule_batch.py:3037-3076`）；本稿不展开。

5. **撤回后修正 ratio**：`new_ratio = min(1, (在批请求已生成总数 + 20 × 在批请求数) / (在批请求 max_new 总和 + 1))`（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:43-50`），写回 `new_token_ratio_tracker.current`（`python/sglang/srt/managers/scheduler.py:5251`）。
6. **没撤回的 decode 步**：`decay_step()`，比例每步线性下降（`python/sglang/srt/managers/scheduler.py:5273`、`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:34-35`）。初值 0.7、下限 0.7×0.14=0.098、衰减步数 600（`python/sglang/srt/environ.py:708-710`，计算 `python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:21-32`）。**服务空闲时重置回初值**（`python/sglang/srt/managers/scheduler.py:6119-6120`）。
   - 来历：【提交】`4cd64b8ee6`「Auto adjust new ratio (#708)」（2024-07，Liangsheng Yin；只有标题）把撤回后的比例改成按在批请求的现状重新估计（此前是旧比例加一个固定恢复量）；同一提交把初值从 0.4 改到 0.7、衰减由 0.0001 改 0.001、新增 `retract_decode_steps = 20`（均为 diff 所见）。为什么这些数——没查到（§11）。
   - 读法：这是一个**估计—纠错**回路。平时乐观（比例低，并发高），撞墙后立刻变保守（比例升到"现状的下一步估计"），然后慢慢又乐观回去。【推断】（依据：上面几条代码事实的组合；没有注释这样总结）。

**为什么要两层，而不是装入时就按最坏情况预留**：【推断】按 `max_new_tokens` 全额预留会让并发低得多（大多数请求会提前结束）；撤回把"估错"的代价限定为"重算被撤的那几条"。代码里支持这个读法的间接证据是 §4.2 的两处【注释】（"too conservative"）。

---

## 8. 第七问：prefill 完的请求怎么并入在跑的批？

见 §2 的清点第 2 点。再补三点：

- 并入的是**整批**：`running_batch.merge_batch(last_batch)`（`python/sglang/srt/managers/scheduler.py:4411`；`merge_batch` 在 `python/sglang/srt/managers/schedule_batch.py:3495`）。**不等 prefill 的结果出来**——在 overlap 下，这批请求的首 token 还在 GPU 路上（§2 末，【注释】`python/sglang/srt/managers/scheduler.py:4413`）。
- 并入之前，如果 `last_batch` 里含着刚好在分块的请求，先把它过滤掉（`python/sglang/srt/managers/scheduler.py:4383-4396`），所以中间块永远不会混进 decode 批。
- 并入后的 `running_batch` 在**下一个没有 prefill 可装的圈**才 decode；若该圈又装出 prefill 批，则 decode 再被推迟（§2）。

---

## 9. 推演：A、B、C、E（接上一趟的例子）

下表每行是调度进程的一圈（只写与"跑谁"有关的）。数字都是（设）。

| 圈 | `waiting_queue` | 在跑批 | 选批结果与关键判断 | 依据 |
|---|---|---|---|---|
| 1 | [A] | [B] | 排序：FCFS 不动，但对 A 做一次前缀匹配（命中 0）。装入：`total_tokens` = 18 + 64 + 1 = 83 < 99863.5 ⇒ 过；extend 18 < 块 8192，不分块；扣账：`remaining_total` −(19+64)，`rem_chunk_tokens` 8192→8174；`budget_state`=CONTINUE，队列已空。**产出：A 的 prefill 批**（B 本圈不动，prefill 优先） | §3、§4 |
| 2 | [ ] | [B] + 并入 A → [B, A] | `last_batch` 是 A 的 extend 批 ⇒ 并入；`waiting_queue` 空 ⇒ 闸门直接 `None`；**产出：[B, A] 的一步 decode**（先 `check_decode_mem` 通过） | §2、§4.1、§7.2 |
| 3…n | 空 | [B, A] | 每圈一步 decode；每步 `new_token_ratio` 衰减 | §7.2 |
| k | [C] | [B, A] | C 到达（此时 A 已生成 5 个，尚未结束，设）。内存关：`total_tokens` = 20000+64+1 仍远小于余量，过；但 extend 20000 > 块限 8192 ⇒ 截成第 1 块 8192，`is_chunked`；扣账 `rem_chunk_tokens`→0，`budget_state`=OTHER。**产出：C 第 1 块的 prefill 批；B、A 本圈不 decode** | §4.3、§6.1 |
| k+1 | [ ]（C 在 `chunked_reqs`） | [B, A] | 选批开头：C 第 1 块的 KV 登记进树，`prefix_len`=8192；`add_chunked_req`：第 2 块 8192；**B、A 仍不 decode** | §6.2 |
| k+2 | [ ] | [B, A] | 第 3 块 = 20000−16384 = 3616 ⇒ 最后一块，`add_chunked_req` 返回 `None`，换表；`rem_chunk_tokens` 剩 4576，此时才有余量放别人 | §6.2 |
| k+3 | [ ] | [B, A] + 并入 C → [B, A, C] | 并入，之后每圈 decode 三条 | §8 |
| m | [E] | 吃紧 | E 的 `total_tokens` = 200+64+1 = 265 ≥ 此刻 `remaining_total`（设 150）⇒ `NO_TOKEN`，E 留队，`batch_is_full=True`；之后每圈被闸门挡掉 | §7.1 |
| m+x | [E] | 吃紧 | decode 步 `check_decode_mem` 不过 ⇒ 撤回：排序键 `(已生成, −输入长)` 为 B(40,−40)、C(10,−20000)、A(5,−18)（设）；从队尾弹 ⇒ **A 先被撤**；A 回 `waiting_queue` 队尾；若撤一条就够，ratio 重估 = (40+10+20×2)/(200+64+1)≈0.34（设 C 的 `max_new_tokens`=64） | §7.2 |

（第 k+2 到 k+3 圈之间 C 的第 3 块结果有"晚一拍"，此处未画，见上一趟站 7。）

**读者层面的两个提醒**：表里 k～k+2 三圈 B、A 完全停摆——这是默认配置（prefill 优先、不混批）的真实表现，不是推演的错觉；E 被挡与 A 被撤，是同一件事（KV 吃紧）在**装入**与**decode** 两个时间点的两种收场。

---

## 10. 值得做成源码页的代码段（6 段 + 1 段备选）

每段给：文件与行、做什么、值得批注的行及"为什么"。我只指出行，不替你决定怎么排版。

### 页 1：`get_next_batch_to_run` 的主干

`python/sglang/srt/managers/scheduler.py:4383-4456`（73 行；4430-4442 的 spec+DP 注意力分支可略去）

做什么：合并上一圈的 prefill 批、清点在跑批、装 prefill、没有就 decode。

批注行：
- `4391-4396`：先把 `last_batch` 里正在分块的请求过滤掉；批变小则 `batch_is_full=False`。为什么：中间块不能混进 decode 批（§8）。【推断】
- `4403-4411`：并入并换表；在跑批为空时直接把 `last_batch` 当在跑批。
- `4413-4418`：「Every step, not only after a merge」——**每圈**都过滤。为什么：【注释】`python/sglang/srt/managers/scheduler.py:4413`（overlap 下并入先于结果处理，`update_running_batch` 可能根本不跑）。
- `4422`：`_should_defer_prefill` 分支，默认不触发（§2）。
- `4443-4445`：【注释】`python/sglang/srt/managers/scheduler.py:4444`「Run prefill first if possible」。
- `4446-4456`：没有 prefill 批才 decode；纯 prefill 批（打分请求）不 decode。

### 页 2：前置闸门与排序

`python/sglang/srt/managers/scheduler.py:4598-4661`（66 行）

批注行：
- `4606-4609`：批满或队列空即返回。
- `4636-4645`：请求槽用完 ⇒ `batch_is_full=True`；**分块请求跳过这一关**（【注释】`python/sglang/srt/managers/scheduler.py:4636`「A chunked request skips this check: it keeps its req_to_token row across」，接着说在 PP 下它可能在别的微批里结束，拦住会泄漏内存）。
- `4647-4661`：【注释】`python/sglang/srt/managers/scheduler.py:4647`——排序在闸门之后。
- `4602-4605`：空批强制清 `batch_is_full`（防死锁，§4.1）。

### 页 3：装入循环

`python/sglang/srt/managers/scheduler.py:4772-4895`（124 行，太长；建议取 `4772-4778` + `4800-4813` + `4845-4849` + `4867-4895`，约 55 行）

批注行：
- `4777`：`budget_state() == OTHER` 才 break——注意 `NO_TOKEN` 不在此 break，而是留给 `add_one_req` 去判（§4.3 第 1、4 点）。
- `4800-4813`：本圈已装的数 ≥ 还能分配的请求数 ⇒ `batch_is_full`；这是"凑满请求槽"的判断。
- `4845`：装入前重新匹配前缀（§4.3 第 3 点）。
- `4876-4895`：`NO_TOKEN` 设 `batch_is_full`；`UNSCHEDULABLE` 单独收集、`continue`；其余 `break`。**其他结果 = 遇到第一个装不下的就停**。

### 页 4：`PrefillAdder` 判一条请求

`python/sglang/srt/managers/schedule_policy.py:2215-2229` + `python/sglang/srt/managers/schedule_policy.py:2243-2323`（共约 95 行；`2243-2323` 内 `dllm` 分支和 `requires_final_shape_check` 分支可略）

批注行：
- `2217-2229`：每请求多留一页（§4.4 第 2 点；【提交】`25549433e8`；【注释】`python/sglang/srt/managers/schedule_policy.py:1351` 的 TODO）。
- `2244`：`extend_len = 整段 − prefix_len`（§5）。
- `2272-2277`：不分块时第一条可超限（【注释】`python/sglang/srt/managers/schedule_policy.py:2272`）。
- `2291-2320`：分块截断；`has_chunked_req` 时拒绝（【注释】`python/sglang/srt/managers/schedule_policy.py:2293`）；非 `exact_chunk_fill` 路径要对页取整（`2307-2314`）。
- `2317-2319`：`is_chunked=True`、`max_new_tokens=0`。

### 页 5：预算的算术

`python/sglang/srt/mem_cache/prefill_budget.py:144-171`、`python/sglang/srt/mem_cache/prefill_budget.py:193-217`、`python/sglang/srt/mem_cache/prefill_budget.py:283-311`（合计约 80 行，是基础形态；SWA/Mamba/链式池各有子类，不在此页）

批注行：
- `91`：类文档串【注释】`python/sglang/srt/mem_cache/prefill_budget.py:91`「Fixed token pool. Offsets include pending allocations and decode headroom.」
- `156-157`：`remaining_total = 可用+可驱逐 − total_offset`——**可驱逐的也算余量**（因为需要时可以淘汰树里的缓存）。为什么 `_available_and_evictable` 把两者相加：【推断】radix 树里的缓存本来就是"随时可让出"的。
- `167-171`：`has_capacity` 要求两个余量都 >0。
- `213`：`total_tokens < remaining_total`——严格小于。
- `302-310`：`immediate = extend(取整) + 1 页 + extra`；`total_offset += immediate + max_new`，`current_offset += immediate`。两本账的区别：总账含"将来要生成的"，当前账只含"眼前要写的"。

### 页 6：decode 内存不够时撤回

`python/sglang/srt/managers/scheduler.py:5198-5232` + `python/sglang/srt/managers/scheduler.py:5268-5282`（约 50 行），配 `python/sglang/srt/managers/schedule_batch.py:3019-3060`（42 行）

批注行：
- `5207`：`not batch.check_decode_mem()`。
- `5209-5212`：有刚结束的请求就不撤回，跳过本步（【注释】`python/sglang/srt/managers/scheduler.py:5209`）。
- `5229`：`retract_decode` 返回 `(撤回列表, 新比例, 需中止列表)`。
- `5251`：写回 ratio；`5271`：回队；`5273`：否则衰减。
- `schedule_batch.py:3029-3037`：循环条件与【注释】`python/sglang/srt/managers/schedule_batch.py:3033`「Always keep at least one request」。
- `schedule_batch.py:3057`：释放时不写树（【注释】）。

### 备选页 7：排序策略

`python/sglang/srt/managers/schedule_policy.py:343-390`（48 行）。讲 `_determine_active_policy`、「match anyway」注释、策略分派。默认 FCFS 路径很短，页的价值主要是"可选策略长什么样"。

---

## 11. 没查到为什么

我查了代码注释和提交说明，没有找到写明的理由；不补猜测。

1. **`new_token_ratio` 初值 0.7、下限因子 0.14、衰减步数 600、撤回预留 20 步**（`python/sglang/srt/environ.py:708-711`）：`4cd64b8ee6` 把 0.4 改 0.7、0.0001 改 0.001、新增 20，提交只有标题「Auto adjust new ratio」。数值选取理由没查到。
2. **`WAITING_QUEUE_PREFIX_MATCH_MAX = 128`**：只有注释（`python/sglang/srt/managers/schedule_policy.py:143`「Arbitrary; bounds per-round prefix work on the waiting queue.」）；`BOUNDED_LPM_WINDOW_SIZE = 64`、in-batch 的两个 32 阈值同样没有理由。
3. **同一时刻只能有一条分块请求**：注释只说"只能跟踪一条"，没说为什么不能多条。
4. **撤回的请求回队尾（FCFS 下排在新来的后面）是不是有意为之**：代码走的是 `move` 默认入队尾（`python/sglang/srt/managers/scheduler.py:3936` + `python/sglang/srt/managers/scheduler_components/request_list.py:175-181`），没有注释说明这样排是对是错。
5. **装入时"遇第一个装不下的就停"而不是继续找小的**：没有任何注释或提交说明理由；§4.3 写的是我的推断。
6. **prefill 优先于 decode 的原因**：沿用上一趟，代码只有 `python/sglang/srt/managers/scheduler.py:4444` 的「Run prefill first if possible」。
7. **`max_prefill_tokens` 的帮助文字说“实际上限取该值与模型最大上下文长度中的较大者”**（【文档】`python/sglang/srt/arg_groups/fields/schedule.py:77-87`）：我没找到实现这句话的代码位置；是否仍成立未验证。
8. **`max_running_requests` 的默认值**怎么从显存/配置推出来：没追。
9. **默认调度策略改 `fcfs` 的原因**：沿用上一趟 §9 第 1 条。
10. 提交说明普遍只有标题，PR 正文无法访问；以上凡写"只有标题"的，不排除 PR 里有理由。

---

## 12. 读者层面的提示（给讲解员）

- **最该先建立的一个印象**：一圈只选**一种**批。读者看到"装入""撤回""合并"很容易以为它们同时发生。
- **最容易迷糊的三处**：(a)“prefill 优先 + 默认不混批 ⇒ 长 prompt 期间在跑的请求停摆”；(b)`batch_is_full` 是粘住的标志，不是每圈算出来的；(c) 装入失败是**留队等**，decode 失败才是**撤回**——两个时间点不要混在一张图上。
- **两件最好对照着看**：§4.2 的"总量/当前"两本账；§7.2 的"撤回后 ratio 上调 → 之后衰减"。
- **名不副实**：`max_prefill_tokens` 在默认配置下基本不是瓶颈（§4.2）；“出队”其实是换表（§1）。
- **效果在别处**：分块的中间块没有输出（§6.2）；FCFS 下的前缀匹配是为指标而不是排序（§3.3）。
- 本稿没有碰：GPU 前向与采样、radix 树的数据结构实现、语法约束、多卡、PD 分离、LoRA、HiCache、beam、投机解码、优先级抢占。需要的话它们各是一个独立的下一层。

---

## 13. 可改进处【观点】

1. 【观点】撤回挑选只看"已生成数 + 输入长度"，作者已在 TODO 里自认不理想（`python/sglang/srt/managers/schedule_batch.py:3124`）；可以把"这条请求的前缀有多少在树里能复用"算进去，撤回代价才算准。
2. 【观点】“prefill 优先 + 不混批”让长 prompt 期间在跑请求停摆，默认不开混批的代价由用户承担；至少 `prefill_decode_interval` 在文档里该提示这个现象。
3. 【观点】`max_prefill_tokens` 与 `chunked_prefill_size` 两个旋钮在默认配置下前者松着，容易让人误调；文档串可以说明它们的关系。
4. 【观点】页取整的 TODO（`python/sglang/srt/managers/schedule_policy.py:1351`）说明预算偏保守；若实测并发偏低，这是第一个可查的地方。
