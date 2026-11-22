# 一个请求如何穿过 vLLM 再回到客户端（顶层底稿）

> 仓库：vllm，HEAD = `0e3c8dbdae`。所有锚点均为仓库相对路径 `文件:行号`，在 HEAD 上核对过。
> 来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。引号「」内为逐字原文。

---

## 0. 一屏结论

一个 `POST /v1/chat/completions` 请求，在默认配置（单机、单 GPU）下只经过**两个进程**：

1. **API Server 进程**（`vllm serve` 本身）：HTTP → 聊天模板 + 分词 → 打包成 `EngineCoreRequest` → 通过 ZMQ 发出去；另一头收回 token id → 去分词成文字 → 按 SSE 一块块写回客户端。
2. **EngineCore 进程**（后台子进程）：一个永不停的“忙循环”，每一轮做：收新请求 → 调度器挑出这一步要算哪些 token → 让 GPU 跑一次前向 + 采样 → 把每个请求新生成的 token id 发回去。

单 GPU 时，**GPU Worker 不是独立进程**，而是住在 EngineCore 进程里（执行器是 `UniProcExecutor`）。只有 TP/PP > 1 时，才会多出“每 GPU 一个 Worker 进程”。这一点和官方架构文档的“每 GPU 一个 worker 进程”说法不一致，是读者最容易看错的地方（见 §6.9）。

两个进程之间只有两条 ZMQ 管道：**请求进（ROUTER→DEALER）**、**结果出（PUSH→PULL）**。两头都用“后台 IO 线程/任务 + 队列”把网络收发和主逻辑隔开。

整条链上反复出现的一个设计思想：**CPU 和 GPU 要错开干活**——分词放线程池、ZMQ 收发放 IO 线程、调度提前一步（async scheduling）、GPU→CPU 结果拷贝走独立 CUDA stream。

---

## 1. 主路怎么选的，为什么

本稿的“主路”= 以下默认组合：

| 选择 | 默认值 / 依据 |
|---|---|
| 启动方式 | `vllm serve <model>`，单 API server 进程：`api_server_count` 未设时取 `data_parallel_size`（默认 1），走 `uvloop.run(run_server(args))`（`vllm/entrypoints/cli/serve.py:113-125`，`vllm/entrypoints/cli/serve.py:153-156`） |
| 接口 | OpenAI Chat Completions `/v1/chat/completions`，`stream=true`（最常见的在线用法；非流式只在最后一步不同，§5 末尾点名） |
| 并行 | TP=PP=DP=1。此时 `distributed_executor_backend` 被设为 `"uni"`（`vllm/config/parallel.py:1316-1317`）→ `UniProcExecutor`（`vllm/v1/executor/abstract.py:86-89`） |
| 引擎客户端 | DP=1 → `AsyncMPClient`（`vllm/v1/engine/core_client.py:170-188`） |
| 调度 | async scheduling **默认开启**：未显式设置时，只要不是 pooling 模型且执行器支持就置 True（`vllm/config/vllm.py:1752-1779`）；于是调度器类是 `AsyncScheduler`（`vllm/config/scheduler.py:299-304`），并发批数 = `pp_size + async_scheduling_depth - 1` = 1+2-1 = 2（`vllm/config/vllm.py:649-655`，depth 默认 2 见 `vllm/config/scheduler.py:275`），因此 EngineCore 走 `step_with_batch_queue` 而不是 `step`（`vllm/v1/engine/core.py:231-237`，`vllm/v1/engine/core.py:265-267`） |
| 模型执行 | `GPUModelRunner`（`vllm/v1/worker/gpu/model_runner.py`，即设计文档里的 “Model Runner V2”，见 `docs/design/model_runner_v2.md:1`），除“仅多模态编码器”模式外 Worker 只会构造它（`vllm/v1/worker/gpu_worker.py:623-629`） |
| 模型 | 纯文本、HuggingFace tokenizer 的普通 chat 模型（非多模态、非 GPT-OSS/harmony） |

理由：这是“一台机一张卡跑 `vllm serve`”时代码实际会走的分支，也是读者最先接触的形态；其他形态（多卡、多 API server、DP、PD 分离、Rust 前端、gRPC、离线 `LLM` 类）都是在这条路的某个点上岔出去，§7 统一点名。

> 读者提示：网上大量 vLLM 讲解基于旧版（V0 或早期 V1），里面“step() 同步跑一步”“scheduler 先 prefill 再 decode”的画面在 HEAD 默认配置下都不成立。本稿的主路是 **async scheduling + batch queue**。

---

## 2. 参与者总览（机器图的部件）

下面 9 个部件足以画出顶层。前 6 个在 API Server 进程，后 3 个在 EngineCore 进程。

| # | 部件 | 所在进程 / 线程 | 一句话 |
|---|---|---|---|
| A | HTTP 层：uvicorn + FastAPI 路由 `create_chat_completion` + `with_cancellation` | API 进程 / asyncio 事件循环 | 收 JSON、解析成 `ChatCompletionRequest`；返回 `StreamingResponse`；监听客户端断开 |
| B | `OpenAIServingChat` | API 进程 / 事件循环 | OpenAI 协议适配：模板渲染 → 采样参数 → 调 `engine_client.generate()` → 把结果拼成 SSE chunk |
| C | Renderer（`OnlineRenderer` → `HfRenderer`/`BaseRenderer`） | API 进程 / 事件循环 + **分词线程池** | 套 chat template、分词，产出 `EngineInput`（`{"type": "token", "prompt_token_ids": [...]}`） |
| D | `AsyncLLM`（含 `InputProcessor`） | API 进程 / 事件循环 | 引擎的前端门面：校验、构造 `EngineCoreRequest`、登记请求、交给客户端；把每个请求的输出交还给调用方 |
| E | `OutputProcessor` + 每请求 `RequestOutputCollector` + `output_handler` 后台任务 | API 进程 / 事件循环中的常驻 task | 把引擎回来的 token id 去分词、判 stop string、生成 `RequestOutput`，塞进对应请求的“信箱” |
| F | `AsyncMPClient`（ZMQ 客户端）+ `EngineCoreOutputQueueTask` | API 进程 / 事件循环 + ZMQ 自带 2 个 IO 线程 | msgpack 序列化，ROUTER 发请求、PULL 收结果 |
| G | `EngineCoreProc`：**输入线程** / **忙循环（主线程）** / **输出线程** | EngineCore 进程 | 三线程两队列；忙循环驱动“调度—执行—更新” |
| H | `Scheduler`（实际是 `AsyncScheduler`）+ `KVCacheManager` | EngineCore 进程 / 主线程 | 每步按 token 预算挑请求、分配 KV block、拿结果后追加 token 并判 EOS/长度 |
| I | `UniProcExecutor` → `Worker` → `GPUModelRunner` → GPU | EngineCore 进程 / 主线程 + GPU 上的 CUDA streams | 准备输入、前向、采样；把采样结果异步拷回 CPU |

**存储单元**（读者会问“状态都放哪儿”）：

- API 进程：`OutputProcessor.request_states`（每请求的去分词器、logprobs 处理器、信箱）；每请求一个 `RequestOutputCollector`（单槽信箱）。
- 跨进程：两条 ZMQ socket（默认单机时是 IPC 路径，`vllm/v1/engine/utils.py:1170-1173`）。
- EngineCore 进程：`input_queue` / `output_queue` / `aborts_queue` 三个 `queue.Queue`（`vllm/v1/engine/core.py:1360-1361`，`vllm/v1/engine/core.py:274`）；调度器里的 `requests`、waiting/running 队列；`batch_queue`（deque，长度 2）。
- GPU：KV cache（按 block 分页）、模型权重、Model Runner 的常驻请求状态（如 `last_sampled_tokens`）。

**关键数据结构的流转**（讲解员可以拿来做一条“对象变形”轴）：

```
HTTP JSON
 → ChatCompletionRequest (pydantic)
 → EngineInput  {"type": "token", "prompt_token_ids": [...]}         (Renderer)
 → SamplingParams (output_kind=DELTA)                                (OpenAIServingChat)
 → EngineCoreRequest (msgspec.Struct)                                (InputProcessor)
 ══ ZMQ / msgpack ══▶
 → Request (调度器内部可变对象)                                       (EngineCore 输入线程)
 → SchedulerOutput  → ModelRunnerOutput / AsyncOutput                (调度器 ↔ GPU)
 → EngineCoreOutput(new_token_ids, finish_reason…) 装进 EngineCoreOutputs
 ◀══ ZMQ / msgpack ══
 → RequestOutput (含 delta 文本)                                     (OutputProcessor)
 → ChatCompletionStreamResponse → "data: {...}\n\n"                  (OpenAIServingChat)
```

---

## 3. 交接（机器图的连线）

| 连线 | 机制 | 谁等谁 | 锚点 |
|---|---|---|---|
| 客户端 → A | HTTP；uvicorn 跑 FastAPI | — | `vllm/entrypoints/launchers/launcher.py:229`，`vllm/entrypoints/openai/chat_completion/api_router.py:43-59` |
| A → B | 普通 `await` 调用 | A 等 B 返回“生成器 / 完整响应 / 错误” | `vllm/entrypoints/openai/chat_completion/api_router.py:67` |
| B → C | `await online_renderer.render_chat()`；C 内部把阻塞工作丢进线程池 | 事件循环不阻塞，协程挂起等线程池 Future | `vllm/entrypoints/openai/chat_completion/serving.py:288`，`vllm/renderers/base.py:162-164` |
| B → D | `engine_client.generate(...)` 返回**异步生成器**（此刻什么都没发生） | 由谁开始迭代决定何时真正提交（见 §6.2 “时间上不连续”） | `vllm/entrypoints/openai/chat_completion/serving.py:411-423` |
| D → E | 函数调用：先 `output_processor.add_request(...)` 登记，再发给引擎 | — | `vllm/v1/engine/async_llm.py:594-598` |
| D → F | `await engine_core.add_request_async(request)` | 只等 zmq.asyncio 的发送完成，不等引擎处理（【推断】，依据 `send_multipart` 是唯一 await 点 `vllm/v1/engine/core_client.py:1335`） | `vllm/v1/engine/core_client.py:1374-1377` |
| F → G（输入线程） | ZMQ：前端 **ROUTER**（bind）→ 引擎 **DEALER**（connect），多帧消息 `(引擎身份, 请求类型字节, msgpack 数据…)` | 跨进程，异步 | `vllm/v1/engine/core_client.py:709-715`，`vllm/v1/engine/core_client.py:1325-1335`，`vllm/v1/engine/core.py:2213-2220` |
| G 输入线程 → G 忙循环 | `input_queue.put_nowait((类型, Request))`；abort 另外双写 `aborts_queue` | 忙循环空闲时**阻塞**在 `input_queue.get()` | `vllm/v1/engine/core.py:2295-2303`，`vllm/v1/engine/core.py:1857-1861` |
| G 忙循环 → H | 函数调用 `scheduler.add_request` / `schedule()` / `update_from_output()` | 同线程 | `vllm/v1/engine/core.py:606`，`vllm/v1/engine/core.py:786`，`vllm/v1/engine/core.py:850-852` |
| H/G → I | `model_executor.execute_model(..., non_block=True)` 与 `sample_tokens(..., non_block=True)`，返回 Future；UniProc 下其实是**同线程直接调用**，“非阻塞”来自 GPU 异步执行 | 忙循环在 `_wait_for_output(future)` 处等 GPU→CPU 拷贝完成 | `vllm/v1/engine/core.py:788-790`，`vllm/v1/engine/core.py:812-814`，`vllm/v1/executor/uniproc_executor.py:108-117`，`vllm/v1/worker/gpu/async_utils.py:192-193` |
| G 忙循环 → G 输出线程 | `output_queue.put_nowait((client_index, EngineCoreOutputs))` | 输出线程阻塞在 `output_queue.get()` | `vllm/v1/engine/core.py:1889-1890`，`vllm/v1/engine/core.py:2340` |
| G 输出线程 → F | ZMQ：引擎 **PUSH** → 前端 **PULL** | 跨进程 | `vllm/v1/engine/core.py:2324`，`vllm/v1/engine/core_client.py:716-718` |
| F 内部 | `EngineCoreOutputQueueTask` 收帧 → 解码 → `outputs_queue`（asyncio.Queue） | `output_handler` 在 `await outputs_queue.get()` 上等 | `vllm/v1/engine/core_client.py:1249-1302`，`vllm/v1/engine/core_client.py:1305-1314` |
| F → E | `output_handler` 调 `output_processor.process_outputs(...)` | 同事件循环 | `vllm/v1/engine/async_llm.py:894`，`vllm/v1/engine/async_llm.py:919-924` |
| E → D（每请求） | `RequestOutputCollector.put()` + `asyncio.Event` | `generate()` 在 `q.get_nowait() or await q.get()` 上等 | `vllm/v1/engine/output_processor.py:864-866`，`vllm/v1/engine/async_llm.py:809` |
| D → B → A → 客户端 | `generate()` `yield RequestOutput` → B 生成 `"data: ...\n\n"` 字符串 → `StreamingResponse` 写出 | Starlette 迭代生成器 | `vllm/v1/engine/async_llm.py:815-816`，`vllm/entrypoints/openai/chat_completion/serving.py:515`，`vllm/entrypoints/openai/chat_completion/api_router.py:82-85` |
| E → F → G（反向小回路） | stop string 命中后，`output_handler` 发 ABORT 让引擎停算 | — | `vllm/v1/engine/async_llm.py:932-936` |

---

## 4. 线程 / 任务清单（并发模型）

**API Server 进程**

- asyncio 事件循环（uvloop，`vllm/entrypoints/cli/serve.py:156`）：所有 HTTP handler 协程、每请求的 `generate()` 协程、以及两个常驻 task：
  - `output_handler`（`vllm/v1/engine/async_llm.py:965`）
  - `EngineCoreOutputQueueTask`（`vllm/v1/engine/core_client.py:1301-1302`）
- Renderer 的分词线程池 `_executor`（大小 `renderer_num_workers`，`vllm/renderers/base.py:149-151`）和单线程的多模态线程池 `_mm_executor`（`vllm/renderers/base.py:153-155`）。
- ZMQ Context 自带 2 个 IO 线程（`vllm/v1/engine/core_client.py:657`）。

**EngineCore 进程**（由 `CoreEngineProcManager` 用 multiprocessing 起，`target=EngineCoreProc.run_engine_core`，`vllm/v1/engine/utils.py:303-310`）

- 主线程：`run_busy_loop()`（`vllm/v1/engine/core.py:1763`），调度器、执行器、Worker、ModelRunner 全在这个线程里。
- 输入线程 `process_input_sockets`（daemon，`vllm/v1/engine/core.py:1436-1446`）。
- 输出线程 `process_output_sockets`（daemon，`vllm/v1/engine/core.py:1448-1457`）。
- GPU 上：主 CUDA stream 跑前向/采样，另一条 `copy_stream` 做结果 D2H 拷贝（`vllm/v1/worker/gpu/async_utils.py:147-150`）。

**谁拥有什么**：调度器状态只被 EngineCore 主线程改；输入线程只做反序列化和 `preprocess_add_request`（构造 `Request`、语法初始化），注释明说这样无竞态（`vllm/v1/engine/core.py:1277-1279`，`vllm/v1/engine/core.py:1305-1309`）。API 进程里 `OutputProcessor` 只在事件循环线程里被访问。

---

## 5. 逐步推演：一个具体请求走一遍

**设定（以下数值除特别注明外都是为讲解设的，不是代码常量）**

- 客户端请求：
  ```json
  POST /v1/chat/completions
  {"model": "my-chat-model",
   "messages": [{"role": "user", "content": "法国的首都是哪里？"}],
   "stream": true, "max_tokens": 8}
  ```
- 没带 `X-Request-Id` 头。
- 假设套模板 + 分词后 prompt 为 **20 个 token**（设的）。
- 假设模型生成 `["法国", "的", "首都", "是", "巴黎", "。", <eos>]` 共 7 个 token（设的，按每词一个 token 简化）。
- 假设此刻引擎里没有别的请求、无前缀缓存命中（设的）。

### 步骤 1：HTTP 进入（部件 A）

- FastAPI 把 body 解析为 `ChatCompletionRequest`，进入路由 `create_chat_completion`（`vllm/entrypoints/openai/chat_completion/api_router.py:43-59`）。
- 外面套着 `@with_cancellation`：它同时起两个 task——一个跑 handler，一个监听 `http.disconnect`，谁先完成就取消另一个（`vllm/entrypoints/serve/utils/api_utils.py:97-104`）。
- handler 调 `handler.create_chat_completion(request, raw_request)`（`vllm/entrypoints/openai/chat_completion/api_router.py:67`）。

### 步骤 2：协议适配 + 渲染（部件 B、C）

- 空 `messages` 直接拒绝（`vllm/entrypoints/openai/chat_completion/serving.py:306-311`）。
- `render_chat_request`：先检查模型名/LoRA，再 `_preflight`，然后交给 `OnlineRenderer.render_chat`（`vllm/entrypoints/openai/chat_completion/serving.py:281-288`）。
- 非 harmony 模型走 `preprocess_chat`（`vllm/renderers/online_renderer.py:309-329`）→ 最终到 `BaseRenderer.render_chat_async`：
  1. `render_messages_async`：解析消息、套 chat template（HF 渲染器里 `apply_chat_template` 被包装成线程池任务，`vllm/renderers/hf.py:1060-1062`）（`vllm/renderers/base.py:1426-1435`）；
  2. `tokenize_prompts_async`：分词，也在线程池里（`vllm/renderers/base.py:1437`，`vllm/renderers/base.py:162-164`）；
  3. `process_for_engine_async`：整理成引擎输入（`vllm/renderers/base.py:1443-1450`）。
- 产物：`engine_inputs = [{"type": "token", "prompt_token_ids": [20 个 id], ...}]`（`TokensInput` 的形状见 `vllm/inputs/engine.py:56`）。

### 步骤 3：定请求 ID、采样参数，拿到“结果生成器”（部件 B → D）

- request_id：没有 `X-Request-Id` 头就用随机 uuid（`vllm/entrypoints/serve/engine/serving.py:140-145`），加前缀 → 例如 `chatcmpl-9f1c…`（`vllm/entrypoints/openai/chat_completion/serving.py:334-336`）。
- `max_tokens = get_max_tokens(...)`：取 `max_model_len - prompt_len`、用户的 `max_tokens` 等之间的最小值（`vllm/entrypoints/openai/chat_completion/serving.py:363-372`，`vllm/entrypoints/serve/utils/api_utils.py:253-264`）。本例 = 8。
- `request.to_sampling_params(...)` 生成 `SamplingParams`；因为 `stream=true`，`output_kind = DELTA`（`vllm/entrypoints/openai/chat_completion/protocol.py:821-822`）。
- 调 `self.engine_client.generate(engine_input, sampling_params, sub_request_id, ...)`（`vllm/entrypoints/openai/chat_completion/serving.py:411-423`）。**`generate` 是 async generator，此时只创建了生成器对象，请求还没发出去。**
- 因为 `stream=true`，B 直接返回 `chat_completion_stream_generator(...)`（又一个生成器）（`vllm/entrypoints/openai/chat_completion/serving.py:430-441`）。
- A 把它包成 `StreamingResponse(media_type="text/event-stream")` 返回（`vllm/entrypoints/openai/chat_completion/api_router.py:80-85`）。Starlette 开始迭代这个生成器时，链条才真正启动。

### 步骤 4：`AsyncLLM.add_request` —— 变成 `EngineCoreRequest`（部件 D）

`generate()` 第一次被迭代时调用 `add_request`（`vllm/v1/engine/async_llm.py:787-801`）：

1. 引擎已死则立刻 `EngineDeadError`（`vllm/v1/engine/async_llm.py:432-433`）。
2. 输入是已渲染的 dict（有 `"type"` 键）→ 走同步的 `input_processor.process_inputs`，注释写明「no blocking preprocessing needed」（`vllm/v1/engine/async_llm.py:495-510`）。
3. `process_inputs` 里：校验参数和 LoRA（`vllm/v1/engine/input_processor.py:383-384`）；**克隆** `SamplingParams`（`vllm/v1/engine/input_processor.py:456`）；用 generation_config 和 tokenizer 补 EOS 等字段（`vllm/v1/engine/input_processor.py:503-508`）；构造 `EngineCoreRequest`（`vllm/v1/engine/input_processor.py:552-564`）。
4. `assign_request_id`：把外部 ID 存到 `external_req_id`，内部 ID 改成 `外部ID-8位随机字符`（`vllm/v1/engine/input_processor.py:357-365`）。本例：`chatcmpl-9f1c…-a1b2c3d4`（后缀是设的）。
5. 确保 `output_handler` 已启动（`vllm/v1/engine/async_llm.py:537-540`）。
6. 新建这个请求的信箱 `RequestOutputCollector(DELTA, request_id)`（`vllm/v1/engine/async_llm.py:543`）。
7. `n == 1` → `_add_request`：准入检查（默认无上限，见 §6.4）→ **先**在 `OutputProcessor` 登记（建去分词器等），**再** `await engine_core.add_request_async(request)`（`vllm/v1/engine/async_llm.py:589-598`）。

此时 `EngineCoreRequest` 大致是（字段见 `vllm/v1/engine/__init__.py:175-213`）：

```
request_id      = "chatcmpl-9f1c…-a1b2c3d4"
external_req_id = "chatcmpl-9f1c…"
prompt_token_ids= int32 ndarray，长度 20
sampling_params = SamplingParams(max_tokens=8, output_kind=DELTA, eos_token_id=<模型的>…)
arrival_time, priority=0, client_index=0, …
```

### 步骤 5：过 ZMQ 进入 EngineCore（部件 F → G）

- `add_request_async`：填 `client_index`，把 prompt token 列表换成 int32 ndarray（较长时走 out-of-band 帧，`vllm/v1/engine/core_client.py:86-93`），发送 `(引擎身份 b"\x00\x00", b"\x00"=ADD, msgpack 帧…)`（`vllm/v1/engine/core_client.py:1374-1377`，`vllm/v1/engine/core_client.py:1325`，`vllm/v1/engine/__init__.py:382`，引擎身份是 DP rank 的 2 字节小端编码 `vllm/v1/engine/core_client.py:785-787`）。
- EngineCore **输入线程**：`poller.poll()` → `recv_multipart` → 识别 ADD → 解码为 `EngineCoreRequest` → `preprocess_add_request` 构造调度器内部的 `Request`（计算前缀缓存块哈希、初始化结构化输出语法）→ `input_queue.put_nowait((ADD, (Request, wave)))`（`vllm/v1/engine/core.py:2254-2303`，`vllm/v1/engine/core.py:1300-1311`）。
- 预处理失败 / 参数校验失败不会杀死引擎，而是给该请求回一个错误输出（`vllm/v1/engine/core.py:2276-2283`）。

### 步骤 6：忙循环第 1 轮 —— 调度 prefill，交给 GPU，**不等结果**（G、H、I）

忙循环每轮：`_process_input_queue()` → `_process_engine_step()`（`vllm/v1/engine/core.py:1816-1823`）。

1. `_process_input_queue`：原本空闲时阻塞在 `input_queue.get()`；拿到 ADD → `_handle_client_request` → `EngineCore.add_request` → `scheduler.add_request`（进 waiting 队列）（`vllm/v1/engine/core.py:1857-1861`，`vllm/v1/engine/core.py:1990-1994`，`vllm/v1/engine/core.py:606`，`vllm/v1/core/sched/scheduler.py:3132-3133`）。
2. `step_fn` = `step_with_batch_queue`：
   - `scheduler.schedule()`：先排 RUNNING，再排 WAITING（`vllm/v1/core/sched/scheduler.py:778`，`vllm/v1/core/sched/scheduler.py:1040`）。本例只有一个请求：查前缀缓存（`vllm/v1/core/sched/scheduler.py:1229`）→ 分配 KV block（`vllm/v1/core/sched/scheduler.py:1489`）→ 移入 running（`vllm/v1/core/sched/scheduler.py:1598`）→ 这一步给它排 **20 个 token**（整段 prompt）。
   - `AsyncScheduler._update_after_schedule`：给这个请求记 1 个“输出占位符”（`num_output_placeholders += 1`），表示“下一步会多出一个还不知道值的 token”（`vllm/v1/core/sched/async_scheduler.py:45-48`）。
   - `execute_model(scheduler_output, non_block=True)`（`vllm/v1/engine/core.py:788-790`）→ UniProc 直接在本线程调 Worker → `GPUModelRunner.execute_model`：同步请求状态、准备输入、启动前向（CUDA graph 或 eager），把中间状态存进 `execute_model_state` 后返回 `None`（`vllm/v1/worker/gpu/model_runner.py:1880-1883`，`vllm/v1/worker/gpu/model_runner.py:2249-2262`，`vllm/v1/worker/gpu/model_runner.py:2275`）。**此时 GPU 内核只是被排进了 stream，CPU 已经回来了。**
   - 无结构化输出 → 立刻 `sample_tokens(grammar_output, non_block=True)`（`vllm/v1/engine/core.py:800-814`）→ ModelRunner 排采样内核，创建 `AsyncOutput`：在 `copy_stream` 上排“采样结果 GPU→CPU”拷贝并记录 event（`vllm/v1/worker/gpu/model_runner.py:2330-2332`，`vllm/v1/worker/gpu/model_runner.py:2377-2387`，`vllm/v1/worker/gpu/async_utils.py:147-150`，`vllm/v1/worker/gpu/async_utils.py:190`）。UniProc 把它包成 `AsyncOutputFuture`（`vllm/v1/executor/uniproc_executor.py:109-111`）。
   - 放进 `batch_queue`；队列长度 1 < 2 且还有活 → **直接返回 `None`，不等结果**（`vllm/v1/engine/core.py:820-828`）。

### 步骤 7：忙循环第 2 轮 —— 先排下一步，再收第一步的结果

1. 输入队列空，`has_work()` 为真（有请求），不阻塞（`vllm/v1/engine/core.py:1801-1807`）。
2. `schedule()` 再排一次：第 1 轮排完后 `num_computed_tokens` 已被加到 20（`vllm/v1/core/sched/scheduler.py:1892`）；running 请求的待算量 = `num_tokens_with_spec + num_output_placeholders - num_computed_tokens` = 20 + 1 − 20 = **1**（`vllm/v1/core/sched/scheduler.py:824-828`），于是给它排 1 个 decode token，并再加一个占位符。CPU 并不知道第一个 token 是什么——GPU 端的 ModelRunner 直接从自己保存的 `last_sampled_tokens` 读出这个 token 当输入（`vllm/v1/worker/gpu/model_runner.py:1614-1619`）。
3. 再次 `execute_model` + `sample_tokens`，入队；此时队列长度 2 == 上限 → 不再提前返回，`batch_queue.pop()` 取出**最早**的那批（第 1 步）（`vllm/v1/engine/core.py:836-837`）。
4. `_wait_for_output(future)`：UniProc 的 Future 没有 `poll` 方法，直接 `future.result()` → `AsyncOutput.get_output()` → `copy_event.synchronize()` 等拷贝完成，把 ndarray 转成 `sampled_token_ids=[[<"法国">]]`（`vllm/v1/engine/core.py:1958-1962`，`vllm/v1/engine/core.py:1982`，`vllm/v1/executor/uniproc_executor.py:38-48`，`vllm/v1/worker/gpu/async_utils.py:192-203`）。
5. **先处理积压的 abort**，再 `scheduler.update_from_output()`（`vllm/v1/engine/core.py:847-852`）。
6. `update_from_output`：`_update_request_with_output` 把 token 追加到 `Request`，消掉一个占位符，调用 `check_stop`（EOS / stop_token_ids / 长度上限）（`vllm/v1/core/sched/scheduler.py:2933-2959`，`vllm/v1/core/sched/utils.py:98-119`）。未结束 → 生成 `EngineCoreOutput(request_id, new_token_ids=[<"法国">], finish_reason=None, …)`，按 `request.client_index` 归类（`vllm/v1/core/sched/scheduler.py:2620-2627`）。
7. `_process_engine_step` 把 `{0: EngineCoreOutputs(outputs=[…])}` 放进 `output_queue`（`vllm/v1/engine/core.py:1887-1890`）。
8. **输出线程**：取出 → msgpack 编码（复用 bytearray 缓冲）→ PUSH 发出（`vllm/v1/engine/core.py:2340-2371`）。

> 时间轴上的含义：第 1 个 token 回到 CPU 时，第 2 个 token 的计算已经在 GPU 上排好了。这就是 async scheduling 的“CPU 提前一步”。

### 步骤 8：结果回到 API 进程，变成文字（部件 F → E → D）

1. `EngineCoreOutputQueueTask`：`await output_socket.recv_multipart()` → `decoder.decode(frames)` → 不是 utility 回包 → `outputs_queue.put_nowait(outputs)`（`vllm/v1/engine/core_client.py:1252-1254`，`vllm/v1/engine/core_client.py:1294-1295`）。
2. `output_handler`：`await engine_core.get_output_async()` 拿到 `EngineCoreOutputs`，按 `VLLM_V1_OUTPUT_PROC_CHUNK_SIZE` 切片交给 `output_processor.process_outputs`（`vllm/v1/engine/async_llm.py:894-924`）。
3. `process_outputs`：查 `request_states`（已 abort 的忽略，`vllm/v1/engine/output_processor.py:762-765`）→ 增量去分词 `detokenizer.update(new_token_ids, …)`，顺便查 stop string（`vllm/v1/engine/output_processor.py:826-836`）→ `make_request_output(...)` 生成 `RequestOutput`（DELTA 模式下 `outputs[0].text = "法国"`）→ `req_state.queue.put(request_output)`（`vllm/v1/engine/output_processor.py:852-866`）。
4. 信箱 `put` 置位 `asyncio.Event`（`vllm/v1/engine/output_processor.py:70-74`）。
5. 该请求的 `generate()` 协程醒来，`yield out`（`vllm/v1/engine/async_llm.py:806-816`）。

### 步骤 9：写回客户端（部件 B → A）

- `chat_completion_stream_generator_raw` 收到**第一个** `RequestOutput` 时，先为每个 choice 发一个只带 `role` 的 chunk（`content=""`），再处理内容；之后每个 `RequestOutput` 生成一个 `ChatCompletionStreamResponse`（`vllm/entrypoints/openai/chat_completion/serving.py:600-636`）。
- 外层把它序列化成 `f"data: {data}\n\n"`（`vllm/entrypoints/openai/chat_completion/serving.py:509-515`）。客户端收到：
  ```
  data: {"id":"chatcmpl-9f1c…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""}…}]}
  data: {"id":"chatcmpl-9f1c…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"法国"}…}]}
  ```
  （JSON 字段为示意。chunk 的 `id` 取自 B 层自己生成的 `request_id` 变量（`vllm/entrypoints/openai/chat_completion/serving.py:625`），所以客户端看到的是不带 8 位随机后缀的外部 ID。）

### 步骤 10：循环直到结束

- 步骤 7–9 每轮重复：“的”“首都”“是”“巴黎”“。”依次回来。
- 第 7 个 token 是 `<eos>`：`check_stop` 命中 `eos_token_id` → `FINISHED_STOPPED`（`vllm/v1/core/sched/utils.py:104-107`）→ `finish_reason = "stop"`（`vllm/v1/request.py:461-462`）→ 调度器 `_free_request` 释放 KV block（`vllm/v1/core/sched/scheduler.py:2580-2587`）。
- 前端：去分词时，以 stop 结束且未要求包含 stop 串时，最后那个 stop token 不进文字（`vllm/v1/engine/detokenizer.py:153-157`）；`RequestOutput.finished=True`；`_finish_request` 清理状态（`vllm/v1/engine/output_processor.py:872-880`）。
- `generate()` 退出循环，`finally: q.close()`（`vllm/v1/engine/async_llm.py:866-868`）。
- 流式生成器发出最后的 chunk（含 `finish_reason`），然后 `"data: [DONE]\n\n"`（`vllm/entrypoints/openai/chat_completion/serving.py:520`）。

若改成 `max_tokens=4`（设的）：第 4 个 token 回来时 `num_output_tokens >= max_tokens` → `FINISHED_LENGTH_CAPPED` → `finish_reason="length"`（`vllm/v1/core/sched/utils.py:114-119`）。

**非流式（`stream=false`）只在最后一段不同**：`SamplingParams.output_kind = FINAL_ONLY`（`vllm/entrypoints/openai/chat_completion/protocol.py:821-822`），B 直接 `await chat_completion_full_generator`，内部 `async for res in result_generator: final_res = res`，结束后拼一个 `ChatCompletionResponse`，A 用 `JSONResponse` 返回（`vllm/entrypoints/openai/chat_completion/serving.py:443-453`，`vllm/entrypoints/openai/chat_completion/serving.py:986-988`，`vllm/entrypoints/openai/chat_completion/api_router.py:74-78`）。

---

## 6. 部件说明卡

### 6.1 A：HTTP 层（uvicorn + FastAPI 路由 + `with_cancellation`）

**做什么**：`vllm serve` → `run_server` → `build_async_engine_client`（创建 `AsyncLLM`，此时 EngineCore 子进程被拉起）→ `build_app` + `init_app_state`（创建 `OpenAIServingChat` 等并挂到 `app.state`）→ `serve_http`（uvicorn）（`vllm/entrypoints/launchers/api_server/entry.py:190-196`，`vllm/entrypoints/launchers/api_server/entry.py:132-141`，`vllm/entrypoints/generate/api_router.py:149-151`）。

**设计 / 为什么**

- 断连检测为什么不用 `request.is_disconnected`：【注释】「This does _not_ use request.is_disconnected, which does not work with middleware.」（`vllm/entrypoints/serve/utils/api_utils.py:72-73`）。改用“handler task 与 disconnect 监听 task 赛跑”的模式。
- 这个装饰器只能用在 body 已解析的 handler 上：【注释】「This decorator is unsafe to use elsewhere, as it will consume and throw away all incoming messages for the request while it looks for a disconnect message.」（`vllm/entrypoints/serve/utils/api_utils.py:81-83`）。

**容易看错**

- 流式情况下，`with_cancellation` 只保护到 handler 返回 `StreamingResponse` 为止；之后断连由 `StreamingResponse` 自己监听：【注释】「In the case where a `StreamingResponse` is returned by the handler, this wrapper will stop listening for disconnects and instead the response object will start listening for disconnects.」（`vllm/entrypoints/serve/utils/api_utils.py:85-87`）。断连后生成器被取消 → `generate()` 捕获 `CancelledError/GeneratorExit` → `abort`（`vllm/v1/engine/async_llm.py:821-826`）。
- `vllm/entrypoints/openai/api_server.py` 现在只是转发壳，并已标记废弃（`vllm/entrypoints/openai/api_server.py:24-30`）；真入口在 `vllm/entrypoints/launchers/api_server/entry.py`。

**值得点进去的机制**：中间件与指标（`load_aware_call`、SSE keep-alive `with_sse_keep_alive`）；入口 `vllm/entrypoints/serve/utils/api_utils.py:186`。

### 6.2 B：`OpenAIServingChat`

**做什么**：协议层——校验、渲染、计算 `max_tokens`、构造 `SamplingParams`、调 `generate`、把 `RequestOutput` 流翻译成 OpenAI chunk（含 reasoning/tool-call 解析）。

**设计 / 为什么**

- 空 messages 在这里拒绝而不是在 pydantic 模型上：【注释】「This is checked here rather than on the request model because internal callers (renderers, adapters) construct ChatCompletionRequest directly.」——原文跨行，逐字见 `vllm/entrypoints/openai/chat_completion/serving.py:303-305`；它解释的是“否则会在完整生成之后才在 `get_chat_request_role()` 里 IndexError”（同处 301-303 行）。
- chat 路径只允许一个 engine input：`assert len(generators) == 1`（`vllm/entrypoints/openai/chat_completion/serving.py:427`）。多 prompt 的 `_i` 后缀逻辑（`vllm/entrypoints/openai/chat_completion/serving.py:357-361`）在 chat 路径里实际不会触发。【推断】依据：同一循环后立即断言只有一个生成器。

**容易看错（时间上不连续）**

- `engine_client.generate(...)` 只是创建 async generator；**真正 `add_request` 发生在第一次迭代时**。流式时，这个迭代发生在 handler 已经返回、Starlette 开始发送响应之后。因此“请求被提交给引擎”在时间上晚于路由函数返回。依据：`generate` 定义为含 `yield` 的 `async def`（`vllm/v1/engine/async_llm.py:740`，`vllm/v1/engine/async_llm.py:787`，`vllm/v1/engine/async_llm.py:816`）；流式分支直接 `return self.chat_completion_stream_generator(...)` 而不 await（`vllm/entrypoints/openai/chat_completion/serving.py:430-441`）。这是 Python 语义，不需要额外证据。

**值得点进去**：流式 chunk 组装与 reasoning / tool parser（`ChatStreamProcessor`），入口 `vllm/entrypoints/openai/chat_completion/serving.py:522`。

### 6.3 C：Renderer（聊天模板 + 分词）

**做什么**：把 `messages` 变成 token id。`OnlineRenderer.render_chat`（`vllm/renderers/online_renderer.py:242`）→ `BaseRenderer.render_chat_async`（`vllm/renderers/base.py:1412-1452`）。

**设计 / 为什么**

- 阻塞的模板渲染、分词都用 `make_async(..., executor=self._executor)` 丢进线程池（`vllm/renderers/base.py:162-165`，`vllm/renderers/hf.py:1060-1062`）。为什么：【推断】HF tokenizer 和 Jinja 模板渲染是同步 CPU 工作，放在事件循环里会卡住所有并发请求的收发；注释也说明「Offload tokenization to the thread pool.」（`vllm/renderers/base.py:158`）。
- 多模态预处理单独一个**单线程**池：【注释】「Separate single-worker executor so tokenization never queues behind MM preprocessing; must stay single-worker per #38418 (P0/P1 order).」（`vllm/renderers/base.py:153-154`）。
- 为什么要 `_SwappableExecutor`：【注释】「`make_async` captures the executor object at wrap time.」（`vllm/renderers/base.py:86`）——直接换掉线程池会让已包装的函数绑在一个已关闭的池上。

**容易看错**：在这一版里，**分词发生在 Renderer，而不是 `InputProcessor`**。`InputProcessor` 对已渲染输入不再分词；直接传原始字符串的旧路径会打 deprecation 警告（`vllm/v1/engine/input_processor.py:401-406`，`vllm/v1/engine/async_llm.py:472-478`）。老文章里“Processor 负责分词”的说法已过时。

**值得点进去**：chat template 解析与多模态处理缓存（P0/P1 缓存）。入口 `vllm/renderers/hf.py:1237`，`vllm/renderers/base.py:1296`。

### 6.4 D：`AsyncLLM` + `InputProcessor`

**做什么**：引擎在 API 进程里的门面，实现 `EngineClient` 接口（`vllm/v1/engine/async_llm.py:94`）。构造时依次建 renderer、`InputProcessor`、`OutputProcessor`、`AsyncMPClient`（后者会拉起 EngineCore 进程）（`vllm/v1/engine/async_llm.py:168-202`）。

**设计 / 为什么**

- **请求 ID 加随机后缀**：【注释】「Replace the externally supplied request ID with an internal request ID that adds 8 random characters in order to ensure uniqueness.」（`vllm/v1/engine/input_processor.py:349-350`）。关掉它的环境变量会打警告，说明重复的外部 ID 的风险（警告文字由相邻字符串拼接而成，后半段逐字为）：「request IDs may cause failures and/or subtle correctness errors.」（`vllm/v1/engine/input_processor.py:361-362`）。【提交】引入随机后缀的是 `f790068600`，标题「[Core] Add a random suffix to frontend-provided request IDs (#27987)」（改动 `vllm/v1/engine/input_processor.py`），提交说明正文无更多理由；`79c7e09235`「Add temporary, off-by-default `VLLM_DISABLE_REQUEST_ID_RANDOMIZATION` workaround」（改动 `vllm/v1/engine/input_processor.py`）为 KV connector 场景留了关闭开关。客户端可通过 `X-Request-Id` 头自带 ID（`vllm/entrypoints/serve/engine/serving.py:140-143`），所以重复是现实问题。
- **先登记、后发送**：【注释】「Register locally before the first await so concurrent tasks see this request.」（`vllm/v1/engine/async_llm.py:594`）。【推断】如果先发送，引擎回包可能早于登记到达，`process_outputs` 会因查不到 `req_state` 把它当已 abort 丢掉（`vllm/v1/engine/output_processor.py:762-765`）。
- **`SamplingParams` 要克隆**：【注释】「Resolve on the request-local copy: `params` may be shared across prompts, requests and even engines by the caller.」（`vllm/v1/engine/input_processor.py:457-458`）。
- **`output_handler` 惰性启动**：【注释】「We start the output_handler on the first call to add_request() so we can call __init__ before the event loop, which enables us to handle startup failure gracefully in the OpenAI server.」（`vllm/v1/engine/async_llm.py:537-539`）。同时在已有事件循环时构造期就会启动（`vllm/v1/engine/async_llm.py:220-225`）。
- **`generate()` 的取值手法**：【注释】「Note: drain queue without await if possible (avoids task switching under load which helps performance).」（`vllm/v1/engine/async_llm.py:807-808`）。
- **准入控制**：`check_admission` 在 `max_num_queued_reqs` / `max_num_queued_tokens` 超限时返回 503（`vllm/v1/engine/async_llm.py:326-337`）；两者默认 `None` 即不限（`vllm/config/scheduler.py:118`）。【文档】「This bounds vLLM's otherwise unbounded request queue and is primarily a coarse capacity valve.」（`vllm/config/scheduler.py:122-123`）。

**故障模型**：`generate()` 对不同异常分流——客户端断开 → abort 并重新抛出；引擎已死 → 不 abort；校验/准入错误 → abort 后抛给 HTTP 层；未知错误 → abort 并包成 `EngineGenerateError`（`vllm/v1/engine/async_llm.py:821-865`）。

**n > 1**：在这里“扇出”成 n 个子请求，共享一个信箱（`vllm/v1/engine/async_llm.py:552-579`），子请求 ID 由 `ParentRequest.get_child_info` 生成。入口 `vllm/v1/engine/parallel_sampling.py`。

### 6.5 E：`OutputProcessor` + `RequestOutputCollector` + `output_handler`

**做什么**：常驻 task 把引擎输出转成每请求的 `RequestOutput`。

**设计 / 为什么**

- **只循环一次全 batch**：【注释】「vLLM V1 minimizes the number of python loops over the full batch to ensure system overheads are minimized. This is the only function that should loop over EngineCoreOutputs.」（`vllm/v1/engine/output_processor.py:751-753`）。
- **分块处理、中间让出事件循环**：【注释】「Split outputs into chunks of at most VLLM_V1_OUTPUT_PROC_CHUNK_SIZE, so that we don't block the event loop for too long.」（`vllm/v1/engine/async_llm.py:901-903`，`vllm/v1/engine/async_llm.py:928-930`）。【提交】该变量由 `aea94362c9`「[Frontend][V1] Online serving performance improvements (#12287)」引入（改动 `vllm/envs.py`），提交正文为空。
- **信箱是单槽 + 合并，而不是无界队列**：【注释】「When streaming deltas, RequestOutputs are merged if the producer gets ahead of the consumer.」（`vllm/v1/engine/output_processor.py:58-59`），合并逻辑见 `vllm/v1/engine/output_processor.py:75-80`。【提交】`9d72daf4ce`「[V1][Perf] Simpler request output queues (#15156)」（改动 `vllm/v1/engine/output_processor.py`），正文无理由。【推断】好处：慢客户端不会让单个请求的输出无限堆积；每次唤醒消费者拿到的是“到目前为止的所有增量”，减少 task 切换次数。
- **stop string 在前端判**：调度器的 `check_stop` 只看 token 级条件（EOS、`stop_token_ids`、长度）（`vllm/v1/core/sched/utils.py:98-119`）；文本 stop 串由前端去分词器在 `update()` 里判断（`vllm/v1/engine/detokenizer.py:178-201`）。命中后前端把请求标为完成，并通过 `reqs_to_abort` 让引擎停算（`vllm/v1/engine/output_processor.py:881-885`，`vllm/v1/engine/async_llm.py:932-936`）。【推断】原因：只有前端持有增量文本；把去分词搬进引擎会占用驱动 GPU 的那个进程的 CPU 时间。代价是引擎可能已多算了 1～2 步（async scheduling 下更明显），这些多余输出会被 `_trim_surplus_tokens` / 查不到 `req_state` 丢弃。
- **流式时为什么文字会“晚几个字”**：有 stop 串且不包含在输出中时，会扣住最长 stop 串长度减 1 个字符不发（`vllm/v1/engine/detokenizer.py:131-134`，`vllm/v1/engine/detokenizer.py:211`），防止把半截 stop 串发给客户端。

**容易看错**：`process_outputs` 在 AsyncLLM 模式下**返回值里没有 `request_outputs`**——全被塞进了各自信箱（`vllm/v1/engine/async_llm.py:925-926`）。返回列表只给同步的 `LLMEngine` 用（`vllm/v1/engine/output_processor.py:867-869`）。

**值得点进去**：增量去分词（`DecodeStream`，`vllm/v1/engine/detokenizer.py:225-241`）、logprobs 处理、`stream_interval` 合批（`vllm/v1/engine/output_processor.py:382-393`，配置说明 `vllm/config/scheduler.py:284-288`）、指标统计。

### 6.6 F：`AsyncMPClient`（ZMQ 客户端）

**做什么**：在构造时绑定 ROUTER（发请求）和 PULL（收结果）两个 socket，调 `launch_core_engines` 拉起 EngineCore 进程，然后**同步等待**每个引擎发来的 ready 消息（`vllm/v1/engine/core_client.py:708-746`，`vllm/v1/engine/core_client.py:789-807`）。

**设计 / 为什么**

- **为什么是 ROUTER**：每个引擎用自己的身份（DP rank 的 2 字节）连接，ROUTER 可以按身份把消息发给指定引擎（`vllm/v1/engine/core_client.py:784-787`，`vllm/v1/engine/core_client.py:1335`）。【推断】为 DP>1 时一个前端对多个引擎做准备，单引擎时只是退化情形。
- **引擎必须先说话**：【注释】「Send initial message to each input socket - this is required before the front-end ROUTER socket can send input messages back to us.」（`vllm/v1/engine/core.py:2241-2243`）。这条 ready 消息同时就是启动握手的完成信号（`vllm/v1/engine/core_client.py:805-807`）。
- **收结果放独立 task**：【注释】「Perform IO in separate task to parallelize as much as possible.」（`vllm/v1/engine/core_client.py:1233`）。task 内出错会把异常塞进 `outputs_queue`，由 `output_handler` 重新抛出并让服务器关停（`vllm/v1/engine/core_client.py:1296-1299`，`vllm/v1/engine/core_client.py:1307-1313`）。
- **避免循环引用**：两个常驻 task 都刻意不持有 client/AsyncLLM 的强引用（`vllm/v1/engine/core_client.py:1234`，`vllm/v1/engine/async_llm.py:875-876`）。
- **零拷贝**：msgpack 编码器对大数组走单独帧（【注释】「By default, arrays below 256B are serialized inline Larger will get sent via dedicated messages.」`vllm/v1/serial_utils.py:141-142`）；prompt token 转 int32 ndarray 正是为此（`vllm/v1/engine/core_client.py:86-88`）。【提交】`fd75a7a920`「[Perf][Core] Keep prompt token ids as int32 buffers (#62702)」（改动 `vllm/v1/engine/core_client.py`）。
- 启动超时：等 ready 超过 `VLLM_ENGINE_READY_TIMEOUT_S` 抛 `TimeoutError`，提示通常是权重加载慢（`vllm/v1/engine/core_client.py:793-804`）。

**同一通道上的另一类消息**：utility 调用（如 `get_supported_tasks`）也走 ADD 同一条 ROUTER，用 `call_id` + Future 配对回包（`vllm/v1/engine/core_client.py:1357-1369`，`vllm/v1/engine/core_client.py:1280-1284`）。

### 6.7 G：`EngineCoreProc`（三线程两队列 + 忙循环）

**做什么**：引擎进程主体。`run_engine_core` 构造 `EngineCoreProc` 并进入 `run_busy_loop`（`vllm/v1/engine/core.py:1748`，`vllm/v1/engine/core.py:1763`）。

**设计 / 为什么**

- **为什么拆出单独进程**：【文档】「vLLM V1 uses a multi-process architecture to separate concerns and maximize throughput.」（`docs/design/arch_overview.md:69`）；API 进程负责「performs input processing (tokenization, multi-modal data loading), and streams results back to clients」（`docs/design/arch_overview.md:73`），引擎进程「runs a busy loop that continuously schedules requests and dispatches work to the GPU workers」（`docs/design/arch_overview.md:81`）。【推断】更具体的动机是 Python GIL：分词/去分词/HTTP 与调度/内核发射若同在一个进程，会互相抢 GIL，GPU 就会空等。没有找到直接写明 GIL 是拆进程原因的注释或提交（见 §8）。
- **为什么还要 IO 线程**：【注释】「Background Threads and Queues for IO. These enable us to overlap ZMQ socket IO with GPU since they release the GIL, and to overlap some serialization/deserialization with the model forward pass.」（`vllm/v1/engine/core.py:1430-1433`）。【提交】这段设计最早来自 `6ace6fba2c`「[V1] `AsyncLLM` Implementation (#9826)」（改动 `vllm/v1/engine/core.py`），提交正文为空。
- **请求预处理放在输入线程**：【注释】「This function could be directly used in input processing thread to allow request initialization running in parallel with Model forward」（`vllm/v1/engine/core.py:1274-1275`）。
- **abort 双写**：【注释】「Aborts are added to *both* queues, allows us to eagerly process aborts while also ensuring ordering in the input queue to avoid leaking requests. This is ok because aborting in the scheduler is idempotent.」（`vllm/v1/engine/core.py:2296-2299`）。消费点：每步在 `update_from_output` 之前 `_process_aborts_queue()`（`vllm/v1/engine/core.py:847-849`）。【提交】`dc264bcea1`「[BugFix] Eagerly abort cancelled final-step requests (#29987)」（改动 `vllm/v1/engine/core.py`）讲了原因：「when a kv connector is involved it thinks the request completed successfully rather than being aborted.」，以及为什么只提前处理 abort 而不处理新请求：「we process only aborts, not new requests, since it's preferable for latency to process model outputs before new incoming requests.」【测试】`tests/v1/engine/test_abort_final_step.py:144` 的 `test_abort_during_final_step` 验证该行为。
- **空闲时阻塞、忙时不阻塞**：`has_work()` 为假才阻塞在 `input_queue.get()`；有活时只把队列里已有的消息清空就去跑 step（`vllm/v1/engine/core.py:1847-1873`）。
- **输出缓冲复用**：输出线程对 msgpack 缓冲做复用，并用 `MessageTracker` 判断 zmq 是否已发完（`vllm/v1/engine/core.py:2312-2317`，`vllm/v1/engine/core.py:2356-2371`）。
- **启动后冻结 GC 堆**：【注释】「Mark the startup heap as static so that it's ignored by GC. Reduces pause times of oldest generation collections.」（`vllm/v1/engine/core.py:278-280`）。相关地，`EngineCoreRequest/Output` 都是 `gc=False` 的 msgspec Struct（`vllm/v1/engine/__init__.py:175-180`）。

**故障模型**：

- 执行器失败 → 回调往 `input_queue` 塞 `EXECUTOR_FAILED` → 忙循环处理时 `raise RuntimeError("Executor failed.")`（`vllm/v1/engine/core.py:1366-1368`，`vllm/v1/engine/core.py:2011-2012`）。
- 致命异常 → `_send_engine_dead()`，输出线程发出 `ENGINE_CORE_DEAD` 字节串（`vllm/v1/engine/core.py:1775-1780`，`vllm/v1/engine/core.py:2341-2344`）；前端 `validate_alive` 检到后转为 `EngineDeadError`（`vllm/v1/engine/core_client.py:1253`）。为确保这条消息在 socket 关闭前发出，PUSH socket 设了 linger：【注释】「We must set linger to ensure the ENGINE_CORE_DEAD message is sent prior to closing the socket.」（`vllm/v1/engine/core.py:2319-2320`）。
- 单个请求的校验/预处理错误只回该请求的错误，不杀引擎（`vllm/v1/engine/core.py:2276-2283`）。

**容易看错**：`EngineCoreProc._wait_for_output` 有一套“等 GPU 时继续收新请求”的自旋逻辑（`vllm/v1/engine/core.py:1955-1982`），但它依赖 Future 上的 `poll`/`wait_briefly`。默认 UniProc 的 Future 没有这两个方法，于是**直接阻塞在 `future.result()`**。【测试】`tests/v1/engine/test_inputs_during_step.py:119` 的 `test_future_without_poll_blocks_for_result` 正是验证这个退化行为。【提交】这套逻辑来自 `cbc77173ed`「[Core] Handle new requests and aborts while waiting for model output (#65466)」（改动 `vllm/v1/executor/multiproc_executor.py`），即主要服务多进程执行器。

**值得点进去**：DP 版本 `DPEngineCoreProc`（wave 同步）、优雅关停（drain/abort 两种模式，`vllm/v1/engine/core.py:1907-1953`）、暂停/休眠、容错 sentinel。

### 6.8 H：`Scheduler` / `AsyncScheduler` + `KVCacheManager`

**做什么**：每步在 token 预算内决定每个请求算多少 token，并分配 KV cache block；拿到模型输出后追加 token、判停、产出 `EngineCoreOutput`。

**设计 / 为什么**

- **没有 prefill/decode 两阶段**：【注释】「There's no "decoding phase" nor "prefill phase" in the scheduler.」以及「This is general enough to cover chunked prefills, prefix caching, speculative decoding, and the "jump decoding" optimization in the future.」（`vllm/v1/core/sched/scheduler.py:705`，`vllm/v1/core/sched/scheduler.py:711-713`）。【文档】「The scheduler is **token-budget-based**: each step, it fills a budget of」（`docs/design/arch_overview.md:140`）。
- **async scheduling 与占位符**：【文档】「overlaps CPU scheduling with GPU execution: while the GPU executes step *N*,」（`docs/design/arch_overview.md:177`）以及「the CPU schedules step *N+1*, using placeholders for the tokens that step *N*」（`docs/design/arch_overview.md:178`）。代码：`num_output_placeholders` 在调度后增加（`vllm/v1/core/sched/async_scheduler.py:45-48`），在结果回来时减少（`vllm/v1/core/sched/scheduler.py:2955-2958`）。【文档】配置项说明：「Async scheduling helps to avoid gaps in GPU utilization, leading to better latency and throughput.」（`vllm/config/scheduler.py:271-272`）。【提交】默认开启来自 `c2ff33cc8c`「[Core] Enable async scheduling by default (#27614)」（改动 `vllm/config/vllm.py`），正文无理由。
- **stop 检查顺序**：`check_stop` 先 EOS、再 stop_token_ids、再长度、再 min_tokens、再重复检测（`vllm/v1/core/sched/utils.py:104-140`）；【注释】「Order of checks is important for min_tokens」（`vllm/v1/core/sched/utils.py:122`），并引用若干 PR 号。
- 预算用尽时抢占：被抢占请求释放 KV、回到 waiting、之后**重算**而不是从 CPU 换回（【文档】`docs/design/arch_overview.md:158-168`）。

**容易看错**

- “结束”的判定有两处：调度器（token 级）和前端（stop 串）。只看调度器会漏掉 stop 串导致的结束。
- `update_from_output` 处理的是**上上一轮**排的那一批（因为 batch_queue 深度 2），不是本轮刚排的。

**值得点进去（读者大概率会点）**：`schedule()` 的完整算法（`vllm/v1/core/sched/scheduler.py:702-1817`）、`KVCacheManager.get_computed_blocks` / `allocate_slots`（前缀缓存、分页 KV，`vllm/v1/core/kv_cache_manager.py:278`，`vllm/v1/core/kv_cache_manager.py:391`）、抢占（`vllm/v1/core/sched/scheduler.py:1831`）。

### 6.9 I：`UniProcExecutor` → `Worker` → `GPUModelRunner`

**做什么**：执行器把 `execute_model` / `sample_tokens` 两次调用转给 Worker（`vllm/v1/executor/uniproc_executor.py:119-142`），Worker 转给 ModelRunner（`vllm/v1/worker/gpu_worker.py:1505-1508`，`vllm/v1/worker/gpu_worker.py:1545-1553`）。ModelRunner：`execute_model` 更新常驻请求状态、准备输入、跑前向（`vllm/v1/worker/gpu/model_runner.py:1865-1899`，`vllm/v1/worker/gpu/model_runner.py:2180`，`vllm/v1/worker/gpu/model_runner.py:2219`）；`sample_tokens` 采样、发起异步拷贝、更新 GPU 端的 `last_sampled_tokens`（`vllm/v1/worker/gpu/model_runner.py:2279-2296`，`vllm/v1/worker/gpu/model_runner.py:2413-2424`）。

**设计 / 为什么**

- **前向和采样拆成两次调用**：中间要插入结构化输出的语法 bitmask——它依赖前面步骤的 token，可能要等（`vllm/v1/engine/core.py:800-818`）。【推断】拆开后，没有结构化输出时两次调用紧挨着发出，有结构化输出时可以把采样推迟到拿到前一步结果之后（`deferred_step`）。
- **D2H 拷贝走独立 stream + 可睡眠 event**：【注释】「NOTE(woosuk): We must retain references to the GPU tensors, as the copy operations are performed on a different CUDA stream than the one where the tensors were created.」（原文跨三行，逐字见 `vllm/v1/worker/gpu/async_utils.py:130-132`）；「Blocking (sleep) event to avoid busy-polling the accelerator driver lock.」（`vllm/v1/worker/gpu/async_utils.py:139`）。
- **先建 AsyncOutput 再做后处理**：【注释】「This sequencing may slightly reduce latency as async D2H copy does not need to wait for the postprocess to finish.」（`vllm/v1/worker/gpu/model_runner.py:2416-2417`）。
- **为什么 CPU 不需要知道上一个 token 就能排下一步**：输入 token 直接从 GPU 上的 `last_sampled_tokens` 读（【注释】「Some input token ids are directly read from the last sampled tokens」`vllm/v1/worker/gpu/model_runner.py:1614`）。【文档】「MRV2 instead assumes the core model execution loop is a CUDA stream with no CPU synchronization points. CPU entrypoints queue work onto the stream.」（`docs/design/model_runner_v2.md:47`）。

**容易看错**

- “GPU Worker 进程”：官方文档写「Each GPU is managed by a dedicated worker process.」（`docs/design/arch_overview.md:89`），但 TP=PP=1 时后端被设为 `"uni"`（`vllm/config/parallel.py:1316-1317`），`UniProcExecutor` 在**本进程**内创建 `WorkerWrapperBase` 并加载模型（`vllm/v1/executor/uniproc_executor.py:52-74`）。所以单卡时只有 2 个进程。
- `execute_model(non_block=True)` 在 UniProc 下不是“另一个线程在跑”：`collective_rpc` 直接 `run_method`，结果是已完成的 Future 或 `AsyncOutputFuture`（`vllm/v1/executor/uniproc_executor.py:108-117`）。非阻塞来自 GPU 异步执行，而非 CPU 并发。

**值得点进去**：输入准备与常驻 batch（`docs/design/model_runner_v2.md`，入口 `vllm/v1/worker/gpu/model_runner.py:1494`）、CUDA graph、采样器（`vllm/v1/worker/gpu/sample/sampler.py`）、注意力后端与 KV cache 布局。

---

## 7. 旁路（只点名，不展开）

| 旁路 | 从哪岔出 | 入口 |
|---|---|---|
| TP/PP > 1 | 执行器换成 `MultiprocExecutor`：每 GPU 一个 Worker 进程，调度结果通过共享内存 `MessageQueue` 广播 | `vllm/config/parallel.py:1272-1278`，`vllm/v1/executor/multiproc_executor.py:203`，`vllm/v1/executor/multiproc_executor.py:517` |
| DP > 1 | 客户端换成 `DPLBAsyncMPClient`/`DPAsyncMPClient`，多个 EngineCore 进程 + DP Coordinator 进程 | `vllm/v1/engine/core_client.py:170-183`，`vllm/v1/engine/utils.py:1283` |
| 多 API server | `run_multi_api_server`，多个前端进程共享引擎 | `vllm/entrypoints/cli/serve.py:151-152`，`vllm/entrypoints/cli/serve.py:274` |
| headless / 多节点 | 只起引擎不起 API | `vllm/entrypoints/cli/serve.py:149-150` |
| Rust 前端 | `VLLM_USE_RUST_FRONTEND` 时前端由 Rust 实现 | `vllm/entrypoints/cli/serve.py:65-67` |
| gRPC | `--grpc` | `vllm/entrypoints/cli/serve.py:59-63` |
| 离线 `LLM` 类 | 同步 `LLMEngine` + `SyncMPClient`；`process_outputs` 返回列表而非塞信箱 | `vllm/v1/engine/core_client.py:988`，`vllm/v1/engine/output_processor.py:867-869` |
| 关闭 async scheduling | `step_fn = step`：调度—执行—等结果同步完成 | `vllm/v1/engine/core.py:714-744` |
| n > 1 / beam search | 前端扇出子请求 / `beam_search` | `vllm/v1/engine/async_llm.py:552-579`，`vllm/entrypoints/openai/chat_completion/serving.py:399-409` |
| 结构化输出 | 采样推迟（`deferred_step`）+ 语法 bitmask | `vllm/v1/engine/core.py:815-818`，`vllm/v1/engine/core.py:861-882` |
| PD 分离 / KV connector | 调度器带 connector，KV 由远端传入 | `vllm/v1/engine/core.py:217-218`，`vllm/v1/core/sched/scheduler.py:3765` |
| 多模态 | Renderer 的 MM 处理 + P0/P1 缓存，tensor 可走共享内存 IPC | `vllm/v1/engine/core_client.py:759-765`，`vllm/v1/engine/core.py:2373` |
| 其它 API（completions、responses、embeddings…） | 换 serving 类，汇入同一个 `generate()` / `encode()` | `vllm/entrypoints/generate/api_router.py:157` |

---

## 8. 没查到为什么

- **为什么把 API server 和 EngineCore 拆成两个进程**：只找到文档的笼统说法「separate concerns and maximize throughput」（`docs/design/arch_overview.md:69`）。引入 `EngineCoreProc` 的提交 `6ace6fba2c` 正文为空；“GIL 争用”是本稿推断，没有直接出处。
- **为什么默认开启 async scheduling**：`c2ff33cc8c` 正文为空。配置说明只写了好处（`vllm/config/scheduler.py:271-272`），没有基准数据或权衡说明。
- **为什么 `async_scheduling_depth` 默认是 2**：配置说明解释了 2 的含义（「overlaps one step's host work」「with the next step's GPU」，`vllm/config/scheduler.py:278-280`），没有说为什么不是 3。
- **为什么 `RequestOutputCollector` 用“单槽合并”替代原来的队列**：`9d72daf4ce` 只有标题「Simpler request output queues」，正文为空。
- **为什么输出处理分块大小取当前默认值**：`aea94362c9` 正文为空。
- **为什么请求 ID 要随机化（具体事故）**：`f790068600` 只有标题；代码只说「ensure uniqueness」（`vllm/v1/engine/input_processor.py:349-350`）。具体是哪类故障（KV connector？重复 ID 崩溃？）未在仓库内找到说明。旧提交 `d8e82bc06d`「fix V1 Engine crash while handling requests with duplicate request id」表明重复 ID 曾导致崩溃，但它和随机化之间的关系没有直接证据。
- **为什么 stop string 放在前端判**：没有找到注释或提交说明，本稿理由是【推断】。

---

## 9. 读者最容易困惑的地方（汇总，供讲解员挑选）

1. **单卡没有 Worker 进程**（§6.9）。文档与默认配置不一致。
2. **路由函数返回时请求还没交给引擎**（§6.2）：`generate()` 是惰性生成器。
3. **每个 token 都晚一步才回到 CPU**（§5 步骤 7）：第 N 步结果是在第 N+1 步已发射后才被取出；`update_from_output` 处理的不是刚排的那批。
4. **调度器不知道最新 token 却能排下一步**：靠占位符 + GPU 端 `last_sampled_tokens`。
5. **两处判停**：调度器判 EOS/长度，前端判 stop 串；后者要反向发 ABORT。
6. **两种 request_id**：客户端看到的是外部 ID，引擎内部是带 8 位后缀的 ID。
7. **“非阻塞”不等于另一个线程**：UniProc 的 `non_block=True` 只是包了个 Future。
8. **分词不在 InputProcessor**：在 Renderer 线程池里。
9. **`process_outputs` 的返回值在 AsyncLLM 模式下是空的**：效果在信箱里。
10. **abort 走了两条队列**：既为了早处理，也为了保持顺序。

---

## 10. 可改进处

- 【观点】`docs/design/arch_overview.md` 的进程计数表把 Worker 进程数写成 `N`（`docs/design/arch_overview.md:111`），对单卡（`uni` 后端）不成立，容易误导第一次读代码的人。
- 【观点】`EngineCoreProc._wait_for_output` 的“等待期间收请求”能力只对带 `poll` 的执行器 Future 生效；默认单卡路径享受不到。是否值得为 `AsyncOutputFuture` 提供基于 CUDA event query 的 `poll`，值得评估。
- 【观点】stop 串在前端判定意味着引擎可能多算若干步；对很短的 stop 串和大 batch，这部分浪费是否可观，代码里没有指标直接反映。
