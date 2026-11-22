# 顶层「KV 缓存 + 前缀树」框（槽位 kv_cache）的下一层。
# 事实全部来自 research/manuscript-kv_cache.md（HEAD = c569c43446）；例子沿用顶层推演：
# prompt 20 token，树里已有 5 个（节点 A，槽 101..105），新分 15 个（2001..2015），行 7，decode 槽 2016..2019。
from cv import *

SBT = 'python/sglang/srt/managers/schedule_batch.py'
SP = 'python/sglang/srt/managers/schedule_policy.py'
SCH = 'python/sglang/srt/managers/scheduler.py'
BRP = 'python/sglang/srt/managers/scheduler_components/batch_result_processor.py'
RRT = 'python/sglang/srt/managers/scheduler_components/request_retirer.py'
MC = 'python/sglang/srt/mem_cache'
MEM = MC + '/common.py'
ALC = MC + '/allocation.py'
SKS = MC + '/static_kv_storage.py'
BASE = MC + '/allocator/base.py'
TOK = MC + '/allocator/token.py'
MP = MC + '/memory_pool.py'
PB = MC + '/prefill_budget.py'
BPC = MC + '/base_prefix_cache.py'
RC = MC + '/radix_cache.py'
URC = MC + '/unified_radix_cache.py'
UTC = MC + '/unified_cache/unified_tree_core.py'
FULL = MC + '/unified_cache/components/full.py'
CRD = MC + '/unified_cache/components/README.md'
MRD = MC + '/README.md'
EVP = MC + '/evict_policy.py'
REG = MC + '/registry.py'
EVD = 'docs/docs/advanced_features/radix_eviction_policy.mdx'
MEMF = 'python/sglang/srt/arg_groups/fields/memory.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数）
def tree_g(rows, on=None):
    return g_list(rows, on=on)


def ledger_g(row, prot, alloc, lock):
    return g_kv([('行号', row), ('protected', prot), ('allocated', alloc), ('lock', lock)])


def alloc_g(free, last):
    return g_kv([('空闲', free), ('刚变动', last)])


def row_g(cells, left='行 7', right=None):
    return g_cells(cells, left=left, right=right)


def _scene():
    parts = [
        part('sched', '调度器', [16, 40, 160, 130], m=[8, 8, 206, 116], kind='ext', code='Scheduler',
             glyph=g_kv([('prompt', '20 token'), ('限长', '20−1 = 19')]),
             say='一条请求来敲门 6 次', why='全在一个线程里按序发生', trap='它不在这个框里',
             card=card(
                 '顶层的调度循环。一条请求从进门到结束，会来这个框打 **6 次**交道：① 匹配 → ② 入批加锁 → ③ prefill 分槽 → ④ prefill 一完就写树 → ⑤ decode 每步 1 槽 → ⑥ 结束时再写树并释放。',
                 ['① 排队时 `init_next_round_input`。' + R(SP, 326),
                  '② 入批 `add_one_req` 临时锁 + 预算，通过后正式锁。' + R(SP, 2054) + ' ' + R(SP, 2361),
                  '③ `prepare_for_extend` → `alloc_for_extend`。' + R(SBT, 2464) + ' ' + R(ALC, 363),
                  '④ prefill 最后一块做完、请求没结束：`checkpoint_kv_cache`。' + R(BRP, 545, 548),
                  '⑤ `prepare_for_decode` → `alloc_for_decode`。' + R(SBT, 3346),
                  '⑥ `release_kv_cache`。' + R(MEM, 385, 433)],
                 [note('推断', '这 6 次都由调度器在自己的循环里依次调用；所以这里说的「锁」不是线程锁，而是一个「别淘汰我」的引用计数 `lock_ref`。')],
                 ['槽不够到淘汰也救不了时，是调度器在 decode 前预检并把请求退回队列（retract），那属于「调度循环」那个框。' + R(SBT, 2996, 3016),
                  '被退回的请求**不写树**，算好的 KV 直接丢掉；代码里留着 TODO。' + R(RRT, 132, 134)])),

        part('match', '前缀匹配', [300, 30, 220, 170], m=[8, 190, 206, 170], kind='gate', code='match_prefix',
             glyph=g_checks([('限长 20−1 = 19', None), ('沿边 A 比对', None), ('第 6 个 token', None)]),
             say='从根往下走，数相同的前缀', why='只记停在哪，不拷槽号', trap='只是读，也会把节点劈开',
             card=card(
                 '拿请求的 token 序列在树上从根往下走，数出和树里最长的共同前缀。结果只有两样：**命中几个**（`prefix_len`）、**停在哪个节点**（`last_node`）。',
                 ['一个 while 循环：用当前位置的 token 当键，去当前节点的 `children` 里找孩子。' + R(UTC, 1061),
                  '找到后 `match_at` 数这条边和请求有几个 token 相同——不是逐个比，而是倍增窗口 + 二分。' + R(UTC, 1068),
                  '整条边吃完：收下这条边的槽号，往下走；只吃了半条：**当场把节点劈成两半**，停在前半。' + R(UTC, 1070, 1083),
                  '停下后，沿路所有祖先的 `last_access_time` 刷新：LRU 的「最近用过」在匹配时就记上了。' + R(UTC, 1182, 1186),
                  '限长 = 输入长度 − 1（本例 19），再套 logprob 等上限。' + R(SBT, 1238, 1257)],
                 [note('注释', '命中最多比输入少 1，注释说是为了 logprob 计算；更早的旧注释说的是「至少要 1 个 token 才能算 logits」。',
                       q='the matched length is at most 1 less than the input length to enable logprob computation', ref=A(SBT, 1239)),
                  note('提交', '这个 −1 最早是一次修 bug 加进来的；当年新增的注释写着 FIXME：为绕开 logprob 计算的 bug，以后可以放宽。',
                       q='[Fix] Fix select by ensuring each request has at least one token (#1318)', ref='c:1e495e0847'),
                  note('文档', '只记位置、不拷槽号：槽号到分配时才沿 `last_node` 从树上读出来，中间这段时间靠锁保证它们不被淘汰。',
                       q='valid from match until allocation writes them into the row.', ref=A(BPC, 728)),
                  note('推断', '为什么匹配时就劈：锁要锚在 `last_node` 上，命中边界必须正好是一个节点。')],
                 ['一模一样的请求第二次来，命中 **19** 不是 20，要重算 1 个 token。',
                  '带 `positional_embed_overrides` 的请求根本不匹配、也不写树：树的键只有 token id，KV 却还取决于 embeds。' + R(SBT, 1196, 1200),
                  '排队阶段还有一棵不带内存池的「假树」，只用来做批内去重、决定谁先跑，不动真树。' + R(SP, 256)]),
             to='kv_cache.match'),

        part('tree', '前缀树', [640, 30, 270, 170], m=[8, 670, 206, 180], kind='store', code='tree_cache',
             glyph=tree_g(['root', '└ A  5 tok · 槽101–105']),
             say='token 前缀 → 槽号', why='一个节点 = 一段边 + 同样多的槽号', trap='存的是槽号，不是 K/V 本身',
             card=card(
                 '默认是 `UnifiedRadixCache`：一棵基数树，记着「哪段 token 前缀的 KV 已经算好、放在哪些槽里」。管匹配、插入、加锁、淘汰四件事。',
                 ['一个节点 = 树上的一段**边**：`key` 是 N 个 token，`value` 是这 N 个 token 的 N 个槽号，一一对应；另有 `last_access_time`、`hit_count`；锁计数 `lock_ref` 记在各组件的数据里。' + R(UTC, 118, 150),
                  '所以「命中 5 个」= 走过的节点 `value` 长度之和。',
                  '默认就开着；`--disable-radix-cache` 才走另一支。' + R(REG, 153) + ' ' + R(REG, 93),
                  'LoRA 名等 `extra_key`、`cache_salt` 会拼进孩子的字典键：不同命名空间互相命中不了。' + R(RC, 227, 249)],
                 [note('文档', '分层里它只回答一个问题：留谁、淘汰谁。发号和存字节是别人的事。',
                       q='what to keep and what to evict', ref=A(MRD, 36)),
                  note('推断', '用前缀树：开头相同的请求天然共用同一串祖先节点，匹配就是从根往下走一遍，不用比对每条历史请求。')],
                 ['这棵树同时挂着 Full / SWA / Mamba 三种组件；普通全注意力模型只有 Full。读源码看到 `window`、`mamba`、`hicache` 字样可以跳过。',
                  '树在 prefill 一做完就写一次，不只是结束时（见「写树」那条线）。']),
             to='kv_cache.ckpt'),

        part('evict', '淘汰', [1040, 30, 164, 160], m=[8, 1390, 206, 150], kind='bg', code='evict',
             glyph=g_kv([('触发', '空闲 < 要的'), ('候选叶子', '—')]),
             say='不够时摘最旧的叶子', why='只摘叶子，锁住的不动', trap='没有后台定时淘汰',
             card=card(
                 '分配器不够时被叫来：从树上「可淘汰叶子」里挑最久没用的摘掉，把它的槽还给分配器，摘到够为止。',
                 ['候选叶子 = 不是根、所有组件 `lock_ref` 都为 0、没有还带着 KV 的孩子。' + R(UTC, 2659, 2675),
                  '把候选放进一个堆；默认 `lru` 的键就是 `last_access_time`，最小（最久没用）的先出。' + R(FULL, 211, 219) + ' ' + R(EVP, 17, 19),
                  '每摘掉一片叶子，它的父节点若也成了叶子，就压回堆里。' + R(FULL, 230, 241),
                  '淘汰量 = 缺口，不多摘。' + R(URC, 832, 838)],
                 [note('文档', 'Full 组件只从「可淘汰叶子」里挑，按访问时间。',
                       q='Full: drives eviction from `evictable_device_leaves` using `last_access_time`', ref=A(CRD, 168)),
                  note('推断', '为什么只摘叶子：中间节点的 KV 是它下面所有后代的前提；先丢中间，后代就成了再也匹配不到的悬空数据。'),
                  note('文档', '真正的硬保护是锁：lock_ref > 0 的节点不会被淘汰。（官方 radix_eviction_policy 文档还说策略只打分、挡不住淘汰。）',
                       q='`lock_ref > 0` protects the node from eviction.', ref=A(CRD, 71))],
                 ['唯一的触发点是「分配时不够」；平时缓存一直攒着。',
                  'Full 组件不是一条 LRU 链表，而是由叶子集合驱动。' + R(CRD, 67),
                  '`_evict_device_walk` 里大段 `write_back` / `pending` 只在 HiCache 开启时走，可以跳过。' + R(URC, 1275)]),
             to='kv_cache.evict'),

        part('ledger', '请求的账本', [300, 310, 220, 170], m=[8, 430, 206, 170], kind='store', code='req.kv',
             glyph=ledger_g('—', '0', '0', '—'),
             say='行里哪段归树、哪段归我', why='释放时只还归自己的那段', trap='只记长度，不记槽号',
             card=card(
                 '每条请求身上的一本账（`ReqKvInfo`）：占着请求行表的哪一行，行里哪一段槽归树、哪一段归自己。读者问「一条请求的 KV 归谁」，看这里。',
                 ['`req_pool_idx`：在请求行表里的行号（有行 = 持有 KV）。' + R(SBT, 278),
                  '`cache_protected_len`：行的 `[0, 这里)` 归树——命中来的，或自己插进树的。' + R(SBT, 282),
                  '`kv_allocated_len` / `kv_committed_len`：分到哪 / KV 已写好到哪。' + R(SBT, 286, 287),
                  '`cache_inserted_len`：自己插过树的长度，之后只对更长的部分计命中。' + R(SBT, 283, 285),
                  '只有几个小方法写它：`set_row_len` `allocate_decode` `protect_prefix` `record_insert`。' + R(SBT, 361, 412)],
                 [note('注释', '一句话分账：请求自己的 KV 是 `[protected, allocated)`；前面那段归树，释放时不还。',
                       q="The request's own KV is [cache_protected_len, kv_allocated_len).", ref=A(SBT, 280)),
                  note('注释', '释放时只还自己那段。', q="The protected prefix is not this req's to free.", ref=A(MEM, 420))],
                 ['`last_node` 和 `lock` 挂在请求本身上，不在 `req.kv` 里；图上为了好看画在一起。' + R(SBT, 277),
                  '`protected` 会往后挪：匹配后是 5，每写一次树就挪到新长度（20、24）。']),
             to='kv_cache.ledger'),

        part('row', '请求行表', [640, 310, 270, 150], m=[8, 940, 206, 150], kind='store', code='req_to_token',
             glyph=row_g([cell(), cell(), cell()], left='行 —'),
             say='第 j 列 = 第 j 个 token 的槽号', why='和树指向同一批槽号', trap='命中那段的槽是树的',
             card=card(
                 '一张二维整数表：第 r 行是一条请求，第 j 列是它第 j 个 token 的 KV 槽号。和树存的是**同一批槽号**，只是一个按位置索引、一个按前缀索引。',
                 ['就是一个 `torch.zeros((size+1, max_context_len), int32)`。' + R(MP, 421, 424),
                  '要行从空闲列表**尾部**切，还行追加回尾部。' + R(MP, 463) + ' ' + R(MP, 476),
                  '行不够直接抛错，报错建议调小 `--max-running-requests`。' + R(ALC, 315, 337)],
                 [note('注释', '第 0 行是垫行，不分给请求：CUDA graph 补齐出来的假请求默认读写这里，无害。',
                       q='+1 padding row at index 0: cuda-graph padded batches default', ref=A(MP, 416)),
                  note('注释', '分行失败说明准入预算算错了，要报出来而不是遮掩。',
                       q='and should surface rather than be masked.', ref=A(ALC, 325)),
                  note('推断', '写树之后行里的槽改指树里那份：两边从此指向同一批槽，树负责留，行负责用。')],
                 ['行里 `[0, protected)` 的槽归树，请求只是租用；行还回去时这段不还。',
                  '槽号 0 和行号 0 都是垫位。']),
             to='kv_cache.alloc'),

        part('alloc', '空闲槽表', [1040, 310, 164, 150], m=[8, 1180, 206, 140], kind='store', code='allocator',
             glyph=alloc_g('N', '—'),
             say='只发号：取前 n 个', why='先腾后取，没满不淘汰', trap='只管号，不碰显存字节',
             card=card(
                 '管「哪些槽号是空的」。`alloc(n)` 从空闲张量头上切走 n 个；`free` 拼回去。',
                 ['空闲表从 1 开始，槽 0 是垫位。' + R(TOK, 42, 46),
                  '`alloc(n)`：不够返回 `None`，够就切走前 n 个。' + R(TOK, 54, 63),
                  '取槽的固定顺序：先 `make_room(n)`，再 `alloc(n)`。' + R(SKS, 277, 278),
                  '`make_room` 只在空闲 < n 时才让树淘汰。' + R(BASE, 259, 261),
                  '淘汰完还不够：抛 `RuntimeError`（Out of memory）。' + R(ALC, 204, 213),
                  '`page_size > 1` 时走按页分配的版本；本页按 1 讲。'],
                 [note('文档', '分层里 allocator 只回答「哪些槽空着」。', q='which slots are free', ref=A(MRD, 32)),
                  note('推断', '没满就不淘汰：缓存一直攒到池子满了才按需挤出去，命中机会最大。依据只是那行判断，没找到说明动机的注释。')],
                 ['树里的槽不算「空闲」，但算「可淘汰」：入批预算 = 空闲 + 可淘汰。' + R(PB, 144, 153),
                  '本例请求结束后一个槽都没还回来：24 个槽全留在树里，等以后被淘汰。']),
             to='kv_cache.alloc'),
    ]

    wires = [
        wire('w_ask', 'sched', 'match', '来匹配', n='1', why='传整条数组加上限', trap='排队时还有棵假树',
             card=card('调度器排队时对每条等待请求调 `init_next_round_input`，带着 token 序列和上限来匹配。',
                       ['入口：' + R(SP, 326) + '；`init_next_round_input` → `match_kv_cache`。' + R(SBT, 1191, 1215),
                        '传进去的是请求的整条 token 序列 `full_untruncated_fill_ids` 和上限 19。' + R(SBT, 1193, 1194)],
                       [note('注释', '传整条数组加一个上限，不切片：每拼一次 prefill 批都省一次 O(上下文长度) 的拷贝。',
                             q='Pass the full array with a raw-token cap (limit) instead of slicing,', ref=A(SBT, 1191))],
                       '缓存感知的排队策略还会用一棵不带内存池的假树做「批内去重」，只影响谁先跑，不动真树。' + R(SP, 585, 591))),
        wire('w_walk', 'match', 'tree', '沿边比对', mfs='r', mts='r', mlp=[277, 540], why='匹配时就刷新时间', trap='读也会劈节点',
             card=card('匹配在树上从根往下走：每到一个节点用下一个 token 找孩子，再比这条边。',
                       ['`match_at`：倍增窗口找第一个不同的 token，再二分。' + R(UTC, 1068),
                        '边只吃了一半就 `_split_node`，前半成为新的中间节点。' + R(UTC, 1070, 1075)],
                       [note('注释', '长的共同前缀不逐个 token 比：一次比一个翻倍的窗口，再在出错的窗口里二分。',
                             q='Exponential search for the first diverging token: gallop in doubling', ref=A(RC, 194))],
                       '只是读，树的形状也可能变：第二条同样的请求会把 B 劈成 B1 + B2（推演第 9 步）。')),
        wire('w_mark', 'match', 'ledger', '记下 5 · A', n='1', why='槽号以后再读', trap='没有槽号数组',
             card=card('匹配结果记到请求上：命中几个、停在哪个节点；账本把 `[0, 5)` 记成归树。',
                       ['`req.prefix_len = device_prefix_len`，`req.last_node = ...`。' + R(MEM, 209, 218),
                        '`req.kv.protect_prefix(prefix_len)`。' + R(SBT, 1214, 1215)],
                       [note('文档', '槽号要到分配时才从树上读；这之间必须锁住这条路径，否则可能被淘汰、读到别人的槽。',
                             q='valid from match until allocation writes them into the row.', ref=A(BPC, 728))],
                       '这一步一个槽号都没有拷。')),
        wire('w_lock', 'ledger', 'tree', '锁到根', n='2', fs='r', ts='l', mfs='b', mts='t',
             why='被锁的不算可腾空间', trap='锁 ≠ 线程锁，是引用计数',
             card=card('入批时把命中的那条路径锁住：从 `last_node` 往上到根，每个节点 `lock_ref += 1`。被锁的节点不会被淘汰。',
                       ['先**临时**锁住、在锁里算预算：可用 = 空闲槽 + 树里可淘汰的槽。' + R(SP, 2052, 2054) + ' ' + R(PB, 144, 153),
                        '预算通过后**正式**锁：`req.lock = tree_cache.lock(req.last_node)`，返回一张回执，解锁必须拿同一张。' + R(SP, 2361) + ' ' + R(URC, 1507, 1519),
                        '节点第一次被锁（0→1）时，它的 token 数从「可淘汰」挪到「受保护」，并从候选叶子集合里摘掉。' + R(FULL, 326, 342)],
                       [note('注释', '临时锁让这段前缀不被算进「能腾出来的空间」。',
                             q='The temporary pin excludes this prefix from the evictable budget.', ref=A(SP, 2052)),
                        note('文档', 'Full 组件是路径锁：从节点走到根，每个祖先都 +1。',
                             q='walks from node to root, `lock_ref += 1` on every ancestor.', ref=A(CRD, 203)),
                        note('推断', '不先锁就算预算，命中的 5 个会被当成可腾出的空间，预算虚高，真分槽时腾不出来。')],
                       '锁在叶子上，整条祖先链都被锁；所以被锁的永远轮不到淘汰。'),
             to='kv_cache.evict'),
        wire('w_hit', 'tree', 'row', '命中槽号', n='3', lp=[705, 255], why='分配时才读', trap='后续块读自己行',
             card=card('分槽时才把命中那段的槽号从树上读出来（沿 `last_node` 往上收各节点 `value`、反转拼起来），写进行的 `[0, prefix_len)`。',
                       ['`_prefix_kv_indices`：已有行的读自己行，新请求读树。' + R(ALC, 352, 360),
                        '`prefix_device_indices` 沿路径收槽号。' + R(URC, 5070, 5084)],
                       [note('注释', '两种来源：已经有行的请求（分块的后续块）前缀就在自己行里；新请求从树上读。',
                             q='already has its prefix there; a new one reads its match off the tree.', ref=A(ALC, 354))],
                       '本例读出 101..105；这 5 个槽仍然归树，行只是借用。')),
        wire('w_new', 'alloc', 'row', '新槽 15 / 每步1', ml='新槽15/每步1', n='3/5', why='只给没命中的要槽', trap='decode 时树不动',
             card=card('只给没命中的部分要新槽，写进行的后半段：prefill 要 15 个，decode 每步再要 1 个。',
                       ['prefill：`alloc_token_slots(tree_cache, extend_num_tokens)`。' + R(ALC, 432),
                        '写行：`[0:prefix_len]` = 命中槽号，`[prefix_len:seq_len]` = 新槽号。' + R(ALC, 524),
                        'decode：整批一次要 bs 个；新槽写在第 `seq_len` 列，然后 seq_len +1。' + R(ALC, 697) + ' ' + R(ALC, 734, 738)],
                       [note('注释', 'decode 的槽在分配时就记成已提交，这一步前向还没跑。',
                             q='A decode step holds num_tokens more slots and commits them at', ref=A(SBT, 367)),
                        note('推断', '命中 5 个就少要 5 个槽、少算 5 个位置：省下的算力和显存都在这里体现。')],
                       'decode 期间树既不匹配也不插入；它只在槽不够时通过淘汰间接参与。')),
        wire('w_ckpt', 'row', 'tree', '写树 · 改指', ml='写树·改指', n='4/6', lp=[850, 255], mfs='r', mts='r', mlp=[272, 895],
             why='prefill 完就写', trap='结束前就写过',
             card=card('把行里这些 token 的槽号插进树：已有的段去重，新的段挂成叶子；再把行改指树里那份，锁移到新叶子，`protected` 往后挪。',
                       ['三个时机：prefill 最后一块做完（请求还在跑）' + R(BRP, 545, 548) + '；分块 prefill 的块与块之间' + R(SCH, 4224, 4228),
                        '请求结束时再写一次。' + R(MEM, 419),
                        '从行里读槽号 → **复制**一份 → `insert`。' + R(URC, 1669, 1671) + ' ' + R(URC, 1725),
                        '插完把行改指树里那份，先解旧锁、再锁新叶子。' + R(URC, 1800, 1812),
                        '插入时撞上树里已有的段：只有请求**自己新算**的那部分算重复，还给分配器。' + R(UTC, 1517) + ' ' + R(UTC, 1540)],
                       [note('文档', '官方说明：运行中每个检查点写一次，结束时再写一次。',
                             q='Called at every checkpoint of a running request and once more when', ref=A(BPC, 579)),
                        note('推断', '为什么 prefill 一完就写：同开头的别的请求在这条还没结束时就能命中它的 prompt。没找到直接说明动机的注释或提交。'),
                        note('注释', '在 prompt 边界单独切一个节点：淘汰时能只丢输出、留下可复用的 prompt。',
                             q='Split the leaf at the prompt boundary so eviction can drop the output', ref=A(URC, 1762))],
                       ['顶层那张卡说「缓存是结束时顺手插的」——不完整：prefill 一完就插了一次，结束时只是补上输出那一段。',
                        '那段文档说写树时不释放任何槽，并不严格：重复的槽会被还掉。' + R(BPC, 581)]),
             to='kv_cache.ckpt'),
        wire('w_free', 'row', 'alloc', '结束：还多余', ml='还多余', n='6', cls='dash', mfs='r', mts='r', mlp=[277, 1120], why='先插树，再还槽',
             trap='本例不还槽',
             card=card('请求结束：先把 prompt + 输出再写一次树，然后只还「归自己、树又不要」的那段槽，解锁，最后把行还回去。',
                       ['`owned_kv_len = min(committed, prompt + 输出含停止 token)`：树该存多长。' + R(SBT, 990, 1002),
                        '顺序：写树 → 还 `[protected, owned)` → 解锁 → 还 `[owned, allocated)` → 还行。' + R(MEM, 409, 432)],
                       [note('注释', '归树的那段不是这条请求能还的。', q="The protected prefix is not this req's to free.", ref=A(MEM, 420)),
                        note('推断', '先插树再还槽：插树要从行里读槽号，先还了就读不到；插树本身还可能还掉一部分重复槽。代码顺序如此，没有注释明说。')],
                       ['写完树 `protected = owned`，所以第二步通常是空区间：**全部留在树里**。真正还回去的只有页尾不满一页的槽和「分了但不该存的」。',
                        '顶层推演里 overlap 多发了一步：按公式那一步的槽也在 `owned` 之内、留在树里，不是「一并回收」（推断，没实跑）。']),
             to='kv_cache.release'),
        wire('w_short', 'alloc', 'evict', '不够才腾', cls='dash', why='缺多少腾多少',
             card=card('分配器取槽前先 `make_room(n)`：空闲够就什么都不做；不够才叫树淘汰，量是缺口。',
                       ['`make_room` = `evict_from_tree_cache`。' + R(MEM, 247),
                        '条件：`available_size() < num_tokens`。' + R(BASE, 259, 261)],
                       [note('推断', '懒淘汰：命中率最高的做法是能不丢就不丢。依据是那行判断，没找到说明动机的注释。')],
                       '没有任何后台线程定时清理缓存。')),
        wire('w_drop', 'evict', 'tree', '摘最旧的叶子', ml='摘最旧叶子', cls='dash', mfs='r', mts='r', mlp=[277, 1250], why='锁住的不在候选里',
             card=card('从堆顶弹出最久没用的叶子：释放它的槽、从父节点摘掉；父节点若成了叶子，压回堆。',
                       ['弹堆顶：' + R(FULL, 241) + '；父节点回压：' + R(FULL, 235),
                        '没开 HiCache 时删叶子：释放槽号、从父节点摘除。' + R(UTC, 2195)],
                       [note('注释', '候选只是：KV 在、下面没有还带 KV 的孩子、没被锁、不是根。',
                             q='D-leaf: Full device value present, no child with Full KV on device,', ref=A(UTC, 2660))],
                       '摘下来的槽回到空闲槽表，图上这条回程没单独画。'),
             to='kv_cache.evict'),
    ]

    # ---------------------------------------------------------------- 推演
    steps = [
        beat('匹配：命中 5 个，停在 A',
             '限长 = 20 − 1 = **19**。从根出发，第 1 个 token 找到孩子 A；`match_at` 比出 A 的 5 个 token 全相同 → 往下走；第 6 个 token 在 A 的孩子里没有 → 停。结果 `prefix_len=5`、`last_node=A`，A 和 root 的访问时间刷新，账本 `protected=5`。**一个槽号都没拷**。',
             note('文档', '槽号到分配时才从树上读，所以中间这段要锁住。',
                  q='valid from match until allocation writes them into the row.', ref=A(BPC, 728)),
             focus=['match', 'w_ask', 'w_walk', 'tree', 'w_mark', 'ledger'],
             state={'match': g_checks([('限长 20−1 = 19', 'ok'), ('边 A：5/5 相同', 'ok'), ('第 6 个：A 下无孩子', 'no')]),
                    'tree': tree_g(['root', '└ A  5 tok · 槽101–105', '   停在 A，刷新访问时间'], on=1),
                    'ledger': ledger_g('—', '5', '0', '—')}),
        beat('入批：临时锁 → 算预算 → 正式锁',
             '`add_one_req` 先临时锁 A，在锁里算：可用 = 空闲 N + 树里可淘汰的槽。A 被锁，它的 5 个 token 从「可淘汰」挪到「受保护」，不算进可用。要 15 个，够 → 正式 `req.lock = lock(A)`：从 A 往上到根，每个节点 `lock_ref` +1。',
             note('注释', '临时锁让这段前缀不被算成能腾出来的空间。',
                  q='The temporary pin excludes this prefix from the evictable budget.', ref=A(SP, 2052)),
             focus=['w_lock', 'tree', 'ledger'],
             state={'tree': tree_g(['root  锁 1', '└ A  5 tok  锁 1'], on=1),
                    'ledger': ledger_g('—', '5', '0', 'A')}),
        beat('分槽：命中的读树，没命中的要 15 个',
             '`alloc_for_extend`：命中的 5 个槽这时才沿 A 从树上读出（101..105）；要一行（设 **行 7**）；再向分配器要 **15** 个：`make_room(15)`——空闲够，**不淘汰**——`alloc(15)` 切走 2001..2015。行 7 = `[101..105, 2001..2015]`，`allocated = 20`。',
             note('推断', '先腾后取、没满不淘汰：只有空闲不够时才动树里的缓存。'),
             focus=['row', 'w_hit', 'w_new', 'alloc', 'ledger'],
             state={'row': row_g([cell('101–105', '命中', 'pick'), cell('2001–2015', '新分', 'new')], right='20 列'),
                    'alloc': alloc_g('N − 15', '−2001…2015'),
                    'ledger': ledger_g('7', '5', '20', 'A')}),
        beat('prefill 一做完就写树（不等结束）',
             'GPU 算完位置 5..19、采出 X1，请求还在跑。`checkpoint` 把 prompt 20 个 token 插树：走过 A，A 那 5 个本来就是树的、没有重复；剩下 15 个在 A 下挂成叶子 **B**（槽 2001..2015，复制一份）。行 7 改指树里那份（值相同）；锁从 A 移到 B；`protected` 5 → **20**。',
             note('文档', '运行中每个检查点写一次，结束时再写一次。',
                  q='Called at every checkpoint of a running request and once more when', ref=A(BPC, 579)),
             focus=['w_ckpt', 'tree', 'row', 'ledger'],
             state={'tree': tree_g(['root → A(5)', '└ B  15 tok · 槽2001–2015', '锁：A → B'], on=1),
                    'row': row_g([cell('101–105', '树 A', 'pick'), cell('2001–2015', '树 B', 'pick')], right='全归树'),
                    'ledger': ledger_g('7', '20', '20', 'B')}),
        beat('decode 第 1 步：1 个槽，树不动',
             '整批一次要 bs 个槽，这条请求分到 **2016**，写进行 7 的**第 20 列**（还没 +1 的 seq_len），然后 seq_len 20 → 21。账本在这一步前向跑之前就记 `allocated = committed = 21`。树完全不参与。',
             note('注释', 'decode 的槽在分配时就记成已提交。',
                  q='A decode step holds num_tokens more slots and commits them at', ref=A(SBT, 367)),
             focus=['w_new', 'row', 'alloc'],
             state={'row': row_g([cell('101–105', '树', 'pick'), cell('2001–2015', '树', 'pick'), cell('2016', '第20列', 'new')]),
                    'alloc': alloc_g('N − 16', '−2016'),
                    'ledger': ledger_g('7', '20', '21', 'B')}),
        beat('decode 第 2..4 步：X4 那步采出 EOS',
             '每步再 1 个：第 21..23 列 = 2017..2019，`committed = 24`（prompt 20 + X1..X4 的 KV）。第 4 步以 X4 为输入采出 X5 = **EOS**；EOS 自己的 KV 这时还没算过。',
             note('推断', '新 token 的槽只记在行里和账上，树等结束时再一起接手。'),
             focus=['row', 'w_new', 'alloc'],
             state={'row': row_g([cell('101–105', '树', 'pick'), cell('2001–2015', '树', 'pick'), cell('2016–2019', 'decode', 'new')]),
                    'alloc': alloc_g('N − 19', '−2017…2019'),
                    'ledger': ledger_g('7', '20', '24', 'B')}),
        beat('结束：再写一次树，补上输出',
             '`release_kv_cache`：`owned = min(committed 24, 20 + 5) = 24`（输出 5 个含 EOS）。先补齐 token 序列，再 `checkpoint(up_to=24)`：走过 A、B，在 B 下挂叶子 **C**（X1..X4，槽 2016..2019）。行改指，锁 B → C，`protected = 24`。',
             note('注释', '插树用的 token 序列落后于 output_ids，先补齐，否则少插最后几个输出。',
                  q='The fill-id array lags output_ids until the next prepare_for_decode.', ref=A(MEM, 417)),
             focus=['w_ckpt', 'tree', 'ledger', 'row'],
             state={'tree': tree_g(['root → A(5) → B(15)', '└ C  4 tok · 槽2016–2019', '锁：B → C'], on=1),
                    'row': row_g([cell('101–105', '树 A', 'pick'), cell('2001–2015', '树 B', 'pick'), cell('2016–2019', '树 C', 'pick')]),
                    'ledger': ledger_g('7', '24', '24', 'C')}),
        beat('还槽：只还归自己的那段——这次是空的',
             '`free_kv_row` 还 `[protected 24, owned 24)`：**空区间，一个槽都不还**。解锁 C（C 到根各 −1）；`[owned, allocated)` 也是空的；最后行 7 还回空闲行表。24 个槽全留在树里，C 成了**可淘汰叶子**。',
             note('推断', '顶层推演里 overlap 多发了一步：那一步的槽也在 owned 之内（committed 25、owned 25），C 就是 5 个，照样一个不还。没实跑。'),
             focus=['w_free', 'row', 'alloc', 'tree'],
             state={'tree': tree_g(['root → A → B → C', 'C 已解锁 → 可淘汰叶子'], on=1),
                    'row': row_g([cell('—', '', 'ghost'), cell('—', '', 'ghost'), cell('—', '', 'ghost')], left='行 7 已还'),
                    'alloc': alloc_g('N − 19', '还回 0 个槽'),
                    'ledger': ledger_g('已还', '24', '0', '—'),
                    'evict': g_kv([('触发', '空闲 < 要的'), ('候选叶子', '{C}')])}),
        beat('同一句再来一次：命中 19，B 被劈开',
             '限长 20 − 1 = 19。走完 A（5）；进 B 只吃到 14 个就到限长 → **当场把 B 劈成 B1（14，槽 2001..2014）+ B2（1，槽 2015）**，停在 B1。命中 5 + 14 = **19**，只算最后 1 个 token。锁加在 B1 到根；B2、C 没人锁。',
             note('推断', '匹配时就劈：锁要锚在 last_node 上，命中边界必须正好是一个节点。'),
             focus=['match', 'w_walk', 'tree', 'sched'],
             state={'sched': g_kv([('prompt', '同一句 20'), ('限长', '19')]),
                    'match': g_checks([('限长 19', 'ok'), ('边 A：5/5', 'ok'), ('边 B：14/15 → 劈', 'no')]),
                    'tree': tree_g(['A(5) → B1(14)  锁', '└ B2(1) → C(4)', 'B 劈成 B1 + B2'], on=[0, 2])}),
        beat('显存满了：摘最久没用的叶子',
             '某次分配时空闲 < 要的 → 淘汰。候选叶子只有 **C**（B2 下面还挂着带 KV 的 C，不算叶子；A、B1 被锁）。弹出 C，槽 2016..2019 还回空闲槽表；B2 变成叶子压回堆，还不够就轮到它。A、B1 不会被动。',
             note('推断', '只摘叶子、摘完把父节点压回堆：从枝头往根推，树里每个节点的祖先链始终完整。'),
             focus=['evict', 'w_short', 'w_drop', 'tree', 'alloc'],
             state={'evict': g_kv([('候选叶子', '{C}'), ('弹出', 'C，压入 B2')]),
                    'tree': tree_g(['A(5) → B1(14)  锁', '└ B2(1)  ← 成了叶子', 'C ✗ 已淘汰'], on=2),
                    'alloc': alloc_g('+4', '还回 2016…2019')}),
    ]

    return scene(1220, 500, parts, wires, steps, mw=340, mh=1560,
                 cap='KV 缓存 + 前缀树里面：一棵树、两张表、一本账')


# ---------------------------------------------------------------- 源码页
def _src_match():
    return node(
        'kv_cache.match', '匹配源码', '匹配：限长 19、沿边走、半条边就劈开',
        '「前缀匹配」框：限长怎么算、在树上怎么走、为什么只记位置。',
        lead='你在「KV 缓存 + 前缀树」里的「前缀匹配」框：从限长 19 到「命中 5、停在 A」的每一行。',
        src=SBT + ' _compute_max_prefix_len · ' + MEM + ' match_kv_cache · ' + UTC + ' _match_prefix_helper',
        blocks=[
            code(SBT, [(1238, 1240), (1246, 1247), (1257, 1257)], notes=[
                at(1239, '注释', '命中最多比输入少 1（本例限长 19），注释说是为了 logprob 计算。',
                   q='the matched length is at most 1 less than the input length to enable logprob computation', has='at most 1 less'),
                at(1240, '提交', '本例 20 − 1 = 19。这个 −1 是一次修 bug 加进来的，当年标着 FIXME，说以后可以放宽。',
                   q='[Fix] Fix select by ensuring each request has at least one token (#1318)', ref='c:1e495e0847', has='input_len - 1'),
                at((1246, 1247), '推断', '要返回 prompt logprob 时，命中不能越过起点：那些位置必须真过一遍模型。', has='logprob_start_len'),
                at(1257, '推断', '很短的 prompt 也不会得到负数。', has='max(max_prefix_len, 0)'),
            ], cap='_compute_max_prefix_len（节选，dLLM 分支略）：限长'),
            code(SBT, [(1191, 1215)], notes=[
                at((1191, 1192), '注释', '传整条数组加上限，不切片，省一次拷贝。',
                   q='Pass the full array with a raw-token cap (limit) instead of slicing,', has='raw-token cap'),
                at((1196, 1200), '注释', '带 embed 覆盖的请求不匹配：同样的 token id、不同的覆盖向量，KV 不同。',
                   q='same token IDs', has='array("q")'),
                at((1214, 1215), '推断', '匹配完先把 [0, 5) 记成归树。', has='self.kv.protect_prefix(self.prefix_len)'),
            ], cap='init_next_round_input（节选）：调度器从这里进来'),
            code(MEM, [(193, 199), (205, 218)], notes=[
                at(195, '推断', '没传 token_ids 时的默认：prompt + 已生成。从 init_next_round_input 进来时传的是整条 fill ids。', has='req.origin_input_ids + req.output_ids'),
                at((205, 208), '推断', '测试 / 基准开关：把每次匹配当 0 命中。', has='SGLANG_RADIX_FORCE_MISS'),
                at((209, 218), '文档', '只记「命中几个、停在哪」，不拿槽号；槽号到分配时才读。',
                   q='valid from match until allocation writes them into the row.', ref=A(BPC, 728), has='req.prefix_len = match_result.device_prefix_len'),
            ], cap='match_kv_cache（节选）：结果记到请求上'),
            code(UTC, [(1061, 1095)], notes=[
                at(1061, '推断', '键 = 当前位置的那个 token（page_size=1）。在当前节点的孩子里找。', has='child_key in node.children'),
                at((1064, 1066), '推断', 'HiCache 才会走到，普通路径跳过。', has='HiCache'),
                at(1068, '注释', '数这条边和请求有几个相同：倍增窗口 + 二分，不逐个比。',
                   q='Exponential search for the first diverging token: gallop in doubling', ref=A(RC, 194), has='match_at'),
                at((1070, 1075), '推断', '只吃了半条边：当场劈开，停在前半。第二条同样的请求在这里把 B 劈成 B1 + B2。', has='_split_node'),
                at((1077, 1083), '推断', '整条边吃完：收下这条边的槽号，往下走。本例走完 A 后，第 6 个 token 找不到孩子，循环结束。', has='value.append(child.component_data'),
                at((1085, 1095), '推断', '后处理：其中会刷新沿路祖先的访问时间。', has='_match_post_processor'),
            ], cap='_match_prefix_helper（节选）：在树上走'),
            code(UTC, [(1182, 1186)], notes=[
                at((1182, 1186), '推断', '从停下的节点一路往上刷新 last_access_time：LRU 的「最近用过」在匹配时就记上。', has='node_update.last_access_time = cur_time'),
            ], cap='_match_post_processor（节选）'),
        ])


def _src_ledger():
    return node(
        'kv_cache.ledger', '账本源码', '账本 req.kv：几个长度把一行分成「归树」和「归我」',
        '「请求的账本」框：`ReqKvInfo` 的字段和唯一写它的几个方法。',
        lead='你在「KV 缓存 + 前缀树」里的「请求的账本」框：一行槽号靠几个长度分账，本例 protected 从 5 → 20 → 24。',
        src=SBT + ' ReqKvInfo',
        blocks=[
            code(SBT, [(273, 287)], notes=[
                at(277, '注释', 'last_node、lock 这些匹配结果和调度状态在 Req 上，不在这里。',
                   q='Match observations and scheduling state stay on the Req itself.', has='Match observations'),
                at(278, '推断', '在请求行表里的行号（本例 7）。', has='req_pool_idx'),
                at((280, 282), '注释', '一句话分账：[0, protected) 归树，[protected, allocated) 归自己。',
                   q="The request's own KV is [cache_protected_len, kv_allocated_len).", has='cache_protected_len: int = 0'),
                at((283, 285), '注释', '插过树的长度：以后只对更长的部分计命中，一个节点只算一次。',
                   q='a hit only on the nodes past it, so a request counts each node once.', has='cache_inserted_len'),
                at((286, 287), '推断', '已写好到哪 / 分到哪。本例 prefill 后都是 20，decode 4 步后都是 24。', has='kv_committed_len'),
            ], cap='ReqKvInfo（节选）'),
            code(SBT, [(361, 370), (405, 412)], notes=[
                at((361, 364), '推断', 'prefill 分完槽：allocated = committed = 20。', has='def set_row_len'),
                at((366, 370), '注释', 'decode：分配时就记成已提交，在前向跑之前。',
                   q='A decode step holds num_tokens more slots and commits them at', has='allocate_decode'),
                at((405, 407), '推断', '匹配后、每次写树后挪这条线：5 → 20 → 24。', has='def protect_prefix'),
                at((409, 412), '推断', '只增不减：短的插入不覆盖记录。', has='max(self.cache_inserted_len, length)'),
            ], cap='写账本的几个方法（节选）'),
            code(SBT, [(990, 1002)], notes=[
                at((999, 1002), '推断', '结束时树该存多长：本例 min(24, 20 + 5) = 24。停止 token 算在输出里。', has='output_ids_through_stop'),
            ], cap='owned_kv_len：结束时留给树的长度'),
        ])


def _src_alloc():
    return node(
        'kv_cache.alloc', '分槽源码', '分槽：先腾后取，再写进请求行',
        '「请求行表」「空闲槽表」两个框：15 个新槽怎么来、怎么写进行 7；decode 每步那 1 个。',
        lead='你在「KV 缓存 + 前缀树」里的「请求行表 / 空闲槽表」：本例要 15 个槽（2001..2015），写成行 7 = [101..105, 2001..2015]。',
        src=ALC + ' alloc_for_extend / alloc_token_slots',
        blocks=[
            code(ALC, [(352, 360), (394, 400), (424, 432)], notes=[
                at((353, 354), '注释', '命中槽号的来源：已经有行的读自己行，新请求从树上读。',
                   q='already has its prefix there; a new one reads its match off the tree.', has='reads its match off the tree'),
                at(360, '推断', '新请求这时才沿 last_node 往上收槽号：101..105。', has='prefix_device_indices'),
                at((394, 400), '推断', '先要一行（本例行 7）；行不够直接抛错。', has='alloc_req_slots'),
                at(432, '推断', 'page_size = 1：一次要 extend 个槽，本例 15。', has='alloc_token_slots(batch.tree_cache, alloc_extend_num_tokens)'),
            ], cap='alloc_for_extend（节选）'),
            code(ALC, [(196, 215)], notes=[
                at((200, 202), '推断', '取槽时把「腾地方」的函数一起传进去。', has='make_room=_room_in(tree_cache)'),
                at((204, 213), '注释', '腾完还不够才报 OOM；正常走不到，前面有准入预算和 decode 预检挡着。',
                   q='Out of memory. Try to lower your batch size.', has='Out of memory'),
            ], cap='alloc_token_slots'),
            code(SKS, [(269, 278)], notes=[
                at((277, 278), '推断', '顺序：先 make_room，再 alloc。不先腾，alloc 就返回 None。', has='make_room(num_tokens)'),
            ], cap='take_tokens：先腾后取'),
            code(BASE, [(248, 261)], notes=[
                at((259, 261), '推断', '只有空闲不够才让树淘汰：没满就不淘汰。本例空闲够，这里什么都不做。', has='self.available_size() < num_tokens'),
            ], cap='evict_to_free_tokens'),
            code(TOK, [(42, 63)], notes=[
                at((43, 46), '注释', '槽 0 是垫位，空闲表从 1 开始。',
                   q='The padded slot 0 is used for writing dummy outputs from padded tokens.', has='torch.arange'),
                at((58, 59), '推断', '不够返回 None，交给上层报错。', has='return None'),
                at((61, 62), '推断', '从头切走前 n 个：本例 2001..2015。', has='self.free_pages[:need_size]'),
            ], cap='空闲槽表：clear / alloc'),
            code(ALC, [(695, 697), (730, 738), (758, 759)], notes=[
                at(697, '推断', 'decode：整批一次要 bs 个。', has='bs * token_per_req'),
                at((734, 738), '推断', '新槽写在第 seq_len 列：本例第 20 列 = 2016。', has='locs = seq_lens_gpu.clone()'),
                at((758, 759), '推断', '账本 allocated、committed 各 +1。', has='allocate_decode'),
            ], cap='alloc_for_decode（节选）：每步 1 个'),
        ])


def _src_ckpt():
    return node(
        'kv_cache.ckpt', '写树源码', '写树：插入、去重、改指、移锁',
        '「前缀树」框和「写树 · 改指」那条线：prefill 后挂上 B、结束时挂上 C 的每一步。',
        lead='你在「KV 缓存 + 前缀树」里的「写树」：本例 prefill 后挂上 B（15 个），结束时挂上 C（4 个），锁 A → B → C。',
        src=URC + ' _checkpoint_into_tree · ' + UTC + ' insert',
        blocks=[
            code(MEM, [(235, 244)], notes=[
                at((236, 237), '注释', 'prefill 做完一块、请求还在跑，就把算好的部分交给树。',
                   q='Publish what the running request has computed so far', has='Publish what the running request'),
                at(244, '推断', '下一块从这里接着算。', has='req.prefix_len = req.extend_end'),
            ], cap='checkpoint_kv_cache：运行中的那次写树'),
            code(URC, [(1657, 1661), (1669, 1680)], notes=[
                at(1661, '推断', '要插的 token：prefill 时是 prompt 20 个，结束时是 24 个。', has='full_untruncated_fill_ids[:up_to]'),
                at((1669, 1671), '推断', '槽号从请求行里读——所以结束时必须先插树、后还槽。', has='kv_indices_orig'),
                at((1674, 1676), '推断', '从账本带上 protected / inserted：去重和计命中都靠它们。', has='prev_prefix_len=req.kv.cache_protected_len'),
            ], cap='_checkpoint_into_tree（一）：准备'),
            code(URC, [(1723, 1725), (1738, 1740), (1760, 1787)], notes=[
                at(1725, '推断', '复制一份：树存自己的拷贝，不是请求行的视图。理由没找到注释，推断是行之后会被回收覆盖。', has='copy=True'),
                at(1740, '推断', '真正插入：走、去重、挂新叶子（下面两段）。', has='self.insert(insert_params)'),
                at((1762, 1764), '注释', '在 prompt 边界再切一刀：淘汰时能只丢输出、留下可复用的 prompt。',
                   q='Split the leaf at the prompt boundary so eviction can drop the output', has='prompt boundary'),
                at((1771, 1775), '推断', '只有单个 Full 组件、这次插的比 prompt 长才切。本例 prefill 那次已经在 20 处分开，结束那次是空操作。', has='len(prompt_key) < len(radix_key)'),
            ], cap='_checkpoint_into_tree（二）：插入 + prompt 边界'),
            code(URC, [(1791, 1792), (1800, 1805), (1812, 1812), (1822, 1824)], notes=[
                at((1800, 1803), '推断', '行里 [protected, 末尾) 改指树里那份；本例值相同。', has='self.req_to_token_pool.write('),
                at(1805, '推断', '先解旧锁（本例 A，结束时 B）……', has='self.unlock(req.lock)'),
                at(1812, '推断', '……再锁新叶子（B，结束时 C）。先解后锁的理由没找到说明：同一线程里两步之间没有分配，淘汰插不进来。', has='req.lock = self.lock('),
                at((1822, 1824), '推断', 'protected 挪到新长度：20，结束时 24。', has='req.kv.protect_prefix(len(new_indices))'),
            ], cap='_checkpoint_into_tree（三）：改指、移锁'),
            code(UTC, [(1498, 1504), (1517, 1517), (1523, 1525), (1539, 1541)], notes=[
                at(1517, '推断', '只有请求自己新算的那段才可能重复；命中来的本来就是树的。本例在 A 上 max(0, 5 − 0) = 5，整个 A 都不算重复。', has='dup_start = max(0'),
                at((1539, 1540), '推断', '重复的槽还给分配器：两条同 prompt 的请求同时 prefill，后写的那份在这里被还掉。', has='FreeDeviceKV([dup'),
            ], cap='插入·OVERLAP（节选，SWA 分支略）：撞上已有的段'),
            code(UTC, [(1546, 1548)], notes=[
                at(1546, '注释', '命中次数在插入时才 +1，同一请求只计一次（只对 lfu / slru 有用，默认 lru 不看）。',
                   q='Nodes this request already inserted were counted back then.', has='already inserted'),
            ], cap='hit_count 什么时候 +1'),
            code(UTC, [(1564, 1578)], notes=[
                at((1570, 1577), '推断', '剩下的 key 挂成新叶子：本例 prefill 时的 B（15 个），结束时的 C（4 个）。', has='_add_new_node'),
            ], cap='插入·COMMIT（节选）：挂新叶子'),
        ])


def _src_release():
    return node(
        'kv_cache.release', '释放源码', '结束释放：先插树，再只还归自己的',
        '「⑥ 结束：还多余」那条线：`release_kv_cache` 整段，本例为什么一个槽都没还。',
        lead='你在「KV 缓存 + 前缀树」里「⑥ 结束」那条线：owned = 24，写树后 protected = 24，所以要还的区间是空的。',
        src=MEM + ':385-433',
        blocks=[
            code(MEM, [(385, 388), (409, 433)], notes=[
                at(388, '推断', '有行和已释放必须互斥：防止还两次。', has='assert (not req.kv.holds_kv)'),
                at(409, '推断', '本例 owned = min(24, 20 + 5) = 24。', has='owned_kv_len = req.owned_kv_len()'),
                at(410, '推断', '带 embed 覆盖的请求不写树。', has='skip_radix_cache_insert'),
                at((417, 418), '注释', '插树用的 token 序列落后于 output_ids，先补齐。',
                   q='The fill-id array lags output_ids until the next prepare_for_decode.', has='refresh_fill_ids'),
                at(419, '推断', '第二次写树：挂上 C。', has='tree_cache.checkpoint(req, up_to=owned_kv_len)'),
                at((420, 421), '注释', '只还 [protected, owned)：本例 [24, 24)，空的。',
                   q="The protected prefix is not this req's to free.", has='free_kv_row'),
                at(422, '推断', '解锁：C 成了可淘汰叶子。', has='tree_cache.unlock(req.lock)'),
                at((424, 426), '推断', '分了但不该存的 [owned, allocated) 还回去；本例也是空的。', has='_release_overallocated_kv_indices'),
                at((429, 432), '推断', '还行 7，账本记成已释放。', has='release_row(req)'),
            ], cap='release_kv_cache（节选，mamba / 会话分支略）'),
            code(MEM, [(444, 450)], notes=[
                at((445, 446), '注释', '非投机解码时，只有「停止点早于最后一个已提交 token」才会有多分的槽要还。',
                   q='A stop landing before the last committed token does the same, via', has='A stop landing'),
            ], cap='_release_overallocated_kv_indices（节选）'),
        ])


def _src_evict():
    return node(
        'kv_cache.evict', '锁与淘汰源码', '锁到根，只摘没锁的叶子',
        '「② 锁到根」那条线和「淘汰」框：锁怎么让节点退出候选，淘汰怎么从枝头往根摘。',
        lead='你在「KV 缓存 + 前缀树」里的「锁 / 淘汰」：本例 A、B1 被锁，只有 C 是候选，先摘 C，再轮到 B2。',
        src=FULL + ' · ' + UTC + ' _is_device_leaf',
        blocks=[
            code(SP, [(2052, 2054)], notes=[
                at((2052, 2054), '注释', '入批先临时锁：这段前缀不算进可淘汰预算。',
                   q='The temporary pin excludes this prefix from the evictable budget.', has='_lock_node'),
            ], cap='add_one_req（节选）：临时锁'),
            code(PB, [(144, 153)], notes=[
                at((150, 153), '推断', '可用 = 空闲槽 + 可淘汰的槽；被锁的不在「可淘汰」里。', has='global_available_size + evictable'),
            ], cap='准入预算'),
            code(FULL, [(326, 342)], notes=[
                at((333, 337), '文档', '第一次被锁（0→1）：这段 token 从可淘汰挪到受保护。',
                   q='On first lock (`lock_ref: 0→1`), moves tokens from `component_evictable_size_` to `component_protected_size_`.', ref=A(CRD, 203), has='component_protected_size_'),
                at((338, 340), '推断', 'lock_ref +1、从候选叶子集合摘掉，再往父节点走，一直到根。', has='evictable_device_leaves.discard(cur)'),
            ], cap='Full 组件加锁：路径锁'),
            code(UTC, [(2659, 2675)], notes=[
                at((2660, 2661), '注释', '候选叶子的定义。', q='D-leaf: Full device value present, no child with Full KV on device,', has='D-leaf'),
                at(2668, '推断', '有锁就不是候选：本例 A、B1。', has='lock_ref > 0'),
                at((2670, 2674), '推断', '还有带 KV 的孩子也不是：本例 B2（下面挂着 C）。', has='for child in node.children.values()'),
            ], cap='_is_device_leaf'),
            code(FULL, [(211, 219), (229, 246)], notes=[
                at((215, 219), '推断', '所有候选叶子放进堆。', has='heapq.heapify'),
                at((230, 238), '推断', '刚摘掉的叶子，父节点若也成了叶子就压回堆：从枝头往根摘。本例摘了 C，压入 B2。', has='heapq.heappush'),
                at(241, '推断', '弹出堆顶（最久没用的）：本例 C。', has='heapq.heappop'),
            ], cap='Full 组件淘汰：一个堆'),
            code(EVP, [(17, 19)], notes=[
                at(19, '推断', '默认 lru：键就是 last_access_time，越小越先走。', has='last_access_time'),
            ], cap='LRU 策略'),
        ])


def nodes():
    return {'kv_cache': node(
        'kv_cache', 'KV 缓存里面', 'KV 缓存 + 前缀树：一棵树、两张表、一本账',
        '「命中 5、只算 15」到底是怎么省下来的：匹配、加锁、分槽、写树、释放、淘汰，跟着同一个例子走一遍。',
        lead=('你在顶层「KV 缓存 + 前缀树」这个框里面。它其实是**前缀树、请求行表、空闲槽表**三样东西，加上每条请求身上的**账本** `req.kv`；'
              '调度器一条请求会来敲 6 次门（线上的角标 1–6）。按 ▶ 接着顶层的例子走：prompt 20 个 token，树里已有 5 个。'),
        src=MC + '/',
        blocks=[_scene()],
        children=[_src_match(), _src_alloc(), _src_ckpt(), _src_release(), _src_evict(), _src_ledger()])}
