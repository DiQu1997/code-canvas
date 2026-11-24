from cv import *

L1B = 'lmcache/v1/distributed/l1_manager/base.py'
SM = 'lmcache/v1/distributed/storage_manager.py'
PC = 'lmcache/v1/distributed/storage_controllers/prefetch_controller.py'
EC = 'lmcache/v1/distributed/storage_controllers/eviction_controller.py'
LRU = 'lmcache/v1/distributed/eviction_policy/lru.py'
EVB = 'lmcache/v1/distributed/eviction.py'
DCFG = 'lmcache/v1/distributed/config.py'
L1MM = 'lmcache/v1/distributed/memory_manager/l1_memory_manager.py'
MM = 'lmcache/v1/memory_management.py'
TTLC = 'csrc/lmcache_native/ttl_lock.cpp'
TTLH = 'csrc/lmcache_native/ttl_lock.h'
LDT = 'lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py'
LK = 'lmcache/v1/multiprocess/modules/lookup.py'
STAGEDOC = 'docs/design/v1/distributed/l1_manager/staging-and-write-tags.md'
PCDOC = 'docs/design/v1/distributed/storage_controllers/prefetch_controller.md'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 引文（逐字照抄）
Q_STAGING = 'Staging objects are kept apart from the resident objects: readers of the key do'
Q_TAG = 'Sharing one'
Q_TAG2 = 'tag makes concurrent stores of the same key exclude each other.'
Q_DISCARD = 'is discarded, the resident object is kept and ``SUCCESS`` is'
Q_BEFORE = 'the second reservation failed, the prefetch controller'
Q_DEDUP = 'which keeps the de-duplication engine stores rely on;'
Q_UNSAFE = 'the caller need'
Q_OBS = 'so a concurrent eviction can shrink what gets discovered but never'
Q_LRUDEC = 'Taking and releasing read locks never'
Q_EXPIRE_H = 'If the previous TTL has expired, the counter is reset to 1.'
Q_REFRESH_H = 'The TTL is always refreshed to the current time + TTL duration.'
Q_MAXLOCK = 'to prevent a single call from holding the global lock for too long.'
Q_MLA = 'for the same key (e.g. MLA models with'
Q_NOFREE = 'Not enough memory; free list is untouched, no rollback needed'
Q_LATER = 'NOTE: for the request, the later keys should be evicted first.'
Q_PREFIX = 'evict key1, due to prefix match, key2 and key3 will not be hit.'
Q_SKIP = 'This is useful for skipping locked keys that'
Q_RECHECK = '``delete()`` will check again and safely reject a key'
Q_RESERVED = 'Track keys from the moment they are reserved, so an abandoned'
Q_NOOP = 'No-op: releasing a read lock does not mean the key was useful.'
Q_ATOMIC = 'For every operation on list of keys, the operation is atomic'
Q_LOCKALL = 'Locks are taken regardless of the lock mode; under ``NO_LOCK``'


# ---------------------------------------------------------------- 小图（同一套数）
# 例子：L1 20 GiB = 20480 MiB，一个对象 36 MiB → 568 个槽；淘汰红线 80% = 16384 MiB。
# A 存 h0 h1 h2；B 在 t0 LOOKUP h0 h1 h2 h3′；C 同前缀；B 崩溃；456 个对象触发淘汰；B 缺口（h1 被淘汰）。
def g_sm(on):
    return g_list(['写 reserve / finish_write', '查 submit_prefetch', '取 unsafe / finish_read'], on=on)


def g_pool(used):
    return g_text(str(used), '/ 568 槽已用')


def g_use(n):
    return g_gauge(n * 36, 16384, total=20480, label='用量（%d 个）' % n, unit=' MiB')


def g_lock(cnt, exp, locked):
    return g_kv([('计数', cnt), ('到期', exp), ('锁着', locked)])


G_SM0 = g_sm(None)
G_STG0 = g_text('空', '没有正在写的对象')
G_OBJ0 = g_text('空', '还没有可读的对象')
G_LOCK0 = g_lock('0', '—', '否')
G_POOL0 = g_pool(0)
G_PF0 = g_text('空闲', '没有在查的请求')
G_LRU0 = g_text('空', '还没跟踪任何 key')
G_EV0 = g_use(0)


def nodes():
    return {'l1': _l1()}


def _l1():
    parts = [
        # ------------------------------------------------ 入口
        part('sm', '门面', [16, 190, 190, 150], m=[70, 10, 200, 120], code='StorageManager', kind='ext',
             glyph=G_SM0,
             say='翻译调用，写入统一 tag', why='同 tag 互斥 = 去重', trap='它自己不存对象',
             card=card(
                 '框外的人（传输模块、查找模块）只跟它说话。它把外面的调用翻成对下面几块的调用：'
                 '写交给 L1 管理器，查交给预取控制器，读和放锁再交回 L1 管理器。',
                 ['写：`reserve_write` 先让写入策略挑 L1（主路只有一个），再以固定 tag `"storage_manager"` 调 L1 的 '
                  '`reserve_write`；没拿到对象的 key 从结果里剔掉。' + R(SM, 314, 328),
                  '查：`submit_prefetch_task` 只是转给预取控制器，再包一个句柄返回。' + R(SM, 584) + R(SM, 596, 602),
                  '取：RETRIEVE 拷完的回调 `finish_read_by_owner` 每个 key 放 1 把读锁。' + R(SM, 1424, 1437),
                  '它还在构造时给每个 L1 建一个淘汰控制器并启动后台线程。' + R(SM, 160, 166)],
                 [note('注释', '所有引擎写入共用一个 tag：两个请求同时存同一个 key，后到的会被拒。',
                       q=Q_TAG2, ref=A(SM, 99, 101)),
                  note('推断', '门面只翻译不存状态：对象和锁只有 L1 管理器一处在管，规则不会分散在几个类里。')],
                 ['它不碰对象表，也不做「锁哪些」的决定：前者归 L1 管理器，后者归预取控制器。',
                  'OOM 的 key 它会发一个 `L1_ALLOCATION_FAILED` 事件（计入指标），但不重试。' + R(SM, 341, 348)])),

        # ------------------------------------------------ 内存池
        part('pool', '内存池', [16, 12, 190, 140], m=[242, 570, 96, 170], code='L1MemoryManager', kind='store',
             glyph=G_POOL0,
             say='凑不齐就整批失败', why='空闲表不动', trap='满了不先挤旧',
             card=card(
                 '给对象切 CPU 固定内存的地方。例子里 20 GiB，一个对象 36 MiB，能放 **568** 个。',
                 ['池里是一张按地址排序的空闲块表。一批要 n 个同尺寸对象：遍历空闲块尽量多切，**凑不齐 n 个就整批失败**。'
                  + R(MM, 1265, 1299),
                  '失败被翻成 `OUT_OF_MEMORY`，L1 管理器给这一批**每个** key 都标 OOM。' + R(L1MM, 136, 141) + R(L1B, 559, 565),
                  '「用量」= 堆大小 − 空闲大小，淘汰线程拿它和红线比。' + R(L1MM, 186, 190),
                  '默认是懒增长的池：先固定 init_size（默认 20 GiB，且不超过总大小），例子里 20 = 20，没有后台增长。'
                  + R(DCFG, 293)],
                 [note('注释', '凑不齐时空闲表原样不动，不用回滚。', q=Q_NOFREE, ref=A(MM, 1289)),
                  note('推断', '一批一次分配：一次 STORE 的几块要么都有地方、要么都没有，不会留下一半已占内存的暂存对象。')],
                 ['满了**不会**先挤掉旧的再写：没有「分配失败时同步淘汰」，只能等淘汰线程下一次心跳。',
                  'A 要存 3 块、只剩 2 个槽 → 3 块**全都** OOM，不是存前 2 块。',
                  note('观点', '按前缀语义，其实可以只存前 2 块：前 2 块对下一个请求仍然有用。')]),
             to='l1.write'),

        # ------------------------------------------------ 暂存表
        part('staging', '暂存表', [316, 12, 240, 150], m=[226, 210, 112, 180], code='_staging', kind='store',
             n='1', glyph=G_STG0,
             say='写入中，读者看不见', why='读不到半截', trap='是另一张表',
             card=card(
                 '正在被写的对象放在这里，按 `key → tag → 对象` 存。拷贝还没完成的数据只存在这张表里，**任何读者都看不见**。',
                 ['`reserve_write` 对每个 key 过三道门：已常驻 → 拒；同 tag 已在暂存且写锁活着 → 拒；'
                  '同 tag 暂存的写锁**已过期** → 不再分配，把那块内存直接交给新写者。' + R(L1B, 526, 547),
                  '过了门的 key 凑成一批**一次**向内存池要；成功就给每个建两把锁（写锁 600 s、读锁 300 s），只给写锁上锁，'
                  '放进暂存表。' + R(L1B, 555, 581),
                  '建好立刻通知监听者：LRU 从这一刻就开始跟踪这些 key。' + R(L1B, 583, 584)],
                 [note('注释', '暂存对象和常驻对象分开放：读这个 key 的人看不见暂存对象；不同 tag 的写者可以同时暂存同一个 key。',
                       q=Q_STAGING, ref=A(L1B, 151, 154)),
                  note('文档', '以前 `reserve_write` 直接在常驻表里建写锁定的对象，别人立刻看得到：两个预取同时装同一个对象，'
                              '第二个失败、整单放弃，把几秒后就会到的块报成 miss。',
                       q=Q_BEFORE, ref=A(STAGEDOC, 11, 13)),
                  note('提交', '引入暂存表的提交，标题就是「避免写写冲突」。',
                       q="Update L1Manager's reserve_write semantics to avoid write-write conflict", ref='c:2848bfec')],
                 ['容易把它想成「常驻表里一个标着写入中的对象」。不是：是**两张表**，转正就是从这张表摘出、放进那张表。',
                  '别人此时 `reserve_read(h0)` 得到的是 `KEY_NOT_EXIST`，不是「正在写，请稍等」。' + R(L1B, 318, 320)]),
             to='l1.write'),

        # ------------------------------------------------ 常驻表
        part('objects', '常驻表', [666, 180, 260, 155], m=[2, 460, 210, 170], code='_objects', kind='store',
             n='2', glyph=G_OBJ0,
             say='拷完的对象，读者只查它', why='只有拷完的才在这里', trap='锁计数不是「谁在用」的名单',
             card=card(
                 '真正「存下来」的对象。读、加读锁、放读锁、淘汰删除，都只看这张表。',
                 ['转正只有一行：`self._objects[key] = entry`。' + R(L1B, 649),
                  '读三件套全在这张表上：`reserve_read` 查到就给读锁计数 +n；`unsafe_read` 只取已锁的。' + R(L1B, 325, 336) + R(L1B, 366, 376),
                  '`finish_read` 计数 −n。' + R(L1B, 415, 439),
                  '每个对象带两把锁，读锁在转正后才派上用场；写锁在转正时已经放掉。' + R(L1B, 1203, 1204),
                  '所有对 key 列表的操作都在一把全局互斥锁里做完，整批原子。' + R(L1B, 191)],
                 [note('推断', '读路径只查这张表，暂存表永远读不到：所以读者不会读到拷了一半的数据。'),
                  note('注释', '对象表的每个批量操作是原子的：没人看得到「暂存已摘出、还没进常驻」的中间态。',
                       q=Q_ATOMIC, ref=A(L1B, 191))],
                 ['读锁只是一个**数字**，不记是谁加的。放锁时只减数，所以加几把、放几把必须对得上。',
                  '对已常驻的 key 再 `reserve_write`（同 tag）会被拒；`finish_write` 发现已常驻时**丢掉自己的、仍返回 SUCCESS**。'
                  + R(L1B, 640, 648)]),
             to='l1.read'),

        # ------------------------------------------------ 读锁
        part('rlock', '一把读锁', [1036, 12, 168, 150], m=[230, 800, 108, 150], code='TTLLock', kind='gate',
             glyph=G_LOCK0,
             say='计数 + 到期时间', why='崩了也会自己过期', trap='到期不需要谁解锁',
             card=card(
                 '常驻表里每个对象都带一把（图上画的是 h0 那把）。它不是互斥锁，而是一张**租约**：一个计数 + 一个到期时间。'
                 '「锁着」= 计数 > 0 **且** 没到期。',
                 ['加锁：计数 +1，并把到期时间刷新成「现在 + 300 s」；若上一轮已经过期，计数不累加，直接**重置为 1**。',
                  '放锁：计数 −1，最低到 0，多放不会变负。',
                  '用 C++ 原子变量实现，Python 侧逐把调用；一次最多 128 把。' + R(L1B, 331, 335) + R(L1B, 39, 41),
                  '默认读锁 300 s、写锁 600 s，可用 `--l1-read-ttl-seconds` / `--l1-write-ttl-seconds` 改。' + R(DCFG, 429, 433)],
                 [note('注释', '过期后再加锁，计数从 1 重新开始。', q=Q_EXPIRE_H, ref=A(TTLH, 36)),
                  note('注释', '读锁要按 worker 数加：同一个 key 可能被多个 worker 各取一次（例如 TP>1 的 MLA 模型）。',
                       q=Q_MLA, ref=A(L1B, 307, 309)),
                  note('推断', '带到期时间是为了客户端崩了也不会把对象永远锁死（MP 文档 docs/source/mp/index.rst 第 370–371 行有此说法；舞台不收 .rst 锚点）。')],
                 ['到期**不需要任何人调 unlock**：计数还是 1，只是被到期时间屏蔽了，`is_locked()` 直接变假。',
                  '最后一个加锁的人决定整把锁何时到期（每次加锁都刷新）。',
                  note('推断', '一个没测到的交错：到期后别的读者 D 加锁把计数重置为 1；慢了的 B 再放锁，减掉的是 D 那一把。')]),
             to='l1.ttl'),

        # ------------------------------------------------ 预取控制器
        part('prefetch', '预取控制器', [316, 380, 240, 136], m=[2, 210, 112, 180], code='PrefetchController',
             kind='gate', n='3', glyph=G_PF0,
             say='见到就锁，再按前缀裁', why='探测即占有', trap='锁了不一定留',
             card=card(
                 'LOOKUP 进 L1 后由它决定「锁哪些、留哪些」。主路没有 L2，它就是在调用线程上同步跑完的三步：锁 → 规划 → 收尾。'
                 + R(PC, 718, 724),
                 ['锁：对每个 L1、每一行，一次批量 `reserve_read(keys, 读者数)`，成功的格子记进「已锁」位图。' + R(PC, 1273, 1289),
                  '规划：策略按**前缀**折叠出该留的格子，不在计划里的读锁**立刻放掉**，再把「已锁」位图改成计划本身。'
                  + R(PC, 1323, 1328) + R(PC, 1334, 1335),
                  '收尾：命中的锁留给调用方（`LOCK` 模式），对命中的 key `touch_keys` 刷新 LRU，发布结果。' + R(PC, 1719, 1733),
                  '它自己记着「我锁了哪些格子」，任何一步失败都能整表放锁。' + R(PC, 1292, 1294)],
                 [note('文档', '查和锁是同一个动作：被发现的块从那一刻起就锁着，并发的淘汰只能让发现得少，不能让已报的命中失效。',
                       q=Q_OBS, ref=A(PCDOC, 31, 33)),
                  note('推断', '如果先问「在不在」再去锁，两步之间块可能刚好被淘汰；用 `reserve_read` 当探测，成功就是「在且已锁」。')],
                 ['第一步是**见到就锁**，不管是不是前缀：B 缺口时 h0、h2 都被锁，裁剪后只留 h0，h2 的锁当场放掉。',
                  note('文档', '加锁、放锁都不刷新 LRU 次序，只有显式 touch 刷新。', q=Q_LRUDEC, ref=A(PCDOC, 53, 54))]),
             to='l1.prefetch'),

        # ------------------------------------------------ LRU 次序表
        part('lru', 'LRU 次序表', [1036, 386, 168, 132], m=[222, 1020, 116, 150], code='LRUEvictionPolicy',
             kind='store', glyph=G_LRU0,
             say='队首最旧，按序挑', why='同请求后面的块先走', trap='加锁不算「用过」',
             card=card(
                 '记着每个 key 最近一次「被用」的先后。队首最久没用，淘汰从队首挑。',
                 ['一个有序字典，队首最旧。' + R(LRU, 52, 53),
                  '它从 L1 管理器的监听回调里得知变化：保留写 → 开始跟踪；转正 → 移到最新端；`touch_keys` → 移到最新端；删除 → 摘掉。'
                  + R(EVB, 191, 215),
                  '新建 / 触碰时把 key 列表**倒过来**逐个放到最新端：A 的 `[h0, h1, h2]` 放完，队尾是 `…, h2, h1, h0`。'
                  + R(LRU, 83, 92)],
                 [note('注释', '同一个请求的几块，后面的先淘汰。', q=Q_LATER, ref=A(LRU, 83)),
                  note('注释', '因为只认前缀：先删 h0，h1、h2 还在也永远命不中。', q=Q_PREFIX, ref=A(LRU, 85)),
                  note('注释', '从保留那一刻就跟踪：写者扔下的暂存（写锁过期）也能像普通 key 一样被淘汰。',
                       q=Q_RESERVED, ref=A(EVB, 201, 202))],
                 [note('注释', '放读锁不算「用过」：次序只由显式 touch 决定。', q=Q_NOOP, ref=A(EVB, 196)),
                  '「最近」以**写完时间或 touch 时间**为准，不是最后一次被读的时间。']),
             to='l1.evict'),

        # ------------------------------------------------ 淘汰线程
        part('evict', '淘汰线程', [666, 392, 260, 124], m=[2, 1020, 140, 150], code='L1EvictionController',
             kind='bg', n='4', glyph=G_EV0,
             say='每秒看用量，过 80% 删 20%', why='挑时过滤，删时复查', trap='分配失败时不会当场淘汰',
             card=card(
                 '一条后台线程，每秒醒一次：看内存用量，过了红线就从 LRU 队首挑一批没锁的对象删掉。',
                 ['循环：睡 1 s → 取用量 / 总量 → 低于 0.8 跳过 → 否则向 LRU 要「该删的 key」→ 逐个 `delete`。' + R(EC, 164, 189),
                  '删几个：`int(跟踪的 key 数 × 0.2)`，至少 1 个。按**个数**算，不是按字节。' + R(LRU, 168, 175),
                  '怎么挑：从队首往后扫，过滤器说「不可淘汰」就跳过，凑够数就停。' + R(LRU, 181, 188),
                  '过滤器是 L1 的 `is_key_evictable`：常驻且读锁没锁着，或者暂存的写锁已过期。' + R(L1B, 983, 992)],
                 [note('注释', '挑的时候跳过锁住的 key：它们反正删不掉。', q=Q_SKIP, ref=A(LRU, 145, 146)),
                  note('注释', '过滤器不拿全局锁；`delete()` 会再查一遍，挑完之后才被锁上的 key 照样会被拒。',
                       q=Q_RECHECK, ref=A(L1B, 973, 974)),
                  note('推断', '过滤器若也拿 L1 全局锁会和监听回调反序互等（L1 锁 → LRU 锁 vs LRU 锁 → L1 锁）。源码只写了「不拿锁、靠复查」。')],
                 ['淘汰**只**由这个 1 s 定时器触发；`reserve_write` 拿到 OOM 时不会当场腾地方。',
                  '配置说明写「淘汰已分配内存的比例」，实现按 key 个数算；对象一样大时两者相同。' + R(DCFG, 495) + R(LRU, 168),
                  note('推断', '如果池里几乎全是被读锁钉住的对象，过滤器会把它们全跳过，一个也删不掉，OOM 会持续到锁放掉或过期。')]),
             to='l1.evict'),
    ]

    wires = [
        wire('w_write', 'sm', 'staging', '申请写入', ml='申请写', n='1', why='同 tag 互斥',
             trap='被拒不占内存',
             card=card(
                 '传输模块的 STORE 经门面进来，用 `reserve_write` 申请几个「写入中」的对象拿去拷。',
                 ['tag 固定为 `"storage_manager"`。' + R(SM, 317, 325),
                  '被拒（已常驻 / 同 tag 在写）或 OOM 的 key 不在返回结果里；传输模块对它们跳过拷贝。' + R(SM, 328) + R(LDT, 973, 975)],
                 [note('文档', '同 tag 的写者在一个 key 上互斥，这就是引擎写入依赖的去重。', q=Q_DEDUP, ref=A(STAGEDOC, 32)),
                  note('推断', '后到的被拒后什么都不拷，也不分配：同一块 KV 不会被拷两遍、存两份。')],
                 '被拒的 key 早在分配之前就筛掉了，不会白占内存。' + R(L1B, 552, 553))),
        wire('w_alloc', 'staging', 'pool', '整批要 n 块', ml='整批要', why='全有或全无', trap='差一块全 OOM',
             card=card(
                 '暂存表向内存池一次要齐这一批要新分配的对象。',
                 ['A 的 3 块：一次要 3 个 36 MiB。' + R(L1B, 555, 556),
                  '失败时给这批每个 key 标 `OUT_OF_MEMORY`，已分到的部分还回去；不建暂存，也不通知 LRU。' + R(L1B, 559, 565) + R(L1B, 583, 584)],
                 [note('注释', '凑不齐时空闲表原样不动。', q=Q_NOFREE, ref=A(MM, 1289))],
                 ['OOM 之后：没有重试、没有阻塞、没有同步淘汰。这几块这次不存，下一个同前缀请求少命中、照常重算，推理本身不受影响。'
                  + R(LDT, 1065, 1084)])),
        wire('w_admit', 'staging', 'objects', 'finish_write 转正', ml='转正', n='2', why='拷完才可见',
             trap='成功≠留下我的',
             card=card(
                 'GPU 拷完后，排空线程调 `finish_write`：把这个 tag 的暂存对象摘出来，放进常驻表。',
                 ['`_take_staging`：写锁还活着就放掉写锁、从暂存表摘出。' + R(L1B, 1189, 1204),
                  '已常驻（别人先转正了）→ 丢掉自己的，内存还给池。否则 `_objects[key] = entry`。' + R(L1B, 640, 654),
                  '非临时对象通知监听者：LRU 把它们移到最新端。' + R(L1B, 656, 658)],
                 [note('注释', '摘出和放入在同一把全局锁里，整批原子。', q=Q_ATOMIC, ref=A(L1B, 191)),
                  note('推断', '只有这一步让对象可见，所以「读者只读到拷完的数据」只靠这一行保证。')],
                 [note('注释', '发现已常驻时丢掉自己的暂存，**仍然返回 SUCCESS**：数据反正在 L1 里。', q=Q_DISCARD, ref=A(L1B, 627, 628)),
                  '写锁已过期 → 返回 `KEY_IN_WRONG_STATE`，**不**转正，对象留给淘汰回收。' + R(L1B, 1193, 1201)]),
             to='l1.write'),
        wire('w_pf', 'sm', 'prefetch', '查 + 上锁', ml='查+锁', n='3', why='无 L2 当场做完',
             card=card(
                 '查找模块的 LOOKUP 经门面 `submit_prefetch_task` 转给预取控制器。',
                 ['默认 `fetching_policy="prefix"`、`lock_mode=LOCK`。' + R('lmcache/v1/distributed/api.py', 742, 743) + R(LK, 382, 393),
                  '门面只转一下、包个句柄；之后 `query_prefetch_status` 取走结果（只能取一次）。' + R(SM, 584) + R(SM, 604, 641)],
                 [note('推断', '主路没有 L2：锁 → 规划 → 收尾都在调用线程上同步做完，返回时结果已经可以查。')])),
        wire('w_rr', 'prefetch', 'objects', 'reserve_read', ml='见到就锁', why='锁就是探测', trap='h3′ 不算错',
             card=card(
                 '预取控制器对一整行 key 批量加读锁；存在的计数 +1（TP=1），不存在的返回 `KEY_NOT_EXIST`。',
                 ['只查常驻表：暂存中的 key 也算 `KEY_NOT_EXIST`。' + R(L1B, 325, 329),
                  '加几把由「读者数」决定（TP=1 时 1 把），一次最多 128。' + R(L1B, 322) + R(L1B, 334, 335),
                  '计划外的格子随后由同一条线以 `finish_read` 放掉。' + R(PC, 1325, 1328)],
                 [note('文档', '探测就是加锁：被报成命中的块，从被发现那一刻起就锁着。', q=Q_OBS, ref=A(PCDOC, 31, 33))],
                 '加读锁不刷新 LRU：B 的 h0–h2 之所以在队尾，是因为收尾时的 `touch_keys`。' + R(PC, 1730, 1733))),
        wire('w_read', 'sm', 'objects', '读 + 放锁', ml='读/放锁', mlp=[170, 300], why='安全靠先前的锁',
             trap='它不加锁',
             card=card(
                 'RETRIEVE 经门面来取对象：先 `unsafe_read` 拿到内存地址，GPU 拷完后回调 `finish_read` 放锁。',
                 ['`unsafe_read` 只取**已经锁着**的对象；没锁返回 `KEY_NOT_READABLE`。' + R(L1B, 372, 376),
                  'RETRIEVE 拷完时每个 worker 放 1 把；取消 / 没取的 LOOKUP 一次放整批。' + R(SM, 1437)],
                 [note('注释', '调用方必须保证 `unsafe_read` 夹在 `reserve_read` 和 `finish_read` 之间。', q=Q_UNSAFE, ref=A(L1B, 349, 350)),
                  note('推断', '名字叫 unsafe，是因为安全性来自之前 LOOKUP 加的锁，不在这一步；这一步的检查只是安全网。')],
                 ['`finish_read` 对已经不再锁着的 key（比如锁过期了）只打 warning、返回 `KEY_IN_WRONG_STATE`。' + R(L1B, 426, 433)]),
             to='l1.read'),
        wire('w_hold', 'objects', 'rlock', '每个对象一把', ml='带一把', cls='dash', why='计数容多个读者',
             card=card(
                 '常驻表里每个对象自带两把 `TTLLock`；读锁决定它能不能被读、能不能被淘汰。',
                 ['两把锁在 `reserve_write` 建对象时就建好：写锁 600 s、读锁 300 s。' + R(L1B, 572, 578)],
                 [note('推断', '读锁用计数：同前缀的 B 和 C 可以同时持有同一个对象，各放各的。')])),
        wire('w_notify', 'objects', 'lru', '监听：转正 / touch', ml='通知', cls='dash', why='只认写完和 touch',
             trap='在 L1 锁里同步调',
             card=card(
                 'L1 管理器的状态一变，就同步回调监听者；LRU 靠这些回调维护次序。',
                 ['保留写、转正 → 新建（移到最新端）；`touch_keys` → 触碰；删除 → 摘掉；加锁 / 放锁 → 什么都不做。'
                  + R(EVB, 191, 215),
                  '回调是 L1 管理器**持着自己的锁**时同步调的。' + R(L1B, 583, 584)],
                 [note('注释', '放读锁不说明这个 key 有用，所以不动次序。', q=Q_NOOP, ref=A(EVB, 196))],
                 '保留写那一刻就进次序表：扔下的暂存（写锁过期）也会被淘汰回收。')),
        wire('w_pick', 'evict', 'lru', '按 LRU 挑 n 个', ml='挑 n 个', n='4', why='跳过锁住的',
             card=card(
                 '淘汰线程把过滤器交给 LRU，让它从队首挑出该删的 key。',
                 ['`target = int(跟踪数 × 0.2)`；从队首扫，过滤器不过就跳过，够数就停。' + R(LRU, 168, 188)],
                 [note('提交', '过滤器是后加的，为了提高淘汰效率。', q='Skip locked keys during LRU eviction to improve eviction efficiency',
                       ref='c:cb8b1c04'),
                  note('推断', '之前 LRU 直接取队首 n 个，锁住的删不掉，配额白白浪费（从提交标题反推）。')])),
        wire('w_del', 'evict', 'objects', 'delete（复查）', ml='删', why='删时再查一遍', trap='不带 force',
             card=card(
                 '淘汰线程对挑中的 key 调 `delete`，真正把对象删掉、内存还给池。',
                 ['`delete` 自己再查：读锁锁着 → `KEY_IS_LOCKED` 不删；有活着的暂存 → 也不删。' + R(L1B, 793, 807),
                  '淘汰线程调 `delete` 时不传 `force`。' + R(EC, 192, 194)],
                 [note('注释', '挑和删之间 key 可能刚被锁上，`delete()` 会再查一遍安全地拒绝。', q=Q_RECHECK, ref=A(L1B, 973, 974))],
                 '两层保险：挑的时候过滤（`is_key_evictable`），删的时候复查。前提是锁没过期。')),
    ]

    steps = [
        beat('A 的 STORE 进门：过三道门',
             '顶层那一步「A：服务端拷贝」进了 L1 就是这里。门面以 tag `storage_manager` 调 `reserve_write(h0, h1, h2)`。'
             '每个 key 过三道门：不在常驻表 ✓、同 tag 没在暂存 ✓ → 三个都要新分配。',
             note('注释', '所有引擎写入共用这个 tag，后面同一个 key 的第二个写者就会被拒。', q=Q_TAG2, ref=A(SM, 99, 101)),
             focus=['staging', 'w_write', 'sm'],
             state={'sm': g_sm(0),
                    'staging': g_checks([('h0–h2 已常驻？', 'no'), ('同 tag 在暂存？', 'no'), ('→ 要分配 3 个', 'ok')])}),
        beat('整批要 3 块，建暂存',
             '一次向内存池要 3 个 36 MiB：池里 568 个槽，用掉 3 个。每个对象建两把锁（写锁 600 s、读锁 300 s），**只给写锁上锁**，'
             '放进暂存表。`_staging_bytes = 108 MiB`；常驻表里**没有** h0–h2。LRU 从这一刻开始跟踪它们。',
             note('推断', '一批一次分配：要么三块都有地方，要么三块都没有，不会留下一半。'),
             focus=['pool', 'w_alloc', 'staging', 'lru'],
             state={'pool': g_pool(3),
                    'staging': g_cells([cell('h0', '写锁', 'new'), cell('h1', '写锁', 'new'), cell('h2', '写锁', 'new')]),
                    'lru': g_text('h2 h1 h0', '← 旧　　新 →'),
                    'evict': g_use(3)}),
        beat('拷贝途中：A2 来存同样的块',
             'A2（设）与 A 前 768 token 相同，在 A 的拷贝还没完时也发 STORE。它的 `reserve_write` 撞在第二道门：同 tag 的暂存还在、'
             '写锁活着 → 三个 key 都是 `KEY_NOT_WRITABLE`。**不分配**，池还是 3 个；传输模块对它们跳过拷贝。'
             '此时若有人 `reserve_read(h0)`，得到的是 `KEY_NOT_EXIST`。',
             note('文档', '同 tag 的写者在一个 key 上互斥：这就是引擎写入依赖的去重。', q=Q_DEDUP, ref=A(STAGEDOC, 32)),
             focus=['staging', 'w_write', 'sm', 'pool'],
             state={'staging': g_checks([('已常驻？', 'no'), ('同 tag 写锁活着？', 'ok'), ('→ NOT_WRITABLE ×3', 'no')])}),
        beat('拷完：转正',
             'GPU 拷完，排空线程调 `finish_write`：h0、h1、h2 依次放写锁、从暂存表摘出、`_objects[key] = entry`。'
             '暂存表空了，常驻表有 3 个对象，读锁计数都是 0。LRU 收到「转正」，把它们重排到最新端：倒序放入，队尾是 h2, h1, h0。',
             note('注释', '同一请求的几块，后面的先淘汰：先删 h0 的话 h1、h2 还在也命不中。', q=Q_LATER, ref=A(LRU, 83)),
             focus=['w_admit', 'objects', 'staging', 'w_notify', 'lru'],
             state={'staging': G_STG0,
                    'objects': g_cells([cell('h0', '锁 0'), cell('h1', '锁 0'), cell('h2', '锁 0')]),
                    'sm': g_sm(None)}),
        beat('B 的 LOOKUP：见到就锁',
             'B（时刻 **t0**）查 `[h0, h1, h2, h3′]`。门面转给预取控制器，它在当前线程批量 `reserve_read`：h0–h2 各 +1，'
             'h3′ 是 `KEY_NOT_EXIST`。按前缀裁：留 h0–h2，没有要放的。收尾 `touch_keys` 把它们移到最新端，结果交给查找模块：命中 3 块。',
             note('文档', '探测就是加锁：被报成命中的块从被发现那刻就锁着，并发淘汰不能让它失效。', q=Q_OBS, ref=A(PCDOC, 31, 33)),
             focus=['prefetch', 'w_rr', 'objects', 'rlock', 'w_pf'],
             state={'sm': g_sm(1),
                    'prefetch': g_checks([('h0 锁上', 'ok'), ('h1 锁上', 'ok'), ('h2 锁上', 'ok'), ('h3′ 不存在', 'no')]),
                    'objects': g_cells([cell('h0', '锁 1', 'pick'), cell('h1', '锁 1', 'pick'), cell('h2', '锁 1', 'pick')]),
                    'rlock': g_lock('1', 't0+300s', '是')}),
        beat('C 也来查：计数到 2',
             'C（设）与 B 同前缀，在 B 还持锁时 LOOKUP。h0 的读锁计数 1 → **2**，到期时间刷新为 **tC + 300 s**（tC = C 加锁的时刻）。'
             '两个请求同时持有同一个对象，互不影响。',
             note('推断', '读锁是计数而不是互斥：同前缀的请求（或 TP>1 时的多个 worker）可以同时持有。'),
             focus=['rlock', 'w_hold', 'objects', 'prefetch'],
             state={'prefetch': g_checks([('h0 锁上', 'ok'), ('h1 锁上', 'ok'), ('h2 锁上', 'ok')]),
                    'objects': g_cells([cell('h0', '锁 2', 'pick'), cell('h1', '锁 2', 'pick'), cell('h2', '锁 2', 'pick')]),
                    'rlock': g_lock('2', 'tC+300s', '是')}),
        beat('B、C 拷完放锁：2 → 1 → 0',
             'B 的 RETRIEVE：`unsafe_read` 不加锁，只检查「锁着」→ 拿到地址；拷完回调 `finish_read`，计数 2 → 1。'
             'C 拷完 → **0**，`is_locked()` 为假：h0 回到普通对象，可以被淘汰了。',
             note('注释', '调用方要保证 `unsafe_read` 夹在加锁和放锁之间：安全来自 LOOKUP 的锁。', q=Q_UNSAFE, ref=A(L1B, 349, 350)),
             focus=['w_read', 'objects', 'rlock', 'sm'],
             state={'sm': g_sm(2),
                    'prefetch': G_PF0,
                    'objects': g_cells([cell('h0', '锁 0'), cell('h1', '锁 0'), cell('h2', '锁 0')]),
                    'rlock': g_lock('0', 'tC+300s', '否')}),
        beat('换一条时间线：B 在 LOOKUP 后崩了',
             '回到 t0：B 加完锁（计数 1，到期 t0+300 s）就整个挂掉（设）。服务端没人知道它死了，没有人来放锁。'
             't0 到 t0+300 s：h0–h2 锁着，淘汰挑不中，**白占内存**。到 **t0+300 s**：计数还是 1，但已到期，`is_locked()` 变假——'
             '没有任何人调 unlock，对象自己变回可淘汰。',
             note('注释', '锁过期后再有人加锁，计数从 1 重新开始。', q=Q_EXPIRE_H, ref=A(TTLH, 36)),
             focus=['rlock', 'objects', 'w_hold'],
             state={'sm': g_sm(None),
                    'objects': g_cells([cell('h0', '锁 1·过期', 'ghost'), cell('h1', '锁 1·过期', 'ghost'),
                                        cell('h2', '锁 1·过期', 'ghost')]),
                    'rlock': g_lock('1', '已过', '否')}),
        beat('池涨到 456 个：淘汰',
             '设：池里攒到 **456** 个对象 = 16416 MiB，过了红线 16384（80%）。淘汰线程在下一秒醒来：要删 `int(456 × 0.2)` = **91** 个；'
             '从 LRU 队首往后扫，被读锁钉住的跳过，凑够 91 个；逐个 `delete`（删时再查一遍锁）。剩 365 个 = 13140 MiB，约 64%。',
             note('注释', '挑的时候跳过锁住的 key：它们反正删不掉。', q=Q_SKIP, ref=A(LRU, 145, 146)),
             focus=['evict', 'w_pick', 'lru', 'w_del', 'objects'],
             state={'evict': g_use(456),
                    'pool': g_pool(456),
                    'rlock': G_LOCK0,
                    'objects': g_cells([cell('…'), cell('h0', '锁 0'), cell('h2', '锁 0')], right='共 456'),
                    'lru': g_text('删 91 个', '从队首扫，跳过锁住的', tone='run')}),
        beat('删完：365 个，回到红线下',
             '91 个最旧的对象删掉，内存还给池：365 个 = 13140 MiB，约 64%。下一秒再看，用量低于 0.8，跳过。'
             'A 的 h0–h2 刚被 B touch 过，在队尾，这一轮轮不到它们。',
             note('推断', '淘汰是 1 s 一次的定时器：一秒之内写满池子，中间这段就只能 OOM。'),
             focus=['evict', 'pool', 'objects', 'lru'],
             state={'evict': g_use(365),
                    'pool': g_pool(365),
                    'objects': g_cells([cell('…'), cell('h0', '锁 0'), cell('h1', '锁 0'), cell('h2', '锁 0')],
                                       right='共 365'),
                    'lru': g_text('… h2 h0', '← 旧　　新 →')}),
        beat('B 缺口：锁 2 个，留 1 个',
             '设：B 到来前 h1 已不在 L1，h0、h2 还在（按 LRU 规则同一请求通常是 h2 先走，这里只为演示「见到就锁」）。见到就锁：h0 ✓、h1 `KEY_NOT_EXIST`、h2 ✓、h3′ 不存在。按前缀折叠：从 h0 数起，h1 断了 → 只留 **h0**。'
             'h2 的锁在规划这一步**立刻放掉**，否则它会被一个没人用的锁钉住最多 300 s。命中 1 块 = 256 token。',
             note('推断', '先锁后裁：锁就是探测，所以只能先全锁上，再把前缀外的放掉。'),
             focus=['prefetch', 'w_rr', 'objects', 'rlock'],
             state={'sm': g_sm(1),
                    'prefetch': g_checks([('h0 锁上·留', 'ok'), ('h1 不存在', 'no'), ('h2 锁上→放', 'skip'), ('h3′ 不存在', 'no')]),
                    'objects': g_cells([cell('…'), cell('h0', '锁 1', 'pick'), cell('h2', '锁 0')], right='共 365'),
                    'rlock': g_lock('1', '+300s', '是')}),
        beat('池满：整批 OOM',
             '设：一秒内写入涌进来、池里只剩 **2** 个空槽，这时一个新请求要存 3 块。暂存表一次要 3 个 → 凑不齐 → 3 个**全** `OUT_OF_MEMORY`。'
             '不建暂存、不进 LRU、不当场淘汰；门面发 OOM 事件，传输模块跳过拷贝。这 3 块这次不存，下次少命中、重算；推理照常。',
             note('注释', '凑不齐时空闲表原样不动，不用回滚。', q=Q_NOFREE, ref=A(MM, 1289)),
             focus=['w_alloc', 'pool', 'staging', 'sm'],
             state={'sm': g_sm(0),
                    'pool': g_pool(566),
                    'evict': g_use(566),
                    'prefetch': G_PF0,
                    'staging': g_checks([('要 3 个', None), ('池里 2 个', 'no'), ('→ OOM ×3', 'no')])}),
    ]

    groups = [group('L1 管理器（对象表 + 锁）', [300, 0, 920, 352], m=[0, 196, 340, 770])]

    blk = scene(1220, 520, parts, wires, steps, mw=340, mh=1185, groups=groups,
                cap='接顶层的例子：A 存 h0 h1 h2，B 取；对象 36 MiB，L1 20 GiB = 568 个；TP=1。带「设」的是为讲解补设的值')
    return node(
        'l1', 'L1 存储', 'L1 存储：两张表、一把租约锁、一条淘汰线程',
        '顶层「L1 存储」框里面：对象怎么从暂存转正、读锁怎么计数和过期、淘汰怎么挑。',
        lead='你在顶层「L1 存储」框的里面。存：先进**暂存表**（读者看不见），拷完一行转进**常驻表**。'
             '取：预取控制器「见到就锁」，读锁是「计数 + 到期时间」的租约。挤：一条后台线程每秒看一次用量，只删没锁的。'
             '按 ▶ 接着顶层的 A、B 往下走。',
        src=A(L1B, 142, 192),
        blocks=[blk],
        children=[_write(), _read(), _ttl(), _prefetch(), _evict()])


# ---------------------------------------------------------------- 源码页
def _write():
    return node(
        'l1.write', '写：暂存与转正', 'reserve_write / finish_write：三道门、整批分配、转正',
        '「L1 存储」里的暂存表和转正那条线：写入两阶段的源码。',
        lead='你在「L1 存储 → 暂存表 / 转正」里面。两段：`reserve_write` 决定谁能写、一次要内存、建暂存；'
             '`finish_write` 把暂存摘出、放进常驻表。',
        src=A(L1B, 526, 595),
        blocks=[
            code(L1B, [(526, 595)], notes=[
                at((527, 529), None, '第一道门：已经常驻 → `KEY_NOT_WRITABLE`。A 存完之后谁再存 h0 都停在这里（预取装 L2 数据时会传 '
                                     '`allow_resident` 绕过）。', has='KEY_NOT_WRITABLE'),
                at((531, 535), None, '第二道门：同一个 tag 已经在暂存、写锁还活着 → 拒。A2 和 A 同用 tag `storage_manager`，'
                                     '就是在这里被拒的。', has='is_locked'),
                at((536, 547), '推断', '第三道门：同 tag 的暂存写锁已过期 → 不再分配，把那块内存直接交给新写者、重新上写锁（日志是 warning）。'
                                     '这样既不浪费内存，也不让新写者干等。', has='hand the buffer over'),
                at((552, 553), None, '全被拒就直接返回，不碰内存池：被拒的 key 不占内存。', has='len(need_to_allocate) == 0'),
                at((555, 557), None, '剩下的 key **一次**向池要：A 的 3 块一次要 3 个 36 MiB。所以 OOM 是整批的。', has='allocate'),
                at((559, 565), None, '凑不齐：这一批每个 key 都标 OOM，分到的部分还回去。只剩 2 个槽要 3 块 → 3 块全 OOM。',
                   has='OUT_OF_MEMORY'),
                at(571, None, '给内存对象盖「属于哪个 L1」的章，之后放锁时靠它找回主人。', has='set_l1_manager'),
                at((572, 578), None, '两把租约锁：写锁 600 s、读锁 300 s。此刻只有写锁上了锁，读锁计数是 0。', has='TTLLock'),
                at(579, None, '放进**暂存表**，不是常驻表：读者看不见它。', has='_put_staging'),
                at((583, 584), None, '同步通知监听者：LRU 从这一刻开始跟踪这些 key（扔下的暂存过期后也能被淘汰）。',
                   has='on_l1_keys_reserved_write'),
            ], cap='reserve_write：三道门 → 整批分配 → 建暂存'),
            code(L1B, [(635, 658), (1189, 1204)], notes=[
                at(636, None, '先从暂存表把自己 tag 的那份取出来（下面 `_take_staging`）。', has='_take_staging'),
                at((640, 648), '注释', '已经常驻（别的写者先转正）→ 丢掉自己的，结果仍是 SUCCESS：数据反正在 L1 里。',
                   q='the resident object is kept', ref=A(L1B, 627), has='already resident'),
                at(649, None, '**整台状态机最关键的一行**：对象进常驻表，从此读者可见。A 的 h0–h2 在这里转正。', has='self._objects[key] = entry'),
                at((650, 652), None, '临时对象（主路没有）不通知监听者。', has='is_temporary'),
                at(654, None, '被丢掉的那份暂存内存还给池。', has='free(discarded)'),
                at((656, 658), None, '通知监听者「写完了」：LRU 把它们移到最新端。', has='on_l1_keys_write_finished'),
                at((1193, 1201), None, '写锁已过期 → 不转正，返回 `KEY_IN_WRONG_STATE`：过期意味着那块内存可能已交给了别的写者（见第三道门）。',
                   has='expired'),
                at((1203, 1204), None, '放写锁、从暂存表摘出。和上面放进常驻表在同一把全局锁里，没人看得到中间态。', has='unlock'),
            ], cap='finish_write + _take_staging：转正'),
        ])


def _read():
    return node(
        'l1.read', '读锁三件套', 'reserve_read / unsafe_read / finish_read',
        '「L1 存储」里的常驻表：加读锁、读、放读锁的源码。',
        lead='你在「L1 存储 → 常驻表」里面。三个函数都只看常驻表 `_objects`：加锁 = 计数 +n，读 = 检查锁着再给地址，放锁 = 计数 −n。',
        src=A(L1B, 322, 446),
        blocks=[
            code(L1B, [(322, 340)], notes=[
                at((326, 329), None, '只查常驻表：暂存中的 h0 也报 `KEY_NOT_EXIST`。B 的 h3′ 也是在这里返回不存在。', has='KEY_NOT_EXIST'),
                at((331, 335), '注释', '一把一把加锁的循环。n 是读者数（TP=1 时 1），最多 128，免得一次调用占着全局锁太久。',
                   q=Q_MAXLOCK, ref=A(L1B, 40), has='read_lock.lock()'),
            ], cap='reserve_read：存在就计数 +n'),
            code(L1B, [(364, 378)], notes=[
                at((372, 374), '推断', '不加锁，只**检查**锁着：没锁返回 `KEY_NOT_READABLE`。它是安全网，安全性来自之前 LOOKUP 加的锁。'
                                     'B 若没死只是慢、t0+400 s 才来取，就会在这里失败，vLLM 重算。', has='KEY_NOT_READABLE'),
            ], cap='unsafe_read：只取已锁的'),
            code(L1B, [(415, 446)], notes=[
                at((426, 433), None, '锁已经不在了（比如过期）：只打 warning、返回 `KEY_IN_WRONG_STATE`。这是慢读者收尾时走的分支。',
                   has='KEY_IN_WRONG_STATE'),
                at((438, 439), None, '计数 −n。B 拷完：2 → 1；C 拷完：1 → 0。只减数，不记是谁的。', has='unlock'),
                at((440, 443), None, '临时对象计数归零就当场删除（主路没有临时对象）。', has='is_temporary'),
            ], cap='finish_read：计数 −n'),
        ])


def _ttl():
    return node(
        'l1.ttl', 'TTLLock：租约', 'TTLLock：计数 + 到期时间（C++）',
        '「L1 存储」里的一把读锁：加锁、放锁、判断锁着的 C++ 实现。',
        lead='你在「L1 存储 → 一把读锁」里面。只有两个原子变量：计数 `counter_`、到期时间 `expiration_ms_`。看三个函数怎么用它们。',
        src=A(TTLH, 33, 40),
        blocks=[
            code(TTLH, [(33, 40)], notes=[
                at((36, 38), '注释', '头文件写明的行为：过期后加锁计数重置为 1，否则 +1；每次都刷新到期时间。',
                   q=Q_REFRESH_H, has='reset to 1'),
            ], cap='lock() 的约定'),
            code(TTLC, [(13, 75)], notes=[
                at(23, None, '过期 = 现在 ≥ 到期时间。新锁的到期时间是 0，所以新锁天然「已过期」，第一次加锁走过期分支。',
                   has='expired'),
                at((25, 33), None, '过期分支：先把到期时间换成「现在 + TTL」，再把计数**直接置 1**——过期前累积的计数被丢掉。',
                   has='counter_.store(1'),
                at((36, 45), None, '没过期：计数 +1，再**无条件**刷新到期时间。所以最后一个加锁的人决定整把锁何时到期（C 加锁后到期跟 C 走）。',
                   has='old_counter + 1'),
                at((55, 58), None, '放锁到 0 为止，多放不会变负。', has='old_counter <= 0'),
                at(74, None, '锁着 = 计数 > 0 **且** 没到期。B 崩了：计数一直是 1，过了 t0+300 s 这里就返回假，不需要任何人解锁。',
                   has='current_time < expiration'),
            ], cap='ttl_lock.cpp：lock / unlock / is_locked'),
        ])


def _prefetch():
    return node(
        'l1.prefetch', '预取：锁、裁、收尾', 'submit_prefetch_request（L1 路径）：见到就锁，再按前缀裁',
        '「L1 存储」里的预取控制器：主路（无 L2）下 LOOKUP 在 L1 里走的三步。',
        lead='你在「L1 存储 → 预取控制器」里面。没有 L2 时，入口在当前线程依次做三件事：锁 → 规划（放掉前缀外的锁）→ 收尾。',
        src=A(PC, 714, 729),
        blocks=[
            code(PC, [(714, 729)], notes=[
                at((718, 724), None, '没有 L2：在调用线程上「锁 → 规划 → 收尾」，返回时结果已经可以查。', has='_lock_l1_keys'),
            ], cap='入口'),
            code(PC, [(1260, 1261), (1263, 1294)], notes=[
                at((1260, 1261), '注释', '无论哪种模式都先加锁：探测即占有。', q=Q_LOCKALL, has='regardless'),
                at((1273, 1279), None, '对每一行批量 `reserve_read(keys, 读者数)`。B 缺口时：h0 ✓、h1 ✗、h2 ✓、h3′ ✗。',
                   has='reserve_read'),
                at((1280, 1290), None, '成功的格子记进「已锁」位图：这是控制器自己的账，之后放锁照它来。', has='l1_locked_keys'),
                at((1292, 1294), None, '没跑完（异常）就把已经加的锁全放掉，不留孤儿锁。', has='_release_all_locks'),
            ], cap='_lock_l1_keys：见到就锁'),
            code(PC, [(1313, 1336)], notes=[
                at((1314, 1321), None, '策略按前缀折叠出「该留哪些」：B 缺口时只留 h0。', has='plan_load'),
                at((1323, 1328), None, '已锁减去计划 = 多锁的；当场 `finish_read` 放掉。B 缺口时放的是 h2。', has='finish_read'),
                at(1334, None, '把「已锁」位图改成计划本身：记录和实际持锁严格一致，整表回滚才可靠。', has='l1_planned_keys.copy()'),
            ], cap='_plan_load：按前缀裁，放掉多余的锁'),
        ])


def _evict():
    return node(
        'l1.evict', '淘汰：挑与删', '淘汰线程 + LRU 挑选 + 两层锁检查',
        '「L1 存储」里的淘汰线程和 LRU 次序表的源码。',
        lead='你在「L1 存储 → 淘汰线程 / LRU 次序表」里面。四段：每秒的循环、LRU 怎么排、怎么挑、两层锁检查。',
        src=A(EC, 160, 198),
        blocks=[
            code(EC, [(160, 198)], notes=[
                at(165, '推断', '每秒醒一次：淘汰粒度是秒级。一秒内写满池子，就只能 OOM。', has='time.sleep(1)'),
                at((166, 177), None, '用量 = 已用 / 总量；低于 0.8 跳过。例子：456 个 = 16416 / 20480 = 0.8016，触发。',
                   has='watermark'),
                at((184, 187), None, '向 LRU 要该删的 key，把 `is_key_evictable` 当过滤器交给它。', has='key_eligible_filter'),
                at(194, None, '真删时不带 `force`：`delete` 会再查一遍锁。', has='delete(action.keys)'),
            ], cap='L1EvictionController.eviction_loop'),
            code(LRU, [(80, 92)], notes=[
                at((83, 85), '注释', '同一请求的几块，后面的先淘汰：先删 h0，h1、h2 还在也命不中。', q=Q_LATER, has='evicted first'),
                at((86, 92), None, '倒序逐个放到最新端：`[h0, h1, h2]` 放完，队尾是 `…, h2, h1, h0`，h2 离队首最近。', has='reversed'),
            ], cap='on_keys_created：倒序放入'),
            code(LRU, [(160, 199)], notes=[
                at(168, None, '个数 = 跟踪的 key 数 × 0.2：int(456 × 0.2) = 91。按个数，不是按字节。', has='target_count'),
                at((171, 172), None, '至少删 1 个。', has='target_count = 1'),
                at((181, 188), None, '从队首扫，过滤器不过（锁着）就跳过，够数就停。', has='key_eligible_filter'),
            ], cap='get_eviction_actions：从队首挑，跳过锁住的'),
            code(L1B, [(969, 992), (787, 807)], notes=[
                at(972, '注释', '第一层不拿全局锁；`delete()` 会再查一遍。', q='does NOT acquire the global L1Manager lock',
                   has='NOT acquire'),
                at((983, 985), None, '常驻且读锁没锁着 → 可淘汰。B 持锁的 h0 在这里被跳过。', has='read_lock.is_locked()'),
                at((986, 992), None, '暂存的写锁已过期也可淘汰（扔下的写入由此回收）。不持锁，所以先 `list(...)` 拍个快照。',
                   has='write_lock.is_locked()'),
                at((793, 797), None, '第二层（删时）：还有活着的暂存 → 不删。', has='KEY_IS_LOCKED'),
                at((804, 807), None, '第二层：读锁锁着且不是 force → `KEY_IS_LOCKED`，不删。', has='locked and not force'),
            ], cap='is_key_evictable（挑时）与 delete（删时）'),
        ])
