# L1 存储：对象状态机、读写锁、淘汰（「L1 存储」框的下一层底稿）

> 研究对象：LMCache 仓库 HEAD = `b27f6bd9e2`。接在顶层底稿（`manuscript.md`）§2.2 ⑨、§4.5、§5.7、§7 之后；传输模块见 `manuscript-xfer.md`。
> 锚点 = `文件:行号`，都在 HEAD 上成立。引文用 “ ” 逐字照抄（英文不翻译）；中文强调用「 」。
> 来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。「设」= 为讲解补设的值。
> 路径缩写：`L1B` = `lmcache/v1/distributed/l1_manager/base.py`；`SM` = `lmcache/v1/distributed/storage_manager.py`；`PC` = `lmcache/v1/distributed/storage_controllers/prefetch_controller.py`；`EC` = `lmcache/v1/distributed/storage_controllers/eviction_controller.py`。

---

## 0. 先看这个（一屏结论）

L1 是「服务进程里的一块 CPU 固定内存 + 一张对象表」。对象表里每个对象带**两把 `TTLLock`**：一把写锁、一把读锁。所有「存下来、锁住、不被挤掉」都是这两把锁加一条规则：

- **写**：先 `reserve_write` 申请一块**读者看不见**的暂存对象（staging），GPU 拷完后 `finish_write` 才把它挪进「常驻表」，从此可读。
- **读**：先 `reserve_read` 给对象**加读锁（计数 +1）**，之后无锁地 `unsafe_read`，用完 `finish_read`（计数 −1）。
- **淘汰**：一个后台线程每秒看一次内存占用，超过 80% 就按 LRU 次序挑最久没用的，**跳过**还被锁着的，一次删掉约 20%（按个数）的对象。
- **锁是租约，不是互斥量**：计数 + 到期时间。到期（读锁 300 s / 写锁 600 s）就整把作废，防止客户端崩了把对象永远锁死。

三个类的分工：`StorageManager` 是门面（把外面的调用翻成对 L1 / 控制器的调用）；`L1Manager`（`BaseL1Manager`）是唯一管对象表和锁的地方；`PrefetchController` 是「批量查 + 上读锁 + 按前缀裁剪」的流程（没有 L2 时它就是在调用线程里同步跑的一个函数）。

---

## 1. 这个框里有谁

### 1.1 部件（建议 6 个）

| # | 部件 | 管什么 | 状态放在哪 | 入口锚点 |
|---|---|---|---|---|
| ① | `StorageManager` | 门面：组装下面所有东西；把 `reserve_write` / `finish_write` / `submit_prefetch_task` / `unsafe_read` / `finish_read_prefetched` 转给 L1 与控制器；写入用固定 tag `"storage_manager"` | 不存对象；持有 L1 列表、各控制器 | `SM:148-271`，`SM:99-101` |
| ② | `L1Manager`（`DramL1Manager` 继承 `BaseL1Manager`） | **对象状态机**：常驻表、暂存表、读写锁、监听者通知 | `_objects`（常驻）`L1B:216`；`_staging`（暂存，按 key→tag）`L1B:219`；`_staging_bytes` `L1B:221`；一把全局互斥锁 `L1B:213` | `L1B:142-198`，`lmcache/v1/distributed/l1_manager/dram.py:16-30` |
| ③ | `TTLLock` ×2 / 对象 | 计数 + 到期时间的租约；C++ 原子实现 | `L1ObjectState.write_lock` / `read_lock` `L1B:88-91` | `csrc/lmcache_native/ttl_lock.cpp:13-75` |
| ④ | `L1MemoryManager` → `LazyMemoryAllocator` → `AddressManager` | 给对象切 CPU 固定内存；放不下就返回 OOM | 空闲块列表（有序） | `lmcache/v1/distributed/memory_manager/l1_memory_manager.py:116-141`，`lmcache/v1/memory_management.py:1228-1299` |
| ⑤ | `PrefetchController` | `submit_prefetch_request`：对 L1 逐 key `reserve_read` → 按策略裁成前缀 → 释放多余的锁 → 发布结果 | 每个请求的 `PrefetchKeyState`（锁了哪些格子） | `PC:693-729`，`PC:433-447` |
| ⑥ | `L1EvictionController` + `LRUEvictionPolicy` + 监听桥 `L1EvictionPolicy` | 后台线程；LRU 次序表；把 L1 事件翻成「新建 / 触碰 / 删除」 | LRU 的 `OrderedDict` `lmcache/v1/distributed/eviction_policy/lru.py:53` | `EC:88-198`，`lmcache/v1/distributed/eviction.py:175-215` |

### 1.2 线（谁把什么交给谁）

| 线 | 从→到 | 交什么 | 锚点 |
|---|---|---|---|
| W1 | 传输模块 → ① → ② | `reserve_write(keys, layout)` → staging 内存对象 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:960-962`，`SM:314-325` |
| W2 | 流回调（排空线程）→ ① → ② | `finish_write(keys, l1_owners)` → 暂存转常驻 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:331-339`，`SM:375-378` |
| W3 | 查找模块 → ① → ⑤ → ② | `PrefetchTaskSpec` → `reserve_read` → 命中位图 + 读锁 | `lmcache/v1/multiprocess/modules/lookup.py:382-393`，`SM:584`，`PC:1273-1279` |
| W4 | 传输模块 → ① → ② | `unsafe_read`（不加锁地取指针）；拷完后 `finish_read` 放锁 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1330-1332`，`SM:710-725`，`SM:1424-1440` |
| W5 | ② → ⑥ | 监听回调（保留 / 写完 / 触碰 / 删除）；② 在**持有自己的锁时**同步调用 | `L1B:583-584`，`L1B:656-658`，`lmcache/v1/distributed/eviction.py:190-215` |
| W6 | ⑥ → ② | `is_key_evictable` 当过滤器；`delete(keys)` 真删 | `EC:184-189`，`L1B:969-992`，`L1B:757-818` |
| W7 | ② → ④ | `allocate(layout, n)` / `free(objs)` | `L1B:555-556`，`L1B:654` |

---

## 2. 例子（沿用上一趟，补设标「设」）

沿用顶层 §4.1 与传输稿 §2：L1 20 GiB（`--l1-size-gb 20`，LRU）；一个对象 = 一个 chunk = **36 MiB**；A 存 `h0 h1 h2`；B 命中 `h0 h1 h2`，`h3′` 不存在；TP=1 → 读者数 1。

本稿补设并推算：
- 槽数：20 GiB = 20480 MiB，20480 / 36 ≈ 568.9 → **568 个对象**（顶层 §4.1 已有）。
- 触发线：用量 ≥ 0.8 即 ≥ 16384 MiB；455 个对象 = 16380 MiB（0.7998，不触发），**456 个 = 16416 MiB（0.8016，触发）**（设：此时池里恰好 456 个对象，全是普通 ready 对象）。
- 一次淘汰个数：`int(456 × 0.2) = 91`（`lmcache/v1/distributed/eviction_policy/lru.py:168`），淘汰后剩 365 个 = 13140 MiB ≈ 64%。
- **A2**（设）：与 A 前 768 token 相同的另一个请求，在 A 的 STORE 拷贝还没完成时发 STORE。
- **C**（设）：与 B 同前缀的另一个请求，在 B 持锁期间 LOOKUP。
- **B 崩溃**（设）：B 的 vLLM 在 LOOKUP 之后（锁已加）整个挂掉，时间点记 t0。
- **B 缺口**（设）：B 到来前 `h1` 已被淘汰，`h0`、`h2` 还在。

---

## 3. 逐步推演（按读者会问的顺序）

### 3.1 一个对象怎么存下来：reserve_write → finish_write

**A 的 STORE 到达 ①**（传输稿 S6–S11 讲了谁在什么线程上调它，这里只看进了 L1 之后）：

1. `StorageManager.reserve_write` 让写入策略挑 L1（默认只有一个）：`SM:314-315`；对每个 L1 调 `reserve_write(..., tag="storage_manager")`：`SM:317-325`。
2. `L1Manager.reserve_write` 对每个 key 先过三道门（`L1B:526-549`）：
   - 已在常驻表 → `KEY_NOT_WRITABLE`：`L1B:527-529`；
   - 同一个 tag 已经在暂存且写锁还活着 → `KEY_NOT_WRITABLE`：`L1B:531-535`；
   - 同一个 tag 的暂存对象写锁**已过期** → 不再分配，直接**把那块 buffer 交给新写者**并重新上写锁：`L1B:536-547`。
3. 过了门的 key 凑成一批，**一次**向内存管理器要：`L1B:555-556`。成功则给每个建 `L1ObjectState`（写锁 `TTLLock(600)`、读锁 `TTLLock(300)`），`write_lock.lock()` 后放进 `_staging[key][tag]`：`L1B:568-581`，`L1B:1151-1155`。
4. 通知监听者 `on_l1_keys_reserved_write`：`L1B:583-584`。LRU 从**这一刻**起就跟踪这些 key（见 3.5）。

此时状态（A 的 3 个对象）：`_staging = {h0:{"storage_manager":…}, h1:…, h2:…}`，`_staging_bytes = 108 MiB`，`_objects` 里**没有** h0–h2。别的请求 `reserve_read(h0)` 得到 `KEY_NOT_EXIST`（`L1B:318-320`；【测试】`tests/v1/distributed/test_l1_manager.py:200`）。

**拷贝完成后**（排空线程）→ `StorageManager.finish_write` → `L1Manager.finish_write`：对每个 key 调 `_take_staging`：找到暂存、写锁还活着就 `unlock()` 并从暂存表摘出（`L1B:1189-1204`）；然后 `self._objects[key] = entry`（`L1B:649`）；非临时对象再通知监听者 `on_l1_keys_write_finished`（`L1B:650-658`）。到这里 h0–h2 成为 ready：常驻、写锁已放、读锁计数 0。

**为什么暂存要单独一张表，而不是在常驻表里加个「写入中」状态？**
- 【文档】改成现在这样之前，`reserve_write` 直接在常驻表里建写锁定的对象，别人立刻看得到：`docs/design/v1/distributed/l1_manager/staging-and-write-tags.md:12-14` 说 “the second reservation failed, the prefetch controller abandoned its whole L2 load (all-or-nothing) and reported a non-hit for a chunk that was seconds away from being resident.” 也就是两个请求同时往 L1 里装同一个 L2 对象会互相撞。
- 【提交】引入暂存表的是 `2848bfec`（标题 “[3/N] Update L1Manager's reserve_write semantics to avoid write-write conflict (#5247)”，正文只有签名行；理由在同一提交加的设计文档里，即上一条）。
- 【注释】`L1B:151-154`：“Staging objects are kept apart from the resident objects: readers of the key do not see them, and writers with different tags may stage the same key at the same time.”
- 对读者的好处（顶层 §5.7 已写）：拷了一半的数据不会被读到【推断，依据：读路径只查 `_objects`，`L1B:326`】。

**「转正」时有一个容易看错的地方**：`finish_write` 发现该 key 已经常驻（别的写者先转正了），会**丢弃自己的暂存、返回 SUCCESS**。【注释】`L1B:626-628`：“the staging object is discarded, the resident object is kept and ``SUCCESS`` is still reported: the data is in L1 either way.” 所以 SUCCESS 不等于「我这份被留下了」。

**写锁到期**：暂存对象的写锁到期后，`finish_write*` 返回 `KEY_IN_WRONG_STATE`、对象原地留给淘汰回收（`L1B:1193-1201`）。主路上它主要是兜底：传输模块失败时会主动 `abort_write` 把暂存立刻丢掉（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1014-1018`，`SM:393-415`）。【提交】`831e0502` 说明了不丢的后果：“left its staging reservations to expire with the write TTL, which blocks every later writer of those keys.”（见传输稿 Q 部分，不重复）。

### 3.2 两个请求同时 STORE 同一个 key 会怎样

结论：**先到的拿到暂存，后到的被拒，什么都不拷、不占内存**。

以 A2（设）为例，A 的 STORE 已 `reserve_write(h0,h1,h2)` 但拷贝还没完：
1. A2 的 `reserve_write` 走到 `L1B:531`：`_get_staging(h0,"storage_manager")` 有对象，`write_lock.is_locked()` 为真 → `KEY_NOT_WRITABLE`：`L1B:533-535`。三个 key 都一样。
2. 三个 key 都没进 `need_to_allocate`，`L1B:552-553` 早退，**不分配**（【测试】`tests/v1/distributed/test_l1_manager.py:1980` `test_refused_keys_do_not_consume_memory`）。
3. `StorageManager.reserve_write` 把没拿到对象的 key 从结果里剔掉：`SM:328`。传输模块里它们变成 `None`，「跳过拷贝」；`all_dict` 为空，不挂 `finish_write`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:973-975`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1005-1011`。
4. A 的拷贝完成 → `finish_write` 转正。之后 A3 再来，撞的是 `L1B:527`（已常驻）。

**为什么 tag 是固定的 `"storage_manager"`？** 【注释】`SM:99-100`：“Sharing one tag makes concurrent stores of the same key exclude each other.” 反过来，不同 tag（例如预取控制器用每请求一个的 `prefetch:<id>`）允许同时暂存同一个 key：【文档】`staging-and-write-tags.md:31-33` “Writers sharing a tag exclude each other on a key (`KEY_NOT_WRITABLE`), which keeps the de-duplication engine stores rely on;”。【测试】`tests/v1/distributed/test_l1_manager.py:1933`（同 tag 两次 → NOT_WRITABLE）、`tests/v1/distributed/test_l1_manager.py:2454`（8 个不同 tag 的线程抢同一批 key，最后每个 key 只留一份）。

**另一种写者不会被「已常驻」拒绝**：预取控制器把 L2 数据装进 L1 时，对「它自己能读的那个 L1」传 `allow_resident=True`（`L1B:481`、`L1B:493-497`），即使该 key 刚被别人写完也照样暂存，转正时发现已常驻就丢掉自己的、改给常驻对象上读锁（`L1B:720-734`）。【提交】`95737da9`：“The cost is one redundant L2 load of the landed key”——宁可多搬一次，也不要把一个其实已在 L1 的块报成 miss。主路（无 L2）不走这条。

### 3.3 取回：submit_prefetch_task 在三者之间怎么走

主路（无 L2）下一次 LOOKUP 的 L1 内部过程，**全部在调用线程上同步完成**：

1. 查找模块拼 `PrefetchTaskSpec`（默认 `fetching_policy="prefix"`、`lock_mode=LOCK`，`lmcache/v1/distributed/api.py:743-744`），调 `StorageManager.submit_prefetch_task`：`lmcache/v1/multiprocess/modules/lookup.py:382-393`。
2. ① 只是转给 `PrefetchController.submit_prefetch_request` 然后包一个 `PrefetchHandle`：`SM:584`，`SM:596-602`。
3. ⑤ 发现 spec 不允许 L2，或根本没挂 L2 → **当前线程**依次做：`_lock_l1_keys` → `_plan_load(request, {})` → `_finish_request`：`PC:718-724`。
4. `_lock_l1_keys`：对每个允许的 L1 的每一行，**批量** `reserve_read(row.keys, row.num_kv_readers)`，成功的格子记进 `l1_locked_keys` 位图：`PC:1273-1289`。B 的例子：h0、h1、h2 各读锁计数 +1（TP=1），`h3′` 返回 `KEY_NOT_EXIST`。
5. `_plan_load`：策略按前缀折叠算出「该留的格子」，**不在计划里的 L1 读锁立刻 `finish_read` 放掉**：`PC:1314-1328`，`PC:1334-1335`。
   - 为什么需要这一步：第 4 步是**见到就锁**，不管是不是前缀。B 缺口（设：`h1` 已被淘汰）时，第 4 步锁住 `h0`、`h2`；折叠后前缀只有 `h0`，`h2` 的锁在这里释放（`PC:1325-1328`）。不放的话 `h2` 会被一个没人会用的锁钉住最多 300 s。
6. `_finish_request`：再按 L1 锁定结果规划一次（防止加载失败后留下孤儿锁），`LOCK` 模式下**命中格子的锁保留给调用方**；对命中的 key 调 `touch_keys`（刷新 LRU）；`_publish_result`：`PC:1719-1733`，`PC:1741-1755`，`PC:1765-1772`。结果里带 `l1_owners`（每个 key 被哪个 L1 持锁）：`PC:1748-1754`。
7. 随后 `query_prefetch_status` 取走结果（只能取一次）：`SM:604-641`，`PC:731-750`。

三者的分工一句话：**① 不碰对象，只翻译；⑤ 决定「锁哪些、留哪些」；② 只负责「锁 / 解锁 / 读 / 写」这些原语的原子性。** ⑤ 从不直接动 `_objects`，它只调 ② 的公共方法；它自己记「我锁了哪些格子」（`PrefetchKeyState`，`PC:433-447`），所以任何一步失败都能整表回滚（`_release_all_locks`，`PC:1793-1817`）。

**为什么「查」和「锁」是同一个动作？** 【文档】`docs/design/v1/distributed/storage_controllers/prefetch_controller.md:31-33`：“is lock-held from the moment it is discovered until the request finishes, so a concurrent eviction can shrink what gets discovered but never invalidate a reported hit.” 即：先问「在不在」再去锁，中间可能被淘汰；所以改成用「锁」本身当探测（`reserve_read` 成功即在且已锁）。同一文档还约定：加锁、放锁都**不**刷新 LRU 次序，recency 只由显式 `touch_keys` 管（`prefetch_controller.md:53-55`；监听桥里对应一条空实现，`lmcache/v1/distributed/eviction.py:195-198`）。

**取回阶段 L1 只做两件事**：`unsafe_read`——不加锁地取已被 `reserve_read` 锁住的对象，若没锁就返回 `KEY_NOT_READABLE`：`L1B:372-376`；然后拷贝完成时的回调 `finish_read`。【注释】`L1B:349-351`：“the caller need to make sure the `unsafe_read` is called between `reserve_read` and `finish_read` calls.” 名字叫 unsafe 是因为**安全性来自前一步 LOOKUP 的锁**，不是这个调用本身。

### 3.4 读锁计数和 TTL 怎么配合；客户端崩了怎样

**读锁 = 计数 + 租约**（`csrc/lmcache_native/ttl_lock.cpp`）：
- `lock()`：每次都把到期时间刷新为「现在 + TTL」；若发现**上一轮已过期**，则不累加而是把计数**重置为 1**：`ttl_lock.cpp:23-33`（过期分支）、`ttl_lock.cpp:36-45`（未过期：计数 +1）。
- `unlock()`：计数 −1，最低到 0：`ttl_lock.cpp:50-66`。
- `is_locked()`：**计数 > 0 且 没到期**才算锁着：`ttl_lock.cpp:68-75`。【注释】`lmcache/lmcache_native.pyi:97-99`：“If the TTL expires, the lock is considered unlocked regardless of the counter value.”
- 【测试】`tests/v1/lmcache_native/test_ttl_lock.py:126`（每次 `lock()` 刷新 TTL）、`tests/v1/lmcache_native/test_ttl_lock.py:148`（过期后再 `lock()` 计数回到 1）。

**为什么读锁是计数？** 一个对象可以同时被多个读者持有：同前缀的两个请求（C 与 B），或 TP>1 的 MLA 模型里多个 worker 各取一次。
- 【注释】`L1B:306-309`：“one per worker that consumes a read lock for the same key (e.g. MLA models with TP > 1).”
- 【提交】`038cd7c9` 标题 “fix(l1_manager): propagate extra_count through prefetch path to prevent premature eviction (#2725)”；`a973aa8e` 标题 “Fix to support mla multiple tp failed to read issue (#2697)”。两个提交都只有标题，具体失败路径没写；与 `L1B:306-309` 的说法一致，推测是「只上 1 把锁、多个 worker 放，先放的人把锁清零」【推断】。
- 上限 128：【注释】`L1B:39-40`：“to prevent a single call from holding the global lock for too long.”（批量加锁是在全局锁里一把一把加的，`L1B:331-335`，注释自己标了 TODO(perf)，并说明 “TTLLock is C++ std::atomic”。）
- 加锁与放锁的数量要对上：LOOKUP 一次 `reserve_read(…, num_kv_readers)`，放锁有两条路——RETRIEVE 完成时每个 worker 放 1 把（`SM:1437` 默认 `read_locks=1`），或取消 / 不取的 LOOKUP 一次放整批（`lmcache/v1/multiprocess/engine_context.py:58-84`）。

**例子：C 与 B 同持 h0（设）**：B 的 LOOKUP 后 h0 读锁计数 1；C 的 LOOKUP 后计数 2，到期时间刷新为 C 加锁时刻 +300 s；B 的 RETRIEVE 完成 → 1；C 的 → 0 → `is_locked()` 为假 → 回到 ready，可被淘汰。

**B 崩溃（设）**：
1. LOOKUP 在 t0 加锁（计数 1，到期 t0+300 s）。服务端没有人知道 B 死了：Session 有 600 s 过期清理（`lmcache/v1/multiprocess/session.py:227`，`lmcache/v1/multiprocess/session.py:294-311`），但我 grep 到的 `destroy_listeners` 注册点只有 blend 模块一处（`lmcache/v1/multiprocess/modules/blend/module.py:169`），**主路的 Session 过期不会放 L1 读锁**；`end_session` 也只做 `touch`（`lmcache/v1/multiprocess/modules/lookup.py:646`）。
2. t0 → t0+300 s：h0–h2 读锁计数 1、未到期 → `is_key_evictable` 为假（`L1B:984`）→ 淘汰挑不中。**这段时间对象白占内存**。
3. t0+300 s：`is_locked()` 变假（`ttl_lock.cpp:74`），**不需要任何人调 `unlock`**；对象从此像普通 ready 对象一样可被淘汰。计数其实还是 1，只是被到期时间屏蔽了。
4. 如果 B 其实没死而是慢（t0+400 s 才来 RETRIEVE）：`unsafe_read` 发现没锁 → `KEY_NOT_READABLE`（对象还在）或 `KEY_NOT_EXIST`（已被淘汰）→ 传输模块 “Some keys not found during retrieve!” 并报失败（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1333-1336`）→ vLLM 重算（顶层 §7）。随后 `finish_read` 也只会打 warning 并返回 `KEY_IN_WRONG_STATE` / `KEY_NOT_EXIST`（`L1B:416-433`）。
5. 一个潜在的坑【推断，依据 `ttl_lock.cpp:25-33` 与 `L1B:426-439`】：若 t0+300 s 之后别的读者 D 来 `lock()`，计数被**重置为 1**（含已过期的 B 的那一份被丢弃）；此时慢速的 B 再 `finish_read`，`is_locked()` 为真，`unlock()` 会把 D 的那把锁减掉。我没找到测试覆盖这个交错。

**默认值与出处**：读锁 300 s、写锁 600 s：`lmcache/v1/distributed/config.py:429-433`；CLI `--l1-read-ttl-seconds` / `--l1-write-ttl-seconds`：`lmcache/v1/distributed/config.py:1046-1057`。【文档】`docs/source/mp/index.rst:370-371` 说 “timeouts to prevent deadlocks from crashed clients.”（为什么是这两个数没查到，见 §8）。

### 3.5 淘汰线程怎么挑；被读锁锁住的为什么不会被挑中

**线程与节奏**：`L1EvictionController` 在 `StorageManager` 构造时为每个 L1 建一个并启动：`SM:160-166`。循环 `time.sleep(1)`（`EC:165`）→ 取 `used/total`（`EC:166-169`）→ 低于水位就跳过（`EC:170-177`）→ 否则向策略要「该删的 key」，逐个 `delete`（`EC:184-189`，`EC:192-198`）。**淘汰只由这个定时循环触发，没有「分配失败时同步淘汰」**（grep 确认 L1 一侧调用 `get_eviction_actions` 的只有 `EC:184`；另有 L2 控制器与 `mp_coordinator` 的调用，不属于本框）。

**水位与比例**：默认 0.8 / 0.2：`lmcache/v1/distributed/config.py:491-495`。注意两处措辞不一致：配置说明写 “The fraction of *allocated* memory to evict when triggered (0.0 to 1.0).”（`lmcache/v1/distributed/config.py:495`），但 LRU 实现是按**被跟踪的 key 个数**算：`int(len(self._order) × ratio)`，至少 1 个（`lru.py:168-175`；其文档字符串 `lru.py:139-140` 也写 “fraction of tracked keys”）。对象大小一致时两者相同；大小不一时不同。

**怎么挑（LRU）**：
1. 次序表 `OrderedDict`，**队首最久未用**（`lru.py:52-53`）。
2. 从队首往后扫，对每个 key 先问过滤器 `key_eligible_filter`（= `L1Manager.is_key_evictable`），不合格就**跳过**，凑够 `target_count` 个为止：`lru.py:181-188`。
3. 返回一个 `DISCARD` 动作：`lru.py:193-199`。

**被读锁锁住的为什么不会被挑中——两层**：
- 第一层（挑选时）`is_key_evictable`：常驻对象**读锁没锁着**才可；或暂存对象写锁已过期：`L1B:983-992`。读锁锁着 → 假。
- 第二层（删除时）`delete` 自己再查一遍：读锁锁着且非 force → `KEY_IS_LOCKED`，不删：`L1B:804-807`；有活着的暂存也不删：`L1B:793-797`。【注释】`L1B:972-974` 解释为什么第一层不拿锁：“will check again and safely reject a key that became locked between the check and the actual deletion.”（句首主语是 `delete()`） 淘汰线程调 `delete(action.keys)` 时没传 `force`：`EC:194`。
- 为什么 `is_key_evictable` 不拿 L1 全局锁，除了性能，还有一个更硬的原因【推断，依据：`reserve_write` 持 L1 锁时同步回调监听者→LRU 要拿自己的锁（`L1B:583-584` → `lmcache/v1/distributed/eviction.py:203` → `lru.py:82`）；而 `get_eviction_actions` 持 LRU 锁时调过滤器（`lru.py:160-182`）。过滤器若再拿 L1 锁，就是两把锁反序互等】。源码只写了前一个理由。

**为什么需要第一层？** 【提交】`cb8b1c04` 标题 “[MP][Optimize] Skip locked keys during LRU eviction to improve eviction efficiency (#2978)”。该提交的 diff 给 `get_eviction_actions` 新增了 `key_eligible_filter`，并让 `EC` 把 `is_key_evictable` 传进去；【注释】`lru.py:145-146` 写明用途：“This is useful for skipping locked keys that cannot be deleted.” 「该提交之前 LRU 直接取队首 N 个、锁住的删不掉所以配额白白浪费」是我从标题 “improve eviction efficiency” 反推的【推断】。

**LRU 次序怎么维护**（`L1EvictionPolicy` 监听桥，`lmcache/v1/distributed/eviction.py:175-215`）：
- 保留写（`reserve_write`）→ 立刻 `on_keys_reserved` → 默认等价于 `on_keys_created`：【注释】`eviction.py:201-202` “Track keys from the moment they are reserved, so an abandoned reservation (expired write lock) is evicted like any unlocked key.”
- 转正 `finish_write` → `on_keys_created` 再来一次，把它们移到最新端（`lru.py:86-92`）。所以 recency 以**写完时间**为准。
- 加读锁 / 放读锁 → **不动**：【注释】`eviction.py:196-197` “No-op: releasing a read lock does not mean the key was useful.”
- 触碰 `touch_keys` → 移到最新端：来源有两处——预取完成时对命中的 key（`PC:1730-1733`），和请求结束时对该请求的**所有** key（`lmcache/v1/multiprocess/modules/lookup.py:646`；【提交】`aad12303` “[MP][optimize] unified touch all keys in end session request (#3020)”，只有标题）。
- 删除 → 从次序表摘掉（`lru.py:112-126`）。
- **同一个请求的多个 chunk，后面的先被淘汰**：新建 / 触碰时对 key 列表做 `reversed` 再逐个放到最新端，所以最前面的 chunk 最后落在最新端。【注释】`lru.py:83-85` “NOTE: for the request, the later keys should be evicted first.” 例子：`[h0,h1,h2]` 放入后次序表尾部是 `…, h2, h1, h0`，队首方向的 `h2` 先被淘汰。**为什么**：前缀匹配只认连续前缀，先删 `h0` 会让 `h1`、`h2` 虽在却永远命不中（同一注释后半句 “due to prefix match, key2 and key3 will not be hit.”）。出处是提交 `bdf4c9a9`（标题 “[MP][optimize] optimize evict in lru policy (#2740)”，正文空；理由只在注释里）。

**例子（设）**：池里 456 个对象，用量 0.8016，淘汰线程在下一个 1 s 心跳醒来 → 要 `int(456×0.2) = 91` 个；队首扫过去，被读锁钉住的 `h0–h2`（B 刚预取、又刚被 `touch`，本就在队尾）根本排不到前面；即使在，也会被过滤器跳过。删完 365 个，约 64%。

### 3.6 L1 内存怎么分配；满了 reserve_write 返回 OUT_OF_MEMORY 之后会发生什么

**分配**：
- 默认池是**懒增长**的 CPU 固定内存：`--l1-use-lazy` 默认真（`lmcache/v1/distributed/config.py:951-955`）；先固定 `init_size`（默认 20 GiB）再后台线程每 64 MiB 一块地固定剩下的，每 1 GiB 提交一次：`lmcache/v1/memory_allocators/lazy_memory_allocator.py:87-89`，`lmcache/v1/memory_allocators/lazy_memory_allocator.py:363-379`。`init_size` 会被截到不超过总大小：`lmcache/v1/distributed/config.py:293`，所以例子里 20 GiB / 20 GiB 没有后台增长。
- 池内是一个**有序空闲块列表**（`AddressManager`）：一批 n 个同尺寸对象，遍历空闲块，每块尽量多切，**凑不齐 n 个就整批失败，空闲表原样不动**：`lmcache/v1/memory_management.py:1265-1287`，`lmcache/v1/memory_management.py:1288-1299`。【注释】`lmcache/v1/memory_management.py:1289` “Not enough memory; free list is untouched, no rollback needed”。
- `L1MemoryManager.allocate` 把失败翻成 `(OUT_OF_MEMORY, [])`：`lmcache/v1/distributed/memory_manager/l1_memory_manager.py:136-141`。
- 「用量」= 地址管理器的 `堆大小 − 空闲大小`，分母是**当前已增长的堆**（不是配置的总大小）：`l1_memory_manager.py:186-190`；这也是淘汰判断用的分子分母。懒增长未完成时分母偏小，水位会**提前**到达【推断，依据同上两处】。

**OUT_OF_MEMORY 之后**：
1. `L1Manager.reserve_write` 对这一批里**每个**需要分配的 key 都标 `OUT_OF_MEMORY`（即使其中有几个其实放得下）：`L1B:559-561`。【测试】`tests/v1/distributed/test_l1_manager.py:892-905`：要 10 个 8 MB、池 64 MB，**10 个全返回 OOM**。例子：A 要存 3 个对象但只剩 2 个槽 → 3 个全不存。
2. 没有建任何 staging 对象，不占内存，也不进 LRU（监听者只收到 `successful_keys`：`L1B:581-584`）。
3. `StorageManager.reserve_write` 把这些 key 从结果里剔掉，并发 `L1_ALLOCATION_FAILED` 事件（`during="l1_store"`）：`SM:328`，`SM:341-348`；指标 `lmcache_mp.l1_allocation_failure`（`lmcache/v1/mp_observability/subscribers/metrics/l1_failures.py:40-46`，描述里写的是 “L1 memory allocation failures (OOM)”）。
4. 传输模块拿到的对象表里没有这些 key，就当 `None` 跳过拷贝；`all_dict` 为空 → 不挂 `finish_write`，返回的 `bool` 仍是 True，但 `stored_mask` 对应位为 False：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:973-975`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1065-1084`。**没有重试、没有阻塞、没有同步淘汰**。
5. 对用户的后果：这几块 KV 这次没进缓存；下一个同前缀的请求 LOOKUP 会少命中（miss），照常重算，之后若有空间再存。**推理本身不受影响**。
6. 空间什么时候回来：淘汰线程在下一个 1 s 心跳、用量 ≥ 80% 时释放约 20%。若池里**几乎全是被读锁钉住的对象**，过滤器会把它们全跳过，一个也删不掉——【推断】这时 OOM 会持续到读锁释放或到期。配置文档有一句相关的警惕：`docs/source/mp/configuration.rst:719-720` 对 `--eviction-ratio` 的说明提到要避免 “so locked data cannot fill L1 and block new KV and prefetches.”（该句讲的是 L2 写出的等待水位，不是 L1 的 OOM，只作为「锁住的数据占满 L1」是被预料到的风险的旁证）。

---

## 4. 源码页候选（6 段）

> 每段给出「读者从头读到尾就能明白这一块」的范围；「批注点」只列真正值得讲的行。

### 页 1 ｜ `reserve_write`：三道门 + 一次分配 + 建暂存
文件：`lmcache/v1/distributed/l1_manager/base.py:526-595`（70 行）。
做什么：对每个 key 判断能不能写；剩下的凑一批向内存管理器要；成功就建带两把锁的对象放进暂存表；失败整批 OOM。
批注点：
- `L1B:527-529` 常驻即拒。为什么：去重；写同一块不浪费（顶层 §4.4 B8）。【文档】`staging-and-write-tags.md:25`。
- `L1B:531-535` 同 tag 暂存还活着即拒 → 这就是「两个 STORE 同一个 key」的去重点（§3.2）。
- `L1B:536-547` **过期的暂存直接交接**：不再分配，`write_lock.lock()` 重新上锁，注意日志级别是 warning。为什么：不浪费内存、又不阻塞新写者【推断】；【测试】`tests/v1/distributed/test_l1_manager.py:2384` 保证交接后 staging 计数不变（提交 `c01c30ea`，“Record the charge on the staging entry at put and subtract exactly that at pop.”）。
- `L1B:552-553` 全被拒就早退，不动分配器。
- `L1B:555-556` **一批一次**向分配器要 —— 所以 OOM 是整批的（§3.6）。
- `L1B:561-565` 失败时给所有 key 标 OOM，并释放可能已分到的部分。
- `L1B:571` `set_l1_manager(...)`：给内存对象盖「属于哪个 L1」的章，之后 `l1_owners_of` 靠它还原（`SM:1770-1789`）。
- `L1B:574-578` 两把 `TTLLock`；只有写锁此刻上了锁（读锁计数 0）。

### 页 2 ｜ `finish_write` 与 `_take_staging`：转正
文件：`L1B:635-654`（`finish_write` 主循环）+ `L1B:1189-1204`（`_take_staging`）。
批注点：
- `L1B:1193-1201` 写锁已过期 → `KEY_IN_WRONG_STATE`，**不**转正。warning 日志里写着 “potential inconsistent data might be written”（`L1B:1196-1197`）：过期意味着别人可能已经接手同一块 buffer（见页 1 的交接）。
- `L1B:1203-1204` 先 `unlock()` 再从暂存表摘出：同一把全局锁内完成，没有别人能看到「暂存已空但还没常驻」的中间态（【注释】`L1B:191` “For every operation on list of keys, the operation is atomic”，指的是整批原子）。
- `L1B:640-648` 已常驻 → 丢弃自己、仍返回 SUCCESS（§3.1）。
- `L1B:649` **整个状态机最关键的一行**：`self._objects[key] = entry`。
- `L1B:650-658` 临时对象不通知监听者（不会被推到 L2）。

### 页 3 ｜ `reserve_read` / `unsafe_read` / `finish_read`：读锁三件套
文件：`L1B:322-340`（`reserve_read`）、`L1B:364-378`（`unsafe_read`）、`L1B:415-446`（`finish_read` 主循环）。
批注点：
- `L1B:326-329` 只查 `_objects`：暂存永远读不到 → `KEY_NOT_EXIST`。
- `L1B:331-335` 逐把加锁的 for 循环 + TODO(perf)；`total` 已被 `validate_read_locks` 夹在 [1,128]（`L1B:53-76`）。
- `L1B:372-374` `unsafe_read` 要求「已锁」，没锁给 `KEY_NOT_READABLE`——它是**安全网**，不是加锁。
- `L1B:426-433` `finish_read` 对没锁的 key 只打 warning、返回 `KEY_IN_WRONG_STATE`：这是 TTL 过期后慢读者收尾时走的分支（§3.4）。
- `L1B:438-444` 放锁后若是临时对象且计数归零就当场删除（`is_temporary`，主路无）。

### 页 4 ｜ `TTLLock`：计数 + 租约
文件：`csrc/lmcache_native/ttl_lock.cpp:13-75`（63 行，C++）。
批注点：
- `ttl_lock.cpp:23` `expired = (current_time >= old_expiration)`；初始到期时间为 0，所以新锁天然是「已过期」。
- `ttl_lock.cpp:25-33` 过期分支：先 CAS 到期时间，再把计数**直接置 1**——过期前累积的计数被丢弃。
- `ttl_lock.cpp:36-45` 未过期：计数 CAS +1，**再无条件刷新到期时间**。所以多读者里最后一个来的决定整把锁何时到期。
- `ttl_lock.cpp:55-58` `unlock` 到 0 为止，多放不会变负。
- `ttl_lock.cpp:74` `counter > 0 && now < expiration`。
- 为什么用 C++ 原子：见 §8「没查到」。可查到的只有：提交 `f3cf3695` 标题 “[MP][2/N] Add native implementation for ttl lock for mp storage manager (#2505)” 并附带 microbenchmark（`benchmarks/microbenchmark/ttl_lock_benchmark.py`）；Python 侧注释把它称为 “TTLLock is C++ std::atomic”（`L1B:333`）。

### 页 5 ｜ 淘汰：定时循环 + LRU 过滤 + `is_key_evictable`
文件：`EC:160-198`（31 行）、`lmcache/v1/distributed/eviction_policy/lru.py:160-199`、`L1B:969-992`。
批注点：
- `EC:165` `time.sleep(1)`：淘汰粒度是秒级；一秒内写满池子就会 OOM。
- `EC:166-177` 用量比与水位比较；`total_bytes == 0` 记 0（影子 L1 没有本地字节）。
- `EC:184-187` 把 `is_key_evictable` 作为过滤器传下去。
- `lru.py:168-175` 个数 = 被跟踪 key 数 × 比例，至少 1。
- `lru.py:181-188` 扫描 + 跳过 + 够数即停。
- `L1B:983-985` 常驻且读锁没锁；`L1B:986-992` 暂存的写锁过期也算可淘汰（`list(...)` 做快照，因为不持 L1 锁）。
- `EC:194` 真删时**不带 force**（第二层保险）。

### 页 6 ｜ 预取（L1 路径）：见到就锁，再按前缀裁
文件：`PC:714-729`（入口）+ `PC:1263-1294`（`_lock_l1_keys`）+ `PC:1313-1336`（`_plan_load` 主体）。
批注点：
- `PC:718-724` 没有 L2 → 调用线程内「锁 → 规划 → 收尾」，返回时结果已可查询。
- `PC:1260-1261`（docstring）“Locks are taken regardless of the lock mode; under ``NO_LOCK`` they are released when the request finishes.” 无论哪种模式都先上锁——探测即占有。
- `PC:1273-1279` 对每个允许的 L1、每行一次批量 `reserve_read(row.keys, row.num_kv_readers)`。
- `PC:1292-1294` `try/finally`：没跑完就 `_release_all_locks`，异常也不留锁。
- `PC:1323-1328` 计划外的格子立刻 `finish_read`。
- `PC:1334-1335` 把「已锁」位图改成计划本身：此后记录与实际持锁严格一致，这是 `_release_all_locks` 能整表回滚的前提。

---

## 5. 不变量

1. **读者只会读到拷完的数据**：暂存 → 常驻只发生在 `finish_write`（`L1B:649`），读路径只查 `_objects`（`L1B:326`、`L1B:367`）。
2. **被探测到的就是被锁住的**：LOOKUP 报告的每个命中，都是已 `reserve_read` 成功的对象（`PC:1273-1289`；【文档】`prefetch_controller.md:29-33`）。
3. **有读锁的对象不会被淘汰删除**：过滤 + `delete` 复查（§3.5），前提是锁没过期。
4. **同一个 key 同一时刻只有一个同 tag 的写者**：`L1B:531-535`。
5. **常驻 key 不会被同 tag 再写一份**：`L1B:527-529`。
6. **TTL 是兜底，不是正常路径**：正常释放靠回调（`finish_read_by_owner` 等，传输稿）。

## 6. 故障与收场（L1 内部视角）

| 坏在哪 | 收场 | 锚点 |
|---|---|---|
| 读者崩了 / 取消忘了放 | 读锁 300 s 后整把失效；对象恢复可淘汰 | `ttl_lock.cpp:74`，`lmcache/v1/distributed/config.py:432` |
| 写者崩了（STORE 没挂回调） | 暂存写锁 600 s 后失效；淘汰可回收；期间同 tag 其他写者被拒 | `L1B:1193-1201`，`L1B:983-992`；【提交】`831e0502` |
| STORE 中途失败 | 主动 `abort_write` → `finish_write_and_delete` 立刻丢暂存 | `SM:393-415`，`L1B:820-865` |
| 池满 | 本批全部 OOM，不存，推理无感 | §3.6 |
| 读者慢到锁过期 | RETRIEVE 失败 → vLLM 重算 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1333-1336` |
| 淘汰线程跟不上 | 秒级轮询；写入速度超过一个心跳内能腾出的量就 OOM【推断】 | `EC:165` |

## 7. 可观测与可调

- 指标：`lmcache_mp.l1_memory_usage_bytes`、`lmcache_mp.l1_usage_ratio`、`lmcache_mp.l1_staging_bytes`（`L1B:243`、`L1B:251`、`L1B:257`）；`lmcache_mp.l1_allocation_failure`（OOM 计数）。
- `report_status()` 给出对象数、读锁数、写锁数、暂存字节：`L1B:1061-1104`。
- 配置：`--l1-size-gb`、`--l1-use-lazy`（默认真）、`--l1-init-size-gb`（默认 20）、`--l1-read-ttl-seconds`（300）、`--l1-write-ttl-seconds`（600）、`--eviction-policy`、`--eviction-trigger-watermark`（0.8）、`--eviction-ratio`（0.2）：`lmcache/v1/distributed/config.py:945-961`，`lmcache/v1/distributed/config.py:1046-1085`。

## 8. 没查到为什么

1. **读锁 300 s、写锁 600 s 为什么是这两个数**。只有「防客户端崩溃死锁」这一层意思（`docs/source/mp/index.rst:370-371`），数值没有理由。
2. **为什么 `TTLLock` 用 C++ 实现**。提交 `f3cf3695` 只有标题和一个 microbenchmark；代码注释把它称为 `std::atomic`。没有「Python 实现太慢 / 需要无 GIL」之类的原话。
3. **读锁到期后「计数重置为 1」的取舍**（§3.4 第 5 点的交错）。头文件只描述行为（`csrc/lmcache_native/ttl_lock.h:36-37`，“If the previous TTL has expired, the counter is reset to 1.”），没说为什么重置而不是保留旧计数。
4. **为什么淘汰是 1 s 轮询而不是分配失败时同步淘汰**。没找到讨论。
5. **为什么淘汰比例按 key 个数、文档写成内存比例**（`lru.py:168` vs `lmcache/v1/distributed/config.py:495`）。没找到说明。
6. **`038cd7c9` / `a973aa8e` 的具体失败场景**：提交只有标题，§3.4 里的因果是推断。
7. **水位 0.8 / 比例 0.2 的选取**：没找到。
8. **为什么 Session 过期不放 L1 读锁**（主路）：只看到 blend 注册了监听者，没找到「主路不需要」的说明。

## 9. 可改进处【观点】

- 【观点】OOM 的批粒度：一批 3 个只差 1 个槽就整批放弃，按前缀语义其实可以只存前 2 个。
- 【观点】读锁到期后别的读者重置计数、慢读者再 `unlock` 会误减（§3.4 第 5 点）：若 `unlock` 能带一个「世代号」可避免。
- 【观点】淘汰完全靠 1 s 定时器；分配失败时顺手触发一次（或唤醒淘汰线程）能缩短 OOM 窗口。
- 【观点】主路 Session 过期时顺手释放该请求遗留的 L1 读锁，崩溃场景下不必干等 300 s。

## 10. 给讲解员的读者层提示

1. **最容易看错的一件事：「常驻表」和「暂存表」是两张表**，不是一个对象带一个状态字段。顶层 §4.5 的状态图画成了一条链，读者容易以为对象在 `_objects` 里从 write_locked 走到 ready；真实是「`_staging` → 转正 → `_objects`」，转正那一行 `L1B:649` 值得放大。
2. **`TTLLock` 名字像互斥锁，其实是「计数 + 租约」**。建议用 B / C / B 崩溃这组例子并排：计数 1 → 2 → 0；以及「到期不需要任何人解锁」。
3. **`unsafe_read` 的名字**：安全性来自 LOOKUP 时加的锁，不在这一步。
4. **「SUCCESS」的含义**：`finish_write` 丢弃自己的暂存时也返回 SUCCESS。
5. **OOM 是整批的**，且**没有同步淘汰**——读者很容易默认「满了会先挤掉旧的再写」。
6. 值得并排对照的两件事：**挑选时过滤**（`is_key_evictable`）与**删除时复查**（`delete`）；**见到就锁**（`_lock_l1_keys`）与**按前缀裁剪后放锁**（`_plan_load`）。
7. 例子里新增的量全部标了「设」：456 个对象触发淘汰、一次删 91 个、A2、C、B 崩溃（t0 / t0+300 s）、B 缺口（`h1` 被淘汰）。其余沿用顶层与传输稿。
