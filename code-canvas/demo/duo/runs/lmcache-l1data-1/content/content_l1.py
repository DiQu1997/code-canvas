from cv import *

L1B = 'lmcache/v1/distributed/l1_manager/base.py'
SM = 'lmcache/v1/distributed/storage_manager.py'
PC = 'lmcache/v1/distributed/storage_controllers/prefetch_controller.py'
EC = 'lmcache/v1/distributed/storage_controllers/eviction_controller.py'
LRU = 'lmcache/v1/distributed/eviction_policy/lru.py'
EVP = 'lmcache/v1/distributed/eviction.py'
DCFG = 'lmcache/v1/distributed/config.py'
API = 'lmcache/v1/distributed/api.py'
LK = 'lmcache/v1/multiprocess/modules/lookup.py'
LDT = 'lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py'
MM = 'lmcache/v1/memory_management.py'
TTLH = 'csrc/lmcache_native/ttl_lock.h'
TTLC = 'csrc/lmcache_native/ttl_lock.cpp'
TL1 = 'tests/v1/distributed/test_l1_manager.py'
STGDOC = 'docs/design/v1/distributed/l1_manager/staging-and-write-tags.md'
PCDOC = 'docs/design/v1/distributed/storage_controllers/prefetch_controller.md'
SMDOC = 'docs/design/v1/distributed/storage_manager.md'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 引文（逐字照抄）
Q_STG = ('Staging objects are kept apart from the resident objects: readers of the key do not see them, '
         'and writers with different tags may stage the same key at the same time.')
Q_OLD = 'Two prefetch requests loading the same L2 object therefore collided'
Q_TAG = 'tag makes concurrent stores of the same key exclude each other.'
Q_DEDUP = 'which keeps the de-duplication engine stores rely on'
Q_HAND = 'The previous reservation expired: hand the buffer over.'
Q_EITHER = 'the data is in L1 either way.'
Q_NOMEM = 'Not enough memory; free list is untouched, no rollback needed'
Q_READERS = 'one per worker that consumes a read lock for the same key (e.g. MLA models with TP > 1).'
Q_MAXRL = 'to prevent a single call from holding the global lock for too long.'
Q_UNSAFE = 'Unsafe read the read-locked objects without adding new read locks.'
Q_RESET = 'If the previous TTL has expired, the counter is reset to 1.'
Q_REFRESH = 'The TTL is always refreshed to the current time + TTL duration.'
Q_OBS = 'Observation is acquisition.'
Q_NEVER = 'so a concurrent eviction can shrink what gets discovered but never invalidate a reported hit.'
Q_ANON = 'L1 locks are anonymous refcounts:'
Q_NOGLOBAL = 'This method does NOT acquire the global L1Manager lock.'
Q_RECHECK = 'will check again and safely reject a key that became locked between the check and the actual deletion.'
Q_LATER = 'for the request, the later keys should be evicted first.'
Q_PREFIX = 'due to prefix match, key2 and key3 will not be hit.'
Q_NOOP = 'No-op: releasing a read lock does not mean the key was useful.'
Q_ABANDON = 'reservation (expired write lock) is evicted like any unlocked key.'
Q_DECOUPLE = 'LRU is decoupled from locking.'
Q_PREFIXDOC = 'only the longest prefix every row can serve under its window.'
Q_OWNS = 'planning, locking and loading live in the prefetch controller'
Q_INCONS = 'The key is not read-locked, which means the reader may read inconsistent data.'
Q_LOCKMODE = 'Locks are taken regardless of the lock mode'


# ---------------------------------------------------------------- 小图（接顶层：A 存 h0 h1 h2，每个 36 MiB；B 查中 3 块、读者数 1）
def stg(rows):
    return g_kv(rows)


G_XFER0 = g_list(['STORE A：h0 h1 h2', 'RETRIEVE B：还没来'])
G_LOOK0 = g_kv([('B 要查', 'h0 h1 h2 h3′'), ('读者数', '1（TP=1）')])
G_STG0 = g_kv([('结构', 'key → tag → 对象'), ('现在', '空')])
G_OBJ0 = g_kv([('结构', 'key → 对象'), ('每个对象', '写锁 + 读锁'), ('现在', '空')])
G_PC0 = g_checks([('① 上锁', None), ('② 按前缀留', None), ('③ 收尾', None)])
G_EV0 = g_list(['用量 0 / 20 GiB', 'LRU 名单：空'])

G_STG_A = stg([('h0', '写锁 1 · 600 s'), ('h1', '写锁 1 · 600 s'), ('h2', '写锁 1 · 600 s')])
G_OBJ_A = stg([('h0', '读锁 0 · ready'), ('h1', '读锁 0 · ready'), ('h2', '读锁 0 · ready')])
G_OBJ_B = stg([('h0', '读锁 1 · 到 t=300'), ('h1', '读锁 1 · 到 t=300'), ('h2', '读锁 1 · 到 t=300')])
G_EV_A = g_list(['用量 108 MiB ≈ 0.5%', 'h2 ← 最先被扫', 'h1', 'h0 ← 最新'], on=1)


def _scene_node():
    parts = [
        # ------------------------------------------------ 框外：两个调用方
        part('xfer', '传输模块（框外）', [16, 100, 190, 175], m=[8, 10, 324, 120], kind='ext',
             glyph=G_XFER0,
             say='来存、来读的 GPU 线程', why='只在拷完时提交、放锁', trap='它不上读锁，只放锁',
             card=card(
                 '框外的调用方：顶层的「传输模块」。在这一层，它只做四件事：STORE 时 `reserve_write`、拷完 `finish_write`；'
                 'RETRIEVE 时 `unsafe_read`、拷完放锁。',
                 ['`reserve_write` / `unsafe_read` 在 GPU 亲和线程上调。' + R(LDT, 960, 962) + R(LDT, 1330, 1332),
                  '`finish_write` / 放锁由 CUDA 流回调的排空线程调（顶层已讲，这里只看 L1 一侧发生什么）。',
                  '它不直接碰 `DramL1Manager`，而是经过门面 `StorageManager`：门面挑写哪个 L1（默认第一个），'
                  '并给所有写入盖同一个 tag `"storage_manager"`。' + R(SM, 314, 325) + R(SM, 99, 101)],
                 [note('推断', '对象表、锁、淘汰都藏在门面后面：调用方只拿到「成功的 key → 内存对象」，失败的 key 就是不在结果里，'
                              '调用方照样往下走。'),
                  note('注释', '所有引擎写入共用一个 tag，所以两个请求同时存同一个 key 会互相排斥（见 A2 那一步）。',
                       q=Q_TAG, ref=A(SM, 100))],
                 ['RETRIEVE 的读锁不是它上的：锁在 LOOKUP 时已经上好，它只在拷完后把锁放掉。']),
             to='l1.write'),
        part('look', '查找模块（框外）', [16, 345, 190, 165], m=[8, 970, 150, 120], kind='ext',
             glyph=G_LOOK0,
             say='带着一串 key 来问', why='一次问完一整串', trap='它只交任务，不碰锁',
             card=card(
                 '框外的另一个调用方：顶层的「查找模块」。它把一个请求的全部 key 打成一个预取任务交进来，问「哪些能用」。',
                 ['任务 `PrefetchTaskSpec`：一行 key（`[h0, h1, h2, h3′]`）+ 读者数 `num_kv_readers` + 策略，默认 '
                  '`fetching_policy="prefix"`、`lock_mode=LOCK`。' + R(API, 742, 746) + R(API, 701),
                  '它调 `StorageManager.submit_prefetch_task`，门面再交给预取控制器。' + R(SM, 584)],
                 [note('文档', '门面只管请求句柄和日志；规划、上锁、加载都在预取控制器里。', q=Q_OWNS, ref=A(SMDOC, 5, 7))],
                 ['读者数是「每个 key 上几把读锁」，不是「几个请求」：TP=1 时是 1；MLA 模型 TP>1 时每个 worker 各一把。']),
             to='l1.prefetch'),

        # ------------------------------------------------ 对象表（两张）
        part('staging', '暂存表', [325, 100, 240, 175], m=[8, 200, 165, 160], kind='store', n='1',
             code='_staging', glyph=G_STG0,
             say='拷了一半的对象放这，读者看不见', why='占住内存，又不让人读半截', trap='同 key 同 tag 只能有一个',
             card=card(
                 '写入中的对象住在这里：内存已经分好、GPU 还在往里拷。读者查不到它们，淘汰也碰不了它们（写锁有效时）。',
                 ['结构：`_staging[key][tag] → L1ObjectState`，一个 key 底下按写者的 tag 再分一层。' + R(L1B, 217, 219),
                  '`reserve_write` 逐 key 判断：已在常驻表 → `KEY_NOT_WRITABLE`；同 tag 已有写锁有效的 → `KEY_NOT_WRITABLE`；'
                  '同 tag 的旧预留写锁过期了 → 把那块 buffer 交给新写者；其余的进「要分配」名单。' + R(L1B, 526, 549),
                  '「要分配」的**一次**向分配器要：A 是 3 块 × 36 MiB。拿不齐就整批 `OUT_OF_MEMORY`，已分到的退回。' + R(L1B, 555, 565),
                  '每块包成 `L1ObjectState`，配两把新 `TTLLock`（写 600 s、读 300 s），只锁上写锁。' + R(L1B, 572, 579)],
                 [note('文档', '旧设计把预留直接放进常驻表：两个预取同时加载同一个对象就撞车，第二个整单放弃。',
                       q=Q_OLD, ref=A(STGDOC, 11, 12)),
                  note('注释', '分开放：读者看不见暂存对象，不同 tag 的写者可以同时暂存同一个 key。', q=Q_STG, ref=A(L1B, 151, 154)),
                  note('推断', '对主路来说：GPU 异步拷贝要时间，内存得先占住，但没拷完不能让人读——暂存表正好满足这两点。')],
                 ['整批原子：A 的 3 块若只剩 2 块空位，是 3 块全 OOM，不是存 2 块；也不会当场去挤掉别的对象，腾地方只靠后台淘汰。'
                  + R(L1B, 559, 561),
                  '写锁过期（600 s）的暂存对象不会自己消失：它留在原地，等淘汰来回收，或被同 tag 的下一个写者接手。']),
             to='l1.write'),
        part('objects', '常驻表', [685, 100, 250, 410], m=[8, 430, 324, 190], kind='store', n='2',
             code='_objects', glyph=G_OBJ0,
             say='读者能查到的对象，各带一把读锁计数', why='状态 = 在哪张表 + 锁是否有效', trap='没有 state 字段',
             card=card(
                 '对读者可见的对象表：`reserve_read` / `unsafe_read` / `delete` 都只查这张表。每个对象挂一把读锁 `TTLLock`，'
                 '计数 > 0 且没过期，就是「正被人用着，不许删」。',
                 ['结构：`_objects[key] → L1ObjectState`，字段只有 `memory_obj / write_lock / read_lock / is_temporary / '
                  'staging_charge`。' + R(L1B, 79, 97) + R(L1B, 216),
                  '`TTLLock` 只有三个数：计数、过期时刻、TTL 时长。' + R(TTLH, 79, 86),
                  '它的规则：`lock()` 若旧锁已过期，计数**重置为 1**，否则 +1；两种情况都把过期时刻刷新成「现在 + TTL」。'
                  '`unlock()` 减 1，最低到 0。`is_locked()` = 计数 > 0 **且**没过期。' + R(TTLH, 33, 58),
                  '两张表的几乎所有读写方法都挂同一把全局锁 `self._lock`；例外是 `is_key_evictable` 和 `touch_keys`，它们不拿。'
                  + R(L1B, 213)],
                 [note('注释', '锁带过期，计数残留不会累积：过期后下一次 `lock()` 直接从 1 开始。', q=Q_RESET, ref=A(TTLH, 36)),
                  note('测试', '有测试专门验证：读锁中的 key 不可淘汰。',
                       q='def test_read_locked_key_is_not_evictable', ref=A(TL1, 1783, 1793)),
                  note('推断', '锁不记「谁持有」，只记个数和到期时刻：客户端崩了也没人需要替它放锁，等过期即可。')],
                 ['「状态」是推出来的：在暂存表 + 写锁有效 = 写入中；在常驻表 + 读锁计数 0 或已过期 = ready；'
                  '在常驻表 + 计数 n 且没过期 = read_locked(n)。' + R(L1B, 983, 992),
                  '所有持有者共用一个过期时刻：设 t=200 另一个请求 C 也锁 h0，计数 2、到期被刷到 t=500——谁最后上锁，谁决定大家的到期。',
                  '转正时对象**不搬家**：同一个 `L1ObjectState` 从暂存表弹出、放进常驻表，内存地址不变。']),
             to='l1.read'),

        # ------------------------------------------------ 查 + 上锁
        part('pc', '预取控制器', [325, 345, 240, 165], m=[8, 740, 150, 160], kind='gate', n='3',
             code='PrefetchController', glyph=G_PC0,
             say='查到就上锁，按前缀留', why='报出的命中必须兑现得了', trap='计划外的锁当场放',
             card=card(
                 '「查 + 上读锁」的执行者。没有 L2 时，它在查找模块那条线程上**同步**做完三步：上锁 → 按策略留 → 收尾。'
                 + R(PC, 718, 723),
                 ['① `_lock_l1_keys`：对每一行 key 调 `reserve_read(keys, num_kv_readers)`，成功的记进位图。' + R(PC, 1273, 1290),
                  '中途出异常，`finally` 里放掉已上的锁。' + R(PC, 1291, 1294),
                  '② `_plan_load`：按「前缀」策略算该留哪几格；已锁但不在计划里的，当场 `finish_read` 放掉，放几把就是上几把。'
                  + R(PC, 1313, 1336),
                  '③ `_finish_request`：对命中的 key `touch_keys`（刷新 LRU）；`LOCK` 模式保留锁，并记下「哪个 L1 持有」。'
                  + R(PC, 1722, 1733),
                  '结果（含 `l1_owners`）放进完成表，调度器 QUERY 来取一次就弹出。' + R(PC, 1748, 1754) + R(PC, 749, 750)],
                 [note('文档', '「看见即锁住」：一个 key 从被发现起就锁着，所以并发的淘汰只能让发现的少一点，不会让已报出的命中失效。',
                       q=Q_NEVER, ref=A(PCDOC, 29, 33)),
                  note('推断', '从 LOOKUP 到 RETRIEVE 隔着好几个调度步；报给 vLLM「能命中 768」是一个承诺，不锁住就可能在中间被删掉。'),
                  note('文档', '「前缀」= 每一行都能连续供给的最长前缀。', q=Q_PREFIXDOC, ref=A(SMDOC, 36))],
                 ['锁总是先上：哪怕 `NO_LOCK` 模式也先锁，收尾时才放。' + R(PC, 1260, 1261),
                  '对照：若链是 h0 命中、h1 不在、h2 命中，前缀在 h1 断了，h2 的锁在第②步就被放掉，命中只算 1 块。']),
             to='l1.prefetch'),

        # ------------------------------------------------ 后台淘汰
        part('evict', '淘汰线程 + LRU', [1055, 100, 150, 220], m=[186, 740, 150, 200], kind='bg', n='4',
             code='L1EvictionController', glyph=G_EV0,
             say='每秒看用量，超线删 20%', why='锁着的跳过，不占名额', trap='按个数删，不按字节',
             card=card(
                 '后台守护线程，每 1 秒醒一次：用量 ≥ 80% 就从 LRU 名单最久没用的一端挑约 20% 的 key 删掉——只挑没被锁着的。'
                 + R(EC, 160, 190),
                 ['LRU 名单是一个 `OrderedDict`，开头最旧、末尾最新；它自带一把锁。' + R(LRU, 50, 53),
                  '名单靠监听更新：预留就登记，转正、touch 都挪到末尾；**同一批 key 按倒序**挪，所以 h0 最新、h2 最先被扫到。'
                  + R(LRU, 86, 92) + R(LRU, 107, 110),
                  '挑选：目标 = `int(名单长度 × 0.2)`，至少 1 个；从旧到新扫，过滤器 `is_key_evictable` 为假就跳过，凑够就停。'
                  + R(LRU, 168, 188),
                  '默认触发线 0.8、比例 0.2。' + R(DCFG, 491, 495)],
                 [note('注释', '同一请求的后面几块先删：先删 key1 的话，因为前缀匹配，key2、key3 也命不中了。',
                       q=Q_PREFIX, ref=A(LRU, 85)),
                  note('提交', '「挑的时候就跳过被锁的 key」是后加的优化，让一轮淘汰不白挑。',
                       q='Skip locked keys during LRU eviction to improve eviction efficiency', ref='c:cb8b1c04'),
                  note('推断', '用量轮询 + 批量删：写路径上从不等淘汰；代价是写入突发时，一秒内可能冲过水位、出现 OOM。')],
                 ['按名单里 key 的**个数**算 20%，配置注释写的却是「内存的比例」；对同尺寸对象两者一样。' + R(DCFG, 495) + R(LRU, 168),
                  '每轮只删一批，下一秒再看；不是「删到水位以下为止」。',
                  'OOM 不会叫醒它：腾地方完全靠这每秒一次的轮询（读代码的结论）。' + R(EC, 165)]),
             to='l1.evict'),
    ]

    wires = [
        wire('w_rsv', 'xfer', 'staging', 'reserve_write', ml='申请暂存', why='先占内存，不露给读者',
             trap='拒绝不报错，只是缺席',
             card=card(
                 'STORE 开始：传输模块经门面申请 3 个对象。成功的拿到空的内存对象，GPU 往里拷；失败的 key 不出现在结果里。'
                 + R(SM, 314, 328),
                 ['门面先按写策略挑 L1、校验每个 key 恰好放一次，再以固定 tag 调 L1 的 `reserve_write`。' + R(SM, 314, 325),
                  'OOM 的 key 门面会另外发一个 `L1_ALLOCATION_FAILED` 事件计数。' + R(SM, 341, 348)],
                 [note('注释', '共用一个 tag，同一 key 的并发写入互相排斥。', q=Q_TAG, ref=A(SM, 100)),
                  note('文档', '这正是引擎写入依赖的「去重」。', q=Q_DEDUP, ref=A(STGDOC, 32))],
                 ['被拒（已存在 / 别人在写 / 没内存）都不抛异常：那几块这次不存，vLLM 那边不受影响。']),
             to='l1.write'),
        wire('w_fin', 'staging', 'objects', 'finish_write', ml='转正', why='先验写锁再换表',
             trap='别人先转正：丢自己仍算成功',
             card=card(
                 'GPU 拷完，流回调调 `finish_write`：把这个 tag 的暂存对象挪进常驻表，从此读者看得见。' + R(L1B, 635, 658),
                 ['`_take_staging`：先查写锁**还有效**（过期 → `KEY_IN_WRONG_STATE`，对象留在暂存表等回收），再解锁、弹出。'
                  + R(L1B, 1189, 1204),
                  'key 已经常驻（别的 tag 先转正了）→ 丢掉自己这块，仍返回成功。' + R(L1B, 640, 648),
                  '转正后通知监听者（LRU、L2 写入控制器）；临时对象不通知。' + R(L1B, 650, 658)],
                 [note('注释', '丢掉自己也报成功：反正数据已经在 L1 里了。', q=Q_EITHER, ref=A(L1B, 628)),
                  note('推断', '先验锁再解锁：过期的预留可能已被别的写者接手，顺序反了会把别人正在写的 buffer 转正。')],
                 ['写失败时走的不是这条线：传输模块调 `abort_write`，在同一个临界区里解锁并丢弃，不等 600 s。']),
             to='l1.write'),
        wire('w_rd', 'xfer', 'objects', 'unsafe_read · 放锁', ml='读·放锁', fs='t', ts='t', mlp=[262, 300],
             why='锁早已上好，读时不再加', trap='unsafe：不证明锁是你的',
             card=card(
                 'RETRIEVE：传输模块按 key 直接取常驻对象（不加锁），CPU→GPU 拷完，流回调把每个 key 的读锁减 1。'
                 + R(SM, 710, 725) + R(SM, 1424, 1440),
                 ['`unsafe_read`：对象不在 → `KEY_NOT_EXIST`；在但锁无效 → `KEY_NOT_READABLE`；否则交出对象。' + R(L1B, 366, 376),
                  '放锁 `finish_read`：先查 `is_locked()`；已过期就**不减**，返回 `KEY_IN_WRONG_STATE` 并告警；否则减 n 把。'
                  + R(L1B, 426, 439)],
                 [note('注释', '读已被锁住的对象，不加新锁；调用方自己保证夹在上锁和放锁之间。', q=Q_UNSAFE, ref=A(L1B, 347)),
                  note('注释', '锁过期后放锁：读者可能读到了不一致的数据，所以报错而不是默默减。', q=Q_INCONS, ref=A(L1B, 406, 407))],
                 ['放锁**不刷新** LRU：用完不等于有用。刷新来自 LOOKUP 收尾时的 touch。' + R(EVP, 196),
                  '取回晚于读锁过期：`unsafe_read` 失败 → 这次 RETRIEVE 失败 → vLLM 自己重算。' + R(LDT, 1333, 1336)]),
             to='l1.read'),
        wire('w_sub', 'look', 'pc', '预取任务', why='一串 key 一次交', mfs='t', mts='b',
             card=card(
                 '查找模块把 B 的 4 个 key 打成一个任务交给预取控制器；没有 L2 时，函数返回时结果已经算好。' + R(PC, 714, 724),
                 '门面先分一个请求号；没有 L2 时直接在调用线程上走「上锁 → 规划 → 收尾」。' + R(PC, 718, 723),
                 [note('推断', '没有 L2 就没有要等的 I/O，同步做完最省事：第一次 QUERY 通常就有结果。')])),
        wire('w_rl', 'pc', 'objects', 'reserve_read ×n', ml='上读锁', why='看见的同时锁住',
             trap='staging 当作不存在', mfs='t', mts='b',
             card=card(
                 '对每个在常驻表里的 key，读锁 `lock()` n 次（n = 读者数），返回对象；不在的报 `KEY_NOT_EXIST`。' + R(L1B, 325, 337),
                 ['B：h0、h1、h2 各锁 1 次；h3′ 不在 → 不存在。',
                  '每个 key 各自判断，不是全有或全无；原子的只是「别的线程看不到中间态」。' + R(L1B, 191),
                  'n 一次最多 128，避免一次调用占全局锁太久。' + R(L1B, 39, 41)],
                 [note('注释', 'n = 每个要读这个 key 的 worker 一把（例如 MLA 模型 TP>1）。', q=Q_READERS, ref=A(L1B, 306, 309)),
                  note('提交', '「一次上 n 把」来自修 MLA 多 TP 读失败的提交。',
                       q='Fix to support mla multiple tp failed to read issue', ref='c:a973aa8e'),
                  note('注释', '锁是匿名计数，所以服务端要精确记下「这次锁了哪些」，不能事后猜着放。', q=Q_ANON, ref=A(LK, 554))],
                 ['只在暂存表里的 key（还在写）对读者就是不存在。' + R(L1B, 318, 320)]),
             to='l1.prefetch'),
        wire('w_ntf', 'objects', 'evict', '通知 LRU', ml='通知', cls='dash', mlp=[284, 662], why='LRU 只听事件', trap='放锁不算用过',
             mfs='b', mts='t',
             card=card(
                 'L1 每次变化都通知监听者；淘汰策略的桥接器把事件翻成 LRU 的「登记 / 挪到末尾 / 移除」。' + R(EVP, 175, 215),
                 ['预留 → 登记（`on_keys_reserved`，LRU 当作新建）；转正 → `on_keys_created`；`touch_keys` → `on_keys_touched`；'
                  '删除 → `on_keys_removed`。' + R(EVP, 200, 215) + R(EVP, 103, 112),
                  'touch 来自两处：预取收尾时对命中的 key；请求结束 `END_SESSION` 时对它存过、取过的全部 key。'
                  + R(PC, 1730, 1733) + R(LK, 640, 646)],
                 [note('注释', '从预留那一刻就登记：被遗弃的预留（写锁过期）也能像普通 key 一样被淘汰。', q=Q_ABANDON, ref=A(EVP, 202)),
                  note('文档', 'LRU 与锁解耦：上锁、放锁都不刷新新旧顺序。', q=Q_DECOUPLE, ref=A(PCDOC, 53))],
                 [note('注释', '放读锁是空操作：放锁不代表这个 key 有用。', q=Q_NOOP, ref=A(EVP, 196))])),
        wire('w_del', 'evict', 'objects', 'delete（再查锁）', ml='删除', mlp=[222, 705], why='挑时快查，删时复查',
             trap='漏删的下一秒再来', mfs='t', mts='b',
             card=card(
                 '对挑出的 key 调 `delete`：在全局锁里再看一遍——活的预留或有效读锁 → `KEY_IS_LOCKED`，不删；否则删掉、内存还给分配器。'
                 + R(L1B, 787, 807) + R(EC, 192, 198),
                 ['写锁过期的暂存对象也在这里回收（`_reclaim_staging`）。' + R(L1B, 793, 802),
                  '第一道门 `is_key_evictable`：常驻且读锁无效，或暂存且写锁已过期。' + R(L1B, 983, 992)],
                 [note('注释', '挑选时的检查不拿全局锁，图快。', q=Q_NOGLOBAL, ref=A(L1B, 972)),
                  note('注释', '删除时再查一次：挑完到删之间被锁上的 key 会被安全拒掉。', q=Q_RECHECK, ref=A(L1B, 973, 974))],
                 ['淘汰线程不看 `delete` 的返回值：被拒的这轮就不删了。']),
             to='l1.evict'),
        wire('w_tick', 'evict', 'evict', '每 1 秒', cls='dash', fs='b', mfs='b', why='轮询，不被写入叫醒',
             card=card(
                 '淘汰循环：睡 1 秒 → 取（已用，总量）→ 低于 80% 就跳过 → 否则挑一批删。' + R(EC, 164, 190),
                 ['总量是分配器「当前已 pin 的堆」；例子 L1 = 20 GiB，一开始就是满额堆。' + R(L1B, 1086, 1087),
                  '线程是守护线程（`daemon=True`）。' + R(EC, 45, 50)],
                 [note('推断', '睡 1 秒、OOM 不叫醒它，这两点仓库里都没写理由；看得出的好处是写路径上从不等淘汰。')])),
    ]

    steps = [
        beat('A 申请 3 个暂存对象',
             '接顶层：A 的 STORE 要存 `[h0, h1, h2]`。门面以 tag `"storage_manager"` 调 `reserve_write`。逐个检查：都不在常驻表、'
             '也没有同 tag 的活预留 → 3 个都要分配。**一次**要 3 块 × 36 MiB = **108 MiB**。每块配两把新锁，只锁写锁'
             '（计数 1，600 s 后过期），放进暂存表。最后通知监听者：LRU 从这一刻就登记这 3 个 key。',
             note('注释', '暂存对象读者看不见：GPU 还在往里拷时，谁也读不到半截数据。', q=Q_STG, ref=A(L1B, 151, 154)),
             focus=['staging', 'w_rsv', 'xfer', 'w_ntf', 'evict'],
             state={'staging': G_STG_A, 'xfer': g_list(['STORE A：拷贝中…', 'RETRIEVE B：还没来'], on=0),
                    'evict': G_EV_A}),
        beat('A2 同时也来存这 3 块',
             '设：另一个请求 A2 几乎同时 STORE 同样的 `[0, 768)`。它的 `reserve_write`：h0 不在常驻表，但 `_staging[h0]'
             '["storage_manager"]` 在、写锁有效 → `KEY_NOT_WRITABLE`；h1、h2 一样。要分配的为空 → **直接返回**，不分配、不通知。'
             'A2 拿到空结果，拷贝全部跳过，不报错——这 3 块只存 A 那一份。',
             note('注释', '同一个 tag 让同 key 的并发写入互斥；不是靠覆盖，而是后来的直接缺席。', q=Q_TAG, ref=A(SM, 100)),
             focus=['staging', 'w_rsv', 'xfer'],
             state={'xfer': g_list(['STORE A：拷贝中…', 'STORE A2：0 块（被拒）'], on=1)}),
        beat('A 拷完：转正',
             'GPU 拷完，回调调 `finish_write`：对每个 key 先确认写锁还有效（没过 600 s）→ 解锁、从暂存表弹出 → `_objects[h]` = '
             '**同一个对象**（内存不动，只换表）。读锁计数 0 = ready，读者从此查得到。通知 LRU：按倒序挪到末尾，名单是 '
             '`h2, h1, h0`，h0 最新。',
             note('推断', '先验写锁再换表：过期的预留可能已被别人接手，验过才能确定转正的是自己写的那块。'),
             focus=['objects', 'w_fin', 'staging', 'w_ntf', 'evict'],
             state={'staging': G_STG0, 'objects': G_OBJ_A,
                    'xfer': g_list(['STORE A：完成 ✓', 'RETRIEVE B：还没来'], on=0)}),
        beat('B 来查：查到就上锁',
             '接顶层：B 的 LOOKUP 交来 `[h0, h1, h2, h3′]`、读者数 1。没有 L2，预取控制器同步做。① `reserve_read`：h0、h1、h2 '
             '在常驻表 → 读锁各 `lock()` 1 次；h3′ 不在 → `KEY_NOT_EXIST`。设此刻 t=0：h0 的读锁计数 0→**1**，到期 **t=300**'
             '（读 TTL 默认 300 s）。',
             note('文档', '看见即锁住：报出去的命中不会被并发的淘汰作废。', q=Q_OBS, ref=A(PCDOC, 29)),
             focus=['pc', 'w_rl', 'objects', 'w_sub', 'look'],
             state={'objects': G_OBJ_B, 'pc': g_checks([('① 上锁 3/4', 'ok'), ('② 按前缀留', None), ('③ 收尾', None)])}),
        beat('按前缀留，刷新 LRU，交出 768',
             '② 前缀策略：h0、h1、h2 连续 → 3 格都留，**一把锁也不放**（对照：若 h1 不在，h2 的锁在这里当场放掉）。'
             '③ 收尾：`LOCK` 模式保留 3 把锁，记下「这 3 个 key 归这个 L1」；对它们 `touch_keys`，LRU 里仍是 `h2, h1, h0` 排在末尾。'
             '结果放进完成表，调度器 QUERY 一次取走：命中 3 块 = **768** token。',
             note('推断', '锁留到 RETRIEVE 拷完才放：从这里到取回，中间隔着几个调度步。'),
             focus=['pc', 'w_ntf', 'evict', 'objects'],
             state={'pc': g_checks([('① 上锁 3/4', 'ok'), ('② 留 3 格', 'ok'), ('③ 收尾 · 768', 'ok')]),
                    'look': g_kv([('B 命中', '3 块 = 768'), ('读者数', '1（TP=1）')])}),
        beat('设：L1 满到 80%，淘汰线程来了',
             '设：B 在 vLLM 队里等着，这期间别的请求又存进 453 个对象 → 共 **456** 个 × 36 MiB = 16416 MiB ≥ 16384（80%）。'
             '下一秒淘汰线程醒来：目标 `int(456 × 0.2)` = **91** 个。从最旧的一端扫：h2、h1、h0 读锁有效 → 跳过，**不占名额**；'
             '接着取 x1…x91。`delete` 在全局锁里再查一次，没锁就删、还内存。剩 365 个 ≈ 64%。',
             note('注释', '挑的时候不拿全局锁图快，删的时候再查一次保证对。', q=Q_RECHECK, ref=A(L1B, 973, 974)),
             focus=['evict', 'w_del', 'objects', 'w_tick'],
             state={'evict': g_list(['用量 80.2% ≥ 80%', 'h2 h1 h0：锁着，跳过', 'x1…x91：删', 'x92…x453：留'], on=[1, 2]),
                    'objects': stg([('h0 h1 h2', '读锁 1 · 到 t=300'), ('x1…x91', '已删'), ('x92…x453', '读锁 0')])}),
        beat('B 取回：读，拷完放锁',
             '设 t=90（还没到 300）：RETRIEVE 调 `unsafe_read([h0, h1, h2])`：对象在、锁有效 → 交出对象，**不加锁**。CPU→GPU 拷完，'
             '回调放锁：先确认锁有效，再减 1——计数 1→**0**，3 个对象回到 ready。放锁不刷新 LRU；下一轮淘汰它们就能被挑了。',
             note('注释', '读锁释放不代表这个 key 有用，所以 LRU 不动。', q=Q_NOOP, ref=A(EVP, 196)),
             focus=['w_rd', 'objects', 'xfer'],
             state={'xfer': g_list(['STORE A：完成 ✓', 'RETRIEVE B：3 块 ✓'], on=1),
                    'objects': stg([('h0 h1 h2', '读锁 0 · ready'), ('x1…x91', '已删'), ('x92…x453', '读锁 0')])}),
        beat('另一种结局：B 查完就崩了',
             '倒回第 5 步之后：B 的客户端在 LOOKUP 之后崩了，没人放锁，计数停在 1。t=300 起 `is_locked()` 为假（计数 > 0 但已过期）→ '
             '`is_key_evictable` 为真，LRU 扫到就删。晚到的放锁：不减计数，返回 `KEY_IN_WRONG_STATE`；晚到的取回：`unsafe_read` 失败，'
             'vLLM 重算。之后若有人再锁 h0：已过期 → 计数**直接重置为 1**，残留不会累积。',
             note('注释', '过期后下一次上锁从 1 开始，所以崩溃留下的计数不会越积越多。', q=Q_RESET, ref=A(TTLH, 36)),
             focus=['objects', 'evict', 'w_del'],
             state={'objects': stg([('h0 h1 h2', '计数 1 · 已过期'), ('现在', 't=301'), ('能删吗', '能')]),
                    'xfer': g_list(['STORE A：完成 ✓', 'RETRIEVE B：没来'], on=1),
                    'evict': g_list(['h2 h1 h0：可删', '锁过期 = 没锁'], on=0)}),
    ]

    groups = [group('StorageManager 里的 L1 一侧', [312, 70, 902, 448], m=[0, 186, 340, 768])]

    return node(
        'l1', 'L1 存储里面', 'L1 存储：两张表、两把带过期的锁、一个后台淘汰线程',
        '顶层「L1 存储」框里面：对象先进暂存表、拷完转正进常驻表；查到就上读锁，锁着的淘汰线程跳过。',
        lead='你在顶层「L1 存储」框里面。这个框是**两张表 + 每个对象两把带过期时间的锁 + 一个每秒醒一次的淘汰线程**。'
             '对象的「状态」不是一个字段，而是「它在哪张表 + 锁现在有没有效」。按 ▶ 接着顶层的例子走：A 存 h0 h1 h2，B 查中并取回。',
        src=A(L1B, 142, 231),
        blocks=[scene(1220, 520, parts, wires, steps, mw=340, mh=1110, groups=groups,
                      cap='L1 存储内部 · 例子沿用顶层：对象 36 MiB，L1 = 20 GiB，TP=1（读者数 1），读锁 300 s、写锁 600 s')],
        children=[_write_src(), _read_src(), _prefetch_src(), _evict_src()])


# ---------------------------------------------------------------- 源码页
def _write_src():
    return node(
        'l1.write', '写入：预留与转正', 'reserve_write / finish_write：进暂存表、整批分配、验锁转正',
        '图上「暂存表」和「转正」那条线的源码：先看对象长什么样，再看它怎么进表、怎么换表。',
        lead='你在「L1 存储」图的「暂存表」里面。先看被操作的数据（一个对象的五个字段、两张表），再看 `reserve_write` 怎么把 A 的 '
             '3 个 key 放进暂存表、`finish_write` 怎么把它们换到常驻表。',
        src=A(L1B, 522, 658),
        blocks=[
            code(L1B, [(79, 97)], notes=[
                at((85, 86), None, '真正装 KV 的那块 CPU 内存。例子里一块 36 MiB = 37,748,736 B，正好是 4096 的整数倍，对齐不浪费。',
                   has='memory_obj: MemoryObj'),
                at((88, 92), None, '每个对象两把锁：写锁只在暂存期间锁着；读锁是「正被几个读者用着」的计数。A 刚预留时：写锁计数 1、读锁计数 0。',
                   has='write_lock: TTLLock'),
                at((94, 95), None, '临时对象（主路的引擎写入都不是）：读完即删，转正时也不通知监听者。', has='is_temporary'),
                at(97, '提交', '记下预留时记的账，转正 / 回收时原样减掉，暂存字节数才不会漂。',
                   q='Record the charge on the staging entry at put and subtract exactly that', ref='c:c01c30ea',
                   has='staging_charge'),
            ], cap='数据：一个对象 L1ObjectState（注意：没有 state 字段）'),
            code(L1B, [(213, 221)], notes=[
                at(213, None, '一把全局锁，两张表的几乎所有读写方法都挂在它上面。', has='threading.Lock()'),
                at((215, 216), None, '常驻表：`key → 对象`。A 转正后是 `{h0: …, h1: …, h2: …}`。', has='self._objects'),
                at((217, 219), None, '暂存表：`key → tag → 对象`，读者看不见。A 预留后是 `{h0: {"storage_manager": …}, …}`。',
                   has='self._staging'),
                at((220, 221), None, '暂存对象占的字节数：A 的 3 块在暂存时是 108 MiB。', has='_staging_bytes'),
            ], cap='数据：两张表放在 L1Manager 的哪里'),
            table(['时刻', '`_staging`', '`_objects`', 'h0 的写锁 / 读锁'],
                  [['预留后', 'h0 h1 h2（tag = storage_manager）', '空', '1（600 s）/ 0'],
                   ['A2 来预留', '不变（A2 被拒）', '空', '不变'],
                   ['转正后', '空', 'h0 h1 h2', '0 / 0'],
                   ['B 查完', '空', 'h0 h1 h2', '0 / 1（到 t=300）']],
                  cap='例子里 A 的 h0 在两张表之间怎么走'),
            code(L1B, [(522, 584)], notes=[
                at((527, 529), None, '已在常驻表 → 不可写。这就是同一块不会存两份的原因（就算有人再存一遍 `[0, 768)`，也在这里被拒）。',
                   has='key in self._objects'),
                at((531, 535), None, '同 tag 已有写锁有效的预留 → 不可写。A2 在这里被拒：A 的写锁还有效。', has='KEY_NOT_WRITABLE'),
                at((536, 547), '注释', '同 tag 的旧预留写锁过期了：把那块 buffer 直接交给新写者，重新上写锁。若旧写者其实没死、只是慢，'
                                     '两边会写同一块；仓库里没有专门讲这种情况的测试或注释。', q=Q_HAND,
                   has='hand the buffer over'),
                at((552, 553), None, '没有要分配的就提前返回，连监听者都不通知。A2 走的就是这里。', has='len(need_to_allocate) == 0'),
                at((555, 565), '注释', '一次要齐 A 的 3 块；拿不齐就 3 个都 `OUT_OF_MEMORY`，已拿到的退回。分配器那边整批失败时空闲链没动过。',
                   q=Q_NOMEM, ref=A(MM, 1289), has='OUT_OF_MEMORY'),
                at((572, 579), None, '两把新锁：写锁 600 s、读锁 300 s；**只锁写锁**，然后放进暂存表。', has='entry.write_lock.lock()'),
                at((583, 584), None, '通知监听者：LRU 从预留这一刻就登记这 3 个 key（被遗弃的预留以后也能被淘汰）。',
                   has='on_l1_keys_reserved_write'),
            ], cap='reserve_write：逐 key 判断 → 整批分配 → 包成对象放进暂存表'),
            code(L1B, [(635, 658), (1189, 1204)], notes=[
                at(636, None, '从暂存表取出这个 tag 的对象（逻辑在下面 1189 起那段）。', has='_take_staging'),
                at((640, 648), '注释', 'key 已经常驻（别的 tag 先转正了）：丢掉自己这块，仍报成功。', q=Q_EITHER, ref=A(L1B, 628),
                   has='already resident'),
                at(649, None, '转正就这一行：同一个对象放进常驻表，内存不搬。', has='self._objects[key] = entry'),
                at((656, 658), None, '通知监听者：LRU 把 h0 h1 h2 按倒序挪到末尾；有 L2 时写入控制器也在听。',
                   has='on_l1_keys_write_finished'),
                at((1193, 1201), '推断', '**先**验写锁还有效：过期了就报 `KEY_IN_WRONG_STATE`，对象留在暂存表等回收。顺序反了，'
                                       '会把已被别人接手的 buffer 转正。', has='write_lock.is_locked()'),
                at((1203, 1204), None, '验过才解锁并弹出。', has='_pop_staging'),
            ], cap='finish_write：验写锁 → 弹出 → 放进常驻表 → 通知'),
        ])


def _read_src():
    return node(
        'l1.read', '读锁与 TTLLock', 'reserve_read / unsafe_read / finish_read，和那把带过期的计数锁',
        '图上「常驻表」的源码：读锁怎么上、读的时候为什么不加锁、怎么放；锁本身只有三个数。',
        lead='你在「L1 存储」图的「常驻表」里面。先看锁本身（C++ 里三个数 + 三条规则），再看 L1 怎么用它：B 的 LOOKUP 上锁、'
             'RETRIEVE 不加锁地读、拷完放锁。',
        src=A(L1B, 322, 446),
        blocks=[
            code(TTLH, [(33, 58), (79, 86)], notes=[
                at((33, 40), '注释', '上锁：旧锁已过期 → 计数重置为 1；否则 +1。两种都把到期刷新成「现在 + TTL」。B 在 t=0 上锁：计数 1，到期 t=300。',
                   q=Q_RESET, has='counter is reset to 1'),
                at((42, 47), None, '放锁：减 1，最低到 0。不检查是谁放的——锁是匿名的。', has='will not go below 0'),
                at((49, 58), None, '有效 = 计数 > 0 **且**没过期。B 崩了不放锁：t=301 时计数仍是 1，但已过期，所以算没锁。',
                   has='TTL has not expired'),
                at((79, 86), None, '整把锁的全部数据：计数、到期时刻（毫秒）、TTL 时长。没有「持有者」字段。', has='counter_'),
            ], cap='数据：TTLLock 的规则和字段（C++ 头文件）'),
            table(['情况', '时刻（设）', '事件', '计数', '到期', '有效？'],
                  [['正常', 't=0', 'B 的 LOOKUP 上锁', '1', 't=300', '是'],
                   ['正常', 't=90', 'B 拷完放锁', '0', 't=300', '否（计数 0）'],
                   ['两个读者', 't=200', 'B 还没放，请求 C 也锁 h0', '2', 't=500', '是'],
                   ['B 崩了', 't=301', '没人放锁', '1', 't=300', '否（过期）'],
                   ['B 崩了', 't=400', '有人再锁 h0', '1（重置）', 't=700', '是']],
                  cap='h0 的读锁在三种情况下的值：每种情况从 t=0 B 上锁开始，各自独立'),
            code(TTLC, [(13, 48), (68, 75)], notes=[
                at((23, 35), None, '过期分支：先用 CAS 改到期时刻，成功了再把计数设成 1；抢输了就重试。', has='expired'),
                at((36, 46), '推断', '没过期分支：CAS 把计数加 1，**再**单独写新的到期时刻——两步不是一个原子操作，极端并发下到期时刻可能被'
                                   '较早的一次写覆盖；影响有限，没有测试专门覆盖。', has='old_counter + 1'),
                at((68, 75), None, '判断有效：同时读计数和到期时刻。', has='current_time < expiration'),
            ], cap='TTLLock 的实现（C++ 原子变量）'),
            code(L1B, [(322, 337)], notes=[
                at(322, '注释', '读者数 n 先校验（最多 128）。', q=Q_MAXRL, ref=A(L1B, 40), has='validate_read_locks'),
                at((326, 329), None, '只查常驻表：只在暂存表里的 key 也报不存在。B 的 h3′ 在这里返回 `KEY_NOT_EXIST`。',
                   has='self._objects.get'),
                at((331, 335), '注释', '一把一把地锁 n 次（Python 循环）。B 的 n = 1。', q='overhead (TTLLock is C++ std::atomic).',
                   has='entry.read_lock.lock()'),
            ], cap='reserve_read：B 的 LOOKUP 给 h0 h1 h2 各上 1 把'),
            code(L1B, [(364, 377)], notes=[
                at((372, 374), '注释', '只检查「现在有锁」，不证明锁是你的——名字里的 unsafe 就是这个意思。', q=Q_UNSAFE, ref=A(L1B, 347),
                   has='is_locked()'),
                at(376, None, '不加锁，直接交出对象：B 的 RETRIEVE 拿到 3 块内存去拷。', has='SUCCESS, entry.memory_obj'),
            ], cap='unsafe_read：RETRIEVE 读已锁住的对象'),
            code(L1B, [(415, 446)], notes=[
                at((426, 433), '注释', '先查有效：锁已过期就不减、报错并告警——这时读者可能读到了不一致的数据。', q=Q_INCONS,
                   ref=A(L1B, 406, 407), has='KEY_IN_WRONG_STATE'),
                at((438, 439), None, '减 n 把：B 拷完后 h0 的计数 1 → 0。', has='entry.read_lock.unlock()'),
                at((440, 443), None, '临时对象计数归零就当场删（读完即焚）；A 的对象不是临时的，留着。', has='entry.is_temporary'),
            ], cap='finish_read：拷完放锁'),
        ])


def _prefetch_src():
    return node(
        'l1.prefetch', '预取：查 + 上锁', 'PrefetchController 的 L1 小路径：上锁 → 按前缀留 → 收尾',
        '图上「预取控制器」的源码：没有 L2 时，B 的 LOOKUP 在一条线程上同步走完三步。',
        lead='你在「L1 存储」图的「预取控制器」里面。先看交进来的任务长什么样，再看三步：上锁、按前缀留（计划外的锁当场放）、'
             '收尾（touch + 交出结果）。例子：B 的 `[h0, h1, h2, h3′]`，读者数 1。',
        src=A(PC, 714, 1754),
        blocks=[
            code(API, [(698, 701), (742, 746)], notes=[
                at(701, None, '每个 key 上几把读锁。TP=1 时是 1。', has='num_kv_readers'),
                at(743, '文档', '默认「前缀」：只要每一行都能连续供给的最长前缀。B 的结果：前 3 格。', q=Q_PREFIXDOC, ref=A(SMDOC, 36),
                   has='"prefix"'),
                at(744, None, '默认 `LOCK`：查到的锁留给调用方，RETRIEVE 拷完再放。', has='PrefetchLockMode.LOCK'),
            ], cap='数据：GroupedObjectKeys 的读者数 + PrefetchTaskSpec 的策略字段'),
            code(PC, [(714, 724)], notes=[
                at((718, 724), None, '没有 L2：在调用线程上同步三连，返回时结果已在完成表里。', has='self._lock_l1_keys(request)'),
            ], cap='submit：没有 L2 时同步做完'),
            code(PC, [(1263, 1294)], notes=[
                at((1273, 1279), None, '① 对每一行调 `reserve_read(keys, 读者数)`。B：h0 h1 h2 成功，h3′ 不存在。', has='reserve_read'),
                at((1280, 1290), None, '成功的格子记进位图 `l1_locked_keys`：B 是 `[1, 1, 1, 0]`。之后放锁严格按它来。', has='l1_locked_keys'),
                at((1291, 1294), '注释', '中途异常：`finally` 里把已上的锁全放掉，不留锁。（不管哪种模式都先上锁。）',
                   q=Q_LOCKMODE, ref=A(PC, 1260), has='_release_all_locks'),
            ], cap='① _lock_l1_keys：查 + 上锁'),
            code(PC, [(1313, 1336)], notes=[
                at((1314, 1321), None, '策略按前缀算计划。B：前 3 格连续 → 计划 = `[1, 1, 1, 0]`。', has='plan_load'),
                at((1323, 1328), None, '已锁 − 计划 = 要放的；放的把数 = 上的把数（读者数）。B 没有要放的；若 h1 不在，h2 的锁就在这里放掉。',
                   has='l1_dropped'),
                at(1334, None, '从此「锁着的」就等于计划。', has='load_plan.l1_planned_keys.copy()'),
            ], cap='② _plan_load：按前缀留，计划外的锁当场放'),
            code(PC, [(1716, 1733), (1748, 1754)], notes=[
                at((1722, 1728), None, '只有 `NO_LOCK`（预热）才在这里放锁；B 是 `LOCK`，锁留着。', has='NO_LOCK'),
                at((1730, 1733), '文档', '对命中的 key touch：LRU 刷新只来自这里这种显式 touch，不来自上锁放锁。', q=Q_DECOUPLE,
                   ref=A(PCDOC, 53), has='touch_keys'),
                at((1748, 1754), None, '`LOCK` 模式记下「每个 key 归哪个 L1」，RETRIEVE 拷完按它放锁。', has='l1_owners'),
            ], cap='③ _finish_request：touch、保留锁、交出结果'),
        ])


def _evict_src():
    return node(
        'l1.evict', '淘汰一圈', '淘汰线程 + LRU：每秒看用量、倒序登记、跳过锁着的、删时复查',
        '图上「淘汰线程 + LRU」的源码：那 456 个对象里删哪 91 个、为什么 h0 h1 h2 没被碰。',
        lead='你在「L1 存储」图的「淘汰线程 + LRU」里面。先看配置和名单长什么样，再看一圈：醒来 → 比水位 → 从旧到新挑（跳过锁着的）→ '
             '删时再查一次。例子：设 L1 有 456 个对象，h0 h1 h2 被 B 锁着。',
        src=A(EC, 160, 198),
        blocks=[
            code(DCFG, [(482, 495)], notes=[
                at((491, 492), None, '触发线 0.8：20 GiB 的 80% = 16384 MiB；第 456 个 36 MiB 对象写入后（16416 MiB）才过线。',
                   has='trigger_watermark'),
                at((494, 495), None, '比例 0.2。注释说是「内存的比例」，LRU 实际按名单里 key 的个数算；同尺寸对象两者一样。',
                   has='eviction_ratio'),
            ], cap='数据：淘汰配置（为什么是 0.8 / 0.2，仓库里没写）'),
            code(LRU, [(52, 53), (83, 92), (107, 110)], notes=[
                at(53, None, '名单就是一个有序字典：开头最旧。例子：`[h2, h1, h0, x1, …, x453]`。', has='OrderedDict'),
                at((83, 85), '注释', '同一请求的后几块应该先删：先删第一块，后面几块因为前缀匹配也命不中了。', q=Q_LATER,
                   has='later keys should be evicted first'),
                at((86, 92), '提交', '所以倒序挪到末尾：h2 先挪、h0 最后挪 → h0 最新、h2 最先被扫到。',
                   q='optimize evict in lru policy', ref='c:bdf4c9a9', has='reversed(keys)'),
                at((107, 110), None, 'touch 也是倒序挪到末尾。', has='move_to_end'),
            ], cap='数据：LRU 名单，以及为什么倒序登记'),
            table(['名单位置（旧 → 新）', 'key', '过滤器', '结果'],
                  [['1–3', 'h2、h1、h0', '读锁有效 → 假', '跳过，不占名额'],
                   ['4–94', 'x1 … x91', '没锁 → 真', '挑中（凑满 91 个）'],
                   ['95–456', 'x92 … x453', '没扫到', '留下']],
                  cap='设：456 个 key，目标 int(456 × 0.2) = 91'),
            code(EC, [(160, 198)], notes=[
                at(165, None, '固定睡 1 秒；全仓没有哪处因为写入 OOM 叫醒它（读代码的结论）。为什么是 1 秒，仓库里没写。', has='time.sleep(1)'),
                at((169, 177), None, '用量 = 已用 / 当前已 pin 的堆。A 存完时 108 MiB / 20 GiB ≈ 0.5%，跳过。', has='usage < watermark'),
                at((184, 187), None, '把 `is_key_evictable` 当过滤器交给策略：挑的时候就跳过锁着的。', has='key_eligible_filter'),
                at((192, 194), None, '只有「丢弃」一种去处；不看 `delete` 的返回值，被拒的下一秒再说。', has='self._l1_manager.delete'),
            ], cap='淘汰循环：醒来 → 比水位 → 挑 → 删'),
            code(LRU, [(160, 199)], notes=[
                at((168, 172), None, '目标按 key 个数算：`int(456 × 0.2)` = 91；至少 1 个。', has='target_count = int'),
                at((181, 188), '提交', '从旧到新扫，过滤器为假就 `continue`：h2 h1 h0 被跳过且不占名额，接着凑满 91 个。',
                   q='Skip locked keys during LRU eviction to improve eviction efficiency', ref='c:cb8b1c04',
                   has='continue'),
            ], cap='LRU 挑人'),
            code(L1B, [(969, 992), (787, 807)], notes=[
                at((972, 974), '注释', '第一道门不拿全局锁，删除时会再查一次。', q=Q_NOGLOBAL, has='does NOT acquire'),
                at((983, 985), None, '常驻且读锁无效 → 可删。h0 在 t<300 时读锁有效 → 不可删；B 崩了、t=301 时 → 可删。',
                   has='not entry.read_lock.is_locked()'),
                at((986, 992), None, '或者：暂存且写锁已过期（被遗弃的预留）→ 可删。', has='write_lock.is_locked()'),
                at((793, 797), None, '第二道门（在全局锁里）：先回收过期的预留；还有活的预留 → `KEY_IS_LOCKED`。', has='_reclaim_staging'),
                at((804, 807), None, '读锁有效 → `KEY_IS_LOCKED`，不删。挑完到删之间刚被锁上的 key 在这里被拦下。',
                   has='locked and not force'),
            ], cap='两道门：is_key_evictable（快查）和 delete 里的复查'),
        ])


def nodes():
    return {'l1': _scene_node()}
