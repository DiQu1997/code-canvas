# 调度侧连接器（顶层图上的 `sched` 框）：下一层底稿

> 研究对象：LMCache 仓库 HEAD = `b27f6bd9e2`。本稿是对上一趟底稿（`manuscript.md`，下称「上一趟」）§5.1 的补充，只讲「调度侧连接器」这一个框的内部。
> 锚点格式 `文件:行号`；引文用 “ ” 括起、逐字照抄，后面紧跟出处。「为什么」都标【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。「设」= 为讲解补设的值。
> 路径简写：下文凡 `lmcache/integration/vllm/…` 都写全，不省略。

---

## 0. 先看这个

调度侧连接器干三件事，都由 vLLM 调度器**每步回调**推动，没有自己的线程循环：

1. **问**：这个请求能从 LMCache 取多少 token？——发一次「不等回执」的 LOOKUP，之后每被问一次就推进一点，直到拿到命中数。
2. **记**：每个请求一个 `tracker`，记着命中数、已分配的 block、已下单存了多少 token、当前状态。
3. **下单**：每步把「取这几块 / 存这几块」写成工单，随 vLLM 的调度结果送给 worker。

最容易迷路的三点：

- **跨多个调度步**：一个请求的 LOOKUP、分配 block、RETRIEVE、再计算、STORE 分散在好几步里，靠 tracker 上的几个字段接力。
- **「下单」≠「存完」**：`num_stored_tokens` 在**下单那一步**就加了，不等 STORE 完成。
- **LOOKUP 会上读锁**：命中的块被锁住等 RETRIEVE；不取的那部分要显式放（`free_lookup_locks`）。

**上一趟之后代码有新机制，上一趟没提**（读者对照上一趟时要知道）：LOOKUP 现在可以带 `start`，跳过 vLLM 已覆盖的前缀（提交 `60538a08`）；`computed_tokens` 多了一个下界 `num_engine_computed_tokens`（提交 `4f7d283a`）；`request_finished` 不再对所有生产者一律延迟回收，只对「真下过 STORE 单」的请求延迟（提交 `752e01fd`），并多了「scheduler → worker」的 `delay_free_req_ids` 通道。上一趟 B3 的「`free_end = num_vllm_hit_tokens = 0` 不需要释放」仍成立。

---

## 1. 框里有什么

### 1.1 五个部件

| # | 部件 | 是什么 / 住在哪 | 锚点 |
|---|---|---|---|
| ① | **回调入口** `LMCacheMPConnector`（SCHEDULER 角色） | 实现 vLLM 的 `get_num_new_matched_tokens` / `update_state_after_alloc` / `build_connector_meta` / `request_finished` / `update_connector_output` | `lmcache/integration/vllm/lmcache_mp_connector.py:1377`、`lmcache/integration/vllm/lmcache_mp_connector.py:1600`、`lmcache/integration/vllm/lmcache_mp_connector.py:1694`、`lmcache/integration/vllm/lmcache_mp_connector.py:1730`、`lmcache/integration/vllm/lmcache_mp_connector.py:1754` |
| ② | **每请求 tracker** `LMCacheMPRequestTracker` + 状态 `LMCacheMPRequestState` | 字典 `request_trackers[request_id]`；一个请求一个 | `lmcache/integration/vllm/lmcache_mp_metadata.py:53-66`、`lmcache/integration/vllm/lmcache_mp_metadata.py:69`、`lmcache/integration/vllm/lmcache_mp_connector.py:846` |
| ③ | **工单工厂** `LMCacheMPRequestMetadata.GetStoreMetadata / GetRetrieveMetadata`（静态方法，只读写 tracker） | 「存多少 / 取多少」的算术全在这 | `lmcache/integration/vllm/lmcache_mp_metadata.py:348`、`lmcache/integration/vllm/lmcache_mp_metadata.py:589` |
| ④ | **查找适配器** `LMCacheMPSchedulerAdapter` + 每请求一个 `LookupState` | 适配器持 `_lookups: dict[request_id, LookupState]`；`LookupState` 管 LOOKUP→QUERY 的异步链 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:650`、`lmcache/integration/vllm/mp_lookup_state.py:69` |
| ⑤ | **随步元数据** `LMCacheMPConnectorMetadata` | 一组工单 + `need_flush_before_forward` + `delay_free_req_ids`；随 `SchedulerOutput` 跨进程送给 worker | `lmcache/integration/vllm/lmcache_mp_metadata.py:666-672` |

另有一个小集合 `_delay_free_req_ids`（调度侧「延迟回收」名单）：`lmcache/integration/vllm/lmcache_mp_connector.py:849`。

### 1.2 谁把什么交给谁（框内连线）

| 线 | 从 → 到 | 方式 | 交的是什么 | 锚点 |
|---|---|---|---|---|
| a | vLLM → ① | 回调（调度线程） | 请求、`num_computed_tokens`、新分配的 blocks | `lmcache/integration/vllm/lmcache_mp_connector.py:1377` |
| b | ① → ④ | 函数调用（同线程） | `maybe_submit_lookup_request`（发）、`check_lookup_result`（取）、`cleanup_lookup_result`、`free_lookup_locks`、`end_session` | `lmcache/integration/vllm/lmcache_mp_connector.py:1463`、`lmcache/integration/vllm/lmcache_mp_connector.py:1478`、`lmcache/integration/vllm/lmcache_mp_connector.py:1678` |
| c | ④ ⇄ LMCache 服务 | ZMQ 异步 RPC；**回复由传输层接收线程触发回调** | LOOKUP、QUERY_PREFETCH_STATUS、FREE_LOOKUP_LOCKS、END_SESSION | `lmcache/integration/vllm/mp_lookup_state.py:136-148`；回调线程见 `lmcache/v1/transport/futures.py:76` |
| d | ① ⇄ ② | 读写字段 | 命中数、block 编号、state | §1.3 |
| e | ① → ③ → ⑤ | 同线程 | tracker → `LoadStoreOp` → 加入 metadata | `lmcache/integration/vllm/lmcache_mp_connector.py:1711-1713` |
| f | ⑤ → worker | 跨进程（随 vLLM 调度结果） | 工单、`delay_free_req_ids` | 上一趟 L4；`lmcache/integration/vllm/lmcache_mp_connector.py:1708-1709` |
| g | worker → ① | 经 vLLM 的返回值 | `finished_sending` 只用来放 boundary-state block；非 lazy 模式下调度侧**不**据此更新 tracker | `lmcache/integration/vllm/lmcache_mp_connector.py:1739-1745` |

**线程**：①②③ 全在 vLLM 调度线程上。④ 的 `LookupState` 有两种线程会碰它：调度线程（`check_lookup_result`）和传输层接收线程（RPC 回复的回调）。所以它自带一把锁：【注释】“RPC operations are thread-safe because they may happen in different”（`lmcache/integration/vllm/mp_lookup_state.py:96`，句子在下一行收尾）；回调线程那句：【注释】“which for messaging clients is the transport's receive thread, so”（`lmcache/v1/transport/futures.py:76`）。

### 1.3 tracker 里的字段：谁在什么时候写（读者要盯的就是这张表）

| 字段 | 含义 | 写入点（锚点） |
|---|---|---|
| `state` | `PREFETCHING / WAITING_FOR_LOAD / READY / BYPASS_LMCACHE` | 初始 PREFETCHING `lmcache/integration/vllm/lmcache_mp_metadata.py:165`；alloc 后 `lmcache/integration/vllm/lmcache_mp_connector.py:1652-1656`；下发 RETRIEVE 后置 READY `lmcache/integration/vllm/lmcache_mp_connector.py:1987`；失败回退 `lmcache/integration/vllm/lmcache_mp_connector.py:1442`、恢复 `lmcache/integration/vllm/lmcache_mp_connector.py:1645` |
| `num_vllm_hit_tokens` | vLLM 自己前缀缓存命中数，**向下取整到各 group 的 block 边界公倍数** | `lmcache/integration/vllm/lmcache_mp_connector.py:1491-1495`（拿到 LOOKUP 结果的那次 poll 写） |
| `num_lmcache_hit_tokens` | LOOKUP 返回的命中数 | `lmcache/integration/vllm/lmcache_mp_connector.py:1540` |
| `num_stored_tokens` | 「已在 LMCache 里或已下单要存」的 token 数，永远是整块的倍数 | 命中时 = 命中数向下取整到整块 `lmcache/integration/vllm/lmcache_mp_connector.py:1539`；每下一张 STORE 单就加 `lmcache/integration/vllm/lmcache_mp_metadata.py:456` |
| `allocated_block_ids` | vLLM 分给请求的 block（按 KV group） | `lmcache/integration/vllm/lmcache_mp_connector.py:1636`（alloc 回调）、`lmcache/integration/vllm/lmcache_mp_connector.py:2042`（后续步新增） |
| `num_scheduled_tokens` | 自 tracker 建立起累计调度过的 token | `lmcache/integration/vllm/lmcache_mp_connector.py:2000`、`lmcache/integration/vllm/lmcache_mp_connector.py:2049` |
| `num_engine_computed_tokens` | vLLM 本步结束时的真实已算位置 | `lmcache/integration/vllm/lmcache_mp_connector.py:2001-2003`、`lmcache/integration/vllm/lmcache_mp_connector.py:2050-2052` |
| `store_scheduled` | 是否发过 STORE 单 | `lmcache/integration/vllm/lmcache_mp_connector.py:2020`、`lmcache/integration/vllm/lmcache_mp_connector.py:2070` |
| `apc_covered_tokens` / `need_full_lookup` / `pending_free_ranges` | APC 覆盖起点（冻结）/ 缩水后粘性回退 / 被放弃的 LOOKUP 留下的锁区间 | `lmcache/integration/vllm/lmcache_mp_connector.py:1476`；`lmcache/integration/vllm/lmcache_mp_connector.py:1514`；`lmcache/integration/vllm/lmcache_mp_connector.py:1512` |
| `packed_tokens` | 增量打包的 token 字节串 | `lmcache/integration/vllm/lmcache_mp_metadata.py:289-309` |

---

## 2. 一个请求的一生

### 2.1 状态机（四个状态）

源码里的注释版：`lmcache/integration/vllm/lmcache_mp_metadata.py:53-66`。实际转移：

```
PREFETCHING ──update_state_after_alloc──▶ WAITING_FOR_LOAD   （要取：命中 > vLLM 自己的）
PREFETCHING ──update_state_after_alloc──▶ READY              （不用取）
WAITING_FOR_LOAD ──build_connector_meta 下发 RETRIEVE──▶ READY
READY ──下一次被问到时 num_computed_tokens==0 且曾有命中──▶ BYPASS_LMCACHE
BYPASS_LMCACHE ──update_state_after_alloc──▶ READY
```

- 「不用取」/「要取」的判断在 `lmcache/integration/vllm/lmcache_mp_connector.py:1648-1656`，条件 `num_external_tokens > 0 and tracker.needs_retrieve()`；`needs_retrieve` 本身是 `lmcache > vllm 命中` 且 state 不在 READY / BYPASS：`lmcache/integration/vllm/lmcache_mp_metadata.py:179-189`。
- 「READY → BYPASS」实际写在 `get_num_new_matched_tokens` 开头，不是回调之外的什么事件：`lmcache/integration/vllm/lmcache_mp_connector.py:1426-1443`。
- 名不副实：枚举 docstring 里的 `process_loading_requests`（`lmcache/integration/vllm/lmcache_mp_metadata.py:57`）现在叫 `_process_retrieve_requests`（`lmcache/integration/vllm/lmcache_mp_connector.py:1971`）。

### 2.2 例子：请求 A（只存）逐步走

沿用上一趟：A = 1000 token，block 16，chunk 256。**步号 s1、s2… 是设的**（只表示先后，不是真实步数）。

| 步 | vLLM 回调 | tracker 变化 | 发出 / 返回 |
|---|---|---|---|
| s1 | `get_num_new_matched_tokens(A, 0)` 第 1 次 | 新建，PREFETCHING；`lookup_start=0` | LOOKUP(`start=0,end=768`) 不等回执；返回 `(None, True)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1478-1480` |
| s2 | 同上，第 2 次 | `num_vllm_hit_tokens=0`；结果 0 → 早退 `(0, False)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1532-1533` | 无 |
| s2 | `update_state_after_alloc(A, 63 个 block, 0)` | block 追加；PREFETCHING→READY | `cleanup_lookup_result`；`free_end=0` 不发 FREE |
| s2 | `build_connector_meta` | `scheduled=1000`、`engine_computed=1000`；`num_stored 0→768`；`store_scheduled=True` | STORE[0,768)，48 个 block |
| s3… | 每步 `build_connector_meta` | `scheduled` 逐步 +1 | 无新单：`1000−768=232<256` |
| sN | `request_finished(A)` | tracker 被删 | END_SESSION；返回 `delay_free=True`；`_delay_free_req_ids={A}` |
| sN+1 | `build_connector_meta` | — | `metadata.delay_free_req_ids={A}`，worker 之后报 `finished_sending={A}`，vLLM 才回收 63 个 block |

### 2.3 例子：请求 B（取 + 再存）逐步走

B = 1200 token，前 768 命中，vLLM 本地命中 0（上一趟）。

| 步 | vLLM 回调 | tracker 变化 | 发出 / 返回 |
|---|---|---|---|
| s1′ | `get_num_new_matched_tokens(B, 0)` 第 1 次 | 新建，PREFETCHING | LOOKUP(`0,1024`)；`(None, True)` |
| s2′ | 第 2 次 | `vllm_hit=0`、`lmcache_hit=768`、`num_stored=768`：`lmcache/integration/vllm/lmcache_mp_connector.py:1539-1540` | 返回 `(768, True)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1575` |
| s2′ | `update_state_after_alloc(B, 48 个 block, 768)` | PREFETCHING→**WAITING_FOR_LOAD** | `cleanup_lookup_result`；`free_end=0` |
| s2′ | `build_connector_meta` | `_process_retrieve_requests` 遍历**所有** tracker（B 此步没被调度计算，不在 `scheduled_new_reqs` 里）→ 置 READY | RETRIEVE[0,768)，`skip=0` |
| s3′ | worker `start_load_kv` → `get_finished` | — | 拷完后报 `finished_recving` |
| s4′ | `update_state_after_alloc` 第 2 次（全部 75 个 block） | 只追加新的 27 个；state 仍 READY：`lmcache/integration/vllm/lmcache_mp_connector.py:1626-1638` | — |
| s4′ | `build_connector_meta` | `scheduled=432`、`engine_computed=768+432=1200`；`num_stored 768→1024` | STORE[768,1024)，16 个 block |

两点读者最容易漏：

- RETRIEVE 单不是跟着「B 被调度计算」发的，而是跟着「B 的 tracker 在 WAITING_FOR_LOAD」发的：`lmcache/integration/vllm/lmcache_mp_connector.py:1977-1978`。【推断】依据：循环遍历 `request_trackers.values()` 而不是 `scheduler_output`；vLLM 在异步加载期间不调度该请求，这是 vLLM 侧行为，本仓库只有 `update_state_after_alloc` docstring（`lmcache/integration/vllm/lmcache_mp_connector.py:1606-1610`）描述两次回调。
- 「s4′ 才存 B 的新块」不是等 STORE 完成，而是等 B 自己真的算过那些 token。见 Q4。

---

## 3. 逐个回答讲解员列的问题

### Q1 tracker 几步里经历哪些状态？每步是谁推动？
见 §2.1 + §2.2/2.3。一句话：**PREFETCHING 由 `get_num_new_matched_tokens` 的重复调用「撑着」，`update_state_after_alloc` 定岔路，`build_connector_meta` 把 WAITING_FOR_LOAD 推成 READY，`get_num_new_matched_tokens` 开头那段把失败的 READY 推成 BYPASS。**

### Q2 LOOKUP 发出去不等，LookupState 怎么一步步拿到命中数？要隔几步？

**发**：`maybe_submit_lookup_request`。三个守卫：服务端不健康 → 不发、返回 False（`lmcache/integration/vllm/vllm_multi_process_adapter.py:850-851`）；该请求已有 LOOKUP → 不重发（`lmcache/integration/vllm/vllm_multi_process_adapter.py:853-855`）；否则建 `LookupState` 并 `submit()`，向**每台**服务端发 LOOKUP（`lmcache/integration/vllm/vllm_multi_process_adapter.py:876-885`，`lmcache/integration/vllm/mp_lookup_state.py:136-148`）。返回值是「这次是否真发了」，tracker 用它决定要不要冻结 `apc_covered_tokens`（`lmcache/integration/vllm/lmcache_mp_connector.py:1473-1476`）。

**LookupState 里每台服务端一条链**（`_Phase`：`lmcache/integration/vllm/mp_lookup_state.py:25-41`）：

```
LOOKUP_IN_FLIGHT ─ack到(回调线程)─▶ 立刻发 QUERY_PREFETCH_STATUS ─▶ QUERY_IN_FLIGHT
QUERY_IN_FLIGHT ─回复是 int─▶ RESOLVED（所有服务端都 RESOLVED → 取最小值，写 _result，之后不再变）
QUERY_IN_FLIGHT ─回复 None/失败─▶ 若期间已有一次调度检查：立刻重发；否则 NEEDS_REQUERY，等下一次检查重发
任一 RPC 超过 deadline ─▶ TIMED_OUT，_result=0
```

锚点：ack→QUERY `lmcache/integration/vllm/mp_lookup_state.py:313-325`；int→RESOLVED `lmcache/integration/vllm/mp_lookup_state.py:339-346`；None→重发/等待 `lmcache/integration/vllm/mp_lookup_state.py:348-353`；检查时重发 `lmcache/integration/vllm/mp_lookup_state.py:250-252`；超时 `lmcache/integration/vllm/mp_lookup_state.py:274-279`；结果粘住 `lmcache/integration/vllm/mp_lookup_state.py:130-131`。

**要隔几步**：
- 每次调度器问到这个请求，`check_lookup_result` → `on_scheduler_check` 只做「处理已到的回复 + 重发延后的 QUERY + 过期检查」，**默认不等**（`lmcache/integration/vllm/mp_lookup_state.py:228-282`；默认 `nonblocking_lookup_status = True`：`lmcache/integration/vllm/vllm_multi_process_adapter.py:129`）。
- 所以下限是 **下一步**：s1 发 LOOKUP、回复线程在两步之间完成 ack→QUERY→结果，s2 一问就拿到。这是「设：RPC 往返比一个调度步短」的乐观情形（推断自上述代码；仓库里没有实测步数）。
- 若服务端回复 None（例如 L2 预取还在跑），一次调度检查最多放行一次新的 QUERY：【注释】“So each server gets **at most one** new”（`lmcache/integration/vllm/mp_lookup_state.py:89`，句子在下一行收尾）。即「重试频率 = 调度步频率」。
- 调度步越长，观察越晚：【文档】“callback, for example when long prefill steps delay observation of an”（`docs/source/mp/configuration.rst:1103`，后接「already-ready reply」）。

**为什么这么设计**：
- 不等 ack：【注释】“Do not wait for the acknowledgement here: that is a full server”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:874`），下一行接 round trip on the scheduler thread.。提交 `ba8acd84` 标题 “perf(mp): defer LOOKUP acknowledgement off the scheduler thread (#4935)”，`588c5366` 标题 “[Perf][MP] Poll lookup status without blocking the scheduler (#5053)”，两条都没有正文。
- QUERY 只在**该服务端**的 LOOKUP 已 ack 后发、且每台至多一个在途：【注释】“is sent to a server **only after** that”（`lmcache/integration/vllm/mp_lookup_state.py:85`）。【推断】否则 QUERY 可能先于 LOOKUP 被服务端看到，查不到 job（服务端对未知 request 返回 0：`lmcache/v1/multiprocess/modules/lookup.py:442-447`）。
- deadline 从**发出**时算：【注释】“An RPC's deadline counts from when it is sent, so time spent waiting”（`lmcache/integration/vllm/mp_lookup_state.py:91`），避免「等调度检查」的时间被算到服务端头上。超时值就是 `mq_timeout`，默认 **300 秒**（`lmcache/integration/vllm/vllm_multi_process_adapter.py:124`、传入点 `lmcache/integration/vllm/vllm_multi_process_adapter.py:881`）；超时后该服务端被标不健康（`lmcache/integration/vllm/vllm_multi_process_adapter.py:1267-1274`），之后新请求的 LOOKUP 不发、`check_lookup_result` 直接返回 0（`lmcache/integration/vllm/vllm_multi_process_adapter.py:914-918`）——**降级成「当没命中」**。
- 结果为什么留在 `_lookups` 里直到 `cleanup_lookup_result`：【注释】“Submitted lookups until cleanup_lookup_result. Each keeps its result”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:647`）。这样 vLLM 因为没空闲 block 而重复问同一请求时，拿到的是同一个值，而不是再发一次。**副作用**【推断】：此时 LOOKUP 已上的读锁一直挂着，直到分配成功（或 TTL，默认 300 秒：`lmcache/v1/distributed/config.py:432`）。

### Q3 「查多少」：LOOKUP 的起点和终点
- **终点**：`lookup_tokens // chunk * chunk`（整块），除非开了未满块：`lmcache/integration/vllm/vllm_multi_process_adapter.py:857-863`。A：1000→768；B：1200→1024。
- **起点 `lookup_start`**：`num_computed_tokens` 先向下取整到各 group block 边界的公倍数（`_hit_alignment_tokens = math.lcm(*cached_spans)`：`lmcache/integration/vllm/lmcache_mp_connector.py:920`；例子里 = 16），再向下取整到 chunk：`lmcache/integration/vllm/lmcache_mp_connector.py:1450-1459`。B′（见 Q5，vLLM 命中 320）：`320//16*16//256*256 = 256`。
- 服务端对 `start` 的处理：`[0,start)` 只「touch」刷新 LRU，**不上锁、不预取**；`[start,end)` 才预取：`lmcache/v1/multiprocess/modules/lookup.py:349-363`、`lmcache/v1/multiprocess/modules/lookup.py:365-380`；命中数再**加回** `start_chunk` 变成绝对值：`lmcache/v1/multiprocess/modules/lookup.py:784`。
- **为什么**：【注释】说明了上限：“vLLM caps the APC hit at num_prompt_tokens - 1; an all-covered”（`lmcache/integration/vllm/lmcache_mp_connector.py:1448`），下一行接 lookup is served by the server's empty prefetch job.；源头是提交 `60538a08`（标题 “[core] Skip lookup/prefetch of APC-covered prefix (non-pin / re-lookup method) (#5383)”，无正文）。【测试】佐证 FREE 的**下限**：模块说明写 “so a release never touches a chunk the lookup did not lock”（`tests/v1/multiprocess/test_apc_covered_lookup.py:10`）。【推断】意图是：vLLM 反正已有的前缀，没必要从 L1/L2 再取一遍，也不该给它们上读锁。
- 注意一个**顺带后果**（看代码得出）：被覆盖的前缀**按「命中」计入**，不管 LMCache 里是否真有。【测试】“The empty job still resolves: its hit is the covered prefix, offset from”（`tests/v1/multiprocess/test_apc_covered_lookup.py:304`）说明这是有意的，「让 `num_stored_tokens` 保持绝对值」。【推断】结果是：若某前缀块只在 vLLM 的 APC 里、LMCache 里被淘汰了，这个请求不会补存它（`num_stored_tokens` 已含它）。
- 第一次 poll 才冻结 `apc_covered_tokens`，之后的 poll 不再改：【注释】“Freeze only on a real submit”（`lmcache/integration/vllm/lmcache_mp_connector.py:1473`）；【测试】`tests/v1/multiprocess/test_apc_covered_lookup.py:151`。默认关着的 `eager_prefetch`（请求一入队就发 LOOKUP，`start=0`）走 `lmcache/integration/vllm/lmcache_mp_connector.py:1577-1598`，默认 False：`lmcache/integration/vllm/lmcache_mp_connector.py:720-724`。

### Q4 GetStoreMetadata 每一步怎么算？为什么用 max 不是相加？
公式（`lmcache/integration/vllm/lmcache_mp_metadata.py:398-428`）：

```
computed  = max( scheduled + max(vllm_hit, lmcache_hit),  engine_computed )
allocated = min over KV groups ( len(block_ids) × tokens_per_block )
avail     = min( len(all_token_ids), allocated, computed )
            再 min 上 num_prompt_tokens（默认只存 prompt），再 min 上 max_offload_tokens（若设了）
staging   = avail − num_stored_tokens
num_chunks= staging // 256        ≥1 才出单，区间 [num_stored, num_stored + n×256)
出单后 num_stored += n×256        ← 下单即加，不等存完
```

- **内层 max 不是相加**：【注释】“available (either from vLLM APC or from LMCache), so they must NOT”（`lmcache/integration/vllm/lmcache_mp_metadata.py:381`）、“be summed (that would double-count the overlapping prefix).”（`lmcache/integration/vllm/lmcache_mp_metadata.py:382`）。两者都是「前缀 [0,x) 的 KV 已有」，只是来源不同，相加会把重叠段算两遍。
- **为什么 `lmcache_hit` 也要进来**：【注释】“LMCache-hit tokens are already counted in”（`lmcache/integration/vllm/lmcache_mp_metadata.py:384`）`num_stored_tokens`，要保持上界一致、且它们不重存。
- **为什么 `vllm_hit` 也要进来**：LMCache 命中向下取整到整块，APC 可能命中更多（注释举例 APC=704、LMCache=512），只用 `lmcache_hit` 会漏存中间那段：`lmcache/integration/vllm/lmcache_mp_metadata.py:387-394`。
- **外层 max（下界）**：提交 `4f7d283a` “[Fix][MP] Bound stores by vLLM's computed position (#5911)”（无正文）；【注释】“Lower bound: vLLM's real computed position. Partial hybrid hits”（`lmcache/integration/vllm/lmcache_mp_metadata.py:396`）。【测试】“APC hit 112 floors to 64; chunk [192, 256) must still be stored.”（`tests/v1/test_mp_tracker_store_bound.py:61`）：`num_vllm_hit_tokens` 被向下取整成 64，旧公式 `188+64=252` 够不到 256，漏存 `[192,256)`；新公式 `max(252, 112+188=300)=300`，补上。
- **为什么要 `num_scheduled_tokens` 这一项**：【注释】“Number of scheduled tokens in this request. We keep tracking this to”（`lmcache/integration/vllm/lmcache_mp_metadata.py:85`）“avoid saving tokens whose KV has not been computed yet.”（`lmcache/integration/vllm/lmcache_mp_metadata.py:86`）。它让分块预填（chunked prefill）边算边存。

**例子（设：预填预算 512 token/步，A 分两步算完）**：
- 第 1 步（设：vLLM 按需分配 block）：只分到 32 个 → `allocated=512`；`scheduled=512`；`computed=max(512+0, 512)=512`；`avail=512` → `staging=512` → 2 块，STORE[0,512)，`num_stored=512`。
- 第 2 步：补齐到 63 个 block → `allocated=1008`；`scheduled=1000`；`computed=1000`；`avail=min(1000,1008,1000)=1000`（prompt 封顶仍是 1000）→ `staging=488` → 1 块，STORE[512,768)，`num_stored=768`。
- 对比上一趟 A4（不分块）：一次出 [0,768)。

**生成的 token 不存**：`avail` 被 `num_prompt_tokens` 封顶（`lmcache/integration/vllm/lmcache_mp_metadata.py:423-424`）；开关 `lmcache.mp.save_decode_cache`，默认 False：`lmcache/integration/vllm/lmcache_mp_connector.py:679-681`，文档 `docs/source/mp/configuration.rst:1037-1039`。【提交】`7ba466fe` 正文：“Cap MP stores at the request's prompt length unless”，下一行 lmcache.mp.save_decode_cache is enabled.（该提交改了 `lmcache/integration/vllm/lmcache_mp_connector.py` 与 `lmcache/integration/vllm/lmcache_mp_metadata.py`）。

**非生产者不存**：`kv_role` 不是 producer 时 `_can_store=False`，整段跳过：`lmcache/integration/vllm/lmcache_mp_connector.py:678`、`lmcache/integration/vllm/lmcache_mp_connector.py:2005-2006`。

### Q5 vLLM 自己命中一部分时：取回区间、skip、FREE 怎么配合？
**B′**（上一趟/传输稿设的值）：prompt 同 B，vLLM 本地命中 320 token（20 个 block），LMCache 命中 768。

| 量 | 值 | 怎么来 |
|---|---|---|
| LOOKUP 区间 | `[256, 1024)` | `lookup_start=256`（Q3） |
| 服务端返回 | 768（绝对） | h1、h2 命中 + `start_chunk=1`：`lmcache/v1/multiprocess/modules/lookup.py:784` |
| `num_vllm_hit_tokens` | 320 | `320//16*16`：`lmcache/integration/vllm/lmcache_mp_connector.py:1491-1495` |
| `need_to_load` | `768−320=448` → 返回 `(448, True)` | `lmcache/integration/vllm/lmcache_mp_connector.py:1544` |
| 取回 `start` | 256 | `320//256*256`：`lmcache/integration/vllm/lmcache_mp_metadata.py:618-622` |
| 取回 `end` | 768 | `lmcache/integration/vllm/lmcache_mp_metadata.py:623` |
| `skip_first_n_tokens` | `320−256=64`（= 4 个 block） | `lmcache/integration/vllm/lmcache_mp_metadata.py:644` |
| block 切片 | 32 个（`[16:48]`） | `lmcache/v1/common/group_view.py:266-275` |
| FREE | `end=free_end=320` → 服务端向下取整到 256 → 空区间，**什么也不放**（默认未开 `save_unfull_chunk`） | `lmcache/integration/vllm/lmcache_mp_connector.py:1675-1686`；`lmcache/v1/common/token_hasher.py:266-267`（`end` 只算整块） |

**为什么取回区间从「vLLM 命中所在的 chunk 的起点」开始，而不是从 320**：LMCache 存取以 chunk 为单位，对象整块；切 block 要求区间对齐，不对齐直接抛 `ValueError`（`lmcache/v1/common/group_view.py:258-265`）。所以取回对齐到 chunk，多出来的 64 个 token 用 `skip_first_n_tokens` 在拷贝时跳过。

**为什么要跳过而不是让它覆盖**：【注释】“to avoid a cross-stream data race”（`lmcache/integration/vllm/lmcache_mp_metadata.py:641`），后文说明：RETRIEVE 在 LMCache 的 CUDA 流上写，而别的请求可能正在 vLLM 流上读这些被 APC 共享的 block。【提交】`38dddf92` “[Correctness]: Avoid overwriting APC overlap (#2671)”（无正文；它改了 `lmcache/integration/vllm/vllm_multi_process_adapter.py`、服务端和 CUDA 内核，引入 `skip_first_n_tokens`）。拷贝侧怎么跳见传输稿 Q5。

**为什么 FREE 的 `end` 向下取整（不放末尾 chunk）**：【注释】“the end LMCache block is not freed (i.e., floor division)”（`lmcache/integration/vllm/lmcache_mp_connector.py:1672`）——vLLM 命中 320 落在 chunk 1 中间，chunk 1 仍要被 RETRIEVE，它的锁由 RETRIEVE 放。

**FREE 什么时候真有东西可放**（三种）：
1. **不用取**（`condition=False`）：`free_end = num_lmcache_hit_tokens`，放全部：`lmcache/integration/vllm/lmcache_mp_connector.py:1664-1667`。例如 vLLM 命中 ≥ LMCache 命中。
2. **LOOKUP 起点冻结得比最终 APC 命中低**：LOOKUP 锁了 `[c0, ret)`，而 vLLM 现在覆盖更多。【测试】`tests/v1/multiprocess/test_apc_covered_lookup.py:151-186` 的场景是 4 块冻结、7 块可见。【推断】`free_end=num_vllm_hit_tokens` 在这里放掉 `[c0, 取整后的 vLLM 命中)` 之间被锁的块。
3. **APC 缩水后重查**：见下面 B″。

**B″（设）：APC 缩水**。B′ 第一次 poll 时 vLLM 命中 320（冻结 `apc_covered=256`，LOOKUP 返回 768，服务端锁了 h1、h2）；下一次 poll vLLM 命中缩到 128（设：那几个缓存 block 被挤出）。代码走：
1. `num_vllm_hit_tokens=128 < apc_covered_tokens=256` → 缩水：`lmcache/integration/vllm/lmcache_mp_connector.py:1500-1503`；
2. 把被放弃的 LOOKUP 锁的区间 `(256, 768)` 记进 `pending_free_ranges`：`lmcache/integration/vllm/lmcache_mp_connector.py:1511-1512`；
3. 清掉 `apc_covered`、`num_stored`、`lmcache_hit`，`need_full_lookup=True`（粘性），**同一步**重发一个 `start=0` 的 LOOKUP，返回 `(None, True)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1513-1530`；
4. 之后新 LOOKUP 返回 768 → `need_to_load=768−128=640`；alloc 时 `FREE(start=0, end=128, extra_ranges=((256,768),))`：`lmcache/integration/vllm/lmcache_mp_connector.py:1677-1687`；
5. 服务端：新 LOOKUP 的 `begin_lookup` 已把旧锁清单忘掉（`lmcache/v1/multiprocess/session.py:198-204`），所以 `extra_ranges` 单独按自己的区间解析并释放：`lmcache/v1/multiprocess/modules/lookup.py:578-591`。结果：h1、h2 的读者数 2→1（新 LOOKUP 那把留给 RETRIEVE），h0=1。取回 `start=0,end=768,skip=128`。

为什么「粘性」：【注释】“Sticky: after a shrink, the request looks up the full prefix from token 0.”（`lmcache/integration/vllm/lmcache_mp_metadata.py:126`）；【测试】“makes the fallback fire only once.”（`tests/v1/multiprocess/test_apc_covered_lookup.py:272`）——同一 docstring 说对已回退过的请求再处理会 “Acting on an already-fallen-back request would record a bogus stale”（`tests/v1/multiprocess/test_apc_covered_lookup.py:274`）。APC 为什么会缩水：没查到原文，【推断】等待中的请求不持有 APC block 的引用，两次 poll 之间可被别的请求挤掉。

### Q6 `request_finished` 什么时候返回「延迟回收」？block 什么时候真正回收？
**返回什么**：`delay_free = tracker.store_scheduled`（`lmcache/integration/vllm/lmcache_mp_connector.py:1793-1794`），再并上「还有 boundary-state block 被钉着」（`lmcache/integration/vllm/lmcache_mp_connector.py:1816-1818`）；lazy offload 模式一律 False：`lmcache/integration/vllm/lmcache_mp_connector.py:1804-1811`。

**为什么只对「下过 STORE 单」的请求延迟**：【注释】“Keep the blocks only while a store may still read them.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1791`）；【提交】`752e01fd` “[Bugfix][MP] Delay frees only for requests with a scheduled store (#5938)”（无正文）。diff 显示改前 `request_finished` 的返回是 `self._can_store`——所有生产者的请求一律延迟。【推断】代价是：纯命中、没存任何东西的请求也白等；且 vLLM 对「没返回过 True 的请求出现在 finished_sending」会断言，所以名单必须精确（下一条）。

**回收的完整链路（四跳）**：
1. 调度侧：`_delay_free_req_ids.add(id)`（`lmcache/integration/vllm/lmcache_mp_connector.py:1822-1823`）。
2. 下一次 `build_connector_meta`：交给 `metadata.delay_free_req_ids` 并清空：`lmcache/integration/vllm/lmcache_mp_connector.py:1708-1709`。
3. worker 在 `start_load_kv` 里把它并进自己的 `_delay_free_req_ids`（**在「没有 RETRIEVE 就早退」之前**）：`lmcache/integration/vllm/lmcache_mp_connector.py:1094`。
4. worker `get_finished`：适配器返回「引擎说已结束 + 本 worker 的 STORE future 都完成（或本来就没有）」的请求，再与名单**求交**，并从名单里扣掉：`lmcache/integration/vllm/lmcache_mp_connector.py:1254-1255`；适配器侧 `lmcache/integration/vllm/vllm_multi_process_adapter.py:2093-2110`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:2179-2184`。vLLM 收齐各 worker 的报告后才真正回收 block（vLLM 侧行为）。

**为什么求交**：【注释】“Report only requests whose request_finished returned True: vLLM”（`lmcache/integration/vllm/lmcache_mp_connector.py:1252`）“asserts on a finished_sending report for a request it freed.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1253`）。

**为什么名单要走一趟 metadata 而不是 worker 自己判断**：worker 不知道调度侧「有没有下过 STORE 单」（`store_scheduled` 只在调度侧）。【推断】这就是提交 `752e01fd` 新增 `delay_free_req_ids` 字段（`lmcache/integration/vllm/lmcache_mp_metadata.py:671-672`）的原因。【测试】“vLLM frees a delayed request once every rank reports it, including”（`tests/v1/test_mp_connector_kv_roles.py:654`）——没有自己 STORE future 的 rank 也必须报，否则 vLLM 凑不齐。

**真正回收大约晚几步**：A 的 STORE 早就拷完，`request_finished` 在 sN，名单在 sN+1 到 worker，那一步的 `get_finished` 就能报 → **晚约一步**（【推断】：来自上面的链路；STORE 未完才会更晚）。调度侧在非 lazy 模式**不**用 `finished_sending` 更新 tracker（tracker 在 `request_finished` 里就删了：`lmcache/integration/vllm/lmcache_mp_connector.py:1797`）。

**`request_finished` 还做的**：`cleanup_lookup_result`（丢掉客户端侧 LOOKUP 状态，连「LOOKUP 结果还没被取走就被取消」的请求也覆盖：`lmcache/integration/vllm/lmcache_mp_connector.py:1799-1802`），再发 END_SESSION（`lmcache/integration/vllm/lmcache_mp_connector.py:1821`）。

### Q7 整段 prompt 都命中，为什么少报一个 token？
**例子 C（设）**：prompt = A 的前 768 token，长度 768。LOOKUP `[0,768)`，返回 768 = `len(all_token_ids)`。

- 少报只改**给 vLLM 的数字**：`need_to_load = 768 − 0 − 1 = 767`，返回 `(767, True)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1559-1570`。
- `tracker.num_lmcache_hit_tokens` **仍是 768**，所以 RETRIEVE 仍取 `[0,768)`（整 48 个 block）：【测试】`tests/v1/test_mp_connector_kv_roles.py:937-939` 断言 `(need, async_load) == (11, True)` 且 `tracker.num_lmcache_hit_tokens == 12`，`tests/v1/test_mp_connector_kv_roles.py:954` 断言取回区间 `(0, 12)`。
- 然后 vLLM 认为 `num_computed=767`，只算最后 1 个 token，拿它的 logits。

**为什么**：【注释】“In full-prompt-hit case, we need to recompute the last token.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1555`）“Without this, num_computed_tokens would equal request.num_tokens,”（`lmcache/integration/vllm/lmcache_mp_connector.py:1556`）“causing num_new_tokens to be 0 and triggering the”（`lmcache/integration/vllm/lmcache_mp_connector.py:1557`）调度器的 `assert num_new_tokens > 0`。【提交】`12aaf1dd` “fix: handle full-prompt-hit in MP connector to avoid scheduler assert failure (#3322)”（无正文）。【推断】vLLM 总要重算至少一个 token 才能得到第一个输出的 logits——LMCache 只存 KV 不存 logits；依据只是上面的 assert。vLLM 自己的 APC 也是同一规则（`lmcache/integration/vllm/lmcache_mp_connector.py:1448`）。

**为什么 `num_lmcache_hit_tokens` 不跟着减 1**：【推断】`GetRetrieveMetadata` 切 block 要求 `end` 对齐（`lmcache/v1/common/group_view.py:258-265`），767 不是 16 的倍数，会抛 `ValueError`。

**SWA 前缀回放的例外**（只点名）：`_prefix_replay_tokens>0` 时 vLLM 会把外部命中向下取整到 block 边界再分配，「减 1」会让取回多出一个 block；这时改成**整块让出**：`lmcache/integration/vllm/lmcache_mp_connector.py:1560-1568`。【测试】“A full-prompt hit must not retrieve past what vLLM allocates.”（`tests/v1/test_mp_connector_kv_roles.py:913`）；提交 `a0ff4701`（标题 “[Fix][MP] Keep a full-prompt hit inside vLLM's allocation under SWA prefix replay (#5976)”）。让出的那块的锁记进 `pending_free_ranges`，下次 FREE 一并放。

### Q8 抢占或加载失败怎么收场？
**抢占（PREEMPTED）**：
- 再次被问到时，若 tracker 不在「全新」的 PREFETCHING，就**整个丢掉重建**：`lmcache/integration/vllm/lmcache_mp_connector.py:2150-2166`。守卫「不在 PREFETCHING 才删」是因为同一请求会被反复问：【注释】“will only do the remove if the tracker is not in the "fresh"”（`lmcache/integration/vllm/lmcache_mp_connector.py:2159`）。
- 重建后从头 LOOKUP。**例子（设）**：B 在解码中途被抢占。它的 [0,1024) 已存；`all_token_ids` 现在含已生成的 token，但整块只到 1024 → LOOKUP 返回 1024 → `(1024, True)`，`num_stored=1024` → 不重存。【测试】“A preempted request starts over: its earlier stores do not delay a”（`tests/v1/test_mp_connector_kv_roles.py:608`），下一行接 free while it loads again。
- 防覆盖：本步有抢占/恢复请求 → `need_flush_before_forward=True`（`lmcache/integration/vllm/lmcache_mp_metadata.py:670`，`lmcache/integration/vllm/lmcache_mp_connector.py:1707`、`lmcache/integration/vllm/lmcache_mp_connector.py:195-224`）→ worker 在前向前 `flush_inflight_stores` 并同步：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2436-2443`。理由见上一趟 §5.2。源码里还留着一条 TODO：【注释】“TODO: How does lmcache driven path handle preemption?”（`lmcache/integration/vllm/lmcache_mp_connector.py:1205`）。

**加载失败 → BYPASS_LMCACHE**：
1. worker 侧 RETRIEVE 失败 → block 记入 `error_block_ids`（`lmcache/integration/vllm/vllm_multi_process_adapter.py:2200-2202`）→ vLLM 把请求重置、`num_computed_tokens` 归零重新排队（vLLM 侧行为；仓库里的描述：`lmcache/integration/vllm/lmcache_mp_connector.py:1418-1425`）。
2. vLLM 再问时：tracker 仍 READY、`request.num_computed_tokens==0`、曾有命中 → 判定为「上次加载失败」：`lmcache/integration/vllm/lmcache_mp_connector.py:1426-1430`。
3. 收场：`cleanup_lookup_result`，清空 block / 命中 / `num_stored`，置 BYPASS，返回 `(0, False)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1436-1443`。**不发 FREE**——【测试】`tests/v1/test_vllm_mp_adapter.py:1353` 处的断言（用例从 `tests/v1/test_vllm_mp_adapter.py:1304` 起）： `free_lookup_locks.assert_not_called()`；【推断】RETRIEVE 的锁在服务端「成败都放」（传输稿 Q4 / R5）。
4. 在 BYPASS 里反复被问一律 `(0, False)`，不再发 LOOKUP：【注释】“A failed asynchronous load is bypassed until vLLM admits the request”（`lmcache/integration/vllm/lmcache_mp_connector.py:1411`）；`lmcache/integration/vllm/lmcache_mp_connector.py:1415-1416`。
5. vLLM 本地重算并分配 → `update_state_after_alloc` 把 BYPASS 改回 READY 并立刻返回：【注释】“Returning zero external tokens admitted this request for local”（`lmcache/integration/vllm/lmcache_mp_connector.py:1642`）。

**为什么要 BYPASS 这个状态**：【测试】“A failed full async load must not re-enter remote wait forever.”（`tests/v1/test_vllm_mp_adapter.py:1304`）；【注释】“Fail closed for this request instead”（`lmcache/integration/vllm/lmcache_mp_connector.py:1424`）；引入于提交 `23cca679` “fix(mp): complete missing-registration GPU transfers (#4709)”，其中一条子提交标题是 “fix(mp): recover vllm after rejected GPU load”。不 BYPASS 的后果：重用旧 tracker 会再报一次外部命中，却不经过 WAITING_FOR_LOAD，请求永远卡在 `WAITING_FOR_REMOTE_KVS`（`lmcache/integration/vllm/lmcache_mp_connector.py:1421-1424`）。

**BYPASS 之后会重存**（【推断】看 `num_stored_tokens=0` 与 §Q4 公式）：该请求本地重算后，会把整段 prompt 当「还没存」再下单；服务端对已在 L1 的 key 会跳过（上一趟 B8 / 传输稿 Q4）。

---

## 4. 值得做成源码页的 6 段

每段先说在干什么，再列逐行批注（行号为绝对行号）。

### 页 1：LOOKUP 的异步链（`lmcache/integration/vllm/mp_lookup_state.py:228-282` 与 `lmcache/integration/vllm/mp_lookup_state.py:327-353`，共约 80 行，可按两个小段展示）
在干什么：一次「调度检查」要做哪几件事；以及回复 None 时何时重发。
- `lmcache/integration/vllm/mp_lookup_state.py:241-242`：`_result` 已定就直接返回，**结果粘住**。
- `lmcache/integration/vllm/mp_lookup_state.py:244-246`：把所有在途的 QUERY 标 `scheduler_checked=True`。这是「调度步」在这个类里留下的**唯一痕迹**。
- `lmcache/integration/vllm/mp_lookup_state.py:250-252`：上次回复为 None 而延后的，在这里重发。
- `lmcache/integration/vllm/mp_lookup_state.py:255-266`：只有 `blocking_lookup=True`（`nonblocking_lookup_status=False`）才 `future.wait`；默认不等。
- `lmcache/integration/vllm/mp_lookup_state.py:274-279`：过期 → TIMED_OUT 且 `_result=0`。
- `lmcache/integration/vllm/mp_lookup_state.py:339-346`：int 回复 → RESOLVED；`lmcache/integration/vllm/mp_lookup_state.py:348-353`：None → 立即重发或进 NEEDS_REQUERY。
- 容易看错：命中数是**所有服务端的最小值**（`lmcache/integration/vllm/mp_lookup_state.py:373-374`），多出来的尾部锁由 `free_lookup_locks` 当场放（`lmcache/integration/vllm/mp_lookup_state.py:386-399`）。

### 页 2：`get_num_new_matched_tokens` 前半（`lmcache/integration/vllm/lmcache_mp_connector.py:1409-1480`，72 行）
在干什么：先处理「BYPASS / 加载失败」，再算 LOOKUP 起点，发 LOOKUP，当场问一次。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1409`：tracker 不存在就建；抢占重建也在里面（§Q8）。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1415-1416`：BYPASS 里一律 `(0, False)`，守卫「别重复发 LOOKUP」。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1426-1430`：三个条件缺一不可；`request.num_computed_tokens==0` 区分「加载成功后」和「失败后」。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1450-1459`：魔数式的两次整除；先 block 公倍数、再 chunk。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1463-1472`：`start=lookup_start`；注意**每个 poll 都会进来**，靠适配器内的「已有 LOOKUP 就不发」去重。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1475-1476`：只在真发时才冻结 `apc_covered_tokens`。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1478-1480`：`None` → `(None, True)`，把「再问一次」交还给 vLLM。

### 页 3：`get_num_new_matched_tokens` 后半（`lmcache/integration/vllm/lmcache_mp_connector.py:1491-1575`，85 行，`lmcache/integration/vllm/lmcache_mp_connector.py:1546-1553` 只是日志可略）
在干什么：拿到结果之后：缩水检测 → 零命中早退 → 记命中 → 算要取多少 → 整段命中少报一个。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1491-1495`：每个 poll 重算 `num_vllm_hit_tokens`（向下取整到公倍数）。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1500-1530`：缩水分支，**同一步**重发 LOOKUP 并返回 `(None, True)`。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1532-1533`：`ret==0` 早退，**此时 `num_lmcache_hit_tokens`/`num_stored_tokens` 都没被写**（保持 0）。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1538-1542`：`num_stored` 取整块；末尾未满块只记进 `unfull_chunk_boundaries`。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1544`：`max(0, ret − num_computed_tokens)`——LMCache 比 vLLM 少时不为负。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1559-1570`：整段命中少报一个；SWA 分支整块让出。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1575`：返回 `(need, need>0)`；第二个值为 False 当 need=0，满足 vLLM 接口约定「第一个元素为 0 时必须 False」（`lmcache/integration/vllm/lmcache_mp_connector.py:1398-1400`）。

### 页 4：alloc 回调 + 取回工单（`lmcache/integration/vllm/lmcache_mp_connector.py:1626-1692`，67 行；`lmcache/integration/vllm/lmcache_mp_metadata.py:610-652`，43 行）
在干什么：记 block、定岔路、放用不上的锁；下一步把岔路变成 RETRIEVE 单。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1629-1638`：**只追加超出已记录数的 block**，因为 vLLM 传的是全部 block（`lmcache/integration/vllm/lmcache_mp_connector.py:1618-1624` 的注释）。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1641-1646`：BYPASS 先于一切，直接 READY 返回。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1648-1656`：`condition` 与状态岔路。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1658`：此刻才 `cleanup_lookup_result`（不是拿到结果时）→ LOOKUP 状态一直存活到分配。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1663-1675`：`free_end` 三选一：0 / 全部命中 / vLLM 命中数。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1677-1687`：有区间才发 FREE，并清 `pending_free_ranges`。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:610-611`：只有 WAITING_FOR_LOAD 才出单。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:618-624`：`start` 向下取整到 chunk；`end` 取命中数；末尾未满块才 `include_unfull_chunk`。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:644`：`skip = vllm命中 − start`，永远 < 一个 chunk。

### 页 5：`GetStoreMetadata`（`lmcache/integration/vllm/lmcache_mp_metadata.py:398-457`，60 行）
在干什么：§Q4 的公式，原样。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:398-402`：两层 max（§Q4）。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:408-417`：`allocated_tokens` 取各 KV group 的最小——混合模型里各组一个 block 覆盖的 token 数不同。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:423-426`：prompt 封顶、`max_offload_tokens` 封顶。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:427-430`：`num_chunks>=1` 才出单。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:439`：`packed_token_ids()` 带的是**全部 token**，服务端要从头算滚动哈希。为不让它拖慢调度线程：【注释】“emits one op per scheduler step, so repacking the whole sequence each”（`lmcache/integration/vllm/lmcache_mp_metadata.py:135`），后文说否则会把上下文长度重新放回调度器的关键路径；提交 `69132592` “[Perf][MP] Carry token ids as a packed uint32 buffer instead of a list of ints (#5150)”。
- `lmcache/integration/vllm/lmcache_mp_metadata.py:456`：**在函数里就加 `num_stored_tokens`**——所以函数不是纯函数，调用两次会少出单。

### 页 6：`request_finished` + worker 求交（`lmcache/integration/vllm/lmcache_mp_connector.py:1793-1824`，32 行；`lmcache/integration/vllm/lmcache_mp_connector.py:1252-1256`，5 行；`lmcache/integration/vllm/lmcache_mp_connector.py:1094`）
在干什么：§Q6。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1793-1794`：**先读** `store_scheduled`，
- `lmcache/integration/vllm/lmcache_mp_connector.py:1797`：**再删** tracker——顺序依赖。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1802`：在 lazy / 非 lazy 分叉之前清 LOOKUP 状态。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1821-1824`：END_SESSION → 登记名单 → 返回。END_SESSION 在服务端只删 Session 并刷新 LRU，**不放读锁**：`lmcache/v1/multiprocess/modules/lookup.py:611-646`。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1254-1255`：worker 求交并扣名单。
- `lmcache/integration/vllm/lmcache_mp_connector.py:1094`：名单在 `start_load_kv` 里并入，且在早退之前。

---

## 5. 容易看错、最好对照着看

1. **两次「max」不是同一件事**：Q4 的 `max(vllm, lmcache)` 防重复计数；外层 `max(…, engine_computed)` 是下界。
2. **`num_stored_tokens` 是「已下单」不是「已存好」**：非 lazy 模式下调度侧收不到存储失败的回报（`lmcache/integration/vllm/lmcache_mp_connector.py:1742-1743` 直接返回）。STORE 失败后这些块缺在 L1 里，tracker 不会重试。【注释】`LMCacheMPWorkerMetadata` 的 docstring 说失败要「break the request's stored-prefix chain」（`lmcache/integration/vllm/lmcache_mp_metadata.py:710-716`），但只有 lazy offload 的 manager 消费它。
3. **LOOKUP 的读锁**：谁上（LOOKUP）、谁用（RETRIEVE）、谁放（RETRIEVE 回调 / FREE / TTL）；调度侧**只有两处**会发 FREE：alloc 回调和多服务端取最小值时的尾部（`lmcache/integration/vllm/lmcache_mp_connector.py:1678`、`lmcache/integration/vllm/mp_lookup_state.py:399`）。
4. **`update_state_after_alloc` 的 `blocks` 是全部 block 而不是增量**（`lmcache/integration/vllm/lmcache_mp_connector.py:1618-1624`）。
5. **`wait_for_save` 不等、`request_finished` 的「延迟」才是保护**（上一趟 §5.2）。
6. 被请求的 `ret` 是**绝对**命中数，不是 LOOKUP 区间内的命中数（`lmcache/v1/multiprocess/modules/lookup.py:784`）。

---

## 6. 故障与降级（这个框里的）

| 坏在哪 | 收场 | 锚点 |
|---|---|---|
| 服务端不健康 | 新请求不发 LOOKUP、`check_lookup_result` 返回 0；`free_lookup_locks`、`end_session` 直接返回 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:850-851`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:914-918`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1048-1049`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1077-1078` |
| LOOKUP/QUERY 超 300 秒不答 | 命中数 0、服务端标不健康 | `lmcache/integration/vllm/mp_lookup_state.py:274-279`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1267-1274` |
| LOOKUP 的 ack 失败 | 仍然继续发 QUERY（只记日志） | `lmcache/integration/vllm/mp_lookup_state.py:317-325` |
| 多服务端命中不一致 | 取最小，向下取整到整块，多出的尾锁当场放 | `lmcache/integration/vllm/mp_lookup_state.py:375-399` |
| 异步加载失败 | BYPASS（Q8） | `lmcache/integration/vllm/lmcache_mp_connector.py:1426-1443` |
| 请求在 alloc 之前被取消 | 只清客户端状态 + END_SESSION；**不发 FREE**，读锁等 TTL（见 §7、§8） | `lmcache/integration/vllm/lmcache_mp_connector.py:1799-1802`、`lmcache/integration/vllm/lmcache_mp_connector.py:1821` |

---

## 7. 没查到为什么

1. **为什么 LOOKUP/状态轮询要改成不阻塞、以及不阻塞带来的步数分布**：提交 `ba8acd84`、`588c5366` 无正文，仓库里也没有实测数据。「下限是下一步」是我从代码推的（Q2）。
2. **APC 前缀「按命中计入」的取舍**：提交 `60538a08` 标题里写的 “non-pin / re-lookup method” 暗示还有一种「pin」方案，但仓库里没说它是什么、为什么没选。
3. **为什么 APC 会缩水**（Q5 B″）：代码处理了，没有任何地方解释触发条件。我的说法是推断。
4. **为什么取消的请求不放 LOOKUP 的锁**：`free_lookup_locks` 的 docstring 明说可用于「cancelled or aborted after lookup but before retrieve」（`lmcache/integration/vllm/vllm_multi_process_adapter.py:1028-1029`），但 `request_finished` 没调它（全仓库调用点只有三处，见 §5.3）。没查到是有意还是遗漏。
5. **为什么 LOOKUP 的 RPC 超时直接用 300 秒的 `mq_timeout`**：该配置的说明只提到「blocking MQ requests」（`lmcache/integration/vllm/vllm_multi_process_adapter.py:121-123`），没有说为什么 lookup 也用它。
6. **`build_connector_meta` 里为什么先 retrieve 后 new/cached**（`lmcache/integration/vllm/lmcache_mp_connector.py:1711-1713`）：worker 按方向过滤，顺序看不出影响；没有注释。
7. **为什么 `request_finished` 先 `cleanup_lookup_result` 再 `end_session`**：适配器的 `end_session` 里有「等在途 RPC 完成，防止其回调在 END_SESSION 之后重建 session」的 `drain`（`lmcache/integration/vllm/vllm_multi_process_adapter.py:1080-1085`），但 `cleanup_lookup_result` 先把 `_lookups` 里的项弹掉了（`lmcache/integration/vllm/vllm_multi_process_adapter.py:941`），所以这条路径上 `drain` 不会触发。没查到这是否有意；服务端默认 CPU 池 1 线程，请求按到达序执行（上一趟 §2.1），也许是靠这个兜底——这只是推断。

---

## 8. 可改进处【观点】

- 【观点】请求在 LOOKUP 拿到结果之后、alloc 之前被取消，读锁要挂到 TTL（默认 300 秒）。`request_finished` 若记得「已有命中且没走 alloc」就发一次 FREE，可更早释放。
- 【观点】vLLM 因没空闲 block 反复重问同一请求时，LOOKUP 的读锁一直挂着（Q2 的副作用），热门前缀在高负载下可能被钉住不能淘汰。
- 【观点】`num_stored_tokens` 下单即加、非 lazy 模式不吃 STORE 失败回报：失败后缓存缺口没有自愈。
- 【观点】`LMCacheMPRequestState` 的 docstring 还写旧函数名（§2.1）。

---

## 9. 给讲解员的提示

- **本层图建议突出的三件事**（都是读者最容易迷路处）：(1) `get_num_new_matched_tokens` 被**反复问**，每问一次 LookupState 推进一点；(2) tracker 上 `num_vllm_hit / num_lmcache_hit / num_stored / allocated_block_ids / state` 这五个字段随步变化；(3) 「延迟回收」是调度侧→worker→vLLM 三跳。
- **最好对照着看**：A 的表（§2.2，只存）和 B 的表（§2.3，先取后存）；`B′`（vLLM 命中一部分）与 `B″`（APC 缩水）。
- **「时间上不连续」**：LOOKUP 发出与取回命中数之间隔 ≥1 个调度步；RETRIEVE 工单在 alloc 的同一步发出，但 B 要到加载完成之后才被调度计算；STORE 工单在算完的那步发出，而 block 在请求结束后再晚约一步才回收。
- **本稿新设的值**（全部标「设」，其余沿用上一趟/传输稿/L1 稿）：步号 s1、s2…（只表先后）；A 分块预填 512 token/步；C（768 token 整段命中）；B″（APC 命中从 320 缩到 128）；B 在解码中途被抢占。**沿用的值**：A、B、B′（vLLM 命中 320）、b0…b47、chunk 256、block 16、63 / 75 个 block。
