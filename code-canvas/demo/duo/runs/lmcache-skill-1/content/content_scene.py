from cv import *

META = dict(title='LMCache：KV cache 怎么存、怎么取回复用')

CON = 'lmcache/integration/vllm/lmcache_mp_connector.py'
ADP = 'lmcache/integration/vllm/vllm_multi_process_adapter.py'
MD = 'lmcache/integration/vllm/lmcache_mp_metadata.py'
MLS = 'lmcache/integration/vllm/mp_lookup_state.py'
WT = 'lmcache/v1/client/worker_transfer.py'
LDT = 'lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py'
LK = 'lmcache/v1/multiprocess/modules/lookup.py'
ENG = 'lmcache/v1/multiprocess/engine_context.py'
SES = 'lmcache/v1/multiprocess/session.py'
L1B = 'lmcache/v1/distributed/l1_manager/base.py'
SM = 'lmcache/v1/distributed/storage_manager.py'
PC = 'lmcache/v1/distributed/storage_controllers/prefetch_controller.py'
DCFG = 'lmcache/v1/distributed/config.py'
TH = 'lmcache/v1/common/token_hasher.py'
TC = 'lmcache/v1/common/token_codec.py'
CT = 'lmcache/v1/common/custom_types.py'
API = 'lmcache/v1/distributed/api.py'
MQ = 'lmcache/v1/multiprocess/transport/zmq_impl/mq.py'
ZS = 'lmcache/v1/multiprocess/transport/zmq_impl/server.py'
AFF = 'lmcache/v1/multiprocess/affinity_pool.py'
FUT = 'lmcache/v1/transport/futures.py'
IDX = 'docs/source/mp/index.rst'
SMD = 'docs/design/v1/distributed/storage_manager.md'


def A(f, a, b=None):
    """note / at 的 ref=：文件:行 或 文件:起-止（不带反引号）。"""
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    """写在文字里的锚点（带反引号）。"""
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 引文（逐字照抄）
Q_NOWAIT = 'Do not wait for the acknowledgement here: that is a full server round trip on the scheduler thread.'
Q_LOCK = "LMCache, which will essentially 'lock' the KV cache chunks in the LMCache"
Q_FREE = 'Calling this prevents those chunks from holding read locks until TTL expiry.'
Q_TTL = 'with configurable timeouts to prevent deadlocks from crashed clients'
Q_FAILCLOSED = ('Fail closed: commit the reserved objects only when every chunk copied successfully; '
                'otherwise the whole store is skipped.')
Q_GIL = "Route finish_write / finish_read_prefetched through a C++ host callback so the driver thread doesn't acquire the GIL."
Q_AFF = ('so that all operations for a given vLLM instance land on one thread, eliminating the need for '
         'per-instance locks on the shared temporary GPU buffer.')
Q_STAGING = 'Staging objects are kept apart from the resident objects: readers of the key do not see them'
Q_KEEP = 'Keep the blocks only while a store may still read them.'
Q_EVENT = 'Producer event that orders reads of the engine KV cache.'


# ---------------------------------------------------------------- 小图（同一套数：A 1000 token，B 1200 token 前 900 同 A）
def g_sched(rows):
    return g_kv(rows)


G_SCHED0 = g_kv([('请求', 'A · 1000 token'), ('查找', '[0, 768)'), ('状态', 'PREFETCHING')])
G_WORKER0 = g_list(['RETRIEVE：无', 'STORE：无'])
G_GPU0 = g_cells([cell('b0–15', '块0'), cell('b16–31', '块1'), cell('b32–47', '块2'), cell('b48–62', '尾巴', 'ghost')])
G_LOOK0 = g_checks([('h0 在 L1', None), ('h1 在 L1', None), ('h2 在 L1', None)])
G_HASH0 = g_cells([cell('h0', '块0'), cell('h1', '块1'), cell('h2', '块2')])
G_XFER0 = g_text('空闲', '还没有拷贝')
G_L10 = g_list(['（空）', '0 / 20 GiB'])


def root(base):
    base['lead'] = ('LMCache 是一个独立的缓存服务进程：vLLM 算好的 KV，由它伸手从 GPU 拷进自己的 CPU 内存；'
                    '下一个前缀相同的请求来，先「查 + 上锁」，再拷回 GPU。按 ▶ 跟着请求 A（存）和请求 B（取）走一遍；'
                    '每个框、每条线都能点开。')

    parts = [
        # ------------------------------------------------ vLLM 一侧
        part('sched', '调度侧连接器', [24, 30, 240, 150], m=[4, 30, 190, 150], code='Connector·调度器',
             kind='act', glyph=G_SCHED0,
             say='记账下单：查多少、存哪几块', why='不在关键线程上等服务端', trap='LOOKUP 不只是查，还上锁',
             card=card(
                 ['vLLM 调度器每一步都会回调它：`get_num_new_matched_tokens` / `update_state_after_alloc` / '
                  '`build_connector_meta` / `request_finished`。' + R(CON, 1377),
                  '它回答两件事：这个请求有多少 token 能从 LMCache 取回来？这一步有哪些整块该存下去？答案写成「工单」交给 worker。'],
                 ['每个请求一个 tracker，带一个小状态机：`PREFETCHING` → `WAITING_FOR_LOAD`（要取）或 `READY`（不用取）。'
                  + R(MD, 53, 66),
                  '只按 256 token 的整块算：A 的 1000 token 向下取整 = 768（3 块），尾巴 232 不查也不存。' + R(ADP, 860, 863),
                  '查询是「发出去不等」：发 LOOKUP 后当场问一次结果，没回来就告诉 vLLM「还没好，下一步再问」。' + R(CON, 1478, 1480),
                  '存多少：已算好的 token 取 vLLM 已有和 LMCache 已有的 max（且不低于 vLLM 真实已算到的位置），封顶到 prompt 长度，减去已存的，够一整块才存。'
                  + R(MD, 398, 428)],
                 [note('注释', '不等 LOOKUP 回执：等回执就是在调度线程上多走一趟服务端往返，而调度线程就是推理的关键路径。',
                       q=Q_NOWAIT, ref=A(ADP, 874, 875)),
                  note('提交', '后来连状态查询也改成不阻塞。', q='Poll lookup status without blocking the scheduler',
                       ref='c:588c5366'),
                  note('提交', '默认只存 prompt 的整块，生成出来的 token 不存。',
                       q="Cap MP stores at the request's prompt length", ref='c:7ba466fe'),
                  note('推断', '生成的 token 很少被别的请求原样复用，存它们只会占 L1、挤掉更有用的 prompt 块。')],
                 [note('注释', '「LOOKUP 只是查询」是错的：它在服务端会给命中的块上读锁。用不上的块必须显式放锁，否则要等超时。',
                       q=Q_LOCK, ref=A(ADP, 843)),
                  '命中数最早在下一个调度步才拿得到：LOOKUP 回执到了才发 QUERY，QUERY 回来了，下一次被 vLLM 问到时才交得出。要隔几步取决于 RPC 往返和调度步的长短，仓库里没有实测。'
                  + R(MLS, 25, 41)]),
             to='sched'),
        part('worker', 'worker 侧连接器', [24, 370, 240, 140], m=[4, 300, 150, 140], code='WorkerAdapter',
             kind='act', glyph=G_WORKER0,
             say='前向前发取，前向后发存', why='录 event 代替同步', trap='wait_for_save 并不等',
             card=card(
                 'GPU worker 进程里的那一半连接器。它**自己不拷任何数据**，只按调度器下来的工单，把「请把这些 GPU block '
                 '拷走 / 拷回来」的请求发给 LMCache 服务，然后每一步轮询哪些做完了。',
                 ['前向之前 `start_load_kv`：收集本步所有 RETRIEVE 工单，在当前 CUDA 流上录一个 event，批量发出。'
                  + R(CON, 1077, 1120),
                  '前向之后 `wait_for_save`：收集所有 STORE 工单，同样录 event、批量发出，发完就返回。' + R(CON, 1165, 1201),
                  '每步 `get_finished`：一个请求要「RPC 回复已到」**且**「服务端给的完成 event 已完成」才算做完，再报给 vLLM。'
                  + R(ADP, 2179, 2183)],
                 [note('注释', '拷贝在服务端进程的 CUDA 流上做，KV 却是 vLLM 进程的流写出来的，两个流之间没有天然先后；'
                              '所以 vLLM 录一个 event 交给服务端，让对方的流先等它。', q=Q_EVENT, ref=A(WT, 702)),
                  note('推断', 'vLLM 一侧不用 synchronize()，只是「录 event + 发一条消息」，GPU 上的等待落在服务端的流里，不卡推理线程。'),
                  note('注释', '服务端坏了：STORE 直接丢，RETRIEVE 标成出错让 vLLM 重算。丢的是缓存，不是引擎。',
                       q='losing the server costs the cache and not the engine.', ref=A(ADP, 376, 377))],
                 ['`wait_for_save` 名字里有 wait，但它不等：提交 STORE 就返回。真正防止 block 被别人覆盖的，是调度侧的'
                  '「延迟回收」和抢占时的 `flush_inflight_stores`。' + R(CON, 1171) + R(CON, 1707),
                  '`wait_for_layer_load` 直接 return：主路上没有逐层流水。' + R(CON, 1122, 1133)]),
             to='worker_src'),
        part('gpu', 'vLLM 的 GPU 显存', [250, 200, 190, 152], m=[4, 760, 190, 160], code='paged KV',
             kind='ext', glyph=G_GPU0,
             say='服务端直接读写的 KV', why='注册一次，之后只传编号', trap='vLLM 不动手拷，服务端拷',
             card=card(
                 'vLLM 自己的 paged KV cache：按 block 编号的一大片显存，例子里一个 block 装 16 个 token。它属于 vLLM，'
                 '但 LMCache 服务通过 CUDA IPC 能**直接读写**它——这是理解整套设计的关键。',
                 ['启动时 worker 调 `register_kv_caches`：把显存包装成跨进程句柄，发 `REGISTER_KV_CACHE` 给服务端。'
                  + R(CON, 1015) + R(WT, 610) + R(WT, 615),
                  '服务端导入句柄、建 `cache_context`，之后 STORE / RETRIEVE 直接用它访问 vLLM 的显存。' + R(LDT, 629),
                  '所以之后每张工单只写「拷哪几个 block」：256 token 的一块 = 256 / 16 = **16 个 block**。' + R(ADP, 685)],
                 [note('推断', 'LMCache 跑在独立进程里：推理路径上没有 GIL 争用和 Python 开销（出处是 MP 文档 docs/source/mp/index.rst 第 25 行；舞台不收 .rst 锚点，只能标推断）。'),
                  note('推断', '让服务端自己伸手拷，vLLM 一侧只剩「发一条消息」，拷贝的开销和等待都落在服务端进程里。')],
                 '注册之后这片显存「同时属于两个进程」。所以 vLLM 回收一个 block 之前，必须确认服务端已经不再读它'
                 '（见「完成名单」那条线）。')),

        # ------------------------------------------------ LMCache 服务一侧
        part('lookup', '查找模块', [500, 30, 230, 170], m=[186, 300, 150, 170], code='LookupModule',
             kind='gate', glyph=G_LOOK0,
             say='算哈希、查 L1、命中就上锁', why='先锁住，取之前不被淘汰', trap='只认从头连续命中的块',
             card=card(
                 '服务端处理 LOOKUP 的模块：拿到一个请求的全部 token，算出每块的身份，看 L1 里有没有——**有的当场上读锁**——'
                 '再数出「从头连续命中了几块」。',
                 ['把查找区间的 token 交给 Session 算链式哈希（A 得到 `[h0, h1, h2]`），并记下 token。'
                  + R(LK, 251) + R(LK, 300, 301),
                  '把哈希展开成 `ObjectKey`，提交预取任务；没有 L2 时预取就在当前线程同步做完：对 L1 批量 `reserve_read`（存在的当场上读锁）。'
                  + R(LK, 382, 393) + R(PC, 718, 722),
                  '按「前缀」数命中：从第一块开始数，断了就停；命中块数 × 256 就是命中 token 数。' + R(LK, 784) + R(LK, 844),
                  '结果放在服务端，调度器之后发 `QUERY_PREFETCH_STATUS` 来取，只交付一次。' + R(LK, 420, 449),
                  'LOOKUP / QUERY 跑在普通 CPU 线程池上（默认 1 条线程）。' + R(ZS, 139, 140)],
                 [note('推断', '从查到到真正取回，中间隔着好几个调度步；不先锁住，这段时间里块可能被淘汰掉，取的时候就扑空。'),
                  note('推断', '锁带超时：客户端崩了，锁也会自己过期，不会死锁（MP 文档 docs/source/mp/index.rst 第 370–371 行有此说法；舞台不收 .rst 锚点）。'),
                  note('文档', '没有 L2 时预取在调用线程上做完，所以主路下第一次 QUERY 通常就有结果。',
                       q='is served on the calling thread and its result is available when the call returns.',
                       ref=A(SMD, 50, 52))],
                 ['锁是三方经手的：**调度器的 LOOKUP 上锁**，worker 的 RETRIEVE 用锁（不再加锁），服务端 GPU 流上的回调放锁。'
                  '用不上的部分由调度器发 `free_lookup_locks` 放；谁都没放，读锁 300 秒后过期。' + R(DCFG, 432),
                  '只能前缀命中：第 2 块没命中，第 3 块就算在 L1 里也不算。' + R(API, 743)]),
             to='lookup_src'),
        part('hasher', '身份：滚动哈希', [930, 30, 270, 130], m=[206, 30, 130, 130], code='Session',
             kind='store', glyph=G_HASH0,
             say='token → 滚动哈希 → key', why='同前缀才同哈希', trap='request_id 不算身份',
             card=card(
                 '决定「一块 KV 是谁」。客户端只发原始 token；服务端按 256 token 一块切开，算链式哈希，再拼上模型名、rank 等，'
                 '组成存储用的 `ObjectKey`。存和取用的是同一套算法，所以同样的前缀一定算出同样的 key。',
                 ['每个 token 打包成 4 字节，256 token = 1024 字节一段。' + R(TC, 19),
                  '链式：`h_i = blake3(h_{i-1} ‖ 第 i 块)`，32 字节。' + R(TH, 43, 53),
                  '每个请求一个 Session，缓存已经算过的哈希，后面只从断点接着算；STORE / RETRIEVE 重算 key 时用的是同一条链。'
                  + R(SES, 172, 196) + R(ENG, 611, 622),
                  '`ObjectKey` = 块哈希 + 模型名 + kv_rank + object group + cache_salt + tags。' + R(API, 226, 250)],
                 [note('推断', 'KV 依赖整个前缀：把前一块的哈希喂进下一块，第 i 块的 key 就隐含「前面所有 token 都相同」，'
                              '这正是这块 KV 可以复用的前提。'),
                  note('推断', '为什么改成服务端用原始 token 算哈希（以前用 vLLM 的 block 哈希）：相关提交只有标题，仓库里没写原因。')],
                 [note('注释', '`request_id` 在 key 里，但不参与相等比较，只用来找 Session。',
                       q='The request_id field is for session tracking and is NOT included in equality/hash comparisons',
                       ref=A(CT, 63, 64)),
                  '`cache_salt` 参与身份：不同租户的相同 prompt 不会互相命中。' + R(CT, 79, 88)]),
             to='hash_src'),
        part('xfer', '传输模块', [500, 360, 230, 150], m=[4, 520, 126, 170], code='TransferModule',
             kind='act', glyph=G_XFER0,
             say='GPU↔CPU 真正的拷贝', why='拷完才转正、才放锁', trap='返回时还没拷完',
             card=card(
                 '服务端里真正搬数据的模块。STORE：把 vLLM GPU block 里的 KV 拷进 L1 的 CPU 内存；RETRIEVE：反过来，'
                 '拷回 vLLM 新分配的 block。拷贝在**服务端自己的 CUDA 流**上做。',
                 ['STORE ①：算 key → 检查 block 数够不够 → 让自己的流等 vLLM 的 event。' + R(LDT, 891, 894),
                  'STORE ②：向 L1 申请对象（staging）→ 入队 GPU→CPU 拷贝 → 在流上挂回调 `finish_write`。'
                  + R(LDT, 960, 962) + R(LDT, 1003, 1011),
                  'RETRIEVE ①：算 key → 让自己的流等 vLLM 的 event → 先定好拷完要放的锁。' + R(LDT, 1269, 1272) + R(LDT, 1314, 1318),
                  'RETRIEVE ②：`unsafe_read` 读 LOOKUP 已锁住的对象（不再加锁）→ 入队 CPU→GPU 拷贝 → 挂回调 `finish_read_by_owner` 放锁。'
                  + R(LDT, 1371, 1377),
                  '两者都**立即返回**一个完成 event 的句柄；GPU 真拷完时，提交 / 放锁由另一条线程来做。' + R(LDT, 269, 293),
                  '跑在「GPU 亲和线程池」上：同一个 vLLM 实例的请求总落在同一条线程，按到达顺序串行。'
                  + R(LDT, 714, 719) + R(AFF, 6)],
                 [note('注释', '要么全存，要么全不存：每块都拷成功才提交，否则整单放弃。', q=Q_FAILCLOSED,
                       ref=A(LDT, 1003, 1004)),
                  note('注释', '完成回调绕道 C++，让驱动线程不用拿 GIL——以前 GIL 和驱动锁互相等，发生过死锁。', q=Q_GIL,
                       ref=A(LDT, 269, 270)),
                  note('注释', '亲和线程：同一 vLLM 实例的操作都在一条线程上，共享的 GPU 临时缓冲就不用加锁。', q=Q_AFF,
                       ref=A(AFF, 8, 10))],
                 ['STORE 返回时数据还没拷完：回的是「已入队 + 完成 event」。对象要等回调 `finish_write` 之后才对读者可见。',
                  '「全不存」只指入队出错或 block id 不够；key 已在 L1 时只跳过那几块，其余照存——这不算失败。' + R(LDT, 973, 975),
                  'RETRIEVE 不加锁：锁是之前调度器的 LOOKUP 上的，它只负责用完放掉。']),
             to='xfer'),
        part('l1', 'L1 存储', [930, 215, 270, 295], m=[210, 540, 126, 260], code='StorageManager',
             kind='store', glyph=G_L10,
             say='CPU 内存里一块块 KV', why='先暂存后转正，锁住不淘汰', trap='L1 是 CPU 内存，不是显存',
             card=card(
                 'KV 存下来的地方：服务端进程里的 CPU 固定内存（pinned DRAM）。一个对象装一块 256 token 的 KV（所有层），'
                 '例子里约 36 MiB（按设定的模型形状推算）。主路没有 L2，所以只有这一层。' + R(LDT, 77, 104),
                 ['写入两阶段：`reserve_write` 先申请一个对读者**不可见**的 staging 对象；拷完调 `finish_write` 才转正。'
                  + R(SM, 289) + R(SM, 353) + R(L1B, 649),
                  '读取三步：`submit_prefetch_task`（查 + 上读锁）→ `unsafe_read`（读）→ `finish_read`（放锁）。'
                  + R(SM, 565) + R(SM, 710) + R(SM, 1424),
                  '读锁是计数：TP=1 时一把；TP>1 时每个 worker 各取各放，一次上 n 把。' + R(L1B, 328, 337),
                  '后台淘汰：用量过 80% 时删掉约 20% 的对象（按个数，不按内存），只挑没被读锁锁住的。' + R(DCFG, 491, 495) + R(L1B, 969, 984)],
                 [note('注释', 'staging 对象和常驻对象分开放，读这个 key 的人看不见它。', q=Q_STAGING, ref=A(L1B, 151, 154)),
                  note('推断', '所以拷了一半的数据不会被读到；读锁则是「正被某个请求用着，不许淘汰」的标记。'),
                  note('推断', '读写锁都带超时，防止客户端崩了把对象永远锁死（默认读锁 300 秒、写锁 600 秒；理由出自 MP 文档 docs/source/mp/index.rst 第 370–371 行，舞台不收 .rst 锚点）。')],
                 ['对已经存在的 key，`reserve_write` 会拒绝（`KEY_NOT_WRITABLE`），所以同一块不会写两份。' + R(L1B, 527, 529),
                  'L1 满了不会卡住：`reserve_write` 返回 OUT_OF_MEMORY，这次就不存——而且整批一起失败（差一块也全不存），也不会当场挤掉旧的。' + R(L1B, 555, 564)]),
             to='l1'),
    ]

    wires = [
        wire('w_lookup', 'sched', 'lookup', 'LOOKUP（不等）', ml='LOOKUP', why='等回执会卡调度线程', trap='这一下就上了锁',
             mfs='b', mts='t',
             card=card(
                 '调度器把这个请求的**全部 token** 和查找区间发给服务端，问「能命中多少」；之后再发 `QUERY_PREFETCH_STATUS` 取结果。',
                 ['走 ZMQ 异步 RPC，key 是 `IPCCacheServerKey`（worker_id 为空）。' + R(ADP, 865, 885),
                  '发全部 token 而不是只发要查的那段：服务端要从头算链式哈希。' + R(MD, 289)],
                 [note('提交', '把等 LOOKUP 回执这件事挪出调度线程。',
                       q='defer LOOKUP acknowledgement off the scheduler thread', ref='c:ba8acd84'),
                  note('注释', 'token 打包是增量的：每步只追加新 token，不重打整串，免得上下文越长调度越慢。',
                       q="repacking the whole sequence each time would put the context length back on the scheduler's critical path.",
                       ref=A(MD, 134, 136))],
                 '这条线（调度器进程发）和 worker 的 STORE / RETRIEVE（worker 进程发）是两个进程对同一批 key 的两次 RPC：'
                 '这条上锁，那条用锁、放锁。')),
        wire('w_hash', 'lookup', 'hasher', '算哈希链', why='存取同一套算法',
             card=card(
                 '查找模块把 token 交给 Session 算哈希，拿到每块的 `ObjectKey`。' + R(ENG, 578, 622),
                 '同一个 Session 后面还会被 STORE / RETRIEVE 用到：服务端重算 key 时，拿到的是同一条链。' + R(ENG, 611, 622),
                 [note('推断', 'key 全在服务端按同一套规则算，客户端不用知道哈希细节，存和取也不会两边算得不一样。')],
                 '传输模块也会走这一步，图上为了不交叉没画那条线。')),
        wire('w_lock', 'lookup', 'l1', '查 + 上读锁', ml='查+上锁', why='取之前不被淘汰', trap='不取的锁要显式放',
             fs='b', ts='l',
             card=card(
                 '预取任务对每个 key 在 L1 上 `reserve_read`：存在就读锁计数 +1，不存在就返回 `KEY_NOT_EXIST`。'
                 + R(PC, 1274, 1278) + R(L1B, 328, 337),
                 ['主路没有 L2：预取在当前线程同步做完。' + R(PC, 718, 722),
                  '命中后在 Session 里记下「这次锁了前几块」，以后放锁按它来。' + R(LK, 800, 804)],
                 [note('注释', '用不上的块要显式放锁，否则一直锁到超时。', q=Q_FREE, ref=A(ADP, 1025, 1026)),
                  note('推断', '查到就锁：查到和真正取回之间隔着调度步，这段时间块不能被淘汰。')],
                 ['放锁有三条路：RETRIEVE 拷完后的流回调；查到却用不上的（不用取时的全部命中、多服务端取最小后的尾部、APC 缩水后被放弃的 LOOKUP），调度器发 `free_lookup_locks`。'
                  + R(CON, 1678),
                  '第三条：谁都没放（比如客户端崩了），就等读锁 300 秒超时。' + R(DCFG, 432)])),
        wire('w_meta', 'sched', 'worker', '工单 LoadStoreOp', ml='工单', why='跨进程，随调度结果下发',
             trap='不是 RPC，是元数据',
             card=card(
                 '调度器每步把要做的 STORE / RETRIEVE 打成工单，随 vLLM 的 `SchedulerOutput` 一起发到 worker 进程。'
                 + R(MD, 666, 672),
                 ['一张 `LoadStoreOp`：打包的 token、要搬的 GPU block 编号、token 区间 `[start, end)`、`skip_first_n_tokens`。'
                  + R(ADP, 483, 502),
                  '例子：A 的 STORE 工单 `[0, 768)`，block b0–b47；B 的 RETRIEVE 工单 `[0, 768)`，写进 B 的前 48 个 block。'],
                 [note('推断', '决定在调度器做，动手在 worker 做：调度器知道每个请求的全局状态，worker 只在前向前后照单执行。')],
                 [note('注释', '取回时要跳过 vLLM 自己已有的那几个 token（`skip_first_n_tokens`）：那些 block 可能正被别的请求读，'
                              '重写一遍就是跨流的数据竞争。例子里 vLLM 本地命中 0，所以跳过 0。',
                       q='to avoid a cross-stream data race', ref=A(MD, 640, 643))])),
        wire('w_done', 'worker', 'sched', '完成名单', why='拷完才回收 block', trap='只报延迟回收的',
             cls='dash',
             card=card(
                 'worker 每步 `get_finished` 报出已完成的「存」（finished_sending）和「取」（finished_recving）请求，'
                 '经 vLLM 回到调度这边。' + R(CON, 1228, 1258),
                 ['A：结束时调度器因为发过 STORE，返回「延迟回收」，vLLM 先留着 A 的 63 个 block。' + R(CON, 1794) + R(CON, 1824),
                  '等 worker 报 A 存完，vLLM 才回收这些 block。' + R(CON, 1252, 1256),
                  'B：worker 报 B 取完，vLLM 才把 B 放进前向。' + R(ADP, 2193, 2197)],
                 [note('注释', '只在 STORE 可能还在读这些 block 时才留着它们。', q=Q_KEEP, ref=A(CON, 1791)),
                  note('提交', '只对真的发过 STORE 的请求延迟回收。',
                       q='Delay frees only for requests with a scheduled store', ref='c:752e01fd')],
                 [note('注释', '反过来：没要求延迟回收的请求不能报「存完」，否则 vLLM 会断言失败。',
                       q='asserts on a finished_sending report for a request it freed.', ref=A(CON, 1253))])),
        wire('w_xfer', 'worker', 'xfer', 'STORE/RETRIEVE+event', ml='存/取+event', why='服务端的流先等 vLLM 的 event',
             trap='回的是「已入队」，不是拷完',
             card=card(
                 'worker 把工单变成 RPC 发给服务端，附带一个 CUDA event 的 IPC 句柄。' + R(WT, 719, 722) + R(WT, 791, 798),
                 ['服务端的 ZMQ 主循环收包、按操作名分派；STORE / RETRIEVE 声明了「客户端亲和」，进 GPU 亲和线程池。'
                  + R(LDT, 714, 719) + R(MQ, 299),
                  '服务端回复「已入队」和一个完成 event 的句柄；worker 要等回复和 event 都完成才算结束。' + R(FUT, 260, 269),
                  '启动时的 `REGISTER_KV_CACHE` 也走这条线，在 ZMQ 主循环线程上直接处理。' + R(LDT, 534, 535)],
                 [note('注释', 'worker 手里的 future 同时等 RPC 回复和设备 event。',
                       q='methods pend on both the original future and the device event', ref=A(FUT, 263, 264)),
                  note('注释', '回复不从线程池里直接发，而是交回主循环发：zmq socket 不是线程安全的。',
                       q='zmq sockets are not thread-safe', ref=A(MQ, 197, 198))],
                 '回复到了不代表拷完了：拷贝还在服务端的 GPU 流上排着。')),
        wire('w_ipc', 'xfer', 'gpu', 'CUDA IPC 直接读写', ml='IPC 读写', why='不经 vLLM 进程中转', trap='存是读它，取是写它',
             fs='t', ts='r',
             card=card(
                 '服务端用启动时导入的句柄，直接读写 vLLM 的 GPU block：STORE 是 GPU→CPU，RETRIEVE 是 CPU→GPU。',
                 ['先让服务端自己的 CUDA 流等 vLLM 的 event，再入队拷贝内核。'
                  + R(LDT, 891, 894) + R(LDT, 978, 986),
                  '工单里的 block 数不够（A 的 3 块要 48 个），就整单放弃。' + R(LDT, 846, 871)],
                 [note('注释', 'block 列表太短会让拷贝内核读到越界的显存，所以先查。',
                       q='would otherwise drive the transfer kernel to read out-of-bounds GPU memory', ref=A(LDT, 847, 848)),
                  note('推断', 'event 给两个进程的 GPU 流排好先后：vLLM 写完 KV，服务端才开始读。')],
                 '服务端还在读这些 block 时，vLLM 不能把它们回收给别人——所以有「延迟回收」（见「完成名单」）。')),
        wire('w_rw', 'xfer', 'l1', '写入 / 读出并放锁', ml='写/读', why='拷完才转正，拷完才放锁', trap='提交在回调里，不在函数里',
             card=card(
                 ['STORE：`reserve_write` 申请对象，拷完 `finish_write` 转正。' + R(LDT, 960, 962) + R(LDT, 1009),
                  'RETRIEVE：`unsafe_read` 读已锁的对象，拷完 `finish_read_by_owner` 放锁。' + R(LDT, 1374) + R(SM, 715)],
                 ['「拷完」那一刻由 GPU 流上的回调决定：驱动线程触发 → 排空线程调 `finish_write` / `finish_read`。'
                  + R(LDT, 269, 293),
                  '转正时还会通知监听者（淘汰控制器等）。' + R(L1B, 658)],
                 [note('推断', '必须等 GPU 真拷完才能让对象可见、才能放锁：否则别人可能读到半截数据，或者块在拷的途中被淘汰。')],
                 '效果不在 STORE / RETRIEVE 函数里同步发生：函数早就返回了，提交和放锁是另一条线程后来做的。')),
    ]

    steps = [
        beat('启动：注册显存',
             'vLLM 分配好 KV 显存后，worker 把它包装成跨进程句柄，发 `REGISTER_KV_CACHE`；服务端导入句柄，从此能直接读写 '
             'vLLM 的 block。调度器也问服务端要了块大小：256 是 vLLM block 大小 16 的倍数，通过。',
             note('推断', '注册只做一次；之后每张工单只写 block 编号，数据本身从不经过 vLLM 进程中转。'),
             focus=['gpu', 'worker', 'w_xfer', 'xfer']),
        beat('A：调度器发 LOOKUP',
             '请求 A 来了：prompt **1000** token。向下取整到整块 → 查 `[0, 768)`（3 块），尾巴 232 不查。调度器把 1000 个 '
             'token 打包发 LOOKUP，**不等回执**；当场问一次结果，还没回来，就告诉 vLLM「下一步再问我」。',
             note('注释', '等回执就是在调度线程上多走一趟服务端往返。', q=Q_NOWAIT, ref=A(ADP, 874, 875)),
             focus=['sched', 'w_lookup', 'lookup']),
        beat('A：算哈希、查 L1，全 miss',
             '服务端把前 768 个 token 按 256 一块切开，算链式哈希得到 `[h0, h1, h2]`，建 Session 记下 token。'
             '再到 L1 批量 `reserve_read`：L1 还是空的，**3 块全 miss**，命中 0。',
             note('推断', '链式哈希：h2 里已经含了 h0、h1，所以 h2 相同就意味着前 768 个 token 全相同。'),
             focus=['lookup', 'w_hash', 'hasher', 'w_lock', 'l1'],
             state={'lookup': g_checks([('h0 在 L1', 'no'), ('h1 在 L1', 'no'), ('h2 在 L1', 'no')])}),
        beat('A：拿到 0，下 STORE 工单',
             '最早下一个调度步，调度器拿到命中 0。vLLM 给 A 分配 ceil(1000/16) = **63** 个 block。算要存多少：已算 1000、'
             '已分配 1008、封顶 prompt 1000，减去已存 0 → 够 3 个整块。于是工单：STORE `[0, 768)`，block **b0–b47**（3 × 16）。',
             note('注释', '已算的 token 取 vLLM 已有和 LMCache 已有的 max，不相加——相加会把重叠的前缀算两遍。',
                  q='so they must NOT be summed', ref=A(MD, 380, 382)),
             focus=['sched', 'w_meta', 'worker'],
             state={'sched': g_kv([('请求', 'A · 63 block'), ('命中', '0'), ('要存', '[0, 768)')]),
                    'worker': g_list(['STORE [0, 768)', 'b0–b47（48 个）'], on=0)}),
        beat('A：前向结束，发 STORE',
             'A 的前向在 GPU 上排好队后，worker 在当前 CUDA 流上录一个 event，把 STORE 工单连同 event 句柄发给服务端，'
             '**不等**，直接返回让 vLLM 继续下一步。',
             note('推断', '只是「录 event + 发消息」，vLLM 不用 synchronize，推理线程不被卡住。'),
             focus=['worker', 'w_xfer', 'xfer'],
             state={'worker': g_list(['STORE 已发出', '等它做完…'], on=0)}),
        beat('A：服务端拷贝（写入中）',
             '服务端用 Session 里的 token 重算出同一条链 `[h0, h1, h2]`；检查 block 够 48 个；让自己的流等 vLLM 的 event；'
             '向 L1 申请 3 个约 **36 MiB** 的 staging 对象；入队 GPU→CPU 拷贝，并在流上挂回调 `finish_write`。'
             '然后立即回复「已入队 + 完成 event」。',
             note('注释', '每块都拷成功才提交，否则整单放弃。', q=Q_FAILCLOSED, ref=A(LDT, 1003, 1004)),
             focus=['xfer', 'w_ipc', 'gpu', 'w_rw', 'l1'],
             state={'xfer': g_text('拷 3 块', 'GPU → CPU', tone='run'),
                    'l1': g_list(['h0 写入中', 'h1 写入中', 'h2 写入中'])}),
        beat('A：拷完，转正',
             'GPU 上的拷贝完成，回调触发，另一条线程调 `finish_write`：3 个对象进入常驻区，从此对读者可见。'
             'L1 里现在有 `h0, h1, h2` 共约 108 MiB，无锁。',
             note('注释', 'staging 对象读者看不见，转正之后才看得见。', q=Q_STAGING, ref=A(L1B, 151, 154)),
             focus=['l1', 'w_rw', 'xfer'],
             state={'l1': g_list(['h0 可读', 'h1 可读', 'h2 可读', '共约 108 MiB']),
                    'xfer': g_text('完成', 'event 已完成', tone='ok')}),
        beat('A：报完成，回收 block',
             'A 结束时调度器因为发过 STORE，答「延迟回收」并把 A 记进名单；名单下一步随元数据到 worker，worker 确认 A 的 STORE'
             '「回复已到 + event 已完成」才报 A 存完，vLLM 这时才回收 63 个 block。同时发 `END_SESSION`，服务端删掉 A 的 Session。'
             'A 生成的 100 个 token 不存。',
             note('注释', '只在 STORE 可能还在读时留着 block。', q=Q_KEEP, ref=A(CON, 1791)),
             focus=['w_done', 'worker', 'sched'],
             state={'sched': g_kv([('请求', 'A 已结束'), ('已存', '768 token'), ('block', '已回收')]),
                    'worker': g_list(['A 存完 ✓'], on=0)}),
        beat('B：LOOKUP，命中 3 块并上锁',
             '请求 B：prompt **1200** token，前 900 与 A 相同；vLLM 自己的前缀缓存已经挤掉了 A，本地命中 0。查 `[0, 1024)`，4 块。'
             '前 3 块内容和 A 一样 → 哈希也一样；第 4 块 900 之后不同，A 也没存过 → `h3′` 不存在。前 3 块 `reserve_read` '
             '成功，**各上 1 把读锁**；按前缀数，命中 3 块 = **768** token。',
             note('推断', '查到就锁：从查到到真正取回要隔几个调度步，这段时间这 3 块不能被淘汰。'),
             focus=['lookup', 'w_lock', 'l1', 'w_hash', 'hasher'],
             state={'sched': g_kv([('请求', 'B · 1200 token'), ('查找', '[0, 1024)'), ('状态', 'PREFETCHING')]),
                    'worker': G_WORKER0,
                    'hasher': g_cells([cell('h0–h2', '同 A', 'pick'), cell('h3′', '新', 'new')]),
                    'lookup': g_checks([('h0 在 L1', 'ok'), ('h1 在 L1', 'ok'), ('h2 在 L1', 'ok'), ('h3′ 在 L1', 'no')]),
                    'l1': g_list(['h0 读锁 1', 'h1 读锁 1', 'h2 读锁 1'], on=[0, 1, 2]),
                    'xfer': G_XFER0}),
        beat('B：拿到 768，下 RETRIEVE 工单',
             '调度器拿到 768：vLLM 命中 0，要加载 768 − 0 = 768，返回 `(768, True)`——True 是「异步加载，加载期间先别算 B」。'
             'vLLM 分好 block 后，状态 `PREFETCHING → WAITING_FOR_LOAD`；工单：RETRIEVE `[0, 768)`，跳过 0 个 token，'
             '写进 B 的前 48 个 block。',
             note('注释', 'vLLM 自己已有的整块，LOOKUP 从一开始就跳过、不上锁；`free_lookup_locks` 放的是「查到了却用不上」的锁（例如根本不用取时放全部），免得锁到超时。这里 768 全要取，不用放。',
                  q=Q_FREE, ref=A(ADP, 1025, 1026)),
             focus=['sched', 'w_meta', 'worker'],
             state={'sched': g_kv([('请求', 'B · 命中 768'), ('状态', 'WAIT_LOAD'), ('要取', '[0, 768)')]),
                    'worker': g_list(['RETRIEVE [0, 768)', 'B 的前 48 个 block'], on=0)}),
        beat('B：拷回 GPU，拷完放锁',
             'worker 在前向之前发 RETRIEVE（B 这一步不参与前向）。服务端重算出同样的 3 个 key，先让流等 vLLM 的 event，'
             '再**不加锁**地读已锁住的对象，入队 CPU→GPU 拷贝，写进 B 的 48 个 block；流上挂的回调在拷完后放掉 3 把读锁。',
             note('推断', '锁是 LOOKUP 上的，RETRIEVE 只负责用完放掉；放锁挂在 GPU 流回调上，拷完之前对象一直锁着、不会被淘汰。'),
             focus=['xfer', 'w_ipc', 'gpu', 'w_rw', 'l1'],
             state={'xfer': g_text('拷 3 块', 'CPU → GPU', tone='run'),
                    'gpu': g_cells([cell('b0–15', 'h0', 'new'), cell('b16–31', 'h1', 'new'), cell('b32–47', 'h2', 'new'),
                                    cell('b48–74', '自己算', 'ghost')]),
                    'l1': g_list(['h0 可读', 'h1 可读', 'h2 可读'])}),
        beat('B：只算 432 个 token',
             'worker 报 B 取完；vLLM 调度 B 时 `num_computed_tokens = 768`，只需算 1200 − 768 = **432** 个 token，省掉 64% 的 '
             'prefill。B 算完同样会被存：已存 768，新的一整块 `[768, 1024)` → L1 只新申请 `h3′` 这一个对象。',
             note('推断', '前 3 块已在 L1，存的时候从「已存 768」往后算，就不会再发一遍；就算重复发，L1 也会拒绝已存在的 key。'),
             focus=['sched', 'w_done', 'worker', 'l1'],
             state={'sched': g_kv([('请求', 'B'), ('取回', '768 token'), ('要算', '432 token')]),
                    'worker': g_list(['B 取完 ✓', '下一步：STORE [768, 1024)'], on=0),
                    'xfer': g_text('完成', '3 把锁已放', tone='ok'),
                    'l1': g_list(['h0 可读', 'h1 可读', 'h2 可读', 'h3′ 写入中'], on=3)}),
    ]

    groups = [group('vLLM（调度器进程 + GPU worker 进程）', [10, 4, 440, 514]),
              group('LMCache 服务进程', [482, 4, 730, 514])]

    base['blocks'] = [scene(1220, 520, parts, wires, steps, mw=340, mh=930, groups=groups,
                            cap='主路：MP 模式 · ZMQ · 服务端用 CUDA IPC 自己拷 · 只有 L1 · 块 = 256 token · TP=1')]
    base['children'] = [
        slot('sched', '调度侧连接器：查多少、存多少、何时放锁'),
        _worker_src(),
        _lookup_src(),
        _hash_src(),
        slot('xfer', '传输模块：STORE / RETRIEVE 在服务端怎么拷'),
        slot('l1', 'L1 存储：两阶段写入、读锁与淘汰'),
    ]
    return base


# ---------------------------------------------------------------- 源码页
def _worker_src():
    return node(
        'worker_src', 'worker 侧：发取 / 发存', 'start_load_kv / wait_for_save：照单发 RPC，都不等',
        '顶层「worker 侧连接器」框里面：前向前发 RETRIEVE、前向后发 STORE 的两个函数。',
        lead='你在顶层「worker 侧连接器」里面。两个函数长得几乎一样：挑出本步某个方向的工单 → 录一个 event → 批量发出 → 返回。',
        src=A(CON, 1077, 1201),
        blocks=[
            code(CON, [(1077, 1120)], notes=[
                at((1101, 1107), None, '只挑方向是 RETRIEVE 的工单；STORE 留给前向之后的 `wait_for_save`。B 的例子里就是 '
                                       'RETRIEVE `[0, 768)`、B 的前 48 个 block。', has='"RETRIEVE"'),
                at(1112, '推断', '在当前 CUDA 流上录一个 event：服务端的流先等它，再往这些 block 里写，两边的 GPU 操作就排好了先后。',
                   has='create_recorded_event'),
                at((1114, 1120), None, '本步所有 RETRIEVE 一次批量发出，发完就返回；做没做完，之后靠 `get_finished` 轮询。',
                   has='batched_submit_retrieve_requests'),
            ], cap='start_load_kv：前向之前，发 RETRIEVE'),
            code(CON, [(1165, 1201)], notes=[
                at(1171, None, '这句文档是 vLLM 接口的原话。本实现里这个函数并不等：真正防止 block 被覆盖的是调度侧的延迟回收，'
                               '以及抢占时的 `flush_inflight_stores`。', has='prevents overwrites'),
                at((1180, 1186), None, '只挑方向是 STORE 的工单。A 的例子里是 STORE `[0, 768)`、block b0–b47。',
                   has='"STORE"'),
                at(1193, '推断', '这时 A 的前向已经在流上排好队；在它后面录 event，服务端等到它，读到的 KV 就是算完的。',
                   has='create_recorded_event'),
                at((1195, 1201), None, '批量发 STORE，立刻返回，让 vLLM 进下一步；拷贝在服务端的 GPU 流上进行。',
                   has='batched_submit_store_requests'),
            ], cap='wait_for_save：前向之后，发 STORE（名字有 wait，其实不等）'),
        ])


def _lookup_src():
    return node(
        'lookup_src', 'LOOKUP 的关键几段', 'lookup()：算哈希 → 建 Session → 提交预取（查 + 上锁）→ 数前缀命中',
        '顶层「查找模块」框里面：LOOKUP 在服务端的几段关键代码。',
        lead='你在顶层「查找模块」里面。下面按执行顺序摘了五段：算哈希、建 Session、提交预取（这一步上锁）、数命中、返回 token 数。',
        src=A(LK, 245, 844),
        blocks=[
            code(LK, [(245, 253), (300, 301), (382, 395)], notes=[
                at((251, 253), None, '对查找区间算链式哈希。A 查 `[0, 768)` 得到 `[h0, h1, h2]`；B 查 `[0, 1024)` 得到 4 个，前 3 个和 A 相同。',
                   has='compute_packed_chunk_hashes'),
                at((300, 301), None, '每个请求一个 Session，记下全部 token；之后 STORE / RETRIEVE 用它重算出同一条链。',
                   has='set_tokens'),
                at((382, 395), None, '把哈希展开成 `ObjectKey`，提交预取。没有 L2 时就在这条线程上对 L1 批量 `reserve_read`：'
                                     '存在的当场上读锁。', has='submit_prefetch_task'),
            ], cap='lookup()：算哈希、建 Session、提交预取'),
            code(LK, [(784, 804), (844, 844)], notes=[
                at(784, None, '预取按前缀数出命中块数；这一行把它从「相对查找起点」换回绝对块号（主路从第 0 块查起，不变）。B 的例子：3 块。', has='found_count += job.start_chunk'),
                at((786, 791), '注释', '把「这次锁了哪些」记到 Session 上，后面 `free_lookup_locks` 才能准确放锁。',
                   q='so a later free_lookup_locks can reconstruct which keys the prefetch read-locked',
                   has='free_lookup_locks'),
                at(844, None, '返回命中的 token 数：3 块 × 256 = 768。', has='found_count * job.chunk_size'),
            ], cap='预取完成后：数命中、记锁、返回 token 数'),
        ])


def _hash_src():
    return node(
        'hash_src', '滚动哈希源码', 'token → 链式哈希 → ObjectKey 的源码',
        '顶层「身份：滚动哈希」框里面：一块的哈希怎么算、Session 怎么接着算、key 由哪些字段组成。',
        lead='你在顶层「身份：滚动哈希」里面。三段：一块的哈希（喂进前一块的哈希）、Session 从断点接着算、身份由哪些字段组成。',
        src=A(TH, 43, 53),
        blocks=[
            code(TH, [(43, 53)], notes=[
                at((46, 51), None, '先把前一块的哈希喂进去（bytes 或 int 两种形式都接受）。', has='prefix_hash'),
                at(52, '推断', '再喂这一块的 1024 字节（256 个 token × 4 字节）。因为前缀也在里面，h2 相同就意味着前 768 个 token 全相同。',
                   has='h.update(chunk)'),
            ], cap='一块的哈希：blake3(前一块哈希 ‖ 这一块的 token)'),
            code(SES, [(172, 196)], notes=[
                at(175, '注释', '已经算过的块不重算：同一个请求后来要更长的链（比如 STORE 时），只从断点接着算。',
                   q='Uses cached state to skip already-computed chunks.', has='skip already-computed'),
                at((188, 196), None, '每算一块，就把它记成下一块的前缀，并追加到 `chunk_hashes`。', has='last_prefix_hash'),
            ], cap='Session._compute_hash：增量地算整条链'),
            code(API, [(226, 234)], notes=[
                at((229, 234), None, '身份字段：块哈希、kv_rank、模型名、object group、cache_salt、tags。主路 TP=1、'
                                     '一个 group，所以 rank 和 group 都是 0。', has='chunk_hash'),
            ], cap='身份由哪些字段组成'),
        ])
