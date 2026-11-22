# 顶层：一个 /v1/chat/completions 流式请求穿过 vLLM（HEAD 0e3c8dbdae，单机单卡默认配置）再回到客户端。
from cv import *

META = dict(title='vLLM：一个请求的来回')

CORE = 'vllm/v1/engine/core.py'
ALLM = 'vllm/v1/engine/async_llm.py'
OUTP = 'vllm/v1/engine/output_processor.py'
CCL = 'vllm/v1/engine/core_client.py'
SERV = 'vllm/entrypoints/openai/chat_completion/serving.py'
ROUT = 'vllm/entrypoints/openai/chat_completion/api_router.py'
APIU = 'vllm/entrypoints/serve/utils/api_utils.py'
BASE = 'vllm/renderers/base.py'
INP = 'vllm/v1/engine/input_processor.py'
SCHED = 'vllm/v1/core/sched/scheduler.py'
ASCH = 'vllm/v1/core/sched/async_scheduler.py'
SUT = 'vllm/v1/core/sched/utils.py'
MR = 'vllm/v1/worker/gpu/model_runner.py'
AU = 'vllm/v1/worker/gpu/async_utils.py'
UNI = 'vllm/v1/executor/uniproc_executor.py'
ARCH = 'docs/design/arch_overview.md'
DET = 'vllm/v1/engine/detokenizer.py'
CFGS = 'vllm/config/scheduler.py'
PAR = 'vllm/config/parallel.py'
SER = 'vllm/v1/serial_utils.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数：20 个 prompt token，生成 法国/的/首都/是/巴黎/。/<eos>）
def bq(*cells):
    return g_cells(list(cells) + [cell()] * (2 - len(cells)), left='新', right='旧')


def sch(n, done, ph):
    return g_kv([('本步排', n), ('已算', done), ('占位', ph)])


def root(base):
    base['lead'] = ('一个流式 `POST /v1/chat/completions` 从进门到最后一个 `data: [DONE]`。单机单卡默认配置下只有**两个进程**：'
                    '上面一排是 API Server 进程，下面一排是 EngineCore 进程，中间只有两条 ZMQ 管道。'
                    '按 ▶ 推演，拿「法国的首都是哪里？」走一遍；每个框、每条线都能点开。')

    parts = [
        part('client', '客户端', [8, 28, 124, 128], m=[70, 8, 200, 104], kind='ext',
             glyph=g_text('POST', 'stream=true'),
             say='发请求，收 SSE 流', trap='收到的 id 不带内部后缀',
             card=card(
                 '调用方：用 OpenAI 的 Chat Completions 协议发一个请求，然后一块一块地收结果。',
                 ['本例：`{"messages":[{"role":"user","content":"法国的首都是哪里？"}],"stream":true,"max_tokens":8}`，没带 `X-Request-Id` 头。',
                  '`stream=true` 时收到的是 SSE：每块一行 `data: {...}`，最后一行 `data: [DONE]`。'],
                 [note('推断', '用 OpenAI 的协议，现成的 OpenAI 客户端不用改就能接上；流式让第一个字尽早出现，不用等整段生成完。')],
                 ['客户端看到的 `id` 是 `chatcmpl-9f1c…`；引擎内部用的是多了 8 位随机后缀的 `chatcmpl-9f1c…-a1b2c3d4`，两者不是同一个字符串。' + R(SERV, 625),
                  '`stream=false` 只有最后一段不同：前端攒完整段再一次性返回 JSON。' + R(SERV, 443, 453)])),

        part('http', 'HTTP 层', [238, 28, 150, 128], m=[70, 182, 200, 120], kind='act', n='1', code='api_router',
             glyph=g_kv([('收', 'JSON'), ('回', 'SSE 流')]),
             say='解析请求，套上断连监听', why='中间件下也能发现断连', trap='流式时监听交给响应对象',
             card=card(
                 'uvicorn + FastAPI：把 HTTP body 解析成 `ChatCompletionRequest`，交给协议层；拿回一个生成器，包成 `StreamingResponse` 写回去。' + R(ROUT, 43, 59),
                 ['路由外面套着 `@with_cancellation`：同时起两个任务，一个跑 handler，一个监听 `http.disconnect`，谁先完成就取消另一个。' + R(APIU, 97, 104),
                  'handler 返回三种东西之一：错误、完整响应（非流式）、生成器（流式）。流式时包成 `text/event-stream`。' + R(ROUT, 67, 85)],
                 [note('注释', '断连检测不用 `request.is_disconnected`，因为它在有中间件时不工作，所以改成「handler 和断连监听赛跑」。',
                       q='This does _not_ use request.is_disconnected, which does not work with middleware.', ref=A(APIU, 72, 73))],
                 [note('注释', '流式时，`with_cancellation` 只管到 handler 返回 `StreamingResponse` 为止；之后由响应对象自己监听断连。断连 → 生成器被取消 → `generate()` 发 abort。',
                       q='this wrapper will stop listening for disconnects and instead the response object will start listening for disconnects.', ref=A(APIU, 85, 87)),
                  '`vllm/entrypoints/openai/api_server.py` 现在只是转发壳、已标记废弃；真入口在 `launchers/api_server/entry.py`。' + R('vllm/entrypoints/openai/api_server.py', 24, 30)])),

        part('chat', 'OpenAI 协议层', [500, 28, 150, 128], m=[70, 372, 200, 130], kind='act', n='2', code='OpenAIServingChat',
             glyph=g_kv([('max_tokens', '8'), ('输出', 'DELTA')]),
             say='定参数，拿生成器，拼 SSE', why='返回生成器，不等引擎', trap='路由返回时请求还没发出',
             card=card(
                 '协议适配：把 OpenAI 的请求翻译成引擎听得懂的东西，再把引擎的输出翻译回 OpenAI 的 chunk。',
                 ['进：拒空 messages → 交给 Renderer 渲染分词 → 定 request_id、`max_tokens`、`SamplingParams` → 调 `engine_client.generate(...)`。' + R(SERV, 411, 423),
                  '`stream=true` 时 `output_kind = DELTA`（每次只要新增的那一截）。' + R('vllm/entrypoints/openai/chat_completion/protocol.py', 821, 822),
                  '出：每个 `RequestOutput` 拼成一个 `ChatCompletionStreamResponse`，序列化成 `data: {...}\\n\\n`；结束时补 `data: [DONE]`。' + R(SERV, 509, 520)],
                 [note('推断', '`generate()` 是 async generator，流式分支直接把生成器 `return` 出去、不 `await`：谁开始读结果，谁才触发提交。客户端没开始收，引擎就不用开始算。'),
                  note('注释', '空 messages 在这里拒，而不是在 pydantic 模型上拒：内部调用方会直接构造请求对象，绕过模型校验。',
                       q='internal callers (renderers, adapters) construct ChatCompletionRequest directly.', ref=A(SERV, 303, 305))],
                 ['**时间上不连续**：调 `generate()` 只是造了一个生成器对象，真正 `add_request` 发生在它第一次被迭代时——那时路由函数早已返回，Starlette 已经开始写响应。' + R(SERV, 430, 441)]),
             to='chat_src'),

        part('render', '渲染 + 分词', [500, 194, 150, 136], m=[70, 568, 200, 110], kind='act', n='3', code='Renderer',
             glyph=g_text('20 个 token', '套模板 + 分词'),
             say='消息 → token id', why='放线程池，不卡事件循环', trap='分词不在 InputProcessor',
             card=card(
                 '把 `messages` 变成 token id：套 chat template，再用 tokenizer 分词。本例得到 20 个 token（设的）。',
                 ['`render_messages_async`（套模板）→ `tokenize_prompts_async`（分词）→ `process_for_engine_async`（整理成引擎输入）。' + R(BASE, 1412, 1452),
                  '产物是 `{"type": "token", "prompt_token_ids": [...]}`。' + R('vllm/inputs/engine.py', 56)],
                 [note('注释', '模板渲染和分词都是同步 CPU 活，用 `make_async` 丢进线程池；放在事件循环里会卡住所有并发请求的收发（后半句是推断）。',
                       q='Offload tokenization to the thread pool.', ref=A(BASE, 158)),
                  note('注释', '多模态预处理单独一个单线程池，保证分词永远不会排在多模态处理后面。',
                       q='Separate single-worker executor so tokenization never queues behind MM preprocessing', ref=A(BASE, 153, 154))],
                 ['这一版里**分词在 Renderer**，不在 `InputProcessor`。老文章说「Processor 负责分词」已经过时；直接传原始字符串的旧路径会打弃用警告。' + R(INP, 401, 406)])),

        part('asyncllm', '引擎门面', [764, 28, 166, 128], m=[70, 744, 200, 130], kind='act', n='4', code='AsyncLLM',
             glyph=g_kv([('内部 ID', '…-a1b2c3d4'), ('信箱', '空')]),
             say='打包成引擎请求，登记信箱', why='先登记再发，回包不会丢', trap='一个请求一个单槽信箱',
             card=card(
                 '引擎在 API 进程里的门面：把渲染好的输入打包成 `EngineCoreRequest` 交给引擎；再把引擎回来的输出，按请求交还给调用方。',
                 ['`generate()` 第一次被迭代 → `add_request`：校验、**克隆** `SamplingParams`、补 EOS、构造 `EngineCoreRequest`。' + R(INP, 552, 564),
                  '内部 ID = 外部 ID + 8 位随机后缀：`chatcmpl-9f1c…-a1b2c3d4`（后缀是设的）。' + R(INP, 357, 365),
                  '新建这个请求的信箱 `RequestOutputCollector`；**先**在 OutputProcessor 登记，**再** `await` 发给引擎。' + R(ALLM, 589, 598),
                  '之后 `generate()` 就守着信箱：`q.get_nowait() or await q.get()`，拿到一个 `yield` 一个。' + R(ALLM, 806, 816)],
                 [note('注释', '先登记、后发送：在第一个 `await` 之前登记，并发的任务（比如处理回包的那个）就能看到它。否则回包可能比登记先到，被当成已 abort 丢掉（这一句是推断）。',
                       q='Register locally before the first await so concurrent tasks see this request.', ref=A(ALLM, 594)),
                  note('注释', '加随机后缀是为了保证内部 ID 唯一：客户端可以用 `X-Request-Id` 自带 ID，重复是现实问题。',
                       q='adds 8 random characters in order to ensure uniqueness.', ref=A(INP, 350))],
                 ['客户端断开 → 生成器被取消 → `generate()` 捕获 `CancelledError` 并 abort 这个请求。' + R(ALLM, 821, 826)]),
             to='gen_src'),

        part('outproc', '输出处理', [1046, 28, 166, 128], m=[70, 944, 200, 130], kind='bg', n='8', code='OutputProcessor',
             glyph=g_kv([('token', '—'), ('文字', '—')]),
             say='token id → 文字，放进信箱', why='全批只循环一次', trap='stop 串在这里判，不在引擎',
             card=card(
                 '常驻后台任务：把引擎送回来的一批 token id，按请求去分词成文字、判 stop 串，做成 `RequestOutput`，塞进各自的信箱。',
                 ['两个常驻 task：`EngineCoreOutputQueueTask` 收 ZMQ 帧、解码、放进 `outputs_queue`；`output_handler` 取出，分块交给 `process_outputs`。' + R(CCL, 1249, 1302) + R(ALLM, 894, 924),
                  '`process_outputs`：用内部 ID 查请求状态（查不到 = 已 abort，忽略）→ 增量去分词 → `RequestOutput` → 放进信箱。' + R(OUTP, 762, 866),
                  '信箱是**单槽**的：消费者没来得及取，新的增量就合并进去，不会无限堆积。' + R(OUTP, 70, 80)],
                 [note('注释', '整批输出只在这一个函数里循环一次，尽量少写 Python 循环，降低系统开销。',
                       q='This is the only function that should loop over EngineCoreOutputs.', ref=A(OUTP, 752, 753)),
                  note('注释', '流式时，生产者跑在消费者前面，就把几个 RequestOutput 合并成一个。',
                       q='When streaming deltas, RequestOutputs are merged if the producer gets ahead of the consumer.', ref=A(OUTP, 58, 59)),
                  note('推断', 'stop 串放在前端判：只有前端手里有增量文本；把去分词搬进引擎，会占用驱动 GPU 那个进程的 CPU 时间。代价是引擎可能多算一两步。')],
                 ['「结束」有两处判：引擎里的调度器判 EOS / 长度；这里判 stop 串，命中后要**反向**发 ABORT 让引擎停算（引擎同一步已自己判停时不发）。' + R(ALLM, 932, 936) + R(OUTP, 881, 885),
                  '有 stop 串、且不要求输出 stop 串（默认如此）时，流式文字会「晚几个字」：扣住最长 stop 串长度减 1 个字符不发，防止把半截 stop 串发出去；要求输出 stop 串时不扣。' + R(DET, 131, 136)]),
             to='outproc'),

        part('engine', '引擎忙循环', [680, 354, 200, 150], m=[70, 1144, 200, 150], kind='act', n='5', code='EngineCoreProc',
             glyph=bq(),
             say='收请求 → 调度 → 交 GPU → 送结果', why='IO 线程和 GPU 重叠', trap='最多 2 批在途，先排后取',
             card=card(
                 '另一个进程里的「心脏」：一个停不下来的循环，每一轮收新请求 → 让调度器排活 → 交给 GPU → 把上一批的结果送回 API 进程。',
                 ['三个线程两个队列：**输入线程**收 ZMQ、解码、构造 `Request`，放进 `input_queue`；**主线程**跑忙循环；**输出线程**从 `output_queue` 取结果、编码、发出。' + R(CORE, 1436, 1457),
                  '主线程每轮：`_process_input_queue()` → `_process_engine_step()`；空闲时阻塞在 `input_queue.get()`，有活时只清空队列就去跑。' + R(CORE, 1816, 1823),
                  '默认 async scheduling 下走 `step_with_batch_queue`：`batch_queue` 最多放 2 批；没满就先排下一批、不等结果，满了才取最早那批的结果。' + R(CORE, 820, 837)],
                 [note('注释', 'IO 放在后台线程：ZMQ 收发会释放 GIL，能和 GPU 重叠；一部分序列化 / 反序列化也能和前向重叠。',
                       q='These enable us to overlap ZMQ socket IO with GPU since they release the GIL', ref=A(CORE, 1430, 1431)),
                  note('文档', '把 API 和引擎拆成两个进程，官方只笼统地说「分离关注点、提高吞吐」。更具体的「避免抢 GIL」是推断，仓库里没有直接出处。',
                       q='vLLM V1 uses a multi-process architecture to separate concerns and maximize throughput.', ref=A(ARCH, 69))],
                 ['单卡时 **GPU Worker 不是独立进程**，就住在这个进程的主线程里（`UniProcExecutor`）。官方文档说「每个 GPU 一个 worker 进程」，只对多卡成立。' + R(PAR, 1316, 1317),
                  'abort 会同时写进 `input_queue` 和 `aborts_queue`：既能每步提前处理，又不打乱顺序。' + R(CORE, 2296, 2299)]),
             to='loop_src'),

        part('sched', '调度器', [380, 364, 180, 140], m=[6, 1392, 160, 150], kind='store', n='6', code='AsyncScheduler',
             glyph=sch('—', '0', '0'),
             say='按 token 预算排活，判停', why='没有 prefill/decode 两阶段', trap='处理的是上上一轮的结果',
             card=card(
                 '每一步决定「每个请求这一步算几个 token」并分配 KV cache block；结果回来后把 token 追加到请求上、判 EOS / 长度。',
                 ['`schedule()`：先排 running，再排 waiting；新请求查前缀缓存 → 分配 KV block → 移入 running。' + R(SCHED, 778) + R(SCHED, 1040),
                  '待算量 = 已有 token + 占位符 − 已算。本例第 1 步 20 个（整段 prompt），之后每步 1 个。' + R(SCHED, 824, 828),
                  '**占位符**：排完一步就给请求记一个「下一步会多出一个还不知道值的 token」，结果回来时消掉。' + R(ASCH, 45, 48),
                  '`update_from_output`：追加 token、`check_stop`（EOS → stop_token_ids → 长度），产出 `EngineCoreOutput`。' + R(SCHED, 2933, 2959)],
                 [note('注释', '调度器里没有「prefill 阶段」「decode 阶段」之分，只有「每个请求还差几个 token」，所以分块 prefill、前缀缓存、投机解码都能用同一套逻辑。',
                       q='There\'s no "decoding phase" nor "prefill phase" in the scheduler.', ref=A(SCHED, 705)),
                  note('文档', 'async scheduling：GPU 跑第 N 步时，CPU 已经在排第 N+1 步，用占位符代替第 N 步还没出来的 token。',
                       q='overlaps CPU scheduling with GPU execution: while the GPU executes step *N*,', ref=A(ARCH, 177))],
                 ['`update_from_output` 处理的是**上上一轮**排的那一批（`batch_queue` 深度 2），不是这一轮刚排的。',
                  '网上很多讲解是旧版的「先 prefill 再 decode、step() 同步跑一步」，在这个版本的默认配置下不成立。']),
             to='sched'),

        part('gpu', 'GPU 执行', [1010, 364, 200, 140], m=[176, 1392, 150, 150], kind='act', n='7', code='GPUModelRunner',
             glyph=g_text('空闲', '等第一批'),
             say='前向 + 采样，结果异步拷回', why='CPU 不用等就能排下一步', trap='单卡没有 Worker 进程',
             card=card(
                 '真正算的地方：准备输入、跑模型前向、采样出下一个 token，再把 token id 从显存拷回内存。',
                 ['链条：`UniProcExecutor` → `Worker` → `GPUModelRunner`，单卡时都在引擎进程的主线程里，是**同线程直接调用**。' + R(UNI, 108, 117),
                  '分两次调用：`execute_model` 发射前向，`sample_tokens` 发射采样并在独立的 `copy_stream` 上排「GPU→CPU 拷贝」。两次都立刻返回一个 Future。' + R(AU, 147, 150),
                  '下一步的输入 token 直接从 GPU 上的 `last_sampled_tokens` 读，CPU 不需要先知道它。' + R(MR, 1614, 1619)],
                 [note('注释', '输入里有些 token 直接从上一步的采样结果读——这就是 CPU 不知道上一个 token 也能排下一步的原因。',
                       q='Some input token ids are directly read from the last sampled tokens', ref=A(MR, 1614)),
                  note('文档', 'Model Runner V2 假设核心执行循环是一条没有 CPU 同步点的 CUDA stream，CPU 只管往上排活。',
                       q='CPU entrypoints queue work onto the stream.', ref='docs/design/model_runner_v2.md:47')],
                 ['`non_block=True` 在单卡下**不是另一个线程在跑**：非阻塞来自 GPU 异步执行，不是 CPU 并发。',
                  '官方文档「每个 GPU 一个 worker 进程」只对多卡（TP/PP > 1）成立。']),
             to='gpu'),
    ]

    wires = [
        wire('w_post', 'client', 'http', 'POST 请求', ml='POST',
             card=card('客户端发起 HTTP 请求。', 'uvicorn 跑 FastAPI，路由到 `create_chat_completion`。' + R(ROUT, 43, 59),
                       [note('推断', '标准 HTTP + JSON，任何语言的客户端都能直接用。')])),
        wire('w_handler', 'http', 'chat', '调 handler', ml='调用',
             card=card('路由把解析好的请求交给协议层。', '普通的 `await handler.create_chat_completion(request, raw_request)`。' + R(ROUT, 67),
                       [note('推断', 'HTTP 层只管收发和断连，协议细节都在协议层，换一种 API（completions、responses）只换协议层。')])),
        wire('w_render', 'chat', 'render', 'await 渲染', ml='渲染',
             card=card('协议层把 messages 交给 Renderer，拿回 20 个 token id。',
                       '`await online_renderer.render_chat()`；Renderer 内部把阻塞活丢进线程池，协程挂起等 Future。' + R(SERV, 288) + R(BASE, 162, 164),
                       [note('推断', '事件循环在等分词时可以去处理别的请求的收发。')])),
        wire('w_gen', 'chat', 'asyncllm', '拿生成器', ml='生成器', mfs='l', mts='l', why='谁读结果，谁触发提交', trap='此刻什么都没发出',
             card=card('协议层调 `engine_client.generate(...)`，拿回一个**异步生成器**。',
                       '`generate` 是含 `yield` 的 `async def`：调用它只创建对象；第一次迭代才执行 `add_request`。' + R(ALLM, 787) + R(ALLM, 816),
                       [note('推断', '提交请求和读取结果绑在同一个生成器里：请求的生命周期就等于这个生成器的生命周期，生成器被取消 = 请求被 abort。')],
                       '箭头画的是「调用」，但这一刻请求**还没有**进引擎。'),
             to='chat_src'),
        wire('w_reg', 'asyncllm', 'outproc', '先登记', why='回包到了能找到人',
             card=card('在 OutputProcessor 里登记这个请求：建去分词器、记下信箱。',
                       '`output_processor.add_request(...)`，在 `await` 发送之前。' + R(ALLM, 594, 598),
                       [note('注释', '在第一个 await 之前登记，并发任务就能看到这个请求。',
                             q='Register locally before the first await so concurrent tasks see this request.', ref=A(ALLM, 594))]),
             to='gen_src'),
        wire('w_req', 'asyncllm', 'engine', 'ZMQ 请求管道', ml='请求管道', mfs='l', mts='l', why='跨进程，只等发完', trap='不等引擎处理',
             card=card('把 `EngineCoreRequest` 送进引擎进程。',
                       ['前端 **ROUTER** → 引擎 **DEALER**，多帧消息 `(引擎身份 b"\\x00\\x00", ADD, msgpack…)`。' + R(CCL, 1325, 1335),
                        'prompt token 换成 int32 数组，大数组走单独的帧，不内联进 msgpack。' + R(CCL, 86, 93)],
                       [note('注释', '小于 256 字节的数组内联，更大的走专门的帧——省一次拷贝。',
                             q='By default, arrays below 256B are serialized inline Larger will get sent via dedicated messages.', ref=A(SER, 141, 142)),
                        note('推断', '用 ROUTER 能按身份把消息发给指定引擎，为一个前端对多个引擎（DP>1）做准备；单引擎只是退化情形。')],
                       '`await add_request_async` 只等 ZMQ 发送完成，不等引擎处理（推断）。')),
        wire('w_sched', 'engine', 'sched', 'schedule / update', ml='排活', mlp=[62, 1343], why='同一线程，无竞态',
             card=card('忙循环调用调度器：`add_request` 收新请求，`schedule()` 排这一步，`update_from_output()` 收结果。',
                       '都是同线程的函数调用。' + R(CORE, 606) + R(CORE, 786) + R(CORE, 850, 852),
                       [note('推断', '调度器状态只被主线程改，输入线程只做反序列化和构造 `Request`，不用加锁。')])),
        wire('w_exec', 'engine', 'gpu', '排前向 + 采样', ml='排活', mlp=[262, 1343], why='发射完就回，不等', trap='不是另一个线程',
             card=card('把调度结果交给 GPU：先 `execute_model`，再 `sample_tokens`，都是 `non_block=True`。',
                       '两次调用都只是把内核排进 CUDA stream，CPU 立刻回来，拿到一个 Future。' + R(CORE, 788, 790) + R(CORE, 812, 814),
                       [note('文档', '配置说明：async scheduling 帮助避免 GPU 利用率出现空档。',
                             q='Async scheduling helps to avoid gaps in GPU utilization', ref=A(CFGS, 271, 272))],
                       '单卡下 `non_block` 只是包了一个 Future，计算是 GPU 异步在跑，不是 CPU 上另开线程。')),
        wire('w_back', 'gpu', 'engine', 'token id（晚一步）', ml='晚一步', cls='dash', mfs='t', mts='r', why='晚一步取，GPU 不空等', trap='取的是最早那批',
             card=card('采样出的 token id 拷回 CPU，交还给忙循环。',
                       ['忙循环在 `batch_queue` 满了之后 `pop()` 最早那批，`_wait_for_output` 等它的拷贝 event。' + R(CORE, 836, 839),
                        '拷贝在独立的 `copy_stream` 上，等待用可睡眠的 event，不忙轮询。' + R(AU, 139)],
                       [note('注释', '用会睡眠的 event 等，避免忙轮询加速器驱动锁。',
                             q='Blocking (sleep) event to avoid busy-polling the accelerator driver lock.', ref=A(AU, 139))],
                       '第 N 个 token 回到 CPU 时，第 N+1 步已经排进 GPU 了。')),
        wire('w_res', 'engine', 'outproc', 'ZMQ 结果管道', ml='结果管道', why='输出线程编码发送',
             card=card('把这一步所有请求的新 token 打包成 `EngineCoreOutputs`，送回 API 进程。',
                       ['主线程 `output_queue.put_nowait` → 输出线程 msgpack 编码（复用缓冲）→ 引擎 **PUSH** → 前端 **PULL**。' + R(CORE, 2340, 2371),
                        '前端的 `EngineCoreOutputQueueTask` 收帧解码，放进 `outputs_queue`。' + R(CCL, 1252, 1254)],
                       [note('注释', 'IO 放在独立 task 里，尽量并行。', q='Perform IO in separate task to parallelize as much as possible.', ref=A(CCL, 1233))],
                       '一条消息里是**一整批**请求的结果，不是一个请求一条。')),
        wire('w_mail', 'outproc', 'asyncllm', '放进信箱', ml='进信箱', mfs='r', mts='r', why='单槽合并，不堆积',
             card=card('把做好的 `RequestOutput` 放进这个请求的信箱，叫醒等着的 `generate()`。',
                       '`RequestOutputCollector.put()` 置位一个 `asyncio.Event`。' + R(OUTP, 864, 866) + R(OUTP, 70, 74),
                       [note('推断', '单槽 + 合并：慢客户端不会让输出无限堆积；消费者每次醒来拿到的是「到目前为止所有的增量」，减少任务切换。')],
                       '`process_outputs` 在这种模式下返回值里**没有** `request_outputs`，效果全在信箱里。' + R(ALLM, 925, 926)),
             to='gen_src'),
        wire('w_yield', 'asyncllm', 'chat', 'yield 输出', ml='yield', mfs='r', mts='r',
             card=card('`generate()` 从信箱取出一个 `RequestOutput`，`yield` 给协议层。', '`q.get_nowait() or await q.get()`，先不 await 地取。' + R(ALLM, 809),
                       [note('注释', '能不 await 就不 await：高负载下少一次任务切换。', q='drain queue without await if possible', ref=A(ALLM, 807))])),
        wire('w_sse', 'chat', 'http', 'data: … 块', ml='SSE 块', mfs='r', mts='r',
             card=card('协议层把 `RequestOutput` 拼成 `ChatCompletionStreamResponse`，序列化成一行 `data: {...}`。',
                       '第一块只带 `role`，之后每块带一截 `delta.content`。' + R(SERV, 600, 636) + R(SERV, 515),
                       [note('推断', '按 OpenAI 的流式格式发，客户端拿到的就是它熟悉的 chunk。')])),
        wire('w_stream', 'http', 'client', 'SSE 流', ml='SSE', mfs='r', mts='r',
             card=card('`StreamingResponse` 把这些字符串一块块写给客户端。', 'Starlette 迭代生成器；外面还包了一层 SSE keep-alive（`with_sse_keep_alive`）。' + R(ROUT, 82, 85),
                       [note('推断', '一个 token 一块，第一个字在 prefill + 第一步出来后就能到达客户端。')],
                       '最后一块带 `finish_reason`，然后是 `data: [DONE]`。' + R(SERV, 520))),
    ]

    steps = [
        beat('客户端发请求',
             '客户端 POST `/v1/chat/completions`：messages = 「法国的首都是哪里？」，`stream=true`，`max_tokens=8`。FastAPI 把 body 解析成 `ChatCompletionRequest`，进入路由；`with_cancellation` 同时起一个任务监听客户端断开。',
             note('注释', '断连检测用「两个任务赛跑」，因为 `request.is_disconnected` 在有中间件时不工作。',
                  q='This does _not_ use request.is_disconnected, which does not work with middleware.', ref=A(APIU, 72, 73)),
             focus=['http', 'w_post', 'client']),
        beat('套模板、分词',
             '协议层拒空 messages、查模型名，再交给 Renderer：套 chat template → 分词 → 得到 `{"type":"token","prompt_token_ids":[…]}`，本例 **20 个 token**（设的）。两步都在线程池里跑。',
             note('注释', '分词丢进线程池，事件循环继续处理其他请求的收发。', q='Offload tokenization to the thread pool.', ref=A(BASE, 158)),
             focus=['render', 'w_render', 'chat']),
        beat('拿到一个「还没开始」的生成器',
             '协议层定 request_id `chatcmpl-9f1c…`（没带 `X-Request-Id`，就随机 uuid），`max_tokens=8`，`output_kind=DELTA`；调 `generate(...)` 只拿回一个生成器——**请求还没发出去**。流式分支直接返回生成器，路由把它包成 `StreamingResponse`。Starlette 开始迭代它，链条才启动。',
             note('推断', '提交和读结果绑在一个生成器里：客户端开始收，引擎才开始算；生成器被取消，请求就被 abort。'),
             focus=['w_gen', 'chat', 'asyncllm'],
             state={'chat': g_kv([('ID', '…9f1c…'), ('max_tokens', '8')])}),
        beat('打包成 EngineCoreRequest，先登记',
             '`generate()` 第一次被迭代 → `add_request`：校验、克隆 `SamplingParams`、补 EOS，构造 `EngineCoreRequest`；内部 ID 变成 `chatcmpl-9f1c…-a1b2c3d4`。新建信箱，**先**在 OutputProcessor 登记，**再** `await` 发给引擎。',
             note('注释', '在第一个 await 之前登记，处理回包的任务一定找得到它。',
                  q='Register locally before the first await so concurrent tasks see this request.', ref=A(ALLM, 594)),
             focus=['asyncllm', 'w_reg', 'outproc'],
             state={'asyncllm': g_kv([('内部 ID', '…-a1b2c3d4'), ('信箱', '空，等着')]),
                    'outproc': g_kv([('登记', '…-a1b2c3d4'), ('文字', '—')])}),
        beat('过 ZMQ 进引擎进程',
             '20 个 token 换成 int32 数组，从 ROUTER 发出 `(b"\\x00\\x00", ADD, msgpack…)`。引擎的**输入线程**收到 → 解码 → 构造调度器内部的 `Request`（算前缀缓存的块哈希）→ 放进 `input_queue`。忙循环原本阻塞在 `input_queue.get()`，醒了，把请求交给调度器的 waiting 队列。',
             note('注释', '构造 Request 放在输入线程里做，可以和模型前向并行。',
                  q='This function could be directly used in input processing thread to allow', ref=A(CORE, 1274)),
             focus=['w_req', 'engine', 'asyncllm']),
        beat('第 1 轮：排 prefill，交给 GPU，不等',
             '调度器把请求移入 running、分配 KV block，这一步排 **20 个 token**（整段 prompt）；已算 → 20，并记 **1 个占位符**。`execute_model` + `sample_tokens` 都是非阻塞：内核排进 CUDA stream，CPU 立刻回来。第 1 批放进 `batch_queue`（1 < 2），**直接返回，不等结果**。',
             note('文档', 'GPU 跑第 N 步时，CPU 就去排第 N+1 步。',
                  q='overlaps CPU scheduling with GPU execution: while the GPU executes step *N*,', ref=A(ARCH, 177)),
             focus=['engine', 'w_sched', 'sched', 'w_exec', 'gpu'],
             state={'engine': bq(cell('第1步', '20 tok', 'new')),
                    'sched': sch('20', '20', '1'),
                    'gpu': g_text('第1步', '前向 20 tok', tone='run')}),
        beat('第 2 轮：先排下一步，再取第 1 步',
             '再排一次：待算 = 20 + 1（占位）− 20 = **1**，排 1 个 decode token，再加一个占位。CPU 还不知道第 1 个 token 是什么——GPU 直接从 `last_sampled_tokens` 读。第 2 批入队，队列满了（2 = 2），于是取出**最早**的第 1 批、等它拷回：`sampled_token_ids = [[<法国>]]`。',
             note('注释', '输入 token 直接从上一步的采样结果读，CPU 不必先知道它。',
                  q='Some input token ids are directly read from the last sampled tokens', ref=A(MR, 1614)),
             focus=['engine', 'w_back', 'gpu', 'sched'],
             state={'engine': bq(cell('第2步', '1 tok', 'new'), cell('第1步', '法国', 'pick')),
                    'sched': sch('1', '21', '2'),
                    'gpu': g_text('第2步', '在跑；第1步已拷回', tone='run')}),
        beat('追加 token、判停、送出',
             '先处理积压的 abort，再 `update_from_output`：「法国」追加到 `Request`，消掉一个占位，`check_stop` 查 EOS / 长度——没命中。产出 `EngineCoreOutput(new_token_ids=[<法国>], finish_reason=None)`，放进 `output_queue`；**输出线程**编码后从 PUSH 发出。',
             note('提交', '每步只提前处理 abort、不提前处理新请求：先处理模型输出，延迟更好。',
                  q='it\'s preferable for latency to process model outputs before new incoming requests.', ref='c:dc264bcea1'),
             focus=['w_res', 'sched', 'engine', 'outproc'],
             state={'sched': sch('1', '21', '1'),
                    'engine': bq(cell('第2步', '1 tok'))}),
        beat('回到 API 进程，变成文字',
             '收帧解码 → `output_handler` 交给 `process_outputs`：用内部 ID 找到这个请求 → 增量去分词得到 `"法国"`，顺便查 stop 串 → 做成 `RequestOutput`（DELTA：`text="法国"`）→ 放进信箱。信箱置位事件，`generate()` 醒来 `yield`。',
             note('注释', '整批输出只在这一个函数里循环一次。', q='This is the only function that should loop over EngineCoreOutputs.', ref=A(OUTP, 752, 753)),
             focus=['outproc', 'w_mail', 'asyncllm'],
             state={'outproc': g_kv([('token', '<法国>'), ('文字', '"法国"')]),
                    'asyncllm': g_kv([('内部 ID', '…-a1b2c3d4'), ('信箱', '"法国"')])}),
        beat('拼成 SSE 块，写回客户端',
             '流式生成器收到**第一个** `RequestOutput`：先发一块只带 `role` 的，再发 `delta.content = "法国"`。每块序列化成 `data: {...}`，经 `StreamingResponse` 写出。块里的 `id` 是 `chatcmpl-9f1c…`，不带内部后缀。',
             note('推断', '拿到一个增量就写一块：第一个字只要等 prefill 加一步，不用等整段。'),
             focus=['chat', 'w_yield', 'w_sse', 'http', 'w_stream', 'client'],
             state={'chat': g_kv([('块', 'data: 法国'), ('id', 'chatcmpl-9f1c…')]),
                    'client': g_text('法国', '第 1 块内容', tone='run')}),
        beat('一轮一轮重复',
             '第 7–10 步每轮重复，「的」「首都」「是」「巴黎」「。」依次回来。节奏始终是：第 N 个 token 回到 CPU 时，第 N+1 步已经在 GPU 上排好了，GPU 不用停下来等 CPU 调度。顶层的「第 1 轮」就是下一层[[sched|调度器]]里的 S1。',
             note('文档', 'async scheduling 帮助避免 GPU 利用率出现空档。', q='Async scheduling helps to avoid gaps in GPU utilization', ref=A(CFGS, 271, 272)),
             focus=['engine', 'gpu', 'sched', 'client'],
             state={'engine': bq(cell('第7步', '1 tok', 'new'), cell('第6步', '。', 'pick')),
                    'sched': sch('1', '26', '1'),
                    'gpu': g_text('第7步', '在跑', tone='run'),
                    'client': g_text('…巴黎。', '6 个 token 已到', tone='run')}),
        beat('第 7 个 token 是 <eos>，收尾',
             '`check_stop` 命中 EOS → `FINISHED_STOPPED`，`finish_reason="stop"`；调度器释放 KV block。但第 8 步早已排出去、正在 GPU 上跑，结果回来时请求已结束，被丢掉（见下一层调度器）。前端去分词时 eos 不进文字，`finished=True`；`generate()` 退出，最后一块带 `finish_reason`，然后 `data: [DONE]`。',
             note('推断', '判停和释放 KV block 都在调度器里：结束和回收在同一处发生。stop 串的结束则由前端判，再反向发 ABORT。'),
             focus=['sched', 'w_res', 'outproc', 'w_stream', 'client'],
             state={'sched': g_kv([('结束', 'stop'), ('KV', '已释放')]),
                    'engine': bq(cell('第8步', '白算', 'ghost')),
                    'gpu': g_text('第8步', '在跑，结果会被丢', tone='run'),
                    'outproc': g_kv([('finished', 'True'), ('状态', '已清理')]),
                    'client': g_text('[DONE]', '法国的首都是巴黎。', tone='ok')}),
    ]

    groups = [
        group('API Server 进程', [222, 6, 996, 328], m=[56, 166, 228, 922]),
        group('EngineCore 进程', [366, 340, 852, 174], m=[2, 1124, 336, 426]),
    ]

    base['blocks'] = [scene(1220, 518, parts, wires, steps, mw=340, mh=1556, groups=groups,
                            cap='单机单卡、默认配置（async scheduling 开）下，一个流式 chat 请求的来回')]
    base['children'] = [_chat_src(), _gen_src(), _loop_src(),
                         slot('sched', '调度器：token 预算、KV block、占位符'),
                         slot('gpu', 'GPU 执行：前向、采样、异步拷回'),
                         slot('outproc', '输出处理：去分词、stop 串、单槽信箱')]
    return base


# ---------------------------------------------------------------- 源码页
def _chat_src():
    return node(
        'chat_src', '协议层源码', '协议层：拿生成器，流式就直接返回',
        '顶层「OpenAI 协议层」框里：怎么拿到生成器、为什么路由返回时请求还没发出、SSE 块怎么拼。',
        lead='你在顶层的「OpenAI 协议层」框里。三段源码：调 `generate()`、按 stream 分支返回、外层把块写成 `data:` 行；最后一段是路由怎么把它包成流式响应。',
        src=SERV + ':411-520',
        blocks=[
            code(SERV, [(411, 453)], notes=[
                at((411, 423), None, '调 `generate()` 只是造了一个异步生成器，这里什么都没发出去。', has='self.engine_client.generate('),
                at(427, '推断', 'chat 路径只允许一个引擎输入；多 prompt 的后缀逻辑在这里实际不会触发。', has='assert len(generators) == 1'),
                at((430, 441), '推断', '流式分支直接 `return` 生成器、不 await：请求要等 Starlette 开始迭代它才真正提交。', has='if request.stream:'),
                at((443, 453), None, '非流式：在这里就 await，把整段结果攒完再返回。', has='chat_completion_full_generator'),
            ], cap='create_chat_completion 的结尾'),
            code(SERV, [(509, 520)], notes=[
                at(515, None, '每个 chunk 变成一行 `data: {...}` 加空行，这就是 SSE。', has='yield f"data: {data}'),
                at(520, None, '流结束（或出错之后）补一行 `data: [DONE]`。', has='[DONE]'),
            ], cap='流式生成器的外层：序列化成 SSE'),
            code(ROUT, [(63, 85)], notes=[
                at(67, None, 'await 的是协议层；流式时拿回的是生成器，不是结果。', has='await handler.create_chat_completion'),
                at((82, 85), None, '生成器包成 `text/event-stream` 响应；外加 SSE keep-alive。', has='StreamingResponse('),
            ], cap='路由：按返回值类型决定怎么回'),
        ])


def _gen_src():
    return node(
        'gen_src', '引擎门面源码', '引擎门面：先登记、再发送、守着信箱',
        '顶层「引擎门面」框里：`add_request` 的最后几步和 `generate()` 的取值循环。',
        lead='你在顶层的「引擎门面」（`AsyncLLM`）框里。两段源码：请求怎么登记并发出去；`generate()` 怎么守着信箱一个个 yield。',
        src=ALLM + ':535-868',
        blocks=[
            code(ALLM, [(535, 550), (589, 598)], notes=[
                at(535, None, '把外部 ID 改成「外部 ID + 8 位随机后缀」，本例 `chatcmpl-9f1c…-a1b2c3d4`。', has='assign_request_id'),
                at((537, 540), '注释', '常驻的 output_handler 在第一次 add_request 时才启动，这样构造可以在事件循环之前完成，启动失败也好处理。',
                   q='We start the output_handler on the first call to add_request() so', has='_run_output_handler'),
                at(543, None, '这个请求的信箱：单槽，生产者跑太快就合并。', has='RequestOutputCollector'),
                at((594, 598), '注释', '先在 OutputProcessor 登记，再 await 发给引擎；回包到达时一定查得到。',
                   q='Register locally before the first await so concurrent tasks see this request.', ref=A(ALLM, 594), has='add_request_async'),
            ], cap='add_request：打包、登记、发出'),
            code(ALLM, [(785, 826)], notes=[
                at((787, 801), None, '生成器第一次被迭代时才走到这里——这才是请求真正提交的时刻。', has='q = await self.add_request('),
                at(809, '注释', '能不 await 就不 await：高负载下少一次任务切换。', q='drain queue without await if possible', ref=A(ALLM, 807), has='q.get_nowait() or await q.get()'),
                at((814, 816), None, '`finished` 为真就退出循环；每拿到一个就 yield 给协议层。', has='yield out'),
                at((821, 826), None, '客户端断开 → 生成器被取消 → 在这里 abort 引擎里的请求。', has='asyncio.CancelledError'),
            ], cap='generate()：提交一次，然后守着信箱'),
        ])


def _loop_src():
    return node(
        'loop_src', '忙循环源码', '引擎忙循环：收请求、排一批、满了才取结果',
        '顶层「引擎忙循环」框里：外层循环、怎么收请求、`step_with_batch_queue` 怎么做到「先排后取」。',
        lead='你在顶层的「引擎忙循环」（`EngineCoreProc`）框里，EngineCore 进程的主线程。三段源码按执行顺序：外层循环 → 收请求 → 一步。',
        src=CORE + ':775-1873',
        blocks=[
            code(CORE, [(1814, 1823)], notes=[
                at(1818, None, '① 收请求：没活就阻塞，有活就把队列清空。', has='self._process_input_queue()'),
                at(1822, None, '② 跑一步：默认就是下面的 `step_with_batch_queue`。', has='self._process_engine_step()'),
            ], cap='run_busy_loop：永不停的两件事'),
            code(CORE, [(1846, 1873)], notes=[
                at(1847, None, '只有「没活」时才进这个等待循环；本例请求到达前就停在这里。', has='while not self.has_work()'),
                at(1860, None, '阻塞在 input_queue 上，输入线程一放进请求就醒。', has='self.input_queue.get(block=block)'),
                at((1871, 1873), None, '有活时不阻塞：只把已经到了的消息处理完就去跑 step。', has='while not self.input_queue.empty()'),
            ], cap='_process_input_queue：空闲时等，忙时只清空'),
            code(CORE, [(783, 852)], notes=[
                at(786, None, '排这一步：本例第 1 轮排 20 个 token，之后每轮 1 个。', has='self.scheduler.schedule('),
                at((788, 790), None, '发射前向，不等，拿回 Future。', has='execute_model('),
                at((812, 814), None, '没有结构化输出就立刻发射采样，同样不等。', has='sample_tokens('),
                at((822, 828), '注释', '队列没满就直接返回、不等结果：先把下一批排上。本例第 1 轮在这里返回。',
                   q='Don\'t block on next worker response unless the queue is full', has='batch_queue.appendleft'),
                at((837, 839), None, '队列满了：取出最早那批（第 1 轮排的），等它的 token 拷回 CPU。', has='batch_queue.pop()'),
                at((849, 852), None, '先处理 abort，再把结果交给调度器：追加 token、判停、产出 EngineCoreOutput。', has='update_from_output'),
            ], cap='step_with_batch_queue：先排后取'),
        ])
