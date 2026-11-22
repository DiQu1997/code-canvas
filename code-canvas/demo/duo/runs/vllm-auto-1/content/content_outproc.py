# 顶层「输出处理」框的下一层：token id 怎么在 API Server 进程里变成客户端看到的文字（HEAD 0e3c8dbdae）。
# 例子接顶层：prompt 20 token，模型生成 法国/的/首都/是/巴黎/。/<eos>，stream=true（DELTA），max_tokens=8。
# 本页补设（都来自第二份底稿的「设」）：stop=["巴黎。"]（扣 2 个字符）；第 1 包里有 300 个请求、我们在第 1 块；
# 「首都」「是」两包到达时客户端慢、generate() 还没回来取（合并）；S7（<eos>）在 abort 到达前已在 GPU 上。
from cv import *

ALLM = 'vllm/v1/engine/async_llm.py'
OUTP = 'vllm/v1/engine/output_processor.py'
CCL = 'vllm/v1/engine/core_client.py'
CORE = 'vllm/v1/engine/core.py'
DET = 'vllm/v1/engine/detokenizer.py'
DU = 'vllm/tokenizers/detokenizer_utils.py'
OUTS = 'vllm/outputs.py'
ENVS = 'vllm/envs.py'
SCHED = 'vllm/v1/core/sched/scheduler.py'
CFGS = 'vllm/config/scheduler.py'
SP = 'vllm/sampling_params.py'
TOP = 'tests/v1/engine/test_output_processor.py'
TSS = 'tests/detokenizer/test_stop_strings.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数）
TEXT = '法国的首都是巴黎。'


def tx(sent, new=0, held=0, cut=0, plain=0):
    """output_text 画成一排字：已发（深）/ 本包放出（绿）/ 扣住（虚线）/ 截掉（红）/ 刚解出还没判（白）。"""
    cs, i = [], 0
    for n, s in ((sent, 'pick'), (new, 'new'), (held, 'ghost'), (plain, None), (cut, 'out')):
        for ch in TEXT[i:i + n]:
            cs.append(cell(ch, '', s) if s else cell(ch, ''))
        i += n
    return g_cells(cs)


def em(allow, text, ids):
    return g_kv([('可发到', allow), ('text', text), ('token_ids', ids)])


def box(slot, ready):
    return g_kv([('槽', slot), ('ready', ready)])


def eng(big, small, tone=None):
    return g_text(big, small, tone=tone)


def inq(*cs):
    cs = list(cs)
    return g_cells(cs + [cell()] * max(0, 2 - len(cs)), left='新', right='旧')


def hd(pkg, chunks, now):
    return g_kv([('本包', pkg), ('分块', chunks), ('当前', now)])


def st(ok):
    return g_checks([('…a1b2c3d4 在表里', 'ok' if ok else 'no')])


def cl(text, small='客户端已收到', tone='run'):
    return g_text(text, small, tone=tone)


# ---------------------------------------------------------------- 机器图
def _scene():
    parts = [
        part('engine', '引擎（对岸）', [10, 96, 150, 140], m=[20, 8, 210, 100], kind='ext', code='EngineCore',
             glyph=eng('—', '还没回包'),
             say='每步送回一包结果', why='只判 EOS / 长度', trap='不知道 stop 串',
             card=card(
                 'EngineCore 进程。每跑完一步，把**这一步所有请求**的新 token 打成一个 `EngineCoreOutputs` 送过来；收到 ABORT 就把请求停掉。',
                 ['一包里每个请求一条 `EngineCoreOutput(request_id, new_token_ids, finish_reason, stop_reason, …)`。' + R('vllm/v1/engine/__init__.py', 275, 293),
                  '它自己判的「结束」只有 EOS / stop_token_ids / 长度；stop **串**它不判，要靠这一层反向通知。'],
                 [note('推断', '把 stop 串放在前端判：只有前端手里有增量文字。仓库里没有注释或提交说明这条理由（底稿 §5 也列为没查到）。')],
                 ['收到 ABORT 后它只改调度器状态，**不回任何回执**：前端早已自己把请求收尾了。' + R(CORE, 612, 617)])),

        part('inbox', '收包 + 队列', [290, 96, 180, 140], m=[20, 172, 210, 124], kind='bg', n='1', code='outputs_queue',
             glyph=inq(),
             say='另一个 task 收帧、解码、入队', why='收包和处理分开，互不等', trap='队列无界，没有反压',
             card=card(
                 '把 ZMQ 结果管道上来的帧收下、解码成 `EngineCoreOutputs`，放进 `outputs_queue` 等着被处理。',
                 ['一个常驻 task（`EngineCoreOutputQueueTask`）：`recv_multipart` → `decoder.decode(frames)` → `put_nowait`。' + R(CCL, 1249, 1254) + R(CCL, 1294, 1295),
                  '`outputs_queue` 是一个**没有 maxsize** 的 `asyncio.Queue`。' + R(CCL, 1209)],
                 [note('注释', 'IO 单独一个 task：处理那边每次 `await`（比如块间让出），这里都能插进来继续收包。',
                       q='Perform IO in separate task to parallelize as much as possible.', ref=A(CCL, 1233))],
                 ['它和处理任务**在同一个事件循环线程上**，不是并行的线程；「并行」指的是在对方 await 的空当里收包。',
                  '队列没有上限：处理跟不上时包会在这里堆着，不会让引擎慢下来。']),
             to='outproc.handler_src'),

        part('handler', '分块处理', [600, 96, 180, 140], m=[20, 360, 210, 130], kind='act', n='2', code='output_handler',
             glyph=hd('—', '—', '—'),
             say='取一包，每 128 条一块', why='不让一包独占事件循环', trap='分块不是并行',
             card=card(
                 '常驻 task：取一包，切成每块最多 128 条，逐块交给 `process_outputs`；块与块之间让出一次事件循环；每块做完立刻把该 abort 的请求发回引擎。',
                 ['`await get_output_async()` 取包 → `for start in range(0, n, chunk_size)` 切片 → 同步调用 `process_outputs`。' + R(ALLM, 894, 924),
                  '块后 `if end < num_outputs: await asyncio.sleep(0)`；然后 `if reqs_to_abort: await abort_requests_async(...)`。' + R(ALLM, 928, 936),
                  '块大小 = `VLLM_V1_OUTPUT_PROC_CHUNK_SIZE`，默认 128。' + R(ENVS, 1593, 1595)],
                 [note('注释', '`process_outputs` 是同步函数，一包几百个请求一口气跑完，中间别的协程都动不了；切块是为了不长时间堵住事件循环。',
                       q="VLLM_V1_OUTPUT_PROC_CHUNK_SIZE, so that we don't block the", ref=A(ALLM, 902)),
                  note('注释', '块大了：各条消息之间的间隔抖动变大；块小了：首字延迟和吞吐变差。128 这个值本身没有找到依据。',
                       q='Setting this too high can result in a higher variance of', ref=A(ENVS, 1590))],
                 ['分块**不是并行**：同一个线程上一块接一块跑，只是块间给别的协程一个插队的机会。',
                  '`process_outputs` 在这里返回的 `request_outputs` 必须是空的——输出早已都塞进各自的信箱。' + R(ALLM, 925, 926)]),
             to='outproc.handler_src'),

        part('states', '查请求状态', [910, 96, 200, 140], m=[20, 554, 210, 120], kind='gate', n='3', code='request_states',
             glyph=st(True),
             say='用内部 ID 找到这个请求的状态', why='查不到 = 已结束，尾巴直接丢', trap='没有「已 abort」标记，只看在不在',
             card=card(
                 '一张表：内部请求 ID → `RequestState`。每条输出先在这里找主人；找不到就跳过。',
                 ['`RequestState` 是每请求一份：去分词器、logprobs 处理器、信箱、`output_kind`、`stream_interval`、已发 token 偏移……' + R(OUTP, 184, 249),
                  '登记发生在 `add_request` 的第一个 `await` 之前（顶层第 4 步）；结束时 `_finish_request` 把它从表里删掉。' + R(OUTP, 920, 922),
                  '`req_state = self.request_states.get(req_id)`，`None` 就 `continue`。' + R(OUTP, 762, 765)],
                 [note('注释', '「已 abort / 已结束」不另记标志：状态删了，之后迟到的输出自然查不到、被丢掉。',
                       q='Ignore output for already-aborted request.', ref=A(OUTP, 764)),
                  note('推断', '这样 stop 串命中后前端可以立刻收尾，不用等引擎确认：引擎多算出来的东西回来时一查就丢。')],
                 ['stop 串命中时，就在这里决定要不要发 abort：前端收尾了、但引擎这一包里**没说**结束（`finished` 为假），才把 ID 记进 `reqs_to_abort`。' + R(OUTP, 880, 885)]),
             to='outproc.process_src'),

        part('detok', '去分词 + 判 stop 串', [900, 306, 310, 160], m=[15, 738, 310, 140], kind='store', n='4', code='detokenizer.update()',
             glyph=g_kv([('stop', '"巴黎。"'), ('扣住', '3 − 1 = 2 个字'), ('已发到', '0')]),
             say='新 token 接到 output_text 后面，再查有没有凑出 stop 串', why='一个 token 不等于一段独立的字',
             trap='按字截，不按 token 截',
             card=card(
                 '每请求一个增量去分词器：记着这个请求**到目前为止解出的全部文字** `output_text`，每来一批新 token 就接上去，并检查有没有凑出 stop 串，凑出了就截断。',
                 ['`update(new_token_ids, 引擎是否已判 STOP)`：逐个 token `decode_next` 追加到 `output_text`，同时记下每个 token 结束时文字有多长。' + R(DET, 161, 170),
                  '再调 `check_stop_strings`：只在**新增的文字**（连同可能横跨新旧的那一段）里找；找到就把 `output_text` 截到 stop 串**起点**。' + R(DET, 178, 199) + R(DET, 400, 401),
                  '解码有快慢两条路：快的用 HF `tokenizers` 的 `DecodeStream`，慢的是 Python 的滑动窗口。' + R(DET, 103, 112),
                  '图上的字格：深色 = 已发给客户端，绿 = 这一包放出去，虚线 = 扣住没发，红 = 被 stop 串截掉。'],
                 [note('注释', '为什么不能每个 token 单独 decode 再拼：一个字符可能被拆成几个 token，单独解前半个只得到 `�`，要等下一个 token 再一起解。',
                       q="utf-8 char at the end means it's a potential unfinished byte sequence", ref=A(DU, 270)),
                  note('注释', '空格加不加也取决于前后的 token，所以要带着前文一起解，再减掉已经发过的那部分。',
                       q='The prefix text is necessary only to defeat cleanup algorithms in', ref=A(DU, 245)),
                  note('注释', '只搜新文字，不重搜旧的：每步的开销和新增量成正比，不和总长度成正比。',
                       q='Avoid searching already-searched text.', ref=A(DET, 400))],
                 ['stop 串是**按字符**截的，可以截在一个 token 中间：测试里 stop=`"gani"` 落在 token「organization」里，输出停在 `"…volunteer or"`。' + R(TSS, 74, 81),
                  '`token_ids` 和文字不同步：被截掉的是文字；`token_ids` 只删「凑出 stop 串那个 token 之后」的（投机解码一步回来多个时才会有）。' + R(DET, 194, 197)]),
             to=['outproc.update_src', 'outproc.decode_src']),

        part('emit', '扣字放行 + 打包', [600, 306, 180, 160], m=[20, 942, 210, 150], kind='gate', n='5', code='make_request_output',
             glyph=em('—', '—', '—'),
             say='只放出「不可能是 stop 串开头」的字', why='发出去的字收不回来', trap='text 晚，token_ids 不晚',
             card=card(
                 '决定这一包给客户端多少字，再做成一个 `RequestOutput`（DELTA：只装新增的那一截）。',
                 ['扣多少：`stop_buffer_length = max(len(s) for s in stop) - 1`；只在有 stop 串、且不要求输出 stop 串时扣，否则 0。' + R(DET, 131, 136),
                  '放多少：`可发到 = len(output_text) − 扣住数`；本包 `text = output_text[已发到 : 可发到]`，再把「已发到」推过去。**结束时不扣**，剩下的全放。' + R(DET, 207, 222),
                  '打包前还有一道 `stream_interval` 闸：默认 1 不挡；设成 N 时只有「第一个 / 结束 / 攒够 N 个 token」才出包，没出的 token 下次一起带上。' + R(OUTP, 382, 403)],
                 [note('推断', '为什么是「最长 stop 串长度 − 1」：长 L 的 stop 串完整出现之前，末尾最多有 L−1 个字是它的开头；这几个字一旦发出去，stop 串凑齐时就收不回来了。仓库里没有注释写这个推导。'),
                  note('文档', '`stream_interval` 调大是为了省主机开销：少造包、少唤醒、少序列化；代价是不那么「一个字一个字」。',
                       q='while a larger value (e.g., 10) reduces host overhead and may increase throughput', ref=A(CFGS, 287))],
                 ['**文字晚，token 不晚**：DELTA 包里 `token_ids` 是这一步的新 token，`text` 却被扣了 2 个字，两者在包里对不上。' + R(OUTP, 407) + R(OUTP, 490),
                  '`stream_interval` 和信箱合并都让包变少，但这里是**事前不造包**，信箱是**事后合并**。',
                  '非流式（`FINAL_ONLY`）在这里直接不出中间包，但去分词每步照跑。' + R(OUTP, 368, 372)]),
             to='outproc.emit_src'),

        part('mailbox', '单槽信箱', [290, 306, 180, 160], m=[20, 1156, 210, 130], kind='store', n='6', code='RequestOutputCollector',
             glyph=box('空', '—'),
             say='至多一个包；没取走就合并进去', why='慢客户端不会让包无限堆积', trap='合并 ≠ 丢包，字全在',
             card=card(
                 '每请求一个「信箱」，只有一个槽，夹在生产者（处理任务）和消费者（这个请求的 `generate()`）之间。',
                 ['`put`：槽空 → 放进去，`ready.set()`；槽里已有一个 `RequestOutput` → `add(..., aggregate)` 合并进去。' + R(OUTP, 70, 80),
                  '合并（DELTA）：`text` 拼接、`token_ids` 拼接、logprobs 拼接；`finish_reason`、`stop_reason`、`cumulative_logprob` 取最新的。' + R(OUTS, 220, 249),
                  '`get`：槽空就 `await ready.wait()`；取走后清槽、清事件。' + R(OUTP, 86, 94)],
                 [note('注释', '流式时生产者跑到消费者前面，就把几个包并成一个。',
                       q='When streaming deltas, RequestOutputs are merged if the', ref=A(OUTP, 58)),
                  note('推断', '单槽 + 合并：每请求最多压一个包，内存有上界；几个增量合成一次唤醒，少几次任务切换。提交 `c:9d72daf4ce` 把原来每请求一个 `asyncio.Queue` 换成了它，但提交正文是空的。'),
                  note('推断', '不用加锁：`put` 和 `get` 里没有 `await` 打断，又都在同一个事件循环线程上，不会交错。')],
                 ['非 DELTA 模式下不是拼接而是**整个替换**：那种包本来就是「到目前为止的全部」。' + R(OUTS, 250, 252),
                  '来的是异常时**无条件覆盖**槽里没读的数据；`generate()` 取到就抛出。' + R(OUTP, 72, 74)]),
             to='outproc.mailbox_src'),

        part('gen', '请求的 generate()', [10, 306, 150, 160], m=[20, 1350, 210, 110], kind='ext', code='generate()',
             glyph=cl('—'),
             say='取一包，yield 给协议层', why='能不等就不等', trap='它慢，信箱就合并',
             card=card(
                 '消费者，不在这个框里：每个请求一个协程，守着自己的信箱，取到一个就 `yield` 给协议层拼成 SSE 块（顶层「引擎门面」框）。',
                 ['`out = q.get_nowait() or await q.get()`；`out.finished` 为真就退出循环。' + R(ALLM, 806, 816)],
                 [note('注释', '先试着不 await 地取：高负载下少一次任务切换。', q='drain queue without await if possible', ref=A(ALLM, 807))],
                 ['它写 SSE 时卡在网络上，就暂时不会回来取信箱——这就是「生产者跑到消费者前面」的来源。']),
             to='outproc.mailbox_src'),
    ]

    wires = [
        wire('w_res', 'engine', 'inbox', '一包结果', ml='结果帧', n='1', why='一包 = 一整批请求',
             card=card('引擎的输出线程把一步的结果编码后从 PUSH 发出，这边 PULL 收下。',
                       '收下 → 解码 → 不是 utility 回包就 `outputs_queue.put_nowait(outputs)`。' + R(CCL, 1252, 1254) + R(CCL, 1294, 1295),
                       [note('推断', '一包装一整批请求，跨进程的消息数和并发数无关，只和步数有关。')],
                       '包里是这一步**所有**请求的结果，不是一个请求一包。')),
        wire('w_pull', 'inbox', 'handler', '取一包', n='2',
             card=card('处理任务从队列取出最早的一包。', '`outputs = await engine_core.get_output_async()`；队列空就在这里等。' + R(ALLM, 894),
                       [note('推断', '一个先进先出的队列把「收」和「处理」解开：处理慢一点时包先排着，收包不受影响。')])),
        wire('w_chunk', 'handler', 'states', '每块 ≤128 条', ml='一块', n='3', why='块间 sleep(0) 让一下',
             card=card('把一块（最多 128 条）交给 `process_outputs`，它逐条处理：找状态 → 去分词 → 放行 → 打包 → 进信箱。',
                       ['同步调用，在事件循环线程里直接跑完这一块。' + R(ALLM, 919, 924),
                        '整包共用一份统计，块全部做完后才更新调度器统计、写日志。' + R(ALLM, 938, 945)],
                       [note('注释', '块之间让出一次，别的协程（比如刚被信箱叫醒的 `generate()`）先跑。',
                             q='Allow other asyncio tasks to run between chunks', ref=A(ALLM, 928))],
                       '最后一块之后不 sleep：反正马上回去等下一包。'),
             to='outproc.handler_src'),
        wire('w_found', 'states', 'detok', '查到了：交新 token', ml='交新 token', n='4',
             card=card('找到状态后，把这条输出里的新 token 交给这个请求自己的去分词器。',
                       '`detokenizer.update(new_token_ids, finish_reason == STOP)`；调用前先记下已有几个输出 token，命中 stop 串时用来算多出的 token。' + R(OUTP, 827, 836),
                       [note('推断', '先记 `num_prev_tokens` 再 `update`：顺序不能反，否则算不出这一步保留了几个 token。')],
                       '第二个参数是「**引擎**判了 STOP（EOS 之类）」，那时（默认不输出 stop 内容）最后一个 token 不解进文字。' + R(DET, 153, 159)),
             to='outproc.process_src'),
        wire('w_text', 'detok', 'emit', '新文字 / 命中的 stop', ml='新文字', n='5',
             card=card('`update` 返回命中的 stop 串（没命中是 `None`）；命中就把 `finish_reason` 改成 STOP、`stop_reason` 改成那个串。',
                       '覆盖引擎给的值，哪怕引擎说的是 length。' + R(OUTP, 831, 833),
                       [note('推断', '同一请求的「结束」有两处在判：引擎判 EOS / 长度，这里判 stop 串，以这里最后一次覆盖为准。')])),
        wire('w_put', 'emit', 'mailbox', 'put', n='6', why='不等消费者',
             card=card('把做好的 `RequestOutput` 放进这个请求的信箱。', '`req_state.queue.put(request_output)`；闸挡住时（返回 `None`）就不放。' + R(OUTP, 852, 866),
                       [note('推断', '`put` 不会阻塞：生产者处理一整块几百个请求，不能被任何一个慢客户端拖住。')])),
        wire('w_take', 'mailbox', 'gen', '取走', ml='取走', n='7',
             card=card('`generate()` 取走槽里的包（可能是合并过的），交给协议层。', '`get_nowait()` 先试，空了才 `await get()`。' + R(OUTP, 96, 104) + R(ALLM, 809),
                       [note('注释', '能不等就不等。', q='drain queue without await if possible', ref=A(ALLM, 807))])),
        wire('w_ids', 'states', 'handler', '要 abort 的 ID', ml='abort ID', cls='dash', why='前端先收尾', mlp=[290, 522],
             card=card('stop 串命中、前端已经收尾、但引擎还以为这个请求在跑：把内部 ID 带回给处理任务。',
                       ['`process_outputs` 返回 `reqs_to_abort`。' + R(OUTP, 894, 897),
                        '条件是 `not engine_core_output.finished`：如果 stop 串和引擎的 EOS / 长度**同一步**命中，引擎已经自己停了，就不发。' + R(OUTP, 881, 885)],
                       [note('注释', '引擎那边可能还留着这个请求：stop 串是在前端发现的。',
                             q='EngineCore may still have the request: a stop string', ref=A(OUTP, 882))]),
             to='outproc.process_src'),
        wire('w_abort', 'handler', 'engine', 'ABORT（反向）', ml='ABORT', cls='dash', fs='t', ts='t', mfs='r', mts='r',
             why='每块做完就发', trap='引擎不回执',
             card=card('处理任务把这一块攒下的 ID 用 ABORT 消息发回引擎，让它停算。',
                       ['`await engine_core.abort_requests_async(ids)`，走的是请求管道；空列表不发，引擎已死也不发。' + R(ALLM, 932, 936) + R(CCL, 1379, 1381),
                        '引擎的输入线程把 ABORT **同时**放进 `aborts_queue` 和 `input_queue`；主线程每步处理模型输出之前先清 `aborts_queue`，调度器把请求标成 `FINISHED_ABORTED`。' + R(CORE, 2295, 2300) + R(CORE, 612, 617)],
                       [note('注释', '放两个队列：既能尽早处理，又不打乱顺序；调度器里重复 abort 没关系。',
                             q='Aborts are added to *both* queues, allows us to eagerly', ref=A(CORE, 2296)),
                        note('注释', '为什么要发：处理到这里的请求是因为 stop 串结束的。',
                             q='Abort any reqs that finished due to stop strings.', ref=A(ALLM, 932))],
                       ['abort 到达前在途的步照样会算完、回来，被「查请求状态」丢掉；已经到了引擎的，调度器也会跳过。' + R(SCHED, 2415, 2423),
                        '客户端断开走的是另一条路：先在前端删状态、往信箱塞一个 `finish_reason=abort` 的收尾包，再发给引擎。' + R(OUTP, 602, 663)]),
             to='outproc.handler_src'),
    ]

    steps = [
        beat('开局：状态已经备好',
             '顶层第 4 步登记时，这个请求就有了自己的 `RequestState`。本页设它带 `stop=["巴黎。"]`：去分词器算出**扣住 3 − 1 = 2 个字**；`stream=true` → DELTA，信箱 `aggregate=True`（会合并）。',
             note('注释', '扣住的字数：stop 串不进输出时才扣。', q='Number of chars to hold back when stop strings are to be excluded', ref=A(DET, 131)),
             focus=['detok', 'states', 'mailbox'],
             state={'mailbox': box('空', '未置位')}),
        beat('第 1 包到了：300 个请求的结果',
             '引擎第 1 步的结果回来。设这一包里有 **300** 个请求，我们这条是 `new_token_ids=[<法国>]`。收包 task 解码后 `put_nowait` 进 `outputs_queue`。',
             note('注释', '收包是单独的 task，和处理任务互不等待。', q='Perform IO in separate task to parallelize as much as possible.', ref=A(CCL, 1233)),
             focus=['inbox', 'w_res', 'engine'],
             state={'engine': eng('第1步', '300 条结果'),
                    'inbox': inq(cell('包1', '300 条', 'new'))}),
        beat('取包，切成 128 / 128 / 44',
             '`output_handler` 取出这一包：300 条切成 `[0:128]`、`[128:256]`、`[256:300]` 三块。设我们的请求在第 1 块。第 1 块交给 `process_outputs`，同步跑完。',
             note('注释', '分块是为了不长时间堵住事件循环。', q="VLLM_V1_OUTPUT_PROC_CHUNK_SIZE, so that we don't block the", ref=A(ALLM, 902)),
             focus=['handler', 'w_pull', 'w_chunk'],
             state={'inbox': inq(),
                    'handler': hd('300 条', '128·128·44', '第 1 块')}),
        beat('查到状态，「法国」解进 output_text',
             '用内部 ID `…-a1b2c3d4` 在 `request_states` 里查到了。`update([<法国>])`：`output_text` 从 `""` 变成 **`法国`**（2 个字）。查 stop 串：没有「巴黎。」。',
             note('推断', '去分词每步都做、每个请求都做，所以它的状态必须跟着请求走，不能是全局的。'),
             focus=['detok', 'w_found', 'states'],
             state={'detok': tx(0, plain=2)}),
        beat('扣字：一个字都不放',
             '可发到 = 2 − 2 = **0**。本包 `text=""`，`token_ids=[<法国>]`。客户端这一刻看不到任何字——「法国」可能是 stop 串的开头吗？不是，但规则只看长度，不看内容。',
             note('推断', '只按长度扣，不判断末尾是不是真的像 stop 串开头：规则简单，代价是总晚 2 个字。仓库没有说明为什么不做更细的判断。'),
             focus=['emit', 'w_text', 'detok'],
             state={'detok': tx(0, held=2),
                    'emit': em('2−2=0', '""', '[法国]')}),
        beat('进信箱；块间让一下',
             '信箱槽空 → 放进去，`ready.set()`。第 1 块做完，`128 < 300` → `await asyncio.sleep(0)`：`generate()` 趁这时醒来取走空文字的包，然后才轮到第 2 块。',
             note('注释', '块之间让出事件循环，别的协程先跑。', q='Allow other asyncio tasks to run between chunks', ref=A(ALLM, 928)),
             focus=['mailbox', 'w_put', 'w_take', 'gen', 'handler'],
             state={'mailbox': box('空（已取走）', '已清'),
                    'gen': cl('""', '还没有字'),
                    'handler': hd('300 条', '128·128·44', '第 2 块')}),
        beat('「的」：放出 1 个字',
             '下一步回来 `[<的>]`：`output_text = 法国的`（3）。可发到 = 3 − 2 = **1**，本包 `text="法"`。信箱空 → 放入 → `generate()` 取走，客户端看到「法」。',
             note('推断', '已发到的位置记在去分词器里（`_last_output_text_offset`），每次只取它到「可发到」之间的那一截。'),
             focus=['emit', 'detok', 'mailbox', 'gen'],
             state={'engine': eng('第2步', '…'),
                    'detok': tx(0, new=1, held=2),
                    'emit': em('3−2=1', '"法"', '[的]'),
                    'mailbox': box('空（已取走）', '已清'),
                    'gen': cl('法')}),
        beat('客户端慢：两包在信箱里合并',
             '设 `generate()` 正卡在写 SSE 上，没回来取。「首都」到：`法国的首都`（5），放出 `"国的"`，槽空 → 放入。「是」到：`法国的首都是`（6），放出 `"首"`，槽**非空** → 合并：`text="国的首"`，`token_ids=[首都, 是]`。',
             note('注释', '生产者跑到消费者前面，就把几个包并成一个。', q='When streaming deltas, RequestOutputs are merged if the', ref=A(OUTP, 58)),
             focus=['mailbox', 'w_put', 'emit', 'detok'],
             state={'detok': tx(1, new=3, held=2),
                    'emit': em('6−2=4', '"首"', '[是]'),
                    'mailbox': box('"国的首"', 'set'),
                    'engine': eng('第4步', '…')}),
        beat('generate() 回来，一次取走',
             '`get_nowait()` 一次拿走合并好的包：客户端收到**一个**块「国的首」，而不是两个。现在客户端有「法国的首」，模型其实已经解出「法国的首都是」——差 2 个字。',
             note('推断', '字一个没丢，只是块变粗；客户端越慢，块越粗，信箱始终只压一个包。'),
             focus=['gen', 'w_take', 'mailbox'],
             state={'detok': tx(4, held=2),
                    'mailbox': box('空（已取走）', '已清'),
                    'gen': cl('法国的首')}),
        beat('「巴黎」：放出「都是」',
             '`output_text = 法国的首都是巴黎`（8）。可发到 = 8 − 2 = **6**，本包 `text="都是"`。「巴黎」被扣住——它正是 stop 串「巴黎。」的前两个字。客户端：「法国的首都是」。',
             note('推断', '这就是扣字要防的情形：如果「巴黎」已经发出去，下一步凑齐「巴黎。」时就收不回来了。'),
             focus=['detok', 'emit', 'gen'],
             state={'engine': eng('第5步', '…'),
                    'detok': tx(4, new=2, held=2),
                    'emit': em('8−2=6', '"都是"', '[巴黎]'),
                    'gen': cl('法国的首都是')}),
        beat('「。」凑出 stop 串，截掉',
             '`output_text = 法国的首都是巴黎。`（9）。只在新字附近搜：起点 = 1 − 新字数 1 − stop 长 3 = −3，即从倒数第 3 个字找起，「巴黎。」在第 **6** 位 → 截成 `法国的首都是`。`finish_reason` 改成 `stop`，`stop_reason="巴黎。"`。',
             note('注释', '只搜新增的文字，不重搜已经搜过的。', q='Avoid searching already-searched text.', ref=A(DET, 400)),
             focus=['detok', 'w_text', 'emit'],
             state={'engine': eng('第6步', '。（引擎没判结束）'),
                    'detok': tx(6, cut=3),
                    'emit': g_kv([('finish', 'stop'), ('stop_reason', '"巴黎。"'), ('token_ids', '[。]')])}),
        beat('最后一包：结束时不再扣',
             '结束了，扣住数变 0：可发到 = 6，已发到也是 6 → 本包 `text=""`，带 `finished=True`、`finish_reason="stop"`。进信箱，`generate()` 取到后退出循环；协议层补上 `finish_reason` 和 `data: [DONE]`（顶层最后一步）。',
             note('注释', '结束时把剩下的文字全给出去。', q='We return the full output text if the sequence is finished.', ref=A(DET, 210)),
             focus=['emit', 'w_put', 'mailbox', 'w_take', 'gen'],
             state={'emit': em('6−0=6', '""（finished）', '[。]'),
                    'mailbox': box('空（已取走）', '已清'),
                    'gen': cl('法国的首都是', 'finish_reason=stop', tone='ok')}),
        beat('前端收尾，反向 ABORT',
             '`_finish_request` 把 `…-a1b2c3d4` 从 `request_states` 删掉。引擎这一包里**没说**结束 → ID 进 `reqs_to_abort`。这一块做完，处理任务马上 `abort_requests_async` 发出 ABORT；引擎下一步处理输出前先清掉它。',
             note('注释', '引擎那边可能还留着这个请求：stop 串是前端发现的。', q='EngineCore may still have the request: a stop string', ref=A(OUTP, 882)),
             focus=['w_ids', 'handler', 'w_abort', 'engine', 'states'],
             state={'states': st(False),
                    'engine': eng('ABORT', '下一步前先处理', tone='fail')}),
        beat('在途那一步的尾巴，查不到就丢',
             'async scheduling 下 ABORT 到之前，第 7 步已经排进 GPU，结果是 `<eos>`。它照常回到前端，到「查请求状态」：`request_states.get(…)` → `None` → `continue`。客户端什么都不会多收到。',
             note('注释', '迟到的输出属于已经结束的请求，直接忽略。', q='Ignore output for already-aborted request.', ref=A(OUTP, 764)),
             focus=['states', 'w_chunk', 'inbox'],
             state={'inbox': inq(cell('包7', '<eos>', 'new')),
                    'engine': eng('第7步', '白算了一步', tone='fail')}),
    ]

    return scene(1220, 480, parts, wires, steps, mw=340, mh=1476,
                 cap='API Server 进程里、同一个事件循环线程上的一条流水线（本页设 stop=["巴黎。"]）。字格：深色 = 已发给客户端 · 绿 = 这一包放出 · 虚线 = 扣住 · 红 = 被截掉')


# ---------------------------------------------------------------- 源码页
def _handler_src():
    return node(
        'outproc.handler_src', '收包与分块', '收包 task 与 output_handler：入队、分块、反向 ABORT',
        '图上「收包 + 队列」「分块处理」两个框和 ABORT 那条线的源码：收包怎么入队，一包怎么切块，块间让出，abort 怎么发、引擎怎么收。',
        lead='你在「输出处理」这张图的第 1、2 个框里（顶层「输出处理」框的入口）。五段源码按数据流的顺序：收帧入队 → 取包分块 → 发 ABORT → 引擎收 ABORT → 在途的尾巴被调度器跳过。',
        src=ALLM + ':888-936',
        blocks=[
            code(CCL, [(1233, 1237), (1249, 1254), (1294, 1295)], notes=[
                at(1233, '注释', '收包单独一个 task：处理任务 await 的空当里，这里继续收。', q='Perform IO in separate task to parallelize as much as possible.', has='Perform IO in separate task'),
                at(1252, None, '等引擎送来下一包（多帧消息）。', has='recv_multipart'),
                at(1254, None, '解码成 `EngineCoreOutputs`：一整批请求的结果。', has='decoder.decode(frames)'),
                at(1295, None, '放进 `outputs_queue`。这个队列没有 maxsize，不会反压引擎。', has='put_nowait(outputs)'),
            ], cap='收包 task：process_outputs_socket'),
            code(ALLM, [(888, 936)], notes=[
                at(888, None, '块大小来自环境变量 `VLLM_V1_OUTPUT_PROC_CHUNK_SIZE`，默认 128。', has='chunk_size = envs.VLLM_V1_OUTPUT_PROC_CHUNK_SIZE'),
                at(894, None, '从队列取出最早一包；队列空就在这里挂起。', has='get_output_async()'),
                at((901, 907), '注释', '按固定步长切片：本例 300 条 → [0:128]、[128:256]、[256:300]。为的是不让一包独占事件循环。',
                   q="VLLM_V1_OUTPUT_PROC_CHUNK_SIZE, so that we don't block the", has='for start in range(0, num_outputs, chunk_size)'),
                at((919, 924), None, '同步调用：这一块里的去分词、判 stop、放信箱全在这里做完，中间不让出。', has='output_processor.process_outputs('),
                at((925, 926), None, '异步模式下输出都已塞进各自的信箱，返回的列表必须是空的（列表只给同步的 LLMEngine 用）。', has='assert not processed_outputs.request_outputs'),
                at((928, 930), '注释', '还有下一块才让一次：`sleep(0)` 是让出事件循环的最小写法，刚被叫醒的 `generate()` 先去取信箱。',
                   q='Allow other asyncio tasks to run between chunks', has='await asyncio.sleep(0)'),
                at((932, 936), '注释', '每块做完立刻发这一块的 abort，不等整包。为什么按块发而不是整包发一次，仓库里没写。',
                   q='Abort any reqs that finished due to stop strings.', has='abort_requests_async'),
            ], cap='output_handler：取包 → 分块 → 处理 → 让出 → 发 abort'),
            code(CCL, [(1379, 1381)], notes=[
                at((1380, 1381), None, '空列表不发，引擎已经死了也不发；发的是请求管道上的一条 ABORT 消息。', has='EngineCoreRequestType.ABORT'),
            ], cap='发 ABORT'),
            code(CORE, [(2295, 2300), (612, 617)], notes=[
                at((2296, 2300), '注释', '引擎输入线程：ABORT 同时进两个队列，主线程每步先清 `aborts_queue`；在调度器里重复 abort 是幂等的。',
                   q='aborting in the scheduler is idempotent.', has='self.aborts_queue.put_nowait(request)'),
                at(617, None, '只改调度器状态（`FINISHED_ABORTED`），不往前端回任何东西——前端早已收尾。', has='FINISHED_ABORTED'),
            ], cap='引擎那头：收 ABORT'),
            code(SCHED, [(2415, 2423)], notes=[
                at((2415, 2418), '注释', 'ABORT 已到引擎、但还有在途的步：结果回来时请求已结束，调度器直接跳过。',
                   q='request is aborted while the model is executing it (e.g.,', has='request.is_finished()'),
            ], cap='调度器里丢掉在途步的结果'),
        ])


def _process_src():
    return node(
        'outproc.process_src', 'process_outputs', 'process_outputs：一条输出从找状态到进信箱',
        '图上「查请求状态」框和两条线的源码：查不到就丢、去分词、stop 命中改写结束原因、截掉多余 token、放进信箱、决定要不要 abort。',
        lead='你在「输出处理」这张图的第 3 个框（查请求状态）里。下面是每条输出在 `process_outputs` 循环里走的那一段，按执行顺序。',
        src=OUTP + ':758-922',
        blocks=[
            code(OUTP, [(758, 765), (826, 836), (845, 845), (851, 866), (871, 885)], notes=[
                at((762, 765), '注释', '用内部 ID 找状态；找不到说明请求已经结束或被 abort，这条输出直接丢。本例第 7 步的 `<eos>` 死在这里。',
                   q='Ignore output for already-aborted request.', has='self.request_states.get(req_id)'),
                at(827, '推断', '先记下已有几个输出 token，再 `update`：命中 stop 串时要靠它算出这一步保留了几个。', has='num_prev_tokens'),
                at((828, 830), None, '去分词 + 判 stop 串；第二个参数表示引擎已判 STOP（EOS 等）。返回命中的 stop 串或 `None`。', has='detokenizer.update('),
                at((831, 833), None, '命中 stop 串就覆盖结束原因：`finish_reason=STOP`、`stop_reason="巴黎。"`，哪怕引擎说的是别的。', has='finish_reason = FinishReason.STOP'),
                at((834, 836), '推断', '截掉越过 stop 串的 token。它**原地**改 `engine_core_output.new_token_ids`，而第 782 行的 `new_token_ids` 是同一个 list，所以后面打包时看到的已是截短的。', has='_trim_surplus_tokens'),
                at(845, '推断', 'logprobs 在截短之后才处理，才能和留下来的 token 对齐。', has='update_from_output'),
                at((852, 860), None, '打包：这里面做扣字放行和 `stream_interval` 闸；闸挡住时返回 `None`，下面就不放信箱。', has='make_request_output('),
                at((864, 866), None, '异步模式：放进这个请求的单槽信箱，叫醒 `generate()`。', has='req_state.queue.put(request_output)'),
                at(880, None, '收尾：把状态从 `request_states` 删掉——之后这个请求的任何迟到输出都会在第 762 行被丢。', has='self._finish_request(req_state)'),
                at((881, 885), '注释', '引擎这一包里没说结束（`finished` 为假），说明是前端发现的 stop 串，引擎还在算：记下来发 abort。同一步引擎也判了结束就不发。',
                   q='EngineCore may still have the request: a stop string', has='reqs_to_abort.append(req_id)'),
            ], cap='process_outputs 的循环体（节选）'),
            code(OUTP, [(899, 910)], notes=[
                at((906, 907), None, '保留数 = `update` 之后的输出 token 数 − 之前的；多出来的就是 stop 串之后的。', has='num_kept'),
                at(910, '提交', '原地删：投机解码一步回来多个 token 时，stop 串之后的那些不能留在输出里。',
                   q='[Bugfix][Spec Decode] Trim token_ids/logprobs left past a stop string under speculative decoding (#47616)', ref='c:51144dd83e', has='del output.new_token_ids[num_kept:]'),
            ], cap='_trim_surplus_tokens'),
        ])


def _update_src():
    return node(
        'outproc.update_src', 'update 与判 stop', 'update()：接上新字、找 stop 串、截断',
        '图上「去分词 + 判 stop 串」框的源码：新 token 怎么接到 output_text，stop 串怎么只在新字附近找，按字截断，多余 token 怎么删。',
        lead='你在「输出处理」这张图的第 4 个框（去分词 + 判 stop 串）里。两段：`update()` 本身，和它调用的 `check_stop_strings()`。',
        src=DET + ':142-201',
        blocks=[
            code(DET, [(142, 201)], notes=[
                at((149, 151), None, '没有新 token 就什么都不做（纯 prefill 的步常见）。', has='if not new_token_ids:'),
                at((153, 159), None, '引擎判了 STOP（如 EOS）且不要求输出 stop 内容：最后一个 token 先摘出来不解码。本例顶层第 7 个 `<eos>` 就走这里。', has='skipped_stop_token_id = new_token_ids[-1]'),
                at(165, None, '只有设了 stop 串才记 `token_offsets`：每个新 token 结束时文字有多长。', has='token_offsets = [len(self.output_text)] if self.stop else None'),
                at((166, 170), None, '逐个 token 解码、接到 `output_text` 后面。本例「。」到时，文字从 8 个字变成 9 个。', has='self.output_text += self.decode_next(new_token_id)'),
                at((172, 176), '推断', '摘出来的 stop token 原样补回 `token_ids`：token 序列要完整，文字里不要它。', has='self.token_ids.append(skipped_stop_token_id)'),
                at((180, 182), '注释', '`min_tokens` 门：只在第 min_tokens 个 token 之后产生的文字里认 stop 串。',
                   q='Support min_tokens, see https://github.com/vllm-project/vllm/pull/22014', has='stop_check_offset'),
                at((183, 188), None, '`new_char_count` = 这一步新增的字数，本例「。」那一步是 1。', has='check_stop_strings('),
                at((191, 197), '注释', '算出凑齐 stop 串的是第几个新 token，之后的 token 删掉（一步回来多个 token 时才会有）。`lo=1` 是因为 `token_offsets[0]` 是这一步开始前的长度（推断）。',
                   q='Drop tokens generated after the one completing the stop string', has='bisect_left(token_offsets, stop_end, lo=1)'),
                at((198, 199), None, '按**字符**截：本例截到第 6 位，`output_text` 变回「法国的首都是」。', has='self.output_text = self.output_text[:truncate_to]'),
            ], cap='BaseIncrementalDetokenizer.update'),
            code(DET, [(392, 423)], notes=[
                at((392, 393), None, '没有新字或没有 stop 串，直接返回。', has='if not new_char_count or not stop:'),
                at(401, '注释', '负数起点：从「新字数 + stop 长度 − 1」个字之前开始找，只匹配结束位置落在新字里的、也能匹配横跨新旧的。本例从倒数第 3 个字找起，在第 6 位找到「巴黎。」。',
                   q='Avoid searching already-searched text.', ref=A(DET, 400), has='output_text.find(stop_str, 1 - new_char_count - stop_string_len)'),
                at((405, 410), '注释', '几个 stop 串同时命中时，选**最先结束**的那个，结果和一个 token 一个 token 地加一样。',
                   q='the stop string that completes earliest in the text is selected,', ref=A(DET, 388), has='if end < best_end:'),
                at((415, 420), None, '要求输出 stop 串：截到它的终点；刚好在末尾就返回 -1（不截）。', has='if include_in_output:'),
                at(423, None, '默认：截到 stop 串的**起点**，stop 串本身不进输出。', has='return best_stop_str, best_stop_index'),
            ], cap='check_stop_strings'),
        ])


def _decode_src():
    return node(
        'outproc.decode_src', '一个 token 怎么解', 'decode_next：为什么不能每个 token 单独解码',
        '图上「去分词」框里最底下那一步：快路径（HF DecodeStream）和慢路径（Python 滑动窗口）怎么把一个 token 变成字，以及半个字符时为什么先不吐。',
        lead='你在「输出处理」这张图的第 4 个框里，再往下一层：`decode_next` 一次只解一个 token。四段：选哪条路 → 快路径的种子 → 快路径的防御 → 慢路径的窗口。',
        src=DET + ':96-307',
        blocks=[
            code(DET, [(96, 112)], notes=[
                at((103, 105), None, '请求 `detokenize=False` 时没有 tokenizer：只记 token id，文字永远是空串。', has='if tokenizer is None:'),
                at((107, 109), None, '快 tokenizer：用 HF `tokenizers` 库的 `DecodeStream`，每个 token 调一次 `step`。它的内部算法在外部 Rust 库里，本仓库没有。', has='FastIncrementalDetokenizer(tokenizer, request)'),
                at(112, None, '否则退回 Python 的慢路径 `detokenize_incrementally`。', has='SlowIncrementalDetokenizer(tokenizer, request)'),
            ], cap='选路：IncrementalDetokenizer.from_new_request'),
            code(DET, [(36, 47)], notes=[
                at((41, 46), '注释', '快路径开始前，用 prompt 末尾几个 token 先喂一下解码流，让第一个新字的前后文正确；只喂尾巴，不喂整个 prompt（本例 20 个 token，只取尾巴）。',
                   q='The seed only shapes the first delta, so a short tail avoids an O(prompt)', has='Return a prompt tail that primes a `DecodeStream` like the full prompt.'),
            ], cap='快路径的「种子」：get_decode_stream_seed'),
            code(DET, [(283, 307)], notes=[
                at(285, None, '每个 token 一次 `step`：返回新字，或 `None`（还不完整，比如半个字符）。', has='self.stream.step(self.tokenizer, next_token_id)'),
                at((286, 290), None, '非法 token id 导致的溢出：记日志，当成空串，不让异常冒出去。', has='except (OverflowError, TypeError):'),
                at((294, 306), '注释', '个别分词器会让 `DecodeStream` 内部状态坏掉：重置流再解。这很要紧——处理任务一旦抛异常，所有请求的信箱都会收到这个异常。',
                   q='Recover from edge case where tokenizer can produce non-monotonic,', has='tokenizers.decoders.DecodeStream('),
            ], cap='快路径：_protected_step'),
            code(DU, [(233, 277)], notes=[
                at((234, 239), '提交', '只拼窗口里的 token，不拼全部历史：每步开销跟窗口走，不跟已生成长度走。',
                   q='[Perf] Reduce fallback decode CPU by 69% at 32K (#60497)', ref='c:8d10c38b73', has='window_start = ('),
                at((245, 254), '注释', '带着前文一起解（`prefix_text` 到末尾），再减掉前文：空格加不加取决于前后的 token。',
                   q='The prefix text is necessary only to defeat cleanup algorithms in', has='prefix_text = tokenizer.convert_tokens_to_string('),
                at((269, 274), '注释', '解出来以 `�` 结尾：可能是一个字符只来了前几个字节，先返回空串，偏移不动，等下一个 token 一起解。',
                   q="utf-8 char at the end means it's a potential unfinished byte sequence", has='new_text.endswith("�")'),
                at((276, 277), None, '吐字成功：只返回前文之后的新字，窗口整体往前挪。', has='return new_tokens, new_text, read_offset, output_token_count'),
            ], cap='慢路径：detokenize_incrementally 的核心'),
        ])


def _emit_src():
    return node(
        'outproc.emit_src', '扣字与打包', '扣字放行与 stream_interval 闸',
        '图上「扣字放行 + 打包」框的源码：扣几个字怎么算，每包放出哪一截，结束时为什么全放，stream_interval 怎么挡包。',
        lead='你在「输出处理」这张图的第 5 个框（扣字放行 + 打包）里。三段：扣几个字（请求进来时就算好）→ 每包放出哪一截 → 打包前的闸。',
        src=DET + ':131-222',
        blocks=[
            code(DET, [(131, 137), (207, 222)], notes=[
                at((133, 134), '注释', '扣住数 = 最长 stop 串长度 − 1，只在 stop 串不进输出时扣。本例「巴黎。」→ 2。为什么是「−1」仓库没写（推断：完整出现前，末尾最多有 L−1 个字是它的开头）。',
                   q='Number of chars to hold back when stop strings are to be excluded', ref=A(DET, 131), has='max(len(s) for s in self.stop) - 1'),
                at(137, None, '「已发到」的位置：每请求一个，从 0 开始。', has='self._last_output_text_offset: int = 0'),
                at(211, '注释', '结束了就不扣：剩下的字全放。本例最后「法国的首都是」已经全发过，所以最后一包是空串。',
                   q='We return the full output text if the sequence is finished.', ref=A(DET, 210), has='buffer_length = 0 if finished else self.stop_buffer_length'),
                at((212, 215), None, '非 DELTA（每包都是全文）：直接返回去掉末尾扣住数的全文。', has='return self.output_text[:-buffer_length]'),
                at((217, 222), None, 'DELTA：只返回「已发到」到「可发到」之间的一截，再把「已发到」推过去。本例依次放出 "" / 法 / 国的 / 首 / 都是。截断后「可发到」可能反而变小，这时返回空串。', has='self._last_output_text_offset = length'),
            ], cap='扣字：stop_buffer_length 与 get_next_output_text'),
            code(OUTP, [(366, 403)], notes=[
                at((368, 372), None, '非流式（FINAL_ONLY）：没结束就不出包。去分词每步照样在跑。', has='if not finished and final_only:'),
                at((382, 395), '注释', '`stream_interval > 1` 才有这道闸：结束、第一个 token、攒够 N 个 token，三者之一才出包；否则返回 `None`，不放信箱。',
                   q='Send output request only when', has='if self.stream_interval > 1:'),
                at((397, 403), '观点', 'DELTA 时把没发的 token 一次带上，并推进 `sent_tokens_offset`。这个偏移只在 DELTA 分支里更新，所以 CUMULATIVE 下 `stream_interval` 疑似不起作用（没运行验证）。',
                   more='CUMULATIVE 时偏移一直是 0，闸的「第一个 token」条件永远成立，每步都放行。OpenAI 服务器流式用 DELTA，不受影响。',
                   has='self.sent_tokens_offset = self.detokenizer.num_output_tokens()'),
            ], cap='make_request_output 的开头：两道闸'),
            code(OUTP, [(486, 490), (300, 305)], notes=[
                at(490, None, '文字就是从这里取的：调用上面那个扣字函数。token_ids 不经过扣字，所以文字和 token 在 DELTA 包里会错位。', has='self.detokenizer.get_next_output_text(finished, delta)'),
                at((303, 305), '文档', '请求自带的 stream_interval 只能把间隔抬高，不能压到引擎设置之下；第一个和最后一个输出总是立刻发。',
                   q='The first and final outputs are always emitted immediately.', ref=A(SP, 395), has='max(sampling_params.stream_interval, stream_interval)'),
            ], cap='取文字的地方；请求级 stream_interval'),
        ])


def _mailbox_src():
    return node(
        'outproc.mailbox_src', '单槽信箱', 'RequestOutputCollector 与 RequestOutput.add',
        '图上「单槽信箱」框和 generate() 的源码：一个槽怎么放、怎么合并、怎么取；DELTA 拼接和非 DELTA 替换的分界线。',
        lead='你在「输出处理」这张图的第 6 个框（单槽信箱）里。三段：信箱本身 → 合并规则 → 对岸的 `generate()` 怎么取。',
        src=OUTP + ':54-104',
        blocks=[
            code(OUTP, [(54, 104)], notes=[
                at((58, 59), '注释', '一句话说明：流式时生产者跑在前面，就把包合并。',
                   q='When streaming deltas, RequestOutputs are merged if the', has='When streaming deltas'),
                at(63, None, '合并方式在建信箱时一次定死：只有 DELTA 才拼接。本例 `stream=true` → 拼接。', has='self.aggregate = output_kind == RequestOutputKind.DELTA'),
                at(65, None, '就一个槽：`None`、一个包、或一个异常。', has='self.output: RequestOutput | PoolingRequestOutput | Exception | None = None'),
                at((72, 74), None, '槽空就放进去并叫醒消费者；来的是**异常**时无条件覆盖，哪怕槽里还有没读的数据。', has='if self.output is None or isinstance(output, Exception):'),
                at((75, 80), '注释', '槽里已有一个包：合并进去。本例「国的」+「首」在这里变成「国的首」。n>1 时不同 index 各自保留。',
                   q='This ensures that request outputs with different request indexes', has='self.output.add(output, aggregate=self.aggregate)'),
                at((81, 84), None, 'pooling 模型的输出：直接替换。', has='isinstance(self.output, PoolingRequestOutput)'),
                at((88, 91), '推断', '槽空就等事件；每次醒来都重查槽。取走后清槽、清事件。没有锁：这里没有 await 打断读写，又都在同一个线程上。', has='await self.ready.wait()'),
                at((96, 104), None, '不等版本：有就拿走，没有返回 `None`。', has='def get_nowait(self)'),
            ], cap='RequestOutputCollector'),
            code(OUTS, [(211, 255)], notes=[
                at(213, None, '只要有一个包说结束，合并后的包就是结束的。', has='self.finished |= next_output.finished'),
                at((220, 225), None, 'DELTA：文字拼接、token_ids 拼接。本例 `"国的" + "首"`、`[首都] + [是]`。', has='completion.text += next_completion.text'),
                at((239, 241), '推断', '`cumulative_logprob` 取最新的而不是相加：它本身就是累计值。', has='completion.cumulative_logprob = ('),
                at((248, 249), None, '结束原因取最新的：最后一包的 `stop` / `"巴黎。"` 会留下。', has='completion.finish_reason = next_completion.finish_reason'),
                at((250, 252), None, '非 DELTA：新包本来就是「到目前为止的全部」，整个替换。', has='self.outputs[i] = next_completion'),
                at((254, 255), None, 'index 不同（n>1 的另一个候选）：追加，不互相覆盖。', has='self.outputs.append(next_completion)'),
            ], cap='RequestOutput.add：DELTA 拼接 vs 非 DELTA 替换'),
            code(ALLM, [(805, 816)], notes=[
                at(809, '注释', '先不等地取，空了才等：高负载下少一次任务切换。', q='drain queue without await if possible', ref=A(ALLM, 807), has='q.get_nowait() or await q.get()'),
                at(814, None, '取到的包（可能是合并过的）带 `finished` 就退出循环。', has='finished = out.finished'),
            ], cap='对岸：generate() 守着信箱'),
        ])


def nodes():
    return {'outproc': node(
        'outproc', '输出处理', '输出处理：token id 怎么变成客户端看到的字',
        '顶层「输出处理」框里面：一包结果怎么被分块、找到请求、增量去分词、判 stop 串、扣字、进单槽信箱，以及 stop 命中后怎么反向通知引擎。',
        lead='你在顶层「输出处理」（`OutputProcessor`）框里面。它是 API Server 进程里的一条流水线，全在同一个事件循环线程上。按 ▶ 推演：还是「法国的首都是巴黎。」，但这次设 `stop=["巴黎。"]`，看文字为什么晚 2 个字、信箱怎么合并、stop 命中后怎么收尾。',
        src=OUTP + ':731-885',
        blocks=[_scene()],
        children=[_handler_src(), _process_src(), _update_src(), _decode_src(), _emit_src(), _mailbox_src()])}
