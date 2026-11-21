# sglang：一条流式 chat 请求怎么走完一圈（只有机器图的项目）
from cv import *

META = dict(title='sglang：一条请求怎么走一圈')

HS = 'python/sglang/srt/entrypoints/http_server.py'
SC = 'python/sglang/srt/entrypoints/openai/serving_chat.py'
SB = 'python/sglang/srt/entrypoints/openai/serving_base.py'
TM = 'python/sglang/srt/managers/tokenizer_manager.py'
SCH = 'python/sglang/srt/managers/scheduler.py'
OS_ = 'python/sglang/srt/managers/scheduler_components/output_streamer.py'
DT = 'python/sglang/srt/managers/detokenizer_manager.py'
ID = 'python/sglang/srt/managers/incremental_detokenize.py'


def root(base):
    base['lead'] = ('跟着一条请求 A（「用一句话介绍上海」，流式）走一圈：去程是 主进程 → 调度进程（GPU）；'
                    '回程**不走原路**，而是 调度 → 反分词进程 → 主进程——三个进程首尾相接成一个环。'
                    '每个框、每条线都能点开；按 ▶ 推演，看值一步步变。')

    parts = [
        part('client', '客户端', [10, 210, 104, 124], m=[76, 10, 188, 100], kind='ext',
             glyph=g_text('请求 A', '已收 3 块'),
             say='发 POST，收 SSE', why='出错能拿到 400', trap='响应头要等首字',
             card=card(
                 '在这台机器之外：发出一条 OpenAI 格式的 `POST /v1/chat/completions`，`stream: true`，'
                 '然后在同一条 HTTP 连接上一块块收 SSE。',
                 ['例子（设）：请求 A，`messages` 只有一条「用一句话介绍上海」，`max_tokens: 64`，服务名 `qwen`。',
                  '收到的每一块都是一行 `data: {...}`，`object` 是 `chat.completion.chunk`，最后一行是 `data: [DONE]`'
                  '（`python/sglang/srt/entrypoints/openai/sse_utils.py:107-115`）。'],
                 [note('注释', '服务端故意等引擎第一次回包后才发 HTTP 200：请求有错时客户端拿到的是正经的 400，'
                       '而不是一个已经开始的流里夹着错误。',
                       q='Kick-start the generator to trigger validation before HTTP 200 is sent.', ref=SC + ':2381')],
                 ['连 HTTP 响应头都要等到首 token（prefill 做完）之后才到：客户端看到的「首字延迟」里包含了排队和 prefill。',
                  '收到的块数可以少于 token 数：主进程忙时，两个 token 的文字会挤进同一块（见推演第 13 步）。',
                  '客户端断开后服务端有三道保险把请求中止掉：连接结束后睡 2 秒再查、发送失败时强制关生成器、'
                  '等待时至多每 4 秒问一次连接还在不在。GPU 不会一直替走掉的客户端算。'])),

        part('chat_in', '套模板 + 分词', [230, 30, 155, 124], m=[76, 196, 188, 124],
             kind='act', n='1',
             glyph=g_kv([('messages', '1 条'), ('input_ids', '18 个')]),
             say='对话 → 18 个 token id', why='放线程池，不卡事件循环', trap='分词在这，不在 TokenizerManager',
             card=card(
                 '`OpenAIServingChat`。HTTP 路由收到请求后直接交给它（`python/sglang/srt/entrypoints/http_server.py:1906-1910`）。'
                 '它把 OpenAI 格式的对话翻译成引擎内部的请求：套聊天模板变成一段文本，再编码成 token id。',
                 ['通用骨架是基类的 `handle_request`：校验 → 转内部格式 → 按 `stream` 分流'
                  '（`python/sglang/srt/entrypoints/openai/serving_base.py:111-118`）；`ValueError` 变 400，其它变 500。',
                  '「先渲染成文本、再编码」分两步做，而不是一步到位'
                  '（`python/sglang/srt/entrypoints/openai/serving_chat.py:2274-2284`）。',
                  '交出去的是 `GenerateReqInput`：纯文本模型带 `input_ids`，外加采样参数、`stream=True`；'
                  '`rid` 客户端没给就是空（`python/sglang/srt/entrypoints/openai/serving_chat.py:1280-1288`）。'],
                 [note('注释', '模板渲染 + 长 prompt 分词会卡住事件循环；主进程只有这一个循环，所有在途请求的收发都靠它，'
                       '所以挪进线程池（rust 分词器还会释放 GIL）。',
                       q='Convert off-loop: jinja render + full-prompt tokenization blocks', ref=SB + ':99'),
                  note('注释', '分两步：模板里已经带了角色和特殊 token，编码时要避免再加一次 BOS。',
                       q='templates already include role/special tokens, so the encode must', ref=SC + ':2135')],
                 '名字叫 TokenizerManager 的那个类，在 chat 主路上**不分词**：`input_ids` 已经有了就直接用'
                 '（`python/sglang/srt/managers/tokenizer_manager.py:1291-1292`）。只有原生 `/generate` 这类只给文字的入口，'
                 '它才真正调分词器。')),

        part('tm', '前台：登记发出', [505, 30, 155, 124], m=[76, 384, 188, 124],
             kind='act', n='2',
             glyph=g_kv([('rid', '5f0c9e2a…'), ('读锁', '持有中')]),
             say='发 rid、登记、发给调度', why='持读锁，换权重前等它', trap='名叫 Tokenizer，却不分词',
             card=card(
                 '`TokenizerManager`，主进程里的引擎「前台」：给请求分配 rid、在信箱表里登记、经 ZMQ 发给调度进程，然后等着回程的结果'
                 '（`python/sglang/srt/managers/tokenizer_manager.py:1061-1140`）。',
                 ['rid：客户端没给就用 `uuid.uuid4().hex`（`python/sglang/srt/managers/io_struct.py:645-646`）。'
                  '例子：A 的 rid = `5f0c9e2a…`（设）。',
                  '登记：给 rid 建一个 `ReqState` 放进 `rid_to_state`；回程的包就靠这一项找到主人（见 8 号框）。',
                  '拿权重更新的**读锁**，包住后面整个「发出 + 等待」（`python/sglang/srt/managers/tokenizer_manager.py:1111`）；'
                  '做长度校验；打包成 `TokenizedGenerateReqInput`；同步发出，然后去等自己的信箱。'],
                 [note('注释', '请求从发出到结束都持读锁，权重更新拿写锁：要等所有在途请求结束才换权重。',
                       q='we are using the model_update_lock to check if there is still on-going requests.',
                       ref=TM + ':2688'),
                  note('提交', '发之前如果这个 rid 已经被中止，就不发了。',
                       q='Do not dispatch a request whose abort was already sent', ref='c:005b8e033f')],
                 ['它是一个异步生成器：接待者 `async for` 它，它就一块块吐结果——「发出去」和「等回来」是同一个调用。',
                  '长度校验在这里做一次、调度进程里又做一次；代码自己标了 FIXME'
                  '（`python/sglang/srt/managers/tokenizer_manager.py:1487`）。'])),

        part('sched', '调度循环', [780, 30, 155, 124], m=[76, 590, 188, 124],
             kind='gate', n='3',
             glyph=g_kv([('waiting', '[ ]'), ('running', '[B, A]')]),
             say='收请求、排队、选这一批', why='能 prefill 就先做', trap='多卡也只有 rank 0 收包',
             card=card(
                 '`Scheduler`：调度进程（每张 GPU 一个，单卡就一个）里的单线程死循环：每一圈先把新请求收进来，'
                 '再决定这一圈让 GPU 跑哪些请求（`python/sglang/srt/managers/scheduler.py:2235-2305`）。',
                 ['收：用非阻塞方式把管道里已有的消息一口气取完，取空就走'
                  '（`python/sglang/srt/managers/scheduler_components/request_receiver.py:152-159`）。新请求变成一个 `Req`，进 `waiting_queue`。',
                  '选批：有能做的 prefill 就先做 prefill，否则才对 `running_batch` 做一步 decode'
                  '（`python/sglang/srt/managers/scheduler.py:4443-4456`）。默认按先来后到排队，但每条等待请求都会先在 radix 前缀树里找最长的已缓存前缀。',
                  '装多少：`PrefillAdder` 按 token 预算、显存余量逐条往批里装，装好后给新请求分配 KV 槽位'
                  '（`python/sglang/srt/managers/scheduler.py:4955-4978`）。prefill 完的请求下一圈并入 `running_batch`。'],
                 [note('注释', '规则就是「能做 prefill 就先做」。', q='Run prefill first if possible', ref=SCH + ':4444'),
                  note('推断', '好处：新请求的首字延迟取决于它什么时候被 prefill；decode 推迟一步，只是让在跑的请求各多等一步。代码没写这个理由。'),
                  note('推断', '收请求不阻塞：这个循环还要驱动 GPU，不能卡在 socket 上等，每圈顺手看一眼有没有新来的。')],
                 ['校验失败的请求**也入队**：先标成中止再入队，不会被算，但会沿正常的输出通道把原因送回去'
                  '（`python/sglang/srt/managers/scheduler.py:3684-3686`）——推断：这样主进程只需要一种接收方式。',
                  '多卡张量并行时只有 rank 0 有 ZMQ 收发口，收到后广播给其他 rank，所以每个 rank 做出同样的批决定。',
                  '默认不把 prefill 和 decode 混进同一次前向（`enable_mixed_chunk` 默认关）。']),
             to='schedloop'),

        part('gpu', 'GPU 前向采样', [1055, 30, 155, 124], m=[76, 778, 188, 124],
             kind='act', n='4',
             glyph=g_kv([('GPU', '算 [B,A] decode'), ('CPU', '处理上一步')]),
             say='算一步，每条请求出 1 个 token', why='CPU 和 GPU 错开一拍并行', trap='结果下一圈才处理',
             card=card(
                 '`TpModelWorker` / `ModelRunner`：真正在 GPU 上做模型前向和采样：给这一批里每条请求算出下一个 token'
                 '（`python/sglang/srt/managers/tp_worker.py:689`、`python/sglang/srt/managers/tp_worker.py:771`）。模型内部这里不展开。',
                 ['默认开 overlap：每一圈先把本圈批 N **发射**给 GPU（结果先放进 `result_queue`），**然后**才去处理上一圈批 N−1 的结果，'
                  '最后再发射 N 的采样（`python/sglang/srt/managers/scheduler.py:2280-2300`）。',
                  '也就是说：CPU 在整理上一批（追加 token、判结束、发给反分词）的时候，GPU 正在算这一批。'],
                 [note('注释', 'overlap 就是为了让 CPU 上的调度工作和 GPU 计算同时进行。',
                       q='A scheduler loop that overlaps the CPU processing and GPU computation.', ref=SCH + ':2236'),
                  note('注释', '采样排在处理上一批之后，因为它可能要用上一批的结果（比如语法约束）。',
                       q='It depends on the result of the last batch (e.g., grammar), so we run it after the last batch is processed.',
                       ref=SCH + ':2298')],
                 ['「处理结果」永远比「发起计算」晚一拍：一条请求在 N−1 里已经结束，N 可能已经把它算进去了，'
                  '代码专门处理这种情况（`python/sglang/srt/managers/scheduler.py:5182-5192`）。中止也一样，还会多跑一次 decode。',
                  '例外：上一批是 prefill 且设了 `SGLANG_DISABLE_OVERLAP_AFTER_PREFILL`，或下一批的语法约束要用上一批的结果时，'
                  '先处理上一批（`python/sglang/srt/managers/scheduler.py:2307-2326`）。'])),

        part('out', '收结果、打包', [1055, 210, 155, 124], m=[76, 966, 188, 124],
             kind='act', n='5',
             glyph=g_kv([('A', '+1 id（共 2）'), ('B', '+1 id')]),
             say='追加 token、判结束、只发新的', why='解码头只发一次', trap='像停止串前半截就先压着',
             card=card(
                 '结果处理 + `OutputStreamer`：拿到一批的 GPU 结果后：把新 token 追加到每条请求的 `output_ids`、判断它结束没有，'
                 '再把「这一步的新 token」打包发给反分词进程（`python/sglang/srt/managers/scheduler_components/output_streamer.py:262-353`）。',
                 ['判结束的顺序：越界 token → 停止串 → 停止 token / EOS → 长度上限 → 语法终止'
                  '（`python/sglang/srt/managers/schedule_batch.py:1487-1528`）。结束的请求把 KV 写回 radix 树，以后同样的前缀能复用。',
                  '发不发：已结束一定发；流式请求按 `stream_interval`（默认 1）发，首 token 总是立刻发；非流式每 50 个才发一次。'
                  '只发上次之后的新 id（`send_token_offset` 往前推）。',
                  '首包额外带一个「解码头」：prompt 最后 5 个 token 等（`python/sglang/srt/managers/incremental_detokenize.py:87-105`）。',
                  '整批打成**一个** `BatchTokenIDOutput` 发出：一步一个消息，不是一条请求一个消息。'],
                 [note('注释', '头只在首包发一次，反分词进程收下后在这条请求的整个生命期里都留着；之后每步只传增量 id。',
                       q="first output only; the detokenizer keeps it for the request's lifetime and", ref=ID + ':75'),
                  note('推断', '旧做法每步都把整段待解码 id 重复序列化跨进程（`c:8080507c3e` 删掉的那些字段），新做法只传增量。'),
                  note('推断', '尾巴像停止串的前半截时先压着不发：否则客户端会先收到它，事后撤不回来。')],
                 ['调度进程里也有分词器：判断停止串要把尾巴几个 token 解成文字'
                  '（`python/sglang/srt/managers/schedule_batch.py:1276-1285`）。只解尾巴一小段，不是完整的反分词。',
                  '尾部可能是某个停止串的前半截时，这一步**先不发**'
                  '（`python/sglang/srt/managers/scheduler_components/output_streamer.py:610`）。'])),

        part('detok', '反分词成文字', [1055, 390, 155, 124], m=[76, 1172, 188, 124],
             kind='act', n='6',
             glyph=g_kv([('新 id', 't2'), ('新文字', '「是」')]),
             say='带一点前文解码，再做差', why='单独解一个 token 常会错', trap='半个汉字先不发，等下一个',
             card=card(
                 '`DetokenizerManager`：独立的一个进程，单线程循环：收一批新 token id → 解成文字 → 推给主进程'
                 '（`python/sglang/srt/managers/detokenizer_manager.py:164-172`）。每条请求在这里有一份增量解码状态，按 rid 存。',
                 ['首包带着头来，就为这条 rid 建状态；之后的包查表接着用。新 id 追加到这条请求的待解码序列里。',
                  '两个游标：`surr_ids` 是已确认文字的最后一小段（当前文），`read_ids` 是前文 + 新 id。整批一起解这两段，'
                  '新文字 = 后者减去前者（`python/sglang/srt/managers/incremental_detokenize.py:216`）。',
                  '结束时剪掉停止 token / 停止串，只发还没发过的部分，然后释放这条 rid 的状态'
                  '（`python/sglang/srt/managers/incremental_detokenize.py:238-246`）。',
                  '打成一个 `BatchStrOutput`（整批的新文字，外加原样透传的 id 和统计）推给主进程。'],
                 [note('推断', '很多分词器单独解一个 token 的结果和在上下文里不同（词首空格、字节级 BPE 把一个汉字拆成几个 token），'
                       '带一小段前文解再做差才对；首包里 prompt 尾巴那 5 个 token 就是给第一个输出当前文的。'),
                  note('推断', '为什么独立成一个进程，代码和提交都没写。可能的好处：放主进程会和 HTTP/SSE 抢同一个事件循环和 GIL，'
                       '放调度进程会拖慢驱动 GPU 的循环；独立出来，三段可以流水并行。')],
                 ['半个汉字：新文字以 `�` 结尾（UTF-8 不完整）时只发能打印的前缀，游标不前进，等下一个 token 再试'
                  '（`python/sglang/srt/managers/incremental_detokenize.py:230-236`）。',
                  '状态表有上限（默认 65536 条，满了挤掉最老的）；状态丢了只会丢文字、打警告。'])),

        part('loop', '回程收包', [505, 390, 155, 124], m=[76, 1378, 188, 124],
             kind='bg', n='7',
             glyph=g_kv([('收到', 'BatchStrOutput'), ('rids', '[B, A]')]),
             say='常驻协程，按 rid 分包', why='中间包不拼全文，免 O(n²)', trap='谁在往信箱放包？是它',
             card=card(
                 '`handle_loop`：主进程里一个常驻协程，专门收反分词进程推回来的消息（`python/sglang/srt/managers/tokenizer_manager.py:2916-2929`）。'
                 '一个 `BatchStrOutput` 里是一整批请求的结果，它按 rid 拆开，分别放进各自的信箱。',
                 ['对每个 rid：查 `rid_to_state`，查不到就警告跳过；组 `meta_info`（结束原因、prompt / completion / cached token 数等）。',
                  '默认的「非增量」模式下，中间包**不拼字符串**，`text` 留空，等这条请求自己的协程去拼'
                  '（`python/sglang/srt/managers/tokenizer_manager.py:3107-3115`）。',
                  '包放进 `out_list`；请求结束就从表里删掉这个 rid（`python/sglang/srt/managers/tokenizer_manager.py:3228`）。'],
                 [note('注释', '中间包不拼字符串：每步拼一次全文是 O(n)，累积起来就是 O(n²)。',
                       q='O(n) per-step cost that compounds to O(n^2).', ref=TM + ':3110'),
                  note('文档', '攒一批再唤醒：高并发时减少 asyncio 唤醒的开销。',
                       q='Reduces asyncio wakeup overhead under high concurrency.',
                       ref='python/sglang/srt/arg_groups/fields/serving.py:263')],
                 ['「效果在别处」的典型：读 `generate_request` 的人看不到谁在往信箱里放东西——放东西的是这个常驻协程。',
                  '它是懒启动的，第一个请求进来才创建（`python/sglang/srt/managers/tokenizer_manager.py:2891-2900`）；'
                  '推断：前台构造时 uvicorn 的事件循环还没起来。',
                  '同一个收口也收调度进程直接发回的控制类回复（flush cache、权重更新等），那些不经过反分词。'])),

        part('state', '每条请求的信箱', [505, 210, 155, 124], m=[76, 1566, 188, 124],
             kind='store', n='8',
             glyph=g_kv([('A 的信箱', '[包2]'), ('event', '已 set')]),
             say='rid → 包列表 + 一个唤醒事件', why='收包和等包的协程靠它交接', trap='中间包可丢：只取最后一个',
             card=card(
                 '`ReqState`：`rid_to_state` 表里每条在途请求一项：包列表 `out_list`、`finished` 标记、一个 `asyncio.Event`'
                 '（`python/sglang/srt/managers/tokenizer_manager.py:4278-4287`）。回程的包在这里从「大家共用的收包协程」'
                 '交到「这条请求自己的协程」手上。',
                 ['去程时前台就建好这一项（重复 rid 直接报错），所以回程的包知道该送到哪。',
                  '这条请求自己的协程等在 `event` 上，带 4 秒超时；超时就顺便查一下客户端是不是断开了'
                  '（`python/sglang/srt/managers/tokenizer_manager.py:2343-2364`）。',
                  '醒来后一次取走所有包；默认非增量模式下**只用最后一个**'
                  '（`python/sglang/srt/managers/tokenizer_manager.py:2381-2382`），此刻才把全文拼出来。'],
                 [note('推断', '用一个 Event 交接：收包协程不用知道谁在等，等的协程也不用轮询；所有并发请求共用一个收口。'),
                  note('推断', '非增量模式下每个包都是「到目前为止的全部」，最后一个包含了前面所有，所以丢掉中间的不丢数据。')],
                 ['主进程忙时信箱里会排着几个包：客户端会收到一块含好几个 token 文字的 delta。',
                  '开了 `--incremental-streaming-output` 才是另一种做法：包里是增量，要把几个包合并起来'
                  '（`python/sglang/srt/managers/tokenizer_manager.py:2374-2380`）。'])),

        part('sse', '写成 SSE', [230, 210, 155, 124], m=[76, 1754, 188, 124],
             kind='out', n='9',
             glyph=g_list(['"上海"', '"是"'], on=1),
             say='切出新增文字，一块一行', why='先等首包，出错能回 400', trap='第三次切增量：按偏移切全文',
             card=card(
                 '`OpenAIServingChat`：回程的最后一站，和 1 号框是**同一个**接待对象：`async for` 前台吐出来的结果，把每次的新文字写成一块 '
                 '`chat.completion.chunk`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2405-2825`）。',
                 ['第一次先发一块 `role: assistant`、内容为空（`python/sglang/srt/entrypoints/openai/serving_chat.py:2596-2615`）。',
                  '之后每次拿到的是累计全文，用 `stream_offsets` 记着上次切到哪，只发后面新增的部分'
                  '（`python/sglang/srt/entrypoints/openai/serving_chat.py:848-853`）。',
                  '结束：发一块带 `finish_reason` 的，可选一块 usage，最后 `data: [DONE]`'
                  '（`python/sglang/srt/entrypoints/openai/serving_chat.py:2825`）。'],
                 [note('注释', '先等生成器吐出第一块再回 HTTP 200：校验错误能变成正经的 HTTP 400，而不是混在 SSE 流里。',
                       q='a proper HTTP 400 error response instead of streaming it as SSE payload.', ref=SC + ':2383')],
                 ['文字一路被「切增量」三次：调度端按 `send_token_offset` 切 id、反分词按两个游标切文字、这里按 `stream_offsets` '
                  '切累计全文；夹在中间的前台反而把它拼回了全文。',
                  'SSE 块的 `id` 默认直接用引擎 rid；类里那个返回 `"chatcmpl-"` 的前缀在默认路径上没用到。'])),
    ]

    wires = [
        wire('w_post', 'client', 'chat_in', 'POST 请求', fs='r', ts='l', mfs='b', mts='t',
             why='都在主进程里',
             card=card('OpenAI 兼容的 `POST /v1/chat/completions`：FastAPI 把请求体解析成 `ChatCompletionRequest`，'
                       '路由函数直接把它交给接待对象（`python/sglang/srt/entrypoints/http_server.py:1901-1910`）。',
                       '主进程是一个 uvicorn + uvloop 的 asyncio 事件循环（`python/sglang/srt/entrypoints/http_server.py:3144-3165`）；'
                       'HTTP 服务和前台都跑在这个循环上。',
                       [note('注释', 'HTTP 服务、Engine、前台都在主进程里，和别的进程只靠 ZMQ 通信。',
                             q='The HTTP server, Engine, and TokenizerManager all run in the main process.', ref=HS + ':3310')],
                       '别的入口（`/v1/completions`、原生 `/generate`、Python `Engine` API）只在这一段换个接待者，后面走同一条路。')),

        wire('w_ids', 'chat_in', 'tm', '交出 18 个 id', fs='r', ts='l', mfs='b', mts='t',
             why='同一循环里的函数调用', trap='回程结果也从这里吐回',
             card=card('把 `GenerateReqInput`（例子里是 18 个 token id + 采样参数 + `stream=True`）交给前台的 `generate_request`。',
                       '`generate_request` 是一个异步生成器：接待者 `async for` 它，它就一块块吐结果。所以这条线其实来回都走——'
                       '同一个调用，回程的结果也从这里吐回接待者（见 9 号框）。',
                       [note('推断', '用异步生成器把「发出去」和「等回来」写成一个调用：调用方只看到一个能一块块迭代的流，不用管中间跨了几个进程。')],
                       '流式响应要先拿到第一块才回 HTTP 200：接待者先 `await` 生成器的第一块，再返回响应'
                       '（`python/sglang/srt/entrypoints/openai/serving_chat.py:2384-2387`）。')),

        wire('w_zmq1', 'tm', 'sched', 'ZMQ① 发请求', fs='r', ts='l', mfs='b', mts='t',
             why='发完就走，不等确认', trap='单向：回音不走这条',
             card=card('跨进程的第一条管道：前台把打包好的 `TokenizedGenerateReqInput` 推给调度进程'
                       '（`python/sglang/srt/managers/tokenizer_manager.py:851-854`）。',
                       ['三条 ZMQ 管道都是 PUSH/PULL、单向，走本机 `ipc://` 临时文件（`python/sglang/srt/server_args.py:894-905`）；消息默认用 pickle。',
                        '同步 send，不 await，不等调度进程确认，发完就去等自己的信箱。',
                        '发之前：`input_ids` 转成紧凑的整数数组，采样参数变成 `SamplingParams` 对象并校验。'],
                       [note('推断', '单向推送、不等回执：前台马上就能去服务下一条请求，回音走另一条路回来。'),
                        note('注释', '进程之间一律走 ZMQ，每个进程用不同的端口。',
                             q='Inter-process communication is done through IPC (each process uses a different port) via the ZMQ library.',
                             ref=HS + ':3311')],
                       '管道是单向的：响应**不会**从这条管道原路回来，而是绕一圈——调度 → 反分词 → 主进程。')),

        wire('w_launch', 'sched', 'gpu', '发射批 N', fs='r', ts='l', mfs='b', mts='t',
             why='只发射，不等算完',
             card=card('把这一圈选好的批交给 GPU：`run_batch` → `forward_batch_generation`'
                       '（`python/sglang/srt/managers/scheduler.py:5388`）。',
                       'overlap 模式下只是把 GPU 工作发射出去，结果先放进 `result_queue`，循环不在这里等'
                       '（`python/sglang/srt/managers/scheduler.py:2280-2284`）。',
                       [note('文档', 'overlap 调度器让 CPU 上的调度和 GPU 上的模型计算重叠；默认是开的。',
                             q='Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker.',
                             ref='python/sglang/srt/arg_groups/fields/schedule.py:215')],
                       '例子：A 第一次被选中时是一个单独的 prefill 批（18 个 token）；之后每圈是 `[B, A]` 一起的 decode 批。')),

        wire('w_result', 'gpu', 'out', '晚一拍取结果', fs='b', ts='t', mfs='b', mts='t',
             why='GPU 算 N 时 CPU 处理 N−1', trap='同一圈：发 N，收 N−1',
             card=card('上一圈批 N−1 的结果在这一圈才被取出来处理（`python/sglang/srt/managers/scheduler.py:2289-2292`）：'
                       '先等 GPU→CPU 拷贝完成，再交给结果处理。',
                       '按批类型分给 prefill / decode 两个处理器（`python/sglang/srt/managers/scheduler.py:5875-5918`）。',
                       [note('注释', '这正是 overlap 的意思：GPU 在算批 N 的同时，CPU 在处理批 N−1。',
                             q='A scheduler loop that overlaps the CPU processing and GPU computation.', ref=SCH + ':2236')],
                       '看代码时最容易迷糊：同一圈里「发射」的是 N、「处理」的是 N−1。')),

        wire('w_again', 'out', 'sched', '没完：下圈接着算', ml='下圈再算', fs='l', ts='b', mfs='r', mts='r',
             cls='dash', mlp=[302, 840], why='每圈 +1',
             card=card('没结束的请求留在 `running_batch` 里，下一圈继续 decode；刚做完 prefill 的批在下一圈并入 `running_batch`'
                       '（`python/sglang/srt/managers/scheduler.py:4378-4411`）。',
                       '一条请求要在这个小环上转很多圈：每圈每条在跑的请求多 1 个 token。例子里 A 转到第 12 个 token 才结束。',
                       [note('推断', '每圈只走一步、每圈重新选批：新请求不用等别人生成完，下一圈就能插进来先做 prefill。')],
                       '显存不够时，decode 批会把一部分请求「撤回」到等待队列，这里不展开。')),

        wire('w_zmq2', 'out', 'detok', 'ZMQ② 新 id', fs='b', ts='t', mfs='b', mts='t',
             why='整批一个消息', trap='只传增量 id',
             card=card('每一步一个 `BatchTokenIDOutput`：这一步所有该发的请求的新 token id 并排放在一起，推给反分词进程'
                       '（`python/sglang/srt/managers/scheduler_components/output_streamer.py:353`）。',
                       '例子：A 做 prefill 那一步发 `rids=[A]`、1 个新 id、带上 A 的解码头；之后每步发 `rids=[B, A]` 各 1 个新 id；'
                       '最后一步 A 的结束原因是 `{"type": "stop", "matched": 151645}`（设）。',
                       [note('推断', '一步一个消息而不是一条请求一个：消息数只跟步数有关，跟同时在跑多少条请求无关。')],
                       '解码需要的前文（头）只在首包传一次，之后反分词进程自己记着。')),

        wire('w_zmq3', 'detok', 'loop', 'ZMQ③ 新文字', fs='l', ts='r', mfs='b', mts='t',
             why='回主进程，不回调度', trap='这个收口有两个发送方',
             card=card('反分词进程把 `BatchStrOutput`（整批的新文字）推回主进程'
                       '（`python/sglang/srt/managers/detokenizer_manager.py:108-111`）。',
                       '收口在主进程的前台（`python/sglang/srt/managers/tokenizer_manager.py:831-833`），由常驻的收包协程读。',
                       [note('注释', '代码自己的总述：调度进程把输出的 token 发给反分词进程。',
                             q='forwards them, and sends the output tokens to the Detokenizer Manager.', ref=HS + ':3306')],
                       '这个收口有**两个**发送方：生成结果走 调度 → 反分词 → 主进程；flush cache、权重更新等控制类回复'
                       '由调度进程直接推回主进程（`python/sglang/srt/managers/scheduler.py:2405-2415`）。')),

        wire('w_fill', 'loop', 'state', '按 rid 放进信箱', fs='t', ts='b', mfs='b', mts='t',
             why='攒 16 个再唤醒',
             card=card('收包协程把每条请求的包放进它的 `out_list`，再 `event.set()` 叫醒等它的协程。',
                       '每攒满 `batch_notify_size`（默认 16）个就 set 一批，并让出一次事件循环；剩下的在最后统一 set'
                       '（`python/sglang/srt/managers/tokenizer_manager.py:3232-3240`）。',
                       [note('文档', '攒一批再唤醒：高并发时减少 asyncio 唤醒的开销。', q='Reduces asyncio wakeup overhead under high concurrency.',
                             ref='python/sglang/srt/arg_groups/fields/serving.py:263')],
                       '默认模式下放进去的中间包 `text` 是空的——全文要等对方醒来自己拼。')),

        wire('w_wake', 'state', 'sse', '醒来取最新包', fs='l', ts='r', mfs='b', mts='t',
             why='只要最后一个累计包', trap='两包排队就并成一块',
             card=card('这条请求自己的协程被唤醒，一次取走信箱里所有包，往上 `yield` 给接待者'
                       '（`python/sglang/srt/managers/tokenizer_manager.py:2333-2466`）。',
                       ['默认非增量模式：只取最后一个包，此刻才把累计全文拼出来'
                        '（`python/sglang/srt/managers/tokenizer_manager.py:2397-2403`）。',
                        '结束包：记日志和指标；如果是调度端产生的错误型中止，就转成异常或错误包。'],
                       [note('推断', '非增量模式下每个包都代表「到目前为止的全部」，所以只取最后一个不丢数据。')],
                       '主进程忙、两步的包在信箱里排了队时，客户端会收到一块**含两个 token 文字**的 delta：块数可以少于 token 数。')),

        wire('w_sse', 'sse', 'client', 'SSE 一块块', ml='回客户端', fs='l', ts='r', mfs='l', mts='l', mlp=[38, 1030],
             why='强制收尾', trap='断连停',
             card=card('每一块写成一行 `data: {...}` 发给客户端，最后 `data: [DONE]`。',
                       '响应对象挂了一个后台任务：连接结束 2 秒后，如果 rid 还在表里就中止它'
                       '（`python/sglang/srt/managers/tokenizer_manager.py:2878-2889`）。',
                       [note('注释', '发送失败时 Starlette 会把迭代器挂起不管，所以要强制关掉生成器，否则没人去中止这条请求。',
                             q='Starlette leaves the iterator suspended when a send fails, and on ASGI', ref=SC + ':162')],
                       '为什么睡 2 秒（而且是从 1 秒改成 2 秒的），提交说明里没写。')),
    ]

    # 推演开始前的样子（overview 里的小图是「A 刚出第 2 个 token『是』」那一刻）
    pre = {
        'client': g_text('请求 A', '刚发出'),
        'chat_in': g_kv([('messages', '1 条'), ('input_ids', '—')]),
        'tm': g_kv([('rid', '未分配'), ('读锁', '—')]),
        'sched': g_kv([('waiting', '[ ]'), ('running', '[B]')]),
        'gpu': g_kv([('GPU', '算 B decode'), ('CPU', '处理上一步')]),
        'out': g_kv([('B', '+1 id')]),
        'detok': g_kv([('A', '还没状态'), ('B', '解码中')]),
        'loop': g_kv([('收到', 'BatchStrOutput'), ('rids', '[B]')]),
        'state': g_kv([('rid 表', '只有 B'), ('A', '未登记')]),
        'sse': g_list(['（A 还没开始）']),
    }

    steps = [
        beat('客户端发出 A',
             '客户端 `POST /v1/chat/completions`：`messages` 一条「用一句话介绍上海」，`stream: true`，`max_tokens: 64`。'
             '此时服务器上已经有一条请求 **B** 在逐 token 解码。',
             note('注释', '主进程一个事件循环跑着 HTTP 服务、Engine 和前台。',
                  q='The HTTP server, Engine, and TokenizerManager all run in the main process.', ref=HS + ':3310'),
             focus=['client', 'w_post', 'chat_in'], state=pre),
        beat('套模板 + 分词（在线程池里）',
             '接待者把对话套上聊天模板渲染成文本，再编码：prompt = **18 个 token**（设）。交出 '
             '`GenerateReqInput`：18 个 id、`max_new_tokens` 64、`stream=True`、`rid=None`。',
             note('注释', '渲染和分词会卡住事件循环，所以挪到线程池里做。',
                  q='Convert off-loop: jinja render + full-prompt tokenization blocks', ref=SB + ':99'),
             focus=['chat_in', 'w_ids', 'tm'],
             state={'chat_in': g_kv([('messages', '1 条'), ('input_ids', '18 个')])}),
        beat('登记 rid，经 ZMQ① 发出',
             '前台分配 rid = `5f0c9e2a…`（设），在信箱表里给 A 建一项（空列表、事件未触发），拿读锁，'
             '把 `TokenizedGenerateReqInput` 经 **ZMQ①** 推给调度进程——同步发，发完就去等信箱。',
             note('推断', '单向推送、不等回执：前台马上能去服务下一条请求，回音走另一条路回来。'),
             focus=['tm', 'w_zmq1', 'state', 'sched'],
             state={'tm': g_kv([('rid', '5f0c9e2a…'), ('读锁', '持有中')]),
                    'state': g_kv([('A 的信箱', '[ ]'), ('event', '未触发')])}),
        beat('调度进程收下，A 进等待队列',
             '调度循环这一圈先用非阻塞方式把管道里的消息全取出来；A 变成一个 `Req` 进 `waiting_queue`。'
             '此时 `running_batch` = [B]。',
             note('推断', '不阻塞：这个循环还要驱动 GPU，每圈只是顺手看一眼有没有新请求。'),
             focus=['sched', 'w_zmq1'],
             state={'sched': g_kv([('waiting', '[A]'), ('running', '[B]')])}),
        beat('这一圈先做 A 的 prefill',
             'prefill 优先：A 单独组成一个 prefill 批，算 **18** 个 token，前缀命中 0（设）。给它分配 KV 槽位，'
             '把批 N 发射给 GPU。B 这一圈不动。',
             note('注释', '规则：能做 prefill 就先做。', q='Run prefill first if possible', ref=SCH + ':4444'),
             focus=['sched', 'w_launch', 'gpu'],
             state={'sched': g_kv([('waiting', '[ ]'), ('prefill', '[A] 18 个')]),
                    'gpu': g_kv([('GPU', '算 N：A prefill'), ('CPU', '处理 N−1：B')])}),
        beat('晚一拍：GPU 算 A 时，CPU 在处理上一批',
             'GPU 正在算批 N（A 的 prefill）；同一时间 CPU 取出上一圈批 N−1（B 的 decode）的结果：B 多 1 个 token，发出去。'
             '要到**下一圈**才轮到处理 A 的 prefill 结果。',
             note('注释', 'overlap：CPU 的处理和 GPU 的计算重叠进行。',
                  q='A scheduler loop that overlaps the CPU processing and GPU computation.', ref=SCH + ':2236'),
             focus=['gpu', 'w_result', 'out'],
             state={'out': g_kv([('B', '+1 id'), ('A', '（还在算）')])}),
        beat('首 token「上海」，打包发给反分词',
             '下一圈处理到 A 的 prefill 结果：首 token「上海」（t1，设）追加进 A 的 `output_ids`，没结束。A 并入 `running_batch`。'
             '首 token 立刻发：`BatchTokenIDOutput(rids=[A], output_ids=[[t1]])`，附带 A 的解码头（prompt 最后 5 个 token），经 **ZMQ②** 发出。',
             note('注释', '头只在首包发一次，反分词进程一直留着。',
                  q="first output only; the detokenizer keeps it for the request's lifetime and", ref=ID + ':75'),
             focus=['out', 'w_zmq2', 'detok'],
             state={'out': g_kv([('A', 't1 + 解码头'), ('A 已出', '1 个')]),
                    'sched': g_kv([('waiting', '[ ]'), ('running', '[B, A]')])}),
        beat('反分词：t1 → 「上海」',
             '反分词进程看到头，为 A 建一份解码状态；把 prompt 尾巴 5 个 token 当前文，和 t1 一起解，再减去前文，'
             '得到新文字「上海」。打成 `BatchStrOutput` 经 **ZMQ③** 推回主进程。',
             note('推断', '单独解一个 token 常常不对（词首空格、一个汉字拆成几个 token），带前文解再做差才对。'),
             focus=['detok', 'w_zmq3', 'loop'],
             state={'detok': g_kv([('新 id', 't1'), ('新文字', '「上海」')]),
                    'loop': g_kv([('收到', 'BatchStrOutput'), ('rids', '[A]')])}),
        beat('收包协程把包放进 A 的信箱',
             '`handle_loop` 按 rid 找到 A 的信箱；默认非增量模式下，中间包的 `text` 先留空；包放进 `out_list`，`event.set()`。',
             note('注释', '不在这里拼字符串，避免每步 O(n)、累积 O(n²)。',
                  q='O(n) per-step cost that compounds to O(n^2).', ref=TM + ':3110'),
             focus=['loop', 'w_fill', 'state'],
             state={'state': g_kv([('A 的信箱', '[包1]'), ('event', '已 set')])}),
        beat('A 的协程醒来：HTTP 200 和前两块 SSE',
             'A 自己的协程醒来，取最后一包，拼出全文「上海」，交给接待者。接待者这时才回 **HTTP 200**：'
             '先发一块 `role: assistant`，再发 `delta: "上海"`。客户端连响应头都是这时才收到。',
             note('注释', '第一块出来时，引擎已经回过一次包了。',
                  q='The first chunk is out, so the wire has seen the first engine event.', ref=SC + ':2400'),
             focus=['state', 'w_wake', 'sse', 'w_sse', 'client'],
             state={'state': g_kv([('A 的信箱', '[ ]'), ('event', '已清')]),
                    'sse': g_list(['role: assistant', '"上海"'], on=1),
                    'client': g_text('200', 'HTTP 头到了', tone='ok')}),
        beat('之后每圈：[B, A] 一起 decode',
             'A 留在 `running_batch` 里，此后每圈都是 `[B, A]` 的 decode 批，各出 1 个 token。第 2 个 token「是」（设）'
             '走同一圈：调度 → 反分词 → 主进程 → `delta: "是"`。',
             note('推断', '每圈只走一步、每圈重新选批：新请求不用等别人生成完就能插进来。'),
             focus=['out', 'w_again', 'sched', 'w_launch', 'gpu'],
             state={'gpu': g_kv([('GPU', '算 [B,A] decode'), ('CPU', '处理上一步')]),
                    'out': g_kv([('A', '+1 id（共 2）'), ('B', '+1 id')]),
                    'detok': g_kv([('新 id', 't2'), ('新文字', '「是」')]),
                    'loop': g_kv([('收到', 'BatchStrOutput'), ('rids', '[B, A]')]),
                    'state': g_kv([('A 的信箱', '[包2]'), ('event', '已 set')]),
                    'sse': g_list(['"上海"', '"是"'], on=1),
                    'client': g_text('请求 A', '已收 3 块', tone='ok')}),
        beat('如果一个 token 只是半个汉字',
             '（设）某一步的新 id 只是一个汉字的前半截字节，解出来以 `�` 结尾：这一步只发能打印的前缀（可能是空），'
             '游标不前进；下一个 token 来了带着它一起再解，整个字一次发出。',
             note('注释', '「只发可打印前缀」的规则是从 transformers 借来的。',
                  q='Borrowed from https://github.com/huggingface/transformers/', ref=ID + ':54'),
             focus=['detok'],
             state={'detok': g_kv([('新文字', '（空，等下一个）'), ('游标', '不动')])}),
        beat('主进程忙时：两步并成一块',
             '（设）主进程忙，A 的信箱里同时躺着两个包：协程只取最后一个（它已经包含前一个），拼出累计全文；'
             '接待者按 `stream_offsets` 切出新增部分——客户端收到一块含两个 token 文字的 delta。',
             note('推断', '每个包都是「到目前为止的全部」，所以只取最后一个不丢数据。'),
             focus=['state', 'w_wake', 'sse'],
             state={'state': g_kv([('A 的信箱', '[包k, 包k+1]'), ('取', '只取最后一个')])}),
        beat('第 12 个 token 是结束符',
             'A 的第 12 个 token 是 151645（设）：判结束时命中停止 token，结束原因 `{"type": "stop", "matched": 151645}`。'
             '这一步一定发；A 的 KV 写回 radix 树，离开 `running_batch`，又只剩 B。',
             note('注释', '判结束有固定顺序：同一步里停止串优先于 EOS（投机解码一步可能接受好几个 token）。',
                  q='Stop string beats EOS/stop-token matched in the same step (speculative',
                  ref='python/sglang/srt/managers/schedule_batch.py:1503'),
             focus=['out', 'w_zmq2', 'sched'],
             state={'out': g_kv([('A', '151645 → 结束'), ('B', '+1 id')]),
                    'sched': g_kv([('waiting', '[ ]'), ('running', '[B]')])}),
        beat('反分词剪掉结束符',
             '反分词进程收到 A 的结束行：末尾的 151645 被剪掉，结束符不会出现在文字里；只发还没发过的部分，然后释放 A 的解码状态。',
             note('注释', '剪法很简单：默认认定最后一个 token 就是命中的停止 token。',
                  q='We can always assume the last token is the matched stop token', ref=DT + ':204'),
             focus=['detok', 'w_zmq3', 'loop'],
             state={'detok': g_kv([('A', '151645 已剪'), ('A 的状态', '已释放')]),
                    'loop': g_kv([('收到', 'A 的结束包'), ('rids', '[B, A]')])}),
        beat('SSE 收尾，A 从表里删掉',
             '收包协程把结束包放进信箱后，从 `rid_to_state` 删掉 A。A 的协程取到结束包、退出（读锁随之放开）；'
             '接待者发一块 `finish_reason: "stop"`，最后 `data: [DONE]`。',
             note('推断', 'rid 一删，连接结束后的那个「2 秒后还在表里就中止」的后台任务就什么都不用做。'),
             focus=['sse', 'w_sse', 'client', 'state'],
             state={'state': g_kv([('A 的信箱', '已删除'), ('rid 表', '只剩 B')]),
                    'tm': g_kv([('rid', '5f0c9e2a…'), ('读锁', '已放开')]),
                    'sse': g_list(['finish_reason: stop', 'data: [DONE]'], on=[0, 1]),
                    'client': g_text('完成', '收到 [DONE]', tone='ok')}),
    ]

    groups = [
        group('主进程：一个事件循环', [218, 6, 454, 512], m=[68, 172, 204, 346]),
        group('调度进程（GPU）', [768, 6, 450, 338], m=[68, 566, 204, 534]),
        group('反分词进程', [990, 370, 228, 148], m=[68, 1148, 204, 158]),
        group('主进程：一个事件循环', [218, 6, 454, 512], m=[68, 1354, 204, 534]),
    ]

    base['blocks'] = [scene(1220, 520, parts, wires, steps, mw=340, mh=1900, groups=groups,
                            cap='主路：默认配置、单机单卡、`POST /v1/chat/completions`、`stream: true`、纯文本模型。'
                                '图上的数（rid、18 个 token、151645 等）是讲解设定的，只用来走一遍流程。'
                                '小图一开始显示的是 A 刚出第 2 个 token「是」那一刻。')]
    base['children'] = [slot('schedloop', '③ 调度循环里面')]
    return base
