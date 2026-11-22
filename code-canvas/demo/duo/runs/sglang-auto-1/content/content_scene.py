# 顶层：一条聊天请求在 SGLang 里怎么走完、响应怎么回到客户端。
# 事实全部来自 research/manuscript.md（HEAD = c569c43446）。
from cv import *

META = dict(title='一条请求走完 SGLang')

HS = 'python/sglang/srt/entrypoints/http_server.py'
SB = 'python/sglang/srt/entrypoints/openai/serving_base.py'
SC = 'python/sglang/srt/entrypoints/openai/serving_chat.py'
TM = 'python/sglang/srt/managers/tokenizer_manager.py'
SCH = 'python/sglang/srt/managers/scheduler.py'
SBT = 'python/sglang/srt/managers/schedule_batch.py'
BRP = 'python/sglang/srt/managers/scheduler_components/batch_result_processor.py'
OS_ = 'python/sglang/srt/managers/scheduler_components/output_streamer.py'
RR = 'python/sglang/srt/managers/scheduler_components/request_receiver.py'
DM = 'python/sglang/srt/managers/detokenizer_manager.py'
INC = 'python/sglang/srt/managers/incremental_detokenize.py'
TP = 'python/sglang/srt/managers/tp_worker.py'
MEM = 'python/sglang/srt/mem_cache/common.py'
MREADME = 'python/sglang/srt/mem_cache/README.md'
OVL = 'python/sglang/srt/managers/overlap_utils.py'
UMD = 'docs/docs/advanced_features/unified_memory_design.mdx'
SERV = 'python/sglang/srt/arg_groups/fields/serving.py'
SCHED_F = 'python/sglang/srt/arg_groups/fields/schedule.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数：prompt 20 token、命中 5、rid 9f3c…）
def loop_g(on):
    return g_list(['收：抽干管道 ①', '选批：prefill 优先', '发射：交给 GPU', '处理上一批的结果'], on=on)


def root(base):
    base['lead'] = ('一条 `stream=true` 的聊天请求要穿过 **3 个进程**、走 **3 条单向管道**（①②③），再回到同一个 HTTP 连接。'
                    '按 ▶ 推演，跟着「法国首都是哪里？」走一遍；每个框、每条线都能点开看「为什么这么设计」，带 ↘ 的还能钻到下一层。')

    parts = [
        # ---------------- 客户端（主进程外）
        part('client', '客户端', [16, 30, 170, 124], m=[8, 8, 232, 118], kind='ext', n='1',
             glyph=g_list(['POST /v1/chat/', 'completions', 'stream=true']),
             say='发请求，收 SSE 流', why='错误能拿到正经 400', trap='200 头不是马上回',
             card=card(
                 '用 OpenAI 的协议发一条聊天请求，然后从同一个 HTTP 连接上一块一块收回 SSE（`data: {...}`），最后收到 `data: [DONE]`。',
                 ['路由函数几乎什么都不做，原样交给聊天协议层的 `handle_request`。' + R(HS, 1901, 1910),
                  '流式时服务器先把生成器「踢一脚」，拿到第一块才回 HTTP 200。' + R(SC, 2381, 2385)],
                 [note('注释', '先跑到第一块再发 200：像「超长上下文」这类校验错误还能变成正经的 HTTP 400，而不是塞进 SSE 正文。',
                       q='Kick-start the generator to trigger validation before HTTP 200 is sent.', ref=A(SC, 2381)),
                  note('提交', '这是专门修过的行为。', q='Return HTTP 400 for streaming validation errors (#21900)', ref='c:269589ad71')],
                 ['客户端断开时，服务器靠一个后台任务收尾：响应结束后睡 2 秒，rid 还在表里就发 abort 给 Scheduler。' + R(TM, 2878, 2889),
                  '为什么是 2 秒，没找到说明（提交说明只有标题）。'])),

        # ---------------- 主进程
        part('chat', '聊天协议层', [310, 30, 190, 124], m=[8, 216, 232, 124], kind='act', n='2', code='serving_chat',
             glyph=g_kv([('消息', '法国首都是哪里？'), ('prompt', '? token')]),
             say='套模板、分词；切增量写 SSE', why='分词放线程池，不卡流', trap='分词在这里，不在 TM',
             card=card(
                 ['进门：把 OpenAI 的 `messages` 套上聊天模板、分好词，装成内部请求 `GenerateReqInput`。',
                  '出门：把 TokenizerManager 给的「到目前为止的全文」切出这一步的增量，包成 `chat.completion.chunk` 写成 SSE。'],
                 ['模板渲染 + 分词丢到线程池里做（`run_in_executor`）。' + R(SB, 99, 104),
                  '纯文本模型在这里就得到 `input_ids`，下游不再分词。' + R(SC, 1184),
                  '出门时用每个 choice 的偏移切增量：`delta = text[offset:]`。' + R(SC, 848, 853)],
                 [note('注释', '主进程只有一个事件循环，同时在给所有流式连接吐数据；长 prompt 的渲染 + 分词放在循环里做，所有人的流都会卡一下。',
                       q='Convert off-loop: jinja render + full-prompt tokenization blocks the loop on long prompts, and the rust tokenizer releases the GIL.',
                       ref=A(SB, 99, 100))],
                 ['「全文 → 再切成增量」看起来多余：TokenizerManager 默认对外给累计全文（原生 `/generate` 的契约），聊天层只好再切回来。'
                  '「为了兼容原生接口」这个动机是推断，没找到提交说明直接佐证。']),
             to='src_chat'),
        part('tm', '请求登记处', [624, 30, 210, 140], m=[8, 430, 232, 130], kind='store', n='3', code='TokenizerManager',
             glyph=g_kv([('rid', '—'), ('out_list', '—'), ('状态', '—')]),
             say='登记 rid，发出去，再等着', why='一个信箱，反复放入、唤醒', trap='名字叫 Tokenizer 却不分词',
             card=card(
                 '主进程里的「请求登记处」：给请求起 rid、在 `rid_to_state` 里建一个信箱 `ReqState`，把请求打包经管道 ① 发给 Scheduler，然后在信箱上等结果。',
                 ['`generate_request` 依次：起 rid（没传就是 uuid4 hex）→ 建信箱 → 打包 `TokenizedGenerateReqInput` → 同步 ZMQ send → 丢掉 `input_ids` → 等。' + R(TM, 1061, 1125),
                  '信箱 `ReqState` = `out_list`（放结果）+ `event`（叫醒）+ `finished`。' + R(TM, 279, 321),
                  '等的时候最多 4 秒醒一次，只为查客户端断没断。' + R(TM, 2346, 2351)],
                 [note('推断', '一个请求会收到很多次结果（每个 token 一次），而且结果是成批从同一根管道进来的；需要一个能反复「放入-唤醒-取走」的信箱，Event + list 正好。'),
                  note('注释', '发出后立刻丢掉 prompt：它已经交给 Scheduler，主进程不再需要这份可能很大的列表。',
                       q='obj.input_ids was dropped after dispatch, so the request cannot be dumped.', ref=A(TM, 296))],
                 ['名不副实：chat 主路上它**通常不分词**，拿到的已经是 `input_ids`。' + R(TM, 1291, 1292),
                  '结果的**接收**不在这里，而在另一个常驻协程 `handle_loop`（右边那个框）；两者只靠信箱交接。']),
             to='src_tm'),
        part('hloop', '收件协程', [958, 30, 250, 124], m=[8, 630, 180, 118], kind='bg', n='8', code='handle_loop',
             glyph=g_text('等管道 ③', '常驻，收所有请求的结果'),
             say='按 rid 把结果投进信箱', why='攒 16 个再一起叫醒', trap='不是发请求的那个协程',
             card=card(
                 '主进程里一个常驻协程：从管道 ③ 收 `BatchStrOutput`（一批 rid 各自的增量文字），按 rid 找到信箱，放进去并叫醒等待方。',
                 ['第一次有请求时才起它（`auto_create_handle_loop`）。' + R(TM, 2891, 2900),
                  '对每个 rid：`append_text(增量)`；流式中间块的 `text` 先给 `None`，等待方醒来时再拼全文。' + R(TM, 3081, 3115),
                  '请求结束就 `del rid_to_state[rid]`。' + R(TM, 3228),
                  '唤醒是攒着的：每攒满 `batch_notify_size`（默认 16）个就一起 `set` 并让出一下循环。' + R(TM, 3232, 3240)],
                 [note('文档', '分批唤醒，减少高并发下 asyncio 的唤醒开销。',
                       q='Number of streaming notifications to batch before yielding to the event loop. Reduces asyncio wakeup overhead under high concurrency.',
                       ref=A(SERV, 263)),
                  note('注释', '中间块只传引用、不拼全文：否则每步 O(n)，累计 O(n²)。',
                       q='Non-incremental intermediate: pass reference (no copy) and defer text to _wait_one_response to avoid O(n) per-step cost that compounds to O(n^2).',
                       ref=A(TM, 3108, 3110))],
                 ['「发出」和「收到」是两个协程：`generate_request` 发完就在信箱上睡，真正从管道收东西的是这里。',
                  'rid 查不到（比如已被 abort）只打警告跳过。' + R(TM, 2946, 2955)]),
             to='src_hloop'),

        # ---------------- Scheduler 子进程
        part('sched', '调度循环', [624, 216, 210, 130], m=[8, 1012, 232, 136], kind='act', n='4', code='Scheduler',
             glyph=loop_g(None),
             say='每圈：收、选批、发射、处理', why='发射和处理差一圈', trap='请求被反复调度，不是跑一次',
             card=card(
                 ['Scheduler 子进程（每张 GPU 一个）的主线程：一个死循环，排队、组批、管显存、调模型。',
                  '一条请求在这里**被反复调度**：第一次 prefill 吐出第 1 个 token，之后每圈 decode 吐 1 个，每圈都和别的请求拼在一起跑。'],
                 ['默认走 overlap 版循环，每圈：收新请求 → 选这一圈跑什么 → 发射（不等算完）→ **处理上一圈**的结果。' + R(SCH, 2235, 2305),
                  '收请求是非阻塞地把管道抽干。' + R(RR, 152, 160),
                  '选批：有新请求能拼成 prefill 批就先跑 prefill，否则跑 decode。' + R(SCH, 4443, 4456)],
                 [note('文档', 'overlap：CPU 处理第 N 批结果时，第 N+1 批已经在 GPU 上跑；CPU 的活被藏到 GPU 计算的阴影里。',
                       q='Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker.', ref=A(SCHED_F, 215)),
                  note('推断', '单独成进程、收请求不阻塞：循环每圈都要走到「发射」，被 I/O 或 GIL 卡住，正在 decode 的请求就停摆。'),
                  note('推断', 'prefill 优先：新请求的首 token 延迟最敏感；decode 晚一步只让大家的下一个 token 慢几毫秒。')],
                 ['结果处理总比发射**晚一拍**：一个请求在第 N 批采到 EOS，第 N+1 批已经带着它发射了，这一步白算，下一圈再剔掉。' + R(SCH, 4413, 4416),
                  '校验失败的请求也照样入队、走同一条输出通道回去，保证「收下的请求恰好产出一次输出」。' + R(SCH, 3922, 3923)]),
             to='sched_loop'),
        part('gpu', '模型前向 + 采样', [310, 216, 190, 130], m=[8, 1232, 180, 136], kind='act', n='6', code='TpModelWorker',
             glyph=g_text('—', '还没发射'),
             say='算一批，每个请求采 1 个 token', why='token 留在 GPU 上直接转交', trap='CPU 晚一圈才看到 token',
             card=card(
                 '把 Scheduler 选好的一批请求在 GPU 上算一遍，给每个请求采样出下一个 token。',
                 ['CPU 侧的 `ScheduleBatch` 变成 GPU 张量批 `ForwardBatch` → 模型前向得到 logits → `sample` 得到 `next_token_ids`。' + R(TP, 657) + ' ' + R(TP, 771, 773),
                  'decode 能用 CUDA graph 就回放录好的图；prefill 通常走另一条路。' + R('python/sglang/srt/model_executor/model_runner.py', 2141, 2146),
                  '下一步要用的 token 和 seq_lens 暂存进 `future_map`，结果再异步拷回 CPU。' + R(SCH, 5475) + ' ' + R(SCH, 5506)],
                 [note('注释', 'overlap 下发射下一步时，上一步的 token CPU 还没看到；下一步 decode 的 input_ids 直接在 GPU 上转交。',
                       q='Next-iter input_ids relayed via future_map.', ref=A(SCH, 5510)),
                  note('注释', 'ScheduleBatch 归 Scheduler 管、多在 CPU；ForwardBatch 归 ModelRunner 管、多是 GPU 张量。',
                       q='ScheduleBatch is managed by `scheduler.py::Scheduler`. It contains high-level scheduling data. Most of the data is on the CPU.',
                       ref=A(SBT, 41, 46))],
                 ['读旧博客会看到中间还有一层 `ModelWorkerBatch`，现在已经去掉了。',
                  '采样出来的 token 是 GPU 上的张量；CPU 要等 `copy_done` 才能读到。']),
             to='model_fwd'),
        part('kv', 'KV 缓存 + 前缀树', [624, 398, 210, 110], m=[8, 1426, 232, 118], kind='store', n='5', code='tree_cache',
             glyph=g_text('5 token', '树里已有：聊天模板开头'),
             say='命中的前缀不用重算', why='缓存是结束时顺手插的', trap='最多命中 输入长度−1',
             card=card(
                 '管 KV 存在哪、谁能复用：前缀树记着「token 前缀 → 已算好的 KV 槽」，新请求开头一样的那段直接复用。',
                 ['三层：`req_to_token_pool`（请求第 j 个 token 的 KV 在哪个槽）、`token_to_kv_pool_allocator`（空闲槽表）、`tree_cache`（前缀树，决定留谁、淘汰谁）。' + R(MREADME, 31, 33),
                  '入批前前缀匹配；prefill 只给没命中的部分分槽；decode 每步每个请求再要 1 个槽。' + R(SBT, 1205) + ' ' + R(SBT, 3346),
                  '请求结束时 `release_kv_cache`：先把它的 token 序列插进前缀树，再释放树不管的槽。' + R(MEM, 385, 422)],
                 [note('推断', '缓存不是单独写的：请求结束时顺手插进树，下一条同开头的请求自然命中，不需要另一套写入路径。'),
                  note('注释', '最多只匹配「输入长度 − 1」个：至少留 1 个 token 真正过模型。',
                       q='the matched length is at most 1 less than the input length to enable logprob computation', ref=A(SBT, 1239))],
                 ['再来一条一模一样的「法国首都是哪里？」：能命中 19 个（上限 20−1），只算 1 个 token 的 prefill。',
                  '显存不够时 decode 会把一部分请求**退回**等待队列（retract），不是直接报错。' + R(SCH, 5216, 5266)]),
             to='kv_cache'),

        # ---------------- Detokenizer 子进程
        part('detok', '反分词', [958, 216, 250, 130], m=[8, 818, 180, 124], kind='act', n='7', code='Detokenizer',
             glyph=g_kv([('surr / read', '— / —'), ('发出', '—')]),
             say='token id → 这一步新增的文字', why='带一小段上文一起解', trap='半个字先憋着不发',
             card=card(
                 '独立子进程，只做一件事：把一批请求各自的新 token id 变回**这一步新增的文字**。',
                 ['阻塞收 → 按类型分发 → 有输出就推给主进程，一个朴素的同步循环。' + R(DM, 164, 171),
                  '每个 rid 一份状态：上文从哪开始（surr）、读到哪（read）。解「上文 + 新 token」再减去「只解上文」，差值就是新文字。' + R(INC, 216),
                  '第一次的上文 = prompt 最后 5 个 token，由 Scheduler 只在首个输出时随 header 带过来。' + R(INC, 22)],
                 [note('推断', '很多分词器里一个 token 的文字取决于它前面的 token（空格、字节级 BPE 拼出的多字节字符），所以不能把新 token 单独解。'),
                  note('注释', '解出来以 � 结尾（UTF-8 字节没凑齐）：只发可打印的前缀，不推进偏移，下一步带着更多 token 重试。',
                       q='Incomplete UTF-8: emit the printable prefix only; do not commit (token offsets stay so the next iteration retries with more tokens).',
                       ref=A(INC, 231, 233)),
                  note('推断', '单独成进程：反分词是纯 CPU 活，量随「并发 × 每秒 token」涨，放主进程抢事件循环，放 Scheduler 挤调度。')],
                 ['状态表有上限（默认 65536 个），满了淘汰最老的，被淘汰请求之后的文字会丢。' + R(INC, 273, 277),
                  'stop 串 / EOS 的裁剪只在结束那一步做。' + R(INC, 238, 246)]),
             to='src_detok'),
    ]

    wires = [
        wire('w_req', 'client', 'chat', 'POST 请求', mlp=[70, 151], why='全在主进程一个循环里',
             card=card('一条 HTTP POST 进入主进程的 FastAPI 路由。',
                       '路由函数只是把请求交给聊天协议层。' + R(HS, 1901, 1910),
                       [note('注释', 'HTTP 服务、Engine、TokenizerManager 都在主进程里，共用一个事件循环。',
                             q='The HTTP server, Engine, and TokenizerManager all run in the main process.', ref=A(HS, 3310))])),
        wire('w_gen', 'chat', 'tm', 'input_ids', mlp=[70, 365], why='分好词再交出去', trap='带的已是 token',
             card=card('把装好的 `GenerateReqInput`（已含 `input_ids`、采样参数、`stream`）交给 `generate_request`。',
                       '同一进程里的函数调用：`generate_request` 是个异步生成器，聊天层 `async for` 它。' + R(SC, 2486) + ' ' + R(SC, 2502),
                       [note('推断', '分词已经在线程池里做完，TokenizerManager 拿到 `input_ids` 直接打包，主事件循环上不做重活。')],
                       '这里还在主进程内，不是跨进程。')),
        wire('w_p1', 'tm', 'sched', '① 请求', mfs='r', mts='r', mlp=[262, 790], why='单向推，回程靠 rid', trap='没有请求-应答',
             card=card('第一条管道：主进程 PUSH → Scheduler PULL，送的是 `TokenizedGenerateReqInput`。',
                       ['发：' + R(TM, 834, 836) + '；收：' + R('python/sglang/srt/managers/scheduler_components/ipc_channels.py', 36, 38),
                        '默认用 pickle 序列化；`input_ids` 被转成紧凑的 `array("q", ...)`。' + R(TM, 1824),
                        '中止也走这条：`AbortReq`。' + R(TM, 2638, 2653)],
                       [note('推断', '三条管道都是单向的，没有「请求-应答」：结果回来时靠 rid 对号入座，不靠连接。'),
                        note('注释', '进程之间全靠 ZMQ，每个进程一个端口。',
                             q='Inter-process communication is done through IPC (each process uses a different port) via the ZMQ library.', ref=A(HS, 3311))],
                       '发送是同步 send，不等 Scheduler 回应。')),
        wire('w_fwd', 'sched', 'gpu', '发射本批', mlp=[70, 1173], why='发完不等，接着干',
             card=card('把这一圈选出的批交给 GPU 算。',
                       ['`run_batch` 在单独的 forward 流上调 `forward_batch_generation`。' + R(SCH, 5433) + ' ' + R(SCH, 5470),
                        '结果对象（`batch.copy()`）进 `result_queue`，下一圈才处理。' + R(SCH, 2284)],
                       [note('注释', 'CPU 的收尾活在下一批 GPU 前向进行中时做，等于白捡的重叠。',
                             q="in overlap mode this CPU work runs while the next batch's GPU forward is in flight, giving free overlap.", ref=A(SCH, 5881, 5882))],
                       '入队的是 `batch.copy()`：原 batch 下一圈会被就地修改。')),
        wire('w_tok', 'gpu', 'sched', 'token 晚一拍', mlp=[180, 1213], cls='dash', why='stop 串先于 EOS 判',
             card=card('采样出的 `next_token_ids` 异步拷回 CPU，Scheduler **下一圈**才处理。',
                       ['处理前先 `copy_done.synchronize()` 等拷贝完成。' + R(BRP, 1156, 1157),
                        '逐请求 `output_ids` 追加 token，再判断结束（stop 串 → EOS/stop token → `max_new_tokens`…）。' + R(BRP, 1243, 1246)],
                       [note('注释', 'stop 串先于 EOS 判：否则按 token 只裁最后一个，会漏出 stop 串。',
                             q='Stop string beats EOS/stop-token matched in the same step', ref=A(SBT, 1503, 1505))],
                       '按长度结束的能提前知道、不白算；EOS 没法预知，会多算一步。' + R(SCH, 5182, 5192))),
        wire('w_kv', 'sched', 'kv', '前缀匹配 / 分槽', ml='匹配 / 分槽', mfs='r', mts='r', mlp=[262, 1290], why='一处管所有 KV 内存', trap='结束时才写进树',
             card=card('Scheduler 拼 prefill 批前问前缀树「这段开头算过没有」，并给没命中的部分分 KV 槽；请求结束时把它插回树里。',
                       ['匹配：`init_next_round_input` → `match_kv_cache`。' + R(SCH, 4845) + ' ' + R(SBT, 1205),
                        '预算：`PrefillAdder.add_one_req` 判断显存 / token 预算够不够。' + R(SCH, 4867),
                        '释放：`release_kv_cache`。' + R(MEM, 385)],
                       [note('文档', '这一块（mem_cache）管所有 KV 内存：谁发槽、谁存字节、哪棵前缀树决定留什么。',
                             q='Everything that owns KV / SSM-state memory: who hands out slots, who holds the bytes on the device, who mirrors them to host and disk, and which radix cache decides what to keep.',
                             ref=A(MREADME, 1, 5))])),
        wire('w_p2', 'sched', 'detok', '② 新 token id', ml='② token id', why='上文只在首次发一次', trap='只发新 token',
             card=card('第二条管道：Scheduler PUSH → Detokenizer PULL，送 `BatchTokenIDOutput`（一批 rid 各自的新 token）。',
                       ['`stream_output` 逐请求决定这一步发不发：结束必发；`stream_interval` 默认 1，每步都发。' + R(OS_, 580, 614),
                        '只发 `output_ids[send_token_offset:]` 这段新 token。' + R(OS_, 626, 630),
                        '首次输出才附 `DetokenizeHeader`（prompt 尾部 token + 反分词选项）。' + R(OS_, 642, 646)],
                       [note('提交', '以前每步都发「反分词用的 id 列表和偏移」，后来改成只发一次、状态留在 Detokenizer。',
                             q='[Perf] detokenizer: Send per-request decode inputs once and keep them in the detokenizer (#47260)', ref='c:8080507c3e')],
                       '尾部可能是 stop 串的前缀时会先憋着不发。' + R(OS_, 608, 610))),
        wire('w_p3', 'detok', 'hloop', '③ 增量文字', lp=[1140, 184], why='一批 rid 一起回', trap='是增量，不是全文',
             card=card('第三条管道：Detokenizer PUSH → 主进程 PULL，送 `BatchStrOutput(output_strs=[...])`。',
                       ['发：' + R(DM, 109, 111) + '；收：' + R(TM, 831, 833),
                        '`output_strs[i]` 是第 i 个 rid 这一步**新增**的文字。' + R(TM, 3081, 3084)],
                       [note('推断', '一批 rid 的结果挤在一个消息里过管道，主进程一次收、逐个分发，比每个请求一条消息省事。')],
                       '这一层发的是增量；到了主进程默认又会被拼回全文。')),
        wire('w_wake', 'hloop', 'tm', '投进信箱·叫醒', ml='投信箱·叫醒', why='取走即清空，几块并一次',
             card=card('按 rid 把 `out_dict` 放进 `ReqState.out_list`，再 `event.set()` 叫醒在信箱上等的协程。',
                       ['等待方醒来**原子地**取走全部 `out_list`、清 event。' + R(TM, 2366, 2370),
                        '只取最后一块，`text` 为 `None` 时这时才拼全文。' + R(TM, 2382) + ' ' + R(TM, 2398, 2403)],
                       [note('推断', '每个请求会被叫醒很多次；取走就清空的信箱，让「来得快、取得慢」时几块合成一次处理。')])),
        wire('w_yield', 'tm', 'chat', 'yield 全文', mlp=[180, 405], why='默认契约是累计全文', trap='全文，不是增量',
             card=card('`generate_request` 这个生成器 yield 一块：`text` 是到目前为止的**全文**，外加 `meta_info`。',
                       ['结束块 `yield` 后 `break`；中间块直接 `yield`。' + R(TM, 2439, 2440)],
                       [note('文档', '`incremental_streaming_output` 默认关：TokenizerManager 默认对外给累计全文。',
                             q='Whether to output as a sequence of disjoint segments.', ref=A(SERV, 269, 272))],
                       '开了增量模式，聊天层直接把 `text` 当增量用。' + R(SC, 849, 850))),
        wire('w_sse', 'chat', 'client', 'SSE 块', mlp=[180, 191], why='切出增量再写出',
             card=card('把增量包成 `chat.completion.chunk` 写进 HTTP 流；最后补一行 `data: [DONE]`。',
                       ['`delta = text[offset:]`，再把偏移更新成全文长度。' + R(SC, 848, 853),
                        '`[DONE]`：' + R(SC, 2825)],
                       [note('推断', '对外是 OpenAI 的增量协议，对内拿到的是全文，所以在最后一站按偏移切。')],
                       'SSE 块的具体字段这里没有逐一核实；空增量在聊天层怎么处理也没逐行核实。')),
    ]

    # ---------------------------------------------------------------- 推演：同一句「法国首都是哪里？」
    steps = [
        beat('客户端发出请求',
             '`POST /v1/chat/completions`，messages 里一句「法国首都是哪里？」，`stream=true`，`max_tokens=32`。聊天协议层在**线程池**里套模板 + 分词，得到 **20 个 token**（设的），装进 `GenerateReqInput(input_ids=[20 个], rid=None)`。',
             note('注释', '分词放线程池：长 prompt 不卡住给所有人吐流的事件循环。',
                  q='Convert off-loop: jinja render + full-prompt tokenization blocks the loop on long prompts', ref=A(SB, 99, 100)),
             focus=['chat', 'w_req', 'client'],
             state={'chat': g_kv([('消息', '法国首都是哪里？'), ('prompt', '20 token')])}),
        beat('登记 rid，经管道 ① 发出',
             '`generate_request` 起 rid=`9f3c…`（uuid4 hex），建空信箱 `ReqState(out_list=[], finished=False)`。已经有 `input_ids`，不再分词，打包成 `TokenizedGenerateReqInput` 经 ① PUSH 给 Scheduler；随即丢掉 `input_ids`，在信箱上 `await event`（最多 4 秒醒一次查断线）。',
             note('推断', '发完就睡：结果会由另一个协程按 rid 投进这个信箱。'),
             focus=['tm', 'w_gen', 'w_p1'],
             state={'tm': g_kv([('rid', '9f3c…'), ('out_list', '[]'), ('状态', '等 event')])}),
        beat('Scheduler 收下、排队',
             '第 n 圈开头，`ingest_requests` 非阻塞地把管道抽干，抽到这一条；造 `Req(origin_input_ids=20 个, output_ids=[])`，校验通过，进 `waiting_queue=[req]`。',
             note('推断', '不阻塞：这个循环还要驱动正在 decode 的别的请求。'),
             focus=['sched', 'w_p1'],
             state={'sched': loop_g(0)}),
        beat('选批：查前缀缓存',
             '有新请求 → 先拼 prefill 批。前缀树里已有聊天模板开头的 **5 个** token（设的），命中；只需算剩下 **15 个**，分 15 个 KV 槽。`prefix_len=5`，`extend_len=15`。',
             note('注释', '最多命中「输入长度 − 1」个。',
                  q='the matched length is at most 1 less than the input length to enable logprob computation', ref=A(SBT, 1239)),
             focus=['kv', 'w_kv', 'sched'],
             state={'sched': loop_g(1),
                    'kv': g_cells([cell('5', '命中', 'pick'), cell('15', '新分', 'new')], left='prompt 20')}),
        beat('发射 prefill，采到 X1',
             '`run_batch` 把这批交给 GPU：`ScheduleBatch → ForwardBatch`，算位置 5..19 这 15 个，采样出第 1 个 token **X1**。发射是异步的：X1 还在 GPU 上，CPU 没等它；这一圈没有上一批要处理。',
             note('文档', 'overlap：CPU 调度和 GPU 计算重叠——第 N+1 批在第 N 批结果处理之前就发射。',
                  q='Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker.', ref=A(SCHED_F, 215)),
             focus=['gpu', 'w_fwd', 'sched'],
             state={'sched': loop_g(2), 'gpu': g_text('X1', 'prefill 15 个位置 → 采样', tone='run')}),
        beat('第 n+1 圈：先发射，再处理上一批',
             'prefill 批并入正在 decode 的大批；再分 1 个槽，seq_len 20→21；input_ids 直接用 GPU 上的 X1（经 `future_map` 转交，CPU 还没看到）——**先发射这一步 decode**。然后才处理第 n 圈：等拷贝完成，`req.output_ids=[X1]`，没结束。',
             note('注释', '下一步的 input_ids 在 GPU 上转交，不经 CPU。', q='Next-iter input_ids relayed via future_map.', ref=A(SCH, 5510)),
             focus=['sched', 'w_tok', 'gpu', 'kv'],
             state={'sched': loop_g([2, 3]), 'gpu': g_text('X2', 'decode 第 1 步在算', tone='run'),
                    'kv': g_cells([cell('5', '命中', 'pick'), cell('15', 'prefill'), cell('+1', 'decode', 'new')], left='序列 21')}),
        beat('新 token 经管道 ② 发出',
             '`stream_output`：第一个 token 立刻发。首次输出附 header（prompt 最后 5 个 token）；打包 `BatchTokenIDOutput(rids=["9f3c…"], output_ids=[X1])` 经 ② 发给 Detokenizer。`send_token_offset` 0→1。',
             note('提交', '上文只在首次发，之后只发新 token。',
                  q='[Perf] detokenizer: Send per-request decode inputs once and keep them in the detokenizer (#47260)', ref='c:8080507c3e'),
             focus=['w_p2', 'detok', 'sched'],
             state={'sched': loop_g(3)}),
        beat('X1 → 「巴」，经管道 ③ 回主进程',
             'Detokenizer 建这个 rid 的状态：`decode_ids=[尾巴 5 个, X1]`，surr=0，read=5。解(尾巴+X1) 减去 解(尾巴) = **「巴」**；不以 � 结尾 → 推进偏移到 5 / 6。`BatchStrOutput(output_strs=["巴"])` 经 ③ 发回。',
             note('推断', '带一小段上文一起解：一个 token 的文字可能取决于它前面的 token。'),
             focus=['detok', 'w_p3', 'hloop'],
             state={'detok': g_kv([('surr / read', '0 / 5 → 5 / 6'), ('发出', '「巴」')]),
                    'hloop': g_text('「巴」', 'rid 9f3c… 的增量')}),
        beat('投信箱 → 叫醒 → 写出 SSE',
             '`handle_loop` 按 rid 找到信箱：`append_text("巴")`，`text` 先给 `None`，放进 `out_list`，`event.set()`。等待的协程醒来取走，这时才拼全文 `"巴"` 并 yield；聊天层 `delta = "巴"[0:]`，偏移记 1，写出一个 SSE 块。客户端看到 **「巴」**。',
             note('注释', '中间块不拼全文，等醒来再拼，避免累计 O(n²)。',
                  q='defer text to _wait_one_response to avoid O(n) per-step cost that compounds to O(n^2).', ref=A(TM, 3108, 3110)),
             focus=['hloop', 'w_wake', 'tm', 'w_yield', 'chat', 'w_sse', 'client'],
             state={'tm': g_kv([('rid', '9f3c…'), ('out_list', '取走 1 块'), ('全文', '巴')]),
                    'chat': g_kv([('全文', '巴'), ('已发偏移', '0 → 1')]),
                    'client': g_text('巴', '第 1 个 SSE 块')}),
        beat('半个字：先憋着，凑齐再发',
             'X2 是「黎」字 UTF-8 的前半个字节（设的）：解出来以 � 结尾 → 只发可打印前缀（空），**不推进偏移**，这一步客户端看不到新字。X3 补齐后半个字节，解出完整的 **「黎」**；X4 → **「。」**。',
             note('注释', '字节没凑齐就不提交，下一步带着更多 token 重试。',
                  q='Incomplete UTF-8: emit the printable prefix only; do not commit', ref=A(INC, 231, 233)),
             focus=['detok', 'w_p2', 'w_p3'],
             state={'detok': g_kv([('X2', '以 � 结尾 → 不发'), ('X3 / X4', '「黎」/「。」')]),
                    'gpu': g_text('X5', 'decode 第 4 步', tone='run'),
                    'client': g_text('巴黎。', '已收 3 个字')}),
        beat('X5 = EOS：结束，顺手写进前缀树',
             '第 n+5 圈处理第 n+4 圈的结果：X5 是 EOS → 判为结束。`release_kv_cache` 把这条请求（prompt + 已生成）插进前缀树，释放其余槽。但 overlap 下第 n+5 圈的 decode 已经带着它发射了——这一步白算，下次选批剔掉，多分的槽一并回收。',
             note('注释', '发射后才离开批的请求，多分的槽在释放时收回。',
                  q='Left the batch after launch; its over-allocated tokens are freed in', ref=A(BRP, 1215, 1216)),
             focus=['sched', 'w_kv', 'kv', 'w_tok'],
             state={'sched': loop_g(3), 'gpu': g_text('白算一步', '第 n+5 圈的 decode', tone='fail'),
                    'kv': g_text('+1 条', '树里新增「…法国首都…巴黎。」', tone='ok')}),
        beat('收尾：最后一块 + [DONE]',
             '结束行必发：Detokenizer 裁掉末尾 EOS，发剩下的尾巴（通常为空），释放这个 rid 的状态。主进程 `finished=True`，`del rid_to_state[rid]`，最后一块 `text="巴黎。"`；聊天层发带 `finish_reason` 的最后一块，再发 `data: [DONE]`，流关闭。2 秒后后台 abort 任务醒来，rid 已不在表里，什么都不做。',
             note('注释', '后台任务只为客户端断开时收尾。', q='Abort the request if the client is disconnected.', ref=A(TM, 2879)),
             focus=['client', 'w_sse', 'chat', 'w_yield', 'tm', 'w_wake', 'hloop', 'w_p3', 'detok'],
             state={'detok': g_kv([('结束', '裁掉 EOS'), ('状态', '已释放')]),
                    'tm': g_kv([('rid', '已删除'), ('finished', 'True'), ('全文', '巴黎。')]),
                    'client': g_text('巴黎。', 'data: [DONE]', tone='ok')}),
    ]

    groups = [
        group('主进程（一个事件循环）', [296, 6, 922, 170], m=[2, 198, 336, 558]),
        group('Scheduler 子进程（每张 GPU 一个）', [296, 192, 552, 324], m=[2, 994, 336, 560]),
        group('Detokenizer 子进程', [944, 192, 274, 164], m=[2, 800, 336, 150]),
    ]

    base['blocks'] = [scene(1220, 520, parts, wires, steps, mw=340, mh=1562, groups=groups,
                            cap='一条 stream 聊天请求：3 个进程、3 条单向管道（①②③）')]
    base['children'] = [
        node('src_chat', '聊天协议层源码', '聊天协议层：进门转换、流式先踢一脚、出门切增量',
             '顶层「聊天协议层」框里的三段代码。',
             lead='你在顶层的「聊天协议层」框里：进门在线程池里转换请求，流式先跑到第一块再回 200，出门按偏移切增量。',
             src=SB + ':80-118',
             blocks=[
                 code(SB, [(80, 118)], notes=[
                     at((99, 104), '注释', '模板渲染 + 分词丢到线程池，不卡主事件循环。',
                        q='Convert off-loop: jinja render + full-prompt tokenization blocks', has='run_in_executor'),
                     at((111, 118), '推断', '按 stream 分流：流式和非流式在这里分开。', has='_handle_streaming_request'),
                 ], cap='handle_request：校验 → 线程池里转换 → 按 stream 分流'),
                 code(SC, [(2369, 2403)], notes=[
                     at((2381, 2387), '注释', '先拿到第一块再回 200：校验错误还能变成 HTTP 400。',
                        q='Kick-start the generator to trigger validation before HTTP 200 is sent.', has='__anext__'),
                     at(2402, '推断', '响应结束后跑的后台任务：客户端断开时负责 abort。', has='create_abort_task'),
                 ], cap='_handle_streaming_request：踢一脚，再交给 StreamingResponse'),
                 code(SC, [(848, 853)], notes=[
                     at((851, 853), '推断', '默认拿到的是全文：按偏移切出这一步的增量，再记下新偏移。', has='content["text"][offset:]'),
                 ], cap='出门：全文 → 增量'),
             ]),
        node('src_tm', 'TokenizerManager 源码', 'generate_request：登记、发出、在信箱上等',
             '顶层「请求登记处」框里的代码。',
             lead='你在顶层的「请求登记处」（TokenizerManager）框里：一条请求怎么被登记、发出，再在信箱上等结果。',
             src=TM + ':1061-1125',
             blocks=[
                 code(TM, [(1061, 1069), (1092, 1092), (1106, 1125)], notes=[
                     at(1066, '推断', '第一次调用时才起收件协程 handle_loop；结果的接收在那边。', has='auto_create_handle_loop'),
                     at(1092, '推断', '为这个 rid 建信箱 ReqState，放进 rid_to_state。', has='_init_req_state'),
                     at((1106, 1108), '推断', '暂停闸门：服务器暂停时请求在这里等。', has='is_pause_cond'),
                     at(1116, '推断', 'chat 主路上 input_ids 已有，这里只是打包，不真的分词。', has='_tokenize_one_request'),
                     at(1120, '推断', '经管道 ① 同步 send，不等回应。', has='_send_one_request'),
                     at((1121, 1123), '注释', '发出后丢掉 prompt，主进程不再持有。',
                        q='obj.input_ids was dropped after dispatch, so the request cannot be dumped.', ref=A(TM, 296), has='obj.input_ids = None'),
                     at((1124, 1125), '推断', '开始等：每次信箱被叫醒就 yield 一块。', has='_wait_one_response'),
                 ], cap='generate_request（节选）'),
                 code(TM, [(279, 286)], notes=[
                     at((282, 284), '推断', '信箱：out_list 放结果、finished 标结束、event 用来叫醒。', has='event: asyncio.Event'),
                 ], cap='ReqState：发请求的协程和收结果的协程之间的信箱'),
                 code(TM, [(2342, 2370)], notes=[
                     at((2346, 2351), '推断', '最多等 4 秒（默认）；超时醒来只为查客户端断没断。', has='wait_for'),
                     at((2366, 2370), '注释', '醒来后原子地取走全部结果、清 event。', q='Drain all pending outputs atomically.', has='state.event.clear()'),
                 ], cap='_stream_one_response：在信箱上等'),
             ]),
        node('src_hloop', 'handle_loop 源码', 'handle_loop：收管道 ③，按 rid 投信箱、攒着叫醒',
             '顶层「收件协程」框里的代码。',
             lead='你在顶层的「收件协程」（handle_loop）框里：从管道 ③ 收结果，按 rid 投进信箱，攒满一批再叫醒。',
             src=TM + ':2916-2929',
             blocks=[
                 code(TM, [(2916, 2929)], notes=[
                     at(2920, '推断', '在管道 ③ 上 await；收到一批就处理一批。', has='async_sock_recv'),
                     at((2921, 2925), '推断', '生成结果交给 _handle_batch_output，其它类型走分发表。', has='_handle_batch_output'),
                 ], cap='handle_loop：常驻协程'),
                 code(TM, [(3081, 3084), (3106, 3115)], notes=[
                     at(3081, '推断', 'Detokenizer 发来的是这一步的增量文字。', has='delta_text = recv_obj.output_strs[i]'),
                     at((3108, 3110), '注释', '中间块只传引用、text 给 None，避免累计 O(n²)。',
                        q='Non-incremental intermediate: pass reference (no copy) and defer text to _wait_one_response', has='O(n^2)'),
                 ], cap='_handle_batch_output（节选）：增量先拼进 ReqState，中间块不拼全文'),
                 code(TM, [(3226, 3240), (3252, 3254)], notes=[
                     at(3228, '推断', '请求结束：从登记表里删掉。', has='del self.rid_to_state[rid]'),
                     at((3236, 3240), '文档', '攒满 batch_notify_size 个才一起叫醒并让出循环。',
                        q='Reduces asyncio wakeup overhead under high concurrency.', ref=A(SERV, 263), has='batch_notify_size'),
                     at((3252, 3254), '推断', '这一批处理完，剩下没叫醒的全部叫醒。', has='s.event.set()'),
                 ], cap='投进信箱、攒着叫醒'),
             ]),
        node('src_detok', 'Detokenizer 源码', 'Detokenizer：surr / read 两个偏移的增量反分词',
             '顶层「反分词」框里的代码。',
             lead='你在顶层的「反分词」框里：每个 rid 一份状态，带一小段上文一起解，差值就是新文字。',
             src=INC + ':207-246',
             blocks=[
                 code(DM, [(164, 171)], notes=[
                     at(168, '推断', '阻塞收：闲着就该睡，不像 Scheduler 那样要避免阻塞。', has='sock_recv(self.recv_from_scheduler)'),
                     at((170, 171), '推断', '有输出就经管道 ③ 推给主进程。', has='sock_send(self.send_to_tokenizer, output)'),
                 ], cap='event_loop：收 → 分发 → 发'),
                 code(INC, [(122, 136)], notes=[
                     at((125, 133), '推断', '两个偏移：surr 之后是「上文」，read 之后是这次新来的 token。', has='surr_offset'),
                 ], cap='DetokenizeState：每个 rid 一份'),
                 code(INC, [(207, 246)], notes=[
                     at(216, '推断', '解(上文 + 新 token) 减去 解(上文) = 这一步的新文字。', has='new_text = read_text[len(surr_text) :]'),
                     at((223, 228), '推断', '干净的文字：提交，偏移往前挪。', has='self.surr_offset = self.read_offset'),
                     at((229, 236), '注释', '以 � 结尾：只发可打印前缀，偏移不动，下次重试。',
                        q='Incomplete UTF-8: emit the printable prefix only; do not commit', has='find_printable_text'),
                     at((238, 246), '推断', '结束那一步才裁 stop 串 / EOS，发 sent_offset 之后的尾巴。', has='trim_matched_stop'),
                 ], cap='step：每一步该发哪段文字'),
             ]),
        slot('sched_loop', '调度循环里面'),
        slot('kv_cache', 'KV 缓存与前缀树里面'),
        slot('model_fwd', '模型前向 + 采样里面'),
    ]
    return base
