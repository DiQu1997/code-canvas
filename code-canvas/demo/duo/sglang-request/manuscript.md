# 一条请求在 sglang 里怎么走完：研究底稿

> 仓库 HEAD = `c569c43446`（2026-11-20）。所有锚点写成 `仓库相对路径:行号` 或 `:起-止`，均在 HEAD 上核对过。
> "为什么"统一标来源：【注释】【提交】【文档】【测试】【推断】；"可改进处"标【观点】。
> 例子里凡是我为讲解设定的值，都写了"（设）"。

---

## 0. 先定主路

**主路 = 默认配置下，单机、`sglang serve` / `python -m sglang.launch_server` 启动，客户端调 OpenAI 兼容的 `POST /v1/chat/completions`，`stream: true`，纯文本模型。**

选它的理由：

- 这是默认启动路径。`run_server` 在既非 encoder-only、也非 Ray 时走 `http_server.launch_server`（`python/sglang/launch_server.py:48-52`）；`sglang serve` 也是调用同一个 `run_server`（`python/sglang/cli/serve.py:104-108`）。
- Rust 服务端是开关，默认关：`SGLANG_RUST_SERVER = EnvBool(False)`（`python/sglang/srt/environ.py:2656`）。
- 默认 1 个 tokenizer worker、1 个 detokenizer worker（`python/sglang/srt/arg_groups/fields/serving.py:50-57`），不起 DP 控制器（只有 `num_dp_ranks > 1` 才起，`python/sglang/srt/entrypoints/engine.py:915-917`）。
- 默认开 overlap 调度：`disable_overlap_schedule` 默认 `False`（`python/sglang/srt/arg_groups/fields/schedule.py:212-218`），于是 `enable_overlap = not disable_overlap_schedule and not use_mlx()`（`python/sglang/srt/managers/scheduler.py:573-575`）。
- 默认是"非增量"流式：`incremental_streaming_output` 默认 `False`（`python/sglang/srt/arg_groups/fields/serving.py:269-272`），这一点后面会让人困惑，单独讲。
- 选 chat + stream，是因为它把"一条请求变成很多次回包"这件事完整暴露出来；非流式只是同一条管道最后只取一次结果（§6.4）。

旁路（别的入口、多卡、多进程、分离部署）只在 §8 点名。

---

## 1. 一句话全貌

**一条请求在主进程里被渲染成 token id，经 ZMQ 推给 GPU 上的调度进程；调度进程把它和别的请求拼批、一步步生成 token；每步的新 token id 经 ZMQ 推给反分词进程变成文字；文字再经 ZMQ 回到主进程，唤醒正在等这条请求的协程，写成一行行 SSE 发回客户端。**

关键点：**响应不是原路返回**。去程是 主进程 → 调度进程；回程是 调度进程 → 反分词进程 → 主进程。三个进程首尾相接成一个环。

代码自己的总述（`python/sglang/srt/entrypoints/http_server.py:3298-3311`）：

- 【注释】`python/sglang/srt/entrypoints/http_server.py:3306`「Scheduler (subprocess): Receives requests from the Tokenizer Manager, schedules batches, forwards them, and sends the output tokens to the Detokenizer Manager.」
- 【注释】`python/sglang/srt/entrypoints/http_server.py:3310`「The HTTP server, Engine, and TokenizerManager all run in the main process.」
- 【注释】`python/sglang/srt/entrypoints/http_server.py:3311`「Inter-process communication is done through IPC (each process uses a different port) via the ZMQ library.」

---

## 2. 参与者清单（机器图的部件）

读者第一个问题是"有哪些东西在跑"。按进程分：

### 2.1 主进程（1 个）

一个 asyncio 事件循环（uvicorn，`loop="uvloop"`，`python/sglang/srt/entrypoints/http_server.py:3144-3165`）上跑着：

| 部件 | 是什么 | 锚点 |
|---|---|---|
| FastAPI 路由 | `/v1/chat/completions` 处理函数 | `python/sglang/srt/entrypoints/http_server.py:1901-1910` |
| OpenAIServingChat | 把 OpenAI 格式翻译成内部请求、把内部输出翻译成 SSE | `python/sglang/srt/entrypoints/openai/serving_chat.py:386` |
| TokenizerManager | 引擎的"前台"：登记请求、发给调度进程、接收结果、唤醒等待者 | `python/sglang/srt/managers/tokenizer_manager.py:606` |
| `rid_to_state` 表 | rid → `ReqState`，每条在途请求一项 | `python/sglang/srt/managers/tokenizer_manager.py:863` |
| `handle_loop` 协程 | 常驻，专门收回程消息 | `python/sglang/srt/managers/tokenizer_manager.py:2916-2929` |
| 默认线程池 | 跑聊天模板渲染 + 分词（离开事件循环） | `python/sglang/srt/entrypoints/openai/serving_base.py:99-104` |

### 2.2 调度进程（每张 GPU 一个；单卡就 1 个）

`Engine._launch_subprocesses` 的文档串：【注释】`python/sglang/srt/entrypoints/engine.py:1207`「Launch the TokenizerManager in the main process, the Scheduler in a subprocess, and the DetokenizerManager in another subprocess.」

每个 (pp_rank, tp_rank) 起一个 `mp.Process`（`python/sglang/srt/entrypoints/engine.py:941-971`），进程里先构造 `Scheduler` 再进入 `run_event_loop`（`python/sglang/srt/managers/scheduler.py:7199-7206`）。进程内部：

| 部件 | 是什么 | 锚点 |
|---|---|---|
| Scheduler 主循环 | 单线程死循环：收请求 → 选批 → 跑 → 处理结果 | `python/sglang/srt/managers/scheduler.py:2235-2305` |
| `waiting_queue` | 已收到、未开始算的请求 | `python/sglang/srt/managers/scheduler.py:3936` |
| `running_batch` | 正在逐 token 解码的请求批 | `python/sglang/srt/managers/scheduler.py:2275` |
| `Req` / `ScheduleBatch` | 调度器里一条请求 / 一个批的数据结构 | `python/sglang/srt/managers/schedule_batch.py:440`、`python/sglang/srt/managers/schedule_batch.py:1997` |
| KV 缓存池 + 前缀树（radix cache） | GPU 显存里的 KV 存储，以及按 token 前缀复用它的树 | `python/sglang/srt/managers/schedule_batch.py:2005`、`python/sglang/srt/mem_cache/common.py:184-197` |
| TpModelWorker / ModelRunner | 真正在 GPU 上做前向和采样 | `python/sglang/srt/managers/tp_worker.py:624`、`python/sglang/srt/managers/tp_worker.py:689`、`python/sglang/srt/managers/tp_worker.py:771` |
| OutputStreamer | 把本步新 token 打包发给反分词进程 | `python/sglang/srt/managers/scheduler_components/output_streamer.py:262-353` |

### 2.3 反分词进程（1 个）

`DetokenizerManager`，单线程循环：收 → 解码 → 发（`python/sglang/srt/managers/detokenizer_manager.py:164-172`），内部有按 rid 存的增量解码状态表 `DetokenizeStateTable`（`python/sglang/srt/managers/detokenizer_manager.py:131-133`）。进程名 `sglang::detokenizer`（`python/sglang/srt/managers/detokenizer_manager.py:493`）。

### 2.4 三条 ZMQ 管道（机器图的主连线）

全部是 PUSH/PULL、单向、走本机 `ipc://` 临时文件（`python/sglang/srt/server_args.py:894-905`）：

| 管道 | 发送方 | 接收方 | 锚点 |
|---|---|---|---|
| `scheduler_input_ipc_name` | TokenizerManager（PUSH） | 调度进程 rank 0（PULL） | `python/sglang/srt/managers/tokenizer_manager.py:835-837`、`python/sglang/srt/managers/scheduler_components/ipc_channels.py:36-38` |
| `detokenizer_ipc_name` | 调度进程 rank 0（PUSH） | DetokenizerManager（PULL） | `python/sglang/srt/managers/scheduler_components/ipc_channels.py:58-61`、`python/sglang/srt/managers/detokenizer_manager.py:102-104` |
| `tokenizer_ipc_name` | DetokenizerManager（PUSH）**以及**调度进程（控制类回复） | TokenizerManager（PULL） | `python/sglang/srt/managers/detokenizer_manager.py:108-111`、`python/sglang/srt/managers/scheduler_components/ipc_channels.py:48-50`、`python/sglang/srt/managers/tokenizer_manager.py:831-833` |

**读者容易看错**：`tokenizer_ipc_name` 有两个发送方。生成结果走 调度 → 反分词 → 主进程；而 flush cache、权重更新等控制类请求的回复，调度进程直接推回主进程（`send_control_output`，`python/sglang/srt/managers/scheduler.py:2405-2415`）。

线上编码：默认用 pickle（`SGLANG_USE_PICKLE_IPC = EnvBool(True)`，`python/sglang/srt/environ.py:390`；`sock_send` 在该开关下走 `send_pyobj`，`python/sglang/srt/managers/io_struct.py:3012-3017`）。消息类型都是 `msgspec.Struct`（`python/sglang/srt/managers/io_struct.py:97`），为的是可选的 msgpack 传输：【提交】`be1930133a`「Convert IPC dataclasses to msgspec.Struct with opt-in msgpack transport」（改了 `python/sglang/srt/managers/io_struct.py`）。

### 2.5 多卡时的一个补充

多张 GPU 做张量并行时，**只有 rank 0 的调度进程有 ZMQ 收发口**，其余 rank 的 `recv_from_tokenizer` 是 `None`（`python/sglang/srt/managers/scheduler_components/ipc_channels.py:35-68`）。rank 0 收到后用 torch.distributed 的 CPU 组广播给其他 rank（`python/sglang/srt/managers/scheduler_components/request_receiver.py:214-234`）。【注释】`python/sglang/srt/managers/scheduler_components/request_receiver.py:104`「Receive results at tp_rank = 0 and broadcast it to all other TP ranks.」所以每个 rank 都跑同一份调度逻辑、做出同样的批决定。

---

## 3. 贯穿全程的例子

- 模型：一个对话模型，服务名 `qwen`（设），文本模型，默认参数启动。
- 客户端请求 A（设）：

```json
POST /v1/chat/completions
{"model": "qwen",
 "messages": [{"role": "user", "content": "用一句话介绍上海"}],
 "stream": true,
 "max_tokens": 64}
```

- 套完聊天模板后 prompt = 18 个 token（设）。
- 服务器上已经有一条请求 B 在逐 token 解码（设），用来演示"凑批"。
- A 最终生成 12 个 token 后吐出结束符，结束符 id 取 151645（设）。

下面每一站都会回到这个例子。

---

## 4. 去程：从 HTTP 到 GPU

### 站 1：HTTP 路由 → OpenAIServingChat（主进程，同一个事件循环里的协程）

- **拿到**：HTTP 请求体，FastAPI 已解析成 `ChatCompletionRequest`（路由签名 `python/sglang/srt/entrypoints/http_server.py:1902-1904`）。
- **做了**：直接 `await raw_request.app.state.openai_serving_chat.handle_request(...)`（`python/sglang/srt/entrypoints/http_server.py:1906-1910`）。这个对象在启动时由 `lifespan` 创建（`python/sglang/srt/entrypoints/http_server.py:352-356`）。
- 通用骨架在基类 `handle_request`（`python/sglang/srt/entrypoints/openai/serving_base.py:80-142`）：校验 → 转内部格式 → 按 `stream` 分流（`python/sglang/srt/entrypoints/openai/serving_base.py:111-118`）。异常统一转成 OpenAI 风格错误：`ValueError` → 400，其它 → 500（`python/sglang/srt/entrypoints/openai/serving_base.py:123-142`）。

### 站 2：聊天模板 + 分词（在线程池里）

**读者会问：分词不是 TokenizerManager 的事吗？** 在 chat 主路上不是。

- **做了**：`_convert_to_internal_request` 被扔进默认线程池执行（`python/sglang/srt/entrypoints/openai/serving_base.py:101-104`）。它调 `_process_messages` 套模板（`python/sglang/srt/entrypoints/openai/serving_chat.py:1264`），模板渲染成文本后，用 `parallel_prompt_encode` 编码成 id（`python/sglang/srt/entrypoints/openai/serving_chat.py:2274-2284`）。
- 为什么拆成"先渲染、再编码"而不是一步 `apply_chat_template(tokenize=True)`：【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:2135`「templates already include role/special tokens, so the encode must」——下一行接着说要避免重复加 BOS（`python/sglang/srt/entrypoints/openai/serving_chat.py:2136`「avoid double BOS on tokenizers that would add it.」）。
- 为什么放线程池：【注释】`python/sglang/srt/entrypoints/openai/serving_base.py:99`「Convert off-loop: jinja render + full-prompt tokenization blocks」，下一行「the loop on long prompts, and the rust tokenizer releases the GIL.」（`python/sglang/srt/entrypoints/openai/serving_base.py:100`）。来自【提交】`6a89181614`「tokenizer perf optimization」（改了 `python/sglang/srt/entrypoints/openai/serving_base.py`）。
  - 白话：整个主进程只有一个事件循环，所有在途请求的收发都靠它；长 prompt 的模板渲染 + 分词若在循环上跑，会让所有流式请求一起卡顿。
- **交给下一站**：一个 `GenerateReqInput`（`python/sglang/srt/entrypoints/openai/serving_chat.py:1303-1358`），对纯文本模型带的是 `input_ids`（`python/sglang/srt/entrypoints/openai/serving_chat.py:1280-1288`；多模态模型则可能带 `text`，`python/sglang/srt/entrypoints/openai/serving_chat.py:1176-1181`），以及 `sampling_params`（由 `to_sampling_params` 生成，`python/sglang/srt/entrypoints/openai/serving_chat.py:1266-1274`）、`stream=True`、`rid=request.rid`（客户端没给就是 `None`）。

例子：A 变成 `GenerateReqInput(input_ids=[...18 个 id...], sampling_params={"max_new_tokens": 64, ...}, stream=True, rid=None)`（字段值示意）。

### 站 3：流式响应"先等第一个包再回 200"

`_handle_streaming_request`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2369-2403`）不是立刻返回 `StreamingResponse`，而是先 `await generator.__anext__()` 拿到第一块（`python/sglang/srt/entrypoints/openai/serving_chat.py:2384-2387`）。

- 为什么：【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:2381`「Kick-start the generator to trigger validation before HTTP 200 is sent.」以及 `python/sglang/srt/entrypoints/openai/serving_chat.py:2383`「a proper HTTP 400 error response instead of streaming it as SSE payload.」。来自【提交】`269589ad71`「Return HTTP 400 for streaming validation errors」（改了 `python/sglang/srt/entrypoints/openai/serving_chat.py`）。
- **读者容易看错（时间上不连续）**：第一块 SSE（`role: assistant`）是在引擎**第一次回包之后**才生成的——它在 `async for content in stream` 循环体内（`python/sglang/srt/entrypoints/openai/serving_chat.py:2502`、`python/sglang/srt/entrypoints/openai/serving_chat.py:2596-2615`）。所以客户端连 HTTP 响应头都要等到首 token（prefill 完成）之后才收到。代码注释也印证：【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:2400`「The first chunk is out, so the wire has seen the first engine event.」
- 返回的是 `_ClosingStreamingResponse`，并挂了一个后台任务 `create_abort_task`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2397-2403`），用于断连清理（§7.1）。

### 站 4：TokenizerManager.generate_request（主进程协程）

入口 `python/sglang/srt/managers/tokenizer_manager.py:1061-1140`。它是一个**异步生成器**：调用方 `async for` 它，它就一块块吐结果。按顺序做：

1. 确保回程协程 `handle_loop` 已启动（`auto_create_handle_loop`，`python/sglang/srt/managers/tokenizer_manager.py:1066`、`python/sglang/srt/managers/tokenizer_manager.py:2891-2900`）。懒启动的原因：它要挂在**当前**事件循环上（`get_or_create_event_loop`，`python/sglang/srt/managers/tokenizer_manager.py:2896`）——【推断】TokenizerManager 构造时 uvicorn 的循环还没起来，所以只能在第一次请求时创建。
2. 规范化：`normalize_batch_and_arguments`（`python/sglang/srt/managers/tokenizer_manager.py:1069`）。**rid 在这里分配**：没给就 `uuid.uuid4().hex`（`python/sglang/srt/managers/io_struct.py:645-646`）。例子：A 的 rid = `"5f0c9e2a…"`（32 位十六进制，设）。
3. 登记：`_init_req_state` 给每个 rid 建一个 `ReqState`（空 `out_list`、`finished=False`、一个 `asyncio.Event`），放入 `rid_to_state`（`python/sglang/srt/managers/tokenizer_manager.py:4278-4287`）。重复 rid 直接报错（`python/sglang/srt/managers/tokenizer_manager.py:4273-4275`）。
4. 暂停闸门：`await self.is_pause_cond.wait_for(lambda: not self.is_pause)`（`python/sglang/srt/managers/tokenizer_manager.py:1106-1109`）。
5. 拿权重更新读锁：`async with self.model_update_lock.reader_lock`（`python/sglang/srt/managers/tokenizer_manager.py:1111`）。**注意它包住了后面整个"发送 + 等待回包"**，也就是说一条请求从发出到结束都持有读锁。为什么：权重更新拿写锁（`python/sglang/srt/managers/tokenizer_manager.py:2752-2755`），从而等所有在途请求结束才换权重；【注释】`python/sglang/srt/managers/tokenizer_manager.py:2688`「we are using the model_update_lock to check if there is still on-going requests.」。锁的引入：【提交】`8496701934`「[Misc] Fix metrics, weight update lock, request logging」（改了 `python/sglang/srt/managers/tokenizer_manager.py`）。
6. "分词"：`_tokenize_one_request`（`python/sglang/srt/managers/tokenizer_manager.py:1116`）。主路上 `obj.input_ids` 已有，直接用（`python/sglang/srt/managers/tokenizer_manager.py:1291-1292`）；只有拿到 `text` 时才真正调分词器（`python/sglang/srt/managers/tokenizer_manager.py:1319-1321`）。然后做长度校验：输入 ≥ 上下文长度就报错或截断（`python/sglang/srt/managers/tokenizer_manager.py:1513-1527`）。
7. 打包：`_create_tokenized_object`（`python/sglang/srt/managers/tokenizer_manager.py:1813-1958`）。`input_ids` 转成紧凑的 `array("q", ...)`（`python/sglang/srt/managers/tokenizer_manager.py:1823-1825`）；`sampling_params` 从 dict 变成 `SamplingParams` 对象并 `normalize`/`verify`（`python/sglang/srt/managers/tokenizer_manager.py:1845-1847`）；产出 `TokenizedGenerateReqInput`（`python/sglang/srt/managers/tokenizer_manager.py:1870-1916`）。
8. 发送：`_send_one_request` → `_dispatch_to_scheduler` → `sock_send`（`python/sglang/srt/managers/tokenizer_manager.py:2100`、`python/sglang/srt/managers/tokenizer_manager.py:851-854`）。注意这是**同步** send，不 await。发送前若该 rid 已被中止就不发（`python/sglang/srt/managers/tokenizer_manager.py:2086-2088`）——【提交】`005b8e033f`「Do not dispatch a request whose abort was already sent」。
9. 发完就把 `obj.input_ids` 置空释放（`python/sglang/srt/managers/tokenizer_manager.py:1121-1123`），然后进入等待：`async for response in self._wait_one_response(...)`（`python/sglang/srt/managers/tokenizer_manager.py:1124-1125`），见 §6。

**交接方式**：站 4 → 站 5 是跨进程 ZMQ PUSH。主进程不等调度进程"确认收到"，发完即转入等待自己的 `Event`。

### 站 5：调度进程收请求（Scheduler 主循环的第一段）

调度进程的主循环默认是 `event_loop_overlap`（选择逻辑 `python/sglang/srt/managers/scheduler.py:7055-7063`）。每一圈第一件事是 `ingest_requests`（`python/sglang/srt/managers/scheduler.py:2253`）。

- **收**：rank 0 用 `zmq.NOBLOCK` 把管道里现有的消息一口气全取出来，取空就停（`python/sglang/srt/managers/scheduler_components/request_receiver.py:152-159`）。
  - 为什么非阻塞：【推断】调度循环同时要驱动 GPU，不能卡在 socket 上等新请求；它每一圈"顺手看一眼有没有新来的"。
- **广播**：多卡时广播给其他 rank（§2.5）。
- **分派**：`process_input_requests` 按类型分派（`python/sglang/srt/managers/scheduler.py:2376-2415`），`TokenizedGenerateReqInput` → `handle_generate_request`（`python/sglang/srt/managers/scheduler.py:1983`）。
- **建 Req**：`_handle_generate_request` 构造 `Req`（`python/sglang/srt/managers/scheduler.py:3399-3420` 一带），校验输入长度（`python/sglang/srt/managers/scheduler.py:3677-3686`），最后若不需要语法约束就入队（`python/sglang/srt/managers/scheduler.py:3766-3768`）；入队即 `move([req], self.waiting_queue)`（`python/sglang/srt/managers/scheduler.py:3936`）。
- **读者容易看错（看起来缺了）**：校验失败的请求**也入队**——先 `req.set_finish_with_abort(error_msg)` 再 `_add_request_to_queue(req)`（`python/sglang/srt/managers/scheduler.py:3684-3686`）。它不会被算，但会沿正常输出通道把"中止"原因送回去，于是主进程只需要一种接收方式。【推断】依据：`_add_request_to_queue` 的文档串说「Every request the scheduler keeps owes its output from here」（`python/sglang/srt/managers/scheduler.py:3922`）。

例子：A 进入 `waiting_queue`，此时 `running_batch` 里有 B。

### 站 6：调度——这一步跑谁？

`get_next_batch_to_run`（`python/sglang/srt/managers/scheduler.py:4339-4476`）。读者会问三个问题：

**(a) prefill 和 decode 谁优先？** 有新的 prefill 批就先跑 prefill，否则才跑 decode：【注释】`python/sglang/srt/managers/scheduler.py:4444`「Run prefill first if possible」（`python/sglang/srt/managers/scheduler.py:4443-4456`）。这行来自【提交】`c211e7b669`「Simplify batch update」。
- 为什么 prefill 优先：【推断】新请求的首 token 延迟（TTFT）取决于它何时被 prefill；decode 批推迟一步，代价只是每个在跑请求多等一步。代码没写原因。
- 默认不把 prefill 和 decode 混在同一次前向里：`enable_mixed_chunk` 默认 `False`（`python/sglang/srt/arg_groups/fields/schedule.py:227-230`），混批路径只在它开启时走（`python/sglang/srt/managers/scheduler.py:4996-5017`）。

**(b) 等待队列里先挑谁？** 先按策略排序（`python/sglang/srt/managers/scheduler.py:4651-4662`）。默认策略是 `fcfs`（`python/sglang/srt/arg_groups/fields/schedule.py:109`）。即便是 FCFS 这种不看缓存的策略，也会对每条等待请求做一次前缀匹配：【注释】`python/sglang/srt/managers/schedule_policy.py:345`「Cache-agnostic policies sort without matching; match anyway so the load」（`python/sglang/srt/managers/schedule_policy.py:345-353`）。前缀匹配 = 在 radix 树里找最长已缓存前缀（`python/sglang/srt/mem_cache/common.py:193-197`）。

**(c) 能装下多少？** `PrefillAdder` 按 token 预算、显存余量、`chunked_prefill_size` 等逐条尝试装入（构造 `python/sglang/srt/managers/scheduler.py:4686-4705`，逐条 `add_one_req` `python/sglang/srt/managers/scheduler.py:4867-4871`）。装好后 `ScheduleBatch.init_new(...)` 建批、`prepare_for_extend()` 分配 KV 槽位（`python/sglang/srt/managers/scheduler.py:4955-4978`；分配在 `python/sglang/srt/managers/schedule_batch.py:2464`）。超长 prompt 会被切块（chunked prefill），分多步 prefill——细节不展开，入口即上面的 adder。

例子：本圈 A 单独组成一个 prefill 批（extend 18 个 token，前缀命中 0 个，设）。B 本圈不动。下一圈，上一圈的 prefill 批会被并入 `running_batch`（`python/sglang/srt/managers/scheduler.py:4378-4411`），此后每圈是 `[B, A]` 的 decode 批。

### 站 7：GPU 前向 + 采样（overlap：CPU 和 GPU 错开一拍）

`run_batch` → `model_worker.forward_batch_generation`（`python/sglang/srt/managers/scheduler.py:5388`、`python/sglang/srt/managers/scheduler.py:5470`）→ `model_runner.forward` 与 `model_runner.sample`（`python/sglang/srt/managers/tp_worker.py:689`、`python/sglang/srt/managers/tp_worker.py:771`）。模型前向、注意力内核等内部一律不展开。

**这是读者最容易迷糊的地方：overlap 循环里，"处理结果"永远比"发起计算"晚一拍。**

`event_loop_overlap`（`python/sglang/srt/managers/scheduler.py:2235-2305`）每一圈：

1. 收请求（`python/sglang/srt/managers/scheduler.py:2253`）；
2. 选出本圈批 N；`run_batch(N)` 只是把 GPU 工作**发射**出去，结果先放进 `result_queue`（`python/sglang/srt/managers/scheduler.py:2280-2284`）；
3. **然后**才处理上一圈批 N−1 的结果（`pop_and_process`，`python/sglang/srt/managers/scheduler.py:2289-2292`）——此时 GPU 正在算 N；
4. 再发射 N 的采样（`python/sglang/srt/managers/scheduler.py:2297-2300`）。为什么采样排在处理上一批之后：【注释】`python/sglang/srt/managers/scheduler.py:2298`「It depends on the result of the last batch (e.g., grammar), so we run it after the last batch is processed.」

- 为什么这么设计：【注释】`python/sglang/srt/managers/scheduler.py:2236`「A scheduler loop that overlaps the CPU processing and GPU computation.」；配置说明也写着【文档】`python/sglang/srt/arg_groups/fields/schedule.py:215`「Disable the overlap scheduler, which overlaps the CPU scheduler with GPU model worker.」。引入它的是【提交】`9116b2896f`「Add a new event loop」（改了 `python/sglang/srt/managers/scheduler.py`）。
- 什么时候破例先处理上一批：上一批是 prefill 且设了 `SGLANG_DISABLE_OVERLAP_AFTER_PREFILL`，或下一批的语法约束需要上一批结果（`python/sglang/srt/managers/scheduler.py:2307-2326`）。
- 副作用（容易看错）：一条请求在 N−1 结束了，但 N 可能已经把它算进去了。代码为此有一套"在途即将按长度结束"的处理（`python/sglang/srt/managers/scheduler.py:5182-5192`）。中止也类似：【注释】`python/sglang/srt/managers/scheduler.py:6595`「still runs one decode forward pass.」

### 站 8：处理结果——追加 token、判断结束

`process_batch_result`（`python/sglang/srt/managers/scheduler.py:5875-5918`）按批类型分到 prefill / decode 处理器。

- prefill 结果：等 GPU→CPU 拷贝完成（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:402-403`），把首 token 追加进 `req.output_ids` 并 `update_finish_state()`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:515-521`）。
- decode 结果：同样先等拷贝（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1156-1157`），每条请求 `req.output_ids.extend(next_token_id)` 再 `update_finish_state(...)`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1243-1246`），最后 `stream_output`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1298`）。
- 结束判断顺序 `update_finish_state`（`python/sglang/srt/managers/schedule_batch.py:1487-1528`）：越界 token → 停止串 → 停止 token/EOS → 长度上限 → 语法终止。顺序本身有讲究：【注释】`python/sglang/srt/managers/schedule_batch.py:1503`「Stop string beats EOS/stop-token matched in the same step (speculative」，以及 `python/sglang/srt/managers/schedule_batch.py:1510`「Stop token/EOS beats the length cap for the same reason: a spec accept」。
- **读者会问：调度进程里怎么也有分词器？** 判断"停止串"需要把尾部 token 解码成文字：`tail_str` 调 `self.tokenizer.decode(self.output_ids[-tail_len:])`（`python/sglang/srt/managers/schedule_batch.py:1276-1285`）。只解码尾巴一小段，不是完整反分词。
- 结束的请求释放 KV（写回 radix 树以便复用前缀）：`_prepare_kv_cache_release`（`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:1624-1647`）。
- 显存不够时 decode 批会"撤回"（retract）部分请求回等待队列（`python/sglang/srt/managers/scheduler.py:5207-5231`）——旁支，不展开。

例子：A 的 prefill 出首 token（"上海"，设）；之后每个 decode 步 A 和 B 各得 1 个 token。第 12 个 token 是 151645（设）→ `_check_token_based_finish` 命中 → `finished_reason = FINISH_MATCHED_TOKEN`，序列化为 `{"type": "stop", "matched": 151645}`（`python/sglang/srt/managers/req_types.py:526-530`）。

---

## 5. 回程第一段：调度进程 → 反分词进程

### 站 9：OutputStreamer 决定"这一步要不要发"

`stream_output` → `_stream_output_generation`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:262-353`）对批里每条请求调 `accept`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:580-630`）：

- 已结束：一定发（`python/sglang/srt/managers/scheduler_components/output_streamer.py:583-589`）。
- 流式且未结束：按 `stream_interval`（默认 1，`python/sglang/srt/arg_groups/fields/serving.py:257-260`）判断；首 token 总是立即发（`python/sglang/srt/managers/scheduler_components/output_streamer.py:601-606`）。
  - 但**若尾部可能是某个停止串的前半截，就先不发**：`should_output &= not req.check_match_stop_str_prefix()`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:610`）。【推断】否则客户端会先收到停止串的前半段、事后无法撤回。这行来自【提交】`5061b8fd3e`「fix stop when stream」（改了 `python/sglang/srt/managers/schedule_batch.py`），提交说明只有标题。
- 非流式且未结束：每 50 个 token 才发一次（`SGLANG_FORCE_STREAM_INTERVAL = EnvInt(50)`，`python/sglang/srt/environ.py:744`；`python/sglang/srt/managers/scheduler_components/output_streamer.py:611-614`）。

**发什么**：只发"上次发送之后的新 token"——`output_ids_[send_token_offset:]`，然后推进 `send_token_offset`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:619-630`）。【注释】`python/sglang/srt/managers/scheduler_components/output_streamer.py:626`「Exclude the tokens after stop condition」（用 `output_ids_through_stop`）。

**首包额外带一个"解码头"**：`DetokenizeHeader`，里面有 prompt 最后 5 个 token（`PROMPT_TAIL_LEN = 5`，`python/sglang/srt/managers/incremental_detokenize.py:22`）、`skip_special_tokens` 等（`python/sglang/srt/managers/incremental_detokenize.py:87-105`；附加位置 `python/sglang/srt/managers/scheduler_components/output_streamer.py:641-646`）。
- 为什么只发一次：【注释】`python/sglang/srt/managers/incremental_detokenize.py:75`「first output only; the detokenizer keeps it for the request's lifetime and」（整段文档串 `python/sglang/srt/managers/incremental_detokenize.py:74-76`）。来自【提交】`8080507c3e`「Send per-request decode inputs once and keep them in the detokenizer」（改了 `python/sglang/srt/managers/incremental_detokenize.py`、`python/sglang/srt/managers/io_struct.py`）。该提交的 diff 显示它**取代的旧做法**：`BatchTokenIDOutput` 上原本每步都带 `decode_ids: List[array]` 和 `read_offsets: List[int]` 等字段，被删除、换成 `detokenize_headers`。【推断】旧做法每步都要把整段待解码 id 重复序列化跨进程，新做法只传增量。
- 有【测试】佐证"后续行沿用首行的头"：`test/registered/unit/managers/test_incremental_detokenize.py:162`（`test_later_rows_decode_with_the_first_rows_header`）。

**怎么发**：整个批打成一个 `BatchTokenIDOutput`（多条请求的数组并排存，`to_payload`，`python/sglang/srt/managers/scheduler_components/output_streamer.py:335-338`），`send_to_detokenizer.send_output(payload)`（`python/sglang/srt/managers/scheduler_components/output_streamer.py:353`）。**一步一个消息，不是一条请求一个消息。**

例子：A prefill 那步发 `BatchTokenIDOutput(rids=[A], output_ids=[[t1]], detokenize_headers=[头(A)], finished_reasons=[None])`（示意）；之后每个 decode 步发 `rids=[B, A]`，各 1 个新 id；最后一步 A 的 `finished_reasons` 是 `{"type":"stop","matched":151645}`。

### 站 10：DetokenizerManager 把 id 变成文字（反分词进程，单线程）

主循环：阻塞收 → 分派 → 有输出就推给主进程（`python/sglang/srt/managers/detokenizer_manager.py:164-172`）。`BatchTokenIDOutput` → `handle_batch_token_id_out`（`python/sglang/srt/managers/detokenizer_manager.py:406-473`）：

1. 更新每个 rid 的状态：有头就新建状态，没头就查表（`python/sglang/srt/managers/detokenizer_manager.py:276-306`）；把新 id 追加进 `decode_ids`（`python/sglang/srt/managers/incremental_detokenize.py:178`）。
2. 批量解码两段：`surr_ids`（已确认部分的最后一小段，当上下文）和 `read_ids`（上下文 + 新 id），整批一起 `batch_decode`（`python/sglang/srt/managers/detokenizer_manager.py:331-334`）。
3. 每条请求 `step`：新文字 = `read_text[len(surr_text):]`（`python/sglang/srt/managers/incremental_detokenize.py:216`）。
4. 打包 `BatchStrOutput`（带 `output_strs`，以及原样透传的 token id、统计等），推给主进程（`python/sglang/srt/managers/detokenizer_manager.py:428-473`）。

**读者会问：为什么不直接 decode 新 token，而要带着前文一起 decode 再相减？** 状态结构的图示在 `python/sglang/srt/managers/incremental_detokenize.py:125-132`（surr_offset / read_offset 两个游标）。【推断】许多分词器单独解码一个 token 得到的文字与在上下文中不同（例如词首空格、字节级 BPE 把一个汉字拆成几个 token），带一小段上下文解码再做差能得到正确增量；首包的 prompt 尾巴 5 个 token 就是给第一个输出 token 当上下文的。来源：这套机制由【提交】`9c902b1954`「Decode Incrementally」和【提交】`a9ef49c12c`「Detokenize incrementally when streaming」引入（都改了 `python/sglang/srt/managers/detokenizer_manager.py`），两条提交说明都只有标题，原因是我的推断。

**半个字怎么办（例子里最可能遇到）**：若新文字以 `�` 结尾（UTF-8 不完整），只发"可打印前缀"，游标不前进，等下一个 token 再试（`python/sglang/srt/managers/incremental_detokenize.py:230-236`）。可打印前缀的规则是：换行全发；末字符是 CJK 全发；倒数第二是 CJK 就留最后一个；否则只发到最后一个空格（`python/sglang/srt/managers/incremental_detokenize.py:52-68`，【注释】`python/sglang/srt/managers/incremental_detokenize.py:54`「Borrowed from https://github.com/huggingface/transformers/」开头的一行说明借自 transformers）。

**结束时**：把整段已解码文字拿出来，`trim_matched_stop` 剪掉停止 token/串，只发尚未发过的部分（`python/sglang/srt/managers/incremental_detokenize.py:238-246`）。剪法：停止 token 默认假设是最后一个 id，直接去掉（`python/sglang/srt/managers/detokenizer_manager.py:194-205`），【注释】`python/sglang/srt/managers/detokenizer_manager.py:204`「NOTE: We can always assume the last token is the matched stop token」。同时释放该 rid 的状态（`python/sglang/srt/managers/detokenizer_manager.py:358-359`）。

**状态表有上限**：`LimitedCapacityDict`，满了淘汰最老的（`python/sglang/srt/managers/incremental_detokenize.py:260-270`），容量默认 `1 << 16`（`python/sglang/srt/environ.py:747`）。状态丢了只会丢文字、打警告（`python/sglang/srt/managers/detokenizer_manager.py:289-296`）。

例子：A 的结束行 `read_ids` 末尾是 151645，被剪掉，于是 EOS 不出现在文字里；A 的状态被释放。

---

## 6. 回程第二段：反分词进程 → 主进程 → 客户端

### 站 11：handle_loop 收包、填 ReqState、唤醒等待者（主进程协程）

`handle_loop`（`python/sglang/srt/managers/tokenizer_manager.py:2916-2929`）：`await async_sock_recv(self.recv_from_detokenizer)`，是生成结果就进 `_handle_batch_output`（`python/sglang/srt/managers/tokenizer_manager.py:2921-2925`），否则交给按类型分派的表（`python/sglang/srt/managers/tokenizer_manager.py:1037-1055`）。

`_handle_batch_output`（`python/sglang/srt/managers/tokenizer_manager.py:2931-3254`）对每个 rid：

1. 查 `rid_to_state`，查不到就警告跳过（`python/sglang/srt/managers/tokenizer_manager.py:2946-2954`）。
2. 组 `meta_info`：id、finish_reason、prompt_tokens、completion_tokens、cached_tokens 等（`python/sglang/srt/managers/tokenizer_manager.py:2973-2979`、`python/sglang/srt/managers/tokenizer_manager.py:3008-3014`）。
3. 文字：`state.append_text(delta_text)`（`python/sglang/srt/managers/tokenizer_manager.py:3084`）。默认非增量模式下，中间包**不拼字符串**，`"text": None`，留给等待者去拼（`python/sglang/srt/managers/tokenizer_manager.py:3107-3115`）。为什么：【注释】`python/sglang/srt/managers/tokenizer_manager.py:3109`「copy) and defer text to _wait_one_response to avoid」，下一行「O(n) per-step cost that compounds to O(n^2).」（`python/sglang/srt/managers/tokenizer_manager.py:3110`）。来自【提交】`c6fd9a00c7`「eliminate O(n²) copy in non-incremental streaming」（改了 `python/sglang/srt/managers/tokenizer_manager.py`）。
4. 结束：`del self.rid_to_state[rid]`（`python/sglang/srt/managers/tokenizer_manager.py:3228`）。
5. 放进 `state.out_list`，攒进 `pending_notify`；每攒满 `batch_notify_size`（默认 16，`python/sglang/srt/arg_groups/fields/serving.py:261-264`）个就 `event.set()` 并 `await asyncio.sleep(0)` 让出循环（`python/sglang/srt/managers/tokenizer_manager.py:3232-3240`）；剩下的在函数末尾统一 set（`python/sglang/srt/managers/tokenizer_manager.py:3252-3254`）。
   - 为什么：【文档】`python/sglang/srt/arg_groups/fields/serving.py:263`「Number of streaming notifications to batch before yielding to the event loop. Reduces asyncio wakeup overhead under high concurrency.」。来自【提交】`1e34cd0ba5`「PD streaming: batch notify + SSE fast path」（改了 `python/sglang/srt/managers/tokenizer_manager.py`）。

**交接方式**：站 11 → 站 12 是同一事件循环里两个协程之间的 `asyncio.Event`。`handle_loop` 是生产者，每条请求的 `_stream_one_response` 是消费者。**这是"效果在别处"的典型**：读 `generate_request` 的人看不到谁在往 `out_list` 里放东西——放东西的是另一个常驻协程。

### 站 12：_stream_one_response 被唤醒、取包（主进程，属于这条请求的协程）

`python/sglang/srt/managers/tokenizer_manager.py:2333-2466`：

1. 等 `state.event`，带超时（`python/sglang/srt/managers/tokenizer_manager.py:2343-2364`）；超时就检查一下客户端是否已断开（§7.1）。超时默认 4 秒（`SGLANG_REQUEST_STATE_WAIT_TIMEOUT = EnvInt(4)`，`python/sglang/srt/environ.py:2030`；`python/sglang/srt/managers/tokenizer_manager.py:240`）。
2. 一次性取走所有待处理包并清事件（`python/sglang/srt/managers/tokenizer_manager.py:2366-2370`）。
3. **只取最后一个**（非增量模式）：`out = out_list[-1]`（`python/sglang/srt/managers/tokenizer_manager.py:2381-2382`）；增量模式才把多个包合并（`python/sglang/srt/managers/tokenizer_manager.py:2374-2380`）。
   - 为什么丢中间包不算丢数据：非增量模式下每个包代表"到目前为止的全部"，最后一个包含前面所有。这段有一段历史：【提交】`b9dba851a0`「Fix streaming token ids data loss under load」在 diff 里写了「non-streaming only the latest cumulative output matters.」，当时对流式改成逐包全吐；之后增量模式改用合并函数 `_coalesce_streaming_chunks`（`python/sglang/srt/managers/tokenizer_manager.py:2237-2272`；由【提交】`9fb00ede15`「Clean up TokenizerManager and req_time_stats: reduce overhead and simplify」引入，改了 `python/sglang/srt/managers/tokenizer_manager.py`），其文档串说明【注释】`python/sglang/srt/managers/tokenizer_manager.py:2245`「Both text and output_ids are incremental deltas, so we concatenate them;」。
4. 若 `text` 是 `None`，此刻才 `state.get_text()` 拼出完整文字（`python/sglang/srt/managers/tokenizer_manager.py:2397-2403`）。
5. 结束：记日志/指标，若是调度端产生的错误型中止，转成异常或错误包（`python/sglang/srt/managers/tokenizer_manager.py:2412-2440`）；否则 `yield out` 并退出。
6. 未结束且流式：`yield out`（`python/sglang/srt/managers/tokenizer_manager.py:2459-2466`）。

### 站 13：OpenAIServingChat 把内部输出写成 SSE

`_generate_chat_stream`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2405-2825`）`async for content in stream`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2502`）：

1. 首次：先发一个 `role="assistant", content=""` 的块（`python/sglang/srt/entrypoints/openai/serving_chat.py:2596-2615`）。
2. 每次：`_generate_stream_content` 算增量。**非增量模式下 `content["text"]` 是累计全文，这里用 `stream_offsets` 自己切出新增部分**：`delta = content["text"][offset:]`（`python/sglang/srt/entrypoints/openai/serving_chat.py:848-853`）。有推理/工具调用解析器时会在这里再分流（`python/sglang/srt/entrypoints/openai/serving_chat.py:861-870`）。
3. 结束：循环结束后 `end_frames` 发带 `finish_reason` 的块（`python/sglang/srt/entrypoints/openai/serving_chat.py:2449-2484`、`python/sglang/srt/entrypoints/openai/serving_chat.py:2657-2659`），可选 usage 块（`python/sglang/srt/entrypoints/openai/serving_chat.py:2763-2802`），最后 `yield "data: [DONE]\n\n"`（`python/sglang/srt/entrypoints/openai/serving_chat.py:2825`）。
4. 每块的 JSON 是 `object="chat.completion.chunk"`，前缀 `data: `（`python/sglang/srt/entrypoints/openai/sse_utils.py:107-115`）。
5. **名不副实**：类里有 `_request_id_prefix` 返回 `"chatcmpl-"`（`python/sglang/srt/entrypoints/openai/serving_chat.py:659-660`），但默认 wire 下 SSE 块的 `id` 直接用引擎 rid（`chunk_id=wire.response_id(content["meta_info"]["id"])`，`python/sglang/srt/entrypoints/openai/serving_chat.py:2602`；默认 wire 的 `response_id` 原样返回 `python/sglang/srt/entrypoints/openai/chat_encoding_adapters.py:132-133`；无编码适配器时用默认 wire `python/sglang/srt/entrypoints/openai/serving_chat.py:654-657`）。我只核对了默认 wire。

例子：客户端依次收到（示意，字段从简）：

```
data: {"id":"5f0c9e2a…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}
data: {"id":"5f0c9e2a…",...,"choices":[{"index":0,"delta":{"content":"上海"}}]}
data: {"id":"5f0c9e2a…",...,"choices":[{"index":0,"delta":{"content":"是"}}]}
...（一个 token 一块；若某 token 只是半个汉字，那一步的 delta 可能为空或少一个字，下一步补上）
data: {"id":"5f0c9e2a…",...,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

注意：如果主进程忙、两步的包在 `out_list` 里排了队，客户端会收到一块**含两个 token 文字**的 delta——因为 §12 只取最后一个累计包、§13 再按偏移切。客户端看到的块数可以少于 token 数。

### 6.4 非流式的区别（一句话）

同一个 `generate_request`，只 `__anext__()` 取一次（`python/sglang/srt/entrypoints/openai/serving_chat.py:2856-2858`）；调度端对非流式每 50 个 token 才发一次中间包（§9），TokenizerManager 对非流式中间包直接 `out_dict = None` 不入队（`python/sglang/srt/managers/tokenizer_manager.py:3122-3123`），只有结束包会唤醒等待者。

---

## 7. 故障、中止与边界

### 7.1 客户端断开（三道保险）

1. 后台任务：`StreamingResponse` 结束后运行 `create_abort_task`，睡 2 秒后若 rid 仍在表里就中止（`python/sglang/srt/managers/tokenizer_manager.py:2878-2889`）。这个机制来自【提交】`8dbdc018a3`「Abort disconnected requests」；`sleep(1)` 改成 `sleep(2)` 来自【提交】`de167cf5fa`「Fix request abortion」，两者说明都只有标题（见 §9 没查到）。
2. 关闭生成器：`_ClosingStreamingResponse` 在发送失败时也强制 `aclose()` 生成器。【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:162`「Starlette leaves the iterator suspended when a send fails, and on ASGI」，下一行「spec >= 2.4 it also skips the background task, so nothing would abort the」（`python/sglang/srt/entrypoints/openai/serving_chat.py:163`）。生成器关闭会触发 `generate_request` 的清理（【注释】`python/sglang/srt/entrypoints/openai/serving_chat.py:2821`「Closing generate_request runs its cleanup, which aborts the」）。
3. 轮询：等待超时或收到包时，至多每 4 秒问一次 `request.is_disconnected()`（`python/sglang/srt/managers/tokenizer_manager.py:2352-2364`、`python/sglang/srt/managers/tokenizer_manager.py:2442-2457`）。为什么限频：【注释】`python/sglang/srt/managers/tokenizer_manager.py:2442`「is_disconnected() costs an event-loop pass, so poll at most once per wait」；【提交】`837d1cf76e`「Poll client disconnects once per wait timeout, not per streamed chunk」。

中止的落地：`abort_request` 发一个 `AbortReq` 给调度进程（`python/sglang/srt/managers/tokenizer_manager.py:2638-2657`）；调度进程从各队列移除，或对正在跑的请求 `request_finish(FINISH_ABORT())`（`python/sglang/srt/managers/scheduler.py:6583-6606`），之后它照常走一遍输出通道把 KV 清掉。

### 7.2 请求在发给调度前就失败

`generate_request` 的 `except BaseException` 调 `_release_req_states_on_failure`（`python/sglang/srt/managers/tokenizer_manager.py:1131-1140`）。【注释】`python/sglang/srt/managers/tokenizer_manager.py:1136`「request -- would otherwise leak those entries forever. Drop」（整段 `python/sglang/srt/managers/tokenizer_manager.py:1132-1138`）。来自【提交】`f478b2bb2d`「Fix: abort handling for dispatched requests after client disconnect」。

### 7.3 子进程崩溃

- 反分词进程异常：打日志后给父进程发 `SIGQUIT`（`python/sglang/srt/managers/detokenizer_manager.py:506-510`）。
- 主进程有 `SubprocessWatchdog` 盯所有调度/反分词子进程（`python/sglang/srt/entrypoints/engine.py:1430-1439`）。

### 7.4 两边各做一次长度校验

主进程 `_validate_one_request`（`python/sglang/srt/managers/tokenizer_manager.py:1483-1527`）和调度进程 `validate_input_length`（`python/sglang/srt/managers/scheduler.py:3678-3686`）都查长度。主进程那边自己承认重复：【注释】`python/sglang/srt/managers/tokenizer_manager.py:1487`「FIXME: unify the length validation logic with the one in the scheduler.」。主进程用的 `max_req_input_len` 是启动时从调度进程拿回来的（`python/sglang/srt/entrypoints/engine.py:1426-1428`）。

---

## 8. 旁路（只点名，不展开）

| 旁路 | 从主路哪一站岔出 | 入口锚点 |
|---|---|---|
| 原生 `/generate` 接口 | 站 1–2：不经 OpenAIServingChat，直接进 TokenizerManager；此时 TokenizerManager 才真正分词 | `python/sglang/srt/entrypoints/http_server.py:972-1050`、`python/sglang/srt/managers/tokenizer_manager.py:1319-1321` |
| `/v1/completions`、Anthropic `/v1/messages`、Ollama `/api/chat` | 站 1：换一个 serving 类，后面同主路 | `python/sglang/srt/entrypoints/http_server.py:1891-1898`、`python/sglang/srt/entrypoints/http_server.py:2193`、`python/sglang/srt/entrypoints/http_server.py:2164` |
| Python `Engine` API（无 HTTP） | 站 1：直接调 `generate_request` | `python/sglang/srt/entrypoints/engine.py:431`、`python/sglang/srt/entrypoints/engine.py:542` |
| 多 tokenizer worker（`--tokenizer-worker-num > 1`） | 站 4：多个 HTTP 进程，经 `MultiTokenizerRouter` 汇总，回包按 `http_worker_ipc` 路由 | `python/sglang/srt/entrypoints/engine.py:1411-1414`、`python/sglang/srt/managers/tokenizer_manager.py:839-844` |
| 多 detokenizer worker | 站 10：前置一个路由进程分发 | `python/sglang/srt/entrypoints/engine.py:1094-1150` |
| `--skip-tokenizer-init` | 站 9：调度进程直接把 token id 推给主进程，跳过反分词 | `python/sglang/srt/managers/scheduler_components/ipc_channels.py:51-56` |
| 数据并行（`dp_size > 1`） | 站 5 之前：多一个 DP 控制器进程分发 | `python/sglang/srt/entrypoints/engine.py:985-1006` |
| 流水线并行 / PD 分离（prefill 与 decode 分机） | 站 5–8：换用另一套事件循环 | `python/sglang/srt/managers/scheduler.py:7055-7077` |
| 内嵌 Rust 服务端（`SGLANG_RUST_SERVER=1`） | 整个主进程与反分词进程被 Rust 取代 | `python/sglang/srt/entrypoints/engine.py:1362-1394`、`python/sglang/srt/entrypoints/http_server.py:3334-3363` |
| encoder-only / Ray | 启动入口分叉 | `python/sglang/launch_server.py:25-47` |
| 前置路由（`sgl-router`） | 在 sglang 进程之外，仓库顶层目录 `sgl-router/` | — |

---

## 9. 没查到为什么

以下地方我查了代码注释和 git 提交说明，没有找到写明的理由；不补猜测。

1. **默认调度策略从 `lpm` 改为 `fcfs`**：发生在【提交】`ac2387279e`（diff 里 `-    schedule_policy: str = "lpm"` / `+    schedule_policy: str = "fcfs"`），但该提交标题是「Support penalty in overlap mode; return logprob with chunked prefill; improve benchmark scripts」，与此无关，正文没写原因。
2. **断连后台任务睡 2 秒**（`python/sglang/srt/managers/tokenizer_manager.py:2881`），且由 1 秒改为 2 秒（`de167cf5fa`）——提交只有标题。
3. **`PROMPT_TAIL_LEN = 5`**（`python/sglang/srt/managers/incremental_detokenize.py:22`）为什么是 5。
4. **IPC 默认用 pickle、msgpack 是可选**（`python/sglang/srt/environ.py:390`）：`be1930133a` 标题写了 "opt-in"，没写为什么不默认。
5. **`batch_notify_size` 默认 16、等待超时默认 4 秒、非流式强制发送间隔 50**：数值本身的选取理由没找到。
6. **反分词为什么是一个独立进程**：从最初版本（【提交】`22085081bb`「release initial code」，已含 `DetokenizerManager`）就是如此，没有说明。【推断】若放在主进程会与 HTTP/SSE 共用一个事件循环和 GIL，放在调度进程会拖慢 GPU 驱动循环；独立进程让三段流水并行。这只是推断。
7. 本仓库的提交说明普遍只有标题（PR 正文不在 git 里，我没有访问外网），所以很多"为什么"只能落到【注释】或【推断】。

---

## 10. 读者层面的提示（给讲解员）

- **最容易困惑的一处**：§7 的 overlap"晚一拍"。建议把"发射 N / 处理 N−1"并排对照着看。
- **第二处**：回程不走原路——三进程是环，不是来回。
- **第三处**：主路上"分词"发生在 OpenAIServingChat 的线程池里，不在名字叫 TokenizerManager 的那个类里（§站 2、站 4 第 6 步）。
- **第四处**：文字在三处被"切增量"：调度端按 `send_token_offset` 切 id（站 9）、反分词按 surr/read 两个游标切文字（站 10）、OpenAIServingChat 再按 `stream_offsets` 切累计文字（站 13）。中间 TokenizerManager 反而是"拼回累计全文"（站 11–12）。这四处最好对照着看。
- **时间不连续**：HTTP 200 响应头要等首 token（站 3）。
- **数据形态一路变化**：`ChatCompletionRequest`（pydantic）→ `GenerateReqInput`（dataclass，dict 采样参数）→ `TokenizedGenerateReqInput`（msgspec，`array("q")` + `SamplingParams`）→ 调度端 `Req` → 批 `ScheduleBatch` → `BatchTokenIDOutput`（整批，增量 id）→ `BatchStrOutput`（整批，增量文字）→ `out_dict`（单条，dict）→ SSE 字符串。

---

## 11. 可改进处

- 【观点】长度校验在主进程和调度进程各写一份，代码自己标了 FIXME（`python/sglang/srt/managers/tokenizer_manager.py:1487`）。
- 【观点】默认非增量流式下，"累计全文"在 TokenizerManager 拼起来、又在 OpenAIServingChat 按偏移切开，两次处理互相抵消；`--incremental-streaming-output` 已提供直传增量的路径，默认值仍是关。
- 【观点】`_request_id_prefix` 的 `"chatcmpl-"` 在默认 chat 流式路径上没被用到，名字容易误导。
