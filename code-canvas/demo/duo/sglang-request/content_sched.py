# 调度循环这一层（顶层「③ 调度循环」框的里面）：一圈怎么定「跑谁」。
# 结构：schedloop（机器图）→ adder（机器图：逐条装入）→ 源码页；schedloop.retract（机器图：撤回）→ 源码页。
from cv import *

_P = 'python/sglang/srt/'
SCH = _P + 'managers/scheduler.py'
SP = _P + 'managers/schedule_policy.py'
SBT = _P + 'managers/schedule_batch.py'
PB = _P + 'mem_cache/prefill_budget.py'
RL = _P + 'managers/scheduler_components/request_list.py'
NTR = _P + 'managers/scheduler_components/new_token_ratio_tracker.py'
BRP = _P + 'managers/scheduler_components/batch_result_processor.py'
SCHED_ARGS = _P + 'arg_groups/fields/schedule.py'


def _r(f, a, b=None):
    """写在文字里的锚点（迷你语法）。"""
    return '`%s:%s`' % (f, a if b is None else '%d-%d' % (a, b))


def S(a, b=None):
    return _r(SCH, a, b)


def P(a, b=None):
    return _r(SP, a, b)


def B(a, b=None):
    return _r(PB, a, b)


def T(a, b=None):
    return _r(SBT, a, b)


def nodes():
    return {'schedloop': _lap()}


# =====================================================================================
# 第一层：一圈怎么定「跑谁」
# =====================================================================================
def _lap():
    parts = [
        part('merge', '并入与清点', [30, 30, 170, 130], m=[76, 10, 188, 124], kind='act', n='1',
             code='merge_batch',
             glyph=g_kv([('上一圈', 'B 的 decode'), ('并入', '无 · 在跑 [B]')]),
             say='上一圈的 prefill 并进在跑批', why='后面的判断都读它', trap='并的是整批，不等首字',
             card=card(
                 '选批**之前**，先把上一圈发射的批整理进 `running_batch`、剔掉已结束的请求。后面「批满了没有」「有几条在跑」的判断都读这个批，所以数据要先是新的'
                 '（' + S(4378, 4418) + '）。',
                 ['上一圈是 prefill 批：整批并入 `running_batch`；在跑批是空的，就直接把它当在跑批（' + S(4403, 4411) + '）。',
                  '上一圈带着一条正在分块的长 prompt：先把它过滤掉，所以分块的中间块永远不会混进 decode 批（' + S(4383, 4396) + '）。',
                  '每一圈都剔掉已结束的请求，批一变小就把 `batch_is_full` 清零（' + S(4413, 4418) + '）。',
                  '例子：圈 1 的上一圈是 B 的 decode，没有 prefill 批可并；圈 2 的上一圈是 A 的 prefill，并入后 [B] 变成 [B, A]。'],
                 [note('注释', '过滤发生在**每一圈**，不只是并入之后：overlap 下并入先于结果处理，`update_running_batch` 可能根本没跑。',
                       q="Every step, not only after a merge: overlap merges a prefill's requests", ref=SCH + ':4413'),
                  note('推断', '先清点再选批：「满不满」「有几条」只看 `running_batch`，它不是最新的，后面的判断就会错。')],
                 '并入的是**整批**，而且不等这批 prefill 的结果：在 overlap 下，A 被并进 `running_batch` 时它的首 token 还在 GPU 路上（顶层说的「晚一拍」在这里的后果）。'),
             to='schedloop.main'),

        part('gate', '前置闸门', [330, 30, 170, 150], m=[76, 200, 188, 150], kind='gate', n='2',
             code='_get_new_batch_prefill_raw',
             glyph=g_checks([('批没满', 'ok'), ('队列有 A', 'ok'), ('请求槽够', 'ok')]),
             say='条件不够就不装，直接返回', why='批满了就不白排一次队', trap='batch_is_full 是粘住的',
             card=card(
                 '决定这一圈要不要**开始**装 prefill。三种情形直接返回「没有 prefill 批」（' + S(4591, 4646) + '）：'
                 '在跑批已满、或等待队列空（且没有正在分块的请求）；请求槽用完。',
                 ['请求槽 = 还能再放进批里的请求数，等于 `min(pp_budget, row_budget)`；单卡时上限就是 `max_running_requests`（' + S(4478, 4504) + '、' + S(1316, 1322) + '）。',
                  '`batch_is_full` 是个**粘住**的标志：装入失败时置位；有请求离开批（结束、撤回、批变小）才清（' + S(4416, 4418) + '）。',
                  '正在分块的长 prompt 跳过「请求槽」这一关（' + S(4636, 4645) + '）。',
                  '排序放在闸门**后面**：被挡在门外的圈不排队（' + S(4647, 4661) + '）。'],
                 [note('注释', '闸门在排序之前：批满了的圈不会白白扫一遍队列。',
                       q='After the early returns, so a full-batch pass does not scan the queue.', ref=SCH + ':4647'),
                  note('注释', '防死锁：批已经空了，就强制清掉 `batch_is_full`——否则没有请求可离开，标志永远清不掉，装入永远关着。',
                       q='An empty batch has no request left to leave, and only a leaving', ref=SCH + ':4602')],
                 ['`batch_is_full` **不是每圈重算**的：它被置位后一直保持，直到有请求离开批。看到「满」不代表这一圈重新量过。',
                  '闸门只管「开不开始」。一条请求装不装得下，要到下一步逐条试才知道。'],),
             to='schedloop.gate'),

        part('order', '排队序', [630, 30, 170, 150], m=[76, 416, 188, 124], kind='act', n='3',
             code='waiting_queue.reorder',
             glyph=g_kv([('策略', 'fcfs'), ('队列', '[A]'), ('A 命中', '0 个')]),
             say='默认先来先服务，队列不动', why='排序在闸门之后', trap='FCFS 也匹配前缀',
             card=card(
                 ['给 `waiting_queue` 排个序，并给每条等待请求在 radix 树里找一次最长的已缓存前缀（' + S(4651, 4661) + '）。',
                  '默认策略是 `fcfs`（`python/sglang/srt/arg_groups/fields/schedule.py:109`）：不开优先级调度时队列保持入队顺序（' + P(355, 359) + '），所以这一步基本不改顺序。'],
                 ['`reorder` 只许改顺序，成员变了会抛错（`python/sglang/srt/managers/scheduler_components/request_list.py:183-191`）。',
                  '别的策略分两类：看缓存的（`lpm` 最长前缀优先、`dfs-weight`、`hrrn`、`shortest-prefill-first`、`uniboost`），不看缓存的（`fcfs`、`lof`、`random`、`routing-key`）（' + P(187, 203) + '）。',
                  '例子：队里只有 A，没有顺序可排；但 A 仍被匹配一次，命中 0 个（设）。',
                  '别的策略、有界 LPM、优先级抢占、`PrefillDelayer`：这里**没展开**，只走默认路径。'],
                 [note('注释', '就算策略不看缓存也先匹配一遍：为了让负载快照里有 `num_matched_prefix_tokens`。',
                       q='Cache-agnostic policies sort without matching; match anyway so the load', ref=SP + ':345'),
                  note('提交', '这次匹配是为一个负载指标加的，不是为了排序。',
                       q='Add num_waiting_uncached_tokens load metric (#27174)', ref='c:f6cd1a9822')],
                 ['FCFS 下前缀匹配**不是**为了排序（排完顺序一点没变），是为了填 `req.num_matched_prefix_tokens`。',
                  '默认不动队列，有个后果：队首的大请求会挡住后面的小请求（见 4 号框）。'])),

        part('adder', '逐条装入', [930, 30, 170, 130], m=[76, 606, 188, 124], kind='act', n='4',
             code='PrefillAdder',
             glyph=g_kv([('A 占', '18+64+1=83'), ('余量', '99863.5')]),
             say='按预算逐条试装，装不下就停', why='给在跑请求留着空间', trap='不跳过队首去找小的',
             card=card(
                 '造一个 `PrefillAdder`，从队首起一条一条试着往这一圈的 prefill 批里装，每条都要过几本账（构造 ' + S(4686, 4705) + '；逐条循环 ' + S(4772, 4895) + '）。'
                 '装出几条算几条。',
                 ['四本账：KV「总量」、KV「当前」、输入 token（`max_prefill_tokens`，默认 16384）、块大小（`chunked_prefill_size`，80GB 档默认 8192）。具体怎么算、怎么扣，点进下一层。',
                  '每一条试完返回四种结果之一：继续装下一条 / KV 不够 / 别的预算耗尽（这两种都停）/ 永远装不下（单独收集、直接报错）。',
                  '装好以后给要算的 token 分配 KV 槽位、建批（' + S(4955, 4978) + '）。'],
                 [note('注释', '「总量」那本账里除了眼前要写的，还含在跑请求将来要用的位置（decode 的余量）。',
                       q='Fixed token pool. Offsets include pending allocations and decode headroom.', ref=PB + ':91'),
                  note('注释', '作者知道队首挡人的现象，专门为「一条也没装出」的圈打日志。',
                       q='Empty passes are the key signal for head-of-line blocking.', ref=SCH + ':4920')],
                 ['**遇到第一个装不下的就停**，不是跳过它去找更小的：队首的大请求会挡住后面的小请求。为什么不跳过，代码没写；推断：跳过会让大请求在小请求不断到来时饿死。',
                  '`max_prefill_tokens` 默认配置下基本不是瓶颈：块大小 8192 比它小，先耗尽的是块大小这本账（推断）。'],),
             to='schedloop.adder'),

        part('build', '这一圈跑 prefill', [930, 290, 170, 130], m=[8, 800, 150, 156], kind='out', n='5',
             code='ScheduleBatch.init_new',
             glyph=g_text('prefill', 'A · 18 个'),
             say='装出了：这一圈只跑它', why='缓存命中的不再算', trap='B、A 这圈不 decode',
             card=card(
                 '装出了请求，就建成一个 prefill 批交给 GPU（`init_new` + `prepare_for_extend`，' + S(4955, 4978) + '）。这一圈**只**跑这个批，`running_batch` 里的请求一步 decode 都不走（' + S(4443, 4456) + '）。',
                 ['只把**没命中缓存**的那一段喂给 GPU，KV 槽位也只给这些新 token 分配：命中多长，就少算多长（`python/sglang/srt/managers/schedule_batch.py:2406`、`python/sglang/srt/managers/schedule_batch.py:2464`）。',
                  '装入的请求并不是「出队」，而是换表：`move(...)` 把它从 `waiting_queue` 换到 `prefill_launched_reqs`；长 prompt 的中间块换到 `chunked_reqs`（' + S(4934, 4942) + '）。'],
                 [note('注释', '规则就这一句：能 prefill 就先 prefill。', q='Run prefill first if possible', ref=SCH + ':4444'),
                  note('注释', '每条请求同一时刻只在一张表里，靠 `move` 换表。',
                       q='A list of live requests. Every live request is in at most one list,', ref=RL + ':69')],
                 ['默认不混批（`enable_mixed_chunk` 默认关，`python/sglang/srt/arg_groups/fields/schedule.py:227-230`）：prefill 的圈里，在跑的 B、A 一步 decode 都不走。长 prompt 分块的那几圈，它们要停好几圈（见推演）。',
                  '长 prompt 的中间块算完**不产出 token**：用户要等最后一块 prefill 完才看到首字。'])),

        part('decode', 'decode 一步', [630, 290, 170, 130], m=[182, 800, 150, 156], kind='act', n='6',
             code='update_running_batch',
             glyph=g_text('本圈不跑', 'B 等一圈'),
             say='没装出才轮到：走一步', why='放不下就先撤回', trap='撤回在这里，不在装入',
             card=card(
                 ['只有 4 号框**一个都没装出**（或者闸门直接返回），才对 `running_batch` 走一步 decode（' + S(4446, 4456) + '）。纯 prefill 的批（打分请求）不 decode。',
                  '走之前先问：「下一步所有在跑请求要的新 token，KV 还放不放得下」，放不下就撤回一部分请求（`update_running_batch`，' + S(5194, 5282) + '）。'],
                 ['先剔除「在途一步就会按长度结束」的请求（' + S(5198, 5200) + '）。',
                  '`check_decode_mem` 不过：若有刚好要结束的请求，这一步**不撤回也不 decode**，等它们释放；否则 `retract_decode` 撤回（' + S(5207, 5229) + '）。',
                  '没撤回的 decode 步：`new_token_ratio` 衰减一格（' + S(5273) + '）。撤回的机制点进下一层。'],
                 [note('注释', '有请求刚好要结束时，先让它结束、不撤回别人。',
                       q="The queued result frees the finishing requests' KV at the end of", ref=SCH + ':5209'),
                  note('推断', '「prefill 优先 + 不混批」会让长 prompt 期间的在跑请求停摆；`enable_mixed_chunk`、`prefill_decode_interval` 两个旋钮应当就是为了让两者交错，但代码没明说动机。')],
                 ['显存不够有**两个**时间点，处理完全不同：**装入**时不够，请求留在队里等（4 号框）；**decode** 时不够，才撤回在跑的请求（这里）。别混在一起想。']),
             to='schedloop.retract'),
    ]

    wires = [
        wire('w_clear', 'merge', 'gate', '清点完', ml='清点完', n='1', why='先更新，再判断',
             card=card('`running_batch` 整理完，闸门才去看「批满了没有」。',
                       '闸门读的是 `running_batch.batch_is_full` 和 `waiting_queue` 的长度（' + S(4606, 4609) + '）。',
                       [note('推断', '闸门的所有判断都依赖在跑批的当前样子，所以并入、清点必须排在它前面。')],
                       '这一步里 `batch_is_full` 可能被清零（批变小了）——闸门看到的是清点之后的值。')),
        wire('w_pass', 'gate', 'order', '放行', ml='放行', n='2', why='放行了才排队',
             card=card('三道关都过了，才去排队、匹配前缀。',
                       '没过的圈在上一步就返回了，不会走到这里（' + S(4647) + '）。',
                       [note('注释', '排序放在提前返回之后：批满的圈不扫队列。', q='After the early returns, so a full-batch pass does not scan the queue.', ref=SCH + ':4647')])),
        wire('w_queue', 'order', 'adder', '排好的队', ml='排好的队', n='3', why='装入前再匹配一次',
             card=card('把排好序的候选交给装入器，从队首起一条条试。',
                       ['每个候选在装入前**重新匹配一次**前缀：`req.init_next_round_input(self.tree_cache, ...)`（' + S(4845) + '）。',
                        '候选里每一条的命中长度，决定它这次要算多少、要占多少（见下一层）。'],
                       [note('推断', '树在两圈之间会变（别的请求结束写入、旧缓存被淘汰），排序时那次匹配的结果不能直接信。')],
                       '这里的「重新匹配」和 3 号框里那次匹配是两次：3 号框那次为排序 / 指标，这里这次才决定真正要算多少。')),
        wire('w_prefill', 'adder', 'build', '装出了', ml='装出了', mlp=[100, 772], n='4', why='至少一条就建批',
             card=card('装入器装出了至少一条请求，就建 prefill 批。',
                       '`new_batch` 不为空，选批函数直接返回它，不再看 decode（' + S(4443, 4445) + '）。',
                       [note('注释', '有 prefill 批就先跑它。', q='Run prefill first if possible', ref=SCH + ':4444')],
                       '一个批里可以装好几条请求；长 prompt 在这里只装第一块。')),
        wire('w_nobatch', 'adder', 'decode', '没有 prefill 批', ml='没批', mlp=[250, 772], cls='dash', trap='闸门返回也走这条',
             card=card('这一圈没有 prefill 批：闸门提前返回了，或者逐条装入一个都没装出。两种情形都走这条线，轮到 decode。',
                       '`new_batch` 为空才走 decode 分支；`running_batch` 也空就什么都不跑（' + S(4446, 4456) + '）。',
                       [note('推断', '「没有 prefill 批」只是一个结果，不管是闸门挡的还是装不进，下游都一样处理：对在跑批走一步。')],
                       '图上这条线从 4 号框画出来，但闸门（2 号框）直接返回时也走它——例如圈 2：队列空，闸门返回，改跑 decode。')),
    ]

    # —— 推演用的几组「小图」
    lap2 = {
        'merge': g_kv([('上一圈', 'A 的 prefill'), ('在跑批', '[B] → [B, A]')]),
        'gate': g_checks([('批没满', 'ok'), ('队列有请求', 'no'), ('请求槽够', 'skip')]),
        'order': g_text('没走到', '队列空'),
        'adder': g_text('没走到', '闸门已返回'),
        'build': g_text('没有', 'prefill 批'),
        'decode': g_text('[B, A] 一步', '每条 +1 token', tone='ok'),
    }
    lapk = {
        'merge': g_kv([('上一圈', '[B, A] decode'), ('在跑批', '[B, A]')]),
        'gate': g_checks([('批没满', 'ok'), ('队列有 C', 'ok'), ('请求槽够', 'ok')]),
        'order': g_kv([('策略', 'fcfs'), ('队列', '[C]'), ('C 命中', '0 个')]),
        'adder': g_kv([('C 要算', '20000'), ('块预算', '8192')]),
        'build': g_text('C 第 1 块', '8192 个 token'),
        'decode': g_text('停摆', 'B、A 本圈不动', tone='fail'),
    }

    steps = [
        beat('圈 1 前半：并入、闸门、排序',
             '服务器上 B 在跑，A 刚进队。这一圈开头：上一圈是 B 的 decode，没有 prefill 批可并，`running_batch` = [B]。'
             '闸门：批没满、队里有 A、请求槽够，放行。排序：FCFS，队里只有 A，不动；只给 A 做一次前缀匹配，命中 0 个（设）。',
             note('注释', '排序放在提前返回之后：批满的圈不扫队列。', q='After the early returns, so a full-batch pass does not scan the queue.', ref=SCH + ':4647'),
             focus=['merge', 'w_clear', 'gate', 'w_pass', 'order']),
        beat('圈 1 后半：装入 A，这一圈只跑 prefill',
             '装入器逐条试：A 要占 18 + 64 + 1 = **83**（设：页 = 1 个 token）个 token，余量 99863.5，装得下；队列已空，装入结束。'
             '产出 **A 的 prefill 批**。B 这一圈不动。',
             note('注释', '规则：能 prefill 就先 prefill。', q='Run prefill first if possible', ref=SCH + ':4444'),
             focus=['adder', 'w_prefill', 'build', 'decode']),
        beat('圈 2：A 并入，没有可装的，改跑 decode',
             '上一圈是 A 的 prefill 批，被并进在跑批：[B] 变成 [B, A]。队列空了，闸门直接返回「没有 prefill 批」，'
             '所以这一圈对 [B, A] 走一步 decode，每条各出 1 个 token。',
             note('注释', '每一圈都清点：overlap 下并入先于结果处理。', q="Every step, not only after a merge: overlap merges a prefill's requests", ref=SCH + ':4413'),
             focus=['merge', 'gate', 'w_nobatch', 'decode'], state=lap2),
        beat('圈 k：长 prompt C 来了，B、A 停摆',
             'C 是一条 20000 个 token 的长 prompt（设），前缀不命中。它被装成**第 1 块 8192 个 token**（过程见「逐条装入」那一层），块预算花光，这一圈只跑 C 的这一块。'
             '默认不混批，所以 B、A 这一圈一步 decode 都不走。',
             note('推断', '「prefill 优先 + 默认不混批」的直接后果：长 prompt 期间，在跑的请求停摆。'),
             focus=['adder', 'w_prefill', 'build', 'decode'], state=lapk),
        beat('圈 k+1 ~ k+3：C 续块，B、A 连停三圈',
             '圈 k+1：C 的第 1 块 KV 登记进树，续第 2 块 8192；圈 k+2：第 3 块 = 20000 − 16384 = 3616，是最后一块，块预算还剩 4576，这时才有余量放别人。'
             '三圈里 B、A 都没 decode。圈 k+3：C 并入在跑批，变成 [B, A, C]，之后每圈 decode 三条。',
             note('推断', '分块把单次前向的 token 数封了顶，但代价是每一块都占掉一整圈，在跑的请求要等。'),
             focus=['build', 'decode', 'merge'],
             state={'build': g_text('C 第 2、3 块', '8192 + 3616'),
                    'decode': g_text('停摆 3 圈', 'B、A 不动', tone='fail'),
                    'merge': g_kv([('k+3 圈', 'C 并入'), ('在跑批', '[B, A, C]')])}),
        beat('显存吃紧：E 装不进（留队），A 被撤回',
             '（设）显存吃紧：请求 E（输入 200、最多再生成 64，占 200 + 64 + 1 = 265）这一圈余量只有 150，装不下，**留在队里**，`batch_is_full` 置位，之后每圈被闸门挡掉。'
             '到了 decode，`check_decode_mem` 又放不下：撤回**已生成最少的 A**，退回等待队列。同一件事（KV 吃紧），在装入和 decode 两个时间点有两种收场。',
             note('推断', '装入时用的是估计（`max_new × ratio`），估低了才会在 decode 中途没地方放新 token；撤回把「估错」的代价限定在重算被撤的那几条。'),
             focus=['adder', 'w_nobatch', 'decode', 'gate'],
             state={'gate': g_checks([('批没满', 'no'), ('队列有 E', 'skip'), ('请求槽够', 'skip')]),
                    'adder': g_kv([('E 占', '200+64+1=265'), ('余量', '150（设）')]),
                    'build': g_text('没装出', 'E 留队'),
                    'decode': g_text('撤回 A', 'A 回队尾', tone='fail')}),
    ]

    return node(
        'schedloop', '调度循环里面', '一圈只选一种批：先 prefill，装不出才 decode',
        '顶层「③ 调度循环」框的里面：并入 → 闸门 → 排序 → 逐条装入 → 要么跑 prefill、要么 decode。',
        lead='你现在在顶层图「③ 调度循环」这个框里面。放大看，一圈做的事是：先清点，再过闸门、排队、逐条装入；'
             '**一圈只选一种批**——装出了 prefill 就跑 prefill，装不出才对在跑的请求 decode。接着顶层那个例子：A 刚到，B 在跑。',
        src=SCH + ' get_next_batch_to_run 4339-4476',
        blocks=[scene(1220, 450, parts, wires, steps, mw=340, mh=1000,
                      cap='默认配置、单卡、80GB 档。图上的数（A、B、C、E 的 token 数，KV 池 100000，一页 = 1 个 token，ratio 0.7）都是**设**的，'
                          '只用来走一遍；没有在真实服务上跑过。只讲了默认路径：别的排序策略、有界 LPM、优先级抢占、PrefillDelayer、别的显存分配后端没展开。')],
        children=[_main(), _gate(), _adder(), _retract()])


# ---------------------------------------------------------------- 源码页：选批主干
def _main():
    return node(
        'schedloop.main', '选批主干', 'get_next_batch_to_run：并入、装 prefill、没有才 decode',
        '一圈选批的骨架：合并上一圈的 prefill 批、清点、装 prefill，装不出才 decode。',
        src=SCH + ':4378-4456',
        blocks=[
            code(SCH, [(4378, 4429), (4443, 4456)], notes=[
                at((4378, 4382), '推断', '只有上一圈是 prefill（extend）批才进这个并入分支；上一圈是 decode 就不并。', has='forward_mode.is_extend()'),
                at((4394, 4395), '推断', '过滤后批变小，就把 `batch_is_full` 清零：有请求离开，装入可以再试。', has='batch_is_full = False'),
                at((4407, 4411), '推断', '整批并入：在跑批是空就直接顶替，否则 `merge_batch`。不等这批 prefill 的结果。', has='running_batch.merge_batch(last_batch)'),
                at((4413, 4418), '注释', '每一圈都过滤，不只是并入之后。',
                   q="Every step, not only after a merge: overlap merges a prefill's requests", has='running_batch.filter_batch()'),
                at(4422, '推断', '`_should_defer_prefill`：「prefill 之后先跑 N 步 decode」的旋钮，默认关，默认路径不会进这里。', has='_should_defer_prefill'),
                at(4426, '推断', '装 prefill：进入前置闸门 + 排序 + 逐条装入（下面几页）。', has='get_new_batch_prefill(running_batch)'),
                at((4443, 4445), '注释', '有 prefill 批就先跑它。', q='Run prefill first if possible', has='ret = new_batch'),
                at((4446, 4456), '推断', '没有 prefill 批才 decode；在跑批为空、或是纯 prefill 批（打分请求）就什么都不跑。',
                   has='update_running_batch(running_batch)'),
            ], cap='get_next_batch_to_run 的主干（4430-4442 的投机解码 + DP 注意力分支略去）'),
        ])


# ---------------------------------------------------------------- 源码页：闸门与排序
def _gate():
    return node(
        'schedloop.gate', '闸门与排序', '前置闸门：批满、队列空、请求槽用完，就不装',
        '一圈里 prefill 装入的入口：三种不开始的情形，然后才排序。',
        src=SCH + ':4598-4661',
        blocks=[
            code(SCH, [(4598, 4609), (4636, 4661)], notes=[
                at((4602, 4604), '注释', '批空了就强制清 `batch_is_full`，否则没有请求可离开，标志永远清不掉。',
                   q='An empty batch has no request left to leave, and only a leaving', has='batch_is_full = False'),
                at((4606, 4609), '推断', '批满、或队列空，且没有正在分块的请求：直接返回「没有 prefill 批」。',
                   has='running_batch.batch_is_full or len(self.waiting_queue) == 0'),
                at((4636, 4638), '注释', '正在分块的请求跳过「请求槽」这一关，拦住它会泄漏它占着的内存。',
                   q='A chunked request skips this check: it keeps its req_to_token row across', has='keeps its req_to_token row'),
                at((4639, 4645), '推断', '请求槽用完：置位 `batch_is_full` 并返回。', has='running_batch.batch_is_full = True'),
                at(4647, '注释', '排序在提前返回之后。', q='After the early returns, so a full-batch pass does not scan the queue.',
                   has='After the early returns'),
                at((4651, 4652), '推断', '按策略给等待队列排序；默认 FCFS，基本不动。', has='self.waiting_queue.reorder('),
            ], cap='前置闸门 + 排序'),
        ])


# =====================================================================================
# 第二层：逐条装入
# =====================================================================================
def _adder():
    parts = [
        part('reserve', '在跑请求的预留', [30, 30, 170, 130], m=[76, 10, 188, 124], kind='store', n='1',
             code='total_offset',
             glyph=g_kv([('B 还要', '200−5=195'), ('预留', '195×0.7=136.5')]),
             say='先替在跑的请求留出空间', why='免得 decode 中途没地方', trap='比例小于 1，是估的',
             card=card(
                 '装入器一上来先给每条**在跑**请求预留它还要生成的那部分显存，从「余量」里扣掉：每条预留 = `min(max_new_tokens − 已生成, 4096) × new_token_ratio`；`ignore_eos` 的请求比例取 1'
                 '（' + P(1174, 1185) + '）。',
                 ['例子：B 的 `max_new_tokens` = 200，已生成 5 个（设）：min(195, 4096) × 0.7 = **136.5**。0.7 是 ratio 的初值（`python/sglang/srt/environ.py:708`）。',
                  '预留之和是「总量」账的初始偏移 `total_offset`（' + P(1095, 1100) + '）。',
                  '4096 只截断这个**估计**，不改请求真正的停止条件（' + P(92, 96) + '）。'],
                 [note('注释', '新请求不能把在跑请求将来要用的 KV 抢光，否则 decode 中途没地方放新 token。',
                       q='Fixed token pool. Offsets include pending allocations and decode headroom.', ref=PB + ':91'),
                  note('注释', '太大的 `max_new_tokens` 要截断，否则服务器会过于保守。',
                       q='This can prevent the server from being too conservative.', ref=SP + ':93'),
                  note('推断', '乘一个小于 1 的比例：大多数请求会提前遇到结束符，全按上限预留会让并发上不去。估错了怎么办——走撤回（见 decode 那一层）。')],
                 '这个比例不是常数：撞墙撤回之后会被重估，之后每个 decode 步又慢慢降下来（见撤回那一页）。')),

        part('ledger', '本圈的几本账', [330, 30, 170, 150], m=[76, 194, 188, 150], kind='store', n='2',
             code='PrefillBudget',
             glyph=g_kv([('KV 总量', '99863.5'), ('块预算', '8192'), ('输入预算', '16384')]),
             say='总量、当前、块大小三本账', why='总量含将来，当前只含眼前', trap='可驱逐的也算余量',
             card=card(
                 '装入器手里的预算：**KV 总量**、**KV 当前**、输入 token（默认 16384）、块大小（80GB 档默认 8192）；外加两个个数上限：请求槽、`prefill_max_requests`（默认不限）。',
                 ['KV 总量 `remaining_total` = 池里可用 + 可驱逐 − `total_offset`（' + B(155, 157) + '）。',
                  'KV 当前 `remaining_current` = 同样的余量 − `current_offset`（' + B(159, 161) + '）。',
                  '区别：总量账含「在跑请求将来要生成的」，当前账只含「眼前要写进去的」。例子（设）：池 100000，总量 = 100000 − 136.5 = 99863.5。底稿没给当前账的初值，这里不写数。',
                  '输入 token 与块大小每装一条都扣同样的量；默认块大小 8192 比 16384 小，所以先耗尽的是块（推断，' + P(1385, 1391) + '）。'],
                 [note('推断', '把「可驱逐」的也算进余量：radix 树里的缓存本来就是需要时可以让出的，不算的话会把能用的空间白白浪费掉。'),
                  note('推断', '分两本账：一本管「会不会撑爆」（含将来），一本管「眼前这一步写不写得下」（只含眼前）。底稿没找到写明这一设计理由的注释。')],
                 ['「可驱逐」也算余量，但装入时会给命中的树节点**临时加锁**，让自己要复用的前缀不算可驱逐，免得余量虚高（'+ P(2052) + '）。',
                  '`max_prefill_tokens` 名字像主角，其实默认配置下块大小先到零；它真正当主角是关掉分块的时候。'],),
             to='schedloop.adder.budget'),

        part('check', '装得下吗', [630, 30, 170, 130], m=[76, 394, 188, 130], kind='gate', n='3',
             code='check_prefill',
             glyph=g_checks([('83 < 99863.5', 'ok')]),
             say='总占量 < 总量余量才放行', why='严格小于，贴线就不装', trap='还要算前缀命中',
             card=card(
                 '先算这条请求要占多少，再对着「KV 总量」比：`total_tokens < remaining_total` 才放行（' + B(211, 215) + '）。不过就被拒绝（`NO_TOKEN`；永远装不下的是 `UNSCHEDULABLE`）。',
                 ['总占量 = (整段待填长度 − 命中前缀) + 还可能生成的 token（截到 4096）+ 每请求额外一页（' + P(2215, 2229) + '）。',
                  '例子：A 整段 18、命中 0、最多再生成 64、页 = 1 个 token（设）：18 + 64 + 1 = **83**；余量 99863.5；83 < 99863.5，过。',
                  '命中的前缀越长，要算的越少：`extend_len = 整段 − prefix_len`（' + P(2244) + '）。',
                  '为什么多留一页：分页的分配器可能给每条请求多占一页。'],
                 [note('注释', '每请求多留一页，给页对齐的开销。',
                       q='Reserve page_size for page-alignment overhead: the paged allocator may', ref=SP + ':2217'),
                  note('提交', '这个页取整是为修「页大小 > 1 时预填 OOM」加的。',
                       q='Fix prefill OOM due to wrong token calculation when page > 1', ref='c:25549433e8'),
                  note('注释', '作者自己承认这个权宜之计偏保守。',
                       q='TODO(lsyin): check this workaround logic, which only ensures the prefill will not out of memory, and may be too conservative',
                       ref=SP + ':1351')],
                 ['比较是**严格小于**：`total_tokens < remaining_total`，等于也不放行（' + B(213) + '）。',
                  '「命中 0」只是例子：若 A 和别的请求共享 12 个 token 的前缀，extend 就只有 6，预算只扣 6（取整后再加一页）。'],),
             to='schedloop.adder.one'),

        part('chunk', '要不要切块', [930, 30, 170, 130], m=[76, 588, 188, 130], kind='gate', n='4',
             code='chunk_tokens_limit',
             glyph=g_checks([('18 ≤ 8192', 'ok')]),
             say='extend 超出块限额就截一块', why='封顶一次前向的 token', trap='同时只许一条在分块',
             card=card(
                 '要算的 token 一次放得下就整条装；超出块限额，就**只截一块**装进来，标 `is_chunked`、`max_new_tokens` 记 0（这一块还不生成）（' + P(2291, 2319) + '）。',
                 ['例子：A 的 extend 18 ≤ 8192，整条装。C 的 extend 20000 > 8192，截成第 1 块 8192。',
                  '之后每一圈先续这条分块请求，再看别人；最后一块装完，请求才换表、才会并入在跑批（' + S(4707, 4720) + '）。',
                  '没开分块时（`chunked_prefill_size = -1`）：批里第一条请求哪怕超过输入上限也放行，否则超长 prompt 永远排不上（' + P(2272, 2278) + '）。'],
                 [note('注释', '同一时刻只能跟踪一条没装完的分块请求，已经有一条时，另一条也要分块就拒绝。',
                       q='Only one unfinished chunked request can be tracked; a', ref=SP + ':2293'),
                  note('推断', '分块把「一次前向最多处理多少 token」封顶，激活内存和单次前向的时间都有了上界，超长 prompt 不会让整台机器停摆或 OOM。'
                       '（块大小被拿去估激活内存，是间接证据：`python/sglang/srt/arg_groups/memory_hook.py:318-320`。）')],
                 ['分块期间，排在后面的新请求也上不了车：C 的几块把块预算吃光，下一个候选在循环第一关就 break。',
                  '为什么「只许一条」：注释只说「只能跟踪一条」，设计动机没写。'],),
             to='schedloop.adder.one'),

        part('commit', '装进去、扣账', [930, 290, 170, 130], m=[76, 782, 188, 124], kind='act', n='5',
             code='_commit_prefill_admission',
             glyph=g_kv([('总量', '−83'), ('块预算', '−18')]),
             say='入列、锁树节点、扣几本账', why='扣完才好判断下一条', trap='总量多扣 max_new',
             card=card(
                 '这条请求过关了：记下它这一次要算到哪（`extend_end`）、给命中的树节点加锁、放进 `can_run_list`、扣掉各本账（' + P(2356, 2396) + '、' + P(1321, 1392) + '）。',
                 ['扣账：`immediate = extend(取整) + 一页 + 额外`；总量账扣 `immediate + max_new`，当前账只扣 `immediate`（' + B(302, 310) + '）。',
                  '例子：A 的 immediate = 18 + 1 = 19，总量扣 19 + 64 = 83：99863.5 → **99780.5**；块预算 8192 → **8174**（只扣 18）。'],
                 [note('推断', '总量账连将来要生成的 `max_new` 一起扣，当前账只扣眼前要写的：这样下一条请求看到的余量，已经把前面装进去的请求将来的需要算进去了。')],
                 '扣的是**估计**：总量里的 `max_new` 是上限，请求常常提前结束，所以这本账偏保守（推断）。'),
             to='schedloop.adder.budget'),

        part('verdict', '这一条的结果', [630, 290, 170, 130], m=[76, 976, 188, 124], kind='out', n='6',
             code='AddReqResult',
             glyph=g_text('继续', '预算还有，装下一条', tone='ok'),
             say='四种结果，只有「继续」不停', why='装不下的留队不丢', trap='遇到第一个装不下就停',
             card=card(
                 '每试完一条返回四种结果之一，装入循环据此决定往下走还是停（' + P(944, 951) + '；循环里的处理 ' + S(4867, 4895) + '）。',
                 ['`CONTINUE`：装进去了、预算还有，继续试下一条。',
                  '`NO_TOKEN`（KV 不够）、`OTHER`（输入或块预算耗尽、个数上限、延迟器）：都 `break`，这一圈到此为止；没装进的请求原样留在 `waiting_queue`。',
                  '`NO_TOKEN` 时还会置 `batch_is_full`：只要这圈已装了别人、或在跑批非空就置位——意思是「在跑的有请求结束、空出内存之前不要再试」（' + S(4876, 4880) + '）。',
                  '`UNSCHEDULABLE`：这条**永远**装不下（没有任何持有显存的请求会还内存），单独收集、`continue`，循环后直接中止它并回错（' + S(4890, 4894) + '）。'],
                 [note('注释', '永远装不下就不能留队等，否则它会永远排在队里。',
                       q='Nothing holding memory will give it back: waiting would', ref=SCH + ':4891'),
                  note('推断', '遇到第一个装不下的就停、不跳过去找小的：跳过会让大请求在小请求不断到来时饿死，停下来等则保证队首最终有机会（与 FCFS 的公平语义一致）。代码和提交都没写理由。')],
                 ['装入失败是**留队等**，不是撤回：撤回只发生在 decode 时 KV 不够（见撤回那一层）。',
                  '队首的大请求会挡住后面的小请求，是这个设计的已知代价。'],),
             to='schedloop.adder.loop'),
    ]

    wires = [
        wire('w_res', 'reserve', 'ledger', '先留出空间', ml='先预留', n='1', why='总量余量 = 池 − 预留',
             card=card('在跑请求的预留，就是「总量」账的初始偏移：从可用 + 可驱逐里先扣掉它。',
                       '例子：100000 − 136.5 = 99863.5（设）。',
                       [note('注释', '偏移里含 decode 的余量。', q='Fixed token pool. Offsets include pending allocations and decode headroom.', ref=PB + ':91')],
                       '预留是估计（`max_new × ratio`），不是真实占用；估低了的后果在 decode 那一层。')),
        wire('w_read', 'ledger', 'check', '余量去比', ml='余量去比', n='2', why='比的是「总量」那本账',
             card=card('装得下吗这一关读的是「总量」余量，不是「当前」余量。',
                       '`check_prefill`：`total_tokens < remaining_total`（' + B(211, 215) + '）。',
                       [note('推断', '总量账已经含了将来要生成的，用它判断，就把「装进去之后在跑请求还能不能活」一并考虑了。')],
                       '「当前」账不在这一关用：它在 `has_capacity` 里和总量一起要求都大于 0（' + B(167, 171) + '）。')),
        wire('w_c2', 'check', 'chunk', '够，再看块', ml='够，看块', n='3', why='先过显存，再看块',
             card=card('显存这一关过了，才去看要不要切块。',
                       '顺序：先 `check_prefill`，再判断分块（' + P(2231, 2353) + '）。',
                       [note('推断', '没有显存放不下的请求，就没有必要为它想怎么切。')]),),
        wire('w_c3', 'chunk', 'commit', '整条或一块', ml='整条/一块', n='4', why='切不切都要入账',
             card=card('不管整条装还是只装一块，都要入账。',
                       '切块时 `is_chunked = True`、`max_new_tokens = 0`；扣账时按这一块的大小扣（' + P(2317, 2319) + '）。',
                       [note('推断', '块里还不生成 token，所以 `max_new` 记 0：这一块不需要为将来的输出预留位置。')],
                       '被切下来的块，下一圈由续块逻辑接着装，而不是从头再来一次。')),
        wire('w_out', 'commit', 'verdict', '返回结果', ml='返回结果', n='5', why='装完才知道还剩多少',
             card=card('扣完账之后，用剩下的预算给出这一条的结果。',
                       '`add_one_req` 末尾返回 `budget_state()`：KV 没了 ⇒ `NO_TOKEN`；输入或块预算 ≤ 0 ⇒ `OTHER`；否则 `CONTINUE`（' + P(1304, 1319) + '、' + P(2210, 2213) + '）。',
                       [note('推断', '这个结果管的是「下一条还能不能装」，而不是「这一条装没装」——所以返回的是装完之后的预算状态。')],
                       '注意：这条请求是装进去了的，哪怕返回的是 `OTHER`；它是这一圈的最后一条。')),
        wire('w_rej', 'check', 'verdict', '不够：被拒', ml='被拒', cls='dash', mfs='r', mts='r', mlp=[302, 760],
             card=card('这一关没过：这一条被拒绝，不扣账，装入循环到此为止。',
                       '多数情形返回 `NO_TOKEN`；如果没有任何持有显存的请求会还内存，返回 `UNSCHEDULABLE`（' + P(1239, 1244) + '）。',
                       [note('注释', '永远装不下，等也没用。', q='Nothing holding memory will give it back: waiting would', ref=SCH + ':4891')],
                       '「留队等」和「报错中止」是两种结果：前者等别人结束，后者永远等不到。')),
    ]

    # 推演
    steps = [
        beat('A：算要占多少，对总量账',
             '在跑的 B 还要生成 200 − 5 = 195 个，按 0.7 预留 **136.5**（设）；池 100000（设），所以总量余量 = **99863.5**。'
             'A：整段 18、命中 0、最多再生成 64、一页 = 1 个 token（设），总占 18 + 64 + 1 = **83**。83 < 99863.5，过。',
             note('注释', '总量账含在跑请求的 decode 余量。', q='Fixed token pool. Offsets include pending allocations and decode headroom.', ref=PB + ':91'),
             focus=['check', 'w_res', 'reserve', 'w_read', 'ledger']),
        beat('A：不用切块，装进去并扣账',
             'A 的 extend 18 ≤ 块限额 8192，整条装。入列、锁命中的树节点、扣账：总量 99863.5 − (19 + 64) = **99780.5**；块预算 8192 − 18 = **8174**。',
             note('推断', '总量账连将来要生成的 `max_new` 一起扣，下一条看到的余量就已经考虑了 A。'),
             focus=['chunk', 'w_c2', 'commit', 'ledger'],
             state={'ledger': g_kv([('KV 总量', '99780.5'), ('块预算', '8174'), ('输入预算', '16366')])}),
        beat('A：返回「继续」，装入结束',
             '预算还有，返回 `CONTINUE`；队列里没有下一条了，装入结束。产出 A 的 prefill 批（回到上一层：这一圈只跑它）。',
             note('推断', '返回的是装完之后的预算状态：它回答的是「下一条还能不能装」。'),
             focus=['commit', 'w_out', 'verdict']),
        beat('C：20000 token，过账但要切块',
             '（设）下一圈 C 到了：总占 20000 + 64 + 1 = 20065，仍远小于余量，过。但 extend 20000 > 块限额 8192：只装**第 1 块 8192**，`is_chunked`、`max_new` 记 0。'
             '块预算降到 0，返回 `OTHER`，这一圈到此为止。',
             note('推断', '分块把单次前向的 token 数封顶；代价是每一块都占一整圈。'),
             focus=['chunk', 'w_c3', 'commit', 'verdict'],
             state={'check': g_checks([('20065 < 余量', 'ok')]),
                    'chunk': g_checks([('20000 ≤ 8192', 'no')]),
                    'commit': g_kv([('这一块', '8192'), ('块预算', '→ 0')]),
                    'verdict': g_text('OTHER', '块预算 0，停', tone='run'),
                    'ledger': g_kv([('KV 总量', '仍够'), ('块预算', '0'), ('输入预算', '已扣')])}),
        beat('E：265 ≥ 150，装不下，留队',
             '（设）显存吃紧：E 输入 200、最多再生成 64，总占 200 + 64 + 1 = **265**；此刻总量余量只有 150（设）。265 不小于 150，被拒：返回 `NO_TOKEN`，'
             '**E 留在队里**，`batch_is_full` 置位，之后每圈被闸门挡掉，直到有请求离开批。',
             note('推断', '「在跑的请求结束、空出内存之前不要再试」：这就是 `batch_is_full` 粘住的原因。'),
             focus=['check', 'w_rej', 'verdict'],
             state={'ledger': g_kv([('KV 总量', '150（设）'), ('块预算', '—'), ('输入预算', '—')]),
                    'check': g_checks([('265 < 150', 'no')]),
                    'chunk': g_checks([('没走到', 'skip')]),
                    'commit': g_text('没走到', '不扣账'),
                    'verdict': g_text('NO_TOKEN', 'E 留队', tone='fail')}),
    ]

    return node(
        'schedloop.adder', '逐条装入', '逐条装入：先算占多少，对着几本账，装不下就停',
        '「④ 逐条装入」里面：在跑请求的预留、几本账、一条请求怎么判、什么时候停。',
        lead='你在「调度循环」的 4 号框「逐条装入」里面：`PrefillAdder` 拿一条候选，算它要占多少显存，对着几本账看装不装得下、要不要切块，装得下就扣账。'
             '用的还是 A、B、C、E 那一套设值。',
        src=SP + ' add_one_req / ' + PB,
        blocks=[scene(1220, 450, parts, wires, steps, mw=340, mh=1130,
                      cap='数都是设的（KV 池 100000、一页 = 1 个 token、ratio 0.7），没有在真实服务上验证。只覆盖基础的预算实现；SWA / Mamba 等别的显存分配后端会改写其中个别算式，没看。')],
        children=[_adder_one(), _adder_budget(), _adder_loop()])


def _adder_one():
    return node(
        'schedloop.adder.one', '判一条请求', 'PrefillAdder 判一条：占多少、要不要切块',
        '`schedule_policy.py` 里判一条请求的核心：总占量、extend、不分块的特例、切块。',
        src=SP + ':2215-2319',
        blocks=[
            code(SP, [(2215, 2229), (2243, 2250), (2272, 2282), (2291, 2319)], notes=[
                at((2217, 2219), '注释', '每请求多留一页，给页对齐的开销。',
                   q='Reserve page_size for page-alignment overhead: the paged allocator may', has='page-alignment overhead'),
                at((2220, 2226), '推断', '总占量 = 整段待填长度 − 命中前缀 + 还可能生成的 token + 每请求额外一页 + 请求槽预留。例子 A：18 − 0 + 64 + 1 = 83。',
                   has='len(req.full_untruncated_fill_ids)'),
                at(2244, '推断', '要算的量 = 整段 − 命中前缀：缓存命中多长，就少算多长。',
                   has='extend_len = len(req.full_untruncated_fill_ids) - prefix_len'),
                at((2272, 2278), '注释', '没开分块时，批里的第一条请求哪怕超过输入上限也放行。',
                   q='Without chunking, allow the first request even above the input cap.', has='self.rem_chunk_tokens is None'),
                at(2281, '注释', '预留的 `max_new_tokens` 截到 4096：只截估计，不改真正的停止条件。',
                   q='Clip the estimation of max_new_tokens for the request whose max_new_tokens is very large.', ref=SP + ':92',
                   has='CLIP_MAX_NEW_TOKENS'),
                at((2291, 2295), '注释', '同一时刻只能跟踪一条分块请求；已经有一条，另一条也要分块就拒绝。',
                   q='Only one unfinished chunked request can be tracked; a', has='has_chunked_req'),
                at((2317, 2319), '推断', '切成一块：标 `is_chunked`，`max_new_tokens` 记 0（这一块不生成）。', has='is_chunked = True'),
            ], cap='add_one_req 里判一条请求（4 号框展开的几个关口；`dllm` 分支、`requires_final_shape_check` 分支略去）'),
        ])


def _adder_budget():
    return node(
        'schedloop.adder.budget', '预算的算术', 'PrefillBudget：两本账怎么算、怎么扣',
        '总量账、当前账、严格小于、扣账：基础形态的预算算术。',
        src=PB + ':144-311',
        blocks=[
            code(PB, [(90, 91), (144, 171), (208, 215), (296, 310)], notes=[
                at(91, '注释', '偏移里含已经挂着的分配，也含 decode 的余量。',
                   q='Fixed token pool. Offsets include pending allocations and decode headroom.', has='Offsets include pending allocations'),
                at((144, 153), '推断', '余量 = 池里**可用 + 可驱逐**：radix 树里的缓存需要时可以让出，所以也算余量。',
                   has='return global_available_size + evictable'),
                at((155, 161), '推断', '两本账：总量账减 `total_offset`（含将来要生成的），当前账减 `current_offset`（只含眼前要写的）。',
                   has='self._available_and_evictable() - self.total_offset'),
                at((167, 171), '推断', '`has_capacity`：两本账的余量都要大于 0。', has='self.remaining_total > 0 and self.remaining_current > 0'),
                at((211, 215), '推断', '放行条件是**严格小于**：`total_tokens < remaining_total`，等于也不放行。', has='< self.remaining_total'),
                at((302, 310), '推断', '扣账：`immediate` = extend（取整）+ 一页 + 额外；总量账扣 `immediate + max_new`，当前账只扣 `immediate`。',
                   has='self.total_offset += immediate + max_new_tokens'),
            ], cap='基础预算类 PrefillBudget（SWA / Mamba / 链式池各有子类，不在此页）'),
        ])


def _adder_loop():
    return node(
        'schedloop.adder.loop', '装入循环', '装入循环：每个候选过几关，什么时候 break',
        '`scheduler.py` 里逐条装入的循环：预算花光就停、请求槽满就停、四种结果怎么处理。',
        src=SCH + ':4772-4895',
        blocks=[
            code(SCH, [(4772, 4778), (4800, 4813), (4845, 4845), (4867, 4895)], notes=[
                at((4775, 4777), '注释', '预算已花光就停，连第一个候选都不放进来。',
                   q='A spent pass budget admits no one, even before the first', has='AddReqResult.OTHER'),
                at((4800, 4807), '推断', '本圈已装的条数 ≥ 还能分配的请求槽数，就置 `batch_is_full`：这是「凑满请求槽」的判断。',
                   has='len(adder.can_run_list) >='),
                at((4809, 4813), '推断', '批满了：不开优先级抢占就 break。', has='batch_is_full'),
                at(4845, '推断', '每个候选在装入前**重新匹配**前缀：树在两圈之间会变，不能信排序时那次匹配。',
                   has='req.init_next_round_input(self.tree_cache'),
                at(4867, '推断', '真正的判断在 `add_one_req` 里（上一页）。', has='adder.add_one_req('),
                at((4876, 4880), '推断', '`NO_TOKEN`：置 `batch_is_full`，意思是「在跑的有请求结束之前不要再试」。',
                   has='adder.batch_is_full_after_rejection('),
                at((4890, 4894), '注释', '永远装不下的：单独收集、`continue`，循环后直接中止它并回错。',
                   q='Nothing holding memory will give it back: waiting would', has='unschedulable.append(req)'),
                at(4895, '推断', '其余结果都 `break`：**遇到第一个装不下的就停**，不是跳过它去找更小的。', has='break'),
            ], cap='逐条装入循环（4779-4799、4814-4866 的 LoRA / HiCache 预取 / 延迟器等分支略去）'),
        ])


# =====================================================================================
# 第二层：decode 时 KV 不够，撤回
# =====================================================================================
def _retract():
    parts = [
        part('check', '放得下吗', [30, 30, 170, 130], m=[76, 10, 188, 124], kind='gate', n='1',
             code='check_decode_mem',
             glyph=g_checks([('下一步放得下', 'no')]),
             say='先问：下一步 KV 够不够', why='估低了才会走到这里', trap='有请求要结束就先别撤',
             card=card(
                 '每个 decode 步之前先问分配器：这一批的每条请求再各多一个 token，KV 放不放得下（`check_decode_mem`，`python/sglang/srt/managers/schedule_batch.py:2996-3016`）。'
                 '放不下，才进入撤回（' + S(5207, 5232) + '）。',
                 ['先剔除「在途一步就会按长度结束」的请求（' + S(5198, 5200) + '）。',
                  '放不下、但有刚好要结束的请求：**这一步不撤回、也不 decode**，下一步再来（' + S(5208, 5212) + '）。',
                  '例子：显存吃紧，[B, C, A] 的下一步放不下，没有请求刚好结束，进入撤回。'],
                 [note('注释', '有请求刚好要结束，它们的 KV 本步末尾就释放了，所以先 decode 其余的、别撤回别人。',
                       q="The queued result frees the finishing requests' KV at the end of", ref=SCH + ':5209'),
                  note('推断', '装入时按估计放进来的请求，估低了才会走到这一关。')],
                 '放得下就正常 decode，不撤回；没撤回的步，`new_token_ratio` 衰减一格。'),
             to='schedloop.retract.src'),

        part('pick', '挑谁撤', [330, 30, 170, 130], m=[76, 194, 188, 130], kind='act', n='2',
             code='_get_decode_retraction_order',
             glyph=g_list(['B (40, −40)', 'C (10, −20000)', 'A (5, −18)  先撤'], on=2),
             say='已生成最少的先撤', why='丢掉的重算最少', trap='作者自己说策略不好',
             card=card(
                 '按键 `(已生成的 token 数, −输入长度)` 从大到小排，**从队尾开始弹**：已生成最少的先撤；一样少时输入最长的先撤'
                 '（' + T(3116, 3155) + '，键在 ' + T(3127) + '）。撤一条就重新问一次放不放得下，够了就停。',
                 ['例子（设）：B 已生成 40、输入 40，C 已生成 10、输入 20000，A 已生成 5、输入 18。键：B(40, −40)、C(10, −20000)、A(5, −18)。排好是 [B, C, A]，队尾是 A，先撤 A。',
                  '至少留一条：只剩一个就不再撤；最后一个都放不下才中止它（' + T(3029, 3037) + '、' + T(3082, 3103) + '）。'],
                 [note('注释', '作者在这里留了 TODO，自认撤回策略对 radix 缓存不理想。',
                       q='TODO(lsyin): improve retraction policy for radix cache', ref=SBT + ':3124'),
                  note('注释', '至少留一条。', q='Always keep at least one request', ref=SBT + ':3033'),
                  note('推断', '已生成最少 = 撤回后要重算的最少；输入最长 = 一次释放的最多。')],
                 '这个排序没把「前缀有多少能在树里复用」算进撤回代价，作者自己已经标了 TODO。'),
             to='schedloop.retract.src'),

        part('free', '释放 KV', [630, 30, 170, 130], m=[76, 388, 188, 124], kind='act', n='3',
             code='release_req',
             glyph=g_text('不写进树', '立刻驱逐几十个'),
             say='释放它的 KV，不进缓存树', why='空间要马上能用', trap='释放的是 KV，不是 token',
             card=card(
                 '把选中的请求的 KV 释放掉，**不**把它写进 radix 树（因为现在就要空间），并立刻驱逐「剩余请求数 × 20」个 token，马上用上新变得可驱逐的内存'
                 '（' + T(3057) + '、' + T(1845, 1847) + '）。',
                 ['落到 `release_kv_cache(..., checkpoint=False)`（`python/sglang/srt/managers/scheduler_components/request_retirer.py:134`）。',
                  '20 是 `SGLANG_RETRACT_DECODE_STEPS`（`python/sglang/srt/environ.py:711`）。',
                  '有些请求不能撤回（beam 组、PD 解码侧无备份等），直接中止并回错（' + T(3037, 3076) + '）；这里没展开。'],
                 [note('注释', '不写进树：现在就需要这块空间。',
                       q="release memory and don't insert into the tree because we need the space instantly", ref=SBT + ':3057'),
                  note('注释', '新变得可驱逐的内存要马上用上。', q='NOTE(lsyin): we should use the newly evictable memory instantly.', ref=SBT + ':1845')],
                 '被释放的只是 **KV**；这条请求已经生成的 token 还留着（见下一个框）。')),

        part('back', '回等待队列', [930, 30, 170, 130], m=[8, 580, 150, 140], kind='store', n='4',
             code='reset_for_retract',
             glyph=g_kv([('A', '队尾'), ('output_ids', '保留 5 个')]),
             say='回队尾，已生成的留着', why='以后重新 prefill', trap='撤回不是丢掉重来',
             card=card(
                 '被撤的请求清掉前缀长度、标 `is_retracted`，但 **`output_ids` 不清**，然后回到 `waiting_queue`（`python/sglang/srt/managers/schedule_batch.py:1530-1557`、' + S(5270, 5271) + '）。',
                 ['FCFS 下它进队尾，排在新来的请求后面（`move` 默认入队尾）。',
                  '重新 prefill 时输入 = 原 prompt + 已生成的 token：A 是 18 + 5 = 23 个 token（我的加法）（`python/sglang/srt/mem_cache/common.py:194-195`）。',
                  '撤回 = **丢掉 KV、保留已生成的 token、之后重新 prefill 它们**。撤回期间客户端的流会怎样，底稿没追。'],
                 [note('推断', '保留 `output_ids`：已经生成、甚至已经发给客户端的 token 不能再变，重来时把它们当输入重算 KV。')],
                 ['回队尾是不是有意为之，没有注释说明；底稿也没查到理由。',
                  '它之后要走的是「装入」那一关，而不是直接回到 decode。'])),

        part('ratio', '估计比例', [630, 290, 170, 130], m=[182, 580, 150, 140], kind='store', n='5',
             code='new_token_ratio',
             glyph=g_text('≈ 0.34', '重估'),
             say='撞墙后重估，之后慢慢降', why='估错就纠，平时乐观', trap='初值 0.7，不是常数',
             card=card(
                 ['装入时给在跑请求预留空间用的那个比例。撤回之后，按在批请求的现状重新估，再写回（' + S(5251) + '）。',
                  '公式：`min(1, (已生成总数 + 20 × 在批数) / (在批 max_new 总和 + 1))`（' + _r(NTR, 41, 49) + '）。'],
                 ['例子（设）：撤掉 A 后在批 B（已生成 40、`max_new` 200）、C（10、64）：(40 + 10 + 20 × 2) / (200 + 64 + 1) = 90 / 265 ≈ **0.34**。',
                  '没撤回的 decode 步：`decay_step()`，比例每步线性下降；初值 0.7、下限 0.098、衰减 600 步（' + S(5273) + '、' + _r(NTR, 21, 35) + '）。',
                  '服务空闲时重置回初值（' + S(6119, 6120) + '）。',
                  '来历：`c:4cd64b8ee6` 把撤回后的比例改成按现状重估，同一提交把初值从 0.4 改到 0.7、新增 `retract_decode_steps = 20`。'],
                 [note('提交', '撤回后的比例按在批请求的现状重新估计。', q='Auto adjust new ratio (#708)', ref='c:4cd64b8ee6'),
                  note('推断', '这是一个「估计—纠错」回路：平时乐观（比例低，并发高），撞墙后立刻变保守，然后慢慢又乐观回去。')],
                 ['0.7、0.14、600、20 这几个数为什么这样选，提交只有标题，底稿没查到理由。',
                  '「撤回」不是装入阶段的事：装入时放不下是留队等；这个比例是撤回之后才被重估的。'],)),
    ]

    wires = [
        wire('w_r1', 'check', 'pick', '放不下', ml='放不下', n='1', why='放不下才挑',
             card=card('`check_decode_mem` 不过，`retract_decode` 先排出撤回顺序（' + T(3023) + '）。',
                       '没有刚好要结束的请求，才走到这里（' + S(5208, 5229) + '）。',
                       [note('推断', '先问再撤：能不撤就不撤，因为撤回要重算。')],
                       '`retract_decode` 返回三样：撤回的列表、新比例、需要中止的列表（' + S(5229) + '）。')),
        wire('w_r2', 'pick', 'free', '挑中的', ml='挑中的', n='2', why='撤到放得下为止',
             card=card('从队尾一条条弹：弹一条、释放它的 KV，再问一次放不放得下，直到放得下为止。',
                       '循环条件是 `check_decode_mem` 不过（' + T(3029, 3031) + '）。',
                       [note('注释', '至少留一条，不会把批撤空。', q='Always keep at least one request', ref=SBT + ':3033')],
                       '例子里撤一条 A 就够了。')),
        wire('w_r3', 'free', 'back', '撤下的请求', ml='撤下的', mlp=[100, 548], n='3', why='KV 释放，token 保留',
             card=card('释放完 KV 的请求，回到等待队列。',
                       '`for req in retracted_reqs: self._add_request_to_queue(req, is_retracted=True)`（' + S(5270, 5271) + '）。',
                       [note('推断', '回队而不是直接丢弃：请求还要继续生成，只是要排队重新算 KV。')],
                       '回队后它和新请求一样要过前置闸门、逐条装入。')),
        wire('w_r4', 'free', 'ratio', '撤完重估', ml='重估', mlp=[250, 548], n='4', why='估计错了，马上纠',
             card=card('撤回完成，按现状重新估 ratio，写回 tracker。',
                       '`self.new_token_ratio_tracker.current = new_token_ratio`（' + S(5251) + '）。',
                       [note('推断', '撞墙说明上一个比例估低了；用在批请求的现状重新估，下一次装入就会按新的比例预留。')],
                       '重估出来的值可以比原来高，也可以更低——它只看在批请求的现状。')),
        wire('w_r5', 'ratio', 'ratio', '没撤回：衰减', ml='衰减', fs='b', mfs='b', cls='dash', why='每步降一点',
             card=card('每个没撤回的 decode 步，比例线性下降一格。',
                       ['`decay_step()`（' + S(5273) + '、' + _r(NTR, 34, 35) + '）。', '下限 0.098；服务空闲时重置回 0.7（' + S(6119, 6120) + '）。'],
                       [note('推断', '平时越来越乐观，直到下一次撞墙。')],
                       '衰减是按 decode 步数算的，不是按时间。')),
    ]

    steps = [
        beat('先问：下一步放得下吗',
             '（设）显存吃紧，在批 [B, C, A]。这一步 decode 之前 `check_decode_mem` 说：放不下。没有请求刚好要结束，所以不跳过，进入撤回。',
             note('注释', '有请求刚好要结束就先不撤回；这里没有。', q="The queued result frees the finishing requests' KV at the end of", ref=SCH + ':5209'),
             focus=['check']),
        beat('挑 A：已生成最少的先撤',
             '键 `(已生成, −输入长)`：B(40, −40)、C(10, −20000)、A(5, −18)，排好 [B, C, A]，从队尾弹出 **A**。撤掉 A 后再问一次：放得下了，停。',
             note('推断', '已生成最少 = 重算的最少。'),
             focus=['pick', 'w_r1'],
             state={'check': g_checks([('撤 A 之后', 'ok')])}),
        beat('释放 A 的 KV，不写进树',
             'A 的 KV 释放，**不**插进 radix 树（现在就要空间），并立刻驱逐「剩余请求数 × 20」个 token，马上用上。',
             note('注释', '不写进树，因为现在就要空间。', q="release memory and don't insert into the tree because we need the space instantly", ref=SBT + ':3057'),
             focus=['free', 'w_r2']),
        beat('A 回队尾，ratio 重估',
             'A 清掉前缀长度、标已撤回，`output_ids` 里的 5 个 token 保留，回 `waiting_queue` 队尾；以后重新 prefill 时输入是 18 + 5 = 23 个 token。'
             '同时 ratio 重估为 (40 + 10 + 20 × 2) / (200 + 64 + 1) ≈ **0.34**（设 C 的 `max_new_tokens` = 64）。',
             note('提交', '撤回后的比例按在批请求的现状重新估计。', q='Auto adjust new ratio (#708)', ref='c:4cd64b8ee6'),
             focus=['back', 'w_r3', 'ratio', 'w_r4'],
             state={'back': g_kv([('A', '队尾'), ('将重算', '18+5=23 个')])}),
        beat('之后没撤回的 decode 步：ratio 慢慢降',
             '[B, C] 这之后每个没撤回的 decode 步，比例都 `decay_step()` 降一格；平时偏乐观、并发高，直到下一次撞墙。A 在队里等装入时的余量。',
             note('推断', '估计—纠错回路：撞墙就变保守，慢慢又乐观回去。'),
             focus=['ratio', 'w_r5'],
             state={'ratio': g_text('≈ 0.34 ↓', '每步衰减')}),
    ]

    return node(
        'schedloop.retract', '撤回', '撤回：decode 时 KV 不够，才把在跑的请求退回队列',
        '「⑥ decode 一步」里面：放不下就撤回哪条、怎么撤、撤完怎么纠正估计。',
        lead='你在「调度循环」的 6 号框「decode 一步」里面。装入时放不下是**留队等**；只有 decode 时 KV 不够，才会走到这里：把在跑的请求**撤回**到等待队列，之后重新 prefill。'
             '接着例子：A 已生成 5 个，B 40 个，C 10 个（设）。',
        src=SCH + ' update_running_batch 5194-5282',
        blocks=[scene(1220, 500, parts, wires, steps, mw=340, mh=880,
                      cap='数是设的（三条请求已生成的个数、C 的 `max_new_tokens` = 64）。只讲基础的撤回：beam 组、PD 解码侧、按优先级的撤回策略没展开。')],
        children=[_retract_src()])


def _retract_src():
    return node(
        'schedloop.retract.src', '撤回源码', 'update_running_batch 与 retract_decode：先问、再挑、再撤',
        '一步 decode 前的内存检查，以及撤回的循环。',
        src=SCH + ':5198-5282',
        blocks=[
            code(SCH, [(5198, 5232), (5251, 5254), (5268, 5282)], notes=[
                at(5207, '推断', '先问：下一步 KV 放不放得下。', has='batch.check_decode_mem()'),
                at((5208, 5212), '注释', '有刚好要结束的请求就不撤回，也不 decode，等它们释放。',
                   q="The queued result frees the finishing requests' KV at the end of", has='finishing_reqs'),
                at(5229, '推断', '`retract_decode` 返回（撤回的列表，新比例，需要中止的列表）。', has='batch.retract_decode('),
                at(5251, '推断', '把重估的比例写回 tracker。', has='self.new_token_ratio_tracker.current = new_token_ratio'),
                at((5270, 5271), '推断', '撤回的请求回到等待队列。', has='_add_request_to_queue(req, is_retracted=True)'),
                at(5273, '推断', '没撤回就衰减一步。', has='decay_step()'),
            ], cap='update_running_batch：decode 之前的内存检查与撤回（5233-5250、5255-5267 的统计与日志略去）'),
            code(SBT, [(3019, 3036), (3050, 3060), (3116, 3128)], notes=[
                at(3023, '推断', '先排出撤回的顺序。', has='_get_decode_retraction_order'),
                at((3029, 3031), '推断', '撤到放得下为止。', has='check_decode_mem(selected_indices=sorted_indices)'),
                at((3032, 3034), '注释', '至少留一条。', q='Always keep at least one request', has='Always keep at least one request'),
                at(3057, '注释', '释放时不写进树：现在就要空间。',
                   q="release memory and don't insert into the tree because we need the space instantly", has="don't insert into the tree"),
                at(3124, '注释', '作者自认撤回策略对 radix 缓存不理想。',
                   q='TODO(lsyin): improve retraction policy for radix cache', has='improve retraction policy'),
                at(3127, '推断', '排序键：已生成的 token 数，再看输入长度（取负）。', has='len(req.output_ids)'),
            ], cap='retract_decode：撤回的循环与排序键（3037-3049 略去）'),
        ])
