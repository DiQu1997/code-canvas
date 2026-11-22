# 调度循环（Scheduler 主循环）：下一层底稿

> 研究对象：sglang 仓库 HEAD = `c569c43446`。接着上一趟顶层底稿（下称「上一趟」）的 §4 往下挖，只讲「调度循环」这一个框。
> 锚点一律是相对仓库根的 `路径:行号`（或 `路径:起-止`），路径写全，便于核对。
> 每条「为什么」后标来源：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 例子沿用上一趟 §8 的请求 A（rid=`9f3c…`，prompt 20 个 token，前缀命中 5，`max_new_tokens=32`，第 5 个输出 X5 是 EOS）。**凡标「设」的数都不是代码常量。** 本趟新补设一个更早的请求 B，用来演示「按长度结束」和「并批」。
> 上一趟的 §4.1 里有一句话需要修正，见本稿 §6.3。

---

## 0. 先给结论（一屏版）

调度循环是 Scheduler 子进程**主线程里的一个死循环**。默认（overlap）版每圈做 5 件事：

1. **收**：把 ZMQ 管道里的新请求抽干，造成 `Req` 放进 `waiting_queue`。
2. **选批**：决定这一圈「跑什么」——有新请求能拼成 prefill 批就先跑 prefill，否则让 running batch 跑一步 decode。
3. **发射**：`run_batch` 把这批交给 GPU，**不等算完**，只把「结果句柄」放进 `result_queue`。
4. **处理上一圈的结果**：从 `result_queue` 取出上一批，把 token 记到 `Req` 上、判断结束、通知 Detokenizer。
5. 记下 `last_batch`，下一圈用。

核心难点只有一句话：**第 4 步处理的是上一圈发射的批；而第 2 步选批时，上一批的结果还没处理。** 所以选批、发射都得在「不知道上一步采到了什么 token、有没有结束」的情况下做。围绕这一点，代码里长出了 4 个装置：

| 问题 | 装置 | 一句话 |
|---|---|---|
| 下一步 decode 的输入 token 还在 GPU 上 | `FutureMap` | 一张按 `req_pool_idx` 索引的 GPU 小表：上一步写、下一步读，token 不回 CPU |
| 按长度结束的请求，下一步又被发射一次 | `inflight_steps` + `_reqs_finishing_inflight` | 看「在飞的这一步」会不会正好写满 `max_new_tokens`，会就不再发射 |
| 采到 EOS 无法预知 | 白算一步 + `still_in_batch` 过滤 | 多算的那一行结果到了直接丢掉 |
| prefill 批的请求并进 running batch 时结果还没处理 | 每圈 `filter_batch()` | 每圈都把已结束的请求滤掉 |

另外两条线贯穿其中：**显存预算**（`PrefillAdder` 管「放不放得进来」，`check_decode_mem` + retract 管「放进来后长不长得动」）和**一个请求同一时刻只属于一张名单**（`waiting_queue / running_reqs / prefill_launched_reqs / chunked_reqs`）。

---

## 1. 这个框里有哪些东西（部件与状态）

读者要把它们画成框和线，先给清单。**状态都挂在 `Scheduler` 对象上**，循环本身只是在反复读写它们。

| 部件 / 状态 | 是什么 | 谁写、谁读 | 锚点 |
|---|---|---|---|
| `waiting_queue` | 还没开始 prefill 的请求名单（可重排序） | `handle_generate_request` 入队；选批时按策略重排、取走；retract 的请求回到这里 | `python/sglang/srt/managers/scheduler.py:1406`；重排 `:4651` |
| `running_batch` | 「正在 decode 的那一大批」，一个 `ScheduleBatch` | 选批时合并新 prefill 批、过滤、`prepare_for_decode` | `python/sglang/srt/managers/scheduler.py:1410` |
| `running_reqs` / `prefill_launched_reqs` / `chunked_reqs` | 请求的「所属名单」：已并入 running batch / prefill 最后一块已发射 / 正在分块 prefill 的那一个 | `move(...)` 在名单间搬；`still_in_batch` 读 | `python/sglang/srt/managers/scheduler.py:1413`、`:1417`、`:1467` |
| `last_batch` | 上一圈发射的批 | 圈末写，下一圈选批读 | `python/sglang/srt/managers/scheduler.py:1421`、`:2303` |
| `result_queue` | 已发射、结果未处理的批（`batch.copy()` + 结果句柄） | `run_batch` 之后入队，同圈稍后出队处理 | `python/sglang/srt/managers/scheduler.py:2238-2240`、`:2284` |
| `future_map` | 按 `req_pool_idx` 索引的 GPU 缓冲，中转跨圈的值 | 前向之后写（`publish`/`stash`），下一次发射前读 | `python/sglang/srt/managers/overlap_utils.py:309` |
| `new_token_ratio_tracker` | 一个 0~1 的「decode 还会长多少」估计，给预算用 | decode 成功一步就衰减；retract 后重估；空闲时复位 | `python/sglang/srt/managers/scheduler.py:1603` |
| `PrefillAdder` | 一次选批里临时造的「预算员」，决定哪些请求进这个 prefill 批 | `_get_new_batch_prefill_raw` 里建、逐个 `add_one_req` | `python/sglang/srt/managers/schedule_policy.py:1009` |
| 三条 CUDA 流 | `schedule_stream`（CPU 线程当前流）/ `forward_stream`（前向）/ `copy_stream`（结果拷回 CPU） | `run_batch` 里用 `wait_stream` 排序 | `python/sglang/srt/managers/scheduler.py:2172`、`:5433`、`:5831` |

与框外的接口（只点一句）：
- 请求从 ZMQ 来：`ingest_requests`（`python/sglang/srt/managers/scheduler.py:2338`），见上一趟 §4.2。
- 前向 + 采样在 `TpModelWorker`，见上一趟 §4.6。
- KV 三层存储（`req_to_token_pool` / `token_to_kv_pool_allocator` / `tree_cache`），见上一趟 §4.5。
- 结果发给 Detokenizer：`output_streamer.stream_output`，见上一趟 §4.7；本稿只讲「发不发」的判断（§7.4）。

**一个请求同一时刻只在一张名单里**。【注释】`python/sglang/srt/managers/scheduler.py:5075-5076` 原文：「The lists that hold the live requests, by kind. A request is in at」+「most one.」。这是后面 `still_in_batch` 能用「是否还在名单里」判断「这个请求还活着吗」的前提。

---

## 2. 一圈的全貌：overlap 版 vs 不 overlap 版

### 2.1 两个循环并排

不 overlap（`--disable-overlap-schedule`）：`python/sglang/srt/managers/scheduler.py:2200-2232`

```
收 → 选批 → run_batch → 立刻 process_batch_result → last_batch=batch
```
`run_batch` 和 `process_batch_result` 是背靠背的两行：`:2223-2224`。

overlap（默认）：`python/sglang/srt/managers/scheduler.py:2235-2305`

```
收 → [可选：先处理上一批] → 选批 → run_batch → result_queue.append → 处理上一批 → last_batch=batch
```
关键差别只有一处：**`process_batch_result` 从「发射之前/紧跟发射」挪到了「下一批发射之后」**（`:2281-2292`）。GPU 在算第 N+1 批的时候，CPU 在做第 N 批的结果处理（记 token、判结束、发给 Detokenizer）。

- 【注释】`python/sglang/srt/managers/scheduler.py:2236` 原文："A scheduler loop that overlaps the CPU processing and GPU computation."
- 【注释】`python/sglang/srt/managers/scheduler.py:5881` 在 `process_batch_result` 里写明收益：「in overlap mode this CPU work runs while」（下一行接 "the next batch's GPU forward is in flight, giving free overlap."）。
- 【文档】`python/sglang/srt/arg_groups/fields/schedule.py:215` 原文："Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker."（默认不禁用：`:212` 起的 `] = False`，开关取值落在 `python/sglang/srt/managers/scheduler.py:573-575`。）
- 循环选择：`python/sglang/srt/managers/scheduler.py:7050-7063`（非 PD、pp=1 时 `enable_overlap` 真走 overlap 版，否则 normal 版）。
- 历史：overlap 循环最早是【提交】`9116b2896f`「Add a new event loop (#1677)」（2024-10）。提交说明只有标题。

### 2.2 overlap 版一圈里的 5 个细节（都值得读源码页）

① **只有 `last_batch` 非空才处理上一批**：`if self.last_batch: if not process_last_batch_early: pop_and_process()`（`python/sglang/srt/managers/scheduler.py:2290-2292`）。所以「这一圈没选出新批（`batch is None`）但上一圈发射过」时，**这一圈仍会处理上一批的结果**；只有两者都空才走 `on_idle`（`:2293-2295`）。这就是请求最后一个 token 的结果怎么被「尾随处理」掉的。

② **入队的是 `batch.copy()` 而不是 `batch`**（`:2284`）。【注释】`python/sglang/srt/managers/schedule_batch.py:3566` 原文："Only contain fields that will be used by process_batch_result."；原 batch 下一圈会被 `filter_batch / merge_batch / prepare_for_decode` 就地改，结果处理要看的是「发射那一刻」的请求名单。`copy()` 里连 `launched_reqs` 一起带走（`python/sglang/srt/managers/schedule_batch.py:3573`）。

③ **发射后紧跟一个栅栏**：`self._apply_war_barrier()`（`python/sglang/srt/managers/scheduler.py:2283`）。【注释】`python/sglang/srt/managers/scheduler.py:2282` 原文："Fence result processing behind this forward's shared reads."。它让 schedule 流上之后的写（比如下一圈改共享缓冲）排在本次前向「读共享缓冲」之后（`python/sglang/srt/managers/scheduler.py:2176-2188`）。具体哪些缓冲、何时才需要，我只读到入口，没追到底，不展开。

④ **`launch_batch_sample_if_needed` 在处理完上一批之后**（`:2299-2300`）。【注释】`:2298` 原文："# It depends on the result of the last batch (e.g., grammar), so we run it after the last batch is processed."——结构化输出的 mask 依赖上一步的 token，所以「采样」被推迟到这里（本稿主路不走，只点名）。

⑤ **有时「先处理、再选批」**：`should_process_last_batch_early`（`python/sglang/srt/managers/scheduler.py:2258-2262`、`:2307-2326`）。默认只有 `last_batch.grammar_needs_sync()` 为真才提前（`:2326`）。【注释】`:2310-2311` 原文：「Whether to process the last result before scheduling the next batch,」/「not after launching it, so the requests it finishes are never launched.」——提前处理的目的就是**让上一批里已结束的请求别再被发射一次**。另有环境变量 `SGLANG_DISABLE_OVERLAP_AFTER_PREFILL`（默认 `False`：`python/sglang/srt/environ.py:761`），打开后 prefill 之后的那圈也提前处理，【注释】`python/sglang/srt/managers/scheduler.py:2315` 原文："A prefill's result goes early, for its TTFT."。
- 来历：【提交】`1c0dc0293d`「[Fix] scheduler: Decide early result processing before scheduling the next batch (#47716)」把「是否提前处理」的决定从「选批之后」挪到「选批之前」；diff 里被删掉的旧函数叫 `is_disable_overlap_for_batch`，旧开关叫 `SGLANG_DISABLE_CONSECUTIVE_PREFILL_OVERLAP`（现在 `python/sglang/srt/environ.py:2783` 有一条旧名到新名的改名提示）。提交说明只有标题，「为什么挪到选批之前」的原因只能从标题和新注释（`python/sglang/srt/managers/scheduler.py:2311`）推：先处理再选批，才能让选批看到上一批已结束的请求。【推断】

### 2.3 三条 CUDA 流各管什么

循环整体跑在 `schedule_stream` 里：`with self.device_module.StreamContext(self.schedule_stream)` 包住 `dispatch_event_loop`（`python/sglang/srt/managers/scheduler.py:2172-2174`）。`run_batch` 里先进 `forward_stream`，并让它先等 schedule 流（`:5432-5433`），再做输入解析和前向；结果拷回 CPU 另用 `copy_stream`，它等 forward 流（`:5831`），但 forward 流**不等它**。【注释】`python/sglang/srt/managers/scheduler.py:5820` 原文："Issue the result readback on copy_stream so it overlaps the next forward."

为什么要分流：schedule 流上做的是 CPU 线程提交的小活（改缓冲、拼下一批），forward 流是大前向，copy 流是搬运；拆开才能让「搬上一批的结果」和「算下一批」在 GPU 上并行。【推断】依据：`run_event_loop` 里专门防止 schedule 流和 forward 流撞成同一条：【注释】`python/sglang/srt/managers/scheduler.py:2160` 原文："stream aliases forward_stream, which would eliminate scheduler"（接下一行 "overlap."）；真正重抽流的是 `python/sglang/srt/managers/scheduler.py:2165-2168` 的 `elif self.schedule_stream.cuda_stream == self.forward_stream.cuda_stream` 分支。

---

## 3. 选批：`get_next_batch_to_run`

入口 `python/sglang/srt/managers/scheduler.py:4339`。它**既返回要跑的批，也返回更新后的 `running_batch`**（`NextBatchPlan`，`:4476`）。顶层三步：

### 3.1 第一步：把上一圈的 prefill 批并进 running batch

条件：`last_batch` 是 extend（prefill）（`python/sglang/srt/managers/scheduler.py:4378-4382`）。做法：
1. 把分块 prefill 中「还没完」的那一个（`chunked_req`）先排除（`:4383-4386` 排除、`:4358` 也在更早处把它放进 `reqs_to_exclude`），因为它还没产出第一个 token，不能去 decode；
2. `last_batch.filter_batch(...)` 滤掉已结束的（`:4393`）；
3. 剩下的请求用 `move(..., self.running_reqs)` 改挂「在 decode」名单（`:4403-4405`），并 `running_batch.merge_batch(last_batch)`（`:4410-4411`；`running_batch` 为空时直接把 `last_batch` 当 `running_batch`）。

读者最易困惑：**overlap 下，P 批的结果此时还没处理**（它刚好排在这一圈「发射之后」才被处理）。所以此刻并进去的请求，可能其实在 P 的结果里已经结束了（比如第一个 token 就是 EOS）——选批时还不知道。

### 3.2 第二步：每圈都再滤一次（为什么？）

```
running_bs = running_batch.batch_size()
running_batch.filter_batch()
if running_batch.batch_size() < running_bs: running_batch.batch_is_full = False
```
（`python/sglang/srt/managers/scheduler.py:4413-4418`）。`filter_batch()` 不带参数时保留「没结束」的行（`python/sglang/srt/managers/schedule_batch.py:3413-3419`，条件是 `not self.reqs[i].finished()`）。

- 【注释】`python/sglang/srt/managers/scheduler.py:4413-4414` 原文："Every step, not only after a merge: overlap merges a prefill's requests"/"before its result finishes them, and update_running_batch may never run."
- 来历：【提交】`3b5ae4ea86`「[Fix] scheduler: Drop finished requests from the running batch before every admission (#44351)」。diff 里被替换掉的旧写法只在 `running_batch.is_prefill_only` 时滤（旧注释说是为了 `/v1/loads` 报告的 `num_running_reqs` 准确）；新写法无条件滤。提交说明只有标题。该提交新增了一个 CPU 单测文件 `test_scheduler_running_batch_finished.py`，首行文档串大意是「每一步都在收新请求之前，先把已结束的请求移出 running batch」（该文件在 HEAD 已不在原路径，我只在该提交里看到，所以这里只转述、不当作逐字引文，也不给 HEAD 锚点）。
- 为什么要滤了才能往下走：`batch_is_full`（running batch 满了就不再收新 prefill）是个会「卡住」的标志，只有「有请求离开」才清；过滤发现少了行就清掉（`:4417-4418`）。若不清，已空出位置却一直不收新请求。【推断】依据：`_get_new_batch_prefill_raw` 里的注释 "An empty batch has no request left to leave, and only a leaving"/"request clears the flag elsewhere, so admission would stay shut."（`python/sglang/srt/managers/scheduler.py:4602-4603`，这条注释写的就是同一逻辑）。

### 3.3 第三步：新 prefill 优先，否则 decode

```
if 新 prefill 批 new_batch 非空: ret = new_batch      # "Run prefill first if possible"
else: ret = update_running_batch(running_batch)       # decode 一步
```
`python/sglang/srt/managers/scheduler.py:4422-4456`；【注释】`:4444` 原文："Run prefill first if possible"。

- **为什么 prefill 优先**：没查到明确理由。最早一次出现这行注释是【提交】`c211e7b669`「Simplify batch update (#2154)」（2024-11），只有标题。我的推测是首 token 延迟（TTFT）对用户最敏感，【推断】；代价是 prefill 那一圈 running batch 里的请求不 decode（空一拍）。缓解手段：分块 prefill（见上一趟 §4.4）；`prefill_decode_interval`（prefill 后先连续 decode 若干圈再收下一批，**默认关**，仅部分 Qwen3-VL/Hopper 配置自动开：`python/sglang/srt/arg_groups/fields/schedule.py:62-67`；生效逻辑 `_should_defer_prefill` 在 `python/sglang/srt/managers/scheduler.py:4422` 调用、定义 `:1493`，prefill 批选出后在 `:1525` 装填计数）；`enable_mixed_chunk`（prefill 与 decode 混在一个批里，默认关：`python/sglang/srt/arg_groups/fields/schedule.py:227`，混入分支 `python/sglang/srt/managers/scheduler.py:4998-5016`）。
- 例子里的请求 B：见 §9。

### 3.4 什么时候「根本没有 prefill 可选」

`_get_new_batch_prefill_raw` 开头有两个早退：`running_batch.batch_is_full` 或 `waiting_queue` 空，且没有正在分块的请求，就返回 `None`（`python/sglang/srt/managers/scheduler.py:4606-4609`）；以及 `num_allocatable_reqs <= 0`（`req_to_token_pool` 的行或 PP 微批名额用完）时把 `batch_is_full` 置真（`python/sglang/srt/managers/scheduler.py:4639-4645`）。注释写明这是为了「满批那一圈不扫等待队列」：【注释】`python/sglang/srt/managers/scheduler.py:4647` 原文："# After the early returns, so a full-batch pass does not scan the queue."

---

## 4. prefill 批怎么拼：`PrefillAdder` 的预算

### 4.1 先看流程

`_get_new_batch_prefill_raw`（`python/sglang/srt/managers/scheduler.py:4586`）：
1. 按策略给 `waiting_queue` 重排（默认 `fcfs`：`python/sglang/srt/arg_groups/fields/schedule.py:109`；重排调用 `python/sglang/srt/managers/scheduler.py:4651`）。
2. 建 `PrefillAdder`（`python/sglang/srt/managers/scheduler.py:4686`），传入：页大小、`tree_cache`、分配器、`running_batch`、`new_token_ratio`（`python/sglang/srt/managers/scheduler.py:4691`）、`max_prefill_tokens`（`python/sglang/srt/managers/scheduler.py:4692`，默认 16384：`python/sglang/srt/arg_groups/fields/schedule.py:87`）、分块大小等。
3. 若有上一块没跑完的分块请求，先让它续跑一块：`adder.add_chunked_req(resumed_req)`（`python/sglang/srt/managers/scheduler.py:4711-4718`）。
4. 遍历候选：每个请求先 `req.init_next_round_input(self.tree_cache, ...)` 做前缀匹配，再 `adder.add_one_req(...)`（`python/sglang/srt/managers/scheduler.py:4867`）。返回 `CONTINUE` 就继续看下一个；其它结果（`NO_TOKEN` / `OTHER` / `UNSCHEDULABLE`，定义 `python/sglang/srt/managers/schedule_policy.py:944-950`）一般就 `break`（`python/sglang/srt/managers/scheduler.py:4876-4905`）。
5. `can_run_list` 非空就 `ScheduleBatch.init_new(...)`（`:4955`）+ `prepare_for_extend()`（`:4978`，这一步才真正分 KV 槽，见上一趟 §4.5）。

### 4.2 预算的核心公式（默认配置）

「预算」本体是 `PrefillBudget`（默认分配器返回它：`python/sglang/srt/mem_cache/allocator/base.py:115-120`；SWA 等特殊分配器用别的子类，本稿不管）。【注释】`python/sglang/srt/mem_cache/prefill_budget.py:91` 原文："Fixed token pool. Offsets include pending allocations and decode headroom."

两个「已承诺但尚未真分配」的计数器：
- `total_offset`：**含**这个请求将来要 decode 的那部分（`max_new_tokens`）。
- `current_offset`：只含眼前 prefill 要的槽位。

```
remaining_total   = (分配器可用 + 前缀树可淘汰) − total_offset     # python/sglang/srt/mem_cache/prefill_budget.py:156-157
remaining_current = (分配器可用 + 前缀树可淘汰) − current_offset   # python/sglang/srt/mem_cache/prefill_budget.py:160-161
```
- 能不能收：`total_tokens < remaining_total`（`python/sglang/srt/mem_cache/prefill_budget.py:213`）。`total_tokens = 还要算的输入长度 + 这个请求 decode 预留 + per_req_token_overhead(+slot_reserve)`（`python/sglang/srt/managers/schedule_policy.py:2220-2226`）。
- 收下之后「记账」：`total_offset += 眼前槽位 + 一页 + max_new_tokens`、`current_offset += 眼前槽位 + 一页`（`python/sglang/srt/mem_cache/prefill_budget.py:309-310`）；同时扣计算预算：`rem_input_tokens -= compute_charge`、`rem_chunk_tokens -= compute_charge`（`python/sglang/srt/managers/schedule_policy.py:1385-1390`）。
- 「还能不能再收一个」：`budget_state()`：内存预算不再允许（`admits_more` 假）→ `NO_TOKEN`；`rem_input_tokens <= 0` 或 `rem_chunk_tokens <= 0` → `OTHER`；否则 `CONTINUE`（`python/sglang/srt/managers/schedule_policy.py:1304-1319`）。**所以三道闸**：显存（KV 槽）、`max_prefill_tokens`（一批总输入 token）、`chunked_prefill_size`（一批总「这一块」token）。分块：一个请求超出本块额度，就只取一部分、标 `is_chunked`（`python/sglang/srt/managers/schedule_policy.py:2291-2317`）。
- 第一个请求即使超过 `max_prefill_tokens` 也放行（未开分块时）：【注释】`python/sglang/srt/managers/schedule_policy.py:2272` 原文："Without chunking, allow the first request even above the input cap"（避免长请求永远排不上）。

### 4.3 running batch 里的 decode 要预留多少：`new_token_ratio`

建 `PrefillAdder` 时，给**已经在 running_batch 里的每个请求**也扣一笔「它还要长多少」：`total_offset += Σ _get_running_request_total_token_offset(r)`（`python/sglang/srt/managers/schedule_policy.py:1095-1100`）。

```
offset(r) = min(max_new_tokens − 已生成数, CLIP_MAX_NEW_TOKENS) × ratio       # python/sglang/srt/managers/schedule_policy.py:1174-1184
ratio = 1.0 if ignore_eos else new_token_ratio                                # :1176
```
- `CLIP_MAX_NEW_TOKENS` 默认 4096（环境变量 `SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION`：`python/sglang/srt/managers/schedule_policy.py:96-98`）。【注释】`python/sglang/srt/managers/schedule_policy.py:92-93` 原文："Clip the estimation of max_new_tokens for the request whose max_new_tokens is very large."/"This can prevent the server from being too conservative."（只是削估计，不改停止条件）。
- `ratio = new_token_ratio`：假设大部分请求会早早遇到 EOS，只预留 `max_new_tokens` 的一部分；`ignore_eos` 的请求必然跑满，所以按 1.0。【注释】`python/sglang/srt/managers/schedule_policy.py:1175` 原文："# Fixed-length requests cannot benefit from the early-EOS estimate."
- `new_token_ratio` 的生命周期（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py`）：初值 `SGLANG_INIT_NEW_TOKEN_RATIO`（0.7）×`schedule_conservativeness`（1.0），封顶 1（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:22-26`）；下限 = 初值×`SGLANG_MIN_NEW_TOKEN_RATIO_FACTOR`（0.14）（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:27-30`）；每个**没触发 retract 的 decode 步**线性衰减一格，`SGLANG_NEW_TOKEN_RATIO_DECAY_STEPS`=600 步从 0.7 降到约 0.098（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:31`、`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:34-35`，调用 `python/sglang/srt/managers/scheduler.py:5273`）；**触发 retract 后**直接重估（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:41-49`，见 §8）；服务空闲时复位（`python/sglang/srt/managers/scheduler.py:6120`）。默认值：`python/sglang/srt/environ.py:708-711`。
- 为什么这样设计：预算要「乐观到不浪费显存、悲观到不频繁 retract」。这是个经验折中，提交里没有数据说明，没查到更深的理由。调参入口是 `--schedule-conservativeness`，【文档】`python/sglang/srt/arg_groups/fields/schedule.py:160` 原文："Use a larger value if you see requests being retracted frequently."

### 4.4 代码里自己承认的保守

【注释】`python/sglang/srt/managers/schedule_policy.py:1351`（`_update_prefill_budget` 里）原文："TODO(lsyin): check this workaround logic, which only ensures the prefill will not out of memory, and may be too conservative"。

### 4.5 例子：请求 A 收进 prefill 批（数值全是设）

设：分配器可用 40,000 token、前缀树可淘汰 2,000；`page_size=1`；`max_prefill_tokens=16384`；`chunked_prefill_size=8192`；`new_token_ratio=0.7`；B 在 running_batch，已生成 6 个、`max_new_tokens=8`。
- 建 adder 时 B 占预算：`min(8−6, 4096)×0.7 = 1.4`（`total_offset≈1.4`）。
- A：还要算 `20−5=15`，`max_new = min(32,4096)=32`，`total_tokens = 15+32+1(overhead)=48`。`remaining_total = 42000−1.4 ≈ 41998.6 > 48` → 收。
- 记账：眼前槽位 `15+1(页)=16`，`total_offset += 16+32=48`（→≈49.4），`current_offset += 16`；`rem_input_tokens = 16384−15 = 16369`，`rem_chunk_tokens = 8192−15 = 8177`。
- 等待队列空了，循环结束，`can_run_list=[A]`。

---

## 5. decode 这一路：`update_running_batch`

`python/sglang/srt/managers/scheduler.py:5194-5284`。顺序（顺序本身有讲究）：

1. **先剔掉「在飞的这一步已经会写满长度」的请求**：`finishing_reqs = self._reqs_finishing_inflight(batch)` → `batch.filter_batch(reqs_to_exclude=finishing_reqs)`（`:5198-5199`）。
2. 再做**显存检查**：`kv_full_retract_flag = not batch.check_decode_mem()`（`:5207`）。
3. 不够就 **retract**（§8）；够就 `new_token_ratio_tracker.decay_step()`（`:5273`）。
4. `batch.prepare_for_decode()`（`:5282`）：真正给每个请求分 1 个 KV 槽、`seq_lens+1`。

### 5.1 为什么要先「预知」按长度结束（`inflight_steps`）

overlap 下，选批时一个请求「还在 running batch 里」的含义是：它**最新的一步已经发射、结果未处理**。如果它在那一步里写满了 `max_new_tokens`，那么再发射一步就是纯浪费，而且这一步的结果还会被丢掉。

做法：
- 每个 `Req` 有 `inflight_steps`：已发射、结果未处理的步数（【注释】`python/sglang/srt/managers/schedule_batch.py:684-685` 原文："# The launched steps carrying this request whose results are not"/"processed yet."，初值 `:686`）。
- 发射时 `batch.add_inflight_step()`（`python/sglang/srt/managers/scheduler.py:5142`，实现 `python/sglang/srt/managers/schedule_batch.py:2255-2258`）；**处理完**结果时 `batch.drop_inflight_step()`（`python/sglang/srt/managers/scheduler.py:5171`，实现 `python/sglang/srt/managers/schedule_batch.py:2260-2262`）。
- 判断：`req.inflight_steps > 0 and req.beam_group is None and req.next_output_finishes_by_length()`（`python/sglang/srt/managers/scheduler.py:5189-5192`），后者即 `len(output_ids)+1 >= max_new_tokens`（`python/sglang/srt/managers/schedule_batch.py:1130-1132`）。
- 开关：`enable_skip_finishing_decode` 只有进了 overlap 循环才为真（`python/sglang/srt/managers/scheduler.py:2237`），其它循环为假（`:7053`），为假时直接返回空列表（`:5184-5185`）。
- 来历：【提交】`e14ef4b658`「[Scheduler] Skip the decode for a request whose last token is already in flight (#42720)」（2026-10）。提交说明只有标题与 co-author。
- 【测试】`test/registered/scheduler/test_overlap_skip_finishing_decode.py:1-2` 文档串原文："The overlap scheduler skips the decode of requests whose queued result"/"already carries their max_new_tokens-th token."；`:80-81` 注释原文："# One prefill emits the first token; each later token needs one decode,"/"# and no decode runs for the token already in flight."。断言（`:82-88`）：8 个请求、`max_new_tokens=4`、`ignore_eos`，一次批请求共 **1 次 prefill + 3 次 decode**（`MAX_NEW_TOKENS − 1`）。

**用 A 验算**（`max_new_tokens=32`）：发射 D_k 时，已处理的输出数是 `k−1`、在飞 1 步，条件 `k−1+1 >= 32` 在 k=32 成立，于是 D_32 不发射；总共 prefill 出 X1、D1…D31 出 X2…X32，刚好 32 个。

### 5.2 与显存检查的交互

【注释】`python/sglang/srt/managers/scheduler.py:5209-5210` 原文："The queued result frees the finishing requests' KV at the end of"/"this step; decode the rest next step instead of retracting them."——若 `check_decode_mem` 不够、而本圈刚好剔除了一批「马上结束」的请求，就**不 retract**，直接返回 `None`（这圈不发射 decode，`:5208-5212`）；等排队的结果处理完、释放了这些请求的 KV，下一圈再试。

---

## 6. 发射：`run_batch` 与 `FutureMap`——「下一步的输入从哪来」

### 6.1 `FutureMap` 长什么样

它是 `python/sglang/srt/managers/overlap_utils.py:309` 的一个类。【注释】`:310-312` 原文："Always-on pool-indexed relay for cross-iter values. Forward writes via"/"publish/stash; next iter reads via resolve_forward_inputs / resolve_seq_lens_cpu."

主路（不开投机解码）下，它实质上是**一条 `int64` 的 GPU 向量 `output_tokens_buf`，长度 = `req_to_token_pool` 的行数**（`python/sglang/srt/managers/overlap_utils.py:345-347`，行数取自 `python/sglang/srt/managers/overlap_utils.py:332`）。下标就是请求的 **`req_pool_idx`**（它占 `req_to_token` 的哪一行）。【注释】`:324-325` 原文："# Bufs indexed by req_pool_idx; slot 0 mirrors KV padding row so"/"# CUDA-graph padded batches (req_pool_idx == 0) are harmless."

读写：
- **写**（上一步）：`run_batch` 里 `_relay_forward_payload`（`python/sglang/srt/managers/scheduler.py:5495`）→ 把 `batch_result.next_token_ids`（还在 GPU 上的采样结果）包成 `RelayPayload(bonus_tokens=...)`（`:5767`）→ `future_map.relay(...)`（`:5777`）→ 最终 `output_tokens_buf[indices] = payload.bonus_tokens`（`python/sglang/srt/managers/overlap_utils.py:800`）。【注释】`python/sglang/srt/managers/scheduler.py:5758` 原文："Stash this iter's relay payload for next iter's resolve_forward_inputs."。
- **读**（下一步）：`resolve_forward_inputs` 里 `batch.input_ids = future_map.output_tokens_buf[batch.req_pool_indices]`（`python/sglang/srt/managers/overlap_utils.py:165-166`）。这是一个 GPU 上的 gather，**不经过 CPU**。【注释】`:127-133` 的文档串：两种来源——prefill 从固定内存的 CPU 暂存 H2D 拷贝（"Prefill: H2D copy from pinned CPU staging (prefill_input_ids_cpu)."，实现 `:142`），decode 从 FutureMap 取（"Decode/spec_v2: gather from FutureMap (last iter's sampled token)."，`:133`）。
- 上一步发射完就把 `batch.input_ids = None`（【注释】`python/sglang/srt/managers/scheduler.py:5510` 原文："Next-iter input_ids relayed via future_map."，赋值 `:5511`），防止下一圈误用旧张量；`prepare_for_decode` 的注释也说明 decode 的 `input_ids` 是个占位（`python/sglang/srt/managers/schedule_batch.py:3338`）。

**为什么 `seq_lens` 不需要中转（主路）**：每个 decode 步每个请求恰好 +1，CPU 自己就知道：`prepare_for_decode` 里 `self.seq_lens = self.seq_lens + 1`、`self.seq_lens_cpu = self.seq_lens_cpu + 1`（`python/sglang/srt/managers/schedule_batch.py:3353-3355`），所以 CPU 不用等 GPU 就能备好下一批的长度。需要 CPU 等 GPU 才知道的是 **token 值本身**（以及投机解码里「接受了几个」）。【推断】依据：上述 `+1` 在 CPU 张量上直接做，且 `resolve_seq_lens_cpu` 只服务带 `spec_info` 的批（`python/sglang/srt/managers/overlap_utils.py:687-704`）。`run_batch` 里仍会 `future_map.publish(future_indices, batch.seq_lens + 1)`（`python/sglang/srt/managers/scheduler.py:5475`），主路里谁读它我没追到。

**不开 overlap 时也走 FutureMap**：`run_batch` 的非 overlap 分支同样 `resolve_forward_inputs` → 前向 → `_relay_forward_payload`（`python/sglang/srt/managers/scheduler.py:5629-5638`），代码里 `FutureMap` 是「always-on」。所以「non-overlap = 没有 FutureMap」不对，差别是没有 forward/copy 流、没有 `copy_done`、不排 `result_queue`。

### 6.2 为什么用 `req_pool_idx` 做下标（设计演变）

- 最初：【提交】`f5a2faf2b8`「Introduce `FutureMap` (#10715)」（2025-09）。当时是一个**环形缓冲**，下标是「未来编号」。diff（见 `512d164916` 的删除行）里写着缓冲大小 `max_running_requests * (3 + max_num_chunks)`，并有一条注释（已被该提交删除，故不作逐字引文）说明环形布局的顺序是：running decode 批 → prefill 第 1 块 → … → 第 N 块，原因是 running decode 批的结果要等所有 prefill 块之后才被消费。
- 现在：【提交】`512d164916`「Address overlap future token map by request-pool index (#25862)」（2026-05）改成按 `req_pool_idx` 索引，删掉了 `alloc_future_indices` 和那一大套容量推算。提交说明只有标题；从 diff 看到的好处有两点：一，缓冲大小直接等于 `req_to_token` 行数，不用再估；二，一个请求只要还占着这一行，它的槽位就一直在，**中间被跳过几圈（比如被插队的 prefill 挡住 decode）值也不会丢**。第二点是我的推断【推断】，依据是旧缓冲需要为「decode 批要等所有 prefill 块之后才被消费」专门预留容量，而按行下标天然不需要。
- 代价：行号会被复用。一个请求结束、行被另一请求拿走后，旧值就是脏的。`SGLANG_IS_IN_CI` 下有「毒化」检查：初始化填 `-1`（`python/sglang/srt/managers/overlap_utils.py:337-343`），每次读完用 `_assert_nonneg_and_invalidate` 断言非负并写回 `-1`（`:92-98`，`_DEBUG_ASSERT` 定义 `:74`）——读到 `-1` 就说明某一行没被写就读了。

### 6.3 EOS 白算一步，以及那一步的 KV 槽到底去哪了（修正上一趟）

上一趟 §4.1 说「多分配的 KV 槽在释放时一并回收」。读更细的代码，这句话**不准确**，实际是下面两种情形之一：

先看账本。decode 一步的分配是在**发射前**做的：`prepare_for_decode` → `alloc_for_decode`（`python/sglang/srt/managers/schedule_batch.py:3346`）→ `req.kv.allocate_decode(1)`（`python/sglang/srt/mem_cache/allocation.py:759`），它**同时**加 `kv_allocated_len` 和 `kv_committed_len`。【注释】`python/sglang/srt/managers/schedule_batch.py:367-368` 原文："A decode step holds num_tokens more slots and commits them at"/"allocation, before the step runs."

释放时（`release_kv_cache`，`python/sglang/srt/mem_cache/common.py:385`）：
```
owned = req.owned_kv_len()                       # :409
tree_cache.checkpoint(req, up_to=owned)          # :419  把前 owned 个 token 插进前缀树
free_kv_row(...[cache_protected_len, owned))     # :421
_release_overallocated_kv_indices(req, owned, kv_allocated_len)   # :424, 实现 :435-465，释放 [owned, allocated)
```
`owned_kv_len()` 的定义（`python/sglang/srt/managers/schedule_batch.py:990-1002`）：已结束请求取 `min(kv_committed_len, len(prompt) + len(通过 stop 的输出))`。

用 A 套公式（prompt=20，EOS 是第 5 个输出，`finished_len=5`：`python/sglang/srt/managers/schedule_batch.py:1336`）：
- **不 overlap**：处理 D4 结果时 `kv_committed = 20+4 = 24`，`owned = min(24, 25) = 24`，EOS 那个 token 还没被喂进模型，没有 KV，**不进树**。
- **overlap，D5 已发射**：`kv_committed = 25`（D5 在发射前就分了槽并「提交」），`owned = min(25, 25) = 25`，`[owned, allocated)` 为空，**什么都不用额外释放**——EOS 这一行（D5 以 EOS 为输入算出的 KV）**反而被当成合法前缀一起 checkpoint 进了树**。

这条结论是我从代码算的【推断】，没有跑过。依据：`allocate_decode` 的提交语义（`:367-368`）、`owned_kv_len` 的公式、`checkpoint` 用 `req.full_untruncated_fill_ids[:up_to]` 做键（`python/sglang/srt/mem_cache/radix_cache.py:518`；释放前先 `refresh_fill_ids`，【注释】`python/sglang/srt/mem_cache/common.py:417` 原文："# The fill-id array lags output_ids until the next prepare_for_decode."），并且 `_release_overallocated_kv_indices` 对非投机请求断言「没有超分尾巴或 stop 在已提交之前」（`python/sglang/srt/mem_cache/common.py:444-452`）。

那 `python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1215-1216` 那条注释（"Left the batch after launch; its over-allocated tokens are"/"freed in `release_kv_cache`."）说的是什么？它标在**第 6 圈处理 D5 结果时跳过 A 那一行**的分支上（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1214-1217`）：A 已经在第 5 圈被释放过一次，D5 的结果行不再提交任何东西。至于「over-allocated」具体指哪些情形（比如投机解码一次接受多个 token 后在 stop 处截断），我没有逐一核实。

结论给读者：**EOS 的白算只白在「算力」，不白在「显存」**（普通 decode）；而「按长度结束」被 §5.1 提前挡掉，连算力都不白。

### 6.4 `copy_stream` 与 `copy_done`：处理结果时怎么保证 token 已到 CPU

发射时：`batch_result.copy_done = self.device_module.Event()`（`python/sglang/srt/managers/scheduler.py:5493`），然后 `_copy_result_to_cpu`（`:5506`）在 `copy_stream` 里调 `batch_result.copy_to_cpu`，后者把 `next_token_ids` 等做异步 D2H（`python/sglang/srt/managers/utils.py:237`，`_async_d2h` 调用在其中）并在最后 `self.copy_done.record()`（`:296`）。处理时：`result.copy_done.synchronize()`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1157`，prefill 版 `:403`）——**CPU 只在这里才真正等 GPU**，且此时下一批已经在 GPU 上跑，等的只是上一批的拷贝尾巴。

---

## 7. 处理结果：谁还活着、怎么判结束、发不发给 Detokenizer

入口 `process_batch_result`（`python/sglang/srt/managers/scheduler.py:5875`），按 `forward_mode` 分流：decode → `process_batch_result_decode`（`:5899-5900`）；extend → `process_batch_result_prefill`（`:5907` 附近）。处理完调 `_on_step_processed`（`:5164`，里面 `drop_inflight_step`：`:5171`）。

### 7.1 `still_in_batch`：丢掉「发射后才离开」的行

结果批是**发射那一刻的快照**，里面的请求在处理前可能已经：结束了、被 retract 了、被 abort 了。处理 decode 结果的第一件事：`still_in_batch_rows = self._rows_still_in_batch(batch)`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1158`；定义 `:349-355`）。判断就是「这个请求还在不在 `running_reqs / prefill_launched_reqs / chunked_reqs` 任一名单里」：【注释】`python/sglang/srt/managers/scheduler.py:5110` 起的文档串（`def still_in_batch` 在 `:5109`）。循环里不在的行直接 `continue`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1214-1217`），最后只对**还在的请求**发输出：`stream_output(reqs_still_in_batch, ...)`（`:1298`）。
- 【注释】`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:350-352` 原文："Each row of a launched step's frozen batch, checked against the requests"/"still in the batch. A row whose request left after launch (finished,"/"retracted, or aborted) commits and streams nothing, and only advances the"。
- 来历：【提交】`d1d317c64d`「[Refactor] scheduler: Commit a step's result only on the rows of requests still in the batch (#49019)」（2026-11）。提交说明只有标题。diff 里被替换掉的是散在各处的 `not req.finished() and not req.is_retracted` / `if req.is_retracted or req.finished()` 一类判断（例如旧的 `should_commit_output = (not req.finished() and not req.is_retracted and not is_middle_chunk)` 变成 `still_in_batch_rows[i] and not is_middle_chunk`）。动机从 diff 看是「把『这行还算不算数』收成一处判断」，这是我的解读【推断】；它顺手也修掉了 abort 的请求（既没结束也没 retract）的漏判，这点没有直接证据，不下结论。
- 白算那一步（§6.3）的结果行，就是在这里被丢掉的。

### 7.2 提交 token、判结束

对还在的行：`req.output_ids.extend(next_token_id)`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1243`）→ `req.update_finish_state(new_accept_len)`（`:1246`）→ `_handle_finish_state_updated_req`（`:1252`）。

`update_finish_state`（`python/sglang/srt/managers/schedule_batch.py:1487`）检查顺序：已排队的结束意图（`to_finish`）→ 词表越界 / NaN → stop 字符串 → stop token / EOS → `max_new_tokens`（`:1498-1523`）→ 语法终止。
- 为什么 stop 字符串先于 EOS：【注释】`:1503` 原文："Stop string beats EOS/stop-token matched in the same step (speculative"（后续行讲：只按 token 截会漏 stop 串）。
- 为什么 EOS 先于长度：【注释】`:1510` 原文："Stop token/EOS beats the length cap for the same reason"（投机解码一次接受多个 token 可能跨过 `max_new_tokens`，长度优先会把 EOS 之后多接受的 token 也留在输出里）。
- 例子：X5 是 EOS → `finished_reason=FINISH_MATCHED_TOKEN`，`finished_len=5`（`:1335-1337`）。

结束了就走 `_handle_finish_state_updated_req`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1569`）里 `if req.finished():` 分支，最终 `self.request_retirer.release(req, checkpoint=checkpoint)`（`:1653`）→ `RequestRetirer.release`（`python/sglang/srt/managers/scheduler_components/request_retirer.py:91`）→ 把请求从所属名单摘掉（`:93`）→ `release_kv_cache(...)`（`:111`）。这一步把请求的 KV 交给前缀树（见 §6.3、上一趟 §4.5）。prefill 路径同理，另外对**没结束**的请求，prefill 结果处理会 `checkpoint_kv_cache`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:547`）——也就是「prompt 的 KV 在第一个 token 回来时就插进前缀树」，不用等整个请求结束。

### 7.3 `free_group_begin/end`

整批处理被 `token_to_kv_pool_allocator.free_group_begin()`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1203`）和 `free_group_end()`（`:1299`）夹住：批内各请求释放的槽先攒着，最后一次性还给分配器。【推断】依据：配对的 begin/end 之间只有释放调用；没有读到分配器里的具体实现。

### 7.4 发不发给 Detokenizer：`accept`

整批只调一次 `output_streamer.stream_output(...)`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1298`，实现 `python/sglang/srt/managers/scheduler_components/output_streamer.py:233`），对每个请求先问 `accept`（`:580`）：
- 已结束 → 必发（`:583-589`）；
- 流式 → `stream_interval == 1`（默认 1：`python/sglang/srt/arg_groups/fields/serving.py:257-260`）每步都发；否则攒够 `stream_interval` 个，或「第一个 token 立刻发」（`python/sglang/srt/managers/scheduler_components/output_streamer.py:603-605`）；但若尾部可能是 stop 字符串的前缀，**先憋着不发**（`python/sglang/srt/managers/scheduler_components/output_streamer.py:610`）；
- 非流式 → 每 `SGLANG_FORCE_STREAM_INTERVAL`（默认 50，`python/sglang/srt/environ.py:744`）个 token 发一次（`python/sglang/srt/managers/scheduler_components/output_streamer.py:613`）；
- 发的内容是 `output_ids_through_stop[send_token_offset:]`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:627`），并推进 `send_token_offset`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:630`）；首次输出才带 `DetokenizeHeader`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:642`）。细节见上一趟 §4.7。

---

## 8. 显存不够：retract

### 8.1 触发条件与顺序

在 `update_running_batch` 里：`check_decode_mem()` 为假（`python/sglang/srt/managers/scheduler.py:5207`），且没有「马上结束」的请求可以等（§5.2），才走 retract（`:5214` 起，日志 "KV cache pool is full. Retract requests."：`:5257`）。

`check_decode_mem` 先算**下一步需要多少新槽**：非投机时，每个请求 `kv_committed_len % page_size == 0`（即当前页刚用满）才需要新开一页，需要 `页数 × page_size`（`python/sglang/srt/managers/schedule_batch.py:2977-2979`）。再问分配器：`check_decode_capacity` 会**先试着淘汰前缀树**再看够不够（`python/sglang/srt/mem_cache/allocator/base.py:263-278` 的 `evict_to_free_tokens` + `available_size() >= num_tokens`）。【注释】`:272-273` 原文："The retract loop converges on this same"/"check, so a shortfall here retracts instead of failing in alloc."——retract 循环和分配用的是同一个判据，所以不会出现「检查通过、真分配时炸掉」。

### 8.2 `retract_decode`：退谁、怎么退

`python/sglang/srt/managers/schedule_batch.py:3019`。
1. 排序：`_get_decode_retraction_order`（`python/sglang/srt/managers/schedule_batch.py:3023`、`python/sglang/srt/managers/schedule_batch.py:3116`）。默认 `length` 策略 key = `(len(output_ids), -len(origin_input_ids))`（`python/sglang/srt/managers/schedule_batch.py:3127`），降序排、**从末尾弹**，所以先退「已生成最少、输入最长」的：先退的是「沉没成本最小」的。【文档】`python/sglang/srt/arg_groups/fields/schedule.py:150` 原文含："retracts short-output,"（接 "long-input requests first."）。
2. 循环：`while first_iter or not self.check_decode_mem(selected_indices=...)`：弹一个、释放它的内存，直到剩下的够放；**至少保留 1 个**（【注释】`python/sglang/srt/managers/schedule_batch.py:3033` 原文："# Always keep at least one request"）。
3. 释放：`release_req` → `request_retirer.retract`（`python/sglang/srt/managers/schedule_batch.py:1844`）→ `release_kv_cache(req, ..., checkpoint=False)`（`python/sglang/srt/managers/scheduler_components/request_retirer.py:134`）。【注释】`python/sglang/srt/managers/schedule_batch.py:3057` 原文："# release memory and don't insert into the tree because we need the space instantly"——所以 retract **不 checkpoint 进树**，否则槽位还被树占着、省不出空间。紧接着 `evict_from_tree_cache(tree_cache, 剩余请求数 × SGLANG_RETRACT_DECODE_STEPS)`（`:1846-1847`，默认 20：`python/sglang/srt/environ.py:711`）。【注释】`python/sglang/srt/managers/schedule_batch.py:1845` 原文："# NOTE(lsyin): we should use the newly evictable memory instantly."
4. `req.reset_for_retract()`（`python/sglang/srt/managers/schedule_batch.py:1849`、`python/sglang/srt/managers/schedule_batch.py:1530`）：把 `prefix_len=0`、`last_node=None`、`lock=None` 清掉，`retraction_count += 1`，并置 `is_retracted / retracted_stain`（`python/sglang/srt/managers/schedule_batch.py:1545`）。**`output_ids` 不清**（除非用的是 `input_embeds`，`python/sglang/srt/managers/schedule_batch.py:1566-1567`）——所以重新 prefill 时输入是「原 prompt + 已生成的 token」，客户端已收到的部分（`send_token_offset`）不重发。
5. 回到 `waiting_queue`：`self._add_request_to_queue(req, is_retracted=True)`（`python/sglang/srt/managers/scheduler.py:5271`）。下次选批时它像新请求一样前缀匹配、重算；自己刚被释放的那份 KV **不在树里**（`checkpoint=False`），能命中的只是树里别人留下的部分。
6. 重估 `new_token_ratio`：`estimate_new_token_ratio_after_retract`（`python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:41-49`）= `(已生成总数 + RETRACT_DECODE_STEPS × 剩余请求数) / (Σ max_new_tokens + 1)`，上限 1；写回 `python/sglang/srt/managers/scheduler.py:5251`。意思是：把「预留比例」直接抬到更贴近现状，免得下一圈马上又 retract。【推断】（提交 `2d868656d0`「Move the retract-decode ratio estimation onto the new-token-ratio tracker (#25717)」只是把这段搬家，标题之外没有说明。）

连最后一个也放不下 → 直接把它 abort（`retract_decode` 末尾的 "Out of memory even after retracting all other requests" 分支，`python/sglang/srt/managers/schedule_batch.py:3085-3104` 一带），而不是让进程崩。beam 请求组不能退，只能 abort。

### 8.3 为什么叫「退」而不是「换出」

默认路径（非 PD 解码端）**只释放、不备份**：`resumable = req.resumable_by_reprefill`（`python/sglang/srt/managers/scheduler_components/request_retirer.py:121`）——恢复靠重新 prefill。PD 解码端才会把 KV 备份到主机（`:122-130`）。【文档】`:114` 文档串原文："Release a retracted request's memory uncheckpointed; return whether it can resume."

### 8.4 有测试吗

【测试】`test/registered/scheduler/test_retract_decode.py:32` 用 `SGLANG_TEST_RETRACT=True` 让 `update_running_batch` 按固定间隔**人为触发** retract（`python/sglang/srt/managers/scheduler.py:5215-5217` 的 `TEST_RETRACT and self.forward_ct % TEST_RETRACT_INTERVAL == 0`），在 MMLU 上跑 256 题，断言分数 `>= 0.64`（`test/registered/scheduler/test_retract_decode.py:56`），说明 retract 后重算的结果依然正确。

---

## 9. 把一切串起来：A 和 B 的逐圈推演

设：B 比 A 早到，`max_new_tokens=8`，第 n 圈开始时 B 已有 6 个输出，它的第 7 步（D_B）已在上一圈发射、在 `result_queue` 里；`req_pool_idx`：B=3、A=7（设）。EOS 在 A 的第 5 个输出。`P` 是 A 的 prefill 批，`Dk` 是第 k 次 decode 批。「处理」指本圈**发射之后**处理的那一批。

| 圈 | 选批 / 发射 | 本圈处理的结果 | 状态要点 |
|---|---|---|---|
| n−1 | 发射 D_B=[B] | —— | B.inflight=1；future_map[3] 在 D_B 发射后被写入 B 的第 7 个 token |
| n | `waiting_queue=[A]`；先 prefill：`P=[A]`（B 被挡一拍，**不 decode**）；`init_next_round_input` 命中 5；adder 收下 A（§4.5）；`prepare_for_extend` 分 15 槽；发射 P | D_B 的结果：B.output=7，B.inflight→0 | A.inflight=1；`result_queue=[P]`；future_map[7] 写入 X1（GPU 上）；`last_batch=P` |
| n+1 | `last_batch` 是 extend → A 并进 `running_batch=[B,A]`（`move` 进 `running_reqs`）；每圈过滤无变化；无新 prefill → `update_running_batch`：B 的 inflight=0 → 不剔；`check_decode_mem` 够；`prepare_for_decode`（A 的 seq_len 20→21、分 1 槽）；`resolve_forward_inputs` 从 future_map[3]、[7] 取输入；发射 D1=[B,A] | P 的结果：A.output=[X1]；`stream_output`：首 token 立刻发，带 header | A.inflight=1(D1)；B.inflight=1(D1)；future_map[3]=B 第 8 个 token、[7]=X2 |
| n+2 | `update_running_batch`：B 的 `inflight>0` 且 `7+1>=8` → **剔除 B**（`running_batch=[A]`，但 B 仍在 `running_reqs`）；发射 D2=[A] | D1 的结果：B 第 8 个 token → 长度结束 → `release`（B 的 KV 进树）；A 记 X2，`stream_output` | B 离开 `running_reqs`；**这是「按长度结束、不白算」的例子** |
| n+3、n+4 | 发射 D3、D4=[A] | 处理 D2、D3：A 记 X3、X4 | A.inflight 始终 1 |
| n+5 | `running_batch=[A]`，A 未知已结束 → **发射 D5=[A]**（白算）：`prepare_for_decode` 分第 5 个槽，`kv_committed=25` | D4 的结果：X5=EOS → `finished_len=5` → `release`：`owned=25`，整条 25 个 token 插进前缀树；A 从 `running_reqs` 摘掉；`stream_output`（结束行必发） | `still_in_batch(A)` 此后为假 |
| n+6 | `filter_batch()` 把已 `finished()` 的 A 滤掉，`running_batch` 空、无新 prefill → `batch=None` | D5 的结果（因 `last_batch` 非空）：A 那一行 `still_in_batch=False` → 直接跳过，不提交、不发送 | D5 是 EOS 白算的那一步；其 KV 已随 `owned=25` 进了树（§6.3，【推断】） |
| n+7 | `batch=None` 且 `last_batch=None` → `on_idle`（含 `new_token_ratio_tracker.reset()`：`python/sglang/srt/managers/scheduler.py:6120`） | —— | 空闲 |

读表的三个对照：**B（长度结束，提前剔，不白算）vs A（EOS，白算一步）**；**n 圈 B 被 prefill 挡了一拍**；**第 n+1 圈 A 刚并进批，它的 P 结果还没处理**（所以才需要 §3.2 的每圈过滤，和 §5.1 的 `inflight_steps` 记账）。

---

## 10. 值得做成「源码页」的代码段（建议 6 段，按推荐顺序）

> 行号区间是我建议的起止；批注行给出「为什么」与来源。

**页 1：overlap 主循环** `python/sglang/srt/managers/scheduler.py:2235-2305`（71 行）
- `:2237` `enable_skip_finishing_decode = True`：只有 overlap 循环才开「提前剔除按长度结束的请求」（§5.1）。
- `:2238-2245` `result_queue` + `pop_and_process`：「上一批」其实是队列的头。
- `:2258-2262` 先处理再选批的例外（§2.2 ⑤）。
- `:2281-2284` 发射 → 栅栏 → `batch.copy()` 入队（为什么是 copy：§2.2 ②）。
- `:2290-2295` 注意是 `if self.last_batch` 而非 `if batch`（§2.2 ①）。
- `:2299-2300` 采样推迟的位置与注释（§2.2 ④）。
- 对照：`:2200-2232`（normal 版，33 行），读者可以左右对照。

**页 2：选批** `python/sglang/srt/managers/scheduler.py:4378-4456`（79 行，跳过 `:4430-4441` 的 DP+投机旁枝可缩到约 65 行）
- `:4378-4382` 条件：上一批是 extend。
- `:4393`、`:4403-4411` 过滤 → 搬名单 → 合并。
- `:4413-4418` **每圈过滤**（读者最易看漏的一段，注释就在上面，§3.2）。
- `:4426` 建 prefill 批；`:4444-4456` prefill 优先，否则 decode。

**页 3：decode 一步 + retract 入口** `python/sglang/srt/managers/scheduler.py:5182-5284`（建议只取 `:5182-5232` 与 `:5251-5282`）
- `:5182-5192` `_reqs_finishing_inflight` 与文档串。
- `:5198-5199` 先剔后查显存（顺序依赖，§5）。
- `:5207-5212` 为什么「剔了又不够」不 retract（§5.2）。
- `:5229` 调 `retract_decode`；`:5251` 写回 ratio；`:5271` 退回 `waiting_queue`；`:5273` 否则衰减。

**页 4：发射（overlap 分支）+ FutureMap 读写** `python/sglang/srt/managers/scheduler.py:5432-5511`（80 行）配 `python/sglang/srt/managers/overlap_utils.py:127-170`（resolve）与 `:784-800`（stash）
- `python/sglang/srt/managers/scheduler.py:5433` forward 流等 schedule 流；`:5442-5444` 先解析输入再前向；`:5493-5506` `copy_done` + 异步拷回；`:5510-5511` `input_ids=None`。
- `python/sglang/srt/managers/overlap_utils.py:142`（prefill 从 CPU 暂存）与 `:165-166`（decode 从 future_map gather）并排看，这是「下一步输入从哪来」的全部答案。

**页 5：处理 decode 结果** `python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1151-1160` + `:1203-1260` + `:1298-1299`
- `:1157` `copy_done.synchronize()` 是 CPU 唯一真正等 GPU 的地方。
- `:1158` `still_in_batch_rows`；`:1214-1217` 跳过「发射后才离开」的行（§7.1）。
- `:1243-1246` 提交 token → 判结束；`:1252` 结束处理；`:1298` 整批一次 `stream_output`。

**页 6：KV 释放与 retract 的对照** `python/sglang/srt/mem_cache/common.py:385-465`（释放；建议取 `:405-432` + `:435-465`）与 `python/sglang/srt/managers/schedule_batch.py:3019-3072` + `:1835-1850`
- `python/sglang/srt/mem_cache/common.py:409`、`:419`、`:421`、`:424`：`owned → checkpoint → 释放 → 释放超分`的顺序（为什么 checkpoint 要在 free 之前：树要先拿走它能 key 的那部分，剩下的才归还）。
- retract 反过来：`checkpoint=False`（`python/sglang/srt/managers/scheduler_components/request_retirer.py:134`），要的是「立刻腾空间」。两者并排看就能看懂 `checkpoint` 参数的意义。

（若还要第 7 页：`python/sglang/srt/mem_cache/prefill_budget.py:156-161`、`:185-215`、`:283-310` 配 `python/sglang/srt/managers/schedule_policy.py:1174-1184`——预算公式，约 60 行。）

---

## 11. 没查到为什么

- **prefill 为什么优先于 decode**：`Run prefill first if possible` 注释最早随【提交】`c211e7b669` 出现，提交只有标题。§3.3 的 TTFT 解释是【推断】。
- **`batch_is_full` 标志为什么这样设计**（满了就不再扫等待队列、有请求离开才清）：相关提交 `04b262cd91`（标题 "Fix major performance bug in certain cases"）、`08104b56de`（"Sanity check to prevent performance regression"）都没有正文，没查到动机。
- **`new_token_ratio` 的数值（0.7 / 0.14 / 600 步 / 20 步）为什么取这些**：只有默认值（`python/sglang/srt/environ.py:708-711`），没查到依据。
- **`_apply_war_barrier` 具体保护哪块共享缓冲**：只读到入口与注释，没追下去。
- **`run_batch` 里 `future_map.publish(..., batch.seq_lens + 1)` 在主路被谁消费**：没追到。
- **`over-allocated tokens are freed in release_kv_cache` 注释说的具体情形**（§6.3）：普通 decode 下我算出没有超分，注释大概指投机解码等情形，未逐一验证。
- **「提交说明只有标题」的几个关键提交**（`512d164916`、`d1d317c64d`、`e14ef4b658`、`3b5ae4ea86`、`1c0dc0293d`）：没有 PR 正文可读（不访问外网），本稿对它们的「为什么」只引标题和 diff 能直接看到的内容。

## 12. 可改进处【观点】

- 【观点】`get_next_batch_to_run` 与 `_get_new_batch_prefill_raw` 里穿插了大量 hisparse / dllm / mamba / lora / beam / PD 的分支（`python/sglang/srt/managers/scheduler.py:4339-4476`、`:4586-5030`），主路读者很难一眼看出骨架；把「主路骨架」和「各特性钩子」分层会好读很多。
- 【观点】`ratio` 一经 retract 就被「重估」，但衰减是线性的、与 batch 大小无关。高并发长尾请求下可能反复「重估→衰减→再 retract」。没有数据，只是推测。
- 【观点】EOS 白算的一步，普通 decode 里它的 KV 反而被留进树（§6.3）。如果树缓存的是「EOS 之后」的 KV，这条前缀只对「后面也是同样 EOS 结尾」的请求有用；是否应截掉，需要作者确认意图。

---

## 附：给讲解员的读者提示

- 读者最容易困惑的第一处：**「第 N 圈发射的批，结果在第 N+1 圈（发射之后）才处理」**。建议把「发射 / 处理」画成两条错开一格的线，并直接用 §9 的表走一遍。
- 第二处：**FutureMap 并不神秘**——一条按 `req_pool_idx` 索引的 GPU 向量；写在上一步发射后，读在下一步发射前。建议和「CPU 在 `prepare_for_decode` 里自己 `seq_lens+1`」并排放，强调「CPU 只缺 token 值」。
- 对照着看：**B（长度结束，提前剔）vs A（EOS，白算）**；**retract（`checkpoint=False`）vs 正常结束（`checkpoint=True`）**。
- 不要把「non-overlap = 没有 FutureMap」画出来：FutureMap 是 always-on（§6.1）。
- 上一趟 §4.1 关于「多分配的 KV 槽在释放时一并回收」的说法，请按本稿 §6.3 修正：普通 decode 下是随前缀树 checkpoint 保留，不是被回收；若上一屏图/卡片里写了「回收」，建议改为「白算一步的 KV 槽随请求一起交给前缀树/释放」之类的中性说法。
