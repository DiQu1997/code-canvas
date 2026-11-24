from cv import *

LDT = 'lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py'
NC = 'lmcache/v1/multiprocess/native_completion.py'
AFF = 'lmcache/v1/multiprocess/affinity_pool.py'
MQ = 'lmcache/v1/multiprocess/transport/zmq_impl/mq.py'
CFG = 'lmcache/v1/multiprocess/config.py'
WT = 'lmcache/v1/client/worker_transfer.py'
FUT = 'lmcache/v1/transport/futures.py'
EIPC = 'lmcache/v1/platform/base/event_ipc.py'
BCC = 'lmcache/v1/platform/base/cache_context.py'
CCC = 'lmcache/v1/platform/devices/cuda/cache_context.py'
DSP = 'lmcache/v1/multiprocess/object_group_transfer/dispatch.py'
DC = 'lmcache/v1/multiprocess/object_group_transfer/direct_copy.py'
STG = 'lmcache/v1/multiprocess/object_group_transfer/staged.py'
PLN = 'lmcache/v1/multiprocess/object_group_transfer/planning.py'
KLG = 'lmcache/v1/kv_layer_groups.py'
SM = 'lmcache/v1/distributed/storage_manager.py'
DCFG = 'lmcache/v1/distributed/config.py'
MD = 'lmcache/integration/vllm/lmcache_mp_metadata.py'
CON = 'lmcache/integration/vllm/lmcache_mp_connector.py'
CPDOC = 'docs/design/v1/multiprocess/lmcache_driven_transfer_copy_paths.md'
TOWN = 'tests/v1/multiprocess/test_l1_owner_completion.py'
TSTG = 'tests/v1/multiprocess/test_staged_loop.py'
KERN = 'csrc/cuda/mp_mem_kernels_detail.cuh'
CREC = 'csrc/cuda/completion_recorder.cpp'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 引文（逐字照抄）
Q_GIL = "Route finish_write / finish_read_prefetched through a C++ host callback so the driver thread doesn't acquire the GIL."
Q_ABAB = "Extends PR #2952's AB-BA fix (GIL x driver lock) to Server.store/retrieve."
Q_AFF = ('so that all operations for a given vLLM instance land on one thread, eliminating the need for '
         'per-instance locks on the shared temporary GPU buffer.')
Q_FIFO = 'Within each worker, tasks execute sequentially in FIFO order.'
Q_SINGLE = 'Single-thread assumption: no lock needed.'
Q_EVENT = 'Producer event that orders reads of the engine KV cache.'
Q_HOLD = ('A stream wait queued on an imported event refers to it until the stream has consumed the wait, '
          'so the import must outlive the handler that queued it.')
Q_OWNER = 'Captured before any copy runs, so a failed store aborts per owner.'
Q_ABORT = 'Abort after the partial copies drain, not at write TTL.'
Q_RECOMP = ('the whole store is skipped and nothing is committed; a subsequent retrieve misses and the engine '
            'recomputes.')
Q_FAILCLOSED = 'Fail closed: commit the reserved objects only when every chunk copied successfully'
Q_SHORT = 'would otherwise drive the transfer kernel to read out-of-bounds GPU memory'
Q_RETAIN = 'Capture every retained lock before enqueuing GPU work'
Q_ORDER = 'assert order == ["callback", "copy", "copy", "record", "callback"]'
Q_PEND = 'pend on both the original future and the device event'
Q_EMPTY = 'An empty handle means the remote side submitted no device work'
Q_MIN = ('Smallest per-layer paged block the direct path moves. Below it, per-entry cost dominates and the '
         'staged kernel path wins')
Q_KNOB = 'There is no configuration knob: the direct path is taken whenever it can be.'
Q_APC = ('the retrieve writes on the LMCache CUDA stream while concurrent requests may read these '
         'APC-shared blocks on the vLLM stream.')
Q_SKIPTEST = ('The skip covers chunk 0 and one block of chunk 1: chunk 0 is not staged, chunk 1 keeps its slot, '
              'and the kernel skips five blocks.')


# ---------------------------------------------------------------- 小图（同一套数：A 存 [0,768) = h0 h1 h2 = b0–b47；B 取回；B′ skip 64）
def S(rows):
    """服务端 CUDA 流：队头在上。rows = [(文字, 状态)]；'ok' = GPU 已执行完。"""
    return g_checks(rows)


ST_A = ['等 ev_A', '回调：放导入的 ev_A', '拷 h0（D2H）', '拷 h1（D2H）', '拷 h2（D2H）', '★ 录 ev_done', '回调：finish_write']
ST_B = ['等 ev_B', '回调：放导入的 ev_B', '拷 h0–h2（一批 H2D）', '★ 录 ev_done', '回调：放 3 把读锁']


def stA(n_enq, n_done=0):
    return S([(t, 'ok' if i < n_done else None) for i, t in enumerate(ST_A[:n_enq])])


def stB(n_enq, n_done=0):
    return S([(t, 'ok' if i < n_done else None) for i, t in enumerate(ST_B[:n_enq])])


AFF_ROWS = ['48 个 id ≥ 3×16', '流等 ev_A', '预留 h0 h1 h2', '发 3 次拷贝', '录 ev·挂提交']


def affA(n_ok):
    return g_checks([(t, 'ok' if i < n_ok else None) for i, t in enumerate(AFF_ROWS)])


G_WORKER0 = g_kv([('发出', 'STORE [0, 768)'), ('block', 'b0–b47'), ('ev_A', 'IPC 句柄')])
G_POOL0 = g_kv([('线程数', '1（默认）'), ('worker 0', '还没见过')])
G_COPY0 = g_kv([('b0–b15', '→ h0'), ('b16–b31', '→ h1'), ('b32–b47', '→ h2')])
G_STREAM0 = g_text('空', '流上没有活')
G_DRAIN0 = g_list(['C++ 缓冲：空', '每 5 ms 取'])
G_L10 = g_list(['（空）'])


def nodes():
    return {'xfer': _xfer()}


def _xfer():
    parts = [
        # ------------------------------------------------ 框外：发起方
        part('worker', 'worker（vLLM 进程）', [20, 340, 190, 160], m=[72, 10, 200, 150], kind='ext',
             glyph=G_WORKER0,
             say='交进句柄，拿回句柄', why='两边只交换 event', trap='两个 event 方向相反',
             card=card(
                 '框外的发起方，顶层「worker 侧连接器」那个框。这一层只关心它交进来什么、拿回什么。',
                 ['交进来：key、instance_id、block id，外加一个 event 的 **IPC 句柄**（bytes）。' + R(WT, 719, 722),
                  '这个 event 是 `Event(interprocess=True)`，录在 vLLM 当前的流上，再导出成 bytes。'
                  + R(EIPC, 221, 238),
                  '拿回去：服务端的完成 event 句柄。worker 导入它，之后每步 `query()`：RPC 回复到了**且** event 完成才算做完。'
                  + R(FUT, 295, 302) + R(FUT, 415, 433)],
                 [note('注释', 'worker 手里的 future 同时等两样：RPC 回复和设备 event。', q=Q_PEND, ref=A(FUT, 263, 264)),
                  note('注释', '回来的句柄为空 = 服务端没提交任何 GPU 工作，RPC 回来就是终态。', q=Q_EMPTY,
                       ref=A(FUT, 266, 267)),
                  note('推断', 'vLLM 路径里 worker 的流从不等服务端的完成 event（`wait_for_layer_load` 直接 return）；'
                               '靠「请求挂起 + 每步 query」保证没人读半截数据。' + R(CON, 1122, 1133))],
                 ['有两个 event，方向相反：**ev_A**（vLLM → 服务端，让服务端的流等它）和 **ev_done**（服务端 → worker，让 worker 轮询）。']),
             to='worker_src'),

        # ------------------------------------------------ 亲和池
        part('pool', '亲和池', [20, 30, 190, 150], m=[72, 226, 200, 130], kind='gate', code='AffinityThreadPool',
             glyph=G_POOL0,
             say='按客户端身份派线程', why='一实例一线程，缓冲免锁', trap='默认全局只有 1 条',
             card=card(
                 'ZMQ 主循环收到 STORE / RETRIEVE 后，不自己跑，而是按「谁发来的」交给一条固定的线程。',
                 ['亲和 key = `hash(客户端 identity)`：同一个 worker 进程永远是同一个 key。' + R(MQ, 298, 300),
                  '第一次见到某个 key，就占下一个槽位 `_next_slot % 线程数`，之后永远是它。' + R(AFF, 131, 145),
                  '每条线程一个队列，逐个取任务执行。' + R(AFF, 107, 125),
                  '线程数默认 **1**（`--max-gpu-workers`）。' + R(CFG, 59)],
                 [note('注释', '同一实例的操作都落在一条线程上，共享的 GPU 临时缓冲就不用加锁。', q=Q_AFF, ref=A(AFF, 8, 10)),
                  note('注释', '那块 block id 缓冲（以及 4 槽暂存缓冲）每实例只有一份，每次覆盖写，靠「只有一条线程在用」免锁。',
                       q=Q_SINGLE, ref=A(CCC, 417, 419)),
                  note('提交', '引入亲和池的同一个提交里，删掉了原来的每设备锁。',
                       q='support worker-affinity in the MQ thread pool', ref='c:3137ce0b')],
                 ['线程数不是「每 worker 一条」：默认全局 1 条，所有 worker 的 STORE / RETRIEVE 都排在它上面；'
                  '槽位用完后来的 key 共享线程，只警告一次。' + R(AFF, 156, 166),
                  '同一条线程上 STORE 和 RETRIEVE 串行：后到的等前一个**入队完**（不是拷完）才开始。']),
             to='xfer.pool'),

        # ------------------------------------------------ 亲和线程
        part('aff', '亲和线程：只下单', [330, 20, 260, 230], m=[72, 422, 200, 240], kind='act', n='1',
             code='store()', glyph=affA(0),
             say='检查、预留、把活排进流', why='不等拷贝，线程很快空出来', trap='返回时还没拷完',
             card=card(
                 '亲和线程上跑 `store()` / `retrieve()`。它只做「排队下单」：检查、向 L1 预留、把等待 / 拷贝 / event / 回调'
                 '依次排进服务端的 CUDA 流，然后就返回。它自己**不等任何一次拷贝**。',
                 ['进入 `torch_dev.stream(服务端流)`：之后发的东西默认都进这条流；最先建好自己的完成 event。' + R(LDT, 840, 844),
                  '守卫：每组 block id 数 < chunk 数 × 16 → 整单放弃。查的是裁剪前的原始 id。' + R(LDT, 853, 858),
                  '先算「全空块掩码」，再把 block id 裁剪、拷进 GPU 缓冲（这一步会就地改写列表）。' + R(LDT, 874, 889),
                  '每个 object group：`reserve_write` → 记 owner → `transfer_object_group`。' + R(LDT, 960, 987),
                  '`finally`：录完成 event → 全成功挂 `finish_write`，否则挂 `abort_write`。' + R(LDT, 1001, 1018)],
                 [note('注释', '「要么全存要么全不存」：失败时整单不提交，之后的取回只是 miss、引擎重算——丢的是一次复用，不是正确性。',
                       q=Q_RECOMP, ref=A(LDT, 784, 786)),
                  note('注释', 'block id 不够时整单放弃：否则拷贝内核会越界读显存。', q=Q_SHORT, ref=A(LDT, 847, 848)),
                  note('推断', '亲和线程只排队、不等 GPU，所以一次 STORE 在 CPU 上很快结束，同一条线程马上能接下一单。')],
                 ['「全存或全不存」只管入队时抛异常、block id 不够。某些 key **已在 L1** 或 **L1 没空间**不算失败：'
                  '那几块被跳过，其余照存。' + R(LDT, 970, 975),
                  '返回的 bool 只表示「没有致命错误」；哪些 chunk 真存了要看第三个返回值（掩码）。' + R(LDT, 1063, 1079),
                  note('推断', '`try` 只包住「入队」这些调用：GPU 上异步执行时才出的错，不归这里的失败处理管。')]),
             to=['xfer.store', 'xfer.retrieve']),

        # ------------------------------------------------ 拷贝路径
        part('copy', '拷贝：三选一 + 平铺', [330, 330, 260, 170], m=[72, 728, 200, 170], kind='gate', n='2',
             code='transfer_object_group', glyph=G_COPY0,
             say='16 个 block ↔ 1 个对象', why='块够大就走 direct', trap='skip 按 block 向下取整',
             card=card(
                 '把 vLLM 的分页 block 和 L1 里连续的对象互相搬：一个 chunk = 16 个 block ↔ 一个 `[kv][layer][token][nh*hs]` '
                 '对象（例子里 36 MiB）。有三条路径，按条件挑一条。',
                 ['平铺：第 j 个 block id → 对象里第 j×16 到 j×16+15 个 token。b0–b15 → h0，b16–b31 → h1，b32–b47 → h2。'
                  'block 编号不必连续，只要按顺序给（内核源码见下一层）。',
                  '三选一（每段同类对象挑一次）：**direct** 每个 chunk 一次 `cudaMemcpyBatchAsync`，不用暂存、不跑内核；'
                  '**native plan** 暂存拷贝 + 内核，整批一次 native 调用；**staged loop** 兜底，逐批在 Python 里发射。'
                  + R(DSP, 69, 111),
                  '例子：每层每块 16 token × 1024 × 2 B = **32 KiB**；direct 的门槛是「小于 32 KiB 才拒」，刚好过线。' + R(DC, 110, 113),
                  'staged 的方向：取回先 stage（宿主→暂存）再内核（暂存→分页）；存反过来。' + R(STG, 399, 413)],
                 [note('注释', '32 KiB 门槛：块再小，逐条 memcpy 的开销占上风，staged 内核路径更快。', q=Q_MIN, ref=A(DC, 38, 39)),
                  note('文档', 'direct 没有配置开关，能走就走。', q=Q_KNOB, ref=A(CPDOC, 232)),
                  note('推断', '例子落在哪条取决于构建：CUDA ≥ 12.8 走 direct，否则走 native plan / staged。'),
                  note('推断', 'staged 为什么要经过 GPU 暂存缓冲，仓库没写；猜测是大块连续 DMA 吃满带宽，内核只做 GPU 内部的分散 / 收集。')],
                 ['取回的 `skip_first_n_tokens` 会换成 block **向下取整**：B′ 跳 64 token = 4 个 block。' + R(STG, 384, 388),
                  'staged 路径里，被跳过的 block 所在的 chunk 仍整块搬进暂存；direct 按 block 跳，不多搬。']),
             to='xfer.copy'),

        # ------------------------------------------------ 服务端 CUDA 流
        part('stream', '服务端 CUDA 流', [710, 250, 240, 250], m=[72, 964, 200, 260], kind='act', n='3',
             code='cache_context.stream', glyph=G_STREAM0,
             say='GPU 按这个顺序真正执行', why='同一条流，天然排好先后', trap='完成 event 在提交之前',
             card=card(
                 '服务端为每个注册的 vLLM 实例开的一条 CUDA 流：GPU 上真正按顺序执行「等待 → 拷贝 → 录 event → 回调」的队列。'
                 '亲和线程往队尾放，GPU 从队头取。',
                 ['流挂在每实例一份的 `cache_context` 上，同挂着的还有 4 槽 GPU 暂存缓冲（例子里 4 × 36 MiB = 144 MiB）'
                  '和 block id 缓冲。' + R(CCC, 443, 453),
                  '「等 event」是流级等待：只在流里插一个依赖，CPU 立刻往下走。' + R(EIPC, 250, 252),
                  'STORE 在流上的顺序：等 ev_A → 放导入 event 的回调 → 3 次拷贝 → ★ 录 ev_done → `finish_write` 回调。'],
                 [note('测试', '测试把入队顺序钉死：回调、拷贝、拷贝、录 event、回调。那个测试是 2 个 chunk 的失败 STORE，最后一个回调是 abort_write；成功时同一位置是 finish_write。', q=Q_ORDER,
                       ref=A(TOWN, 193)),
                  note('注释', '流要先等 vLLM 的 event：要读的 KV 是 vLLM 流上的前向写的。', q=Q_EVENT, ref=A(WT, 702)),
                  note('推断', '不加这个依赖，拷贝可能读到前向还没写完的 KV；拷贝、event、回调都在同一条流上，彼此不用再同步。'),
                  note('推断', '完成 event 录在拷贝之后、提交回调之前：worker 只关心 block 能不能回收 / 能不能用，那只取决于拷贝。')],
                 ['worker 看到「完成」时，对象还没转正：提交回调排在 ev_done 后面，还要等排空线程一轮。',
                  note('推断', '同一实例的 STORE 和 RETRIEVE 进同一条流：后面的拷贝要等前面的拷贝物理完成才开始，即使 block 毫无交集。')]),
             to=['xfer.store', 'xfer.cb']),

        # ------------------------------------------------ 排空线程
        part('drain', '排空线程', [1060, 250, 150, 170], m=[72, 1290, 200, 150], kind='bg', n='4',
             glyph=G_DRAIN0,
             say='拷完后做收尾', why='驱动线程不拿 GIL', trap='最多晚 5 ms',
             card=card(
                 '`DeviceHostFuncDispatcher`：一条后台线程，每 5 ms 去 C++ 缓冲里取「流走到这里了」的消息，按 kind 解码、'
                 '调对应的 Python 处理函数。转正、放锁、放导入 event 都是它做的。',
                 ['亲和线程挂回调只做两件事：msgpack 编码载荷，交给 C++ 在流上挂一个 host 函数。' + R(NC, 147, 153),
                  '流走到那个位置时，CUDA 驱动线程执行这个 C++ 函数：只把消息放进带互斥锁的缓冲，不碰 Python（C++ 源码见下一层）。',
                  '5 种 kind：`finish_write` / `abort_write` / `finish_read_prefetched` / `finish_read_by_owner` / '
                  '`release_imported_event`。' + R(LDT, 272, 296),
                  '轮询间隔 5 ms，写死。' + R(NC, 67)],
                 [note('注释', '回调绕道 C++，让驱动线程不用拿 GIL。', q=Q_GIL, ref=A(LDT, 269, 270)),
                  note('注释', '这是把一个「GIL × 驱动锁」的 AB-BA 死锁修复推广到 store / retrieve。', q=Q_ABAB, ref=A(NC, 27)),
                  note('提交', '修 store / retrieve 死锁的提交引入了这套机制。',
                       q='Fix store/retrieve deadlock by routing completion callbacks through C++ host callback',
                       ref='c:50e45434'),
                  note('推断', '死锁的样子：Python 线程拿着 GIL 去调要驱动锁的 CUDA，驱动线程拿着驱动锁在回调里等 GIL。')],
                 ['处理函数抛异常：计数、记日志，**不重试**。' + R(NC, 133, 144),
                  note('推断', '`finish_write` 失败的对象留在 staging，直到写锁超时（默认 600 秒）。')]),
             to='xfer.cb'),

        # ------------------------------------------------ 框外：L1
        part('l1', 'L1 存储', code='StorageManager', d= [710, 20, 240, 150], m=[72, 1506, 200, 150], kind='store', glyph=G_L10,
             say='这里只用它几个接口', why='先暂存，转正才可见', trap='unsafe 只是不加锁',
             card=card(
                 '框外的存储层（顶层「L1 存储」框）。传输模块只调用它的几个接口。',
                 ['STORE：`reserve_write` 拿到 staging 对象 → 拷完 `finish_write` 转正（key 已存在则保留旧的、丢掉新的）；'
                  '失败则 `abort_write` 丢掉预留。' + R(SM, 353, 361) + R(SM, 393, 413),
                  'RETRIEVE：`unsafe_read` 读 LOOKUP 已锁住的对象 → 拷完 `finish_read_by_owner` 放锁。' + R(SM, 710, 720)],
                 [note('注释', '失败时在同一条流上、等已入队的部分拷贝跑完再放弃预留，不拖到写锁超时。', q=Q_ABORT, ref=A(LDT, 1015)),
                  note('提交', '以前失败的 STORE 把预留留到写锁超时，挡住后来所有写同一 key 的人。',
                       q='left its staging reservations to expire with the write TTL', ref='c:831e0502')],
                 ['`unsafe_read` 的 unsafe 是「不新加锁」：锁是 LOOKUP 时上的。']),
             ),
    ]

    wires = [
        wire('w_in', 'worker', 'pool', 'STORE + ev_A', ml='存+ev_A', n='1', why='句柄随消息走',
             card=card(
                 'worker 把 STORE 工单变成一条 ZMQ 异步 RPC：key `[0, 768)`、instance_id、48 个 block id、ev_A 的句柄。'
                 + R(WT, 719, 722),
                 ['ZMQ 主循环收包，算亲和 key，交给亲和池。' + R(MQ, 298, 300),
                  '服务端用 `Event.from_ipc_handle` 把句柄导入成自己进程里的 event。' + R(EIPC, 240, 244)],
                 [note('推断', '只传句柄，不传数据：KV 留在 vLLM 显存里，由服务端自己伸手拷。')])),
        wire('w_pick', 'pool', 'aff', '派给线程 0', ml='线程 0', why='同一实例 FIFO',
             card=card(
                 '把这次 STORE 放进线程 0 的队列。',
                 '线程逐个取任务，跑完一个才取下一个。' + R(AFF, 107, 125),
                 [note('注释', '同一条线程内严格 FIFO。', q=Q_FIFO, ref=A(AFF, 6))],
                 '「跑完」= 全部入队完，不是拷完。')),
        wire('w_enq', 'aff', 'stream', '等·录·挂回调', ml='等录挂', why='只排队', trap='留导入',
             mfs='l', mts='l', mlp=[36, 820],
             card=card(
                 '亲和线程往流里放「非拷贝」的三样：等 ev_A、录完成 event、挂回调。',
                 ['导入 ev_A → 让服务端流等它 → 持有导入对象 → 紧跟着挂 `release_imported_event` 回调。' + R(LDT, 891, 900),
                  '`finally` 里录 ev_done，再挂 `finish_write` 或 `abort_write`。' + R(LDT, 1002, 1018)],
                 [note('注释', '导入的 event 要活到流真正消费完那次等待，所以先持有，等流走过去再由回调放掉。', q=Q_HOLD,
                       ref=A(BCC, 84, 86)),
                  note('提交', '修复：持有导入的 IPC event。', q='Hold imported IPC events', ref='c:4d96107c')],
                 '这三样都只是排队，CPU 一样都不等。'),
             to='xfer.store'),
        wire('w_res', 'aff', 'l1', 'reserve_write', ml='预留', why='先记账', trap='缺≠败',
             mfs='r', mts='r', mlp=[306, 1100],
             card=card(
                 '发拷贝之前，向 L1 要 staging 对象：A 要 `[h0, h1, h2]`，各 36 MiB，读者看不见。',
                 ['拿到后立刻记下每个对象的 owner。' + R(LDT, 960, 964),
                  '没要到的 key 变成 `None`，后面拷贝跳过它。' + R(LDT, 970, 975)],
                 [note('注释', 'owner 在任何拷贝之前就记下，失败时才能按 owner 精确放弃。', q=Q_OWNER, ref=A(LDT, 939))],
                 '某个 key 已在 L1 或 L1 没空间：那块不存，其余照存，不算失败。')),
        wire('w_copy', 'aff', 'copy', '每组发一次', ml='发拷贝', n='2', why='STORE 每批 1 块',
             card=card(
                 '对每个 object group 调一次 `transfer_object_group`。A 只有 1 个 group：3 个对象、48 个 block id。',
                 ['STORE：`batch_size=1`、`skip=0`、方向 D2H。' + R(LDT, 977, 987),
                  'RETRIEVE：`batch_size=4`（暂存缓冲 4 槽）、带 skip、方向 H2D。' + R(LDT, 1341, 1350)],
                 [note('推断', 'STORE 为什么必须每批 1 块，注释没说；猜测：staged 循环里批内有一个 `None` 就跳过整批，'
                               '而 STORE 的 `None` 是常态（key 已存在），批大了会连累好的 chunk。')],
                 '传进去的 `block_ids_host` 已经被裁剪函数就地改写过。' + R(PLN, 125))),
        wire('w_launch', 'copy', 'stream', 'memcpy / 内核进流', ml='进流', why='同流，自动排在等待之后',
             card=card(
                 '选好的路径把 memcpy 或内核发到当前流——服务端流——上。',
                 ['direct：每个 chunk 一次批量 memcpy，宿主 ↔ 分页 block。' + R(DC, 258, 264),
                  'staged：暂存拷贝 + 内核；内核的 grid 是 (kv, 所有 block, 层)：STORE 每次 1 块 = (2, 16, 36)，'
                  'B 取回一批 3 块 = (2, 48, 36)。'],
                 [note('推断', '发在同一条流上，所以自动排在「等 ev_A」之后、「录 ev_done」之前，不需要额外同步。')]),
             to='xfer.copy'),
        wire('w_host', 'stream', 'drain', 'host 函数', ml='host 函数', n='3', why='驱动线程只 push', cls='dash',
             card=card(
                 '流走到回调位置时，CUDA 驱动线程执行一个 C++ host 函数，把（kind，载荷）放进 C++ 缓冲。',
                 ['CUDA 下用 `cudaLaunchHostFunc`，HIP 下用 `hipLaunchHostFunc` 挂上去。' + R(NC, 147, 153),
                  '载荷是 msgpack 编码的 bytes，排空线程按 kind 登记的类型解码。' + R(NC, 80, 87)],
                 [note('注释', '这一段的目的就是：驱动线程上不碰 GIL。', q=Q_GIL, ref=A(LDT, 269, 270))]),
             to='xfer.cb'),
        wire('w_fw', 'drain', 'l1', 'finish_write', ml='转正', n='4', why='拷完才可见', trap='失败走 abort_write',
             card=card(
                 '排空线程取到 `finish_write` → `_finish_write` → `StorageManager.finish_write`：3 个 staging 对象转正，读者可见。'
                 + R(LDT, 331, 339),
                 ['key 已存在时保留旧的、丢掉新的。' + R(SM, 353, 361),
                  'RETRIEVE 时这条线上走的是 `finish_read_by_owner`：放读锁。'],
                 [note('推断', '必须等 GPU 真拷完才让对象可见，否则别人可能读到半截数据。')],
                 '失败时这条线上走的是 `abort_write`：丢掉预留。'),
             to='xfer.cb'),
        wire('w_ret', 'aff', 'worker', '回句柄（未拷完）', ml='回句柄', why='早于拷完', trap='未存好',
             cls='dash', mfs='l', mts='l', mlp=[36, 300],
             card=card(
                 '入队完，`store()` 返回 `(ev_done 句柄, True, [✓, ✓, ✓])`；ZMQ 主线程编码发回。此刻 GPU 多半还在拷。'
                 + R(LDT, 1080, 1084),
                 ['返回值进输出队列，eventfd 唤醒主循环发出。' + R(MQ, 313, 314),
                  'worker 导入 ev_done，之后每步 `query()`。' + R(FUT, 415, 433)],
                 [note('注释', 'worker 的 future 同时等 RPC 回复和这个 event。', q=Q_PEND, ref=A(FUT, 263, 264))],
                 ['守卫失败时也回一个真 event（录在没有设备工作的位置）；只有没注册时才回空句柄。' + R(LDT, 867, 871),
                  'ev_done 完成 ≠ 对象可见：提交回调还排在它后面。'])),
    ]

    steps = [
        beat('A 的 STORE 进门，派给线程 0',
             '顶层那一步「A：前向结束，发 STORE」到了这里：worker 在 A 的前向后面录了 **ev_A**，把 `[0, 768)`、48 个 block id '
             '（b0–b47）和 ev_A 的句柄发过来。ZMQ 主线程算亲和 key，第一次见到 worker 0 → 占槽位 0。',
             note('注释', '同一实例的操作都在一条线程上，共享的 GPU 缓冲不用加锁。', q=Q_AFF, ref=A(AFF, 8, 10)),
             focus=['pool', 'w_in', 'worker', 'w_pick', 'aff'],
             state={'pool': g_kv([('线程数', '1（默认）'), ('worker 0', '→ 线程 0')])}),
        beat('守卫：48 个 id 够不够',
             '线程 0 进入服务端流的上下文，先建好自己的完成 event **ev_done**。A 有 1 个 group、3 个 chunk，要 3 × 16 = **48** '
             '个 id，给了 48 个，过。再算全空块掩码（没有），把 48 个 id 拷进 GPU 上的 block id 缓冲。',
             note('注释', '少给了就整单放弃，否则内核会越界读显存。', q=Q_SHORT, ref=A(LDT, 847, 848)),
             focus=['aff'],
             state={'aff': affA(1)}),
        beat('流先等 ev_A（只是排队）',
             '导入 ev_A，让**服务端流**等它；持有这个导入对象，紧跟着挂一个回调，等流走过去再把它放掉。流上现在排着 2 件事，'
             'GPU 上 A 的前向可能还在算——没关系，CPU 已经往下走了。',
             note('注释', '导入的 event 要活到流消费完这次等待。', q=Q_HOLD, ref=A(BCC, 84, 86)),
             focus=['w_enq', 'aff', 'stream'],
             state={'aff': affA(2), 'stream': stA(2)}),
        beat('向 L1 预留 3 个 staging 对象',
             '`reserve_write([h0, h1, h2])`：L1 里还没有它们，3 个都要到了，各 **36 MiB**，共 108 MiB，读者看不见。'
             '立刻记下 3 个 owner——这时一字节都还没拷。',
             note('注释', 'owner 在任何拷贝之前记下，失败时才能按 owner 放弃。', q=Q_OWNER, ref=A(LDT, 939)),
             focus=['w_res', 'aff', 'l1'],
             state={'aff': affA(3), 'l1': g_list(['h0 staging（看不见）', 'h1 staging', 'h2 staging'])}),
        beat('选路径，发 3 次拷贝',
             '每层每块 16 × 1024 × 2 B = **32 KiB**，正好不小于 direct 的门槛 → 构建支持就走 direct：每个 chunk 一次批量 memcpy。'
             '`batch_size=1`，3 个 chunk 发 3 次，都进服务端流，排在「等 ev_A」后面。b0–b15 → h0 的 token 0–255，依此类推。',
             note('注释', '块再小，逐条 memcpy 的开销占上风，内核路径更快。', q=Q_MIN, ref=A(DC, 38, 39)),
             focus=['copy', 'w_copy', 'w_launch', 'stream'],
             state={'aff': affA(4), 'stream': stA(5),
                    'copy': g_checks([('每层块 32 KiB ≥ 32 KiB', 'ok'), ('批量 memcpy 可用', 'ok'), ('→ direct', 'ok')])}),
        beat('finally：录 ev_done，挂 finish_write',
             '3 次拷贝都入队成功，`store_succeeded = True`。`finally` 里先在流上录 **ev_done**——它在所有拷贝**之后**；'
             '再挂 `finish_write` 回调——它在 ev_done **之后**。流上一共 7 件事，GPU 一件都可能还没做完。',
             note('测试', '测试钉死的入队顺序：回调、拷贝…、录 event、回调。', q=Q_ORDER, ref=A(TOWN, 193)),
             focus=['stream', 'w_enq', 'aff'],
             state={'aff': affA(5), 'stream': stA(7)}),
        beat('函数返回：GPU 多半还在拷',
             '`store()` 返回 `(ev_done 句柄, True, [✓, ✓, ✓])`，主线程发回 worker。线程 0 空了，可以接下一单。'
             'worker 拿到回复，但 `query()` 还要看 ev_done——这时还是 False。',
             note('推断', '回复只说明「已入队」：从这里到收尾，没有任何一行 Python 在等这次拷贝。'),
             focus=['w_ret', 'worker', 'aff'],
             state={'aff': g_text('空闲', '可以接下一单', tone='ok'),
                    'worker': g_kv([('回复', '已到 ✓'), ('ev_done', '未完成'), ('STORE', '还没完')])}),
        beat('GPU 按顺序执行到 ev_done',
             'GPU 走流：ev_A 完成（A 的前向算完了）→ 放导入 ev_A 的回调进 C++ 缓冲 → 拷 h0、h1、h2（3 × 36 MiB）→ ev_done 完成。'
             '下一次 `get_finished`，worker 的 `query()` 变 True，报 A 存完。**但 L1 里对象还是 staging。**',
             note('推断', '完成 event 在提交回调之前：worker 只需要知道「block 可以回收了」，那只取决于拷贝。'),
             focus=['stream', 'w_host', 'drain', 'worker'],
             state={'stream': stA(7, 6),
                    'drain': g_list(['放导入 ev_A', 'finish_write ×3'], on=1),
                    'worker': g_kv([('回复', '已到 ✓'), ('ev_done', '完成 ✓'), ('STORE', '报完成')])}),
        beat('排空线程：≤ 5 ms 后转正',
             '流走到最后一件事，驱动线程把 `finish_write` 放进 C++ 缓冲；排空线程下一轮（≤ 5 ms）取走、解码，调 '
             '`StorageManager.finish_write`：h0、h1、h2 转正，从此 LOOKUP 能命中。这就是顶层那一步「A：拷完，转正」。',
             note('注释', '回调绕道 C++：驱动线程不用拿 GIL。', q=Q_GIL, ref=A(LDT, 269, 270)),
             focus=['drain', 'w_fw', 'l1'],
             state={'stream': stA(7, 7), 'drain': g_list(['C++ 缓冲：空', '已处理 2 条']),
                    'l1': g_list(['h0 可读', 'h1 可读', 'h2 可读'])}),
        beat('B 的 RETRIEVE：同一线程、同一条流',
             'B 的 RETRIEVE `[0, 768)`、skip 0，还是 worker 0 → 线程 0。同样建 ev_done、守卫 48 个 id、**先**让流等 ev_B；'
             '然后**在入队任何拷贝之前**定好「拷完要放的 3 把锁」；`unsafe_read` 3 个都在（不新加锁）；`batch_size=4` → 一批发完 3 块。',
             note('注释', '放锁清单在入队 GPU 工作之前就定下，中途失败也不会漏放。', q=Q_RETAIN, ref=A(LDT, 1314)),
             focus=['aff', 'w_enq', 'stream', 'w_copy', 'copy'],
             state={'aff': g_checks([('48 个 id ≥ 48', 'ok'), ('流等 ev_B', 'ok'), ('先定放锁清单', 'ok'),
                                     ('unsafe_read 3/3', 'ok'), ('录 ev·挂放锁', 'ok')]),
                    'stream': stB(5), 'drain': G_DRAIN0,
                    'copy': g_kv([('方向', 'H2D 宿主→显存'), ('一批', 'h0 h1 h2'), ('skip', '0')]),
                    'l1': g_list(['h0 读锁 1', 'h1 读锁 1', 'h2 读锁 1'], on=[0, 1, 2]),
                    'worker': g_kv([('发出', 'RETRIEVE [0, 768)'), ('block', 'B 的前 48 个'), ('ev_B', 'IPC 句柄')])}),
        beat('拷完，排空线程放锁',
             'GPU 等到 ev_B → 一批写进 B 的 48 个 block → ev_done 完成，worker 报 B 取完，vLLM 可以开始算 B。'
             '放锁回调随后由排空线程执行：3 把读锁归零。成败都会放，因为清单早就定好了。',
             note('推断', '锁晚一点放无碍：读锁有 300 秒超时兜底，而 vLLM 用这批 block 只看 ev_done。'),
             focus=['stream', 'w_host', 'drain', 'w_fw', 'l1'],
             state={'stream': stB(5, 5), 'drain': g_list(['放导入 ev_B', '放读锁 ×3'], on=1),
                    'l1': g_list(['h0 可读（无锁）', 'h1 可读（无锁）', 'h2 可读（无锁）'])}),
        beat('换个 B′：vLLM 自己已有 320 token',
             '设 vLLM 本地命中前 320 token（20 个 block）。调度器算：起点 320 向下取整 = 256，skip = 320 − 256 = **64**。'
             '服务端只处理 h1、h2（32 个 block id）。64 token → **4 个 block**：内核里前 4 个线程块直接 return，'
             'h1 的 token 256–319 不写。staged 路径下 h1 仍整块进暂存。',
             note('注释', '不能重写 vLLM 自己已有的 block：别的请求可能正在 vLLM 的流上读它们。', q=Q_APC, ref=A(MD, 640, 643)),
             focus=['copy', 'w_copy', 'aff'],
             state={'copy': g_kv([('h1 前 4 块', '跳过'), ('h1 后 12 块', '写'), ('h2 全部 16 块', '写')])}),
    ]

    return node(
        'xfer', '传输模块里面', '传输模块：亲和线程下单，CUDA 流搬运，排空线程收尾',
        '顶层「传输模块」框里面：一次 STORE / RETRIEVE 被三个执行体接力完成，函数返回时数据还没动完。',
        lead='你在顶层「传输模块」框里面。这个框里有三个执行体接力：**亲和线程**只排队下单，**服务端 CUDA 流**在 GPU 上按顺序'
             '真正搬数据，**排空线程**在拷完之后收尾（转正、放锁）。按 ▶ 接着顶层的例子走：请求 A 存 `[0, 768)`，请求 B 取回。',
        src=A(LDT, 837, 1018),
        blocks=[scene(1220, 520, parts, wires, steps, mw=340, mh=1680,
                      cap='STORE / RETRIEVE 在服务端里面 · 例子沿用顶层：A 存 3 块（48 个 block → h0 h1 h2），B 取回')],
        children=[_store_src(), _retrieve_src(), _copy_src(), _cb_src(), _pool_src()])


# ---------------------------------------------------------------- 源码页
def _store_src():
    return node(
        'xfer.store', 'STORE 源码', 'store()：进流、守卫、等 ev_A、预留、发拷贝、录 ev_done、提交或放弃',
        '亲和线程上跑的 `store()` 主体：图上「亲和线程」框和它往流里放的每一样东西，在这里逐行对上。',
        lead='你在「传输模块」图的「亲和线程」框里面，看 STORE 的源码。两段：前半是进流 → 守卫 → 等 ev_A；后半是预留 → 发拷贝 → '
             '录 ev_done → 提交或放弃。例子：A 的 `[0, 768)`、48 个 block、h0 h1 h2。',
        src=A(LDT, 840, 1084),
        blocks=[
            code(LDT, [(840, 858), (867, 900)], notes=[
                at((840, 844), '推断', '进入服务端流的上下文：之后发的拷贝、event、回调默认都进这条流。完成 event 在最前面就建好——'
                                     '为什么这么早仓库没写，推测是让后面每个出口都有 event 可导出。',
                   has='torch_dev.stream(cache_context.stream)'),
                at((846, 858), '注释', '下溢守卫：A 有 1 个 group、3 个 chunk，要 3 × 16 = 48 个 id，给了 48 个，过。少了就整单放弃，'
                                     '免得内核越界读显存。查的是裁剪前的原始 id。',
                   q=Q_SHORT, has='num_chunks * bpc'),
                at((867, 872), None, '守卫失败也录一个真 event 再导出（录在没有任何设备工作的位置），worker 立刻看到完成。'
                                     'docstring 说「没有设备工作时句柄为空」，这里并不一致，原因没查到。', has='record_event'),
                at((874, 885), '注释', '全空块掩码：必须在下面那步改写 id 之前，用原始 id 算。',
                   q='Computed on the raw block ids before downsampling mutates them.', has='all_null_chunk_masks'),
                at((887, 889), None, '裁剪 block id 并拷进 GPU 上每实例一份的 block id 缓冲。它会**就地改写**传入的列表。',
                   has='downsample_and_stage_block_ids'),
                at((891, 894), None, '★ 导入 vLLM 的 ev_A，让服务端流等它。只是在流里插一个依赖，CPU 立刻往下走。', has='wait_event'),
                at((895, 900), '注释', '持有导入对象，并紧跟着挂一个回调，等流走过这次等待再放掉它。', q=Q_HOLD, ref=A(BCC, 84, 86),
                   has='hold_imported_event'),
            ], cap='STORE 前半：进流 → 守卫 → 等 ev_A（中间省略了一段日志）'),
            code(LDT, [(937, 987), (998, 1018), (1080, 1084)], notes=[
                at((937, 942), '注释', '`l1_owners` 在任何拷贝之前就记下；`store_succeeded` 默认 False，只有走到 998 才变真。',
                   q=Q_OWNER, has='store_succeeded = False'),
                at((960, 964), None, '向 L1 要 staging 对象：A 是 `[h0, h1, h2]`，各 36 MiB，读者看不见；随后立刻记 owner。',
                   has='reserve_write'),
                at((970, 975), None, '没要到的 key（已在 L1、或 L1 没空间）在这里变成 `None`，拷贝时跳过——**不算失败**，其余照存。',
                   has='reserved_dict.get(obj_key)'),
                at((977, 987), '推断', '`batch_size=1`、`skip=0`、方向 D2H。注释只说「必须是 1」没说原因；推测：staged 循环里批内有一个 '
                                     '`None` 就整批跳过，而 STORE 里 `None` 是常态，批大了会连累好的 chunk。',
                   has='batch_size must stay 1'),
                at((998, 1000), None, '能走到 998 就是全部入队成功。异常只记日志、不重抛：失败靠 `store_succeeded` 传下去。',
                   has='store_succeeded = True'),
                at(1002, None, '★ `finally` 里第一件事：在所有拷贝之后录完成 event（图上的 ev_done）。worker 等的就是它。',
                   has='record_event'),
                at((1003, 1011), '注释', '全成功才挂 `finish_write`：A 的 3 个对象由排空线程一起转正。', q=Q_FAILCLOSED,
                   has='finish_write'),
                at((1012, 1018), '注释', '失败：在同一条流上、等已入队的部分拷贝跑完再挂 `abort_write` 丢掉预留，不拖到写锁超时。',
                   q=Q_ABORT, has='abort_write'),
                at((1080, 1084), None, '返回 `(ev_done 句柄, True, [✓, ✓, ✓])`，此刻 GPU 多半还在拷。bool 只说明没有致命错误；'
                                       '哪些 chunk 真存了看第三项。', has='stored_mask'),
            ], cap='STORE 后半：预留 → 发拷贝 → 录 ev_done → 提交或放弃 → 返回'),
        ])


def _retrieve_src():
    return node(
        'xfer.retrieve', 'RETRIEVE 源码', 'retrieve()：先等 ev_B，先定放锁清单，再读、再拷，成败都放锁',
        '`retrieve()` 的核心几段，和 STORE 对照着看：批大小、skip、放锁回调为什么不分成败。',
        lead='你在「传输模块」图的「亲和线程」框里面，看 RETRIEVE 的源码。和 STORE 对照：每批 4 块、带 skip、拷完放锁且不分成败。'
             '例子：B 取回 `[0, 768)`；B′ 的 skip 是 64。',
        src=A(LDT, 1265, 1376),
        blocks=[
            code(LDT, [(1265, 1278), (1306, 1351), (1366, 1376)], notes=[
                at((1269, 1278), None, '和 STORE 同一段：导入 ev_B、流等它、持有导入对象并挂释放回调。**等 event 在读对象之前**。',
                   has='wait_event'),
                at((1307, 1313), None, '要放哪些锁：全注意力下就是 B 的 `[h0, h1, h2]`。', has='retained_keys'),
                at((1314, 1318), '注释', '放锁清单在入队任何 GPU 工作**之前**定下，中途失败也不会漏放。', q=Q_RETAIN,
                   has='prepare_read_completion'),
                at((1330, 1336), None, '`unsafe_read`：读 LOOKUP 已锁住的对象，不新加锁。缺任何一个 key 就 break、标失败，不拷半截。',
                   has='unsafe_read'),
                at((1339, 1340), '注释', '滑窗模型里被跳过的前缀对象用 `None` 占位，block id 的切片不用改。全注意力下这里不跳。',
                   q='None-pad the skipped prefix so block-id slicing is unchanged.', has='None-pad'),
                at((1341, 1350), None, '`batch_size=4`（暂存缓冲 4 槽）、带 `skip_first_n_tokens`、方向 H2D——对照 STORE 的 1 / 0 / D2H。'
                                       'B 的 3 块一批发完；B′ 这里是 64。', has='max_batch_size'),
                at((1366, 1368), None, '异常只标失败，不重抛。', has='retrieve_succeeded = False'),
                at((1370, 1376), None, '★ 先录 ev_done，再挂 `finish_read_by_owner`。条件只看 `retained_keys`：**成败都放锁**。',
                   has='finish_read_by_owner'),
            ], cap='RETRIEVE：等 ev_B → 定放锁清单 → 读 → 拷 → 录 ev_done → 放锁'),
        ])


def _copy_src():
    return node(
        'xfer.copy', '拷贝路径源码', '三选一的路径、32 KiB 门槛、staged 循环里的 skip、内核里的平铺',
        '图上「拷贝」框里面：怎么选路径，48 个 block 怎么平铺成 3 个对象，skip 在哪里生效。',
        lead='你在「传输模块」图的「拷贝」框里面。按调用顺序：挑路径 → 门槛 → staged 循环（skip 换算）→ 内核（平铺 + 跳过前几块）。',
        src=A(DSP, 69, 111),
        blocks=[
            code(DSP, [(69, 111)], notes=[
                at((72, 87), None, 'direct：宿主内存对象 + 批量 memcpy 可用 + 格式 / 几何合格（含每层块 ≥ 32 KiB）。', has='run_direct_transfer'),
                at((88, 99), None, 'native plan：暂存拷贝 + 内核，整批一次 native 调用。', has='run_object_group_transfer_plan'),
                at((100, 111), None, 'staged loop：兜底（含 GDS 等非宿主内存对象），逐批在 Python 里发射。', has='run_staged_loop'),
            ], cap='dispatch：每段同类对象挑一条路径'),
            code(DC, [(38, 42), (110, 113)], notes=[
                at((38, 42), '注释', '门槛 32 KiB：块再小，逐条 memcpy 的开销占上风，内核路径更快（附 H200 实测）。', q=Q_MIN,
                   has='_DIRECT_COPY_MIN_BLOCK_BYTES'),
                at((110, 113), None, '每层块字节 = bs × nh × hs × 元素大小 = 16 × 8 × 128 × 2 = 32,768。比较用 `<`，32 KiB 不被拒。',
                   has='block_bytes <'),
            ], cap='direct 的块大小门槛'),
            code(STG, [(367, 413)], notes=[
                at((370, 378), None, '批里有 `None`：取回直接报错；存则跳过整批。推测这就是 STORE 每批只放 1 块的原因（注释没写）。', has='mo is None'),
                at((384, 388), None, 'B′：批起点 token 0、skip 64 → effective_start = 64，块内跳过 64 个 token。', has='effective_start'),
                at((390, 397), '注释', '整块都被跳过的 chunk 连暂存都不做。B′ 的 64 < 256，first = 0，h1 照样整块进暂存。',
                   q='On retrieve the kernel skips the chunks before the first one it', has='first ='),
                at((399, 413), None, '方向：取回先 stage（宿主→暂存）再内核（暂存→分页）；存先内核再 stage。', has='_launch_batch_kernels'),
            ], cap='staged 循环：一批里怎么跳、先拷哪边'),
            code(KLG, [(663, 669)], notes=[
                at((666, 669), None, '64 个 token → 64 × slots/tpb // bs = 4 个 block，**向下取整**。', has='num_physical_slots'),
            ], cap='token 数换成 block 数'),
            code(KERN, [(317, 333)], notes=[
                at((320, 323), None, '★ 线程块的 y 下标 < 跳过块数就直接 return。B′：跳 4 个，h1 的 token 256–319（vLLM 自己已有）不写。',
                   has='skip_prefix_n_blocks'),
                at((324, 332), None, '平铺的全部：第 flat 个 block → 第 flat / 16 个对象、对象内 token 偏移 (flat % 16) × 16。'
                                     'b0–b15 → h0 的 token 0–255；block 编号不必连续。', has='block_idx_in_object * shape_desc.bs'),
            ], cap='CUDA 内核：一个线程块搬一个分页 block'),
            code(TSTG, [(112, 116)], notes=[
                at((113, 114), '测试', '测试：skip 盖住第 0 块和第 1 块的一个 block → 第 0 块不进暂存，内核跳过 5 个 block。', q=Q_SKIPTEST,
                   has='kernel skips five blocks'),
            ], cap='测试把 skip 的两种效果钉住'),
        ])


def _cb_src():
    return node(
        'xfer.cb', '流回调链源码', '回调怎么从 GPU 流走到 Python：C++ host 函数 → 缓冲 → 排空线程',
        '图上「排空线程」框和「host 函数」那条线：登记五种回调、驱动线程上只 push、排空线程每 5 ms 取走。',
        lead='你在「传输模块」图的「排空线程」框里面。三段：服务端登记五种回调；驱动线程上的 C++ 只做一次 push；排空线程每 5 ms 取走、解码、调处理函数。',
        src=A(NC, 114, 153),
        blocks=[
            code(LDT, [(269, 297)], notes=[
                at((269, 271), '注释', '回调绕道 C++，让 CUDA 驱动线程不用拿 GIL。', q=Q_GIL, has='C++ host'),
                at((272, 296), None, '五种 kind 各配一个处理函数 + 载荷类型。STORE 用 `finish_write` / `abort_write`，RETRIEVE 用 '
                                     '`finish_read_by_owner`，两者都用 `release_imported_event`。', has='payload_type'),
            ], cap='登记：kind → 处理函数'),
            code(CREC, [(24, 49)], notes=[
                at((28, 33), None, '驱动线程上跑的就是这个：把消息 push 进带互斥锁的缓冲，不碰 Python、不拿 GIL。', has='push'),
                at((35, 49), None, '在流上挂 host 函数（CUDA 下是 `cudaLaunchHostFunc`）；挂失败就把消息收回。',
                   has='LMCACHE_COMPLETION_LAUNCH_HOST_FUNC'),
            ], cap='C++：流走到这里时，驱动线程只做一次 push'),
            code(NC, [(62, 73), (114, 153)], notes=[
                at(67, None, '轮询间隔 5 ms，构造时没传参数，是固定值。为什么是 5 ms 没查到。', has='0.005'),
                at((115, 120), '注释', '取消息出错只记日志返回：这条线程不能死。',
                   q='Broad except keeps the drain thread alive across native/handler errors.', has='drain_recorded_completions'),
                at((125, 132), None, '没登记的 kind：警告并丢弃。', has='no handler'),
                at((133, 144), None, '解码 + 调处理函数（例如 `_finish_write`）；异常只计数、记日志，**不重试**，一条出错不影响后面的。',
                   has='reg.handler(decoded)'),
                at((147, 153), None, '亲和线程碰到的只有这两行：编码载荷，交给 C++ 在流上挂 host 函数。', has='record_completion_on_stream'),
            ], cap='排空线程：每 5 ms 取走、解码、调用'),
        ])


def _pool_src():
    return node(
        'xfer.pool', '亲和池源码', '按客户端身份把 STORE / RETRIEVE 绑到固定线程',
        '图上「亲和池」框里面：亲和 key 怎么来、怎么占槽位、线程怎么逐个执行。',
        lead='你在「传输模块」图的「亲和池」框里面。两段：ZMQ 主循环算亲和 key；线程池按 key 占槽位、每条线程 FIFO 执行。',
        src=A(AFF, 107, 167),
        blocks=[
            code(MQ, [(298, 314)], notes=[
                at(299, None, '亲和 key = 客户端身份的哈希：同一个 worker 进程永远同一个 key。', has='hash(identity)'),
                at((313, 314), None, '线程跑完后，回复交回输出队列 + eventfd，由主循环线程发出。', has='output_queue'),
            ], cap='ZMQ 主循环：算亲和 key，回复交回主循环'),
            code(AFF, [(107, 125), (131, 145), (156, 167)], notes=[
                at((110, 117), '注释', '每条线程一个队列，逐个取任务执行：同一个 key 的任务 FIFO 串行。', q=Q_FIFO, ref=A(AFF, 6),
                   has='q.get()'),
                at((118, 122), None, '任务返回 `DeferredResponse`（ROCm 主机等待路径用）时线程立刻空出来；主路不走这里。',
                   has='DeferredResponse'),
                at((137, 145), None, '第一次见到某个 key：占 `_next_slot % 线程数` 这个槽，之后永远是它。默认 1 条线程，所以 worker 0 在线程 0。',
                   has='_next_slot % self._num_workers'),
                at((156, 166), None, '槽位用完后再来的 key 只能和前面的共享线程，只警告一次。', has='is_overflow'),
            ], cap='AffinityThreadPool：执行与绑定'),
        ])
