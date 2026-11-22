# 顶层「调度器」框的下一层：一步 schedule() 里面怎么排（HEAD 0e3c8dbdae）。
# 例子接顶层：prompt 20 token，生成 法国/的/首都/是/巴黎/。/<eos>，max_tokens=8。
# 本页补设：block_size=16、预算 2048；场景 B 的长请求 L、场景 A 的 R2、场景 C 的 A/B 都是设的。
from cv import *

SCHED = 'vllm/v1/core/sched/scheduler.py'
ASCH = 'vllm/v1/core/sched/async_scheduler.py'
SUT = 'vllm/v1/core/sched/utils.py'
RQ = 'vllm/v1/core/sched/request_queue.py'
OUT = 'vllm/v1/core/sched/output.py'
KVM = 'vllm/v1/core/kv_cache_manager.py'
STM = 'vllm/v1/core/single_type_kv_cache_manager.py'
BP = 'vllm/v1/core/block_pool.py'
REQ = 'vllm/v1/request.py'
ARG = 'vllm/engine/arg_utils.py'
CFGS = 'vllm/config/scheduler.py'
CC = 'vllm/config/cache.py'
ARCH = 'docs/design/arch_overview.md'
OPT = 'docs/configuration/optimization.md'
PC = 'docs/design/prefix_caching.md'
TAS = 'tests/v1/core/test_async_scheduler.py'
TS = 'tests/v1/core/test_scheduler.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图
def q(*cs):
    cs = list(cs)
    return g_cells(cs + [cell()] * max(0, 2 - len(cs)), left='队首')


def rq(has, done, ph):
    return g_kv([('已有', has), ('已算', done), ('占位', ph)])


def bud(used):
    return g_gauge(used, 2048, total=2300, label='本步已用')


def calc(a, b, c, la='待算', lb='预算剩', lc='本步排'):
    return g_kv([(la, a), (lb, b), (lc, c)])


def pool(*cs, left='队头', right=None):
    return g_cells(list(cs), left=left, right=right)


def rn(*rows):
    return g_kv(list(rows))


def wo(new, cached):
    return g_kv([('首次', new), ('增量', cached)])


def up(a, b):
    return g_kv([('回来', a), ('判停', b)])


# ---------------------------------------------------------------- 机器图
def _scene():
    parts = [
        part('req', '请求的三个数', [1010, 16, 200, 124], m=[70, 8, 200, 124], kind='store', code='Request',
             glyph=rq('20', '0', '0'),
             say='每步让「已算」追上「已有+占位」', why='没有 prefill/decode 两阶段', trap='「已算」其实是「已排」',
             card=card(
                 '调度器眼里，每个请求只有三个数：**已有**（`num_tokens`，prompt + 已确认的输出）、**已算**（`num_computed_tokens`）、**占位**（`num_output_placeholders`，已经排出去、值还没回来的输出个数）。' + R(REQ, 338) + R(REQ, 172),
                 ['每一步做的事，就是给每个请求分几个 token，让「已算」追上「已有 + 占位」。',
                  'running 请求这一步要排的量 = 已有 + 占位 − 已算。本例第 1 步：20 + 0 − 0 = 20；之后每步：x + 1 − x = 1。' + R(SCHED, 824, 828),
                  '「已算」在**排完的那一刻**就加上去，不等 GPU 算完；只要这一步会采样，就再记 1 个占位。结果回来时「已有」+1、占位 −1。' + R(SCHED, 1892) + R(ASCH, 45, 48) + R(SCHED, 2957),
                  '「已算」也会往回走：被抢占时清零；投机 token 被拒、或一次回来多个 token 中途停下时回退。' + R(SCHED, 1854) + R(SCHED, 1887, 1888) + R(SCHED, 2947, 2952)],
                 [note('注释', '调度器里没有「prefill 阶段」「decode 阶段」，只有「已算」追「已有」；分块 prefill、前缀缓存、投机解码都只是这一个追赶循环的不同取值。',
                       q="so that each request's num_computed_tokens can catch up its", ref=A(SCHED, 710)),
                  note('注释', '排完就推进「已算」：下一步可以马上再排这个请求（比如分块 prefill 的下一块），不用等结果回来。',
                       q='schedule the prefill request again immediately in the next', ref=A(SCHED, 1885))],
                 ['「已算」其实是「已排」：GPU 还没算，它就加了。顶层框上的三个数（本步排 / 已算 / 占位）就是这里的值。',
                  '占位不是一个 token 值，只是个计数：它让「已有 + 占位 − 已算」在结果还没回来时仍然等于 1，于是不用等就能排下一步。']),
             to='sched.ph'),

        part('running', '运行队列', [120, 16, 160, 124], m=[220, 388, 118, 140], kind='store', code='running',
             glyph=g_text('空', '还没有请求'),
             say='有 KV 的请求，按进入顺序', why='先排它：decode 不断流', trap='最后进的最先被踢',
             card=card(
                 '已经分到 KV block 的请求，不管在 decode 还是 prefill 到一半。每一步 `schedule()` **先**从头到尾过一遍它。',
                 ['一个按进入顺序排的 `list[Request]`：waiting 里的请求分到 block 后被 `append` 到末尾，越靠后越晚进。' + R(SCHED, 1596, 1598),
                  '循环条件是 `token_budget > 0`：预算用完就停，没排上的留在原地，下一步再来。' + R(SCHED, 780),
                  '某个请求这一步排不了（`num_new_tokens == 0`）就 `continue` 跳过它，接着排后面的。' + R(SCHED, 890, 894)],
                 [note('文档', '先排所有 decode，再排 prefill：decode 优先，token 间延迟（ITL）更好；同一批里 prefill（算力密集）和 decode（访存密集）搭配，GPU 用得更满。',
                       q='It batches all pending decode requests before scheduling any prefill operations.', ref=A(OPT, 91)),
                  note('推断', 'running 的请求已经占着 KV；先把它们排完，剩下的预算和 KV 才给新请求，新请求就不会把老请求饿住、或逼老请求被抢占。'),
                  note('注释', '排不了就跳过（`continue` 而不是 `break`）：不死守先来先服务，让后面的请求也能排上。',
                       q='we do not strictly follow the FCFS scheduling policy and', ref=A(SCHED, 891))],
                 ['「最后一个」= 最晚进来的那个：显存不够时，FCFS 抢占踢的就是它。' + R(SCHED, 923),
                  '人数另有上限 `max_num_seqs`（显存 ≥70GB 默认 1024，其余 256）；满了 waiting 里的就进不来。' + R(ARG, 3008) + R(ARG, 3032)]),
             to='sched.loop'),

        part('waiting', '等待队列', [120, 402, 160, 116], m=[220, 858, 118, 124], kind='store', code='waiting',
             glyph=q(),
             say='还没 KV：新来的+被踢的', why='队首卡住，后面也不排', trap='本步踢过人就不排它',
             card=card(
                 '还没分到 KV block 的请求：`add_request` 新来的，和被抢占踢回来的。',
                 ['默认 FCFS：一个 deque；被抢占的请求用 `prepend_request`（`appendleft`）插回**队首**。' + R(RQ, 75) + R(RQ, 94),
                  '`--scheduling-policy priority` 时换成堆。' + R(RQ, 131),
                  'running 排完才轮到它，而且只在**这一步没发生过抢占**时：`if not preempted_reqs`。' + R(SCHED, 1041),
                  '一次看队首一个：查前缀缓存 → 定排几个 → 要 block；分到就弹出、加进 running；分不到就 `break`，后面的也不排。' + R(SCHED, 1508, 1515)],
                 [note('推断', '队首分不到就停、不让后面的小请求插队：否则大请求可能一直排不上。仓库里没有注释说明这一点；依据是它和 running 那边「continue」的注释正好相反。'),
                  note('推断', '刚因为 KV 不够踢了人，说明显存紧；这时再放新请求进来，要么分不到，要么又逼出下一次抢占。这一行从 V1 第一个提交就在，没有注释。')],
                 ['这一步排不了、但**不是显存问题**的（比如多模态编码预算用完），不会 `break`，而是暂放一边，循环结束放回队首。' + R(SCHED, 1449)]),
             to='sched.admit'),

        part('budget', '本步 token 预算', [400, 402, 190, 116], m=[85, 1078, 170, 126], kind='gate', code='token_budget',
             glyph=bud(0),
             say='每排一个就扣掉', why='按 token 数限', trap='满了下一步再排',
             card=card(
                 '`schedule()` 开头设的一个局部变量：这一步所有请求加起来最多排多少 token。每排上一个请求就扣掉它的 token 数。',
                 ['`token_budget = self.max_num_scheduled_tokens`；没开投机解码时就等于 `max_num_batched_tokens`。' + R(SCHED, 728, 731) + R(SCHED, 147, 150),
                  '单个请求一次最多拿 `min(待算, 剩余预算)`：一个长 prompt 就这样被**自然地切块**，没有专门的分块分支。' + R(SCHED, 831, 833) + R(SCHED, 1388),
                  '默认值按显存分档：≥160GB 是 16384；≥70GB（A100 / GB10 除外）是 8192；其余 2048。本页设为 **2048**。' + R(ARG, 3004) + R(ARG, 3018) + R(ARG, 3028),
                  '结尾有硬断言：总数不超过预算。' + R(SCHED, 1674)],
                 [note('文档', '预算小（如 2048），夹进来的 prefill 少，decode 的 token 间延迟（ITL）更好；预算大，一批能吞更多 prefill，首 token 延迟（TTFT）更好。',
                       q='Smaller values (e.g., 2048) achieve better ITL because there are fewer prefills slowing down decodes.', ref=A(OPT, 102)),
                  note('推断', '按 token 而不按请求数限：一步的计算量约正比于 token 数；一个 prefill 可能几千 token、一个 decode 只有 1 个，按请求数限不住一步的耗时。')],
                 ['预算满了不是错误：没排上的请求原地不动，下一步再来。',
                  '三档默认值里只有 A100 有一条注释说明；其余硬件源码里写着「还没调」。' + R(ARG, 3025)]),
             to='sched.loop'),

        part('calc', '定这步排几个', [400, 196, 190, 150], m=[94, 623, 156, 165], kind='gate', code='num_new_tokens',
             glyph=calc('—', '2048', '—'),
             say='待算和剩余预算取小', why='一个公式盖住 prefill 和 decode', trap='命中的 token 不算待算',
             card=card(
                 '对眼下这个请求，算这一步给它排几个 token。running 和 waiting 两个循环都要过这一关。',
                 ['running 的请求：待算 = 已有 + 占位 − 已算，再和剩余预算取小。' + R(SCHED, 824, 833),
                  'waiting 的请求：先查前缀缓存，命中的 token 直接当「已算」；待算 = 已有 − 命中，再和预算取小。' + R(SCHED, 1239) + R(SCHED, 1348) + R(SCHED, 1388),
                  'running 的请求**先**过一道守卫：在飞的几步已经确定能凑满 `max_tokens`，这一步就不排它（推演第 9 步算给你看）。' + R(SCHED, 785, 799),
                  '另有两个封顶：`long_prefill_token_threshold`（默认关；只有一个请求时也不生效）和 `max_model_len`。' + R(SCHED, 829) + R(SCHED, 835, 842)],
                 [note('注释', '没有 prefill / decode 之分：整段 prompt、分块 prefill、decode、前缀缓存命中，都是同一个「已算追已有」。',
                       q='There\'s no "decoding phase" nor "prefill phase" in the scheduler.', ref=A(SCHED, 705)),
                  note('注释', '守卫的用意：异步调度下，确定在飞的几步会到达 `max_tokens` 时，不再多排一步。',
                       q='Async scheduling: Avoid scheduling an extra step when we are sure that', ref=A(SCHED, 795))],
                 ['waiting 这边用的是 `num_tokens` 而不是 prompt 长度：被抢占后重来的请求，已经生成的 token 也要重算。' + R(SCHED, 1345, 1347),
                  '守卫只挡得住「长度」，挡不住 `<eos>`：EOS 由生成的数据决定，排的时候还不知道（推断）。']),
             to='sched.loop'),

        part('kvm', 'KV 账房', [710, 196, 180, 150], m=[2, 388, 126, 165], kind='gate', code='KVCacheManager',
             glyph=g_checks([('查前缀缓存', None), ('块够不够', None)]),
             say='查命中、分块，不够就回 None', why='整条放得下才放行', trap='None 不是报错',
             card=card(
                 '调度器的 KV 账房：替每个请求记着它占了哪几块，管三件事——查前缀缓存命中多少（`get_computed_blocks`）、给这一步的 token 分块（`allocate_slots`）、请求结束时还块（`free`）。',
                 ['要几块：需要的槽位 = 已算 + 本步排（+ lookahead），按 `block_size` 向上取整，减去已经有的。' + R(KVM, 561, 564) + R(STM, 322),
                  '空闲块不够就**返回 `None`**：running 那边据此去抢占别人，waiting 那边据此 `break`。' + R(KVM, 593, 599),
                  'waiting 的请求多一道闸（默认开）：**整条** prompt 要的块都放得下，才让第一块进来。' + R(KVM, 542, 559),
                  '分完，把**已满、且 token 已确认**的块登记进前缀缓存。' + R(KVM, 628, 637)],
                 [note('文档', '整条放得下才放行：只看第一块就放进来，后面的块可能分不到，造成过量准入和 KV cache 来回抖动。',
                       q='checking the first chunk. Prevents over-admission and KV cache thrashing', ref=A(CFGS, 229)),
                  note('注释', '只登记「已确认」的 token：投机解码的草稿 token 可能被拒，不能进缓存。',
                       q='Therefore, we cap the number at `request.num_tokens`, ensuring only', ref=A(KVM, 631))],
                 ['它自己不碰物理块：块本身、空闲队列、「哈希 → 块」表都在块仓库（BlockPool）；中间还隔着一层 `coordinator`。' + R(KVM, 164),
                  '前缀缓存只按**整块**命中，而且最多命中「已有 − 1」个 token：最后一个 token 总要重算，才拿得到采样要的 logits。' + R(REQ, 359)]),
             to='sched.kv'),

        part('pool', '块仓库', [1010, 196, 200, 150], m=[2, 858, 126, 150], kind='store', code='BlockPool',
             glyph=pool(cell('#1', '空'), cell('#2', '空'), cell('#3', '空')),
             say='所有物理块 + 哈希表', why='有哈希的放队尾，最后复用', trap='还了≠清空，还能命中',
             card=card(
                 '所有物理 KV block 的仓库：每块的引用计数 `ref_cnt`、一条空闲队列、一张「块哈希 → 块」表——前缀缓存就是这张表。',
                 ['分块：从空闲队列**头部**弹出；弹出的块要是还挂着旧哈希，这时才把它从缓存表里摘掉（驱逐）。' + R(BP, 696) + R(BP, 710, 715),
                  '命中：被命中的块 `touch`：`ref_cnt + 1`，正在空闲队列里的就摘出来，免得被别人弹走。两个请求共用的是**同一个物理块**。' + R(BP, 783, 790),
                  '还块：`ref_cnt` 减到 0 的块回到空闲队列——没哈希的放**队头**（最先复用），有哈希的放**队尾**（最后复用）。' + R(BP, 824, 837),
                  '块 0 是全局的 `null_block`，不分给任何请求，只作占位。' + R(BP, 161, 164)],
                 [note('注释', '有哈希的块先进先出地复用，效果就是 LRU：越久没被用的缓存块越先被挤掉。',
                       q='FIFO reuse of cached blocks for LRU eviction behavior.', ref=A(BP, 831)),
                  note('注释', '没哈希的块后进先出：刚还回来的马上再用，GPU 局部性更好。',
                       q='LIFO reuse of non-cached blocks for better GPU locality,', ref=A(BP, 827)),
                  note('文档', '一个请求的块倒着还：尾块的哈希里含的 token 更多，更不容易被别人复用，应该先被挤掉。',
                       q='This is because the last block of a request must hash more tokens and is less likely to be reused by other requests.', ref=A(PC, 198))],
                 ['**还块不是清空**：请求结束后，有哈希的块还留在缓存表里，直到被后来者弹出时才真正驱逐。所以请求结束了，前缀缓存还能命中它。',
                  '本页 `block_size` 设为 16，才看得见两块；这个版本 config 的默认是 **64**（旧资料里的 16 已过时），本例全程只要 1 块。实际值还可能被注意力后端改掉，以启动日志为准。' + R(CC, 185) + R('vllm/platforms/interface.py', 800, 812)]),
             to='sched.kv'),

        part('out', '本步工单', [710, 16, 180, 124], m=[2, 196, 126, 124], kind='out', code='SchedulerOutput',
             glyph=wo('—', '—'),
             say='谁排几个、用哪几块', why='老请求只发增量', trap='被抢占的重来算首次',
             card=card(
                 '`schedule()` 的产物，交给 GPU 那边（顶层的「GPU 执行」）：这一步每个请求排几个 token、用哪几块。',
                 ['`scheduled_new_reqs`：第一次被排的请求，发**完整**数据；`scheduled_cached_reqs`：排过的，只发变化；`num_scheduled_tokens`：每个请求本步几个。' + R(OUT, 241, 249),
                  '打包之后马上 `_update_after_schedule`：每个请求「已算 += 本步排」，会采样的再记 1 个占位。' + R(SCHED, 1889, 1892) + R(ASCH, 45, 48)],
                 [note('注释', '请求的数据缓存在 worker 进程里，所以排过的请求只发变化，省通信。',
                       q="We cache the request's data in each worker process, so that we don't", ref=A(OUT, 243)),
                  note('注释', '「已算」在打包**之后**才推进：工单里要带着推进前的值，GPU 那边才定得出输入 token 的位置。',
                       q='original number of scheduled tokens to determine input IDs.', ref=A(SCHED, 1883))],
                 ['被抢占后重新入场的请求，会作为 `scheduled_new_reqs` **再发一次完整数据**：它的块已经还了，worker 上的状态也丢了。' + R(SCHED, 1606, 1607)]),
             to='sched.ph'),

        part('update', '收结果', [400, 16, 190, 124], m=[212, 196, 126, 124], kind='act', code='update_from_output',
             glyph=up('—', '—'),
             say='追加 token、消占位、判停', why='停了立刻还块', trap='晚到的结果找不到人就丢',
             card=card(
                 'GPU 结果回来后（默认处理的是上上一步排的那批，见顶层）：把新 token 追加到请求上、消掉占位、判停；停了就还块，并做出发回 API 进程的 `EngineCoreOutput`。',
                 ['每回来 N 个 token：逐个追加、逐个 `check_stop`，最后占位 −N。' + R(SCHED, 2941, 2958),
                  '判停顺序：EOS → `stop_token_ids` → 长度（`max_tokens` / `max_model_len`）→ `min_tokens` 门 → 重复检测。' + R(SUT, 98, 140),
                  '停了：`_free_request` → 记进 `finished_req_ids`、还块、从总表 `requests` 里删掉；它也退出 running。' + R(SCHED, 2587) + R(SCHED, 3244, 3257) + R(SCHED, 2655, 2656),
                  '分块 prefill 的中间块回来时没有 token，不产生输出。' + R(SCHED, 2649)],
                 [note('注释', '先判停、再做输出：输出里要带上结束原因。',
                       q='This must be called before we make the EngineCoreOutput.', ref=A(SCHED, 2945)),
                  note('推断', '判停和还块在同一处：请求一结束，它的块下一步就能分给别人。')],
                 ['结果回来时请求可能已经不在了（被 abort，或像本例这样已因 EOS 结束）：`requests.get` 拿到 `None`，整条结果直接丢掉。' + R(SCHED, 2403) + R(SCHED, 2415, 2423),
                  '被抢占的请求还在飞的输出**照样收下**，只是不许改已经清零的计数器。' + R(SCHED, 1857, 1858)]),
             to='sched.ph'),
    ]

    wires = [
        wire('w_run', 'running', 'calc', '先排 running', ml='先排', n='1', mfs='l', mts='t', why='decode 优先',
             card=card('每一步先把 running 从头到尾过一遍，每个请求都去「定排几个」→「要块」。',
                       '`while req_index < len(self.running) and token_budget > 0`：预算用完就停。' + R(SCHED, 778, 780),
                       [note('文档', 'decode 优先，token 间延迟更好。',
                             q='It improves inter-token latency (ITL) and generation decode because decode requests are prioritized.', ref=A(OPT, 95))],
                       'running 的请求要不到块时，不是自己让路，而是去踢队尾的人（见「抢占」那条线）。'),
             to='sched.loop'),
        wire('w_wait', 'waiting', 'calc', '没抢占才排', ml='再排', n='2', mfs='l', mts='b', why='刚踢过人，不放新的', trap='队首卡住就全停',
             card=card('running 排完，轮到 waiting：一次看队首一个，查缓存、定排几个、要块。',
                       ['只在这一步没发生抢占时才进这个循环。' + R(SCHED, 1041),
                        '每取一个前先看：running 满了（`max_num_seqs`）或预算用完，就不再取新请求。' + R(SCHED, 1073, 1076),
                        '只对「已算 = 0」的请求查前缀缓存（新来的、被抢占回来的）。' + R(SCHED, 1132) + R(SCHED, 1149)],
                       [note('推断', '踢过人说明显存紧，这一步不再放新请求，免得刚放进来又逼出下一次抢占。仓库里这一行没有注释。')],
                       'FCFS 下队首分不到块就 `break`：后面的请求再小也不排。'),
             to='sched.admit'),
        wire('w_admit', 'waiting', 'running', '分到块 → 入场', ml='入场', fs='t', ts='b', mfs='t', mts='b', why='有 KV 才进 running',
             card=card('队首请求分到块后，弹出 waiting、加到 running 末尾，状态改成 RUNNING。',
                       '`pop_request` → `self.running.append(request)`，记进 `scheduled_new_reqs`，扣预算，「已算」= 命中数。' + R(SCHED, 1596, 1624),
                       [note('推断', '进 running 的门槛就是「拿到了 KV」：running 里的每个请求都有地方写 KV，之后每步只需按需补块。')],
                       '加在末尾 = 它是最晚进的：FCFS 抢占时第一个被踢的就是它。' + R(SCHED, 923)),
             to='sched.admit'),
        wire('w_pre', 'running', 'waiting', '抢占：踢回队首', ml='抢占', fs='l', ts='l', mfs='b', mts='t', cls='dash',
             lp=[60, 270], mlp=[286, 586], why='重算不换出', trap='踢最晚进的',
             card=card('running 里有人要不到块：把别人（FCFS：running 最后一个）踢回 waiting，腾出它的块，再试；直到分到，或者踢到自己。',
                       ['选谁：FCFS 取 `self.running[-1]`；priority 策略取 `(priority, arrival_time)` 最大的。' + R(SCHED, 915, 923),
                        '被踢的：还掉全部块 → 状态 `PREEMPTED`、已算清零、占位清零 → 插回 waiting **队首**。' + R(SCHED, 1849, 1876),
                        '踢到的正是自己（它就是最后一个）→ 这一步的 running 循环到此结束。' + R(SCHED, 967, 969),
                        '这一步只要踢过人，就**不再排 waiting**。' + R(SCHED, 1041)],
                       [note('文档', 'V1 默认重算（RECOMPUTE），不换到 CPU 内存（SWAP）。文档只说重算在 V1 架构下开销更低，没说为什么。',
                             q='In vLLM V1, the default preemption mode is `RECOMPUTE` rather than `SWAP`, as recomputation has lower overhead in the V1 architecture.', ref=A(OPT, 85)),
                        note('文档', '重算往往没那么贵：前缀缓存能马上补回一部分进度。',
                             q='Prefix caching or a KV connector can restore part of its progress', ref=A(ARCH, 167))],
                       ['「重算」是重算 prompt **加上已经生成的** token，而且作为新请求再发一次完整数据。' + R(SCHED, 1345, 1347),
                        '被踢时还在飞的输出不会丢，回来时照样交给客户端。' + R(ARCH, 181, 182)]),
             to='sched.preempt'),
        wire('w_bud', 'budget', 'calc', '封顶，排完扣掉', ml='封顶', mfs='t', mts='b', why='自动切块',
             card=card('定排几个时，拿剩余预算封顶；排上了就扣掉。',
                       '`min(num_new_tokens, token_budget, …)`；扣：`token_budget -= num_new_tokens`。' + R(SCHED, 831, 833) + R(SCHED, 991) + R(SCHED, 1621),
                       [note('推断', '一个 5000 token 的 prompt 遇上 2048 的预算，就被这一个 `min` 切成几块：分块 prefill 不需要单独的代码路径。')],
                       '关掉分块 prefill 时，装不下的 waiting 请求会让整个 waiting 循环停下，不会切块。' + R(SCHED, 1384, 1386)),
             to='sched.loop'),
        wire('w_ask', 'calc', 'kvm', '要块', ml='要块', mfs='l', mts='r', why='槽位按块向上取整',
             card=card('把「这一步给这个请求排 n 个 token」换算成需要的块，向账房要。',
                       'running：`allocate_slots(request, num_new_tokens)`；waiting：还带上命中的块和「整条放得下」的要求。' + R(SCHED, 899, 903) + R(SCHED, 1489, 1506),
                       [note('推断', '先定 token 数、再要块：token 数由预算决定，块只是它的结果；要不到时，调度器就知道该去抢占还是停下。')],
                       '要不到时不会「少排几个凑合」：running 那边去抢占，waiting 那边直接停。'),
             to='sched.kv'),
        wire('w_take', 'kvm', 'pool', '弹块 / touch', ml='取块', mfs='b', mts='t', mlp=[64, 822], why='命中块 ref+1',
             card=card('从仓库拿块：新块从空闲队列头部弹出；命中的块 `touch`。',
                       ['先把命中的块挂到这个请求的块表上，再申请新块。' + R(KVM, 601, 621),
                        '`touch`：`ref_cnt += 1`，在空闲队列里的就摘出来。' + R(BP, 783, 790)],
                       [note('注释', '先 touch 命中的块，保证它们不会被当成空闲块弹走。',
                             q="Touch the computed blocks to make sure they won't be evicted.", ref=A(STM, 414))],
                       '命中的块不复制：两个请求的块表里指着同一个物理块。'),
             to='sched.kv'),
        wire('w_out', 'kvm', 'out', '分到了 → 记工单', ml='记工单', mfs='t', mts='b', why='分到块才算排上',
             card=card('块分到了，这个请求这一步才算排上：记进工单，扣预算。',
                       '`num_scheduled_tokens[request_id] = num_new_tokens`；`token_budget -= num_new_tokens`。' + R(SCHED, 985, 991),
                       [note('推断', '「排上」以分到 KV 为准：工单里的每个 token 在 GPU 上都有地方写 KV。')]),
             to='sched.loop'),
        wire('w_gpu', 'out', 'update', 'GPU 跑完（晚一步）', ml='晚一步', mfs='r', mts='l', cls='dash', why='不等结果就排下一步', trap='回来的是上上一步',
             card=card('工单交给 GPU；采样结果回来后交给 `update_from_output`。中间那段是顶层的「GPU 执行」和「引擎忙循环」。',
                       '默认 async scheduling：`batch_queue` 深 2，排了第 k 步才去取第 k−1 步的结果。',
                       [note('文档', 'GPU 跑第 N 步时，CPU 已经在排第 N+1 步，用占位代替第 N 步还没出来的 token。',
                             q='overlaps CPU scheduling with GPU execution: while the GPU executes step *N*,', ref=A(ARCH, 177))],
                       '所以第 k 次 `schedule()` 时，第 k−1 步的 token 还在飞——占位就是为这个准备的。')),
        wire('w_back', 'update', 'running', '追加 token，消占位', ml='追加', mfs='b', mts='t', why='已有 +1，占位 −1',
             card=card('把回来的 token 写回 running 里那个请求：已有 +1、占位 −1；没停就继续留在 running。',
                       '`append_output_token_ids` → `check_stop` → `num_output_placeholders -= N`。' + R(SCHED, 2941, 2958),
                       [note('测试', '占位被扣成负数曾经让 EngineCore 崩溃（抢占 + 在飞输出时）；现在有断言和回归测试守着。',
                             q='Regression for the num_output_placeholders underflow EngineCore crash:', ref=A(TAS, 578))],
                       '停了的请求在这里退出 running。' + R(SCHED, 2655, 2656)),
             to='sched.ph'),
        wire('w_free', 'update', 'kvm', '结束：倒序还块', ml='还块', mfs='b', mts='t', why='尾块先被挤掉', trap='还了还留在缓存里',
             card=card('请求结束，把它的块还给仓库。',
                       '`_free_request` → `KVCacheManager.free` → `BlockPool.free_blocks`，块表**倒序**交出去。' + R(SCHED, 3220, 3257) + R(STM, 736, 737),
                       [note('注释', '倒着还：开了缓存时，尾块最先被挤掉。',
                             q='We free the blocks in reverse order so that the tail blocks are evicted', ref=A(KVM, 643))],
                       '不必等最后那一步在 GPU 上写完再还：普通前向在同一条 stream 上按顺序执行，块的新主人一定更晚运行（推断）；只有 KV 连接器的异步写才要推迟。' + R(SCHED, 3268, 3269)),
             to='sched.kv'),
    ]

    steps = [
        # ---- 主线：顶层那个请求 R
        beat('R 进 waiting',
             '顶层第 5 步：引擎把 R（20 个 prompt token、`max_tokens=8`）交给调度器，`add_request` 放进 waiting 队尾。三个数：已有 20、已算 0、占位 0。',
             note('推断', '新请求只是排队：要等下一次 `schedule()`，而且 running 先排完，才轮得到它。'),
             focus=['waiting', 'req'],
             state={'waiting': q(cell('R', '20 tok', 'new'))}),
        beat('S1：running 是空的，去 waiting；先查前缀缓存',
             '第 1 次 `schedule()`（S1，顶层的「第 1 轮」）：预算 2048。running 空，没有抢占，进 waiting 循环。R 的已算 = 0，于是查前缀缓存：命中上限 20 − 1 = 19 → 至多 19 // 16 = 1 整块；缓存是空的，命中 **0**。',
             note('注释', '最后一个 token 总要重算才拿得到 logits，所以命中上限是「已有 − 1」。',
                  q='The last token is always recomputed to produce logits.', ref=A(REQ, 359)),
             focus=['kvm', 'w_wait', 'waiting', 'calc'],
             state={'kvm': g_checks([('已算=0，查缓存', 'ok'), ('至多命中 1 块', 'ok'), ('实际命中 0', 'no')]),
                    'calc': calc('20 − 0', '2048', '?')}),
        beat('S1：排 20 个，要 2 块',
             '待算 20，和预算取小：`min(20, 2048)` = **20**，预算剩 2028。向账房要块：需要 ⌈20 / 16⌉ = 2 块；整条 prompt 放得下，空闲也够 → 仓库从队头弹出 #1、#2（编号是设的）。#1 装满 16 个 token，**马上登记哈希**；#2 只装了 4 个，不登记。',
             note('注释', '只登记已满、且 token 已确认的块。', q='Therefore, we cap the number at `request.num_tokens`, ensuring only', ref=A(KVM, 631)),
             focus=['calc', 'w_ask', 'kvm', 'w_take', 'pool', 'budget'],
             state={'calc': calc('20', '2048', '20'),
                    'budget': bud(20),
                    'kvm': g_checks([('要 2 块', 'ok'), ('整条放得下', 'ok'), ('空闲够', 'ok')]),
                    'pool': pool(cell('#1', 'R满', 'new'), cell('#2', 'R 4', 'new'), cell('#3', '空'))}),
        beat('S1：入场、写工单、推进「已算」',
             'R 弹出 waiting、加进 running，状态 RUNNING。工单：`scheduled_new_reqs = [R]`（首次，发完整数据），R 排 20。打包后马上推进：已算 0 → **20**；20 已经追上「已有 20 + 占位 0」，这一步会采样 → 记 **1 个占位**。这就是顶层框上的「本步排 20 / 已算 20 / 占位 1」。',
             note('注释', '排完就推进「已算」，下一步可以立刻再排。', q='Advance the number of computed tokens for the request AFTER', ref=A(SCHED, 1880)),
             focus=['w_admit', 'running', 'w_out', 'out', 'req'],
             state={'waiting': q(),
                    'running': rn(('R', '排 20')),
                    'out': wo('R：20', '—'),
                    'req': rq('20', '20', '1')}),
        beat('S2：结果还没回来，就排下一步',
             '第 1 步还在 GPU 上，CPU 已经开始 S2。先过守卫：20 + (1 + 1) − 1 = 21 < 20 + 8 = 28 → 照排。待算 = 20 + **1** − 20 = 1，排 1 个。需要 21 个槽位，还在 2 块之内，不分新块。R 是老请求，工单里只发增量。推进后：已算 21、占位 2。',
             note('文档', 'GPU 跑第 N 步时，CPU 已经在排第 N+1 步，用占位代替还没出来的 token。',
                  q='the CPU schedules step *N+1*, using placeholders for the tokens that step *N*', ref=A(ARCH, 178)),
             focus=['calc', 'running', 'w_run', 'out', 'req'],
             state={'calc': calc('20+1−20', '2048', '1'),
                    'budget': bud(1),
                    'kvm': g_checks([('要 2 块', 'ok'), ('已有 2 块', 'ok')]),
                    'pool': pool(cell('#1', 'R满'), cell('#2', 'R 5'), cell('#3', '空')),
                    'out': wo('—', 'R：1'),
                    'req': rq('20', '21', '2')}),
        beat('第 1 个 token「法国」回来',
             '忙循环取回第 1 步的结果，交给 `update_from_output`：「法国」追加到 R 上，已有 20 → **21**；占位 2 → **1**；`check_stop`：不是 EOS，输出 1 个 < 8，没停。做出 `EngineCoreOutput` 送回 API 进程。',
             note('注释', '先判停、再做输出。', q='This must be called before we make the EngineCoreOutput.', ref=A(SCHED, 2945)),
             focus=['update', 'w_gpu', 'out', 'w_back', 'running', 'req'],
             state={'update': up('法国', '没停'),
                    'req': rq('21', '21', '1')}),
        # ---- 场景 B：长 prompt 插进来
        beat('插曲（设）：来了一个 5000 token 的长请求 L',
             'S3 前来了 L：5000 个 prompt token，无缓存命中（设）。S3：running 先排 R 的 1 个（预算剩 **2047**）；没抢占，轮到 waiting：L 待算 5000，`min(5000, 2047)` = **2047**。整条要 ⌈5000/16⌉ = 313 块，设放得下 → 先分 ⌈2047/16⌉ = 128 块。L 入场。本步 1 + 2047 = **2048**，预算正好用完。',
             note('文档', '先排所有 decode，剩余预算才给 prefill；prefill 放不下就自动切块。',
                  q='When there are available tokens in the `max_num_batched_tokens` budget, it schedules pending prefills.', ref=A(OPT, 91)),
             focus=['budget', 'w_bud', 'calc', 'w_wait', 'waiting', 'kvm'],
             state={'waiting': q(),
                    'running': rn(('R', '排 1'), ('L', '排 2047')),
                    'calc': calc('5000', '2047', '2047', la='L 待算'),
                    'budget': bud(2048),
                    'kvm': g_checks([('L 整条 313 块', 'ok'), ('先分 128 块', 'ok')]),
                    'out': wo('L：2047', 'R：1'),
                    'req': rq('21', '22', '2')}),
        beat('S4、S5：L 一块一块地吃，R 不断流',
             'S4：R 排 1；L 已在 running，待算 5000 − 2047 = 2953，`min(2953, 2047)` = 2047，已算 4094。S5：R 排 1；L 剩 **906**，本步共 907。L 的前两块「已算」没追上「已有」，是中间块：**不记占位、回来时也没有输出**；S5 这块之后 L 才采样出第 1 个 token。R 每步照样拿到 1 个。',
             note('注释', '引擎不发分块 prefill 的中间结果。', q='Invariant: EngineCore returns no partial prefill outputs.', ref=A(SCHED, 2649)),
             focus=['calc', 'running', 'w_run', 'budget', 'out'],
             state={'running': rn(('R', '排 1'), ('L', '排 906')),
                    'calc': calc('906', '2047', '906', la='L 待算'),
                    'budget': bud(907),
                    'kvm': g_checks([('L 再补 57 块', 'ok')]),
                    'out': wo('—', 'R1 L906'),
                    'req': rq('23', '24', '2')}),
        # ---- 回到主线
        beat('S8：守卫算一算，还能排',
             '「法国 的 首都 是 巴黎 。」6 个 token 已回来：已有 26、已算 26、占位 1，第 7 个还在飞。守卫：26 + (1 + 1) − 1 = **27** < 28 → 照排，已算 27、占位 2。假如第 7 个不是 `<eos>`，下一步 S9 时：27 + 2 − 1 = **28** ≥ 28 → 跳过——所以按长度结束时，一步也不会多排。',
             note('测试', '测试断言：在飞多步时，也不会排出超过 `max_tokens` 的一步。',
                  q='No step is scheduled past max_tokens with several steps in flight, even', ref=A(TAS, 542)),
             focus=['calc', 'req', 'running', 'w_run'],
             state={'running': rn(('R', '排 1'), ('L', '排 1')),
                    'calc': calc('27<28 过', '26+1−26', '1', la='守卫', lb='待算'),
                    'budget': bud(2),
                    'kvm': g_checks([('要 2 块', 'ok'), ('已有 2 块', 'ok')]),
                    'out': wo('—', 'R1 L1'),
                    'req': rq('26', '27', '2')}),
        beat('第 7 个 token 是 <eos>：结束、倒序还块',
             '`check_stop` 命中 EOS → `FINISHED_STOPPED` → `_free_request`：R 的块表倒着交出——先 #2（4+ 个 token、没装满、没哈希）放回空闲队列**队头**，再 #1（有哈希）放到**队尾**，还留在缓存表里。R 从 `requests` 里删掉，退出 running。（格子只画 R 用过的块。）',
             note('文档', '尾块的哈希里含的 token 更多、更不容易被复用，应该先被挤掉。',
                  q='This is because the last block of a request must hash more tokens and is less likely to be reused by other requests.', ref=A(PC, 198)),
             focus=['update', 'w_free', 'kvm', 'pool', 'running'],
             state={'update': up('<eos>', 'EOS→停'),
                    'running': rn(('L', 'decode')),
                    'pool': pool(cell('#2', '空', 'mv'), cell('#3', '空'), cell('#1', '缓存', 'ghost'), right='队尾'),
                    'kvm': g_checks([('R 的块已还', 'ok')]),
                    'req': g_kv([('R', '已结束'), ('块', '已还')])}),
        beat('S8 的结果回来：查无此人，丢掉',
             'S8 在 R 结束之前就排出去了，它算出的第 8 个 token 现在才回来：`requests.get` 拿到 `None` → `continue`，整条丢掉。这是 EOS 躲不掉的代价：排 S8 时还不知道第 7 个是 `<eos>`。代价是多算 1 个 token 的前向；它和同批别的请求共用这一步，边际成本小（推断）。',
             note('推断', '注释只说了 abort 会走这条分支；EOS 之后的那一步也走它，是从代码路径推出来的，没有专门的注释或测试。'),
             focus=['update', 'w_gpu', 'out'],
             state={'update': up('S8 的 token', 'R 不在→丢')}),
        # ---- 场景 A：前缀缓存
        beat('场景 A（设）：同样的 20 token 再来一次',
             '另一个客户端发来完全相同的 prompt：R2。查缓存：上限 19 → 至多 1 块；第 1 块的哈希命中 **#1**（R 留下的）→ 已算 = **16**。待算 20 − 16 = 4，只排 **4** 个，预算只花 4。账房先 `touch` #1（ref 0 → 1，从空闲队列摘出），再新分 1 块装这 4 个。',
             note('注释', '先 touch 命中的块，保证它们不会被挤掉。', q="Touch the computed blocks to make sure they won't be evicted.", ref=A(STM, 414)),
             focus=['kvm', 'w_take', 'pool', 'calc', 'w_wait', 'waiting'],
             state={'waiting': q(),
                    'running': rn(('L', '排 1'), ('R2', '排 4')),
                    'kvm': g_checks([('#1 哈希命中', 'ok'), ('已算 = 16', 'ok'), ('再分 1 块', 'ok')]),
                    'calc': calc('20 − 16', '2047', '4'),
                    'budget': bud(5),
                    'pool': pool(cell('#1', 'R2', 'pick'), cell('新', '4个', 'new'), cell('#3', '空')),
                    'out': wo('R2：4', 'L1'),
                    'req': rq('20', '16→20', '0→1')}),
        # ---- 场景 C：抢占
        beat('场景 C（换一套设定）：池子满了',
             '设：`block_size=16`，池里共 10 个可用块；A、B 各 80 token 的 prompt，各占 5 块，池子空了；A 先进 running。A 的下一个 decode 要 81 个槽位 → 要第 **6** 块 → `allocate_slots` 返回 **`None`**。',
             note('测试', '同尺寸的测试：块 0 被 null_block 占着，11 块里实际可用 10 块。',
                  q='because block 0 is reserved as the null block.', ref=A(TS, 2496)),
             focus=['kvm', 'calc', 'running', 'pool'],
             state={'waiting': q(),
                    'running': rn(('A', '要第6块'), ('B', '已算 80')),
                    'calc': calc('1', '2048', '1', la='A 待算'),
                    'budget': bud(0),
                    'kvm': g_checks([('A 要第 6 块', 'no'), ('空闲 0 块', 'no')]),
                    'pool': pool(cell('A', '5块'), cell('B', '5块'), cell('空', '0'), left='已用'),
                    'out': wo('—', '—'),
                    'update': up('—', '—'),
                    'req': g_kv([('A 已算', '80'), ('B 已算', '80'), ('空闲', '0 块')])}),
        beat('抢占 running[-1] = B',
             'FCFS 踢 running 最后一个：B。B 的 5 块全部还回去；B 状态 `PREEMPTED`、已算 **0**、占位清零，插回 waiting **队首**。再试一次：A 拿到第 6 块，排上 1 个。',
             note('文档', 'FCFS 下，KV cache 用完时踢的是最后进入 running 的请求。',
                  q='the KV cache is exhausted, the last-admitted running request is preempted.', ref=A(ARCH, 151)),
             focus=['w_pre', 'running', 'waiting', 'pool', 'kvm'],
             state={'running': rn(('A', '排 1')),
                    'waiting': q(cell('B', '0', 'new')),
                    'kvm': g_checks([('踢 B，还 5 块', 'ok'), ('A 拿到第 6 块', 'ok')]),
                    'pool': pool(cell('A', '6块', 'pick'), cell('空', '4块', 'mv'), left='已用'),
                    'budget': bud(1),
                    'out': wo('—', 'A：1'),
                    'req': g_kv([('A 已算', '81'), ('B 已算', '0'), ('空闲', '4 块')])}),
        beat('这一步不再排 waiting；B 下一步重新入场',
             '本步 `preempted_reqs` 不空 → 整个 waiting 循环跳过，B 这一步不动。下一步起 B 重新入场：已算 = 0 → 查前缀缓存 → 重算 80 + 已生成的 token，减去还在缓存里的整块；作为 `scheduled_new_reqs` 再发一次完整数据。',
             note('推断', 'B 的 5 块倒着放到队尾：#5、#4…#1；A 从队头弹走的正是 B 的尾块 #5，前 4 块还在缓存里，B 回来能直接命中 64 个 token。这正是「倒着还」想要的效果。这一串是按规则推出来的，底稿和测试都没有逐块断言。'),
             focus=['waiting', 'w_wait', 'calc', 'out'],
             state={'calc': calc('—', '—', '不排', la='B', lb='原因')}),
    ]

    return scene(1220, 520, parts, wires, steps, mw=340, mh=1212,
                 cap='一次 schedule()：先 running、再 waiting；块不够就抢占。block_size=16、预算 2048 是本页设的。')


# ---------------------------------------------------------------- 源码页
def _loop_src():
    return node(
        'sched.loop', 'schedule() 主线', 'schedule()：设预算，先排 running',
        '`schedule()` 的开头和 running 循环：没有 prefill/decode 之分的那段注释、两个预算、`max_tokens` 守卫、待算公式。',
        lead='你在「调度器」框里的「定这步排几个」「本步 token 预算」「运行队列」这几块后面。`schedule()` 一千多行，这里只留主线；连接器、Mamba、编码器、DP 节流等旁支都省略了。',
        src=SCHED + ':702-993',
        blocks=[
            code(SCHED, [(702, 714), (728, 734)], notes=[
                at(705, '注释', '整个设计的钥匙：没有 prefill / decode 阶段，每个请求只有「已算」和「已有」，调度器每步让前者追后者。',
                   q='There\'s no "decoding phase" nor "prefill phase" in the scheduler.', has='There\'s no "decoding phase"'),
                at((728, 731), None, '两个预算：没开投机解码时 `draft_slots = 0`，两个数相同。本页设 2048。', has='token_budget = self.max_num_scheduled_tokens'),
            ], cap='开头：设计说明 + 设预算'),
            code(SCHED, [(778, 799), (824, 842), (876, 894)], notes=[
                at(780, None, '先排 running；预算用完就停，没排上的留在原地，下一步再来。', has='token_budget > 0'),
                at((785, 799), '注释', '`max_tokens` 守卫：在飞的几步已确定凑满 `max_tokens`，就不再多排。本例排 S8 前：26 + (1+1) − 1 = 27 < 28 → 照排；假如要排 S9：27 + 2 − 1 = 28 ≥ 28 → 跳过。',
                   q='Async scheduling: Avoid scheduling an extra step when we are sure that', has='request.num_prompt_tokens + request.max_tokens'),
                at((824, 828), None, '待算 = 已有 + 占位 − 已算。本例 S1 之后每步：x + 1 − x = 1。', has='request.num_output_placeholders'),
                at((829, 833), None, '先按长 prefill 阈值封顶（默认关），再按剩余预算封顶——分块 prefill 就是这个 `min`。', has='input_budget - draft_slots'),
                at((835, 842), '注释', '不越过 `max_model_len`（投机解码时才可能碰到）。',
                   q='Make sure the input position does not exceed the max model len.', has='self.max_model_len'),
                at((890, 894), '注释', '这个请求这一步排不了：跳过它，接着排后面的，不严格守先来先服务。',
                   q='we do not strictly follow the FCFS scheduling policy and', has='req_index += 1'),
            ], cap='running 循环：守卫 → 待算 → 封顶'),
            code(SCHED, [(985, 993)], notes=[
                at((989, 991), None, '分到块之后才算排上：记进工单、扣预算。本例 S1 之后每步扣 1。', has='token_budget -= num_new_tokens'),
            ], cap='排上了：记账'),
        ])


def _preempt_src():
    return node(
        'sched.preempt', '抢占源码', '抢占：踢最晚进的，清零，插回队首',
        'running 循环里要不到块时的 `while True` 抢占循环，和 `_preempt_request` 对被踢请求做的事。',
        lead='你在「调度器」框里那条「抢占：踢回队首」的线后面。两段源码：要不到块时怎么选人、踢人；被踢的请求身上发生了什么。',
        src=SCHED + ':896-983, 1831-1876',
        blocks=[
            code(SCHED, [(896, 983)], notes=[
                at((899, 903), None, '向账房要块；拿到 `None` 就往下进抢占。场景 C：A 要第 6 块，池子空了。', has='allocate_slots('),
                at((915, 923), '文档', '选谁：FCFS 踢 running 最后一个（最晚进的）；priority 踢优先级最低的。场景 C 里是 B。',
                   q='the last-admitted running request is preempted.', ref=A(ARCH, 151), has='self.running[-1]'),
                at((925, 927), '注释', '受害者的块此刻还不能还（只在 KV 连接器 + 多批在飞时出现）就放弃：推迟的释放帮不了眼下的分配。',
                   q='A deferred free will not help with immediate allocation.', has='_request_blocks_can_be_freed'),
                at((929, 933), '推断', 'priority 下删的是 running 中间的元素；它若在当前下标前面，下标要往回挪一格，否则会漏排一个请求。', has='req_index -= 1'),
                at((935, 941), None, 'priority 下被踢的人若这一步已经排过，把它的份额撤销、预算退回。', has='token_budget += restored'),
                at(959, None, 'FCFS：直接从 running 尾部弹出。', has='self.running.pop()'),
                at((961, 966), None, '真正的抢占（见下一段）；记进 `preempted_reqs`，这一步因此不再排 waiting。', has='preempted_reqs.append'),
                at((967, 969), '注释', '踢到的正是自己：没人可踢了，这个请求这一步排不了。',
                   q='No more request to preempt. Cannot schedule this request.', has='if preempted_req == request:'),
                at((982, 983), None, '分不到块：整个 running 循环结束。', has='break'),
            ], cap='要不到块就踢人，直到分到或踢到自己'),
            code(SCHED, [(1831, 1834), (1849, 1854), (1857, 1868), (1875, 1876)], notes=[
                at(1849, None, '还掉它全部的块。', has='self._free_request_blocks(request)'),
                at((1853, 1854), None, '状态改为 PREEMPTED，已算清零：重新入场时从头算（前缀缓存命中的部分除外）。', has='request.num_computed_tokens = 0'),
                at((1857, 1867), '注释', '还在飞的输出标成「过期」：回来时照样交付，但不许改已清零的计数器。',
                   q='Async scheduling: mark all in-flight output as stale. Its tokens are', has='num_stale_output_tokens = request.num_in_flight_tokens'),
                at(1868, None, '占位清零。', has='request.num_output_placeholders = 0'),
                at(1876, None, '插回 waiting 队首（FCFS 是 deque 的 `appendleft`）。', has='prepend_request'),
            ], cap='_preempt_request：还块、清零、插回队首'),
        ])


def _admit_src():
    return node(
        'sched.admit', 'waiting 入场源码', 'waiting：查缓存、定几个、要块、入场',
        'waiting 循环的主线：抢占过就整段跳过；只对「已算 = 0」的查前缀缓存；分不到块就停；分到了就进 running。',
        lead='你在「调度器」框里「等待队列」和「② 没抢占才排」「入场」两条线后面。这一段夹着大量 KV 连接器、Mamba、投机填充的旁支，这里只摘主线的几行。',
        src=SCHED + ':1040-1624',
        blocks=[
            code(SCHED, [(1040, 1041), (1073, 1076)], notes=[
                at(1041, '推断', '这一步发生过抢占，就跳过整个 waiting 循环：显存紧时不再放新请求进来。仓库里这行没有注释。', has='if not preempted_reqs'),
                at((1073, 1076), None, 'running 满了（`max_num_seqs`）或预算用完：不再取新请求。', has='num_running >= self.max_num_active_reqs'),
            ], cap='入口：两道门'),
            code(SCHED, [(1131, 1149), (1238, 1241)], notes=[
                at(1132, None, '只对「已算 = 0」的查前缀缓存：新来的，和被抢占清零的。', has='if request.num_computed_tokens == 0:'),
                at(1149, None, '查本地前缀缓存，返回命中的块和命中的 token 数。本例 R 命中 0；场景 A 的 R2 命中 16。', has='_get_local_prefix_cache_hit'),
                at((1239, 1241), None, '命中数（本地 + 外部）就当作「已算」。', has='num_new_local_computed_tokens + num_external_computed_tokens'),
            ], cap='查前缀缓存'),
            code(SCHED, [(1344, 1348), (1374, 1389)], notes=[
                at((1345, 1348), '注释', '用 `num_tokens` 而不是 prompt 长度：被抢占回来的请求，已生成的 token 也要重算。',
                   q='`request.num_prompt_tokens` to consider the resumed', has='request.num_tokens - num_computed_tokens'),
                at((1384, 1386), '注释', '关了分块 prefill：装不下就整个停下，不切块。', q='If chunked_prefill is disabled,', has='break'),
                at(1388, None, '和这个请求能用的预算（`request_token_budget`，在 1337 行取 `token_budget` 等的较小值）取小。场景 B 里 L：`min(5000, 2047)` = 2047。', has='min(num_new_tokens, request_token_budget)'),
            ], cap='定排几个'),
            code(SCHED, [(1489, 1515)], notes=[
                at(1503, '文档', '整条 prompt 的块都放得下才放行（默认开）。',
                   q='checking the first chunk. Prevents over-admission and KV cache thrashing', ref=A(CFGS, 229), has='full_sequence_must_fit'),
                at((1508, 1515), '推断', '分不到块就 `break`：队首卡住，后面的也不排（严格 FCFS；仓库里没有注释说明）。', has='if new_blocks is None:'),
            ], cap='要块：分不到就停'),
            code(SCHED, [(1596, 1598), (1606, 1613), (1620, 1624)], notes=[
                at((1596, 1598), None, '入场：弹出 waiting，加到 running 末尾。', has='self.running.append(request)'),
                at((1606, 1613), '注释', '被抢占回来的请求也走这里，像新请求一样重发完整数据。',
                   q='A resumed request is re-sent in full, like a new one: its', has='scheduled_new_reqs.append(request)'),
                at((1620, 1624), None, '扣预算、状态改 RUNNING、「已算」= 命中数（本例 R 是 0，R2 是 16）。', has='request.num_computed_tokens = num_computed_tokens'),
            ], cap='入场'),
        ])


def _kv_src():
    return node(
        'sched.kv', 'KV 块源码', 'KV 块：查命中、分块、倒序还',
        '`get_computed_blocks`（查前缀缓存）、`allocate_slots`（分块，不够返回 None）、`BlockPool` 的弹块 / touch / 还块。',
        lead='你在「调度器」框里的「KV 账房」「块仓库」以及「要块」「取块」「还块」几条线后面。四段源码按一次请求的一生排：查命中 → 分块 → 仓库进出。',
        src=KVM + ':278-639, ' + BP + ':681-837',
        blocks=[
            code(KVM, [(301, 319)], notes=[
                at((308, 313), '注释', '最后一个 token 必须重算才能拿到 logits，所以命中上限是「已有 − 1」；而命中只按整块算，于是可能要重算一整块。场景 A：上限 19 → 命中 1 块 = 16。',
                   q='to obtain logits. Thus, set max_cache_hit_length to prompt_length - 1.', has='max_cache_hit_length'),
                at((315, 319), None, '顺着请求的块哈希链找最长命中，见下一段。', has='find_longest_cache_hit'),
            ], cap='get_computed_blocks：命中上限'),
            code(STM, [(921, 929)], notes=[
                at((921, 926), '注释', '哈希是链式的（每块的哈希里含父块的哈希）：某一块没命中，后面的一定也没命中，直接停。所以命中一定是**前缀**。',
                   q='A missing block implies every later block misses too (chained hashes).', has='break'),
            ], cap='找最长命中'),
            code(KVM, [(542, 564), (593, 621), (628, 639)], notes=[
                at((542, 559), '文档', 'waiting 的请求：整条 prompt 要的块都放得下才放行。',
                   q='checking the first chunk. Prevents over-admission and KV cache thrashing', ref=A(CFGS, 229), has='full_sequence_must_fit'),
                at((561, 564), None, '需要的槽位 = 已算 + 本步排（+ lookahead）。本例 S1：0 + 20 = 20 → 2 块（block_size 设 16）；S2：21，仍是 2 块。', has='num_tokens_need_slot'),
                at((593, 599), '注释', '空闲块不够就返回 `None`：调度器据此去抢占或停下。',
                   q='Keep `reserved_blocks` free for other in-flight sequences, and an', has='return None'),
                at((605, 614), '注释', '先把命中的块挂上，再申请新块。',
                   q='avoid the case where the new blocks cannot be allocated.', has='allocate_new_computed_blocks'),
                at((616, 621), None, '申请新块：最终从仓库空闲队列头部弹出。', has='allocate_new_blocks'),
                at((628, 637), '注释', '只把已确认 token 所在的整块登记进前缀缓存。本例 S1：20 个 token → #1（16 个）登记，#2（4 个）不登记。',
                   q='Therefore, we cap the number at `request.num_tokens`, ensuring only', has='cache_blocks'),
            ], cap='allocate_slots：闸门 → 算块数 → 不够返回 None → 挂命中块 → 分新块 → 登记缓存'),
            code(BP, [(693, 696), (710, 715), (783, 790), (812, 837)], notes=[
                at(696, None, '分块：从空闲队列头部弹。', has='popleft_n'),
                at((710, 713), None, '弹出的块若还挂着旧哈希，这时才从缓存表里摘掉——还块不是清空，被弹出才是。', has='_maybe_evict_cached_block'),
                at((786, 790), '注释', '`touch`：`ref_cnt` 为 0 说明它在空闲队列里（随时可能被弹走），先摘出来，再 +1。',
                   q='ref_cnt=0 means this block is in the free list (i.e. eviction', has='self.free_block_queue.remove(block)'),
                at((826, 829), '注释', '没哈希的块放队头：后进先出，GPU 局部性更好。本例 R 的 #2。',
                   q='LIFO reuse of non-cached blocks for better GPU locality,', has='blocks_to_evict_first.append(block)'),
                at((830, 832), '注释', '有哈希的块放队尾：先进先出 = LRU。本例 R 的 #1，留着给场景 A 命中。',
                   q='FIFO reuse of cached blocks for LRU eviction behavior.', has='blocks_to_evict_last.append(block)'),
            ], cap='BlockPool：弹块、touch、还块'),
        ])


def _ph_src():
    return node(
        'sched.ph', '占位与收结果源码', '占位：排完就记，回来就消',
        '`_update_after_schedule` 推进「已算」、记占位；`update_from_output` 追加 token、判停、消占位，找不到请求就丢。',
        lead='你在「调度器」框里的「请求的三个数」「本步工单」「收结果」后面。四段源码按时间排：排完推进 → 记占位 → 结果回来先找人 → 追加、判停、消占位。',
        src=SCHED + ':1879-1899, 2401-2423, 2933-2959; ' + ASCH + ':18-48',
        blocks=[
            code(SCHED, [(1879, 1899)], notes=[
                at((1880, 1888), '注释', '排完就推进「已算」：工单里要带推进前的值；推进后下一步能马上再排；投机 token 被拒时再回退。',
                   q='Advance the number of computed tokens for the request AFTER', has='Advance the number'),
                at(1892, None, '本例 S1：0 → 20；之后每步 +1。', has='request.num_computed_tokens += num_scheduled_token'),
                at((1897, 1899), None, '还没追上「已有 + 占位」= 分块 prefill 的中间块。场景 B 的 L 在 S3、S4 是。', has='is_prefill_chunk'),
            ], cap='_update_after_schedule：推进「已算」'),
            code(ASCH, [(18, 19), (29, 48)], notes=[
                at((31, 32), None, '中间块这一步不采样，不记占位。', has='if request.is_prefill_chunk:'),
                at((45, 47), None, '这一步会采样：占位 + 1（没开投机解码）。本例 S1 之后占位 1；稳态下排完是 2，结果回来后是 1。', has='request.num_output_placeholders +='),
            ], cap='AsyncScheduler：记占位'),
            code(SCHED, [(2401, 2423)], notes=[
                at(2403, None, '按 id 找请求；已经结束、从总表删掉的，拿到 `None`。', has='self.requests.get(req_id)'),
                at((2415, 2423), '注释', '请求已经结束就丢掉这条结果。注释只写了 abort；本例 S8 的结果（R 已因 EOS 结束）也走这里，这一点是推断。',
                   q='The request is already finished. This can happen if the', has='continue'),
            ], cap='update_from_output：先找人'),
            code(SCHED, [(2933, 2959)], notes=[
                at(2942, None, '追加 token：「已有」+1。', has='append_output_token_ids'),
                at((2945, 2946), '注释', '先判停，再做输出：输出里要带结束原因。',
                   q='This must be called before we make the EngineCoreOutput.', has='check_stop('),
                at((2947, 2953), '注释', '一次回来多个 token 且中途停了：截掉后面的，回退「已算」。',
                   q='num_computed_tokens covers all new tokens but the last one.', has='del new_token_ids[num_new:]'),
                at((2955, 2958), '测试', '占位 −N，并断言不为负：这里曾因抢占时的计数错误让 EngineCore 崩溃，现在有回归测试守着。',
                   q='Regression for the num_output_placeholders underflow EngineCore crash:', ref=A(TAS, 578), has='assert request.num_output_placeholders >= 0'),
            ], cap='_update_request_with_output：追加、判停、消占位'),
            code(SUT, [(98, 119)], notes=[
                at((104, 107), None, '第一道：EOS。本例第 7 个 token 在这里命中 → `FINISHED_STOPPED`。', has='eos_token_id'),
                at((114, 119), None, '长度：输出数到 `max_tokens`（本例 8）或总长到 `max_model_len`。', has='request.max_tokens'),
            ], cap='check_stop：判停顺序'),
        ])


def nodes():
    return {'sched': node(
        'sched', '调度器内部', '调度器：一步是怎么排出来的',
        '顶层「调度器」框里面：两个队列、token 预算、KV 账房和块仓库、抢占，以及「已算 / 占位」怎么变。',
        lead=('你在顶层图「调度器」这个框里面。顶层只画了三个数（本步排 / 已算 / 占位）；这里拆开看它们是怎么定出来的。'
              '按 ▶ 接着顶层的例子 R（20 个 prompt token，生成 7 个，最后一个是 `<eos>`，`max_tokens=8`）走一遍，'
              '中间插进三个补设的场景：长 prompt 挤进来、同样的 prompt 再来一次、显存满了要抢占。'),
        src=SCHED + ' schedule() / update_from_output()',
        blocks=[_scene()],
        children=[_loop_src(), _admit_src(), _preempt_src(), _kv_src(), _ph_src()])}
