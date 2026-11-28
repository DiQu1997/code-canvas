# L1 存储框的内部：对象怎么存下来、锁住、不被挤掉（补充底稿）

> 研究对象：LMCache 仓库 HEAD = `b27f6bd9e2`。本稿只讲顶层图上的「L1 存储」框（上一趟底稿 §2.2 ⑨ 的 L1 部分）。
> 锚点格式 `文件:行号`；英文引文用弯引号括起，紧跟（出处），逐字照抄。「为什么」都标【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 例子沿用上一趟与传输模块底稿：Qwen3-8B、TP=1、chunk=256、对象 36 MiB、请求 A 存 `[0,768)`＝对象 h0/h1/h2、请求 B 命中 768。补设的值标「设」。
> 上一趟已讲清、这里不重复：预取在无 L2 时同步完成（上一趟 §5.7）、STORE/RETRIEVE 的 GPU 侧时间线（传输模块底稿）、哈希与 key 的生成（上一趟 §5.5）。

---

## 0. 先看这个（一段话）

L1 是服务进程里的一张**「对象表 + 两把带 TTL 的锁」**，外加一个后台淘汰线程。

- 写：先 `reserve_write` 拿一块**读者看不见**的 staging 内存，GPU 拷完再 `finish_write` 转正。
- 读：先 `reserve_read`（查 + 上读锁，读锁只是个计数），之后 `unsafe_read` 直接读、不再加锁，用完 `finish_read` 减一。
- 淘汰：每秒看一次用量，超过 80% 就按 LRU 删约 20% 的 key，**只删没被读锁锁着的**。
- 锁不靠「谁持有」，只靠「计数 + 过期时间」：客户端崩了没人放锁，锁自己过期（读 300 秒、写 600 秒）。

状态不是一个枚举字段，而是**「对象在哪张表里 + 两把锁现在是否有效」**推出来的（§1.3）。这是读者最容易困惑的点。

---

## 1. 这个框里有什么

### 1.1 部件与职责（按「读写哪些状态」）

| 部件 | 一句话 | 它持有的状态 | 锚点 |
|---|---|---|---|
| `StorageManager` | 门面：对外 `reserve_write / finish_write / submit_prefetch_task / unsafe_read / finish_read_prefetched`；启动时把下面几样拼起来 | `_l1_managers`、`_write_policy`、`_eviction_controllers`、`_prefetch_controller` | `lmcache/v1/distributed/storage_manager.py:131-272` |
| `DramL1Manager`（继承 `BaseL1Manager`） | **对象表 + 状态机 + 锁**；所有 key 的生死都在这里 | `_objects`（常驻对象）、`_staging`（暂存对象）、`_staging_bytes`、一把全局 `_lock`、监听者列表 | `lmcache/v1/distributed/l1_manager/base.py:213-231`，`lmcache/v1/distributed/l1_manager/dram.py:16-30` |
| `TTLLock`（C++ 原子） | 带过期时间的计数锁；每个对象带两把：`write_lock`、`read_lock` | `counter_`、`expiration_ms_`、`ttl_ms_` | `csrc/lmcache_native/ttl_lock.h:79-86` |
| `L1MemoryManager` → 分配器 | 给对象分 CPU 固定内存；懒增长 | 空闲块链表（`AddressManager`）、已 pin 的堆大小 | `lmcache/v1/distributed/memory_manager/l1_memory_manager.py:116-141`，`lmcache/v1/memory_management.py:1228-1312` |
| `L1EvictionController` + `LRUEvictionPolicy` | 后台淘汰线程 + 它的「谁最久没用」名单 | LRU 的 `_order`（OrderedDict） | `lmcache/v1/distributed/storage_controllers/eviction_controller.py:88-190`，`lmcache/v1/distributed/eviction_policy/lru.py:53` |
| `L1EvictionPolicy`（监听桥） | 把 L1 的事件翻译成 LRU 的 `on_keys_*` 调用 | 无 | `lmcache/v1/distributed/eviction.py:175-215` |
| `PrefetchController` | 「查 + 上读锁」的执行者；带 L2 时还管 L2 查找/加载 | 每个在途请求的 `PrefetchKeyState`、已完成结果表 `_completed_results` | `lmcache/v1/distributed/storage_controllers/prefetch_controller.py:434-447`，`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:731-750` |

`StoreController`（L1→L2）与 L2 淘汰只在有 L2 时工作，主路不展开（上一趟 §8）。

### 1.2 线程：谁在什么线程上碰 L1

- **GPU 亲和线程**（STORE/RETRIEVE）调 `reserve_write`、`unsafe_read`（传输模块底稿）。
- **CUDA 流回调的排空线程**调 `finish_write` / `finish_read`（上一趟 §5.6）。
- **普通 CPU 线程池**（LOOKUP）调 `submit_prefetch_task`；无 L2 时 `reserve_read` 就在这条线程上执行：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:718-723`。
- **淘汰线程**（`daemon=True`，每 1 秒醒一次）：`lmcache/v1/distributed/storage_controllers/eviction_controller.py:45-50`、`lmcache/v1/distributed/storage_controllers/eviction_controller.py:165`。
- **预取控制器线程**：只在带 L2 时有活干。
- **懒分配扩容线程**（后台一块块 pin 内存）：`lmcache/v1/memory_allocators/lazy_memory_allocator.py:188-192`、`lmcache/v1/memory_allocators/lazy_memory_allocator.py:363-379`。

并发保护：`BaseL1Manager` 的几乎所有读写方法都挂 `@l1_mgr_synchronized`，即同一把 `self._lock`：`lmcache/v1/distributed/l1_manager/base.py:106-116`、`lmcache/v1/distributed/l1_manager/base.py:213`。**例外**：`is_key_evictable` 和 `touch_keys` 不拿这把锁（§4）。

### 1.3 对象的「状态」到底存在哪（隐式状态机）

`L1ObjectState` 只有 `memory_obj / write_lock / read_lock / is_temporary / staging_charge` 五个字段，**没有 state 字段**：`lmcache/v1/distributed/l1_manager/base.py:79-97`。状态由「在哪张表」+「锁是否有效」推出：

| 状态 | 在哪 | 锁 | 读者看得见？ |
|---|---|---|---|
| staging（写锁定） | `_staging[key][tag]` | `write_lock` 有效 | 否，`reserve_read` 报 `KEY_NOT_EXIST` |
| ready | `_objects[key]` | `read_lock` 计数 0（或已过期） | 是 |
| read_locked(n) | `_objects[key]` | `read_lock` 计数 n 且未过期 | 是 |
| 被遗弃的 staging | `_staging[key][tag]` | `write_lock` 已过期 | 否；可被淘汰回收 |

依据：`lmcache/v1/distributed/l1_manager/base.py:215-219`（两张表的注释与声明）、`lmcache/v1/distributed/l1_manager/base.py:983-992`（`is_key_evictable` 就是这样推的）、官方状态图 `lmcache/v1/distributed/l1_manager/base.py:142-191`。

---

## 2. 例子设定（接上一趟，补设标「设」）

- 对象 36 MiB ＝ 37,748,736 B（与传输模块底稿一致），恰好是 4096 的整数倍（9216×4096），所以对齐不浪费（默认对齐 `lmcache/v1/distributed/config.py:272-273`）。
- L1 ＝ 20 GiB（`--l1-size-gb 20`，上一趟 §4.1）。懒分配默认初始 20 GB 且不超过总量，所以这里**一开始就是满额 20 GiB 的堆**，不再扩容：`lmcache/v1/distributed/config.py:269-270`、`lmcache/v1/distributed/config.py:293`。
- 推算：20480 MiB ÷ 36 MiB ＝ 568 个对象（上一趟的数）。用量 ≥ 80% ＝ 16384 MiB 时触发淘汰；455 个对象＝16380 MiB（未到），**第 456 个对象写入后**才到线。此时 LRU 名单有 456 个 key，淘汰 `int(456×0.2)=91` 个，剩 365 个（≈64%）。（推算，设：L1 里只有这一种对象、没有碎片。）
- **设**：t=0 时 B 的 LOOKUP 对 h0/h1/h2 各上 1 把读锁（TP=1，读者数 1）；读 TTL 取默认 300 秒。
- **设**：另一个请求 A2 与 A 几乎同时 STORE 同样的 `[0,768)`。

---

## 3. 逐步推演：读者会依次问的问题

### Q1. 一个对象从无到有，状态怎么变？（A 的 h0、h1、h2）

1. **申请**。传输模块调 `StorageManager.reserve_write(keys, layout_desc)`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:960-962`。门面先问「写放置策略」该写哪个 L1（默认全给第一个 L1），再校验、再逐个 L1 调 `reserve_write(..., tag=_L1_WRITE_TAG)`：`lmcache/v1/distributed/storage_manager.py:314-325`，`lmcache/v1/distributed/storage_manager.py:99-101`，`lmcache/v1/distributed/storage_controllers/write_policy.py:99-110`。
2. **逐 key 检查**（`L1Manager.reserve_write`）：key 已在 `_objects` → `KEY_NOT_WRITABLE`；同 tag 已有活的 staging → `KEY_NOT_WRITABLE`：`lmcache/v1/distributed/l1_manager/base.py:526-535`。h0/h1/h2 都过关，进 `need_to_allocate`：`lmcache/v1/distributed/l1_manager/base.py:549`。
3. **一次分配 3 块**：`self._memory_manager.allocate(layout_desc, 3)`，成功则每块包成 `L1ObjectState`，**配两把新 TTLLock**（写 600 秒、读 300 秒），立即 `write_lock.lock()`，放进 `_staging`：`lmcache/v1/distributed/l1_manager/base.py:555-581`、`lmcache/v1/distributed/l1_manager/base.py:1151-1155`。
4. **通知监听者**：`on_l1_keys_reserved_write` → LRU 从此刻起就跟踪这 3 个 key：`lmcache/v1/distributed/l1_manager/base.py:583-584`、`lmcache/v1/distributed/eviction.py:200-203`。
5. GPU 拷贝（传输模块底稿 §3）。拷完，流回调调 `StorageManager.finish_write` → `L1Manager.finish_write`：`lmcache/v1/distributed/storage_manager.py:353-378`。
6. **转正**（`finish_write`）：`_take_staging` 先检查写锁**还有效**（过期则 `KEY_IN_WRONG_STATE`），再 `unlock` 并从 `_staging` 弹出；然后 `self._objects[key] = entry`；非临时对象才通知监听者（LRU `on_keys_created`、StoreController）：`lmcache/v1/distributed/l1_manager/base.py:635-658`、`lmcache/v1/distributed/l1_manager/base.py:1189-1204`。

**A 走完**：`_objects` 有 3 项，read_lock 计数 0 → ready；`_staging` 空。

**为什么要先 staging 再转正，而不是直接放进对象表？**
- 【文档】旧设计的问题：“Two prefetch requests loading the same L2 object therefore collided: the second reservation failed, the prefetch controller abandoned its whole L2 load (all-or-nothing) and reported a non-hit for a chunk that was seconds away from being resident.”（`docs/design/v1/distributed/l1_manager/staging-and-write-tags.md:10-14`）。
- 【注释】新设计：“Staging objects are kept apart from the resident objects: readers of the key do not see them, and writers with different tags may stage the same key at the same time.”（`lmcache/v1/distributed/l1_manager/base.py:151-154`）。
- 【提交】`2848bfec` 标题「[3/N] Update L1Manager's reserve_write semantics to avoid write-write conflict (#5247)」，只有标题，细节在上面的设计文档里。
- 【推断】对「GPU 异步拷贝」这条主路：拷贝在 GPU 流上要时间，内存必须先占住，但内容没拷完不能让别人读。staging 就是这两个需求的交点（依据：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1003-1011` 把 `finish_write` 挂在流回调上）。

### Q2. 取回：一次 `submit_prefetch_task` 在三个对象之间怎么走？（B 的 LOOKUP）

三者分工：**StorageManager 管句柄与日志；PrefetchController 管规划、上锁、（有 L2 时）加载；L1Manager 管真正的锁**（【文档】“The storage manager owns the request handle and the logging; planning, locking and loading live in the prefetch controller”，`docs/design/v1/distributed/storage_manager.md:5-7`）。

B 的 LOOKUP 走法（无 L2，全在 LOOKUP 线程上）：

1. `LookupModule` 组 `PrefetchTaskSpec`（默认 `fetching_policy="prefix"`、`lock_mode=LOCK`）：`lmcache/v1/multiprocess/modules/lookup.py:382-391`，`lmcache/v1/distributed/api.py:742-746`。
2. `StorageManager.submit_prefetch_task` → `PrefetchController.submit_prefetch_request`：`lmcache/v1/distributed/storage_manager.py:584`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:693-724`。
3. 无 L2 分支：`_lock_l1_keys` → `_plan_load(request, {})` → `_finish_request`，**全部同步**：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:718-723`。
4. `_lock_l1_keys`：对每个 L1、每个 key 行调 `reserve_read(row.keys, row.num_kv_readers)`，成功位记进 `l1_locked_keys`：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1273-1289`。B 的 h0/h1/h2 命中、h3′ `KEY_NOT_EXIST`。
5. `L1Manager.reserve_read`：对每个已存在的 key，把 `read_lock.lock()` 调 `read_locks` 次，返回对象；**staging 对象一律当不存在**：`lmcache/v1/distributed/l1_manager/base.py:318-337`。
6. `_plan_load`：按「前缀」策略算该保留哪些格；**计划之外的读锁当场 `finish_read` 释放**：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1313-1336`。B 的 3 格就是连续前缀，不释放任何锁。（对照 **设**：若链是 h0 命中、h1 未命中、h2 命中，h2 的锁会在这里被放掉，因为前缀在 h1 断了。【文档】`prefix` 的含义：“only the longest prefix every row can serve under its window”，`docs/design/v1/distributed/storage_manager.md:36`。）
7. `_finish_request`：对命中的 key 调 `touch_keys` 刷新 LRU；`LOCK` 模式下**保留**读锁，并把「哪个 L1 持有」写进 `PrefetchResult.l1_owners`；`NO_LOCK` 才在此释放：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1722-1733`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1748-1754`。
8. 结果放进 `_completed_results`；调度侧 QUERY 时 `query_prefetch_result` **弹出**（每个结果只能取一次）：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1769-1772`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:749-750`。

之后 RETRIEVE：`StorageManager.unsafe_read` 直接按 key 取对象，**不再加锁**：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1330-1332`、`lmcache/v1/distributed/storage_manager.py:710-725`。拷完，流回调 `finish_read_by_owner` → `finish_read_prefetched` 把每个 key 的锁减 1：`lmcache/v1/distributed/storage_manager.py:1424-1440`。

**为什么 LOOKUP 就要上读锁，而不是 RETRIEVE 时才上？**
- 【文档】“Observation is acquisition.” 以及 “so a concurrent eviction can shrink what gets discovered but never invalidate a reported hit.”（`docs/design/v1/distributed/storage_controllers/prefetch_controller.md:29-33`）。也就是：LOOKUP 报给 vLLM 的「能命中 768」必须是兑现得了的承诺，所以「看见」的同时就必须「锁住」。
- 配合上一趟：LOOKUP 与 RETRIEVE 之间隔着好几个调度步（上一趟 §4.3 A3 / B2），中间没有锁就可能被淘汰【推断】。

**为什么 RETRIEVE 用 `unsafe_read`、不再加锁？**
- 【注释】“the caller need to make sure the `unsafe_read` is called between `reserve_read` and `finish_read` calls.”（`lmcache/v1/distributed/l1_manager/base.py:349-351`）。
- 名字里的 unsafe 就是这个意思：它只检查「现在有锁」（`read_lock.is_locked()`，否则 `KEY_NOT_READABLE`，`lmcache/v1/distributed/l1_manager/base.py:372-374`），**不替你证明锁是你的**。

**读锁为什么是「计数」，一次上 n 把？**
- 【注释】“read_locks: Total read locks acquired per key -- one per worker that consumes a read lock for the same key (e.g. MLA models with TP > 1).”（`lmcache/v1/distributed/l1_manager/base.py:306-309`）。每个 worker 各取各放，所以 LOOKUP 一次上 `num_kv_readers` 把，每个 worker RETRIEVE 结束放 1 把。主路 TP=1 就是 1 把。
- 【提交】`a973aa8e` 标题「Fix to support mla multiple tp failed to read issue (#2697)」，改动含 `lmcache/v1/distributed/l1_manager.py`（后来改名为 `l1_manager/base.py`）；提交说明只有标题。
- 单次上限 128 把：【注释】“to prevent a single call from holding the global lock for too long.”（`lmcache/v1/distributed/l1_manager/base.py:39-41`）。
- 【注释】TODO 说明现在是 Python 循环逐把加：“overhead (TTLLock is C++ std::atomic).”（`lmcache/v1/distributed/l1_manager/base.py:331-333`）。

**读锁是匿名的，所以别人替你放不了、也不能乱放**
- 【注释】“L1 locks are anonymous refcounts: guessing a range could strip a concurrent reader's lock.”（`lmcache/v1/multiprocess/modules/lookup.py:553-555`）。这就是为什么服务端 Session 要精确记下「这次 lookup 锁了哪些 key、哪个 L1」（上一趟 §5.4），而不是事后猜。

### Q3. 读锁计数和 TTL 怎么配合？客户端崩了会怎样？

**TTLLock 的规则**（`csrc/lmcache_native/ttl_lock.cpp:13-75`，注释 `csrc/lmcache_native/ttl_lock.h:33-47`）：

| 操作 | 规则 |
|---|---|
| `lock()` | 若旧 TTL 已过期：计数**重置为 1**；否则计数 +1。两种情况都把过期时间刷新为「现在 + TTL」。 |
| `unlock()` | 计数 −1，最低到 0。 |
| `is_locked()` | 计数 > 0 **且**没过期。 |

- 【注释】“If the previous TTL has expired, the counter is reset to 1.”（`csrc/lmcache_native/ttl_lock.h:36`）。
- 【测试】刷新 TTL：`tests/v1/lmcache_native/test_ttl_lock.py:126-146`；过期后重置：`tests/v1/lmcache_native/test_ttl_lock.py:148-167`。

**用例子走三种情况**（TTL=300 秒，**设**时间）：

1. **正常**：t=0 B 的 LOOKUP 上锁 → 计数 1、到期 t=300。t=2 RETRIEVE 拷完，`finish_read` 计数→0，ready。
2. **同时有别人读**：t=200 另一个请求 C 也 `reserve_read` h0 → 计数 2，**到期被刷新到 t=500**。这两个持有者共享一个过期时间：谁最后上锁，谁决定大家的过期。（由 `lock()` 规则推出，非单独注释。）
3. **B 的客户端在 LOOKUP 后崩了**（没人调 `finish_read`）：t=300 起 `is_locked()` 为假 → `is_key_evictable` 为真，LRU 轮到它时可被删。计数里残留的 1 不会累积——下一个 `lock()` 发现已过期，直接重置为 1。
   - 【文档】设计意图：“Each object has two ``TTLLock`` instances (read and write) with configurable timeouts to prevent deadlocks from crashed clients.”（`docs/source/mp/index.rst:370-371`）。
   - 用户配置：读 300 秒、写 600 秒：`lmcache/v1/distributed/config.py:429-433`，命令行 `--l1-read-ttl-seconds / --l1-write-ttl-seconds`：`lmcache/v1/distributed/config.py:1046-1057`。

**过期之后，晚到的 `finish_read` 会怎样？** `finish_read` 先查 `is_locked()`：已过期就**不减计数**，返回 `KEY_IN_WRONG_STATE` 并打警告：`lmcache/v1/distributed/l1_manager/base.py:426-433`。【注释】docstring：“KEY_IN_WRONG_STATE: The key is not read-locked, which means the reader may read inconsistent data.”（`lmcache/v1/distributed/l1_manager/base.py:406-407`）。

**过期之后，晚到的 RETRIEVE 会怎样？** `unsafe_read` 返回 `KEY_NOT_READABLE`（对象还在但没锁）或 `KEY_NOT_EXIST`（已被淘汰）→ 传输模块打 “Some keys not found during retrieve!” 并把本次 RETRIEVE 标失败：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1333-1336`。文档称它会作为 `invalid_block_ids` 报给 vLLM（见下条引文的出处），即该请求回退为自己重算。
- 【文档】官方配方里专门写了这个坑：“Under deep queues a request can wait many minutes between its lookup and its retrieve.”并说默认 300/600 下读锁会在取回前过期（`docs/source/recipes/kimi_k3.rst:159-165`）。需要时调大 `--l1-read-ttl-seconds`。

**写锁同理**：staging 的写锁 600 秒；过期后 `finish_write` 返回 `KEY_IN_WRONG_STATE` 且**对象留在原地等淘汰回收**：`lmcache/v1/distributed/l1_manager/base.py:1193-1201`，【文档】`docs/design/v1/distributed/l1_manager/staging-and-write-tags.md:49-58`。写失败时则不等 TTL，由 `abort_write` 立刻丢弃，见 Q6。

### Q4. 淘汰线程怎么挑？被读锁锁住的为什么不会被挑中？

**循环**（`lmcache/v1/distributed/storage_controllers/eviction_controller.py:160-190`）：
1. `time.sleep(1)`，取 `(used, total)`；
2. `usage < 0.8` → 跳过；
3. 否则让策略出方案：`get_eviction_actions(0.2, key_eligible_filter=l1.is_key_evictable)`；
4. 对每个 action 调 `l1.delete(keys)`（目的地只有 DISCARD）：`lmcache/v1/distributed/storage_controllers/eviction_controller.py:192-198`。

默认值：触发线 0.8、淘汰比例 0.2：`lmcache/v1/distributed/config.py:491-495`；命令行默认同：`lmcache/v1/distributed/config.py:1073-1086`。

**LRU 怎么挑**（`lmcache/v1/distributed/eviction_policy/lru.py:160-199`）：
- `target_count = int(len(_order) × ratio)`，至少 1：`lmcache/v1/distributed/eviction_policy/lru.py:168-172`。**按名单里的 key 个数算，不是按字节**。
- 从最久没用的一端扫，`key_eligible_filter` 为假就 `continue` 跳过，凑够 `target_count` 个就停：`lmcache/v1/distributed/eviction_policy/lru.py:181-188`。

**两道门挡住读锁中的 key**：
- 第一道（挑选时）：`is_key_evictable` ＝ 有常驻对象且 `read_lock` 未锁，**或**有 staging 且其写锁已过期：`lmcache/v1/distributed/l1_manager/base.py:983-992`。被读锁锁住 → 假 → 被跳过。【测试】`tests/v1/distributed/test_l1_manager.py:1783-1793`（读锁中不可淘汰）、`tests/v1/distributed/test_l1_manager.py:1769-1777`（写锁中不可淘汰）、`tests/v1/distributed/test_lru_eviction_policy.py:268`、`tests/v1/distributed/test_lru_eviction_policy.py:360`（过滤器跳过被锁 key）。
- 第二道（删除时）：`delete` 在全局锁内**再检查**，被锁则返回 `KEY_IS_LOCKED` 而不删：`lmcache/v1/distributed/l1_manager/base.py:804-807`。
- 为什么有两道？【注释】“This method does NOT acquire the global L1Manager lock.”“``delete()`` will check again and safely reject a key that became locked between the check and the actual deletion.”（`lmcache/v1/distributed/l1_manager/base.py:972-974`）。第一道为了快（扫几百个 key 不抢全局锁），第二道为了对。
- 【提交】`cb8b1c04` 标题「[MP][Optimize] Skip locked keys during LRU eviction to improve eviction efficiency (#2978)」：这个「先过滤再取」是后加的优化；提交说明只有标题。

**例子 1：满了之后的名单**（见 §2，设：L1 只有 456 个同尺寸 key）→ 目标 91 个，从最久没用开始，凑 91 个没被锁的删掉；`execute_eviction_action` 不看 `delete` 的返回值，漏网的下一秒再来。
**例子 2：锁怎么救命**（**设**，缩小到名单只有 10 个 key，比例 0.2）：目标 2 个。名单头 x1 被一个读者锁着 → 跳过；取 x2、x3。x1 没被动，且**不占名额**。

**h0/h1/h2 在名单里的位置**（为什么「后面的 chunk 先被淘汰」）：
- 【注释】“for the request, the later keys should be evicted first.” 与 “if we first evict key1, due to prefix match, key2 and key3 will not be hit.”（`lmcache/v1/distributed/eviction_policy/lru.py:83-85`）。
- 做法：`on_keys_created / on_keys_touched` 都对 `reversed(keys)` 逐个移到末尾：`lmcache/v1/distributed/eviction_policy/lru.py:86-92`、`lmcache/v1/distributed/eviction_policy/lru.py:107-110`。结果名单尾部是 …h2, h1, h0，h0 最「新」，h2 最先被扫到。
- 【提交】`bdf4c9a9` 标题「[MP][optimize] optimize evict in lru policy (#2740)」，其改动正是加了 `reversed` 与上面的注释；说明只有标题。
- 刷新时机：读锁释放**不刷新**LRU——【注释】“No-op: releasing a read lock does not mean the key was useful.”（`lmcache/v1/distributed/eviction.py:196`）；真正的刷新来自预取结束时 `touch_keys`（`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1730-1733`）和请求结束时 END_SESSION 的统一 touch（`lmcache/v1/multiprocess/modules/lookup.py:640-646`）。【文档】“LRU is decoupled from locking.”（`docs/design/v1/distributed/storage_controllers/prefetch_controller.md:53-55`）。

**staging 对象也在名单里**：`on_l1_keys_reserved_write` 就把 key 放进 LRU；【注释】“Track keys from the moment they are reserved, so an abandoned reservation (expired write lock) is evicted like any unlocked key.”（`lmcache/v1/distributed/eviction.py:201-203`）。写锁过期的 staging 由 `delete → _reclaim_staging` 回收：`lmcache/v1/distributed/l1_manager/base.py:793-802`、`lmcache/v1/distributed/l1_manager/base.py:1206-1238`。【提交】`c1a38136`「fix(mp): separate ARC reservation from admission」是给 ARC 策略补上「预留」与「转正」的区分，LRU 沿用默认（`lmcache/v1/distributed/eviction.py:103-112`）。

### Q5. 内存怎么分？满了 `reserve_write` 返回 OUT_OF_MEMORY 之后呢？

**分配链**：`L1Manager.reserve_write` → `L1MemoryManager.allocate` → `LazyMemoryAllocator.batched_allocate` → `TensorMemoryAllocator.batched_allocate` → `AddressManager.batched_allocate`（空闲块表按起始地址排序，从头逐块贪心地切，一批可以跨几个空闲块，`lmcache/v1/memory_management.py:1102`）：`lmcache/v1/distributed/memory_manager/l1_memory_manager.py:136-141`、`lmcache/v1/memory_allocators/lazy_memory_allocator.py:221-253`、`lmcache/v1/memory_allocators/tensor_memory_allocator.py:119-152`、`lmcache/v1/memory_management.py:1265-1286`。

- **整批要么都成要么都不成**：不够就抛 `RuntimeError`，上层转成 `None` → `OUT_OF_MEMORY`。【注释】“Not enough memory; free list is untouched, no rollback needed”（`lmcache/v1/memory_management.py:1289`）；【文档】“Allocation remains atomic within each batch.”（`docs/source/mp/multi_l1.rst:123`）。
- 落到例子：A 的 3 个对象是**一批**申请的。如果 L1 只剩 2 块空位，**3 个都 OOM**，而不是存 2 个：`lmcache/v1/distributed/l1_manager/base.py:559-561`。
- 懒增长：后台线程每 64 MB 一块 pin，每 1 GB `sbrk` 提交一次：`lmcache/v1/memory_allocators/lazy_memory_allocator.py:87-89`、`lmcache/v1/memory_allocators/lazy_memory_allocator.py:371-379`。**用量比分母是「当前已 pin 的堆」**：【注释】“is what the allocator currently backs (the grown heap on the lazy tier).”（`lmcache/v1/distributed/l1_manager/base.py:1086-1087`）；见 `lmcache/v1/distributed/memory_manager/l1_memory_manager.py:186-190`。所以大 L1 在扩容期间，80% 是相对**当时**的堆算的【推断，依据上面两处】。

**OOM 之后发生什么**（逐层）：
1. L1Manager：这一批所有 key 返回 `(OUT_OF_MEMORY, None)`，已分配的一部分退回：`lmcache/v1/distributed/l1_manager/base.py:559-565`。
2. StorageManager：返回字典里**没有**这些 key；并发布 `L1_ALLOCATION_FAILED` 事件（计数器 `lmcache_mp.l1_allocation_failure`）：`lmcache/v1/distributed/storage_manager.py:328-348`、`lmcache/v1/mp_observability/subscribers/metrics/l1_failures.py:40-41`。
3. 传输模块：没拿到对象的 chunk 就是 `None`，D2H 拷贝跳过；`all_dict` 里没有它，所以**不进完成掩码**，也不抛异常：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:970-975`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1070-1074`。vLLM 请求本身不受影响。
4. **没有「立刻淘汰再重试」**：代码里 OOM 只触发事件，没有任何地方因此唤醒淘汰线程；腾空间完全靠淘汰线程每秒的轮询（`lmcache/v1/distributed/storage_controllers/eviction_controller.py:165`）。【推断】依据：全仓对 `OUT_OF_MEMORY` 与 `L1_ALLOCATION_FAILED` 的使用只有上面几处与 P2P/预取侧各自的处理（`lmcache/v1/multiprocess/modules/p2p_controller.py:465`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:344`）。
5. **这些 chunk 这次就丢了**：调度器在**下发工单时**就把 `num_stored_tokens` 加上了（`lmcache/integration/vllm/lmcache_mp_metadata.py:456`），不会因 OOM 重发【推断】；要等以后有请求带着同样前缀再算并存一次。
6. 多 L1 时也不会换一个 L1 重试：【文档】“nothing is retried on another manager”（`docs/source/mp/multi_l1.rst:121-123`）。

### Q6. 两个请求同时 STORE 同一个 key 会怎样？（A 与 A2）

结论：**先到的存，后到的这些 chunk 被安静地跳过，不报错、不覆盖、不重复占内存。**

1. 两边都经 `StorageManager.reserve_write`，**共用同一个 tag** `"storage_manager"`：`lmcache/v1/distributed/storage_manager.py:99-101`、`lmcache/v1/distributed/storage_manager.py:323`。
2. A 先预留 → `_staging[h0]["storage_manager"]` 存在、写锁有效。
3. A2 再预留 h0：key 不在 `_objects`，但 `_get_staging(key, tag)` 命中且 `write_lock.is_locked()` → `KEY_NOT_WRITABLE`：`lmcache/v1/distributed/l1_manager/base.py:531-535`。3 个 key 都如此 → `need_to_allocate` 为空 → **直接返回**，不分配、不通知：`lmcache/v1/distributed/l1_manager/base.py:552-553`。
4. A2 的传输模块拿到空字典：D2H 被跳过（`transfer_object_group` 仍被调用，但每项都是 `None`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:970-972`）、`stored_count=0`、没有回调、没有 `abort_write`（`l1_owners` 为空）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1005-1018`。完成掩码全假，但 RPC 本身不报错（`返回的 bool 只表示没有致命错误`，见传输模块底稿）。
5. 若 A 已经转正才来 A2：`key in self._objects` → 同样 `KEY_NOT_WRITABLE`：`lmcache/v1/distributed/l1_manager/base.py:527-529`。

**为什么共用一个 tag？**
- 【注释】“Sharing one tag makes concurrent stores of the same key exclude each other.”（`lmcache/v1/distributed/storage_manager.py:99-100`）。
- 【文档】“which keeps the de-duplication engine stores rely on”（`docs/design/v1/distributed/l1_manager/staging-and-write-tags.md:32`）。
- 对照：**不同 tag 的写者互不阻塞**（预取 `prefetch:<request_id>`、serde 包装各有自己的 tag），各拿一块 staging，先转正的赢，后到的被**丢弃但返回 SUCCESS**（数据反正已在 L1）：`lmcache/v1/distributed/l1_manager/base.py:640-648`；【测试】`tests/v1/distributed/test_l1_manager.py:1933-1942`（同 tag 第二次 `KEY_NOT_WRITABLE`）、`tests/v1/distributed/test_l1_manager.py:1946-1958`（不同 tag 各自成功）。
- `allow_resident=True` 是给预取用的例外：key 在查找与预留之间被别人加载进来了，也照样 staging，转正时发现已有常驻就丢掉自己、锁住常驻那份。【提交】`95737da9`：“The cost is one redundant L2 load of the landed key,”（commit `95737da9`）；它同时写明 “Other writers keep the default, so engine stores still de-duplicate on KEY_NOT_WRITABLE.”（commit `95737da9`）。

**一个要小心的窗口**：A 崩了、预留悬着。600 秒内，A2 都会拿到 `KEY_NOT_WRITABLE`（该 chunk 没人存）。600 秒后同 tag 的新预留会走「交接」分支，**直接拿走同一块 buffer 并重新上写锁**：【注释】“The previous reservation expired: hand the buffer over.”（`lmcache/v1/distributed/l1_manager/base.py:536`）；`lmcache/v1/distributed/l1_manager/base.py:536-546`。若旧写者其实没死、只是很慢，它的 `finish_write` 会因为锁又有效而成功转正这块 buffer，而新写者还在写——两边写的是同一个 key（同一份内容），所以多半无害，但**没有测试或注释专门讲这一点**【推断】。

**写失败怎么收场**（和上面是一对）：【注释】`abort_write` 的 docstring：“A private L1 frees them before write-TTL expiry;”（`lmcache/v1/distributed/storage_manager.py:393-399`）。它调 `finish_write_and_delete`，在同一个临界区里解锁并丢弃：【注释】“Unlock and discard happen in one critical section, so no other component can observe or lock the object in between.”（`lmcache/v1/distributed/l1_manager/base.py:826-830`）；【提交】`4d5423a2`「[Fix][MP] Add atomic finish_write_and_delete (#5068)」。

---

## 4. 源码页候选（6 段，各带批注与数据定义）

### 页 1：`reserve_write` 的核心循环
`lmcache/v1/distributed/l1_manager/base.py:522-584`（62 行）。
- **做什么**：逐 key 决定「拒绝 / 复用旧 buffer / 要分配」，再一批分配，包成带两把锁的对象放进 `_staging`。
- **逐处批注**：
  - 527：`key in self._objects and not allow_resident` → 常驻对象不可写（去重）。
  - 531-535：同 tag 且写锁有效 → 拒绝（同 tag 互斥）。
  - 536-546：写锁已过期 → 把旧 buffer 交给新写者（见 Q6 的窗口）。
  - 552-553：没东西要分配就**提前返回**，不发监听通知。
  - 555-561：**一次**申请 `len(need_to_allocate)` 块，失败全部标 OOM（整批原子）。
  - 574-578：新建两把 `TTLLock`（写 `_write_ttl_seconds`、读 `_read_ttl_seconds`），**只有写锁被 lock**；读锁此时计数 0。
  - 579：`_put_staging` 同时登记 `staging_charge`（见下方提交）。
- **数据定义**：`L1ObjectState` `lmcache/v1/distributed/l1_manager/base.py:79-97`；两张表 `lmcache/v1/distributed/l1_manager/base.py:215-221`；`TTLLock` 三个字段 `csrc/lmcache_native/ttl_lock.h:79-86`；`MemoryLayoutDesc`（shapes/dtypes）`lmcache/v1/common/api.py:11-18`；`ObjectKeyIdentity` `lmcache/v1/distributed/api.py:226-234`。
- **staging 字节记账的坑**：【提交】`c01c30ea`「[Bugfix][MP] L1 staging bytes: subtract the charge recorded at reserve (#6152)」：“Record the charge on the staging entry at put and subtract exactly that”（commit `c01c30ea`）。所以 `L1ObjectState` 里才有 `staging_charge` 这个字段（`lmcache/v1/distributed/l1_manager/base.py:97`）。

### 页 2：转正与丢弃
`lmcache/v1/distributed/l1_manager/base.py:635-658` ＋ `lmcache/v1/distributed/l1_manager/base.py:1189-1204`（合计约 35 行）。
- **做什么**：把 staging 变成常驻；若已有常驻则丢弃自己。
- **批注**：
  - 1193-1201：**先**查写锁是否仍有效（过期 → `KEY_IN_WRONG_STATE`，对象留在 staging 等回收），**再**解锁并弹出——顺序反了会把已被别人接手的 buffer 转正。【推断】
  - 640-648：已有常驻 → 丢弃自己的 buffer，**仍返回 SUCCESS**。【注释】“the data is in L1 either way”（`lmcache/v1/distributed/l1_manager/base.py:627-628`）。
  - 650-652：`is_temporary` 的对象不通知监听者，因此不会被推给 L2（`lmcache/v1/distributed/l1_manager/base.py:608-610`）。
- **数据定义**：监听接口 `lmcache/v1/distributed/internal_api.py:68-140`（`on_l1_keys_write_finished` 等）。

### 页 3：读锁的上与放 ＋ 锁本身
`lmcache/v1/distributed/l1_manager/base.py:325-337`（`reserve_read` 循环）、`lmcache/v1/distributed/l1_manager/base.py:426-446`（`finish_read` 主体）、`csrc/lmcache_native/ttl_lock.cpp:13-48`、`csrc/lmcache_native/ttl_lock.cpp:68-75`。
- **批注**：
  - `reserve_read`：`entry is None`（包括「只有 staging」）→ `KEY_NOT_EXIST`；否则 `lock()` ×`total`。
  - `finish_read` 426-433：**先** `is_locked()`；过期的锁不减计数、返回 `KEY_IN_WRONG_STATE`（这是 Q3 的「晚到的放锁」）。
  - 440-443：`is_temporary` 且计数归零 → 当场删除并释放内存（临时对象读完即焚）。
  - `csrc/lmcache_native/ttl_lock.cpp:23-33`：过期分支用 CAS 改过期时间再把计数设 1；25-34 是一个重试循环。
  - `csrc/lmcache_native/ttl_lock.cpp:38-43`：未过期分支 CAS 加 1，**再**单独 store 新过期时间（两步不是同一个原子操作）。【推断：这意味着极端并发下过期时间可能被较早的写覆盖，影响有限；没有测试专门覆盖。】
- **数据定义**：`TTLLock` 字段 `csrc/lmcache_native/ttl_lock.h:79-86`。

### 页 4：淘汰一圈
`lmcache/v1/distributed/storage_controllers/eviction_controller.py:160-190` ＋ `lmcache/v1/distributed/eviction_policy/lru.py:160-199` ＋ `lmcache/v1/distributed/l1_manager/base.py:983-992`（共约 85 行，可只取三段关键）。
- **批注**：eviction_controller 165 的 `sleep(1)`（轮询周期，写死）；170 的 `usage < watermark`；184-187 把 `is_key_evictable` 当过滤器传进策略；lru 168-172 的「至少 1 个」；181-188 的 `continue`（被锁 key **不占名额**）；base 983-985「常驻且读锁无效」、986-992「staging 且写锁已过期」，全程**不拿全局锁**（`lmcache/v1/distributed/l1_manager/base.py:971-974`）。
- **数据定义**：`EvictionConfig` `lmcache/v1/distributed/config.py:482-495`；LRU 的 `_order` `lmcache/v1/distributed/eviction_policy/lru.py:53`；`EvictionAction` 在 `lmcache/v1/distributed/internal_api.py`（keys + destination）。

### 页 5：预取控制器的 L1 小路径
`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:714-724`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1263-1294`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1313-1336`、`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1716-1733`。
- **批注**：
  - 718-723：同步三连 `_lock_l1_keys → _plan_load → _finish_request`。
  - 1260-1261（docstring）：【注释】“Locks are taken regardless of the lock mode; under ``NO_LOCK`` they are released when the request finishes.”（`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1260-1261`）
  - 1291-1294：`completed` 标志 + `finally` 里 `_release_all_locks`——中途异常也不留锁。
  - 1323-1328：`l1_dropped = 已锁 − 计划`，逐行 `finish_read(keys, row.num_kv_readers)`——**放的数量等于上的数量**。
  - 1722-1733：`NO_LOCK` 才释放；无论哪种模式都 `touch_keys`。
- **数据定义**：`PrefetchTaskSpec`（`key_groups / fetching_policy / lock_mode`）`lmcache/v1/distributed/api.py:717-746`；`GroupedObjectKeys.num_kv_readers` `lmcache/v1/distributed/api.py:701`；`PrefetchKeyState` `lmcache/v1/distributed/storage_controllers/prefetch_controller.py:434-447`；`PrefetchResult`（`hit_cells / l1_owners`）`lmcache/v1/distributed/api.py:801-829`。

### 页 6：门面的 `reserve_write` 与分配失败
`lmcache/v1/distributed/storage_manager.py:314-348` ＋ `lmcache/v1/distributed/memory_manager/l1_memory_manager.py:136-141` ＋ `lmcache/v1/memory_management.py:1288-1300`。
- **批注**：314-315 写策略 + 校验「每个 key 恰好放一次」；323 固定 tag；328 结果字典**只含成功的**（失败的靠「缺席」表示）；341-348 把 OOM 单独统计成事件；`lmcache/v1/distributed/memory_manager/l1_memory_manager.py:139-140`「分配器返回 None ⇒ OUT_OF_MEMORY」；`lmcache/v1/memory_management.py:1288-1300`「整批失败不动空闲链」。
- **数据定义**：`_L1_WRITE_TAG` `lmcache/v1/distributed/storage_manager.py:99-101`；`DefaultWritePolicy` `lmcache/v1/distributed/storage_controllers/write_policy.py:99-110`；`L1Error` 枚举 `lmcache/v1/distributed/error.py:26-48`。

---

## 5. 不变量

1. **读者只见完整对象**：staging 在对象表之外，转正前 `reserve_read` 报不存在：`lmcache/v1/distributed/l1_manager/base.py:318-320`。
2. **被读锁锁着（且没过期）的对象不会被淘汰**：两道门（Q4）。**例外**是 TTL 过期，此时系统认为锁不存在。
3. **写锁有效的 staging 不会被淘汰/回收**；`delete` 遇到活的 staging 返回 `KEY_IS_LOCKED`：`lmcache/v1/distributed/l1_manager/base.py:793-797`。
4. **一个 key 同时至多一个 tag 的 staging 持有者互斥；不同 tag 可并存，转正只有一个赢**（Q6）。
5. **对多个 key 的批量操作是原子的**（`BaseL1Manager` 类注释 “For every operation on list of keys, the operation is atomic”，`lmcache/v1/distributed/l1_manager/base.py:191`），靠全局 `_lock` 实现。注意：`reserve_read` 对每个 key 是各自判断、**不是全有或全无**（逐个 `continue`，`lmcache/v1/distributed/l1_manager/base.py:325-337`），原子的只是「别的线程看不到中间态」。

---

## 6. 故障模型（只列与 L1 框相关的）

| 坏在哪 | 收场 | 锚点 |
|---|---|---|
| 客户端崩了没放读锁 | 300 秒后锁失效，可被淘汰；计数残留被下次 `lock()` 重置 | `csrc/lmcache_native/ttl_lock.cpp:25-33` |
| 取回晚于读锁过期 | `unsafe_read` 失败 → RETRIEVE 失败 → vLLM 重算；可调大 TTL | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1333-1336`，`docs/source/recipes/kimi_k3.rst:159-165` |
| 写者崩了/拷贝失败 | 失败时 `abort_write` 立即丢弃；崩溃时 staging 悬 600 秒，之后被淘汰回收或被同 tag 新写者接手 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1012-1018`，`lmcache/v1/distributed/l1_manager/base.py:536-546` |
| L1 满 | 整批 OOM，该批 chunk 不存，等淘汰线程下一次轮询 | Q5 |
| 预取中途异常 | `_abort_request` 释放它记录的所有锁并发布「全未命中」 | `lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1774-1787` |
| 一次读锁放多了/放晚了 | 放晚（已过期）→ `KEY_IN_WRONG_STATE` + 警告；对象不存在 → `KEY_NOT_EXIST` | `lmcache/v1/distributed/l1_manager/base.py:416-433` |

---

## 7. 没查到为什么（如实）

1. **读 TTL 300 秒、写 TTL 600 秒为什么是这两个数**：配置里只有「Default is 600s (10 minutes)」之类的事实陈述，没有选数理由：`lmcache/v1/distributed/config.py:429-433`。
2. **淘汰循环为什么固定 `sleep(1)`、OOM 为什么不立即触发淘汰**：没有注释、提交说明或文档解释（`lmcache/v1/distributed/storage_controllers/eviction_controller.py:165`）。上面「无即时淘汰」是读代码的结论，不是设计意图。
3. **水位 0.8 / 比例 0.2 为什么是默认值**：没找到。
4. **`MAX_READ_LOCK_COUNT = 128` 为什么是 128**：注释只讲「防止长时间占全局锁」，没讲 128 的来历（`lmcache/v1/distributed/l1_manager/base.py:39-41`）。
5. **TTLLock 为什么用 C++ 原子，而 `L1Manager` 外面又包一把全局锁**：提交 `f3cf3695` 只有标题「[MP][2/N] Add native implementation for ttl lock for mp storage manager (#2505)」与「microbenchmark」字样；没写为何不用纯 Python。
6. **文档与代码不一致**（不是「为什么」，是需要读者留意）：
   - 文档说淘汰“until usage drops below the target”（`docs/source/mp/index.rst:421-422`），代码是**每轮只按比例删一批、下一轮再看**（`lmcache/v1/distributed/storage_controllers/eviction_controller.py:184-190`）。
   - 配置注释说比例是“The fraction of *allocated* memory to evict”（`lmcache/v1/distributed/config.py:495`），LRU 实际按**名单里 key 的个数**算（`lmcache/v1/distributed/eviction_policy/lru.py:168`）；对同尺寸对象二者等价，对不同尺寸（混合模型多 object group）可能不等价【推断】。
7. **同 tag 交接后旧写者仍然 `finish_write` 的语义**：没找到说明或专门测试（Q6）。
8. **`touch_keys` 为什么不拿全局锁、只通知监听者**：没找到说明（`lmcache/v1/distributed/l1_manager/base.py:867-881`）；LRU 自带锁（`lmcache/v1/distributed/eviction_policy/lru.py:50`），可能是原因，属【推断】。

---

## 8. 可改进处【观点】

- 【观点】OOM 时顺手「踢」一下淘汰线程（或把轮询周期做成可配），否则写入突发时水位 0.8 的缓冲可能一秒内被冲穿。
- 【观点】读锁是匿名计数，崩溃的客户端只能等整整 300 秒；若加一个「持有者→key 集合」的登记，会话结束就能精确清理，不用等 TTL。
- 【观点】把配置说明里「fraction of memory」与 LRU 的「按 key 个数」对齐，或让策略接受字节预算。
- 【观点】同 tag 交接时给旧预留一个「代号」，让旧写者的 `finish_write` 能被识别并拒绝。

---

## 9. 给讲解员的读者层提示

- **最困惑的三处**：① 状态不是字段，是「在哪张表 + 锁是否有效」；② 读锁是匿名计数，且 TTL 在**每次上锁时被刷新**、对所有持有者共享；③ LOOKUP 上锁与 RETRIEVE 释放隔着好几个调度步、在不同线程上。
- **最好对照着看的两件事**：「staging（写）」与「read_locked（读）」都是 **TTL 锁**，只是挂在不同对象上、不同表里；「挑选时过滤」与「删除时复查」是同一件事的快慢两道门。
- **小机器图的素材**：部件见 §1.1；连线可取：传输模块→`reserve_write / finish_write / unsafe_read / finish_read`；查找模块→`submit_prefetch_task / query_prefetch_status`；L1Manager→（监听回调）→LRU；淘汰线程→`is_key_evictable` / `delete`→L1Manager；L1Manager→分配器。
- **推演例子的时间线**：A 写入（Q1）→ B LOOKUP 上读锁（Q2）→ 淘汰线程同一秒到来被跳过（Q4 例子 2）→ B RETRIEVE 放锁 → B 若崩溃则 300 秒后过期（Q3 情况 3）→ A2 并发写同 key 被跳过（Q6）。
- **与上一趟的小出入**：上一趟顶层卡片写「淘汰约 20%（按个数）」，是对的；上一趟 §4.5 的 L1 状态图与本稿一致，只是本稿补上了「staging 被遗弃」「同 key 不同 tag」。`ObjectKey` 现在包了一层 `identity`（`lmcache/v1/distributed/api.py:226-262`），字段内容与上一趟一致。
