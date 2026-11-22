# 顶层「调度循环」框的下一层：一圈里的状态怎么变，以及「选批」再下一层，直到源码。
# 事实全部来自 research/manuscript-sched_loop.md（HEAD = c569c43446）；例子沿用顶层：请求 A（rid 9f3c…，prompt 20，命中 5，
# max_new_tokens=32，X5 是 EOS），底稿 §9 补设的更早请求 B（max_new_tokens=8，req_pool_idx=3；A 的是 7）。
from cv import *

SCH = 'python/sglang/srt/managers/scheduler.py'
SBT = 'python/sglang/srt/managers/schedule_batch.py'
OVL = 'python/sglang/srt/managers/overlap_utils.py'
BRP = 'python/sglang/srt/managers/scheduler_components/batch_result_processor.py'
OS_ = 'python/sglang/srt/managers/scheduler_components/output_streamer.py'
RRT = 'python/sglang/srt/managers/scheduler_components/request_retirer.py'
NTR = 'python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py'
SP = 'python/sglang/srt/managers/schedule_policy.py'
PB = 'python/sglang/srt/mem_cache/prefill_budget.py'
SCHED_F = 'python/sglang/srt/arg_groups/fields/schedule.py'
TSKIP = 'test/registered/scheduler/test_overlap_skip_finishing_decode.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数）
def pick_g(a=None, b=None, c=None):
    return g_checks([('上批是 prefill → 并入', a), ('滤掉已结束', b), ('有新 prefill 批？', c)])


def proc_g(a=None, b=None, c=None):
    return g_checks([('还在名单里？', a), ('记 token · 判结束', b), ('发管道 ②', c)])


def roster_g(run, pre):
    return g_kv([('running', run), ('prefill 发射', pre)])


def fmap_g(b, a):
    return g_kv([('[3] B', b), ('[7] A', a)])


# ================================================================ 第一层：调度循环里面
def loop_scene():
    parts = [
        part('wq', '等待队列', [16, 16, 200, 124], m=[8, 8, 130, 140], kind='store', code='waiting_queue',
             glyph=g_cells([cell('A', '新到', 'new')]),
             say='等 prefill 的请求', why='收请求永不卡在显存', trap='退回的也排这里',
             card=card(
                 '还没开始 prefill 的请求名单。管道 ① 抽出来的新请求、显存不够被退回（retract）的请求，都排在这里。',
                 ['每圈开头 `ingest_requests` 非阻塞地把管道抽干，新请求追加进来。' + R(SCH, 2252, 2253),
                  '选批时按策略重排（默认 `fcfs`），再逐个交给预算员试。' + R(SCH, 4651),
                  '队列空、又没有正在分块的请求 → 选批直接跳过 prefill，连预算员都不建。' + R(SCH, 4606, 4609)],
                 [note('推断', '先排队、选批时再按预算挑：「收请求」和「这圈收几个」分开，收请求这一步永远不会因为显存满而卡住。')],
                 '在等待队列里不等于「一个 token 都没生成过」：被退回的请求带着已生成的 token 回到这里，之后重新 prefill。')),
        part('rb', 'running batch', [16, 196, 200, 124], m=[206, 8, 126, 140], kind='store', code='running_batch',
             glyph=g_cells([cell('B', '6/8')]),
             say='正在 decode 的一批', why='一次前向人人长一个', trap='出批 ≠ 出名单',
             card=card(
                 '正在 decode 的那一大批请求（一个 `ScheduleBatch`）。decode 一圈，就是让这批里每个请求各长 1 个 token。格子里写「CPU 已处理的输出数 / max_new_tokens」。',
                 ['新请求 prefill 完，下一圈并进来（`merge_batch`）。' + R(SCH, 4403, 4411),
                  '每圈 `filter_batch()`，只留没结束的。' + R(SCH, 4413, 4418),
                  'decode 前 `prepare_for_decode`：每人分 1 个 KV 槽、`seq_lens+1`。' + R(SCH, 5282)],
                 [note('推断', '把在 decode 的请求攒成一批：一次前向让所有人各长一个 token，GPU 吃得饱。'),
                  note('注释', '每圈都滤，不只在合并之后：overlap 下 prefill 的请求是在它的结果把它判为结束之前并进来的。',
                       q="Every step, not only after a merge: overlap merges a prefill's requests", ref=A(SCH, 4413))],
                 ['running batch ≠ `running_reqs` 名单：第 n+2 圈 B 被剔出这一批（不再发射），却还在名单里等它最后一个 token 的结果。',
                  '格子里的数是 CPU 已处理的；GPU 上总有一步在飞，实际已经多一个。'])),
        part('pick', '选批', [346, 40, 230, 236], m=[8, 260, 260, 180], kind='gate', n='1', code='get_next_batch_to_run',
             glyph=pick_g(),
             say='决定这圈跑 prefill、decode，还是什么都不跑', why='prefill 优先；上一步结果还没看到',
             trap='选批时上一圈的结果还没处理',
             card=card(
                 '决定这一圈跑什么：一个 prefill 批，或者 running batch 的一步 decode；也可能什么都不跑（None）。',
                 ['三步：① 上一圈是 prefill → 把它的请求并进 running batch；② 每圈都把已结束的请求滤掉；③ 有新 prefill 批就跑它，否则 decode 一步。' + R(SCH, 4378, 4456),
                  'decode 前还要剔掉「最后一步已经在飞」的请求、查显存，不够就退回几个。' + R(SCH, 5194, 5283),
                  '它连同新的 running batch 一起返回，循环把它存回去。' + R(SCH, 2272, 2276)],
                 [note('注释', '有新 prefill 就先跑 prefill，否则才 decode。', q='Run prefill first if possible', ref=A(SCH, 4444)),
                  note('推断', '为什么 prefill 优先，底稿没查到明确理由；推测是首 token 延迟最敏感。代价是 prefill 那一圈，running batch 里的请求停一拍。')],
                 '选批时，上一圈发射的批结果还没处理：它看到的 `output_ids` 落后一步。所以要靠 `inflight_steps` 记账、靠每圈过滤补救。'),
             to='sched_loop.pick'),
        part('launch', '发射', [706, 16, 190, 124], m=[8, 514, 124, 130], kind='act', n='2', code='run_batch',
             glyph=g_text('—', '还没选出批'),
             say='交给 GPU，不等', why='三条流各干各的', trap='input_ids 用完即清',
             card=card(
                 '把这一圈的批交给 GPU，**不等算完**就返回。这一层多看一眼它怎么安排 GPU 上的三条流、输入从哪来。',
                 ['循环本身跑在 schedule 流上；前向放到 forward 流，并让它先等 schedule 流。' + R(SCH, 5432, 5433),
                  '先 `resolve_forward_inputs` 备好输入：prefill 从 CPU 暂存拷上去，decode 从 `future_map` 取。' + R(SCH, 5442, 5444),
                  '结果另在 copy 流上异步拷回 CPU，记一个 `copy_done` 事件。' + R(SCH, 5493, 5506),
                  '发射时这批每个请求 `inflight_steps += 1`；结果处理完再减 1。' + R(SBT, 2255, 2262)],
                 [note('注释', '结果拷回放在 copy 流上，和下一批的前向重叠。',
                       q='Issue the result readback on copy_stream so it overlaps the next forward.', ref=A(SCH, 5820)),
                  note('推断', '三条流分开：schedule 流做 CPU 提交的小活，forward 流做大前向，copy 流搬结果；拆开后「搬上一批」和「算下一批」能在 GPU 上并行。')],
                 ['发射完立刻 `batch.input_ids = None`：下一圈的输入不从这里来，从 `future_map` 来。' + R(SCH, 5510, 5511),
                  '紧跟发射还有一道栅栏 `_apply_war_barrier`；它具体保护哪块共享缓冲，底稿没追到。' + R(SCH, 2282, 2283)]),
             to='sched_loop.src_launch'),
        part('gpu', 'GPU 前向', [1016, 16, 190, 124], m=[212, 514, 120, 130], kind='ext',
             glyph=g_text('D_B', 'B 的第 7 步', tone='run'),
             say='算完写两处', why='token 先留 GPU', trap='GPU 总领先一步',
             card=card(
                 '框外的「模型前向 + 采样」（顶层那个框）。在这一层只关心它算完之后的两件事：新 token 写进 `future_map`，同时拷一份回 CPU。',
                 ['采样结果 `next_token_ids` 包成中转数据交给 `future_map`。' + R(SCH, 5764, 5767),
                  '拷回 CPU 走 copy 流，处理结果时才等它。' + R(SCH, 5831)],
                 [note('推断', 'token 先留在 GPU：下一步 decode 要的正是它，绕道 CPU 就得等拷贝。')],
                 '同一时刻，GPU 上的 token 和 CPU 上的 `output_ids` 不一样：GPU 总是领先一步。')),
        part('fmap', 'future_map', [1016, 196, 190, 124], m=[212, 714, 120, 130], kind='store', code='FutureMap',
             glyph=fmap_g('t7', '—'),
             say='按行号存下一步输入', why='行号做下标不用估', trap='不开 overlap 也有',
             card=card(
                 '一张按 `req_pool_idx`（请求占 KV 表的哪一行）索引的 GPU 小表：上一步把采样出的 token 写进去，下一步发射前直接读出来当输入。例子里 B 占第 3 行、A 占第 7 行（设）。',
                 ['主路下它就是一条 `int64` 的 GPU 向量 `output_tokens_buf`，长度 = KV 表的行数。' + R(OVL, 345, 347),
                  '写：`output_tokens_buf[indices] = 采样结果`。' + R(OVL, 800, 802),
                  '读：`input_ids = output_tokens_buf[req_pool_indices]`，GPU 上一次 gather，不经 CPU。' + R(OVL, 165, 166)],
                 [note('注释', '始终开着、按池下标索引的跨圈中转。', q='Always-on pool-indexed relay for cross-iter values.', ref=A(OVL, 310)),
                  note('提交', '最早是环形缓冲、下标是「未来编号」，后来改成按请求行号索引。',
                       q='Address overlap future token map by request-pool index (#25862)', ref='c:512d164916'),
                  note('推断', '按行号做下标：缓冲大小就等于行数，不用估容量；请求中间被 prefill 挡了几圈，它那一格的值也还在。')],
                 ['「关掉 overlap 就没有 future_map」是错的：非 overlap 分支也经它中转，差别只是没有单独的流、不排 `result_queue`。' + R(SCH, 5629, 5638),
                  '序列长度不用中转：每步 +1，CPU 在 `prepare_for_decode` 里自己就加了。CPU 缺的只是 token 的值。' + R(SBT, 3353, 3354)]),
             to='sched_loop.src_launch'),
        part('rq', 'result_queue', [706, 196, 190, 124], m=[8, 714, 124, 130], kind='store', code='result_queue',
             glyph=g_cells([cell('D_B', '[B]')], left='队头'),
             say='发了、没处理的批', why='存发射那刻的快照', trap='队头才是上一圈',
             card=card(
                 '已经发射、结果还没处理的批：每一项是「发射那一刻的批快照 + 结果句柄」。队头就是「上一圈」。',
                 ['发射后 `append((batch.copy(), batch_result))`。' + R(SCH, 2284),
                  '同一圈稍后 `popleft()` 取队头处理。' + R(SCH, 2242, 2245),
                  '只要上一圈发射过（`last_batch` 不空），这一圈就一定处理一批，哪怕这圈自己没发射。' + R(SCH, 2290, 2295)],
                 [note('注释', '这个循环让 CPU 处理和 GPU 计算重叠。', q='A scheduler loop that overlaps the CPU processing and GPU computation.', ref=A(SCH, 2236)),
                  note('注释', '入队的是快照：请求列表另切一份，保证不和原 batch 共用（下一圈选批会改原 batch）。',
                       q='Shallow-copy the reqs list as a defensive snapshot.', ref=A(SBT, 3567))],
                 ['有时先处理、再选批：默认只在上一批需要语法同步时这样，好让它结束的请求不再被发射。' + R(SCH, 2307, 2326)]),
             to='sched_loop.src_loop'),
        part('proc', '处理上一圈', [706, 376, 190, 140], m=[8, 914, 250, 150], kind='act', n='3', code='process_batch_result',
             glyph=proc_g(),
             say='查名单、记 token、判结束、发出', why='发射后才离开的行，一概不算', trap='白算那步白的是算力',
             card=card(
                 '处理上一圈的结果：哪些行还算数、给每个请求记 token、判断结束、决定发不发给反分词。',
                 ['先 `copy_done.synchronize()`：CPU 只在这里真正等 GPU，等的是上一批拷贝的尾巴——这时下一批已经在 GPU 上跑。' + R(BRP, 1156, 1157),
                  '`still_in_batch`：请求已不在任何名单里 → 这一行直接跳过。' + R(BRP, 1214, 1217),
                  '还在的：`output_ids` 追加 → `update_finish_state` → 结束就 `release`（摘名单、KV 交给前缀树）。' + R(BRP, 1243, 1254),
                  '整批只调一次 `stream_output`，逐请求决定发不发。' + R(BRP, 1298)],
                 [note('注释', '发射后才离开的行（结束、被退回、被中止）：什么都不提交、不发送。',
                       q='still in the batch. A row whose request left after launch (finished,', ref=A(BRP, 351)),
                  note('提交', '「只在还在批里的行上提交」是专门收拢成一处判断的。',
                       q="[Refactor] scheduler: Commit a step's result only on the rows of requests still in the batch (#49019)", ref='c:d1d317c64d')],
                 ['EOS 白算的那一步，白的是**算力**：按代码推算，它的 KV 随请求一起交给了前缀树（`owned=25`），不是单独回收。这是研究员从代码算的，没跑过；顶层卡里「多分的槽一并回收」的说法不够准确。',
                  '判结束的顺序：stop 串 → EOS / stop token → 长度。' + R(SBT, 1503, 1518)]),
             to='sched_loop.src_result'),
        part('roster', '名单', [346, 376, 230, 140], m=[8, 1134, 124, 140], kind='store', code='running_reqs …',
             glyph=roster_g('B', '—'),
             say='谁还活着', why='一处判活', trap='快照≠现在',
             card=card(
                 '每个活着的请求挂在哪张名单上：`running_reqs`（在 decode）/ `prefill_launched_reqs`（prefill 已发射）/ `chunked_reqs`（正在分块 prefill）。',
                 ['`move(...)` 在名单之间搬：prefill 批并入时挪进 `running_reqs`。' + R(SCH, 4403, 4406),
                  '结束时 `release` 先把请求从名单摘掉，再释放 KV。' + R(RRT, 91, 93),
                  '处理结果时的 `still_in_batch`，就是查它在不在这三张名单里。' + R(SCH, 5109, 5118)],
                 [note('注释', '活着的请求按种类放在几张名单里，一个请求最多在其中一张。',
                       q='The lists that hold the live requests, by kind. A request is in at', ref=A(SCH, 5075)),
                  note('推断', '有了这条不变式，「这一行结果还算不算数」只要查一下名单，不用在各处分别判断结束 / 退回 / 中止。')],
                 '结果快照里的请求是「发射那一刻」的，名单是「现在」的。两者对不上的那一行，就是发射后才离开的请求。')),
        part('detok', '反分词', [1016, 386, 190, 120], m=[212, 1134, 120, 140], kind='out',
             glyph=g_text('—', '等 token id'),
             say='管道 ② 那头', why='一圈一条消息', trap='白算行到不了',
             card=card(
                 '框外的反分词进程（顶层那个框）。这一层只看「发不发」：`stream_output` 对每个请求先问一句 `accept`。',
                 ['结束的必发；流式默认每步都发，第一个 token 立刻发。' + R(OS_, 583, 606),
                  '尾部可能是 stop 串的前缀时先憋着不发。' + R(OS_, 608, 610)],
                 [note('推断', '整批一条消息：一圈只过一次管道 ②，不是每个请求各发各的。')],
                 '白算的那一行到不了这里：它在 `still_in_batch` 那一步就被丢了。')),
    ]

    wires = [
        wire('w_take', 'wq', 'pick', '取新请求', mlp=[72, 176], why='逐个试，够了就停', trap='收不下的留在队里',
             card=card('选批时把等待队列里的请求逐个交给预算员试。',
                       '每个请求先做前缀匹配，再 `add_one_req` 判预算；不再是 `CONTINUE` 就停。' + R(SCH, 4867),
                       [note('推断', '逐个试、到不够就停：先来的先服务（默认 fcfs），一批收几个由预算决定。')],
                       '收不下的请求留在队里，下一圈再试。')),
        wire('w_retract', 'pick', 'wq', '显存不够·退回', ml='不够·退回', mlp=[72, 230], cls='dash', why='退的不进树，立刻腾地', trap='例子里没发生',
             card=card('decode 前发现显存不够下一步：挑几个请求退回等待队列（retract），释放它们的 KV。',
                       ['先退「已生成最少、输入最长」的。' + R(SBT, 3126, 3127),
                        '退回的请求保留已生成的 token，以后重新 prefill。' + R(SCH, 5270, 5271)],
                       [note('注释', '退的时候不插进前缀树：空间要马上用。',
                             q="release memory and don't insert into the tree because we need the space instantly", ref=A(SBT, 3057))],
                       'A、B 的例子里没发生退回；细节在下一层「选批」的「退回」框。')),
        wire('w_merge', 'pick', 'rb', '并入·过滤·剔除', ml='并入·过滤', why='每圈对一次账', trap='剔出批≠结束',
             card=card('选批会就地改 running batch：并入上一圈的 prefill 请求、滤掉已结束的、decode 前剔掉最后一步已在飞的。',
                       ['并入：' + R(SCH, 4410, 4411) + '；过滤：' + R(SCH, 4416) + '；剔除：' + R(SCH, 5198, 5199)],
                       [note('推断', 'overlap 下选批看不到上一圈的结果，所以每圈都要对一次账：已知结束的滤掉，可预知结束的剔掉。')],
                       '剔出这一批 ≠ 结束：B 被剔后还在名单里，等它最后一个 token 的结果。')),
        wire('w_batch', 'pick', 'launch', '本圈的批', why='prefill、decode 不混', trap='可能是 None',
             card=card('选出的这一批交给 `run_batch`；也可能是 None，这圈不发射。',
                       '有新 prefill 批就是它，否则 decode 批，都没有就 None。' + R(SCH, 4443, 4456),
                       [note('推断', '默认一圈只做一种：要么整批 prefill，要么整批 decode。prefill 和 decode 混在一批（`enable_mixed_chunk`）是可选项，默认关。')],
                       'None 不等于这圈闲着：上一圈发射过的话，这圈照样处理它的结果。')),
        wire('w_fwd', 'launch', 'gpu', '前向·不等', why='CPU 腾出手干别的',
             card=card('在 forward 流上排队前向，CPU 立刻返回。',
                       '`forward_batch_generation` 在 forward 流的上下文里调用。' + R(SCH, 5470, 5472),
                       [note('推断', 'CPU 不等 GPU，才腾得出手去处理上一批的结果。')])),
        wire('w_relay', 'gpu', 'fmap', '写下一步输入', ml='写 token', why='token 不回 CPU',
             card=card('采样出的 token 不回 CPU，直接按 `req_pool_idx` 写进 future_map。',
                       ['包成 `RelayPayload(bonus_tokens=next_token_ids)` 交给 `future_map.relay`。' + R(SCH, 5766, 5767) + ' ' + R(SCH, 5777)],
                       [note('注释', '把这一圈的中转数据存好，给下一圈的 `resolve_forward_inputs` 用。',
                             q="Stash this iter's relay payload for next iter's resolve_forward_inputs.", ref=A(SCH, 5758))])),
        wire('w_read', 'fmap', 'launch', '下一圈取输入', ml='取输入', fs='l', ts='r', why='GPU 上 gather', trap='prefill 不走这里',
             card=card('下一圈发射 decode 前，用这批请求的 `req_pool_idx` 从 future_map 里 gather 出输入 token。',
                       R(OVL, 165, 166),
                       [note('注释', 'decode 从 FutureMap 取上一圈采样出的 token。',
                             q="Decode/spec_v2: gather from FutureMap (last iter's sampled token).", ref=A(OVL, 133))],
                       'prefill 的输入不走这里：从固定内存的 CPU 暂存拷上去。' + R(OVL, 141, 142))),
        wire('w_enq', 'launch', 'rq', '快照入队', why='只带结果要用的',
             card=card('发射后把 `batch.copy()` 和结果句柄一起排进 result_queue。',
                       ['快照只带结果处理要用的字段，连「这一步发射了哪些请求」（`launched_reqs`）一起带走。' + R(SBT, 3571, 3573)],
                       [note('注释', '快照只含结果处理会用到的字段。', q='Only contain fields that will be used by process_batch_result.', ref=A(SBT, 3566))])),
        wire('w_pop', 'rq', 'proc', '同圈稍后取出', ml='稍后取出', why='处理时下一批已在算', trap='不 overlap 是紧挨着',
             card=card('同一圈发射完新批之后，才取出队头（上一圈的批）处理。',
                       R(SCH, 2289, 2292),
                       [note('注释', '这些 CPU 活在下一批的 GPU 前向进行中时做，等于白捡的重叠。',
                             q="the next batch's GPU forward is in flight, giving free overlap.", ref=A(SCH, 5882))],
                       '不 overlap 的版本：发射完立刻处理同一批，两行紧挨着。' + R(SCH, 2223, 2224))),
        wire('w_out', 'proc', 'detok', '② token id', why='只发还在的请求',
             card=card('还在批里的请求，按 `accept` 的判断发出新 token。',
                       ['整批一次 `stream_output`。' + R(BRP, 1298), '发 `output_ids_through_stop[send_token_offset:]`，再推进偏移。' + R(OS_, 627, 630)],
                       [note('推断', '只对还在的请求发：白算那一行不会多吐一个 token 给客户端。')])),
        wire('w_list', 'proc', 'roster', '查名单·结束摘掉', ml='查·摘名单', why='判活只看名单',
             card=card('处理每一行前查「请求还在名单里吗」；请求结束时把它从名单摘掉。',
                       ['查：' + R(BRP, 349, 354) + '；摘：' + R(RRT, 91, 93)],
                       [note('注释', '每一行都对照「现在还在批里的请求」检查。',
                             q="Each row of a launched step's frozen batch, checked against the requests", ref=A(BRP, 350))],
                       '摘掉名单在先、从 running batch 滤掉在后（下一圈选批时）。')),
    ]

    steps = [
        beat('第 n 圈开头：A 进等待队列',
             'B 比 A 早到：`max_new_tokens=8`，已处理 6 个输出；它的第 7 步 D_B 上一圈已发射、还排在 `result_queue` 里，B 的第 7 个 token 已写进 `future_map[3]`。这时 `ingest_requests` 抽到 A（rid `9f3c…`，prompt 20 token），放进 `waiting_queue=[A]`。',
             note('推断', '新请求先进等待队列，不直接进 running batch：要等选批时看预算够不够。'),
             focus=['wq', 'rq', 'rb', 'fmap']),
        beat('第 n 圈 · 选批：先跑 A 的 prefill',
             '`last_batch`=D_B 是 decode，不用并；过滤 running batch：B 没结束，不变。等待队列里有 A → 拼 prefill 批：前缀命中 5，只算 15 个，预算员收下 → **P=[A]**。有了 prefill 批就不 decode：B 这一圈**停一拍**。',
             note('注释', '能跑 prefill 就先跑 prefill。', q='Run prefill first if possible', ref=A(SCH, 4444)),
             focus=['pick', 'w_take', 'wq', 'rb'],
             state={'pick': pick_g('skip', 'ok', 'ok'), 'wq': g_cells([cell('A', '取走', 'pick')]),
                    'launch': g_text('P=[A]', '本圈的批')}),
        beat('第 n 圈 · 发射 P，快照入队，不等',
             '`run_batch(P)`：A 的 15 个输入从 CPU 暂存拷上 GPU，前向在 forward 流上排队，**CPU 不等**。A 记 `inflight_steps=1`，挂进「prefill 已发射」名单。GPU 采样出的 X1 不回 CPU，写进 `future_map[7]`。`batch.copy()` 进 `result_queue`：此刻队里是 **[D_B, P]**。',
             note('推断', '先发射、后处理：GPU 一拿到 P 就开算，CPU 回头去收拾 D_B 的结果。'),
             focus=['launch', 'w_fwd', 'gpu', 'w_relay', 'fmap', 'w_enq', 'rq'],
             state={'launch': g_text('P=[A]', '已发射，不等', tone='run'),
                    'gpu': g_text('P', 'A 的 15 个位置', tone='run'),
                    'fmap': fmap_g('t7', 'X1'),
                    'rq': g_cells([cell('D_B', '[B]'), cell('P', '[A]', 'new')], left='队头'),
                    'roster': roster_g('B', 'A'),
                    'wq': g_cells([cell()])}),
        beat('第 n 圈 · 处理上一圈：D_B',
             '发射完才回头处理队头 D_B：`copy_done.synchronize()` 等 B 的 token 拷回 CPU——GPU 这时已经在算 P。B 还在名单里 → 记下第 7 个 token：7 个输出，`7 < 8` 没结束；`inflight_steps` 1→0；发管道 ②。队里剩 **[P]**，`last_batch=P`。',
             note('注释', '这些 CPU 活在下一批的 GPU 前向进行中时做。', q="the next batch's GPU forward is in flight, giving free overlap.", ref=A(SCH, 5882)),
             focus=['proc', 'w_pop', 'rq', 'w_out', 'detok'],
             state={'proc': proc_g('ok', 'ok', 'ok'), 'rq': g_cells([cell('P', '[A]')], left='队头'),
                    'rb': g_cells([cell('B', '7/8')]), 'detok': g_text('B · t7', '这一圈发出'),
                    'launch': g_text('—', '等下一圈')}),
        beat('第 n+1 圈 · 选批：A 并进来，X1 还没处理',
             '`last_batch`=P 是 prefill → A 从「prefill 已发射」挪进 `running_reqs`，并入 running batch = **[B, A]**。注意：P 的结果此刻**还没处理**，A 的第一个 token 是不是 EOS，选批时不知道。没有新请求 → decode：B 在飞 0 步、A 离 32 还远，都不剔；显存够，A 的 seq_len 20→21。得到 **D1=[B, A]**。',
             note('注释', '每圈都滤：overlap 在 prefill 的结果把请求判为结束之前就把它们并进来了。',
                  q="Every step, not only after a merge: overlap merges a prefill's requests", ref=A(SCH, 4413)),
             focus=['pick', 'w_merge', 'rb', 'roster'],
             state={'pick': pick_g('ok', 'ok', 'no'), 'rb': g_cells([cell('B', '7/8'), cell('A', '0/32', 'new')]),
                    'roster': roster_g('B, A', '—'), 'launch': g_text('D1=[B,A]', '本圈的批'),
                    'proc': proc_g()}),
        beat('第 n+1 圈 · 发射 D1：输入从 future_map 取',
             '发射前 `resolve_forward_inputs`：`input_ids = future_map[[3, 7]]` → B 的 t7 和 A 的 X1，**一次 GPU 上的 gather，不经 CPU**——CPU 这时还没见过 X1。D1 算完，GPU 把 B 的 t8、A 的 X2 写回第 3、7 格。队里：**[P, D1]**。',
             note('推断', 'CPU 缺的只是 token 的值：序列长度每步 +1，CPU 自己就算得出来，所以不等 GPU 也能把下一批备好。'),
             focus=['fmap', 'w_read', 'launch', 'w_fwd', 'gpu'],
             state={'fmap': fmap_g('t7 → t8', 'X1 → X2'), 'launch': g_text('D1=[B,A]', '已发射，不等', tone='run'),
                    'gpu': g_text('D1', 'B·t8  A·X2', tone='run'),
                    'rq': g_cells([cell('P', '[A]'), cell('D1', '[B,A]', 'new')], left='队头')}),
        beat('第 n+1 圈 · 处理 P：CPU 第一次看到 X1',
             '处理队头 P：A 在名单里 → `output_ids=[X1]`，没结束；第一个 token **立刻发**（带 header）。A 的 `inflight_steps` 2→1（P 处理完，D1 还在飞）。队里剩 **[D1]**。',
             note('推断', '这就是「差一圈」：X1 第 n 圈就算出来了，CPU 到第 n+1 圈发射完 D1 之后才第一次看到它。'),
             focus=['proc', 'w_pop', 'rq', 'w_out', 'detok'],
             state={'proc': proc_g('ok', 'ok', 'ok'), 'rq': g_cells([cell('D1', '[B,A]')], left='队头'),
                    'rb': g_cells([cell('B', '7/8'), cell('A', '1/32')]), 'detok': g_text('A · X1', '首 token，带 header')}),
        beat('第 n+2 圈 · B 不再发射：最后一个 token 已在飞',
             '选批走 decode：B 在飞 1 步（D1），已处理 7 个输出，`7 + 1 ≥ 8` → D1 会带回它的最后一个 token → **剔出这一批**。running batch = **[A]**，但 B 还在 `running_reqs` 名单里（结果没处理）。发射 **D2=[A]**。',
             note('测试', 'overlap 调度器跳过「排队中的结果已带着第 max_new_tokens 个 token」的请求的 decode。',
                  q='The overlap scheduler skips the decode of requests whose queued result', ref=A(TSKIP, 1)),
             focus=['pick', 'w_merge', 'rb', 'roster', 'launch'],
             state={'pick': pick_g('skip', 'ok', 'no'), 'rb': g_cells([cell('B', '剔出', 'out'), cell('A', '1/32')]),
                    'launch': g_text('D2=[A]', 'B 没被带上'), 'proc': proc_g(),
                    'fmap': fmap_g('t8 不再读', 'X2 → X3')}),
        beat('第 n+2 圈 · 处理 D1：B 按长度结束',
             '处理 D1：B 记第 8 个 token → 写满 `max_new_tokens=8` → 结束 → `release`：从 `running_reqs` 摘掉，KV 交给前缀树。A 记 X2。两行都发管道 ②。**B 一步都没白算。**',
             note('推断', '长度是能预知的：上一步在飞时就知道它会写满，所以不再多发射一步。EOS 预知不了，看后面的 A。'),
             focus=['proc', 'w_list', 'roster', 'w_out', 'detok'],
             state={'proc': proc_g('ok', 'ok', 'ok'), 'roster': roster_g('A', '—'), 'rb': g_cells([cell('A', '2/32')]),
                    'rq': g_cells([cell('D2', '[A]')], left='队头'), 'detok': g_text('B 结束 · A·X2', '一条消息两行'),
                    'fmap': fmap_g('（B 已走）', 'X3')}),
        beat('第 n+3、n+4 圈：稳定的节奏',
             '每圈：先发射 D3、D4=[A]，再处理上一圈的 D2、D3，A 记下 X3、X4。A 的 `inflight_steps` 一直是 1——总有一步在飞；`future_map[7]` 每圈改写一次，D4 写进的是 X5。',
             note('推断', '稳态下 GPU 一直有活：CPU 处理第 k 步结果时，第 k+1 步已经在算。'),
             focus=['launch', 'gpu', 'fmap', 'rq', 'proc'],
             state={'launch': g_text('D4=[A]', '已发射', tone='run'), 'gpu': g_text('D4', 'A·X5', tone='run'),
                    'rb': g_cells([cell('A', '4/32')]), 'fmap': fmap_g('—', 'X5'),
                    'rq': g_cells([cell('D4', '[A]')], left='队头'), 'detok': g_text('A · X4', '每圈一个')}),
        beat('第 n+5 圈 · 发射 D5：白算的一步',
             '选批时 A 有 4 个输出，`4 + 1 < 32`，看不出要结束 → 照常发射 **D5=[A]**：`prepare_for_decode` 给它再分 1 个槽（`kv_committed=25`）。可 X5 其实是 EOS——要等本圈稍后处理 D4 时才知道。',
             note('推断', 'EOS 只有看到 token 才知道；「发射」和「处理」差一圈，这一步注定白算。'),
             focus=['launch', 'pick', 'rb', 'gpu', 'rq'],
             state={'launch': g_text('D5=[A]', 'A 其实已结束', tone='fail'), 'gpu': g_text('D5', '白算', tone='fail'),
                    'rq': g_cells([cell('D4', '[A]'), cell('D5', '[A]', 'new')], left='队头'), 'proc': proc_g()}),
        beat('第 n+5 圈 · 处理 D4：X5 = EOS',
             '处理 D4：A 记 X5 → 是 EOS → `finished_len=5`，结束 → `release`：A 从 `running_reqs` 摘掉；按代码推算 `owned=min(25, 25)=25`，白算那一步的 KV 也一起交给前缀树。结束行必发。现在 A **已不在任何名单里**，可队里 D5 的快照里还有它。',
             note('注释', '结束先体现在名单上：活着的请求一个最多在一张名单里。',
                  q='The lists that hold the live requests, by kind. A request is in at', ref=A(SCH, 5075)),
             focus=['proc', 'w_list', 'roster', 'w_out', 'detok', 'rq'],
             state={'proc': proc_g('ok', 'ok', 'ok'), 'roster': roster_g('—', '—'),
                    'rb': g_cells([cell('A', '已结束', 'ghost')]), 'rq': g_cells([cell('D5', '[A]')], left='队头'),
                    'detok': g_text('A 结束', '结束行必发', tone='ok')}),
        beat('第 n+6 圈 · 丢掉白算的那一行',
             '选批：每圈过滤把已结束的 A 滤出 running batch → **空**；没有新请求 → `batch=None`，这圈不发射。但 `last_batch`=D5 不空 → 照样处理：A 那一行不在名单里 → **直接跳过**，不记 token、不发送。',
             note('注释', '发射后才离开的行，什么都不提交、不发送。', q='A row whose request left after launch (finished,', ref=A(BRP, 351)),
             focus=['proc', 'w_list', 'roster', 'w_pop', 'rq', 'pick'],
             state={'pick': pick_g('skip', 'ok', 'no'), 'rb': g_cells([cell()]),
                    'proc': proc_g('no', 'skip', 'skip'), 'launch': g_text('None', '这圈不发射'),
                    'gpu': g_text('—', '空闲'), 'rq': g_cells([cell()], left='队头'), 'detok': g_text('—', '没有要发的')}),
        beat('第 n+7 圈 · 空闲',
             '`batch=None` 且 `last_batch=None` → `on_idle`：做自检，把 decode 预留比例 `new_token_ratio` 复位到初值。循环继续空转，等管道 ① 来新请求。',
             note('推断', '两样都空才算真空闲：只要上一圈发射过，就一定还有一批结果要收尾。'),
             focus=['pick', 'launch', 'rq'],
             state={'pick': pick_g(None, None, 'no'), 'proc': proc_g()}),
    ]

    return scene(1220, 520, parts, wires, steps, mw=340, mh=1284,
                 cap='调度循环里面：第 n 圈发射，第 n+1 圈才处理（overlap 默认开）')


# ================================================================ 第二层：「选批」里面
def mf_g(a=None, b=None, c=None):
    return g_checks([('上批是 prefill？', a), ('并入 running', b), ('滤掉已结束', c)])


def dec_g(a=None, b=None, c=None):
    return g_checks([('剔：末步在飞', a), ('显存够？', b), ('每人 +1 槽', c)])


def pick_scene():
    parts = [
        part('p_last', '上一圈的批', [16, 16, 180, 124], m=[8, 8, 240, 110], kind='ext', code='last_batch',
             glyph=g_text('D_B', '上一圈：decode'),
             say='先看它是不是 prefill', why='并进同一批，一次前向', trap='它的结果还没处理',
             card=card(
                 '上一圈发射的那一批。选批先看它是不是 prefill：是的话，它的请求这一圈要并进 running batch，从此跟着 decode。',
                 ['圈末写 `last_batch = batch`，下一圈选批读。' + R(SCH, 2303),
                  '分块 prefill 中还没完的那一个先排除：它还没产出第一个 token，不能去 decode。' + R(SCH, 4383, 4386)],
                 [note('推断', 'prefill 完的请求不单独成批，而是并进同一个 running batch：所有在 decode 的请求每圈只用一次前向。')],
                 'overlap 下，它的结果此刻还没处理（排在本圈发射之后）。')),
        part('p_mf', '并入 + 每圈过滤', [296, 16, 240, 150], m=[8, 180, 260, 150], kind='gate', n='1',
             glyph=mf_g(),
             say='把 prefill 的请求并进来，再滤掉已结束的', why='overlap 下结束可能晚一圈才知道', trap='只能滤「已经知道」结束的',
             card=card(
                 '把上一圈 prefill 的请求并进 running batch，再把已结束的请求滤掉。',
                 ['先 `filter_batch` 过滤 P 本身，剩下的 `move` 进 `running_reqs`，再 `merge_batch`。' + R(SCH, 4391, 4411),
                  '不管有没有合并，每圈都对 running batch `filter_batch()`，留下没结束的。' + R(SCH, 4413, 4416),
                  '少了人就清掉「满了」标志 `batch_is_full`，好重新收新请求。' + R(SCH, 4417, 4418)],
                 [note('注释', '每圈都滤：overlap 在 prefill 的结果把请求判为结束之前就合并了它们，而 decode 那条路未必走得到。',
                       q='before its result finishes them, and update_running_batch may never run.', ref=A(SCH, 4414)),
                  note('提交', '以前只在特定情况下滤，后来改成每一步收新请求之前都滤。',
                       q='[Fix] scheduler: Drop finished requests from the running batch before every admission (#44351)', ref='c:3b5ae4ea86')],
                 '它只能滤掉「已经知道结束」的请求；EOS 还躺在没处理的结果里的，照样留在批里——白算一步就是这么来的。'),
             to='sched_loop.pick.src_pick'),
        part('p_rb', 'running batch', [656, 16, 200, 124], m=[8, 400, 124, 124], kind='store', code='running_batch',
             glyph=g_cells([cell('B', '6/8')]),
             say='在 decode 的', why='改完一并返回', trap='比 GPU 慢一步',
             card=card(
                 '这一刻正在 decode 的请求。格子里写「CPU 已处理的输出数 / max_new_tokens」。',
                 ['选批时就地改它：并入、过滤、剔除，最后 `prepare_for_decode`。' + R(SCH, 5282),
                  '选批把新的 running batch 一起返回，循环存回去。' + R(SCH, 2275)],
                 [note('推断', '所有改动都在选批里做完、一并返回：循环其余部分只读它，不用关心它是怎么变的。')],
                 '格子里的数是 CPU 已处理的；GPU 上总有一步在飞。')),
        part('p_wq', '等待队列', [16, 206, 180, 124], m=[212, 400, 120, 124], kind='store', code='waiting_queue',
             glyph=g_cells([cell('A', '20 tok', 'new')]),
             say='等 prefill 的', why='空就早退', trap='退回的也在这',
             card=card(
                 '等 prefill 的请求。',
                 ['按策略重排（默认 `fcfs`），逐个交给预算员。' + R(SCH, 4651),
                  '队列空、又没有分块中的请求 → 直接返回，不建预算员。' + R(SCH, 4606, 4609)],
                 [note('注释', '早退放在扫队列之前：满批的那一圈不去扫等待队列。',
                       q='After the early returns, so a full-batch pass does not scan the queue.', ref=A(SCH, 4647))],
                 '被退回的请求也回到这里，带着已生成的 token。')),
        part('p_adder', '预算员', [296, 200, 240, 160], m=[8, 594, 260, 160], kind='gate', n='2', code='PrefillAdder',
             glyph=g_kv([('可用+可淘汰', '42000'), ('已承诺', '0'), ('输入余额', '16384')]),
             say='先给在跑的留量，再逐个看收不收', why='要价带上 decode 要长的部分', trap='留量按比例估，会变',
             card=card(
                 '一次选批里临时造的预算员：逐个看等待的请求，决定哪些能进这个 prefill 批。',
                 ['先给 running batch 里每个请求预留「还能长多少 × `new_token_ratio`」。' + R(SP, 1093, 1100),
                  '每个请求要价 = 要算的输入 + 它要 decode 的 + 一点开销，小于余量才收。' + R(PB, 211, 215),
                  '收下后记账：「已承诺」加上它（含 decode 的部分），输入余额、分块余额减掉它的输入。' + R(PB, 302, 310),
                  '三道闸：显存不够 → `NO_TOKEN`；输入或分块余额用完 → `OTHER`；否则继续看下一个。' + R(SP, 1304, 1319)],
                 [note('注释', '固定的可用池；「已承诺」里含还没真分配的部分和 decode 要的余量。',
                       q='Fixed token pool. Offsets include pending allocations and decode headroom.', ref=A(PB, 91)),
                  note('文档', '经常退回请求，就把保守度调大。', q='Use a larger value if you see requests being retracted frequently.', ref=A(SCHED_F, 160))],
                 ['`new_token_ratio` 会变：每个顺利的 decode 步衰减一点，退回后重估，空闲时复位。' + R(NTR, 34, 49),
                  '代码自己也承认这套预算可能太保守。' + R(SP, 1351)]),
             to='sched_loop.pick.src_budget'),
        part('p_dec', 'decode 前检查', [656, 380, 200, 136], m=[8, 830, 130, 160], kind='gate', n='3', code='update_running_batch',
             glyph=dec_g(),
             say='先剔、再查显存', why='长度可预知', trap='EOS 预知不了',
             card=card(
                 '没有 prefill 批时，让 running batch decode 一步之前的检查：先剔、再查显存、不够就退、最后每人分 1 个槽。',
                 ['剔：在飞的那一步会写满 `max_new_tokens` 的请求（`len(output_ids)+1 ≥ max_new_tokens` 且在飞步数 > 0）。' + R(SCH, 5182, 5199),
                  '查：下一步要的新槽，分配器（先试淘汰前缀树）给不给得起。' + R(SCH, 5207),
                  '剔了人又不够：不退，这圈不 decode，等排队的结果把那些人的 KV 释放。' + R(SCH, 5208, 5212),
                  '够：`decay_step()`，再 `prepare_for_decode`。' + R(SCH, 5272, 5282)],
                 [note('注释', '在飞那一步会按长度提交最后一个输出的请求。',
                       q='Requests whose step in flight commits their last output by length.', ref=A(SCH, 5183)),
                  note('注释', '排队的结果会在这一步末尾释放即将结束者的 KV；剩下的下一步再 decode，不必退。',
                       q="The queued result frees the finishing requests' KV at the end of", ref=A(SCH, 5209))],
                 ['顺序有讲究：先剔再查显存，剔掉的人不算进需求。',
                  '只有 overlap 循环打开「剔」这一步；不 overlap 时结果当圈就处理，用不着（后半句是推断）。' + R(SCH, 5184, 5185)]),
             to='sched_loop.pick.src_decode'),
        part('p_ret', '退回', [296, 396, 240, 110], m=[218, 830, 114, 110], kind='bg', code='retract_decode',
             say='放掉几个人', why='腾地不进树', trap='不是换出',
             card=card(
                 '显存不够下一步 decode 时，挑几个请求退回等待队列，释放它们的 KV。这个例子里没发生。',
                 ['顺序：先退「已生成最少、输入最长」的。' + R(SBT, 3126, 3127),
                  '一个个退，直到剩下的够下一步；至少留 1 个。' + R(SBT, 3029, 3034),
                  '释放时不插进前缀树；`output_ids` 保留，回到队列后重新 prefill。' + R(RRT, 133, 134),
                  '按剩下的请求重估 `new_token_ratio`，免得下一圈又退。' + R(NTR, 41, 49)],
                 [note('注释', '释放内存、不插进前缀树：空间要马上用。',
                       q="release memory and don't insert into the tree because we need the space instantly", ref=A(SBT, 3057)),
                  note('文档', '默认策略先退输出短、输入长的。', q='retracts short-output,', ref=A(SCHED_F, 150)),
                  note('推断', '先退已生成最少的：重算它损失最小。')],
                 ['不是「换出」：默认只释放、不备份，恢复靠重算。',
                  '连最后一个都放不下就中止（abort）它，进程不崩。']),
             to='sched_loop.pick.src_retract'),
        part('p_out', '本圈的批', [976, 190, 228, 150], m=[8, 1060, 260, 120], kind='out',
             glyph=g_text('—', '还没定'),
             say='prefill 批、decode 批或 None', why='一圈只做一种', trap='None 也会处理上一圈',
             card=card(
                 '选批的结果：一个 prefill 批、一个 decode 批，或 None。',
                 '有新 prefill 批就是它；否则 decode 批；都没有就 None。' + R(SCH, 4443, 4456),
                 [note('注释', '能跑 prefill 就先跑。', q='Run prefill first if possible', ref=A(SCH, 4444)),
                  note('推断', '为什么 prefill 优先没查到明确理由；推测是首 token 延迟最敏感。')],
                 'None 不等于这圈闲着：上一圈发射过的话，这圈照样处理它的结果。'),
             to='sched_loop.pick.src_pick'),
    ]

    wires = [
        wire('pw_last', 'p_last', 'p_mf', '是 prefill 就并', ml='prefill 就并', why='跟着一起 decode',
             card=card('上一圈是 prefill 批时，把它的请求并进 running batch。', R(SCH, 4378, 4382),
                       [note('推断', '并进来之后，这些请求下一步就和其他人一起 decode。')],
                       '分块 prefill 还没完的那一个不并。')),
        wire('pw_mf', 'p_mf', 'p_rb', '并入·过滤', why='就地改',
             card=card('就地改 running batch：`merge_batch` 并入、`filter_batch()` 滤掉已结束的。', R(SCH, 4407, 4418),
                       [note('推断', '就地改、改完返回：循环只需把返回值存回去。')])),
        wire('pw_take', 'p_wq', 'p_adder', '逐个试', why='fcfs 先来先试',
             card=card('等待的请求逐个交给预算员：先前缀匹配，再 `add_one_req`。', R(SCH, 4867),
                       [note('推断', '一个收不下就停（大多数结果直接 break），后面的下圈再试。')])),
        wire('pw_res', 'p_rb', 'p_adder', '先给在跑的留量', ml='给在跑的留量', why='按比例估要长多少',
             card=card('建预算员时，先为 running batch 里每个请求扣一笔「它还要长多少」。',
                       '`min(max_new_tokens − 已生成, 4096) × ratio`。' + R(SP, 1174, 1184),
                       [note('注释', '固定长度的请求用不上「早遇到 EOS」的估计，按 1.0 留。',
                             q='Fixed-length requests cannot benefit from the early-EOS estimate.', ref=A(SP, 1175))],
                       '4096 只是削估计，不改停止条件。' + R(SP, 92, 95))),
        wire('pw_p', 'p_adder', 'p_out', '有 P：跑它', why='prefill 优先',
             card=card('`can_run_list` 不空 → 建 prefill 批，这一圈就跑它。', R(SCH, 4443, 4445),
                       [note('注释', '能跑 prefill 就先跑。', q='Run prefill first if possible', ref=A(SCH, 4444))],
                       '这一圈 running batch 里的人停一拍。')),
        wire('pw_d', 'p_adder', 'p_dec', '没 P：decode', why='否则 decode 一步',
             card=card('没有 prefill 批，running batch 也不空 → 走 decode。', R(SCH, 4446, 4449),
                       [note('推断', 'decode 是兜底：没有新人要进来时，让在跑的人各长一个。')])),
        wire('pw_dout', 'p_dec', 'p_out', 'decode 批', why='可能是 None',
             card=card('检查通过的 running batch 就是本圈的 decode 批。', R(SCH, 4449, 4454),
                       [note('推断', '剔了人又不够显存时返回 None：这圈不 decode，等下一圈。')])),
        wire('pw_ret', 'p_dec', 'p_ret', '显存不够', cls='dash', why='而且没人可等',
             card=card('显存不够下一步、又没有「马上结束」的人可等 → 退回。', R(SCH, 5214, 5231),
                       [note('注释', '有马上结束的人就先等它们释放，不退。',
                             q='this step; decode the rest next step instead of retracting them.', ref=A(SCH, 5210))])),
        wire('pw_back', 'p_ret', 'p_wq', '退回队列', mlp=[290, 792], cls='dash', why='保留已生成的',
             card=card('被退的请求回到等待队列，下次像新请求一样前缀匹配、重新 prefill。', R(SCH, 5270, 5271),
                       [note('推断', '它自己的 KV 没进树（不 checkpoint），能命中的只是树里别人留下的部分。')],
                       '客户端已收到的部分不会重发。')),
    ]

    steps = [
        beat('第 n 圈 · 并入：上一圈是 decode，不用并',
             '`last_batch`=D_B 是 decode 批 → 跳过合并。每圈过滤：B 有 6 个输出、没结束 → 留下。running batch 仍是 **[B]**。',
             note('注释', '每一步都滤，不只在合并之后。', q="Every step, not only after a merge: overlap merges a prefill's requests", ref=A(SCH, 4413)),
             focus=['p_mf', 'pw_last', 'p_last', 'p_rb'],
             state={'p_mf': mf_g('no', 'skip', 'ok')}),
        beat('第 n 圈 · 预算：先给 B 留量',
             '建 `PrefillAdder`（设：分配器可用 40,000 + 前缀树可淘汰 2,000 = **42,000** token，`new_token_ratio=0.7`）。B 还能再长 `min(8 − 6, 4096) = 2` 个，按 0.7 估 → 先记 **1.4** 到「已承诺」里。',
             note('注释', '按比例少留：假设请求会早早遇到 EOS；固定长度的才按 1.0。',
                  q='Fixed-length requests cannot benefit from the early-EOS estimate.', ref=A(SP, 1175)),
             focus=['p_adder', 'pw_res', 'p_rb'],
             state={'p_adder': g_kv([('可用+可淘汰', '42000'), ('已承诺', '1.4'), ('输入余额', '16384')])}),
        beat('第 n 圈 · 预算：A 过三道闸',
             'A 前缀命中 5，还要算 **15** 个；`max_new_tokens=32`。要价 `15 + 32 + 1 = 48` < 余量 `42000 − 1.4` → **收下**。记账：已承诺 +48 → **≈49.4**；输入余额 16384 → **16369**，分块余额 8192 → **8177**。队里没别人了：`can_run_list=[A]`。',
             note('推断', '要价里带上它要 decode 的 32 个：收下之后得长得动，不然很快又要退回。'),
             focus=['p_adder', 'pw_take', 'p_wq'],
             state={'p_adder': g_kv([('可用+可淘汰', '42000'), ('已承诺', '≈49.4'), ('输入余额', '16369')]),
                    'p_wq': g_cells([cell('A', '收下', 'pick')])}),
        beat('第 n 圈 · 出批：有 prefill 就跑 prefill',
             '有了 **P=[A]** → 这一圈就跑它；B 这一圈不 decode，**停一拍**。`prepare_for_extend` 这时才真正给 A 分 15 个 KV 槽。',
             note('注释', '能跑 prefill 就先跑。', q='Run prefill first if possible', ref=A(SCH, 4444)),
             focus=['p_out', 'pw_p', 'p_adder'],
             state={'p_out': g_text('P=[A]', 'B 停一拍', tone='run'), 'p_wq': g_cells([cell()])}),
        beat('第 n+1 圈 · 并入：A 并进来',
             '`last_batch`=P 是 prefill → 先过滤 P（A 没结束——其实是**还不知道**）→ A 挪进 `running_reqs` → `merge_batch`：running batch = **[B, A]**。再滤一遍，无变化。',
             note('推断', '并入时 P 的结果还没处理：A 的第一个 token 是不是 EOS 此刻不知道，所以以后每圈都得再滤。'),
             focus=['p_mf', 'pw_last', 'p_last', 'pw_mf', 'p_rb'],
             state={'p_last': g_text('P=[A]', '上一圈：prefill'), 'p_mf': mf_g('ok', 'ok', 'ok'),
                    'p_rb': g_cells([cell('B', '7/8'), cell('A', '0/32', 'new')]), 'p_out': g_text('—', '还没定'),
                    'p_adder': g_text('—', '每圈新建一个')}),
        beat('第 n+1 圈 · 没有新请求 → decode',
             '等待队列空 → 早退，连预算员都不建。走 decode：B 在飞 0 步 → 不剔；A 在飞 1 步，但 `0 + 1 < 32` → 不剔。显存够 → `new_token_ratio` 衰减一格；每人分 1 个槽，A 的 seq_len 20→21。得到 **D1=[B, A]**。',
             note('注释', '早退放在扫队列之前。', q='After the early returns, so a full-batch pass does not scan the queue.', ref=A(SCH, 4647)),
             focus=['p_dec', 'pw_d', 'pw_dout', 'p_out'],
             state={'p_adder': g_text('没建', '队列空，早退'), 'p_dec': dec_g('no', 'ok', 'ok'),
                    'p_out': g_text('D1=[B,A]', 'decode 一步', tone='run')}),
        beat('第 n+2 圈 · decode 前：B 的最后一个 token 已在飞 → 剔',
             'B 已处理 7 个输出，D1 还在飞（`inflight_steps=1`）：`7 + 1 ≥ 8` → D1 会带回它的最后一个 token → **剔出这一批**。A：`1 + 1 < 32` 照常。running batch = **[A]**，得到 **D2=[A]**。',
             note('测试', '每个请求只要 1 次 prefill + (max_new_tokens − 1) 次 decode：已在飞的那个 token 不再多跑一次 decode。',
                  q='# and no decode runs for the token already in flight.', ref=A(TSKIP, 81)),
             focus=['p_dec', 'p_rb', 'pw_dout', 'p_out'],
             state={'p_last': g_text('D1=[B,A]', '上一圈：decode'), 'p_mf': mf_g('no', 'skip', 'ok'),
                    'p_dec': dec_g('ok', 'ok', 'ok'),
                    'p_rb': g_cells([cell('B', '剔出', 'out'), cell('A', '1/32')]),
                    'p_out': g_text('D2=[A]', 'B 不再发射')}),
        beat('第 n+6 圈 · 过滤：结束的 A 被滤掉',
             '上一圈（n+5）处理结果时 A 采到 EOS、已结束，可那一圈已经带着它发射了 D5。这一圈的「每圈过滤」把它滤掉：running batch **空**。队列也空 → 没有 prefill；空批不 decode → 本圈 **None**。',
             note('注释', '空批里没人能「离开」，所以顺手清掉「满了」标志，不然收新请求的门一直关着。',
                  q='An empty batch has no request left to leave, and only a leaving', ref=A(SCH, 4602)),
             focus=['p_mf', 'p_rb', 'p_out'],
             state={'p_last': g_text('D5=[A]', '上一圈：白算的 decode'), 'p_mf': mf_g('no', 'skip', 'ok'),
                    'p_rb': g_cells([cell('A', '已结束', 'out')]), 'p_dec': dec_g('skip', 'skip', 'skip'),
                    'p_out': g_text('None', '这圈不发射')}),
    ]

    return scene(1220, 520, parts, wires, steps, mw=340, mh=1190,
                 cap='选批里面：并入 → 过滤 → 有 prefill 跑 prefill，否则 decode')


# ================================================================ 源码页
def src_loop():
    return node(
        'sched_loop.src_loop', '主循环源码', 'overlap 主循环：发射在前，处理上一批在后',
        '调度循环本身：71 行，对照 33 行的不 overlap 版。',
        lead='你在「调度循环」里的 result_queue 框下面：这就是那个死循环本身。先看 overlap 版，再看下面不 overlap 的版本，差别只在「处理结果」挪到了哪。',
        src=SCH + ':2200-2305',
        blocks=[
            code(SCH, [(2235, 2305)], notes=[
                at(2237, '推断', '只有 overlap 循环打开「跳过最后一步已在飞的 decode」。', has='self.enable_skip_finishing_decode = True'),
                at((2238, 2245), '推断', 'result_queue：已发射、未处理的批。pop_and_process 取的是队头，也就是「上一圈」。', has='self.result_queue.popleft()'),
                at((2258, 2262), '注释', '例外：先处理上一批再选批，让它结束的请求不再被发射。默认只在需要语法同步时。',
                   q='not after launching it, so the requests it finishes are never launched.', ref=A(SCH, 2311), has='process_last_batch_early'),
                at((2272, 2276), '推断', '选批：同时拿回新的 running batch。', has='self.running_batch = plan.running_batch'),
                at((2281, 2284), '注释', '发射 → 栅栏 → 快照入队。', q="Fence result processing behind this forward's shared reads.",
                   has='self.result_queue.append((batch.copy(), batch_result))'),
                at((2290, 2295), '推断', '看的是 last_batch 不是 batch：这圈没发射，上一圈的结果也照样处理；两者都空才空闲。', has='if self.last_batch:'),
                at((2298, 2300), '注释', '采样推迟到处理完上一批之后：结构化输出的约束依赖上一步的 token。',
                   q='It depends on the result of the last batch (e.g., grammar), so we run it after the last batch is processed.',
                   has='launch_batch_sample_if_needed'),
                at(2303, '推断', '记下这圈发射的批：下一圈选批要看它是不是 prefill。', has='self.last_batch = batch'),
            ], cap='event_loop_overlap：默认走这个'),
            code(SCH, [(2200, 2232)], notes=[
                at((2222, 2224), '推断', '不 overlap：发射完立刻处理同一批，CPU 等 GPU 的时候什么也不干。', has='self.process_batch_result(batch, result)'),
                at((2225, 2228), '推断', '没有批就空闲。', has='self.on_idle()'),
            ], cap='event_loop_normal：--disable-overlap-schedule 时走这个'),
        ])


def src_launch():
    return node(
        'sched_loop.src_launch', '发射与 future_map 源码', '发射：三条流、输入从哪来、token 怎么留在 GPU',
        'run_batch 的 overlap 分支，加上 future_map 的读和写。',
        lead='你在「调度循环」里的「发射」和「future_map」框下面：run_batch 的 overlap 分支，以及下一步输入的两个来源。',
        src=SCH + ':5432-5511',
        blocks=[
            code(SCH, [(5432, 5447), (5470, 5475), (5493, 5511)], notes=[
                at(5433, '推断', 'forward 流先等 schedule 流：这圈在 schedule 流上改好的东西，前向一定看得到。',
                   has='self.forward_stream.wait_stream(self.schedule_stream)'),
                at((5442, 5444), '推断', '先备输入：prefill 从 CPU 暂存，decode 从 future_map。', has='self.resolve_forward_inputs('),
                at((5470, 5472), '推断', '真正的前向 + 采样（框外）。', has='forward_batch_generation'),
                at(5475, '推断', '也把 seq_lens+1 发布进 future_map；主路里谁读它，底稿没追到。',
                   has='self.future_map.publish(future_indices, batch.seq_lens + 1)'),
                at(5493, '推断', 'copy_done：处理结果时 CPU 就等这个事件。', has='batch_result.copy_done = self.device_module.Event()'),
                at((5495, 5497), '注释', '把这一圈的采样结果中转给下一圈。',
                   q="Stash this iter's relay payload for next iter's resolve_forward_inputs.", ref=A(SCH, 5758), has='self._relay_forward_payload('),
                at(5506, '注释', '结果在 copy 流上拷回，和下一批前向重叠。',
                   q='Issue the result readback on copy_stream so it overlaps the next forward.', ref=A(SCH, 5820), has='self._copy_result_to_cpu(batch_result, batch)'),
                at((5510, 5511), '注释', '下一圈的 input_ids 经 future_map 转交，这里清掉防误用。',
                   q='Next-iter input_ids relayed via future_map.', has='batch.input_ids = None'),
            ], cap='run_batch（overlap 分支，节选）'),
            code(OVL, [(127, 142), (165, 170)], notes=[
                at((130, 133), '注释', '输入只有两个来源。', q='Decode/spec_v2: gather from FutureMap', has='Two sources'),
                at(142, '推断', 'prefill：从固定内存的 CPU 暂存异步拷上 GPU。', has='prefill_input_ids_cpu.to(batch.device, non_blocking=True)'),
                at((165, 166), '推断', 'decode：按 req_pool_idx 在 GPU 上 gather，不经 CPU。例：future_map[[3, 7]]。',
                   has='future_map.output_tokens_buf[batch.req_pool_indices]'),
                at((167, 170), '推断', '调试模式下读完就写回 -1：哪一行没写就被读，断言会抓住。', has='_assert_nonneg_and_invalidate'),
            ], cap='resolve_forward_inputs：下一步的输入从哪来'),
            code(OVL, [(309, 312), (324, 325), (332, 332), (344, 347), (800, 802)], notes=[
                at((310, 312), '注释', '始终开着：不开 overlap 也用它。', q='Always-on pool-indexed relay for cross-iter values.', has='Always-on'),
                at((324, 325), '注释', '下标是 req_pool_idx；第 0 行留给 CUDA graph 的填充行。',
                   q='Bufs indexed by req_pool_idx; slot 0 mirrors KV padding row so', has='Bufs indexed by req_pool_idx'),
                at(332, '推断', '长度 = KV 表的行数，不用另估容量。', has='req_to_token.shape[0]'),
                at((345, 347), '推断', '主路下它就是一条 int64 的 GPU 向量。', has='self.output_tokens_buf = torch.empty('),
                at((800, 802), '推断', '写：按下标把这一步采样的 token 放进去。', has='self.output_tokens_buf[indices] = payload.bonus_tokens.to('),
            ], cap='FutureMap：一条按请求行号索引的 GPU 向量'),
        ])


def src_result():
    return node(
        'sched_loop.src_result', '处理结果源码', '处理 decode 结果：等拷贝、查名单、记 token、整批发出',
        'process_batch_result_decode 的骨架，加上 still_in_batch。',
        lead='你在「调度循环」里的「处理上一圈」框下面：一批 decode 结果怎么被逐行处理，白算的那一行在哪被丢掉。',
        src=BRP + ':1151-1299',
        blocks=[
            code(BRP, [(1151, 1158), (1203, 1217), (1240, 1254), (1298, 1299)], notes=[
                at((1156, 1157), '推断', 'CPU 唯一真正等 GPU 的地方；这时下一批已在 GPU 上跑，等的只是拷贝的尾巴。', has='result.copy_done.synchronize()'),
                at(1158, '推断', '先算出每一行还算不算数。', has='still_in_batch_rows = self._rows_still_in_batch(batch)'),
                at(1203, '推断', '和末尾的 free_group_end 夹住整批：批内释放的槽先攒着，最后一次还（底稿没读分配器内部）。', has='free_group_begin'),
                at((1214, 1217), '注释', '发射后才离开的行：跳过。例：第 n+6 圈 D5 里 A 那一行。',
                   q='Left the batch after launch; its over-allocated tokens are', has='if not still_in_batch_rows[i]:'),
                at(1243, '推断', '记下这一步的 token。', has='req.output_ids.extend(next_token_id)'),
                at(1246, '注释', '判结束：stop 串先于 EOS，EOS 先于长度。', q='Stop string beats EOS/stop-token matched in the same step',
                   ref=A(SBT, 1503), has='req.update_finish_state(new_accept_len)'),
                at((1252, 1254), '推断', '结束了就 release：摘名单、KV 交给前缀树。', has='self._handle_finish_state_updated_req('),
                at(1298, '推断', '整批只发一次，而且只发还在的请求。', has='self.output_streamer.stream_output(reqs_still_in_batch'),
            ], cap='process_batch_result_decode（节选）'),
            code(BRP, [(349, 354)], notes=[
                at((350, 353), '注释', '每一行对照「现在还在批里的请求」。',
                   q='A row whose request left after launch (finished,', has='frozen batch'),
            ], cap='_rows_still_in_batch'),
            code(SCH, [(5109, 5118)], notes=[
                at((5114, 5118), '推断', '三张名单任一张里有它，就还活着。', has='req in self.running_reqs'),
            ], cap='still_in_batch：查名单'),
        ])


def src_pick():
    return node(
        'sched_loop.pick.src_pick', '选批源码', '选批：并入、每圈过滤、prefill 优先',
        'get_next_batch_to_run 的主干（跳过 DP + 投机解码旁枝）。',
        lead='你在「选批」里的「并入 + 每圈过滤」和「本圈的批」框下面：选批的主干代码。',
        src=SCH + ':4378-4456',
        blocks=[
            code(SCH, [(4378, 4429), (4443, 4456)], notes=[
                at((4378, 4382), '推断', '上一圈是 prefill 才合并。', has='last_batch.forward_mode.is_extend()'),
                at((4383, 4386), '推断', '分块 prefill 中还没完的那个排除：它还没有第一个 token。', has='reqs_to_exclude.add(last_batch.chunked_req)'),
                at((4392, 4395), '推断', '先滤 P 本身。', has='last_batch.filter_batch(reqs_to_exclude=list(reqs_to_exclude))'),
                at((4403, 4406), '推断', '挪进 running_reqs 名单：从这一刻起它算「在 decode」。例：第 n+1 圈的 A。', has='self.running_reqs,'),
                at((4407, 4411), '推断', 'running batch 空就直接拿 P 当它，否则合并。', has='running_batch.merge_batch(last_batch)'),
                at((4413, 4418), '注释', '每圈都滤，不只在合并之后。', q="Every step, not only after a merge: overlap merges a prefill's requests",
                   has='running_batch.filter_batch()'),
                at((4422, 4424), '推断', '可选的「推迟 prefill」（默认关）。', has='_should_defer_prefill'),
                at((4426, 4428), '推断', '拼 prefill 批：预算员在这里面。', has='self.get_new_batch_prefill(running_batch)'),
                at((4443, 4445), '注释', 'prefill 优先。', q='Run prefill first if possible', has='ret = new_batch'),
                at((4447, 4454), '推断', '否则 decode 一步；decode 那边返回 None 就这圈不跑。', has='self.update_running_batch(running_batch)'),
            ], cap='get_next_batch_to_run（节选）'),
            code(SCH, [(4601, 4609), (4639, 4647)], notes=[
                at((4602, 4604), '注释', '空批顺手清「满了」标志。', q='An empty batch has no request left to leave, and only a leaving',
                   has='running_batch.batch_is_full = False'),
                at((4606, 4609), '推断', '满了，或队列空又没在分块：不拼 prefill。', has='len(self.waiting_queue) == 0'),
                at((4639, 4645), '推断', 'KV 表的行用完：标记「满了」，直接返回。', has='running_batch.batch_is_full = True'),
                at(4647, '注释', '早退放在扫队列之前。', q='After the early returns, so a full-batch pass does not scan the queue.', has='full-batch pass'),
            ], cap='_get_new_batch_prefill_raw 开头的早退'),
        ])


def src_decode():
    return node(
        'sched_loop.pick.src_decode', 'decode 检查源码', 'decode 前：先剔在飞即写满的，再查显存，不够就退',
        'update_running_batch 与 inflight_steps 记账。',
        lead='你在「选批」里的「decode 前检查」框下面：update_running_batch 的顺序，以及 inflight_steps 怎么记。',
        src=SCH + ':5182-5283',
        blocks=[
            code(SCH, [(5182, 5212), (5214, 5217), (5229, 5231), (5251, 5251), (5270, 5283)], notes=[
                at((5182, 5192), '注释', '在飞那一步会按长度写满的请求。例：第 n+2 圈的 B。',
                   q='Requests whose step in flight commits their last output by length.', has='req.next_output_finishes_by_length()'),
                at((5184, 5185), '推断', '只有 overlap 循环打开这个开关。', has='if not self.enable_skip_finishing_decode:'),
                at((5198, 5199), '推断', '先剔……', has='finishing_reqs = self._reqs_finishing_inflight(batch)'),
                at(5207, '推断', '……再查显存。', has='kv_full_retract_flag = not batch.check_decode_mem()'),
                at((5208, 5212), '注释', '剔了又不够：不退，这圈不 decode。',
                   q="The queued result frees the finishing requests' KV at the end of", has='return None'),
                at((5215, 5217), '推断', '测试可以按固定间隔人为触发退回。', has='TEST_RETRACT'),
                at((5229, 5231), '推断', '退：返回被退的请求和重估的 ratio。', has='batch.retract_decode('),
                at(5251, '推断', 'ratio 直接改成重估值。', has='self.new_token_ratio_tracker.current = new_token_ratio'),
                at((5270, 5271), '推断', '被退的请求回等待队列。', has='self._add_request_to_queue(req, is_retracted=True)'),
                at((5272, 5273), '推断', '没退：ratio 衰减一格。', has='self.new_token_ratio_tracker.decay_step()'),
                at(5282, '推断', '每人分 1 个 KV 槽、seq_lens+1。', has='batch.prepare_for_decode()'),
            ], cap='_reqs_finishing_inflight + update_running_batch（节选）'),
            code(SBT, [(684, 686), (1130, 1132), (2255, 2262)], notes=[
                at((684, 686), '注释', '已发射、结果没处理的步数。', q='# The launched steps carrying this request whose results are not', has='self.inflight_steps = 0'),
                at((1130, 1132), '推断', '再提交一个输出就到上限？例：B 有 7 个，7+1 ≥ 8。', has='len(self.output_ids) + 1 >= self.sampling_params.max_new_tokens'),
                at((2255, 2258), '推断', '发射时 +1。', has='req.inflight_steps += 1'),
                at((2260, 2262), '推断', '结果处理完 -1。', has='req.inflight_steps -= 1'),
            ], cap='inflight_steps 的记账'),
        ])


def src_budget():
    return node(
        'sched_loop.pick.src_budget', '预算员源码', 'PrefillAdder：给在跑的留量、要价、记账、三道闸',
        '预算公式散在 schedule_policy.py 和 prefill_budget.py。',
        lead='你在「选批」里的「预算员」框下面：图上 1.4、48、≈49.4 这几个数分别是哪几行算出来的。',
        src=PB + ':155-310',
        blocks=[
            code(SP, [(1093, 1100), (1174, 1184)], notes=[
                at((1095, 1100), '推断', '给在跑的每个请求预留一笔。', has='self._get_running_request_total_token_offset(r)'),
                at((1175, 1176), '注释', '固定长度按 1.0。', q='Fixed-length requests cannot benefit from the early-EOS estimate.',
                   has='ratio = 1.0 if req.sampling_params.ignore_eos'),
                at((1179, 1183), '推断', '还能长多少，削到 4096 以内，再乘 ratio。例：B 是 min(8−6, 4096)×0.7 = 1.4。', has='CLIP_MAX_NEW_TOKENS'),
            ], cap='给 running batch 留量'),
            code(PB, [(155, 161), (211, 215), (302, 310)], notes=[
                at((156, 157), '推断', '余量 = 可用 + 可淘汰 − 已承诺（含 decode 余量）。', has='self.total_offset'),
                at((211, 215), '推断', '要价小于余量才收。例：A 要价 48 < 42000 − 1.4。',
                   has='total_tokens + state_slots * self.state_slot_cost < self.remaining_total'),
                at((309, 310), '推断', '记账：已承诺多加 max_new_tokens，眼前只记槽位。例：+48 → ≈49.4。', has='self.total_offset += immediate + max_new_tokens'),
            ], cap='PrefillBudget：余量、要价、记账'),
            code(SP, [(1304, 1319)], notes=[
                at((1305, 1307), '推断', '第一道闸：显存。', has='AddReqResult.NO_TOKEN'),
                at((1309, 1310), '推断', '第二道闸：一批的总输入（max_prefill_tokens）。', has='self.rem_input_tokens <= 0'),
                at((1316, 1317), '推断', '第三道闸：这一块（chunked_prefill_size）。', has='self.rem_chunk_tokens <= 0'),
            ], cap='budget_state：还能不能再收一个'),
            code(NTR, [(21, 49)], notes=[
                at((22, 26), '推断', '初值 0.7 × 保守度 1.0，封顶 1。', has='SGLANG_INIT_NEW_TOKEN_RATIO'),
                at((31, 35), '推断', '每个顺利的 decode 步线性衰减一格，600 步降到下限。', has='SGLANG_NEW_TOKEN_RATIO_DECAY_STEPS'),
                at((41, 49), '推断', '退回后重估：(已生成 + 20 × 请求数) ÷ 总 max_new_tokens，封顶 1。', has='def estimate_new_token_ratio_after_retract'),
            ], cap='new_token_ratio 的一生'),
        ])


def src_retract():
    return node(
        'sched_loop.pick.src_retract', '退回源码', '退回：谁先退、退到什么时候、为什么不进前缀树',
        'retract_decode 与 release_req，对照正常结束的 release。',
        lead='你在「选批」里的「退回」框下面：显存不够时怎么挑人、怎么释放。最后一段把它和正常结束的 release 并排看。',
        src=SBT + ':3019-3059',
        blocks=[
            code(SBT, [(3019, 3038), (3057, 3059)], notes=[
                at(3023, '推断', '先排序：谁最该留。', has='self._get_decode_retraction_order(self.reqs)'),
                at((3029, 3031), '推断', '一直退到剩下的够下一步。', has='not self.check_decode_mem(selected_indices=sorted_indices)'),
                at((3032, 3034), '注释', '至少留一个。', q='# Always keep at least one request', has='Always keep at least one request'),
                at(3037, '推断', '从末尾弹：最不该留的先退。', has='sorted_indices.pop()'),
                at((3057, 3059), '注释', '释放、不进树。', q="release memory and don't insert into the tree because we need the space instantly",
                   has='self.release_req(idx, len(sorted_indices), request_retirer)'),
            ], cap='retract_decode（节选）'),
            code(SBT, [(3116, 3127)], notes=[
                at((3126, 3127), '推断', '默认按（已生成数，−输入长度）排；末尾是已生成最少、输入最长的。',
                   has='len(req.output_ids), -len(req.origin_input_ids)'),
            ], cap='退回顺序'),
            code(SBT, [(1835, 1850)], notes=[
                at(1844, '推断', '交给 retirer：释放 KV。', has='request_retirer.retract(req, offload_kv=offload_kv)'),
                at((1845, 1847), '注释', '马上用新腾出来的可淘汰空间：按剩余请求数 × 20 步淘汰前缀树。',
                   q='# NOTE(lsyin): we should use the newly evictable memory instantly.', has='evict_from_tree_cache'),
                at(1849, '推断', '清掉前缀匹配等状态；output_ids 保留。', has='req.reset_for_retract()'),
            ], cap='release_req'),
            code(RRT, [(91, 93), (110, 111), (113, 135)], notes=[
                at(93, '推断', '正常结束：先摘名单。', has='_leave_owner_list(req)'),
                at((110, 111), '推断', '正常结束按调用方给的 checkpoint 释放：KV 交给前缀树。', has='release_kv_cache(req, self.tree_cache, checkpoint=checkpoint)'),
                at(114, '文档', '退回：只释放、不 checkpoint；返回能不能恢复。',
                   q="Release a retracted request's memory uncheckpointed; return whether it can resume.", has='uncheckpointed'),
                at((120, 121), '推断', '默认（非 PD 解码端）：恢复靠重新 prefill。', has='resumable = req.resumable_by_reprefill'),
                at((133, 134), '推断', 'checkpoint=False：KV 不进前缀树。', has='checkpoint=False'),
            ], cap='RequestRetirer：release（正常结束）vs retract（退回）'),
        ])


def nodes():
    pick = node(
        'sched_loop.pick', '选批里面', '选批：并入、过滤、预算、decode 前检查',
        '「选批」框里的三步和两道预算。',
        lead='你在「调度循环」里的「选批」框里面：上一层说它决定这圈跑什么；这里拆开看它怎么并、怎么滤、怎么算预算、decode 前剔谁。按 ▶ 跟着 A、B 走同一套数。',
        blocks=[pick_scene()],
        children=[src_pick(), src_budget(), src_decode(), src_retract()])
    top = node(
        'sched_loop', '调度循环里面', '调度循环：一圈里的状态怎么变，为什么「处理」总比「发射」晚一圈',
        '等待队列、running batch、result_queue、future_map、名单，逐圈推演。',
        lead='你在顶层「调度循环」框里面。顶层说它每圈「收、选批、发射、处理」；这里把每圈都在变的几样状态摊开。按 ▶ 跟着请求 A（和一个更早的请求 B）逐圈走，看「第 n 圈发射、第 n+1 圈才处理」怎么错开，以及白算的那一步在哪被丢掉。循环本身的代码见 [[sched_loop.src_loop|主循环源码]]。',
        blocks=[loop_scene()],
        children=[pick, src_loop(), src_launch(), src_result()])
    return {'sched_loop': top}
