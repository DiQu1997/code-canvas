from cv import *

CON = 'lmcache/integration/vllm/lmcache_mp_connector.py'
ADP = 'lmcache/integration/vllm/vllm_multi_process_adapter.py'
MD = 'lmcache/integration/vllm/lmcache_mp_metadata.py'
MLS = 'lmcache/integration/vllm/mp_lookup_state.py'
LK = 'lmcache/v1/multiprocess/modules/lookup.py'
FUT = 'lmcache/v1/transport/futures.py'
DCFG = 'lmcache/v1/distributed/config.py'
GV = 'lmcache/v1/common/group_view.py'
T_BOUND = 'tests/v1/test_mp_tracker_store_bound.py'
T_APC = 'tests/v1/multiprocess/test_apc_covered_lookup.py'
T_ROLES = 'tests/v1/test_mp_connector_kv_roles.py'
T_ADP = 'tests/v1/test_vllm_mp_adapter.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 引文（逐字照抄）
Q_NOWAIT = 'Do not wait for the acknowledgement here: that is a full server'
Q_KEEPRES = 'Submitted lookups until cleanup_lookup_result. Each keeps its result'
Q_ONLYAFTER = 'is sent to a server **only after** that'
Q_ATMOST = 'So each server gets **at most one** new'
Q_DEADLINE = "An RPC's deadline counts from when it is sent, so time spent waiting"
Q_THREAD = 'RPC operations are thread-safe because they may happen in different'
Q_RECV = "which for messaging clients is the transport's receive thread, so"
Q_FREEZE = 'Freeze only on a real submit'
Q_APCCAP = 'vLLM caps the APC hit at num_prompt_tokens - 1; an all-covered'
Q_NOSUM = 'be summed (that would double-count the overlapping prefix).'
Q_LOWER = "Lower bound: vLLM's real computed position. Partial hybrid hits"
Q_SCHED = 'avoid saving tokens whose KV has not been computed yet.'
Q_KEEP = 'Keep the blocks only while a store may still read them.'
Q_ASSERT = 'asserts on a finished_sending report for a request it freed.'
Q_FULLHIT = 'In full-prompt-hit case, we need to recompute the last token.'
Q_FAILCLOSED = 'Fail closed for this request instead'
Q_BYPASS = 'A failed asynchronous load is bypassed until vLLM admits the request'
Q_RACE = 'to avoid a cross-stream data race'
Q_FLOOR = 'the end LMCache block is not freed (i.e., floor division)'
Q_STICKY = 'Sticky: after a shrink, the request looks up the full prefix from token 0.'
Q_ALLBLK = 'which returns ALL blocks for the request (not just newly allocated).'
Q_FREE = 'Calling'
Q_TTLFREE = 'this prevents those chunks from holding read locks until TTL expiry.'
Q_T112 = 'APC hit 112 floors to 64; chunk [192, 256) must still be stored.'
Q_TCOVER = 'The empty job still resolves: its hit is the covered prefix, offset from'
Q_TRANK = 'vLLM frees a delayed request once every rank reports it, including'
Q_TFAIL = 'A failed full async load must not re-enter remote wait forever.'


# ---------------------------------------------------------------- 小图（同一套数：A 1000 token，B 1200 token，chunk 256，block 16）
def TR(state, vh, lh, stored, blk, sched):
    return g_kv([('state', state), ('vLLM 命中', vh), ('LMCache 命中', lh),
                 ('已下单存', stored), ('block', blk), ('已调度', sched)])


G_TR0 = g_kv([('（还没有请求）', '')])
G_ASK0 = g_kv([('第几次问', '—'), ('查', '—'), ('答', '—')])
G_ADP0 = g_list(['LOOKUP —', 'QUERY —', '结果 —'])
G_SRV0 = g_text('—', '还没收到 LOOKUP')
G_ALLOC0 = g_kv([('岔路', '—'), ('FREE', '—')])
G_BUILD0 = g_kv([('computed', '—'), ('avail', '—'), ('staging', '—')])
G_FIN0 = g_kv([('store 过?', '—'), ('延迟名单', '{}')])
G_META0 = g_list(['（本步没有单）'])

# 全景（没按 ▶ 时）显示的快照：请求 B 在 s2′ 出完 RETRIEVE 单那一刻
S_TR = TR('READY', '0', '768', '768', '48', '0')
S_ASK = g_kv([('第几次问', '2'), ('查', '[0, 1024)'), ('答', '(768, True)')])
S_ADP = g_list(['B：已清掉', '锁留给 RETRIEVE'])
S_SRV = g_text('768', '3 块命中，已上读锁')
S_ALLOC = g_kv([('岔路', '→ 要取'), ('FREE', '终点 0，不发')])
S_BUILD = g_kv([('取 start', '0'), ('取 end', '768'), ('skip', '0')])
S_FIN = g_kv([('store 过?', '否'), ('延迟名单', '{}')])
S_META = g_list(['RETRIEVE B [0, 768)', 'skip 0 · 48 个 block'], on=0)


def nodes():
    return {'sched': _sched()}


def _sched():
    parts = [
        # ------------------------------------------------ 中心：账本
        part('tracker', '每请求一本账', [490, 190, 240, 190], m=[20, 706, 300, 220], code='RequestTracker',
             kind='store', glyph=S_TR,
             say='跨步接力全靠它记着', why='回调一次一调，状态只能存这', trap='「已下单存」不等于存好了',
             card=card(
                 ['每个请求一本账：`request_trackers[request_id]`，请求第一次被问到时建、结束时删。'
                  + R(CON, 846) + R(MD, 69),
                  '图上四个回调都读写它。一个请求的 LOOKUP、分配、取回、存，分散在好几个调度步里，'
                  '靠的就是这本账上的几个字段接力。'],
                 ['**state**：`PREFETCHING`（在查）/ `WAITING_FOR_LOAD`（要取）/ `READY` / `BYPASS_LMCACHE`（加载失败后绕开）。'
                  + R(MD, 53, 66),
                  '**vLLM 命中**：vLLM 自己前缀缓存已有的 token，向下取整到 block 边界（例子里是 16）。每次拿到 LOOKUP 结果时重写。'
                  + R(CON, 1491, 1495),
                  '**LMCache 命中**：LOOKUP 返回的命中 token 数（从 0 数起的绝对值）。' + R(CON, 1540),
                  '**已下单存**（`num_stored_tokens`）：已在 LMCache 里、或已经下过 STORE 单的 token 数，永远是 256 的倍数。'
                  '命中时先设成命中数（取整块）。' + R(CON, 1539),
                  '之后每出一张 STORE 单就加上。' + R(MD, 456),
                  '**block**：vLLM 分给它的 block 编号；**已调度**：从建账起累计调度过的 token。另有 `num_engine_computed_tokens`'
                  '（vLLM 真实已算位置）、`store_scheduled`（发没发过 STORE 单）。' + R(MD, 83, 99),
                  '还有三个只在「vLLM 自己命中一部分」时才用的字段：LOOKUP 起点、粘性的「从 0 重查」、待放的锁区间。' + R(MD, 122, 130)],
                 [note('推断', 'vLLM 的回调是一次一次互不相干的函数调用，没有地方替连接器记「这个请求走到哪了」；'
                               '跨步的接力只能放在连接器自己的这本账里。'),
                  note('注释', '记「已调度」是为了不把还没算出来的 token 拿去存。', q=Q_SCHED, ref=A(MD, 86))],
                 ['「已下单存」是**下单那一步**就加的，不等 STORE 做完。非 lazy 模式下调度侧收不到存储失败的回报，'
                  '失败了也不会补存。' + R(MD, 456) + R(CON, 1742, 1743),
                  '状态枚举的 docstring 还写着旧函数名 `process_loading_requests`，现在叫 `_process_retrieve_requests`。'
                  + R(MD, 57) + R(CON, 1971)]),
             to='sched.tracker'),

        # ------------------------------------------------ 四个回调（vLLM 调度器按顺序调）
        part('ask', '问：能取多少', [130, 120, 240, 140], m=[4, 456, 160, 140], code='get_num_new_matched…',
             kind='act', n='1', glyph=S_ASK,
             say='没好就答 None，下步再问', why='不在调度线程上等', trap='True 不等于命中',
             card=card(
                 ['vLLM 调度器想把一个请求排进来之前，先问它：「这个请求有多少 token 能从外面拿？」'
                  '（`get_num_new_matched_tokens`）。' + R(CON, 1377),
                  '三种回答：`(None, True)` 还没查完、下一步再问我；`(0, False)` 没命中；`(n, True)` 有 n 个要异步取回。'
                  + R(CON, 1395, 1400)],
                 ['每被问一次都从头走一遍：先看是不是 BYPASS → 算 LOOKUP 起点 → **试着**发 LOOKUP（这个请求已经发过就什么也不做）'
                  '→ 问一次结果。' + R(CON, 1409, 1480),
                  '查的终点 = 总 token 向下取整到 256：A 1000 → 768，B 1200 → 1024。' + R(ADP, 857, 863),
                  '查的起点 = vLLM 已有的 token 先取整到 16、再取整到 256：A、B 都是 0；B′（vLLM 已有 320）是 256。'
                  + R(CON, 1450, 1459),
                  '拿到结果 ret：写 vLLM 命中、LMCache 命中，「已下单存」= ret 取整块；要取 = max(0, ret − vLLM 已算)。'
                  + R(CON, 1491, 1544)],
                 [note('注释', '不等 LOOKUP 回执：那是调度线程上一整趟服务端往返。', q=Q_NOWAIT, ref=A(ADP, 874)),
                  note('提交', '后来连查状态也改成不阻塞：没回来就答 None，交还给 vLLM 下一步再问。',
                       q='Poll lookup status without blocking the scheduler', ref='c:588c5366'),
                  note('注释', '起点只在「真发出 LOOKUP」那次记下；之后的空转询问不改它，否则会记成在途 LOOKUP 根本没用过的边界。',
                       q=Q_FREEZE, ref=A(CON, 1473)),
                  note('注释', '整段 prompt 都命中时少报一个 token，让 vLLM 至少算一个（否则调度器断言失败）；账上的命中数不减。',
                       q=Q_FULLHIT, ref=A(CON, 1555))],
                 ['第二项 `True` 不是「命中了」，是「异步：这个请求先别算」——`(None, True)` 也是 True。',
                  '要问几次不固定：最快是下一步就拿到（设 RPC 往返比一个调度步短）；仓库里没有实测步数。',
                  note('注释', 'APC 缩水（设 B″：vLLM 已有从 320 掉到 128）：记下旧 LOOKUP 锁住的区间、清账、同一步从 0 重发 LOOKUP，'
                               '之后一直从 0 查。', q=Q_STICKY, ref=A(MD, 126))]),
             to='sched.ask'),
        part('alloc', '分配后：定岔路', [880, 190, 240, 140], m=[176, 456, 160, 140], code='update_state_after…',
             kind='gate', n='2', glyph=S_ALLOC,
             say='要取还是不用取，顺手放锁', why='有了 block 才知道往哪写', trap='blocks 是全部不是新增',
             card=card(
                 'vLLM 给请求分好 block 后回调 `update_state_after_alloc(请求, 全部 block, 外部 token 数)`。'
                 '这里做四件事：记 block、定岔路、丢掉 LookupState、放用不上的读锁。' + R(CON, 1600),
                 ['只追加超出已记录数量的 block。' + R(CON, 1629, 1638),
                  '岔路：外部 token > 0 **且** LMCache 命中 > vLLM 命中 → `WAITING_FOR_LOAD`（要取）；否则 → `READY`。'
                  + R(CON, 1648, 1656) + R(MD, 179, 189),
                  '到这时才 `cleanup_lookup_result`：LookupState 一直活到分配成功。' + R(CON, 1658),
                  'FREE 的终点三选一：LMCache 没命中 → 0；不用取 → 全部命中；要取 → vLLM 命中数。再加上以前留下的待放区间；'
                  '有东西可放才发。' + R(CON, 1663, 1687),
                  'BYPASS 的请求在这里直接回 `READY`，不走岔路。' + R(CON, 1641, 1646)],
                 [note('注释', 'FREE 的终点在服务端向下取整：vLLM 命中落在某块中间时，那块还要取，它的锁由 RETRIEVE 放。',
                       q=Q_FLOOR, ref=A(CON, 1672)),
                  note('注释', '用不上的块要显式放锁，否则锁到超时。', q=Q_TTLFREE, ref=A(ADP, 1026)),
                  note('推断', '岔路放在分配之后：要取就得有 block 可写，分配前取回的数据没有落脚处。')],
                 [note('注释', '`blocks` 是这个请求的**全部** block。要异步取的请求会被调两次（第一次分配取回用的，'
                               '取完再分配其余），所以只追加新增部分。', q=Q_ALLBLK, ref=A(CON, 1619)),
                  'FREE 常常什么都不放：B 的 vLLM 命中 0 → 不发；B′ 发 `[0, 320)`，服务端取整成 `[0, 256)` 是空的，'
                  '因为 LOOKUP 从 256 查起、h0 根本没锁。真放东西的是：不用取（放全部）、起点记得比最终 vLLM 命中低、APC 缩水留下的区间。',
                  '请求在分配之前就被取消：不发 FREE，读锁等 TTL（默认 300 秒）。底稿没查到这是有意还是遗漏。'
                  + R(DCFG, 432)]),
             to='sched.alloc'),
        part('build', '每步出单', [130, 370, 240, 140], m=[4, 1036, 160, 140], code='build_connector_meta',
             kind='act', n='3', glyph=S_BUILD,
             say='算出存哪几块、取哪几块', why='两个 max，防重算防漏存', trap='下单即记账，不是纯函数',
             card=card(
                 '每个调度步 vLLM 调一次 `build_connector_meta`：为所有请求出本步的工单，打进本步元数据。'
                 + R(CON, 1694, 1713),
                 ['顺序：交出延迟名单 → 遍历**所有**账本，`WAITING_FOR_LOAD` 的出 RETRIEVE 单并置 `READY` → 本步新调度的请求 → '
                  '已在跑的请求（这两类先更新「已调度」和 vLLM 已算位置，再试着出 STORE 单）。',
                  '取单和存单各自的入口：' + R(CON, 1977, 1987) + R(CON, 1996, 2020),
                  '存多少：computed = max(已调度 + max(vLLM 命中, LMCache 命中), vLLM 已算位置)；'
                  'avail = min(总 token, 已分配 block × 16, computed)，再按 prompt 长度封顶；staging = avail − 已下单存；'
                  '够几个 256 就出几块。',
                  '公式在这里：' + R(MD, 398, 430),
                  '出 STORE 单的同时：「已下单存」+= 块数 × 256，`store_scheduled` = True。' + R(MD, 456) + R(CON, 2020),
                  '取多少：start = vLLM 命中向下取整到 256；end = LMCache 命中；skip = vLLM 命中 − start。' + R(MD, 618, 644)],
                 [note('注释', '内层 max 不能换成相加：两者都是「前缀 [0, x) 已有」，只是来源不同，相加会把重叠的前缀算两遍。',
                       q=Q_NOSUM, ref=A(MD, 382)),
                  note('测试', '外层 max 是下界：vLLM 命中被取整变小时，旧公式会漏存一块。测试的例子：命中 112 取整成 64，'
                               '188 + 64 = 252 够不到 256；用 vLLM 已算位置 300 兜底，`[192, 256)` 就补上了。',
                       q=Q_T112, ref=A(T_BOUND, 61)),
                  note('注释', '「已调度」那一项让分块预填边算边存：只存已经算出 KV 的 token。', q=Q_SCHED, ref=A(MD, 86)),
                  note('注释', '取回要跳过 vLLM 已有的那几个 token：那些 block 可能正被别的请求在 vLLM 的流上读。',
                       q=Q_RACE, ref=A(MD, 641))],
                 ['两个 max 不是一回事：内层防重复计数，外层是下界。',
                  'RETRIEVE 单跟着账本的状态出，不跟着「请求被调度」出：B 那一步没被调度计算，照样出了取单。'
                  '（看循环遍历的是全部账本得出。）' + R(CON, 1977, 1978),
                  '存的算术在函数里就改了「已下单存」：同一步调两次会少出单。',
                  '分块预填（设每步预算 512）：A 第 1 步只分到 32 个 block → STORE `[0, 512)`；第 2 步补齐 → STORE `[512, 768)`。']),
             to='sched.store'),
        part('finish', '结束：延迟回收', [880, 370, 240, 140], m=[176, 1036, 160, 140], code='request_finished',
             kind='act', n='4', glyph=S_FIN,
             say='下过存单的，block 晚点还', why='STORE 可能还在读 block', trap='名单要绕 worker 一圈',
             card=card(
                 '请求结束时 vLLM 调 `request_finished`，问：这个请求的 block 能马上回收吗？返回 True = 先别回收，等我报。'
                 + R(CON, 1754),
                 ['先读 `store_scheduled` 定下 delay_free，**再**删账本——顺序不能反。' + R(CON, 1793, 1797),
                  '清掉 LookupState（LOOKUP 结果还没被取走就取消的请求也靠这一句）→ 发 `END_SESSION` → 进延迟名单 → 返回。'
                  + R(CON, 1802) + R(CON, 1821, 1824),
                  '名单下一步随元数据交给 worker；worker 只报「名单里有、且 STORE 已做完」的请求，报完从名单里扣掉；'
                  'vLLM 收齐各 worker 的报告才回收。',
                  '这几跳的位置：' + R(CON, 1708, 1709) + R(CON, 1094) + R(CON, 1254, 1255)],
                 [note('注释', '只在 STORE 可能还在读这些 block 时才留着它们。', q=Q_KEEP, ref=A(CON, 1791)),
                  note('提交', '改前是所有生产者的请求一律延迟；改成只对真下过 STORE 单的请求延迟。',
                       q='Delay frees only for requests with a scheduled store', ref='c:752e01fd'),
                  note('注释', '名单必须精确：vLLM 对「没要求延迟、却被报存完」的请求会断言失败。', q=Q_ASSERT, ref=A(CON, 1253)),
                  note('测试', '没有自己 STORE 的 worker 也要报，否则 vLLM 凑不齐。', q=Q_TRANK, ref=A(T_ROLES, 654))],
                 [note('推断', '名单为什么要绕元数据走一趟：「下没下过 STORE 单」只记在调度侧的账本上，worker 自己不知道。'),
                  '`END_SESSION` 在服务端只删 Session、刷新 LRU，**不放读锁**。' + R(LK, 611, 646),
                  '真正回收大约晚一步（推断）：A 在 sN 结束，名单 sN+1 才到 worker；STORE 没做完还会更晚。']),
             to='sched.finish'),

        # ------------------------------------------------ 查找这一侧
        part('adapter', '查找适配器', [490, 10, 240, 120], m=[90, 196, 160, 150], code='LookupState',
             kind='bg', glyph=S_ADP,
             say='每请求一条异步查询链', why='回复在别的线程推进', trap='结果留到分配后才清',
             card=card(
                 ['调度侧专管 LOOKUP 的一层：适配器里每个请求一个 `LookupState`，'
                  '管「LOOKUP → QUERY_PREFETCH_STATUS → 命中数」这条异步链。', '位置：' + R(ADP, 650) + R(MLS, 69)],
                 ['发：服务端不健康 → 不发；这个请求已有 LookupState → 不重发；否则新建，向**每台**服务端发 LOOKUP。'
                  + R(ADP, 850, 885),
                  '每台服务端一条链：LOOKUP 回执到 → 立刻发 QUERY；QUERY 回整数 → 定了；回 None（预取还在跑）→ 再发。'
                  + R(MLS, 25, 41) + R(MLS, 313, 353),
                  '推着它走的有两条线程：RPC 回复一到，传输层接收线程跑回调；调度器每问一次（`on_scheduler_check`），'
                  '处理已到的回复、重发延后的 QUERY、查超时，**默认不等**。',
                  '两条线程各自的入口：' + R(MLS, 228, 282) + R(FUT, 76),
                  '多台服务端：取最小值，多出来的尾部锁当场放掉。' + R(MLS, 373, 399)],
                 [note('注释', '两条线程都会碰它，所以自带一把锁。', q=Q_THREAD, ref=A(MLS, 96)),
                  note('注释', 'QUERY 只在该服务端回执 LOOKUP 之后才发。推断：否则 QUERY 可能先于 LOOKUP 到服务端，查不到任务。',
                       q=Q_ONLYAFTER, ref=A(MLS, 85)),
                  note('注释', '回 None 后，每次调度检查最多放行一条新 QUERY：重试的频率就是调度步的频率。', q=Q_ATMOST, ref=A(MLS, 89)),
                  note('注释', '超时从发出时算，等调度检查的时间不算在服务端头上。', q=Q_DEADLINE, ref=A(MLS, 91)),
                  note('注释', '结果定了就留着，直到 cleanup：vLLM 重复问同一个请求，拿到的是同一个值，不会再查一次。',
                       q=Q_KEEPRES, ref=A(ADP, 647))],
                 ['结果留到分配后才清：vLLM 因为没空闲 block 一直重问时，LOOKUP 上的读锁也一直挂着（推断）。',
                  '超时默认 300 秒；超时的服务端被标不健康，之后新请求不查、一律当 0 命中——降级成「没缓存」，不是报错。'
                  + R(ADP, 124) + R(ADP, 914, 918)]),
             to='sched.lookup'),
        part('server', 'LMCache 服务', [880, 10, 240, 120], m=[90, 6, 160, 132], code='查找模块',
             kind='ext', glyph=S_SRV,
             say='查 L1、上锁、回命中数', why='已有前缀只刷新不上锁', trap='回的是绝对命中数',
             card=card(
                 '顶层的「查找模块」。这一层只关心它对 LOOKUP / QUERY 回什么。',
                 ['LOOKUP 只回执；之后 `QUERY_PREFETCH_STATUS` 回命中 token 数（整数），预取还没完就回 None。',
                  '带起点时：`[0, start)` 只刷新 LRU，不上锁、不预取；`[start, end)` 才预取、上读锁。'
                  + R(LK, 349, 363) + R(LK, 365, 380),
                  '命中数再加回起点，变成从 0 数起的绝对值。' + R(LK, 784)],
                 [note('提交', '跳过 vLLM 已覆盖的前缀：不查、不预取、不上锁。',
                       q='Skip lookup/prefetch of APC-covered prefix', ref='c:60538a08'),
                  note('推断', 'vLLM 反正已有的前缀，没必要从 L1 再取一遍，也不该给它们上读锁。'),
                  note('注释', '起点永远不超过查找终点：vLLM 的 APC 命中最多到 prompt − 1。', q=Q_APCCAP, ref=A(CON, 1448))],
                 [note('测试', '被覆盖的前缀**按命中计入**，不管 LMCache 里有没有，这样「已下单存」保持绝对值。'
                               '推断的后果：那段若只在 vLLM 里、LMCache 里被淘汰了，这个请求不会补存它。',
                       q=Q_TCOVER, ref=A(T_APC, 304))]),
             to='lookup_src'),

        # ------------------------------------------------ 输出
        part('meta', '本步元数据', [490, 420, 240, 90], m=[70, 1286, 200, 120], code='ConnectorMetadata',
             kind='out', glyph=S_META,
             say='工单 + 延迟名单，送 worker', why='搭调度结果的车', trap='下单 ≠ 存完',
             card=card(
                 '本步的全部输出：一组工单、`need_flush_before_forward`、`delay_free_req_ids`，随 vLLM 的调度结果跨进程送到 worker。'
                 + R(MD, 666, 672),
                 ['一张工单：方向（STORE / RETRIEVE）、token 区间 `[start, end)`、要搬的 block、`skip_first_n_tokens`。',
                  '本步有被抢占 / 恢复的请求 → `need_flush_before_forward` = True，worker 前向之前先把在途的 STORE 刷完。'
                  + R(CON, 1707)],
                 [note('推断', '不单独发 RPC：搭 vLLM 每步本来就要发的调度结果，worker 在前向前后照单执行。')],
                 ['工单发出去不代表做完：「已下单存」在出单时就加了，做没做完只有 worker 知道。']),
             to=None),
    ]

    wires = [
        wire('w_sub', 'ask', 'adapter', '发 LOOKUP·取结果', ml='发·取', why='已发过就不重发', trap='每问一次都会进来',
             mfs='t', mts='b', mlp=[80, 376],
             card=card(
                 '每次被问，「问」都调适配器两下：`maybe_submit_lookup_request`（发）和 `check_lookup_result`（取）。'
                 + R(CON, 1463, 1480),
                 ['发：已有这个请求的 LookupState 就直接返回 False；返回值决定要不要记下 LOOKUP 起点。' + R(ADP, 853, 855)
                  + R(CON, 1475, 1476),
                  '取：没定就返回 None；不健康或已清掉返回 0。' + R(ADP, 914, 922)],
                 [note('推断', '把「发」写成幂等的，调用方就可以每次无脑调用，不用自己记「发过没有」。')],
                 '同一线程上的普通函数调用，不是 RPC；RPC 在下一条线（适配器 ⇄ 服务端）。')),
        wire('w_rpc', 'adapter', 'server', 'LOOKUP / QUERY', ml='LOOKUP / QUERY', why='回复在接收线程处理', trap='不等回执',
             cls='dash', mfs='t', mts='b',
             card=card(
                 '适配器和服务端之间的 ZMQ 异步 RPC：LOOKUP、QUERY_PREFETCH_STATUS，以及 FREE_LOOKUP_LOCKS、END_SESSION。'
                 + R(MLS, 136, 148),
                 ['回复到达时，future 的回调在传输层的接收线程上跑，直接推进 LookupState。' + R(FUT, 76),
                  'LOOKUP 回执失败也照发 QUERY，只记日志。' + R(MLS, 317, 325)],
                 [note('注释', '完成回调跑在传输层的接收线程上。', q=Q_RECV, ref=A(FUT, 76)),
                  note('推断', '让回复在别的线程里推进，调度线程只在被 vLLM 问到时看一眼结果，不必等任何一趟往返。')],
                 '虚线：这条线上的事发生在两个调度步之间，不在调度线程上。')),
        wire('w_hit', 'ask', 'tracker', '写命中数', why='每次拿到结果都重写', trap='命中 0 时什么都不写',
             mfs='b', mts='t', mlp=[80, 626],
             card=card(
                 '拿到 LOOKUP 结果后，把 vLLM 命中、LMCache 命中、「已下单存」写进账本。' + R(CON, 1491, 1544),
                 ['vLLM 命中 = `num_computed_tokens` 向下取整到 16。' + R(CON, 1491, 1495),
                  '「已下单存」= 命中向下取整到 256：命中的块已在 LMCache 里，不用再存。' + R(CON, 1538, 1539)],
                 [note('推断', '把「LMCache 已有」直接记成「已存」，后面存的算术就从命中之后接着算，命中的块不会再存一遍。')],
                 '命中 0 时提前返回 `(0, False)`：LMCache 命中和「已下单存」都没被写，保持 0。' + R(CON, 1532, 1533))),
        wire('w_state', 'alloc', 'tracker', '定岔路·记 block', ml='定岔路', why='岔路只在 PREFETCHING 定', trap='第二次调只追加',
             mfs='b', mts='t', mlp=[260, 676],
             card=card(
                 '分配回调把 block 追加进账本，并把 `PREFETCHING` 推成 `WAITING_FOR_LOAD` 或 `READY`。' + R(CON, 1629, 1656),
                 '只有在 `PREFETCHING` 时才定岔路；同一请求第二次被调（取完再分配其余 block）只追加 block，state 不动。'
                 + R(CON, 1649),
                 [note('推断', '第一次分配就把路定死，第二次回调就只是记账，不会把已经取完的请求又推回「要取」。')])),
        wire('w_free', 'alloc', 'adapter', '清状态·放锁', ml='清·放锁', why='不取的块别锁到超时', trap='常常一把也不放',
             cls='dash', mfs='t', mts='b', mlp=[260, 426],
             card=card(
                 '分配回调调适配器两件事：`cleanup_lookup_result` 丢掉 LookupState；有区间时发 `free_lookup_locks`。'
                 + R(CON, 1658) + R(CON, 1677, 1687),
                 ['FREE 的区间是 `[0, free_end)` 再加 `extra_ranges`（以前放弃的 LOOKUP 留下的区间），服务端向下取整到 256。',
                  '调度侧只有两处会发 FREE：这里，和多服务端取最小值时的尾部。' + R(CON, 1678) + R(MLS, 399)],
                 [note('注释', '用不上的块要显式放锁，否则锁到超时。', q=Q_TTLFREE, ref=A(ADP, 1026))],
                 '虚线：大多数请求走这条线时只清状态，不放锁。')),
        wire('w_read', 'build', 'tracker', '算账·记已下单', ml='算账', why='下单那刻就记', trap='调两次会少出单',
             mfs='t', mts='b', mlp=[80, 956],
             card=card(
                 ['出单时读账本里的命中数、已调度、已分配 block、「已下单存」，算完立刻改账：加「已下单存」、标 `store_scheduled`、'
                  'RETRIEVE 后置 `READY`。', '改账的三处：' + R(MD, 456) + R(CON, 1987) + R(CON, 2020)],
                 '取单只看 state 是不是 `WAITING_FOR_LOAD`；存单每步对被调度的请求都算一遍。',
                 [note('推断', '出单当场记账，下一步就从新的「已下单存」往后算，同一块不会被下两次单——不用等 worker 回报。')],
                 '代价：STORE 失败了账本也不知道。')),
        wire('w_del', 'finish', 'tracker', '先读后删', why='删了就读不到了', trap='删的是账本不是 KV',
             mfs='t', mts='b', mlp=[260, 1006],
             card=card(
                 '结束回调先读 `store_scheduled`，再删掉这个请求的账本。' + R(CON, 1793, 1797),
                 '删账本只是调度侧的内存清理；LMCache 里存下的 KV 不受影响。',
                 [note('推断', '延迟与否只取决于「下没下过存单」，所以必须在删之前读出来。')])),
        wire('w_op', 'build', 'meta', '工单', why='一步一份，随调度结果走', trap='取单可能给没被调度的请求',
             mfs='b', mts='t', mlp=[80, 1206],
             card=card(
                 '算出来的 STORE / RETRIEVE 单逐张加进本步元数据。' + R(CON, 1986) + R(CON, 2019),
                 '先加取单，再加存单。为什么是这个顺序底稿没查到（worker 按方向分开处理，看不出影响）。',
                 [note('推断', '工单是「本步的」：worker 拿到就执行，不跨步保存。')])),
        wire('w_list', 'finish', 'meta', '延迟名单（下一步）', ml='延迟名单', why='worker 不知道谁下过存单',
             trap='晚一步才交出', cls='dash', mfs='b', mts='t', mlp=[260, 1256],
             card=card(
                 '结束回调把请求加进 `_delay_free_req_ids`；**下一次** `build_connector_meta` 才把它交给元数据并清空。'
                 + R(CON, 1822, 1823) + R(CON, 1708, 1709),
                 'worker 在 `start_load_kv` 开头把名单并进自己的（在「没有取单就早退」之前），`get_finished` 时求交、扣除。'
                 + R(CON, 1094) + R(CON, 1254, 1255),
                 [note('注释', '只报 request_finished 返回过 True 的请求。', q=Q_ASSERT, ref=A(CON, 1253))],
                 '虚线：名单不在结束的那一步走，而是在下一步。')),
    ]

    steps = [
        beat('A · s1：第 1 次被问',
             '接顶层的请求 A：1000 token。（步号 s1、s2… 是设的，只表示先后。）vLLM 第 1 次问「能取多少」：新建账本，state = '
             '`PREFETCHING`；起点 0（vLLM 已有 0），终点 1000 取整 = **768**。适配器里没有 A 的 LookupState → 新建，发 LOOKUP '
             '`[0, 768)`，不等回执；当场问一次结果：没有 → 答 `(None, True)`。',
             note('注释', '等回执就是调度线程上一整趟服务端往返。', q=Q_NOWAIT, ref=A(ADP, 874)),
             focus=['ask', 'w_sub', 'adapter', 'w_hit', 'tracker'],
             state={'ask': g_kv([('第几次问', '1'), ('查', '[0, 768)'), ('答', '(None, True)')]),
                    'tracker': TR('PREFETCHING', '—', '—', '0', '0', '0'),
                    'adapter': g_list(['LOOKUP 在途', 'QUERY —', '结果 —'], on=0),
                    'server': G_SRV0, 'alloc': G_ALLOC0, 'build': G_BUILD0, 'finish': G_FIN0, 'meta': G_META0}),
        beat('两步之间：回复在别的线程里推进',
             '服务端查 L1：A 的 3 块全 miss（顶层讲过）。LOOKUP 回执到达，传输层接收线程上的回调**立刻**发 QUERY_PREFETCH_STATUS；'
             'QUERY 回整数 0 → 这台服务端定了，结果 = **0**，之后不再变。这一切调度线程都没参与。',
             note('注释', 'QUERY 只在这台服务端回执 LOOKUP 之后才发。', q=Q_ONLYAFTER, ref=A(MLS, 85)),
             focus=['adapter', 'w_rpc', 'server'],
             state={'adapter': g_list(['LOOKUP ✓ 回执', 'QUERY ✓ 回 0', '结果 = 0（定了）'], on=2),
                    'server': g_text('0', 'A 的 3 块全 miss')}),
        beat('A · s2：第 2 次被问，拿到 0',
             '又被问：`maybe_submit` 看到已有 LookupState，不重发；`check_lookup_result` 直接拿到 0。写 vLLM 命中 = 0。'
             '结果是 0 → 提前答 `(0, False)`：LMCache 命中、「已下单存」都**没写**，保持 0。',
             note('注释', '结果定了就留着，vLLM 重复问拿到的是同一个值。', q=Q_KEEPRES, ref=A(ADP, 647)),
             focus=['ask', 'w_hit', 'tracker', 'w_sub', 'adapter'],
             state={'ask': g_kv([('第几次问', '2'), ('查', '不重发'), ('答', '(0, False)')]),
                    'tracker': TR('PREFETCHING', '0', '0', '0', '0', '0')}),
        beat('A · s2：分好 63 个 block，岔向 READY',
             'vLLM 给 A 分配 ceil(1000/16) = **63** 个 block，调 `update_state_after_alloc(A, 63 个, 0)`。追加 63 个；'
             '外部 token 0 → 不用取 → `READY`。丢掉 A 的 LookupState。LMCache 命中 0 → FREE 终点 0，也没有待放区间 → 不发 FREE。',
             note('推断', '没命中就没有锁，自然也没有要放的。'),
             focus=['alloc', 'w_state', 'tracker', 'w_free', 'adapter'],
             state={'alloc': g_kv([('岔路', '→ READY'), ('FREE', '不发')]),
                    'tracker': TR('READY', '0', '0', '0', '63', '0'),
                    'adapter': g_list(['A：已清掉'])}),
        beat('A · s2：出 STORE 单，当场记账',
             '同一步出单：A 本步调度 1000 → 已调度 1000。computed = max(1000 + 0, 1000) = 1000；'
             '已分配 63 × 16 = 1008；avail = min(1000, 1008, 1000) = 1000；staging = 1000 − 0 → **3 块**。'
             '出 STORE `[0, 768)`、48 个 block；「已下单存」**当场** 0 → 768。',
             note('注释', '两个命中取 max，不相加：都是前缀 [0, x)。', q=Q_NOSUM, ref=A(MD, 382)),
             focus=['build', 'w_read', 'tracker', 'w_op', 'meta'],
             state={'build': g_kv([('computed', '1000'), ('avail', '1000'), ('staging', '1000 → 3 块')]),
                    'tracker': TR('READY', '0', '0', '768', '63', '1000'),
                    'meta': g_list(['STORE A [0, 768)', '48 个 block'], on=0),
                    'finish': g_kv([('store 过?', '是'), ('延迟名单', '{}')])}),
        beat('A · s3…：解码，每步都算一遍',
             '之后每步 A 生成 1 个 token，已调度 +1。avail 仍被 prompt 长度封顶在 1000；staging = 1000 − 768 = **232 < 256** → 不出单。'
             '生成的 100 个 token 默认不存。',
             note('提交', '默认只存到 prompt 长度。', q="Cap MP stores at the request's prompt length", ref='c:7ba466fe'),
             focus=['build', 'w_read', 'tracker'],
             state={'build': g_kv([('computed', '1001 起'), ('avail', '1000（封顶）'), ('staging', '232 < 256')]),
                    'tracker': TR('READY', '0', '0', '768', '63+', '1001 起'),
                    'meta': g_list(['（A 没有新单）'])}),
        beat('A · sN：结束，要求延迟回收',
             '`request_finished(A)`：先读 `store_scheduled` = True → delay_free = True；再删账本；发 `END_SESSION`；'
             'A 进延迟名单 `{A}`；答 True：「这 63 个 block 先别回收」。',
             note('注释', '只在 STORE 可能还在读这些 block 时才留着它们。', q=Q_KEEP, ref=A(CON, 1791)),
             focus=['finish', 'w_del', 'tracker'],
             state={'finish': g_kv([('store 过?', '是 → 延迟'), ('延迟名单', '{A}')]),
                    'tracker': g_kv([('A', '账本已删')])}),
        beat('A · sN+1：名单随单走，晚约一步回收',
             '下一步 `build_connector_meta` 才把 `{A}` 放进元数据，并清空自己的名单。worker 并进自己的名单；`get_finished` 里 '
             'A 的 STORE 早已做完 ∩ 名单 = `{A}` → 报 `finished_sending = {A}`，扣掉。vLLM 这时才回收 63 个 block。',
             note('注释', '只能报「要求过延迟」的请求，否则 vLLM 断言失败。', q=Q_ASSERT, ref=A(CON, 1253)),
             focus=['finish', 'w_list', 'meta'],
             state={'meta': g_list(['delay_free_req_ids', '= {A}'], on=1),
                    'finish': g_kv([('store 过?', '—'), ('延迟名单', '{}（已交出）')])}),
        beat('B · s1′：第 1 次被问，查 4 块',
             '请求 B：1200 token，vLLM 本地命中 0。新账本 `PREFETCHING`；查 `[0, 1024)`（1200 取整），答 `(None, True)`。'
             '两步之间：服务端前 3 块命中、各上 1 把读锁，QUERY 回 **768**，结果定了。',
             note('推断', '和 A 一样，命中数要到下一次被问才交得出。'),
             focus=['ask', 'w_sub', 'adapter', 'w_rpc', 'server'],
             state={'ask': g_kv([('第几次问', '1'), ('查', '[0, 1024)'), ('答', '(None, True)')]),
                    'tracker': TR('PREFETCHING', '—', '—', '0', '0', '0'),
                    'adapter': g_list(['LOOKUP ✓ 回执', 'QUERY ✓ 回 768', '结果 = 768（定了）'], on=2),
                    'server': g_text('768', '3 块命中，已上读锁'),
                    'alloc': G_ALLOC0, 'build': G_BUILD0, 'finish': G_FIN0, 'meta': G_META0}),
        beat('B · s2′：拿到 768，答 (768, True)',
             '第 2 次被问：vLLM 命中 0，LMCache 命中 768，「已下单存」= 768（这 3 块已在 LMCache，不用再存）。'
             '要取 = 768 − 0 = 768 → 答 `(768, True)`：True 是「异步加载，加载完之前先别算 B」。',
             note('推断', '把命中直接记成「已存」，后面存的算术就从 768 往后接。'),
             focus=['ask', 'w_hit', 'tracker'],
             state={'ask': g_kv([('第几次问', '2'), ('查', '不重发'), ('答', '(768, True)')]),
                    'tracker': TR('PREFETCHING', '0', '768', '768', '0', '0')}),
        beat('B · s2′：分好 48 个 block，岔向「要取」',
             '`update_state_after_alloc(B, 48 个, 768)`：外部 768 > 0，且 LMCache 命中 768 > vLLM 命中 0 → `WAITING_FOR_LOAD`。'
             '这时才丢掉 LookupState。FREE 终点 = vLLM 命中 0 → 不发：3 把读锁留着，等 RETRIEVE 拷完再放。',
             note('注释', '终点向下取整：还要取的那块，锁由 RETRIEVE 放。', q=Q_FLOOR, ref=A(CON, 1672)),
             focus=['alloc', 'w_state', 'tracker', 'w_free', 'adapter'],
             state={'alloc': g_kv([('岔路', '→ 要取'), ('FREE', '终点 0，不发')]),
                    'tracker': TR('WAITING_FOR_LOAD', '0', '768', '768', '48', '0'),
                    'adapter': g_list(['B：已清掉', '锁留给 RETRIEVE'])}),
        beat('B · s2′：出 RETRIEVE 单（B 并没被调度）',
             '同一步 `build_connector_meta` 遍历**所有**账本，B 在 `WAITING_FOR_LOAD`：start = 0 取整 = 0，end = 768，'
             'skip = 0 − 0 = 0，前 48 个 block → RETRIEVE `[0, 768)`；置 `READY`。B 本步不在被调度计算的名单里，不出 STORE 单。',
             note('推断', '取单跟着账本状态出，不跟着「请求被调度」出：循环遍历的是全部账本。'),
             focus=['build', 'w_read', 'tracker', 'w_op', 'meta'],
             state={'build': g_kv([('取 start', '0'), ('取 end', '768'), ('skip', '0')]),
                    'tracker': TR('READY', '0', '768', '768', '48', '0'),
                    'meta': g_list(['RETRIEVE B [0, 768)', 'skip 0 · 48 个 block'], on=0)}),
        beat('B · s4′：取完、算 432、再存一块',
             'B 取完后被调度，第 2 次分配回调传来**全部 75 个** block → 只追加 27 个。出单：已调度 432；'
             'computed = max(432 + 768, 1200) = 1200；已分配 75 × 16 = 1200；staging = 1200 − 768 = 432 → '
             '**1 块** STORE `[768, 1024)`，「已下单存」→ 1024。',
             note('注释', '`blocks` 是全部 block，不是新增的。', q=Q_ALLBLK, ref=A(CON, 1619)),
             focus=['build', 'w_read', 'tracker', 'alloc', 'w_state', 'w_op', 'meta'],
             state={'alloc': g_kv([('传来 75 个', '追加 27'), ('state', '仍 READY')]),
                    'tracker': TR('READY', '0', '768', '1024', '75', '432'),
                    'build': g_kv([('computed', '1200'), ('avail', '1200'), ('staging', '432 → 1 块')]),
                    'meta': g_list(['STORE B [768, 1024)', '16 个 block'], on=0)}),
        beat('换个 B′：vLLM 自己已有 320',
             '设 B′ 同 B，但 vLLM 本地已有前 **320** token（20 个 block）。起点 = 320 取整到 16 再到 256 = **256** → LOOKUP '
             '`[256, 1024)`：服务端对 h0 只刷新 LRU、不上锁，h1、h2 上锁；回绝对值 768。拿到后 vLLM 命中 320，'
             '要取 = 768 − 320 = **448** → `(448, True)`。',
             note('提交', '跳过 vLLM 已覆盖的前缀：不查、不预取、不上锁。',
                  q='Skip lookup/prefetch of APC-covered prefix', ref='c:60538a08'),
             focus=['ask', 'w_sub', 'adapter', 'w_rpc', 'server', 'w_hit', 'tracker'],
             state={'ask': g_kv([('起点', '320 → 256'), ('查', '[256, 1024)'), ('答', '(448, True)')]),
                    'server': g_text('768', 'h0 不锁 · h1 h2 锁'),
                    'adapter': g_list(['LOOKUP ✓ 回执', 'QUERY ✓ 回 768', '结果 = 768（定了）'], on=2),
                    'tracker': TR('PREFETCHING', '320', '768', '768', '—', '0'),
                    'alloc': G_ALLOC0, 'build': G_BUILD0, 'meta': G_META0}),
        beat('B′：FREE 放了个空，取回带 skip 64',
             '分配回调：要取 → `WAITING_FOR_LOAD`；FREE 终点 = vLLM 命中 320 → 发 `[0, 320)`，服务端取整成 `[0, 256)`——'
             '空的，h0 本来就没锁。出单：start = 320 取整到 256 = 256，end = 768，skip = 320 − 256 = **64**（4 个 block）'
             '→ RETRIEVE `[256, 768)`，32 个 block。',
             note('注释', '跳过 vLLM 已有的 64 个 token：那些 block 可能正被别的请求读。', q=Q_RACE, ref=A(MD, 641)),
             focus=['alloc', 'w_free', 'adapter', 'build', 'w_op', 'meta'],
             state={'alloc': g_kv([('岔路', '→ 要取'), ('FREE', '[0,320) → 空')]),
                    'build': g_kv([('取 start', '256'), ('取 end', '768'), ('skip', '64')]),
                    'tracker': TR('READY', '320', '768', '768', '已分配', '0'),
                    'meta': g_list(['RETRIEVE B′ [256, 768)', 'skip 64 · 32 个 block'], on=0)}),
    ]

    return node(
        'sched', '调度侧连接器里面', '调度侧连接器：一本账，被四个回调轮流推着走',
        '顶层「调度侧连接器」框里面：每个请求一本账，vLLM 的四个回调在好几个调度步里轮流读写它；查询是异步的，存单下了就记账。',
        lead='你在顶层「调度侧连接器」框里面。中间是**每个请求一本账**；vLLM 调度器按 ①②③④ 四个回调轮流来问、来改它，'
             '一个请求要跨好几个调度步才走完。按 ▶ 接着顶层的例子走：A 只存，B 先取后存，最后换个 vLLM 自己已有 320 的 B′。',
        src=A(CON, 1377, 1824),
        blocks=[scene(1220, 520, parts, wires, steps, mw=340, mh=1420,
                      cap='图上的值：请求 B 在 s2′ 出完 RETRIEVE 单那一刻（按 ▶ 从 A 开始走）· 全在 vLLM 调度线程上（RPC 回调除外）· '
                          'A 1000 token，B 1200 token · chunk 256 · block 16')],
        children=[_tracker_src(), _ask_src(), _lookup_src(), _alloc_src(), _store_src(), _finish_src()])


# ---------------------------------------------------------------- 源码页
def _tracker_src():
    return node(
        'sched.tracker', '账本的字段', 'tracker：四个状态和图上那几个字段',
        '账本的状态枚举、字段定义和「要不要取」的判断，在源码里就是一个 dataclass。',
        lead='你在「调度侧连接器」图中间的「每请求一本账」里面。三段：状态枚举、字段（图上那几行）、「要不要取」的判断。',
        src=A(MD, 53, 189),
        blocks=[
            code(MD, [(53, 66)], notes=[
                at((55, 60), None, 'docstring 里的状态机。第 57 行的 `process_loading_requests` 是旧名字，现在叫 '
                                   '`_process_retrieve_requests`。另外还有 `PREFETCHING → READY`（不用取）这条，docstring 没写。',
                   has='State machine'),
                at((62, 65), None, '四个状态。A 走 PREFETCHING → READY；B 走 PREFETCHING → WAITING_FOR_LOAD → READY。',
                   has='PREFETCHING'),
            ], cap='状态枚举'),
            code(MD, [(83, 103), (122, 130)], notes=[
                at(83, None, '图上的「block」：按 KV group 分组的 block 编号。A 是 63 个，B 先 48 后 75。',
                   has='allocated_block_ids'),
                at((85, 87), '注释', '图上的「已调度」：存的时候用它保证不存还没算的 token。A 一步调度 1000；B 取完后一步 432。',
                   q=Q_SCHED, has='num_scheduled_tokens'),
                at((89, 90), None, 'vLLM 真实已算到的位置，存的算术里当下界用。B 在 s4′ 是 768 + 432 = 1200。',
                   has='num_engine_computed_tokens'),
                at((92, 96), None, '图上的「已下单存」：只算整块，永远是 256 的倍数。A 出单后 768；B 命中后 768、再存一块后 1024。',
                   has='num_stored_tokens'),
                at((98, 99), None, '有没有发过 STORE 单——结束时据此决定要不要延迟回收 block。', has='store_scheduled'),
                at((101, 103), None, '图上的两个命中数：拿到 LOOKUP 结果那次写。B′ 是 vLLM 320、LMCache 768。',
                   has='num_lmcache_hit_tokens'),
                at((122, 130), '注释', '只在 vLLM 自己命中一部分时才用的三个字段：LOOKUP 起点（发出时冻结）、缩水后粘性地从 0 重查、'
                                     '被放弃的 LOOKUP 留下的待放锁区间。', q=Q_STICKY, has='need_full_lookup'),
            ], cap='字段：图上账本里的那几行'),
            code(MD, [(179, 189)], notes=[
                at((182, 189), None, '要取 = LMCache 命中多于 vLLM 已有，并且还没处理过（不在 READY / BYPASS）。'
                                     'B：768 > 0 → 要取；A：0 > 0 不成立 → 不用取。', has='num_lmcache_hit_tokens > self.num_vllm_hit_tokens'),
            ], cap='needs_retrieve：分配回调用它定岔路'),
        ])


def _ask_src():
    return node(
        'sched.ask', '「问」的源码', 'get_num_new_matched_tokens：每被问一次从头走一遍',
        'vLLM 每问一次，这个函数从头走一遍：先处理 BYPASS，算起点、试着发 LOOKUP、问结果，拿到了再记账、算要取多少。',
        lead='你在「调度侧连接器」图的 ① 「问：能取多少」里面。两段：前半「发 + 问」（每次被问都会走），后半「拿到结果之后」（只走一次，'
             'APC 缩水时例外）。例子：A 查 `[0, 768)` 拿到 0；B 查 `[0, 1024)` 拿到 768；B′ 从 256 查起。',
        src=A(CON, 1409, 1575),
        blocks=[
            code(CON, [(1409, 1416), (1426, 1443), (1450, 1480)], notes=[
                at(1409, None, '有账本就拿，没有就建（新账本是 PREFETCHING）。被抢占的请求再来时，旧账本会在这里被整个丢掉重建。',
                   has='_get_or_create_request_tracker'),
                at((1415, 1416), '注释', 'BYPASS 里的请求一律答 `(0, False)`，不再发 LOOKUP。', q=Q_BYPASS,
                   ref=A(CON, 1411), has='BYPASS_LMCACHE'),
                at((1426, 1430), '注释', '三个条件一起成立 = 上次异步加载失败了：账本还是 READY，vLLM 却把已算数归零重来。',
                   q=Q_FAILCLOSED, ref=A(CON, 1424), has='request.num_computed_tokens == 0'),
                at((1436, 1443), '测试', '收场：清 LookupState、清账、置 BYPASS、答 `(0, False)`，让 vLLM 本地重算；不发 FREE。',
                   q=Q_TFAIL, ref=A(T_ADP, 1304), has='tracker.state = LMCacheMPRequestState.BYPASS_LMCACHE'),
                at((1450, 1459), None, '起点：先取整到各 group block 大小的公倍数（例子 16），再取整到 256。A、B：0；B′：320 → 256。'
                                     '缩水后 `need_full_lookup` 为真，起点固定 0。', has='lookup_start = ('),
                at((1463, 1472), None, '**每次被问都会走到这里**；适配器里已有这个请求的 LOOKUP 就什么也不做，返回 False。',
                   has='maybe_submit_lookup_request'),
                at((1475, 1476), '注释', '只在真发出时记下起点，空转的询问不改它。', q=Q_FREEZE, ref=A(CON, 1473),
                   has='apc_covered_tokens = lookup_start'),
                at((1478, 1480), None, '问一次结果；还没定 → `(None, True)`，把「下一步再问」交还给 vLLM。A、B 的第 1 次都停在这。',
                   has='return None, True'),
            ], cap='前半：BYPASS → 算起点 → 发（幂等）→ 问一次'),
            code(CON, [(1491, 1544), (1555, 1575)], notes=[
                at((1491, 1495), None, 'vLLM 命中，每次拿到结果时重算。B′：320 // 16 × 16 = 320。', has='tracker.num_vllm_hit_tokens = ('),
                at((1500, 1503), None, 'APC 缩水：vLLM 现在有的比发 LOOKUP 时记下的起点还少（设 B″：320 → 128，起点 256）。',
                   has='tracker.num_vllm_hit_tokens < tracker.apc_covered_tokens'),
                at((1511, 1512), None, '旧 LOOKUP 锁住的 `(256, 768)` 记进待放区间，下次 FREE 一起放。', has='pending_free_ranges.append'),
                at((1513, 1530), '注释', '清账，粘性地标「从 0 重查」，**同一步**重发 LOOKUP，答 `(None, True)`。',
                   q=Q_STICKY, ref=A(MD, 126), has='need_full_lookup = True'),
                at((1532, 1533), None, '没命中提前返回：LMCache 命中和「已下单存」都不写，保持 0。A 走这里。', has='if ret == 0'),
                at((1538, 1542), None, '「已下单存」= 命中取整块：B 是 768。不满一块的尾巴只记边界。', has='tracker.num_stored_tokens = full_chunk_tokens'),
                at(1544, None, '要取 = 命中 − vLLM 已算，不为负。B：768；B′：448。', has='need_to_load = max(0, ret - num_computed_tokens)'),
                at((1555, 1558), '注释', '整段 prompt 都命中时要少报 1 个：否则 vLLM 没有 token 可算，调度器断言失败。',
                   q=Q_FULLHIT, has='full-prompt-hit'),
                at((1559, 1570), None, '一般情况少报 1（设 C：768 全中 → 答 767，账上仍是 768，取回仍取整 48 个 block）。'
                                     'SWA 前缀回放时改成整块让出。', has='need_to_load - 1'),
                at(1575, None, '第二项：要取的数大于 0 才是 True。', has='return need_to_load, need_to_load > 0'),
            ], cap='后半：拿到结果 → 缩水检测 → 零命中早退 → 记账 → 算要取多少（中间省略了一段日志）'),
        ])


def _lookup_src():
    return node(
        'sched.lookup', '查询链的源码', 'LookupState：发、推进、定结果——谁在哪条线程上',
        '适配器怎么发 LOOKUP、调度器每问一次 LookupState 做哪几件事、QUERY 回 None 时何时重发。',
        lead='你在「调度侧连接器」图上方的「查找适配器」里面。三段：适配器的发和取、调度检查做的事、回复回调怎么推进。'
             '例子：A 回 0，B 回 768，都在两个调度步之间定下来。',
        src=A(MLS, 228, 353),
        blocks=[
            code(ADP, [(850, 886), (914, 922)], notes=[
                at((850, 855), None, '两个守卫：服务端不健康不发；已有这个请求的 LOOKUP 不重发。所以「问」可以每次都调它。',
                   has='if request_id in self._lookups'),
                at((857, 863), None, '终点取整到 256：A 1000 → 768，B 1200 → 1024。', has='aligned_end'),
                at((874, 875), '注释', '不等回执：那是调度线程上一整趟往返。', q=Q_NOWAIT, has='Do not wait'),
                at((876, 885), None, '建 LookupState、登记、`submit()` 向每台服务端发 LOOKUP。超时用 `mq_timeout`（默认 300 秒）。',
                   has='lookup.submit()'),
                at((914, 918), None, '没有 LookupState（已清掉）或服务端不健康 → 0：降级成「没命中」。', has='return 0'),
                at((920, 922), None, '推进一次，再读结果：没定是 None。', has='on_scheduler_check'),
            ], cap='适配器：maybe_submit_lookup_request / check_lookup_result'),
            code(MLS, [(228, 282)], notes=[
                at((241, 242), None, '结果定了就直接返回：从此不变（粘住）。A 的 0、B 的 768 第 2 次被问时都走这里。',
                   has='if self._result is not None'),
                at((244, 246), None, '把在途的 QUERY 标成「调度检查过了」——调度步在这个类里留下的唯一痕迹，回复回调据此决定能不能立即重发。',
                   has='scheduler_checked = True'),
                at((250, 252), '注释', '上次回 None 而延后的 QUERY 在这里重发：每次调度检查最多一条。', q=Q_ATMOST,
                   ref=A(MLS, 89), has='NEEDS_REQUERY'),
                at((255, 266), None, '只有关掉 `nonblocking_lookup_status`（默认开）才会在这里等 QUERY 的回复。',
                   has='future.wait'),
                at((274, 279), None, '过期：这台服务端标 TIMED_OUT，结果 = 0。', has='self._result = 0'),
            ], cap='on_scheduler_check：调度器每问一次做的事'),
            code(MLS, [(313, 353)], notes=[
                at((317, 325), None, 'LOOKUP 回执到了（哪怕失败）→ 立刻发 QUERY。通常在传输层接收线程的回调里跑；回调还没跑到时，调度检查会代为处理。',
                   has='_send_query_prefetch_status'),
                at((340, 346), None, 'QUERY 回整数 → RESOLVED；所有服务端都定了就取最小值写进结果。A：0；B：768。',
                   has='server.phase = _Phase.RESOLVED'),
                at((348, 353), None, '回 None（预取还在跑）：上次发出后调度器检查过 → 立刻重发；否则挂起，等下一次调度检查再发。',
                   has='NEEDS_REQUERY'),
            ], cap='回复回调：LOOKUP 回执 → 发 QUERY；QUERY 回复 → 定或再查'),
        ])


def _alloc_src():
    return node(
        'sched.alloc', '分配回调与取单', 'update_state_after_alloc + GetRetrieveMetadata：定岔路、放锁、出取单',
        '分配回调怎么记 block、定岔路、算 FREE 区间；下一步出单时怎么把「要取」变成 RETRIEVE 区间和 skip。',
        lead='你在「调度侧连接器」图的 ② 「分配后：定岔路」里面，外加 ③ 里出 RETRIEVE 单的那段。例子：B 岔向「要取」、'
             '不发 FREE、取 `[0, 768)`；B′ 发了个空 FREE、取 `[256, 768)` 跳过 64。',
        src=A(CON, 1626, 1692),
        blocks=[
            code(CON, [(1626, 1692)], notes=[
                at((1629, 1638), '注释', '只追加超出已记录数的 block：传进来的是全部。B 第 2 次传 75 个，只追加 27 个。',
                   q=Q_ALLBLK, ref=A(CON, 1619), has='group_blocks[existing:]'),
                at((1641, 1646), None, 'BYPASS 的请求：vLLM 已经同意本地重算，回 READY，之后照常存。', has='tracker.state = LMCacheMPRequestState.READY'),
                at(1648, None, '岔路条件：外部 token > 0 且 LMCache 命中多于 vLLM 已有。A：0 → 不成立；B：768 → 成立。',
                   has='condition = num_external_tokens > 0 and tracker.needs_retrieve()'),
                at((1649, 1656), None, '只在 PREFETCHING 时定岔路；第 2 次回调时已是 READY，跳过。', has='WAITING_FOR_LOAD'),
                at(1658, None, '到这时才丢掉 LookupState：在此之前 vLLM 再问，拿到的都是同一个结果。', has='cleanup_lookup_result'),
                at((1663, 1675), '注释', 'FREE 终点：没命中 0；不用取 = 全部命中；要取 = vLLM 命中（服务端向下取整）。'
                                     'B：0；B′：320 → 取整 256 → 空。', q=Q_FLOOR, has='free_end = tracker.num_vllm_hit_tokens'),
                at((1677, 1687), None, '有东西可放才发，带上待放区间（APC 缩水留下的），发完清空。', has='free_lookup_locks'),
            ], cap='update_state_after_alloc'),
            code(MD, [(610, 652)], notes=[
                at((610, 611), None, '只有 WAITING_FOR_LOAD 才出取单。', has='is_ready_for_retrieving'),
                at((618, 622), None, 'start = vLLM 命中向下取整到 256：B 0，B′ 256。LMCache 按整块存取，区间必须对齐。',
                   has='start_token_idx = ('),
                at(623, None, 'end = LMCache 命中：B、B′ 都是 768。整段命中「少报 1 个」不影响这里。', has='end_token_idx = tracker.num_lmcache_hit_tokens'),
                at((639, 644), '注释', 'skip = vLLM 命中 − start，永远不满一块：B′ 是 64。跳过 vLLM 已有的 block，避免跨流的数据竞争。',
                   q=Q_RACE, has='skip_first_n_tokens = tracker.num_vllm_hit_tokens - start_token_idx'),
            ], cap='GetRetrieveMetadata：取哪一段、跳过多少'),
            code(CON, [(1971, 1987)], notes=[
                at((1977, 1979), None, '遍历**全部**账本，不看本步调度了谁：B 没被调度计算，照样在这一步出取单。',
                   has='for request_tracker in self.request_trackers.values()'),
                at(1987, None, '出完单置 READY：同一个请求不会出第二张取单。', has='request_tracker.state = LMCacheMPRequestState.READY'),
            ], cap='_process_retrieve_requests：每步开头先出取单'),
        ])


def _store_src():
    return node(
        'sched.store', '存单的算术', 'GetStoreMetadata：两个 max、四个 min，下单即记账',
        '每步对每个被调度的请求算一遍「这步能存几块」：源码就是图上那条公式。',
        lead='你在「调度侧连接器」图的 ③ 「每步出单」里面，看存单怎么算。例子：A 一步出 `[0, 768)`；B 取完后出 `[768, 1024)`。',
        src=A(MD, 398, 457),
        blocks=[
            code(CON, [(1996, 2020)], notes=[
                at((1999, 2003), None, '先更新「已调度」和 vLLM 已算位置：A 本步 1000 → 1000 / 1000。', has='increase_num_scheduled_tokens'),
                at((2005, 2006), None, '不是生产者（只取不存的角色）就跳过存。', has='if not self._can_store'),
                at((2014, 2020), None, '出了单就加进元数据，并标 `store_scheduled`：结束时据此延迟回收。', has='store_scheduled = True'),
            ], cap='_process_new_requests：本步新调度的请求（已在跑的请求走同样的几行）'),
            code(MD, [(398, 457)], notes=[
                at((398, 402), '注释', '内层 max：两个命中都是前缀，取大的，不相加。外层 max：vLLM 真实已算位置当下界。'
                                     'A：max(1000 + 0, 1000) = 1000；B 第二次：max(432 + 768, 1200) = 1200。',
                   q=Q_LOWER, ref=A(MD, 396), has='computed_tokens = max('),
                at((407, 417), None, '已分配能装多少 token：多个 group 时取最少的那组。A：63 × 16 = 1008。', has='allocated_tokens = min('),
                at((418, 422), None, '三者取最小：总 token、已分配、已算。A：min(1000, 1008, 1000) = 1000。', has='min_available_tokens = min('),
                at((423, 426), '提交', '默认按 prompt 长度封顶：生成的 token 不存。A 解码时这里一直是 1000。',
                   q="Cap MP stores at the request's prompt length", ref='c:7ba466fe', has='num_prompt_tokens'),
                at((427, 428), None, '减去已下单存，按 256 数块。A：1000 − 0 → 3 块；A 解码时 232 → 0 块；B：1200 − 768 = 432 → 1 块。',
                   has='num_chunks = num_staging_tokens // lmcache_tokens_per_chunk'),
                at((430, 438), None, '区间从「已下单存」往后接：A `[0, 768)`，B `[768, 1024)`；按区间切出 block（A 48 个，B 16 个）。',
                   has='end_token_idx = start_token_idx + num_chunks * lmcache_tokens_per_chunk'),
                at(439, None, '工单里带全部 token（打包好的），服务端要从头算链式哈希。', has='token_bytes = tracker.packed_token_ids()'),
                at(456, None, '**在函数里**就加「已下单存」：下单即记账，不等做完；所以这个函数调两次会少出单。',
                   has='increase_num_stored_tokens'),
            ], cap='GetStoreMetadata：图上那条公式的原样'),
        ])


def _finish_src():
    return node(
        'sched.finish', '结束与延迟回收', 'request_finished → 名单 → worker 求交：block 怎么晚一步回收',
        '结束回调怎么决定延迟回收，名单怎么跟着下一步的元数据到 worker，worker 怎么只报名单里的请求。',
        lead='你在「调度侧连接器」图的 ④ 「结束：延迟回收」里面。四跳按顺序：结束回调登记 → 下一步交给元数据 → worker 并入 → '
             'worker 求交后上报。例子：A 在 sN 结束，sN+1 回收 63 个 block。',
        src=A(CON, 1791, 1824),
        blocks=[
            code(CON, [(1791, 1802), (1816, 1824)], notes=[
                at((1791, 1794), '注释', '第 1 跳的前提：发过 STORE 单才延迟。A：True。', q=Q_KEEP, has='delay_free = tracker is not None and tracker.store_scheduled'),
                at(1797, None, '读完再删账本：顺序不能反。', has='_cleanup_request_tracker'),
                at(1802, None, '清 LookupState：LOOKUP 结果还没被取走就取消的请求也被覆盖。但这里不发 FREE，那种请求的读锁要等 TTL。',
                   has='cleanup_lookup_result'),
                at((1816, 1818), None, '还有被钉住的 boundary-state block 时也要延迟（注释说的是存过不满一块、又被抢占的请求）。', has='_pinned_boundary_state_blocks'),
                at(1821, None, 'END_SESSION：服务端删 Session、刷新 LRU，不放读锁。', has='end_session'),
                at((1822, 1824), None, '第 1 跳：登记进调度侧的名单 `{A}`，答 True。', has='self._delay_free_req_ids.add'),
            ], cap='request_finished：先读后删，登记名单'),
            code(CON, [(1706, 1709)], notes=[
                at((1708, 1709), None, '第 2 跳：下一步出单时把名单整个交给元数据，自己清空。sN+1 的元数据里是 `{A}`。',
                   has='metadata.delay_free_req_ids = self._delay_free_req_ids'),
            ], cap='build_connector_meta 开头'),
            code(CON, [(1092, 1094)], notes=[
                at(1094, None, '第 3 跳：worker 在 `start_load_kv` 一开头就并入名单——在「本步没有取单就早退」之前，所以不会漏。',
                   has='self._delay_free_req_ids.update'),
            ], cap='worker 侧 start_load_kv 开头'),
            code(CON, [(1252, 1256)], notes=[
                at((1252, 1255), '注释', '第 4 跳：「STORE 已做完」∩ 名单才报，报完扣掉。A 早已存完 → 报 `{A}`，vLLM 才回收。',
                   q=Q_ASSERT, ref=A(CON, 1253), has='& self._delay_free_req_ids'),
            ], cap='worker 侧 get_finished：求交后上报'),
        ])
