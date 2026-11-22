# 一条请求在 SGLang 里怎么走完：顶层底稿

> 研究对象：sglang 仓库 HEAD = `c569c43446`。
> 所有锚点都是相对仓库根目录的路径 `文件:行号`（或 `行号-行号`）。只写 `:行号` 的简写，指的是正文里它前面最近出现的那个完整路径。
> 每条「为什么」后面标来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 例子里凡是「设的」数，都不是代码里的常量，是为了讲解而假设的。

---

## 0. 先给结论（一屏版）

一条 OpenAI 风格的聊天请求，在 SGLang 默认配置下要穿过 **3 个进程**、**3 条 ZMQ 单向管道**，最后原路回到同一个 HTTP 连接：

```
客户端
  │ HTTP POST /v1/chat/completions
  ▼
┌──────────────── 主进程（一个 asyncio/uvloop 事件循环）────────────────┐
│ FastAPI 路由 → OpenAIServingChat（套聊天模板 + 分词，丢到线程池做）       │
│        → TokenizerManager.generate_request()  ──┐                        │
│        ← 等 ReqState.event 被唤醒、取 out_list  ←─┼── handle_loop 协程     │
└───────────────────────────────────────────────────┼──────────────────────┘
        ZMQ PUSH ①                                  │ ZMQ PULL ③
        TokenizedGenerateReqInput                   │ BatchStrOutput（增量文本）
        ▼                                           │
┌──── Scheduler 子进程（每张 GPU 一个；主线程死循环）──┐   ┌── Detokenizer 子进程 ──┐
│ 收请求 → 等待队列 → 组 batch → 前向+采样 → 处理结果 │──►│ token id → 增量文本      │
│ 拥有：GPU 模型、KV cache 池、前缀树(radix cache)    │ ② │ 拥有：每个请求的解码状态 │
└──────────────────────────────────────────────────────┘   └──────────────────────────┘
                          ZMQ PUSH ② BatchTokenIDOutput（新 token id）
```

- **主进程**：HTTP 服务、OpenAI 协议适配、TokenizerManager 都在这里，共用一个事件循环。【注释】`python/sglang/srt/entrypoints/http_server.py:3310` 原文："The HTTP server, Engine, and TokenizerManager all run in the main process."
- **Scheduler 子进程**：真正的大脑兼苦力——排队、组批、管显存、调模型。
- **Detokenizer 子进程**：只做一件事，把 token id 变回文字（而且是「增量地」变）。
- 进程间全是 ZMQ。【注释】`python/sglang/srt/entrypoints/http_server.py:3311` 原文："Inter-process communication is done through IPC (each process uses a different port) via the ZMQ library."

数据形态一路在变：
`ChatCompletionRequest`（JSON）→ `GenerateReqInput`（主进程内）→ `TokenizedGenerateReqInput`（跨进程）→ `Req`（Scheduler 里一个请求的全部状态）→ `ScheduleBatch`（一批请求，CPU 侧调度数据）→ `ForwardBatch`（GPU 张量）→ logits → `next_token_ids` → `BatchTokenIDOutput`（跨进程）→ `BatchStrOutput`（跨进程）→ `out_dict`（主进程内）→ SSE 文本块。

最容易困惑的三件事，先打个预防针：
1. **一条请求在 Scheduler 里不是「跑一次」，而是被反复调度**：第一次跑 prefill（把整段提示算一遍，吐出第 1 个 token），之后每轮 decode 只吐 1 个 token，每轮都和别的请求拼在一起跑（连续批处理）。
2. **默认开着 overlap 调度**：Scheduler 处理第 N 轮结果的时候，第 N+1 轮已经在 GPU 上跑了。所以代码里「结果处理」总比「发射」晚一拍。
3. **文本是 delta → 累积 → 再切成 delta**：Detokenizer 发的是增量文字，TokenizerManager 默认把它拼回全文，OpenAI 适配层再按偏移切出增量。看起来绕，是两套对外契约叠在一起的结果（见 §6.3）。

---

## 1. 选定「主路」，以及为什么是它

本稿只把下面这条路讲透：

| 维度 | 主路取值 | 依据 |
|---|---|---|
| 启动方式 | `sglang serve` / `python -m sglang.launch_server`，默认 HTTP 模式 | `python/sglang/launch_server.py:47-50`：不是 encoder-only、不是 Ray 时走 `http_server.launch_server` |
| 入口 | `POST /v1/chat/completions`，`stream=true` | 最常见的线上用法；`/generate` 只在 §9 点名 |
| 并行 | 单卡（tp=1, dp=1, pp=1） | 多卡只改「Scheduler 怎么收请求、怎么同步」，主干不变 |
| Rust 前端 | 关闭 | `SGLANG_RUST_SERVER` 默认 `False`：`python/sglang/srt/environ.py:2656` |
| Tokenizer / Detokenizer 个数 | 各 1 个 | `tokenizer_worker_num`、`detokenizer_worker_num` 默认 1：`python/sglang/srt/arg_groups/fields/serving.py:50-57` |
| overlap 调度 | 开 | `disable_overlap_schedule` 默认 `False`：`python/sglang/srt/arg_groups/fields/schedule.py:212-218`；`python/sglang/srt/managers/scheduler.py:573-575` |
| 前缀缓存 | 开（radix cache） | `disable_radix_cache` 默认 `False`：`python/sglang/srt/arg_groups/fields/memory.py:75-81` |
| 调度策略 | `fcfs`（先来先服务） | `python/sglang/srt/arg_groups/fields/schedule.py:92-109` |
| 流式粒度 | 每 1 个 token 发一次 | `stream_interval` 默认 1：`python/sglang/srt/arg_groups/fields/serving.py:257-260` |
| 推测解码、结构化输出、多模态、PD 分离、LoRA | 都不开 | 各自是旁路，§9 点名 |

为什么选 chat + stream：它同时用到了「聊天模板」「流式回传」「增量反分词」这三套机制，非流式只是它的子集（区别在 §6.4 一句话讲清）。

---

## 2. 启动时：这些进程和管道是怎么搭起来的

读者第一个问题通常是：「一个请求进来之前，系统长什么样？」

### 2.1 谁起了谁

`http_server.launch_server` 调 `Engine._launch_subprocesses`：`python/sglang/srt/entrypoints/http_server.py:3323`。后者依次：

1. 分配 IPC 地址：`PortArgs.init_new(server_args)`：`python/sglang/srt/entrypoints/engine.py:1248`。`PortArgs` 里最关键的三个名字就是三条管道的地址：`tokenizer_ipc_name`（往主进程送结果）、`scheduler_input_ipc_name`（往 Scheduler 送请求）、`detokenizer_ipc_name`（往 Detokenizer 送 token）：`python/sglang/srt/server_args.py:826-832`。
2. 起 Scheduler 子进程，每个 (pp_rank, tp_rank) 一个 `mp.Process`：`python/sglang/srt/entrypoints/engine.py:965`。
3. 起 Detokenizer 子进程：`python/sglang/srt/entrypoints/engine.py:1398`。
4. 在**主进程里**构造 TokenizerManager：`python/sglang/srt/entrypoints/engine.py:1408`。
5. 等所有 Scheduler 把模型加载完：`python/sglang/srt/entrypoints/engine.py:1419`；然后把 Scheduler 报回来的 `max_req_input_len` 抄给 TokenizerManager：`python/sglang/srt/entrypoints/engine.py:1425-1428`。
6. 起一个看门狗盯所有子进程：`python/sglang/srt/entrypoints/engine.py:1436`。
7. 最后主进程跑 uvicorn（`loop="uvloop"`）：`python/sglang/srt/entrypoints/http_server.py:3155`。

Scheduler 子进程的入口很短：构造 `Scheduler` → 通过 pipe 告诉父进程「我好了」 → 进入事件循环：`python/sglang/srt/managers/scheduler.py:7199`、`:7203`、`:7206`。
Detokenizer 子进程同理，把进程名设成 `sglang::detokenizer` 后进 `event_loop()`：`python/sglang/srt/managers/detokenizer_manager.py:493`、`:503`。

### 2.2 三条管道（读者要画成线的东西）

| 管道 | 发送方（socket 类型） | 接收方（socket 类型） | 地址 | 锚点 |
|---|---|---|---|---|
| ① 请求下行 | TokenizerManager `PUSH` | Scheduler(rank0) `PULL` | `scheduler_input_ipc_name` | 发：`python/sglang/srt/managers/tokenizer_manager.py:834-836`；收：`python/sglang/srt/managers/scheduler_components/ipc_channels.py:36-38` |
| ② token 上行 | Scheduler `PUSH` | Detokenizer `PULL` | `detokenizer_ipc_name` | 发：`python/sglang/srt/managers/scheduler_components/ipc_channels.py:59-61`；收：`python/sglang/srt/managers/detokenizer_manager.py:102-104` |
| ③ 文本上行 | Detokenizer `PUSH` | TokenizerManager `PULL` | `tokenizer_ipc_name` | 发：`python/sglang/srt/managers/detokenizer_manager.py:109-111`；收：`python/sglang/srt/managers/tokenizer_manager.py:831-833` |

- 都是**单向** PUSH/PULL，没有「请求-应答」。所以回程靠的是**请求 id（rid）**对号入座，不是靠连接。
- 序列化：默认 pickle（`SGLANG_USE_PICKLE_IPC` 默认 `True`：`python/sglang/srt/environ.py:390`），否则用 msgpack：`python/sglang/srt/managers/io_struct.py:3012-3017`。
- 注意 Scheduler 也有一个直连主进程的 `send_to_tokenizer`（`python/sglang/srt/managers/scheduler_components/ipc_channels.py:48-50`），用于控制类消息的回复，以及 abort 等「不走输出流」的结束通知（`send_abort_output`：`python/sglang/srt/managers/scheduler.py:2428-2430`，实际发送在 `:2437`）。生成结果的主路不走它。

**为什么拆成三个进程？**
- Scheduler 单独成进程：Python 有 GIL，若 HTTP 处理、分词、反分词和 GPU 调度挤在一个进程里，CPU 活会直接拖慢 GPU 发射节奏。拆开后 Scheduler 的循环只管调度和 GPU。【推断】依据：`launch_server` 的文档字符串把三者明确分成主进程/子进程（`python/sglang/srt/entrypoints/http_server.py:3302-3311`），且 Scheduler 收请求用非阻塞轮询（§4.2），说明它的循环不允许被 I/O 卡住。没找到提交说明直接讲这个动机。
- Detokenizer 单独成进程：反分词是纯 CPU 活，量随「并发请求数 × 每秒 token 数」增长；放在主进程会和 HTTP 事件循环抢 CPU，放在 Scheduler 会挤占调度。【推断】同上，没找到原始动机的提交说明。旁证：仓库后来还加了 `detokenizer_worker_num > 1` 时在前面挂一个路由进程扇出的能力（`python/sglang/srt/entrypoints/engine.py:1094-1150`），说明反分词确实会成为需要横向扩的瓶颈。

---

## 3. 主进程：从 HTTP 到「把请求扔给 Scheduler」

### 3.1 路由层：几乎什么都不做

`/v1/chat/completions` 的路由函数只是把请求交给 `openai_serving_chat.handle_request`：`python/sglang/srt/entrypoints/http_server.py:1901-1910`。这个 `openai_serving_chat` 对象是在 FastAPI 的 lifespan 里、用 TokenizerManager 构造出来的：`python/sglang/srt/entrypoints/http_server.py:352-356`。

### 3.2 OpenAIServingChat：把聊天消息变成 `GenerateReqInput`

`handle_request`（`python/sglang/srt/entrypoints/openai/serving_base.py:80`）做三件事：校验 → 转换 → 按 `stream` 分流（`:111-118`）。

转换是**放到线程池里做的**：`await loop.run_in_executor(None, self._convert_to_internal_request, ...)`：`python/sglang/srt/entrypoints/openai/serving_base.py:101-104`。
- 为什么：【注释】`python/sglang/srt/entrypoints/openai/serving_base.py:99-100` 原文："Convert off-loop: jinja render + full-prompt tokenization blocks the loop on long prompts, and the rust tokenizer releases the GIL." 也就是说：主进程只有一个事件循环，它同时在给所有流式连接吐数据；一条长 prompt 的模板渲染 + 分词如果在循环里做，所有人的流都会卡一下。

`_convert_to_internal_request`（`python/sglang/srt/entrypoints/openai/serving_chat.py:1210`）里：
- `_process_messages` 套聊天模板（jinja）并分词：`python/sglang/srt/entrypoints/openai/serving_chat.py:1264`。
- 对纯文本模型，`_engine_prompt` 最后返回的是 `("input_ids", prompt_ids)`：`python/sglang/srt/entrypoints/openai/serving_chat.py:1184`。也就是说**在 chat 主路上，分词已经在这里做完了**。
- 组装 `GenerateReqInput(...)`：`python/sglang/srt/entrypoints/openai/serving_chat.py:1303`，带上 `sampling_params`、`stream`、`rid=request.rid`（客户端没传就是 None）等。

> 读者易困惑点（名不副实）：名字叫 TokenizerManager，但在 chat 主路上它**通常不分词**——拿到的已经是 `input_ids`，`_tokenize_one_request` 直接走 `elif obj.input_ids is not None: input_ids = obj.input_ids` 分支：`python/sglang/srt/managers/tokenizer_manager.py:1291-1292`。真正调分词器的是 `/generate` 传 `text` 时（`:1319-1320`）。

### 3.3 流式分支：先「踢一脚」生成器，再回 HTTP 200

`_handle_streaming_request`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2369`）不是直接把生成器交给 `StreamingResponse`，而是先 `await generator.__anext__()` 拿到第一个块：`python/sglang/srt/entrypoints/openai/serving_chat.py:2381-2385`。
- 为什么：【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:2381` 原文："Kick-start the generator to trigger validation before HTTP 200 is sent." 一旦 200 头发出去，就只能把错误塞进 SSE 正文里了；先跑到第一个块，像「超长上下文」这类校验错误还能变成正经的 HTTP 400。【提交】`269589ad71` 标题："Return HTTP 400 for streaming validation errors (#21900)"。
- 同时挂了一个后台任务 `create_abort_task`：`python/sglang/srt/entrypoints/openai/serving_chat.py:2402`（§7 讲它的用途）。

### 3.4 TokenizerManager.generate_request：登记、打包、发出、等待

`generate_request`：`python/sglang/srt/managers/tokenizer_manager.py:1061`。按顺序：

1. `auto_create_handle_loop()`：第一次调用时，在当前事件循环上起 `handle_loop` 协程（接收管道 ③ 的那个循环）：`python/sglang/srt/managers/tokenizer_manager.py:1066`、`:2891-2900`。
   - 读者易困惑点（效果在别处）：结果的**接收**不在 `generate_request` 里，而在这个常驻协程里；两者靠 `ReqState` 交接。
2. `obj.normalize_batch_and_arguments()`：`:1069`。其中单请求没 rid 时生成 `uuid.uuid4().hex`：`python/sglang/srt/managers/io_struct.py:645-646`。
3. `_init_req_state`：为这个 rid 建一个 `ReqState(out_list=[], finished=False, event=asyncio.Event(), obj, time_stats)` 放进 `self.rid_to_state`：`python/sglang/srt/managers/tokenizer_manager.py:1092`、`:4281-4287`。重复 rid 直接报错：`:4274-4275`。
4. 过一道「暂停闸门」（`is_pause_cond`），并在模型权重更新锁的读锁下继续：`:1106-1111`。
5. `_tokenize_one_request` → `_create_tokenized_object`：解析/校验 `SamplingParams`（`:1846-1847`），构造 `TokenizedGenerateReqInput`（代码里通过 `self.tokenized_generate_req_type(...)` 构造：`:1870`），其中 `input_ids` 被转成紧凑的 `array("q", ...)`：`:1824`。
6. `_send_one_request` → `_dispatch_to_scheduler` → `sock_send(self.send_to_scheduler, obj)`：`python/sglang/srt/managers/tokenizer_manager.py:2100`、`:851-854`。发送是同步的 ZMQ send，不等 Scheduler 回应。
7. 发出后立刻丢掉 `obj.input_ids`（`:1121-1123`）——prompt 已经交给 Scheduler，主进程不再需要这份可能很大的列表。【推断】依据是紧随其后的 `state.prompt_released = True` 以及 `ReqState` 上的注释 "obj.input_ids was dropped after dispatch, so the request cannot be dumped."（`:296`）。
8. `async for response in self._wait_one_response(obj, request): yield response`：`:1124-1125`——开始等。

`ReqState` 的定义：`python/sglang/srt/managers/tokenizer_manager.py:279-321`。它就是「发请求的协程」和「收结果的协程」之间的信箱：`out_list` 放结果、`event` 用来叫醒、`finished` 标记结束、`text_chunks` 懒拼接文本。

**为什么用「rid → ReqState + asyncio.Event」而不是每个请求一个 future？**
因为一个请求会收到很多次结果（每个 token 一次），而且结果是成批（一个 `BatchStrOutput` 里有很多 rid）从同一根管道进来的；需要一个可以反复「放入-唤醒-取走」的信箱，Event + list 正好。【推断】依据：`_handle_batch_output` 按 `recv_obj.rids` 逐个查 `rid_to_state` 并 `out_list.append` + `event.set`（`python/sglang/srt/managers/tokenizer_manager.py:2945-2955`、`:3232-3240`），`_stream_one_response` 每次醒来"Drain all pending outputs atomically"（`:2366-2370`）。

---

## 4. Scheduler：一条请求在这里被反复调度

Scheduler 是最大的一块（`scheduler.py` 7000+ 行）。顶层只需要抓住：**一个死循环，每一圈做「收 → 选批 → 发射 → 处理上一批结果」**。

### 4.1 事件循环本体（overlap 版）

选哪个循环：`_dispatch_event_loop_once`，非 PD、pp=1、非 MLX、`enable_overlap` 为真时走 `event_loop_overlap`：`python/sglang/srt/managers/scheduler.py:7050-7062`。

`event_loop_overlap`：`python/sglang/srt/managers/scheduler.py:2235-2305`，每一圈：

1. `ingest_requests()`：收新请求：`:2253`。
2. （少数情况）`should_process_last_batch_early` 为真时先处理上一批：`:2258-2262`。
3. `get_next_batch_to_run(...)`：决定这一圈跑什么：`:2272-2276`。
4. `run_batch(batch)`：把这一批**发射**到 GPU（异步，不等算完）：`:2281`；结果对象塞进 `result_queue`：`:2284`。
5. 处理**上一批**的结果：`pop_and_process()` → `process_batch_result`：`:2290-2292`、`:2242-2245`。
6. `launch_batch_sample_if_needed`：有些情况下采样被推迟到这里做（结构化输出等，主路默认不走）：`:2299-2300`。
7. `self.last_batch = batch`：`:2303`。

**为什么要 overlap？**
- 【文档】`docs/docs/advanced_features/unified_memory_design.mdx:121` 原文："Under the overlap scheduler, forward N+1 is launched before forward N's result is processed."
- 【注释】`python/sglang/srt/managers/scheduler.py:5881-5882` 原文："in overlap mode this CPU work runs while the next batch's GPU forward is in flight, giving free overlap."
- 【文档】配置说明：`python/sglang/srt/arg_groups/fields/schedule.py:215` 原文："Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker."
- 人话：decode 一步 GPU 只要几毫秒，而 CPU 侧「收请求、拼 batch、处理结果、发输出」也要毫秒级。串行做，GPU 每步都要空等 CPU；重叠做，CPU 活被藏到 GPU 计算的阴影里。
- 历史：overlap 事件循环最早由 【提交】`9116b2896f` "Add a new event loop (#1677)"（2024-10）引入；提交说明只有标题。

**overlap 带来的连锁反应（读者最容易卡住的地方）：**
- 发射第 N+1 批时，第 N 批采出来的 token 还在 GPU 上、CPU 还不知道。那下一步 decode 的 `input_ids` 从哪来？答案：不经 CPU，直接在 GPU 上「转交」。【注释】`python/sglang/srt/managers/scheduler.py:5510` 原文："Next-iter input_ids relayed via future_map."；`FutureMap` 的文档字符串：`python/sglang/srt/managers/overlap_utils.py:309-312` 原文："Always-on pool-indexed relay for cross-iter values."；`prepare_for_decode` 也注明 decode 的 input_ids 是"placeholder for overlap"（`python/sglang/srt/managers/schedule_batch.py:3338-3339`）。
- 一个请求在第 N 批里刚采到 EOS，但 CPU 处理第 N 批结果前，第 N+1 批已经带着它发射了——多算了一步。代码分两种情况处理：
  - 能提前知道的（按 `max_new_tokens` 长度结束），发射下一步前就剔掉，不白算：`_reqs_finishing_inflight`（`python/sglang/srt/managers/scheduler.py:5182-5192`，文档字符串原文 "Requests whose step in flight commits their last output by length."；调用处 `:5198`）。
  - EOS 这种没法预知的，就白算一步，下一圈 `running_batch.filter_batch()` 把已结束的请求剔掉（`python/sglang/srt/managers/scheduler.py:4413-4416`），多分配的 KV 槽在释放时一并回收（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1215-1216` 的注释："Left the batch after launch; its over-allocated tokens are freed in `release_kv_cache`."）。
- 结果入队时存的是 `batch.copy()`（`python/sglang/srt/managers/scheduler.py:2284`），因为原 batch 下一圈会被就地修改；`copy()` 只拷「process_batch_result 会用到的字段」：【注释】`python/sglang/srt/managers/schedule_batch.py:3566` 原文："Only contain fields that will be used by process_batch_result."

关闭 overlap（`--disable-overlap-schedule`）时走 `event_loop_normal`：`python/sglang/srt/managers/scheduler.py:2200-2233`，就是朴素的「收 → 选批 → 跑 → 立即处理结果」。读者可以把它当作 overlap 版的「教学版」对照着看。

### 4.2 收请求：非阻塞地把管道抽干

`ingest_requests` → `request_receiver.recv_requests`：`python/sglang/srt/managers/scheduler.py:2364`。
核心是一个 `while True` 循环反复 `sock_recv(self.recv_from_tokenizer, zmq.NOBLOCK)`，直到抛 `zmq.ZMQError`（没货了）或达到单次上限：`python/sglang/srt/managers/scheduler_components/request_receiver.py:152-160`。

- **为什么非阻塞？** Scheduler 的主循环同时要驱动 GPU；如果在 recv 上阻塞，正在 decode 的请求就停摆了。【推断】依据是循环结构本身（每圈都要往下走到 `run_batch`）。
- 多卡时只有 attn_tp_rank 0 去读 socket，其余 rank 通过 `broadcast_pyobj` 拿到同一份：`python/sglang/srt/managers/scheduler_components/request_receiver.py:140`、`:229-234`。【注释】`:104` 原文："Receive results at tp_rank = 0 and broadcast it to all other TP ranks." 背后的不变量：所有 TP rank 必须做出**完全一样**的调度决定，否则集合通信会死锁或算错。【文档】`python/sglang/srt/mem_cache/README.md:66-67` 讲分配器时也强调同一原则："Allocator decisions read logical state only"……"never whether an event fired, so TP ranks agree."

然后 `process_input_requests` 按类型分发：`self._request_dispatcher(recv_req)`：`python/sglang/srt/managers/scheduler.py:2405`。映射表里 `TokenizedGenerateReqInput → handle_generate_request`：`python/sglang/srt/managers/scheduler.py:1983`；`AbortReq → abort_request`：`:1991`。

### 4.3 `handle_generate_request`：造 `Req`，校验，入队

`_handle_generate_request`：`python/sglang/srt/managers/scheduler.py:3373`。
- 构造 `Req`：`python/sglang/srt/managers/scheduler.py:3399-3404`（`self._create_generation_req(recv_req, recv_req.rid, recv_req.input_text, recv_req.input_ids, recv_req.sampling_params, ...)`）。`Req` 类：`python/sglang/srt/managers/schedule_batch.py:440`；它持有 `origin_input_ids`（`:501`）、`output_ids`（`:511`）、KV 记账 `self.kv = ReqKvInfo()`（`:535`）等。
- 一串校验（输入长度、logprob 参数等）。**有意思的是：校验失败不是直接回错，而是 `req.set_finish_with_abort(...)` 后照样 `_add_request_to_queue(req)`**，例如 `python/sglang/srt/managers/scheduler.py:3683-3686`。
  - 为什么：让错误也走和正常结果同一条输出通道回到 TokenizerManager，保证「每个被 Scheduler 收下的请求都恰好产出一次输出」。【注释】`python/sglang/srt/managers/scheduler.py:3922-3923` 原文："Every request the scheduler keeps owes its output from here; one rejected at intake without being queued sends it in the call that built it."
- 最后 `_add_request_to_queue` → `move([req], self.waiting_queue)`：`python/sglang/srt/managers/scheduler.py:3936`。等待队列本身：`python/sglang/srt/managers/scheduler.py:1406`。

### 4.4 选批：`get_next_batch_to_run`

`get_next_batch_to_run`：`python/sglang/srt/managers/scheduler.py:4339`。顶层逻辑只有三步：

1. **把上一圈的 prefill 批并进 running batch**：如果上一批是 extend（prefill），过滤掉已结束的请求后 `running_batch.merge_batch(last_batch)`：`python/sglang/srt/managers/scheduler.py:4378-4411`。running batch 就是「正在 decode 的那一大批」：`python/sglang/srt/managers/scheduler.py:1410`。
   - 易困惑点（时间上不连续）：在 overlap 下，prefill 批在它的结果**还没被处理**时就已经并进 running batch 了，所以每一圈都要再过滤一次已结束的请求。【注释】`python/sglang/srt/managers/scheduler.py:4413-4414` 原文："Every step, not only after a merge: overlap merges a prefill's requests before its result finishes them, and update_running_batch may never run."
2. **试着从等待队列拼一个新的 prefill 批**：`get_new_batch_prefill`：`:4426`。
3. **有新 prefill 就先跑 prefill，否则跑 decode**：`:4443-4456`。注释原文："Run prefill first if possible"（`:4444`）。

**为什么 prefill 优先？**【推断】新请求的首 token 延迟（TTFT）对用户最敏感；而 decode 批晚一步只是让所有人的下一个 token 慢几毫秒。代价是 prefill 期间 decode 停一拍——这正是 chunked prefill（把长 prompt 切块，每块最多 `chunked_prefill_size` 个 token，H100/A100-80G 级显卡默认 8192：`python/sglang/srt/arg_groups/memory_hook.py:139-143`）要缓解的：一个超长 prompt 不会一次霸占 GPU 太久。没找到提交说明直接写「prefill 优先」的理由。

拼 prefill 批的细节在 `_get_new_batch_prefill_raw`（`python/sglang/srt/managers/scheduler.py:4586`），顶层只需知道：
- 按策略给等待队列排序：`self.policy.calc_priority(...)`：`:4651-4653`（主路 fcfs）。
- 对每个候选请求先 `req.init_next_round_input(self.tree_cache, ...)` 做**前缀匹配**：`:4845`；再 `adder.add_one_req(...)` 问「显存/token 预算还够不够」：`:4867`。`PrefillAdder` 类：`python/sglang/srt/managers/schedule_policy.py:1009`；返回值 `AddReqResult` 有 `CONTINUE / NO_TOKEN / OTHER / UNSCHEDULABLE`：`python/sglang/srt/managers/schedule_policy.py:944-950`。
- 用选中的请求 `ScheduleBatch.init_new(...)`：`python/sglang/srt/managers/scheduler.py:4955`，然后 `new_batch.prepare_for_extend()`：`:4978`。

decode 那一路：`update_running_batch`：`python/sglang/srt/managers/scheduler.py:5194`。它先检查显存够不够再走一步（`batch.check_decode_mem()`：`:5207`），不够就**回退（retract）**一部分请求放回等待队列（`:5216-5266`，日志 "KV cache pool is full. Retract requests."），够的话 `batch.prepare_for_decode()`（`:5282`）。

### 4.5 显存：三层存储单元

读者问「KV cache 存在哪、怎么找到」，这三样是答案：

| 存储单元 | 是什么 | 锚点 |
|---|---|---|
| `req_to_token_pool`（`ReqToTokenPool`） | 一张表：第 i 行 = 某个请求；第 j 列 = 它第 j 个 token 的 KV 存在哪个槽 | 类：`python/sglang/srt/mem_cache/memory_pool.py:382-383`（"A memory pool that maps a request to its token locations."） |
| `token_to_kv_pool_allocator` | 管 KV 槽位的空闲表：「给我 N 个槽」 | Scheduler 持有：`python/sglang/srt/managers/scheduler.py:678`；README 分层表：`python/sglang/srt/mem_cache/README.md:31-33` |
| `tree_cache`（默认 `UnifiedRadixCache`） | 前缀树：token 前缀 → 已算好的 KV 槽；决定留谁、淘汰谁 | Scheduler 持有：`python/sglang/srt/managers/scheduler.py:686`；默认工厂落到 `create_unified_radix_cache`：`python/sglang/srt/mem_cache/registry.py:153` |

【文档】`python/sglang/srt/mem_cache/README.md:1-5` 原文："Everything that owns KV / SSM-state memory: who hands out slots, who holds the bytes on the device, who mirrors them to host and disk, and which radix cache decides what to keep."

请求在这三者之间的生命周期：
- **前缀匹配**（入批前）：`init_next_round_input` → `match_kv_cache(...)`：`python/sglang/srt/managers/schedule_batch.py:1205`。匹配上的部分直接复用，`prefix_len` 之后的才需要算。
  - 微观「为什么」：最多只匹配 `input_len - 1` 个 token。【注释】`python/sglang/srt/managers/schedule_batch.py:1239` 原文："the matched length is at most 1 less than the input length to enable logprob computation"。（另一层原因【推断】：至少要留 1 个 token 真正过一遍模型，才能拿到「下一个 token」的 logits。）
- **prefill 分配**：`prepare_for_extend` → `alloc_for_extend(...)`：`python/sglang/srt/managers/schedule_batch.py:2464`；函数文档："Allocate KV cache for extend batch and write to req_to_token_pool."：`python/sglang/srt/mem_cache/allocation.py:363-367`。
- **decode 分配**：`prepare_for_decode` 每步给每个请求再要 1 个槽：`self.out_cache_loc = alloc_for_decode(self, token_per_req=1)`：`python/sglang/srt/managers/schedule_batch.py:3346`。
- **结束释放**：`release_kv_cache`：`python/sglang/srt/mem_cache/common.py:385`。先 `tree_cache.checkpoint(req, ...)` 把这条请求的 token 序列（含输出）插进前缀树（`:419`），再释放「树不管的那部分」（`:420-421`，注释 "The protected prefix is not this req's to free."），最后解锁（`:422`）。
  - 这就是「下一条相同开头的请求能命中缓存」的来源：**缓存不是单独写的，是请求结束时顺手插进去的**。

> 每个框内部值得单独研究的机制：前缀树的匹配/插入/淘汰（入口 `python/sglang/srt/mem_cache/radix_cache.py:369`、`:433`、`:559`；默认实现在 `python/sglang/srt/mem_cache/unified_cache/`）；分配器与分页（`python/sglang/srt/mem_cache/allocation.py:363`）；`PrefillAdder` 的预算计算（`python/sglang/srt/managers/schedule_policy.py:1009`）；retract 的 `new_token_ratio`（`python/sglang/srt/managers/scheduler.py:5229`）。

### 4.6 发射：`run_batch` → TpModelWorker → ModelRunner

`run_batch`：`python/sglang/srt/managers/scheduler.py:5388`。overlap 分支里：
- 在 `forward_stream` 上下文里，先让 forward 流等 schedule 流（`self.forward_stream.wait_stream(self.schedule_stream)`：`:5433`），再从 `future_map` 解析出这一步真正的 input_ids（`:5442-5444`）。
- 调 `self.model_worker.forward_batch_generation(batch, ...)`：`python/sglang/srt/managers/scheduler.py:5470`。
- 把下一步要用的 seq_lens 发布进 `future_map`（`self.future_map.publish(future_indices, batch.seq_lens + 1)`：`:5475`）；把采样出的 `next_token_ids` 经 `_relay_forward_payload` 暂存进 `future_map`（调用处 `:5495`；函数内 `RelayPayload(bonus_tokens=batch_result.next_token_ids)`：`:5767`；文档字符串原文 "Stash this iter's relay payload for next iter's resolve_forward_inputs."：`:5758`）。
- 安排 GPU→CPU 异步拷贝（`_copy_result_to_cpu`：`:5506`），配一个 `copy_done` 事件（`:5493`）供之后处理结果时 `synchronize()`。

为什么用两条 CUDA 流（schedule_stream / forward_stream）？overlap 要求「CPU 准备下一批时写的小张量」和「GPU 正在算的上一批」互不踩踏；分流后用 `wait_stream`/事件精确排序。【推断】依据：`run_event_loop` 里专门防止两条流撞成同一条，注释 "Redraw if this stream aliases forward_stream, which would eliminate scheduler overlap."（`python/sglang/srt/managers/scheduler.py:2159-2161`）。

`TpModelWorker.forward_batch_generation`：`python/sglang/srt/managers/tp_worker.py:624`。
- `ForwardBatch.init_new(batch, self.model_runner, ...)`：`python/sglang/srt/managers/tp_worker.py:657`——把 CPU 侧的 `ScheduleBatch` 变成 GPU 张量批。
  - 【注释】`python/sglang/srt/managers/schedule_batch.py:41-46`：数据流是 "ScheduleBatch -> ForwardBatch"，"ScheduleBatch is managed by `scheduler.py::Scheduler`. It contains high-level scheduling data. Most of the data is on the CPU." / "ForwardBatch is managed by `model_runner.py::ModelRunner`. It contains low-level tensor data. Most of the data consists of GPU tensors."
  - 历史：中间曾有一层 `ModelWorkerBatch`，【提交】`63ba2f8d7b` "Clean up batch data structures: Introducing ModelWorkerBatch (#1544)" 引入，【提交】`58ece60703` "refactor: remove ModelWorkerBatch indirection (#25516)" 去掉。两次提交说明都只有标题。读旧博客/旧代码时会看到三层结构，现在只有两层。
- `self.model_runner.forward(forward_batch, ...)`：`python/sglang/srt/managers/tp_worker.py:689-692` → logits。
- 主路下直接 `self.model_runner.sample(logits_output, forward_batch)`：`python/sglang/srt/managers/tp_worker.py:771-773` → `next_token_ids`（GPU 张量，每个请求 1 个）。

`ModelRunner._forward_raw`：`python/sglang/srt/model_executor/model_runner.py:2093`。顶层只需知道一个分叉：
- decode 且能用 CUDA graph → 回放预先录好的图：`python/sglang/srt/model_executor/model_runner.py:2141-2146`；
- 否则（prefill 通常如此）→ 可用时走 prefill 的分段 CUDA graph，不然 eager：`:2163-2194`。

> 值得单独研究：CUDA graph 的录制/回放与 batch size 档位；attention backend；`Sampler`（`python/sglang/srt/model_executor/model_runner.py:2231`）；`ForwardBatch.init_new`（`python/sglang/srt/model_executor/forward_batch_info.py:937`）。

### 4.7 处理结果：把 token 记到 `Req` 上，判断结束，发给 Detokenizer

`process_batch_result`：`python/sglang/srt/managers/scheduler.py:5875`，按 forward 模式分流到 `batch_result_processor`：decode → `process_batch_result_decode`（`:5899-5900`），extend → `process_batch_result_prefill`（`:5907`）。

prefill 结果（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:389`）：
- 先 `result.copy_done.synchronize()` 等 GPU→CPU 拷贝完成：`:402-403`。
- 对每个请求 `req.output_ids.append(next_token_id)`，然后 `req.update_finish_state()`：`:516-521`。注释原文："req output_ids are set here"（`:516`）。

decode 结果（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1151`）：
- 同样先 `copy_done.synchronize()`：`:1156-1157`。
- 逐请求 `req.output_ids.extend(next_token_id)` → `req.update_finish_state(new_accept_len)`：`:1243-1246`。
- 结束了就 `_handle_finish_state_updated_req` → `self.request_retirer.release(req, ...)`（最终调 `release_kv_cache`，见 §4.5）：`:1252-1254`、`:1653`。
- 最后**整批**调用一次 `self.output_streamer.stream_output(...)`：`:1298`。

`update_finish_state`：`python/sglang/srt/managers/schedule_batch.py:1487`。（先兑现之前预约的结束 `to_finish`，）检查顺序是：词表越界/NaN → stop 字符串 → stop token/EOS → `max_new_tokens` → 语法终止（`:1498-1528`）。
- 为什么 stop 字符串先于 EOS 判：【注释】`python/sglang/srt/managers/schedule_batch.py:1503-1505` 原文："Stop string beats EOS/stop-token matched in the same step (speculative decoding can accept >1 token): token-based would trim only the last token and leak the stop string."

`SchedulerOutputStreamer.stream_output`：`python/sglang/srt/managers/scheduler_components/output_streamer.py:233`。对每个请求问「这一步要不要发」（`accept`：`:580`）：
- 已结束 → 一定发（`:583-589`）。
- 流式 → `stream_interval == 1` 时每步都发；否则攒够 `stream_interval` 个，或第一个 token 立刻发（`:602-606`）；但如果尾部可能是 stop 字符串的前缀，先憋着不发（`:608-610`）。
- 非流式 → 每 `SGLANG_FORCE_STREAM_INTERVAL`（默认 50）个 token 发一次（`:612-614`；默认值 `python/sglang/srt/environ.py:744`）。
- 要发的请求，把 `output_ids[send_token_offset:]` 这段**新 token** 追加进扁平数组，并推进 `send_token_offset`：`python/sglang/srt/managers/scheduler_components/output_streamer.py:626-630`。
- **只在请求第一次输出时**附带一个 `DetokenizeHeader`（prompt 尾部几个 token + 反分词选项）：`:642-646`。
- 打包成 `BatchTokenIDOutput`（`:898`），`self.send_to_detokenizer.send_output(payload)`：`:353`。

**为什么 header 只发一次？** 早期做法是每一步都把「用于反分词的 id 列表和偏移」一起发过去；后来改为只在首个输出发 header，之后只发新 token，Detokenizer 自己保存状态。【提交】`8080507c3e` 标题："[Perf] detokenizer: Send per-request decode inputs once and keep them in the detokenizer (#47260)"；从该提交的 diff 能看到 `output_streamer.py` 删掉了 `decode_ids_list`、`read_offsets` 等每步发送的字段，换成 `detokenize_headers`。说明文字只有标题。
- 【测试】`test/registered/unit/managers/test_incremental_detokenize.py:162-181`（`test_later_rows_decode_with_the_first_rows_header`）：三行输出里只有第一行带 header，后两行 header 为 `None`，仍能依次解出三段文本；请求结束后 `assertIsNone(detok.detokenize_state_table.get("r"))`，即状态随结束行一起释放。

---

## 5. Detokenizer：把 token id 增量地变成文字

`DetokenizerManager.event_loop`：`python/sglang/srt/managers/detokenizer_manager.py:164-171`——阻塞收 → 按类型分发 → 有输出就 PUSH 给主进程。这是一个朴素的单线程同步循环（不需要像 Scheduler 那样避免阻塞，它闲着就该睡）。

`handle_batch_token_id_out`：`python/sglang/srt/managers/detokenizer_manager.py:406`：
1. `_update_detokenize_states`：带 header 的行新建状态，否则按 rid 取旧状态，然后把新 token 追加进去：`:276-306`。
2. `_decode_batch_token_id_output`：对整批做两次 `batch_decode`（`surr_ids` 一次、`read_ids` 一次；默认 `disable_tokenizer_batch_decode=False`：`python/sglang/srt/arg_groups/fields/serving.py:312-314`），再逐个请求 `s.step(...)` 得到这一步该发的增量文本：`python/sglang/srt/managers/detokenizer_manager.py:308-369`。结束的请求顺手 `release` 掉状态：`python/sglang/srt/managers/detokenizer_manager.py:358-359`。
3. 组装 `BatchStrOutput(output_strs=..., ...)` 返回：`python/sglang/srt/managers/detokenizer_manager.py:428-432`。

**增量反分词的核心技巧（surr/read 两个偏移）**：`DetokenizeState`：`python/sglang/srt/managers/incremental_detokenize.py:122-145`。
- 不能单独 decode 新 token：很多分词器里一个 token 的文字取决于它前面的 token（空格、字节级 BPE 拼成的多字节字符）。所以每次 decode 「一小段上文 + 新 token」，再减去「只 decode 上文」的结果：`new_text = read_text[len(surr_text) :]`：`:216`。
- 首次的「上文」就是 header 里 prompt 的最后 `PROMPT_TAIL_LEN = 5` 个 token：`python/sglang/srt/managers/incremental_detokenize.py:22`、`:91-92`。
- 如果 `new_text` 以「�」结尾（字节还没凑齐一个完整 UTF-8 字符），只发「可打印前缀」，**不推进偏移**，等下一个 token 再试：`:230-236`。【注释】`:231-233` 原文："Incomplete UTF-8: emit the printable prefix only; do not commit (token offsets stay so the next iteration retries with more tokens)."
- 结束时才做 stop 串/stop token 的裁剪（`trim_matched_stop`：`python/sglang/srt/managers/detokenizer_manager.py:174`），输出 `sent_offset` 之后的尾巴：`python/sglang/srt/managers/incremental_detokenize.py:238-246`。

**为什么这段活放在 Detokenizer 而不是 Scheduler / 主进程**：见 §2.2 的【推断】。

**故障面**：状态表有容量上限，满了淘汰最老的：`python/sglang/srt/managers/incremental_detokenize.py:273-277`；上限默认 `1 << 16`：`python/sglang/srt/environ.py:747`。【注释】`python/sglang/srt/environ.py:745-746` 原文："Most per-request states the detokenizer keeps; past it, the oldest is evicted." 被淘汰的请求之后的文本会丢，并打警告：`python/sglang/srt/managers/detokenizer_manager.py:289-295`。

---

## 6. 回到主进程：handle_loop → ReqState → SSE

### 6.1 handle_loop：一个常驻协程收所有结果

`handle_loop`：`python/sglang/srt/managers/tokenizer_manager.py:2916-2929`：`await async_sock_recv(self.recv_from_detokenizer)`，是 `BatchStrOutput` 就 `_handle_batch_output`，其它类型交给分发表。

`_handle_batch_output`：`python/sglang/srt/managers/tokenizer_manager.py:2931`。对 batch 里每个 rid：
- 查 `rid_to_state`，查不到就警告跳过：`:2946-2955`。
- 拼 `meta_info`（`id`、`finish_reason`、`prompt_tokens`、`completion_tokens`、`cached_tokens` 等）：`:2973-2979`、`:3010-3012`。
- 文本：`state.append_text(delta_text)`——注意 Detokenizer 发来的 `output_strs[i]` 是**增量**：`:3081-3084`。
- 主路（流式、非增量模式）的中间块不当场拼全文，而是 `"text": None`，留给等待方取：`:3108-3115`。
  - 为什么：【注释】`:3108-3110` 原文："Non-incremental intermediate: pass reference (no copy) and defer text to _wait_one_response to avoid O(n) per-step cost that compounds to O(n^2)."；【提交】`c6fd9a00c7` 标题："[tokenizer] eliminate O(n²) copy in non-incremental streaming (#22567)"。
- 结束了：`del self.rid_to_state[rid]`：`:3228`。
- 唤醒：`state.out_list.append(out_dict)`，攒满 `batch_notify_size`（默认 16）个就一起 `event.set()` 并 `await asyncio.sleep(0)` 让出循环，最后把剩下的全部 set：`:3232-3240`、`:3253-3254`。
  - 为什么分批唤醒：【文档】`python/sglang/srt/arg_groups/fields/serving.py:263` 原文："Number of streaming notifications to batch before yielding to the event loop. Reduces asyncio wakeup overhead under high concurrency."；【提交】`1e34cd0ba5` 标题："PD streaming: batch notify + SSE fast path (#22658)"。

### 6.2 等待方被唤醒：`_stream_one_response`

`_stream_one_response`：`python/sglang/srt/managers/tokenizer_manager.py:2333`。
- 带超时地等 `state.event`（`_REQUEST_STATE_WAIT_TIMEOUT`，默认 4 秒：`python/sglang/srt/environ.py:2030`）：`python/sglang/srt/managers/tokenizer_manager.py:2346-2351`。超时醒来只是为了检查客户端是否断开（§7）。
- 醒来后原子地取走全部 `out_list`、清 event：`:2366-2370`。
- 只取最后一个块（非增量模式下每个块都是「到目前为止的全部」）：`:2382`；文本为 None 时这时才 `state.get_text()` 拼出全文：`:2398-2403`。
- 结束块：记日志/指标后 `yield out; break`：`:2439-2440`；中间块（流式）直接 `yield out`：`:2459-2466`。

### 6.3 OpenAIServingChat 把「全文」切回「增量」，写成 SSE

`_generate_chat_stream` 里 `async for content in stream:`（`stream = self.tokenizer_manager.generate_request(...)`：`python/sglang/srt/entrypoints/openai/serving_chat.py:2486`、`:2502`）。
`_generate_stream_content` 用每个 choice 的偏移切增量：`delta = content["text"][offset:]`，再更新偏移：`python/sglang/srt/entrypoints/openai/serving_chat.py:848-853`。然后（若开了推理/工具解析器还要过解析器）包成 `chat.completion.chunk` 的 SSE 块。流结束后补一行 `data: [DONE]`：`python/sglang/srt/entrypoints/openai/serving_chat.py:2825`。

> 读者易困惑点（看起来多余）：Detokenizer 给的是增量，TokenizerManager 拼成全文，Chat 层又切回增量。为什么不一路增量？
> - TokenizerManager 服务多个前端；原生 `/generate` 的默认契约是每个流式块给「累计全文」。开关 `incremental_streaming_output` 默认 `False`，说明原文："Whether to output as a sequence of disjoint segments."：【文档】`python/sglang/srt/arg_groups/fields/serving.py:269-272`。
> - Chat 层两种模式都兼容：开关打开时直接用 `content["text"]` 作增量（`python/sglang/srt/entrypoints/openai/serving_chat.py:849-850`）。
> - 所以「拼回再切」是默认契约的代价；c6fd9a00c7 已把其中最贵的 O(n²) 拷贝去掉了。【推断】（「为了兼容原生接口契约」这一动机没有找到提交说明直接佐证。）

### 6.4 非流式有什么不同（一句话）

非流式时 `handle_request` 走 `_handle_non_streaming_request`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2827`），只 `await` 生成器一次（`:2856`）；TokenizerManager 侧中间块 `out_dict = None` 不唤醒（`python/sglang/srt/managers/tokenizer_manager.py:3122-3123`），Scheduler 侧每 50 个 token 才送一次中间结果（§4.7）。

---

## 7. 断开与中止：谁来收尾

- 流式：`StreamingResponse(..., background=create_abort_task(...))`。后台任务在响应结束后跑（这是 Starlette `BackgroundTasks` 的语义，不是本仓库代码）：睡 2 秒，若该 rid 还在 `rid_to_state`（说明没正常结束，多半是客户端断了），就 `abort_request(rid)`：`python/sglang/srt/managers/tokenizer_manager.py:2878-2889`。【注释】`:2879` 原文："Abort the request if the client is disconnected."
- 非流式 / 排队中：`_stream_one_response` 每次超时醒来就 `request.is_disconnected()`，断了就 `abort_request` 并抛 `ValueError`：`python/sglang/srt/managers/tokenizer_manager.py:2352-2363`、`:2445-2457`。
- `abort_request` 向 Scheduler 发 `AbortReq`（走管道 ①）：`python/sglang/srt/managers/tokenizer_manager.py:2638-2653`；Scheduler 侧 `AbortReq → abort_request`：`python/sglang/srt/managers/scheduler.py:1991`。
- 仓库里有一张「各种中止情形由谁收尾」的表：【注释】`python/sglang/srt/managers/tokenizer_manager.py:4523-4533`，例如原文 "| http       | yes          | running         | background task | fast api              | del in _handle_batch_output |"。这张表最早由 【提交】`de167cf5fa` "Fix request abortion (#6184)" 加入（同一提交把等待时间从 `asyncio.sleep(1)` 改成 `asyncio.sleep(2)`）。
- 请求在**被发出前**就失败（如校验报错）时，`generate_request` 的 `except BaseException` 负责清掉 `rid_to_state`：【注释】`python/sglang/srt/managers/tokenizer_manager.py:1131-1138`，原文节选："a failure *before* a request reaches the scheduler -- e.g. input-length validation rejecting an over-context request -- would otherwise leak those entries forever."

---

## 8. 逐步推演：一条具体请求走一遍

**设定（全部是设的）**：单张 80GB GPU；模型是某个 Llama 风格的对话模型；前缀树里已有前面请求留下的、聊天模板开头的 5 个 token；`page_size` 视为 1。

**客户端发：**
```json
POST /v1/chat/completions
{"model":"m","messages":[{"role":"user","content":"法国首都是哪里？"}],
 "stream":true,"max_tokens":32}
```

| 步 | 在哪 | 发生了什么 | 具体值（设的） |
|---|---|---|---|
| 1 | 主进程·FastAPI | 解析成 `ChatCompletionRequest`，交给 `OpenAIServingChat.handle_request` | — |
| 2 | 主进程·线程池 | 套模板 + 分词，得 prompt_ids；构造 `GenerateReqInput(input_ids=[...20 个...], stream=True, sampling_params={max_new_tokens:32,...}, rid=None)` | prompt 长 20 token |
| 3 | 主进程·事件循环 | `generate_request`：生成 rid；建 `ReqState(out_list=[], finished=False, event)` 放入 `rid_to_state` | rid=`"9f3c…"`（uuid4 hex） |
| 4 | 主进程 | 已有 input_ids，不再分词；构造 `TokenizedGenerateReqInput(input_ids=array('q',[20 个]), sampling_params=SamplingParams(...))`，经管道 ① PUSH | — |
| 5 | 主进程 | 丢掉 `obj.input_ids`；`_stream_one_response` 开始 `await event`（最多 4 秒醒一次查断线） | — |
| 6 | Scheduler·第 n 圈 | `ingest_requests` 非阻塞抽到这条；`handle_generate_request` 造 `Req(origin_input_ids=20 个, output_ids=[])`；校验通过；进 `waiting_queue` | waiting_queue=[req] |
| 7 | Scheduler·第 n 圈 | `get_next_batch_to_run`：fcfs 排序；`init_next_round_input` 前缀匹配命中 5 个；`PrefillAdder` 预算够；`ScheduleBatch.init_new([req])`；`prepare_for_extend` → `alloc_for_extend` 分 15 个 KV 槽、占 req_to_token 第 r 行 | prefix_len=5，extend_len=15，forward_mode=EXTEND |
| 8 | Scheduler·第 n 圈 | `run_batch`：在 forward 流上 `ForwardBatch.init_new` → 模型前向（15 个位置 5..19）→ `sample` → next_token_ids（GPU 上）；登记 future_map；异步拷回 CPU；结果入 `result_queue`。没有上一批要处理 | 采到 X1 |
| 9 | Scheduler·第 n+1 圈 | 上一批是 prefill → 并入 running_batch；没新 prefill → `update_running_batch` → `prepare_for_decode`：再分 1 个槽，seq_len 20→21；input_ids 用 future_map 里的 X1（CPU 还没看到它）；**发射 decode 步** | running_batch=[req] |
| 10 | Scheduler·第 n+1 圈 | 处理第 n 圈结果：`copy_done.synchronize()`；`req.output_ids=[X1]`；未结束；`stream_output`：首个 token 立刻发，附 `DetokenizeHeader(prompt_tail_ids=最后 5 个 prompt token, ...)`；`BatchTokenIDOutput(rids=["9f3c…"], output_ids_flat=[X1], finished_reasons=[None])` 经管道 ② | send_token_offset 0→1 |
| 11 | Detokenizer | 建状态：decode_ids=[5 个尾巴]+[X1]，surr_offset=0，read_offset=5；surr_text=decode(尾巴)，read_text=decode(尾巴+X1)，差值=「巴」；不以 � 结尾 → 提交并推进偏移；回 `BatchStrOutput(output_strs=["巴"])` 经管道 ③ | 发出 「巴」 |
| 12 | 主进程·handle_loop | `state.append_text("巴")`；流式非增量中间块 → `out_dict={"text":None,...}`；`out_list.append`；`event.set()` | — |
| 13 | 主进程·等待协程 | 醒来取 out_list，text 为 None → `get_text()`=「巴」；yield；Chat 层 `delta="巴"[0:]="巴"`，偏移记 1；写出形如 `data: {"object":"chat.completion.chunk",...,"delta":{"content":"巴"}}` 的 SSE 块（字段细节未逐一核实） | 客户端看到「巴」 |
| 14 | 循环往复 | 第 n+1 圈发射的 decode 采到 X2（第 n+2 圈才处理）= 「黎」字 UTF-8 的前半个字节 token；Detokenizer 得到以「�」结尾的 new_text → 只发可打印前缀 ""，**不推进偏移**；主进程收到空增量 | 这一步客户端什么也看不到（Chat 层对空增量的具体处理未逐行核实） |
| 15 | 循环往复 | X3 = 后半个字节；这次 read 能解出完整的「黎」 → 发 「黎」 | 客户端看到「黎」 |
| 16 | 循环往复 | X4 → 「。」 | 客户端看到「。」 |
| 17 | Scheduler·第 n+5 圈 | 处理第 n+4 圈 decode 的结果：X5 = EOS：`update_finish_state` → `FINISH_MATCHED_TOKEN`；`release_kv_cache`：把这条请求已有 KV 的 token 序列（prompt + 已生成部分）插入前缀树、释放其余槽、解锁。注意 overlap 下本圈是**先发射再处理上一批**，所以第 n+5 圈的 decode 已经带着它发射了——这一步白算，下次选批时被剔掉、多分的槽一并回收 | 前缀树多了一条「法国首都…巴黎。」 |
| 18 | Scheduler→Detokenizer | 结束行必发；Detokenizer 用 `trim_matched_stop` 去掉末尾 EOS，发剩余尾巴（通常为空），释放该 rid 状态 | finished_reason=`{"type": "stop", "matched": X5}`（`python/sglang/srt/managers/req_types.py:526-530`） |
| 19 | 主进程 | `state.finished=True`；`del rid_to_state[rid]`；最终块 `text="巴黎。"`；`_stream_one_response` yield 后 break | — |
| 20 | 主进程·Chat 层 | 发带 `finish_reason` 的最后一块（以及可能的 usage 块），然后 `data: [DONE]`；HTTP 流关闭；2 秒后后台 abort 任务醒来，发现 rid 已不在表里，什么都不做 | 结束 |

如果紧接着又来一条「法国首都是哪里？」：第 7 步的前缀匹配能命中 19 个（上限是 input_len−1），只需算 1 个 token 的 prefill。

---

## 9. 旁路：从主路的哪里岔出去（只点名，不展开）

| 旁路 | 从哪里岔 | 入口锚点 |
|---|---|---|
| 原生 `/generate` 接口 | 路由层直接调 TokenizerManager，不经 Chat 层；单请求的最后一块和 `[DONE]` 一起写 | `python/sglang/srt/entrypoints/http_server.py:972-986`、`:998-1001` |
| Python 离线 `Engine` API | 不起 HTTP，直接调 `tokenizer_manager.generate_request` | `python/sglang/srt/entrypoints/engine.py:431` |
| 关闭 overlap | 选 `event_loop_normal` | `python/sglang/srt/managers/scheduler.py:7062-7063` |
| 多 TokenizerManager 进程 | `tokenizer_worker_num > 1` 时主进程换成 `MultiTokenizerRouter` | `python/sglang/srt/entrypoints/engine.py:1411-1413` |
| 多 Detokenizer 进程 | 前面挂一个路由进程扇出 | `python/sglang/srt/entrypoints/engine.py:1094-1150` |
| `skip_tokenizer_init` | Scheduler 把 token 直接发回主进程，不经 Detokenizer | `python/sglang/srt/managers/scheduler_components/ipc_channels.py:51-56` |
| Rust 前端（`SGLANG_RUST_SERVER`） | 不起 Python TokenizerManager/Detokenizer，由 rank0 Scheduler 里嵌入的 Rust 服务接管 HTTP/分词/反分词 | `python/sglang/srt/entrypoints/engine.py:1362-1366`；Scheduler 侧从环形缓冲区取请求：`python/sglang/srt/managers/scheduler_components/request_receiver.py:142-148` |
| 张量并行 tp>1 | rank0 收请求再广播 | `python/sglang/srt/managers/scheduler_components/request_receiver.py:215-234` |
| 数据并行 / DP attention | 多一个 DataParallelController 进程在 Scheduler 前分发 | `python/sglang/srt/entrypoints/engine.py:915-918`、`:986-1005` |
| 流水线并行 pp>1 | `event_loop_pp` | `python/sglang/srt/managers/scheduler.py:7056-7057` |
| PD 分离（prefill/decode 分机） | 入队走 bootstrap/prealloc 队列；专用事件循环 | `python/sglang/srt/managers/scheduler.py:3943-3957`、`:7064-7077` |
| 推测解码 | `run_batch` 里的 spec 分支、`spec_prepare_for_decode` | `python/sglang/srt/managers/schedule_batch.py:3323-3330` |
| 结构化输出（grammar） | 入队前先进 grammar 队列；采样延后到处理完上一批之后 | `python/sglang/srt/managers/scheduler.py:3766-3768`；`python/sglang/srt/managers/tp_worker.py:745-767` |
| 多模态 | Chat 层传 text+图像，TokenizerManager 用 mm_processor；Scheduler 里展开占位 token | `python/sglang/srt/managers/scheduler.py:3633-3661` |

---

## 10. 每个参与者里面有没有「值得点进去」的机制

| 参与者 | 里面值得单独研究的 | 入口 |
|---|---|---|
| OpenAIServingChat | 聊天模板渲染与分词、推理/工具调用流式解析器 | `python/sglang/srt/entrypoints/openai/serving_chat.py:1735`（`_process_messages`）、`:803` |
| TokenizerManager | 有：请求生命周期/中止/暂停/权重更新锁；输出 meta_info 与 logprob 组装 | `python/sglang/srt/managers/tokenizer_manager.py:1061`、`:2931` |
| Scheduler 事件循环 | 有：overlap 与 FutureMap、CUDA 双流排序、WAR barrier | `python/sglang/srt/managers/scheduler.py:2235`、`python/sglang/srt/managers/overlap_utils.py:309` |
| 选批 / PrefillAdder | 有：预算、chunked prefill、retract、各调度策略 | `python/sglang/srt/managers/scheduler.py:4586`、`python/sglang/srt/managers/schedule_policy.py:1009` |
| 前缀树 + 内存池 | 有：匹配/插入/淘汰、分页分配、HiCache 分层 | `python/sglang/srt/mem_cache/README.md`、`python/sglang/srt/mem_cache/common.py:184` |
| TpModelWorker / ModelRunner | 有：CUDA graph、attention backend、采样 | `python/sglang/srt/model_executor/model_runner.py:2093` |
| 结果处理 / OutputStreamer | 中等：结束判定、logprob、发送节奏 | `python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1151`、`python/sglang/srt/managers/scheduler_components/output_streamer.py:580` |
| Detokenizer | 有一小套：surr/read 偏移的增量解码 | `python/sglang/srt/managers/incremental_detokenize.py:207` |

---

## 11. 没查到为什么

如实列出：查了，但没找到直接说明理由的注释/提交/文档。

1. **为什么默认调度策略从 `lpm` 改成 `fcfs`。** 改动发生在 【提交】`ac2387279e`（标题 "Support penalty in overlap mode; return logprob with chunked prefill; improve benchmark scripts (#3988)"），diff 里 `schedule_policy: str = "lpm"` → `"fcfs"`，但说明里没提理由。
2. **为什么中止后台任务要睡 2 秒**（`python/sglang/srt/managers/tokenizer_manager.py:2881`）。`de167cf5fa` 把 1 改成 2，提交说明只有标题。
3. **为什么等待超时是 4 秒**（`SGLANG_REQUEST_STATE_WAIT_TIMEOUT`）。【提交】`9d6029fb92` "Fix TokenizerManager bottleneck for offline generation (#16456)" 只是把写死的 4 变成可配置，没解释 4 的来历。
4. **为什么非流式请求每 50 个 token 也要往 Detokenizer 送一次中间结果**（`SGLANG_FORCE_STREAM_INTERVAL`）。注释只说了「做什么」（"Non-streaming requests flush intermediate outputs every N decoded tokens."，`python/sglang/srt/environ.py:743`），没说为什么。
5. **为什么拆成三个进程**、**为什么 prefill 优先于 decode**：本稿给的是【推断】，没找到原始设计说明。
6. **为什么 `PROMPT_TAIL_LEN` 取 5**：没找到说明。
7. **为什么 IPC 默认用 pickle 而保留 msgpack 选项**：只找到两种模式的实现与 `PickleWrapper` 的说明（`python/sglang/srt/managers/io_struct.py:132-139`），没找到选默认值的理由。

（注：这个仓库里大量提交的说明只有标题，PR 正文在外网，本次按要求没有访问。）

---

## 12. 可改进处

- 【观点】主路上 TokenizerManager 不分词、Chat 层分词，"TokenizerManager"这个名字对新读者有误导；文档字符串（`python/sglang/srt/entrypoints/http_server.py:3305`）仍写 "Tokenizes the requests and sends them to the scheduler."，可以补一句「OpenAI 路由通常在上层已分好词」。
- 【观点】文本「增量→累积→增量」的往返在默认配置下仍然存在；若 OpenAI 路由能内部强制使用增量模式，可以省掉 `ReqState` 的全文拼接与 Chat 层的偏移切片。
- 【观点】overlap 带来的「多算一步、晚一拍处理」散落在多处（`_reqs_finishing_inflight`、`batch.copy()`、`release_kv_cache` 回收超额槽），没有一处集中说明；对新读者，一段集中的设计说明会很有帮助（目前最接近的是 `docs/docs/advanced_features/unified_memory_design.mdx` 的 "Page lifecycle under the overlap scheduler" 一节（`docs/docs/advanced_features/unified_memory_design.mdx:117`），但它只讲统一内存场景）。

---

## 附：给讲解员的读者提示

- **最值得对照看的两件事**：`event_loop_normal`（`python/sglang/srt/managers/scheduler.py:2200`）和 `event_loop_overlap`（`:2235`）。先看前者理解「一圈做什么」，再看后者理解「为什么结果处理晚一拍」。
- **时间上不连续的地方**：一条请求的「发出」（`generate_request`）和「收到」（`handle_loop`）是两个协程；Scheduler 里「发射」和「处理结果」差一圈。画时序图时这两处最容易画错。
- **效果在别处**：前缀缓存不是专门写的，而是请求结束时 `release_kv_cache` 顺手插进前缀树。
- **名不副实**：TokenizerManager 在 chat 主路上通常不分词；Detokenizer 发的是增量而 TokenizerManager 默认对外给累积。
