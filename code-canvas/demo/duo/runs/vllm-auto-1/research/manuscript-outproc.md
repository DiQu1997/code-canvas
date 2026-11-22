# “输出处理”这个框的下一层（补充底稿）

> 仓库 vllm，HEAD = `0e3c8dbdae`。锚点都是 `文件:行号`，在 HEAD 上核对过。
> 来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。「」里是逐字原文。
> 接着上一趟底稿（`manuscript.md`）：不重复它已讲清的 §5 步骤 8、§5 步骤 10、§6.5，只在这里往下挖。
> 例子沿用上一趟：prompt 20 token；模型生成 `["法国","的","首都","是","巴黎","。",<eos>]` 共 7 个 token（上一趟设的）；`stream=true`、`max_tokens=8`。**本稿新补的值一律标“设”。**
> 说明：本仓库里这些文件的提交说明几乎都是空的（只有标题）。凡标【提交】的，要么引的是标题，要么说明“只读了 diff”。

---

## 0. 一屏结论

这个框是**API Server 进程里的一条流水线**，全在同一个 asyncio 事件循环线程上跑，没有锁，也没有线程池：

```
ZMQ 收帧 task ──► outputs_queue ──► output_handler ──► process_outputs（逐请求）──► 每请求信箱 ──► generate()
 (解码 msgpack)   (无界队列)        (分块 128)         去分词 → 判 stop → 组装      (单槽，会合并)   (消费者)
                                          ▲                                  │
                                          └──── 反向 ABORT（stop 串命中）◄──┘
```

三句话：

1. **token id 变文字发生在 `process_outputs` 的一次循环里**：每个请求有自己的一份状态（`RequestState`），里面带一个**增量去分词器**；去分词器同时负责“判 stop 串、截断文字、扣住尾巴不发”。
2. **文字会“晚几个字”是故意的**：有 stop 串（且不要求输出 stop 串）时，每次最多少发 `最长 stop 串长度 − 1` 个字符，等确认它不是 stop 串的开头才放出去。
3. **“输出”和“消费”被一个单槽信箱隔开**：生产者（`output_handler`）不等消费者（`generate()`）；消费者慢了，新的增量就**合并进槽里**，而不是排队。stop 串命中时，前端自己把请求收尾，再**单向**通知引擎停算；引擎多算的 token 在两个地方被丢掉。

---

## 1. 框里有哪几样东西，状态放哪

按数据流的先后：

| # | 部件 | 跑在哪 | 读 / 写什么状态 | 锚点 |
|---|---|---|---|---|
| ① | `process_outputs_socket`（任务名 `EngineCoreOutputQueueTask`） | 事件循环里的 task | 收 ZMQ 多帧 → `decoder.decode(frames)` → 非 utility 回包就 `outputs_queue.put_nowait(outputs)` | `vllm/v1/engine/core_client.py:1249-1254`、`vllm/v1/engine/core_client.py:1294-1295` |
| ② | `outputs_queue` | 同上 | 一个**没有 maxsize 的** `asyncio.Queue`；没有反压 | `vllm/v1/engine/core_client.py:1209` |
| ③ | `output_handler` | 事件循环里的 task | `get_output_async()` 取包 → 按 `chunk_size` 切片 → 调 `process_outputs` → 必要时发 abort | `vllm/v1/engine/async_llm.py:890-936` |
| ④ | `OutputProcessor.process_outputs` | 在 ③ 里同步调用 | 读写 `request_states: dict[str, RequestState]`（内部请求 ID → 状态） | `vllm/v1/engine/output_processor.py:731-760`、`vllm/v1/engine/output_processor.py:569` |
| ⑤ | `RequestState`（每请求一个） | 被 ④ 持有 | 字段：`detokenizer`、`logprobs_processor`、`queue`（信箱）、`output_kind`、`stream_interval`、`sent_tokens_offset`、`is_prefilling` | `vllm/v1/engine/output_processor.py:184-249` |
| ⑥ | `IncrementalDetokenizer`（每请求一个，挂在 ⑤ 上） | 同上 | `token_ids`（输出 token）、`output_text`（已解出的全部文字）、`stop` / `stop_buffer_length`、`_last_output_text_offset`（已发出的文字位置）；快路径还有 `DecodeStream`，慢路径有 `prefix_offset` / `read_offset` | `vllm/v1/engine/detokenizer.py:115-140`、`vllm/v1/engine/detokenizer.py:225-246`、`vllm/v1/engine/detokenizer.py:322-342` |
| ⑦ | `RequestOutputCollector`（单槽信箱，每请求一个） | ④ 写、`generate()` 读 | `output`（槽，至多一个 `RequestOutput`）、`ready`（`asyncio.Event`）、`aggregate`（只有 DELTA 为真） | `vllm/v1/engine/output_processor.py:54-94` |
| ⑧ | `generate()`（消费者，不在本框内，只是对岸） | 每个请求一个协程 | `q.get_nowait() or await q.get()` → `yield` | `vllm/v1/engine/async_llm.py:805-816` |
| ⑨ | 反向 abort 通道 | ③ 发起 | `process_outputs` 返回 `reqs_to_abort`，③ 调 `engine_core.abort_requests_async` | `vllm/v1/engine/output_processor.py:759`、`vllm/v1/engine/async_llm.py:932-936` |

**每请求状态是谁创建的**：请求进来时 `AsyncLLM.add_request` 先 `RequestOutputCollector(params.output_kind, request.request_id)` 建信箱（`vllm/v1/engine/async_llm.py:543`），再在**第一个 `await` 之前**调 `output_processor.add_request(...)` 登记（`vllm/v1/engine/async_llm.py:594-598`，上一趟 §5 步骤 4 已讲）。`RequestState.from_new_request` 在那里建去分词器（`vllm/v1/engine/output_processor.py:310-313`）。

**两个“全局”表**：`request_states`（内部 ID → 状态）和 `external_req_ids`（外部 ID → 内部 ID 列表，用于 `n>1` 和按外部 ID abort）（`vllm/v1/engine/output_processor.py:569-571`）。这个框的所有“请求已结束 / 已 abort”判断，最终都落在 `request_states.get(req_id)` 查不查得到上。

**执行模型**：`process_outputs` 是普通的同步函数，直接在事件循环线程里被调用（`vllm/v1/engine/async_llm.py:919`），去分词也在这里。所以这个框的 CPU 开销直接占用 API 进程的事件循环，这是下面“分块”存在的原因。

---

## 2. 先走一遍主线（无 stop 串）

设定同上一趟：`stream=true` → `output_kind = DELTA`（`vllm/entrypoints/openai/chat_completion/protocol.py:821-823`）；没有 stop 串 → `stop_buffer_length = 0`（`vllm/v1/engine/detokenizer.py:133-136`）。

引擎某一步送回一个 `EngineCoreOutputs`，里面 `outputs` 是**每个请求一条** `EngineCoreOutput(request_id, new_token_ids, finish_reason, stop_reason, …)`（`vllm/v1/engine/__init__.py:275-293`、`vllm/v1/engine/__init__.py:343-355`）。我们的请求这一步带回 `new_token_ids=[“法国”的 id]`。

`process_outputs` 对这一条做（`vllm/v1/engine/output_processor.py:760-866`）：

1. 用内部 ID 查 `request_states`；查不到 → `continue`（`vllm/v1/engine/output_processor.py:762-765`）。
2. 记统计（`vllm/v1/engine/output_processor.py:777-779`）；若 `is_prefilling` 就把它置假——这是“收到第一个输出”的标志（`vllm/v1/engine/output_processor.py:793-804`）。
3. `detokenizer.update(new_token_ids, finish_reason == STOP)`：逐 token 解码，追加到 `output_text`；返回命中的 stop 串或 `None`（`vllm/v1/engine/output_processor.py:827-830`）。本例没有 stop，返回 `None`。
4. `logprobs_processor.update_from_output`（`vllm/v1/engine/output_processor.py:845`，不展开）。
5. `make_request_output(...)`：`stream_interval == 1` 时不设闸，直接 `_new_completion_output` → `detokenizer.get_next_output_text(finished, delta)` 取“自上次起新增的文字”，此时 `text = "法国"`（`vllm/v1/engine/output_processor.py:490`）。
6. `req_state.queue.put(request_output)`（`vllm/v1/engine/output_processor.py:864-866`）→ 信箱槽空，写入并 `ready.set()`（`vllm/v1/engine/output_processor.py:72-74`）→ `generate()` 醒来。

第 7 个 token `<eos>` 到达时：引擎判定 `finish_reason=STOP`，`update(..., stop_terminated=True)` 把最后一个 token 从解码里**摘出来**（默认不输出 stop token 的文字），但仍追加到 `token_ids`（`vllm/v1/engine/detokenizer.py:153-159`、`vllm/v1/engine/detokenizer.py:172-176`）。所以最后一包是：`text=""`、`token_ids=[eos]`、`finish_reason="stop"`；随后 `_finish_request` 把状态从 `request_states` 删掉（`vllm/v1/engine/output_processor.py:880`）。这里引擎已经自己判了结束（`engine_core_output.finished` 为真），**不再发 abort**（`vllm/v1/engine/output_processor.py:881-885`）。

---

## 3. 逐个问题

### Q1 增量去分词怎么做？为什么不能每个 token 单独 decode 再拼？

**先说结论**：因为“一个 token ≠ 一段独立的文字”。三类情形（都有代码依据）：

- **一个字符被拆成几个 token（字节回退 / 字节级 BPE）**：单独解码前半个字符得到 `�`。慢路径的守卫：新文字以 `�` 结尾就先不吐、等下一个 token（`vllm/tokenizers/detokenizer_utils.py:269-274`）。【注释】「utf-8 char at the end means it's a potential unfinished byte sequence from byte fallback tokenization.」（`vllm/tokenizers/detokenizer_utils.py:270-271`）。
  - 设（举例，取决于具体分词器）：“巴”的 UTF-8 三个字节被拆成两个 token。第一个单独解 → `�`；增量去分词返回 `""` 并保持偏移不动，第二个到了再一起解出“巴”。
- **空格 / 清理规则依赖前后文**：【注释】「The prefix text is necessary only to defeat cleanup algorithms in the decode which decide to add a space or not depending on the surrounding ids.」（`vllm/tokenizers/detokenizer_utils.py:245-247`）。所以慢路径每次解码一个**窗口**（`prefix_offset` 到末尾），再减去窗口里“已经发过的前缀文字”`prefix_text`，只发差值（`vllm/tokenizers/detokenizer_utils.py:248-277`）。
- **SentencePiece 一类的 Metaspace 分词器单独 decode 会吃掉开头的空格**：【注释】「Uses decode() for human-readable output, then checks the raw vocab piece via convert_ids_to_tokens() to restore any leading spaces that decode() stripped (SentencePiece add_dummy_prefix inverse).」（`vllm/tokenizers/detokenizer_utils.py:149-151`，这是另一个“逐个 token 转字符串”的工具函数，间接说明单独 decode 的问题）；快路径种子注释同理：【注释】「(decoders like Metaspace treat the start of the text specially)」（`vllm/v1/engine/detokenizer.py:45-46`）。

**为什么也不是“每步把全部输出 decode 一遍再取差”**：【推断】那样每步 O(已生成长度)，总体 O(n²)。代码用滑动窗口规避：窗口起点 `prefix_offset`、已读终点 `read_offset`，成功吐字后 `prefix_offset ← 旧 read_offset`、`read_offset ← 当前 token 总数`（`vllm/tokenizers/detokenizer_utils.py:276-277`）。依据：提交 `8d10c38b73` 标题「[Perf] Reduce fallback decode CPU by 69% at 32K (#60497)」，diff 把 `prev_tokens` 的整表拼接改成只拼 `window_start` 之后的窗口（`vllm/tokenizers/detokenizer_utils.py:233-239`）——说明窗口长度确实是热点。【注释】窗口大小常数：「5 is an arbitrary value that should work for all tokenizers (bigger = more conservative).」（`vllm/tokenizers/detokenizer_utils.py:57-59`）。

**两条路径**（谁被选中：`IncrementalDetokenizer.from_new_request`，`vllm/v1/engine/detokenizer.py:96-112`）：

- `tokenizer is None`（请求 `detokenize=False`，`vllm/v1/engine/output_processor.py:300-301`）→ 基类 `IncrementalDetokenizer`，只记 token id、文字恒为 `""`（`vllm/v1/engine/detokenizer.py:76-93`）。
- 快 tokenizer（`TokenizersBackend`）→ `FastIncrementalDetokenizer`：每 token 调 HF `tokenizers` 库的 `DecodeStream.step`，返回 `str` 或 `None`（还不完整）（`vllm/v1/engine/detokenizer.py:270-281`、`vllm/v1/engine/detokenizer.py:283-307`）。`DecodeStream` 的内部算法在 `tokenizers` 这个外部 Rust 库里，**本仓库里没有，我没读**。
- 否则 → `SlowIncrementalDetokenizer`，Python 实现 `detokenize_incrementally`（`vllm/v1/engine/detokenizer.py:353-368`）。

**快路径的“种子”**：新请求创建 `DecodeStream` 时，用**prompt 的末尾几个 token** 先喂它，让第一个增量文字的前后文正确，而不是喂整个 prompt。【注释】「The seed only shapes the first delta, so a short tail avoids an O(prompt) decode.」（`vllm/v1/engine/detokenizer.py:43`）；尾巴取 5 个“能解出文字的”token（`INITIAL_INCREMENTAL_DETOKENIZATION_OFFSET`），若尾巴文字以 `U+FFFD` 结尾就最多再往前延 3 个 token（一个 UTF-8 字符最多 4 字节，【注释】`vllm/v1/engine/detokenizer.py:27-30`），最多回看 `_MAX_SEED_WINDOW = 256` 个 token（`vllm/v1/engine/detokenizer.py:31-33`、`vllm/v1/engine/detokenizer.py:49-68`）。【提交】`da7acd6f42` 标题「[Perf][Frontend] Seed the fast detokenizer with a prompt tail, not the full prompt (#67153)」；【测试】`tests/detokenizer/test_decode_stream_seed.py:215` 起验证“尾巴种子流出的文字与整段 prompt 种子相同”。

**一处防御**：`DecodeStream.step` 在个别分词器上会抛 `Invalid prefix encountered` 或因非法 token id 抛 `OverflowError/TypeError`；代码捕获后重置流（或把该 token 当空串），避免整个 `output_handler` 崩掉（`vllm/v1/engine/detokenizer.py:286-306`）。【注释】「Recover from edge case where tokenizer can produce non-monotonic, invalid UTF-8 output, which breaks the internal state of tokenizers' DecodeStream.」（`vllm/v1/engine/detokenizer.py:294-296`）；【测试】`tests/v1/engine/test_fast_incdec_prefix_err.py:13` 的用例就是这个复现。这点之所以重要：`output_handler` 一旦抛异常，**所有**请求的信箱都会被塞入这个异常（`vllm/v1/engine/async_llm.py:963`、`vllm/v1/engine/output_processor.py:596-600`）。

### Q2 有 stop 串时为什么要扣字？扣多少？命中后怎么截断？

**扣多少**：`stop_buffer_length = max(len(s) for s in stop) - 1`，**仅当** 有 stop 且 `include_stop_str_in_output=False`；否则 0（`vllm/v1/engine/detokenizer.py:131-136`）。【注释】「Number of chars to hold back when stop strings are to be excluded from streamed output.」（`vllm/v1/engine/detokenizer.py:131-132`）。

**为什么是“长度 − 1”**：【推断】一个长度 L 的 stop 串，在它**完整出现之前**，末尾最多已有 L−1 个字符是它的前缀；这 L−1 个字符不能发出去，否则 stop 串一旦完成，客户端已经看到了它的前半截，无法收回。为什么 `include_stop_str_in_output=True` 时不扣：这时 stop 串本身就要输出，没有“收回”的问题（推断，依据是 `vllm/v1/engine/detokenizer.py:133` 的条件）。没有注释直接写这个推导。

**怎么扣**：`get_next_output_text(finished, delta)`（`vllm/v1/engine/detokenizer.py:207-222`）：

- `buffer_length = 0 if finished else stop_buffer_length`（`vllm/v1/engine/detokenizer.py:211`）。【注释】「We return the full output text if the sequence is finished.」（`vllm/v1/engine/detokenizer.py:210`）；【测试】`tests/detokenizer/test_stop_string_while_stop_model_terminates.py:100-102` 断言结束时返回完整文字。
- DELTA：`length = len(output_text) - buffer_length`；只返回 `output_text[last_offset:length]` 并把 `_last_output_text_offset` 推到 `length`（`vllm/v1/engine/detokenizer.py:217-222`）。
- 非 DELTA：直接 `output_text[:-buffer_length]`（`vllm/v1/engine/detokenizer.py:212-215`）。

**命中判断**：`update()` 先把新 token 逐个解码追加（同时记录每个 token 结束时 `output_text` 的长度 `token_offsets`，`vllm/v1/engine/detokenizer.py:165-170`），再调 `check_stop_strings`（`vllm/v1/engine/detokenizer.py:180-188`）。它**只在新增文字里找**：`output_text.find(stop_str, 1 - new_char_count - stop_string_len)`（负起点，保证只匹配“结束位置落在新文字里”的情形，同时能匹配横跨旧 / 新文字的 stop 串）（`vllm/v1/engine/detokenizer.py:400-401`）。【注释】「Avoid searching already-searched text.」（`vllm/v1/engine/detokenizer.py:400`）。多个 stop 串同时命中时选“最早**结束**”的，并列按列表顺序（`vllm/v1/engine/detokenizer.py:405-410`）；【注释】「the stop string that completes earliest in the text is selected, so the result matches appending one token at a time.」（`vllm/v1/engine/detokenizer.py:386-389`）；【测试】`tests/detokenizer/test_check_stop_strings.py:16-62`。

**截断**：`check_stop_strings` 返回 `(stop_str, truncate_to)`：不含 stop 串时截到 stop 串**起点**（`vllm/v1/engine/detokenizer.py:422-423`）；含 stop 串时截到**终点**，刚好在末尾则返回 `-1` 表示不用截（`vllm/v1/engine/detokenizer.py:415-420`）。`update()` 随后 `self.output_text = self.output_text[:truncate_to]`（`vllm/v1/engine/detokenizer.py:198-199`）。**是按字符截的，不是按 token**：【测试】`tests/detokenizer/test_stop_strings.py:74-81` 里 stop=`"gani"` 落在 token “organization” 中间，输出 `"VLLM is a 100% volunteer or"`。

**同一步里 stop 后面的 token 怎么办**（投机解码一步可能回来多个 token）：`bisect_left(token_offsets, stop_end, lo=1)` 算出该保留几个 token，`del self.token_ids[first_new_token_index + num_kept:]`（`vllm/v1/engine/detokenizer.py:191-197`）。【注释】「Drop tokens generated after the one completing the stop string (possible when multiple tokens are appended in one step).」（`vllm/v1/engine/detokenizer.py:194-195`）。`OutputProcessor` 再用 `_trim_surplus_tokens` 把 `EngineCoreOutput` 里的 `new_token_ids`、logprobs、mask 同步截短（`vllm/v1/engine/output_processor.py:831-836`、`vllm/v1/engine/output_processor.py:900-918`）。【提交】`51144dd83e` 标题「[Bugfix][Spec Decode] Trim token_ids/logprobs left past a stop string under speculative decoding (#47616)」；【测试】`tests/detokenizer/test_stop_string_while_stop_model_terminates.py:147` 起的 `test_stop_string_trims_speculative_overflow`（第 97 行：「Tokens after the stop string, including the stop token, are dropped.」）。

**`min_tokens` 的交互**：`stop_check_offset = token_offsets[max(min_tokens - num_prev_tokens, 0)]`，stop 串只在第 `min_tokens` 个 token 之后产生的文字里才算数（`vllm/v1/engine/detokenizer.py:180-188`）；【注释】指向 PR：「Support min_tokens, see https://github.com/vllm-project/vllm/pull/22014」（`vllm/v1/engine/detokenizer.py:181`）。

**例（设 `stop=["巴黎。"]`，长度 3 → 扣 2 个字符）**：

| 引擎回来的 token | `output_text`（解出的） | 扣字后允许发出的长度 | 本包 `text` |
|---|---|---|---|
| “法国” | `法国`（2） | 0 | `""` |
| “的” | `法国的`（3） | 1 | `法` |
| “首都” | `法国的首都`（5） | 3 | `国的` |
| “是” | `法国的首都是`（6） | 4 | `首` |
| “巴黎” | `法国的首都是巴黎`（8） | 6 | `都是` |
| “。” | `…巴黎。`（9）→ 命中 `巴黎。`，起点 6 | — | `""`，截成 `法国的首都是`，`finish_reason="stop"`，`stop_reason="巴黎。"` |

客户端一共收到 `法 / 国的 / 首 / 都是`，拼起来是“法国的首都是”；任何时刻客户端看到的文字都比模型已产出的少 2 个字符，这就是“晚几个字”。**注意 `token_ids` 不晚**：DELTA 包里 `token_ids` 仍是本步的新 token，只有 `text` 被扣——文字和 token 在这些包里是**错位**的（`vllm/v1/engine/output_processor.py:407`、`vllm/v1/engine/output_processor.py:490`）。

### Q3 stop 命中后，反向 ABORT 怎么送回？引擎多算的 token 怎么丢？

**前端先收尾，再通知引擎**。接上面的例子，第 6 个 token 命中后，`process_outputs` 里（`vllm/v1/engine/output_processor.py:828-836`、`vllm/v1/engine/output_processor.py:872-885`）：

1. `finish_reason = STOP`、`stop_reason = stop_string`（覆盖引擎给的值，哪怕引擎说是 length）（`vllm/v1/engine/output_processor.py:831-833`）。
2. 照常 `make_request_output` → 信箱，这一包带着 `finished=True`。
3. `_finish_request` 从 `request_states` 删掉该请求（`vllm/v1/engine/output_processor.py:880`、`vllm/v1/engine/output_processor.py:920-926`）。
4. 判断 `req_state.streaming_input or not engine_core_output.finished`：这一步引擎**没**说结束（`finished` 为假）→ 把内部 ID 追加到 `reqs_to_abort`（`vllm/v1/engine/output_processor.py:881-885`）。【注释】「EngineCore may still have the request: a stop string was detected here, or an in-flight streaming-input chunk may re-create the session.」（`vllm/v1/engine/output_processor.py:882-884`）。
   - 如果 stop 串和引擎的 EOS / 长度**同一步**命中，`engine_core_output.finished` 为真，就不发 abort（引擎已经停了）。

回到 `output_handler`：每个 chunk 处理完，`if processed_outputs.reqs_to_abort: await engine_core.abort_requests_async(...)`（`vllm/v1/engine/async_llm.py:932-936`）。【注释】「Abort any reqs that finished due to stop strings.」（`vllm/v1/engine/async_llm.py:932`）。`AsyncMPClient.abort_requests_async` 把 `ABORT` 消息通过请求管道发走；空列表不发，引擎已死也不发（`vllm/v1/engine/core_client.py:1379-1381`）。

**在引擎那头**：输入线程收到 ABORT 后，**同时**放进 `aborts_queue` 和 `input_queue`（`vllm/v1/engine/core.py:2295-2300`）。【注释】「Aborts are added to *both* queues, allows us to eagerly process aborts while also ensuring ordering in the input queue to avoid leaking requests. This is ok because aborting in the scheduler is idempotent.」（`vllm/v1/engine/core.py:2296-2299`）。主线程每步在**处理模型输出之前**先清空 `aborts_queue`（`vllm/v1/engine/core.py:735-737`、`vllm/v1/engine/core.py:847-849`、`vllm/v1/engine/core.py:886-894`）→ `scheduler.finish_requests(ids, FINISHED_ABORTED)`（`vllm/v1/engine/core.py:612-617`）。注意 `abort_requests` 里只改调度器状态，不产生任何要回给前端的输出——前端早已把这请求收尾了（以 `vllm/v1/engine/core.py:612-617` 为据，我没有穷举 `_free_request` 的所有下游）。

**“多算的 token”在三处被丢**（接例子：S7 已经排进 GPU，它的结果 = `<eos>`）：

1. **同一步内越过 stop 的 token**（投机解码一步多个）：见 Q2，`update()` 里 `del token_ids[...]` + `_trim_surplus_tokens`。
2. **abort 到达引擎之前就已经在途的步**：它的结果照常回到前端，但 `request_states.get(req_id)` 已经查不到 → 直接跳过（`vllm/v1/engine/output_processor.py:762-765`）。【注释】「Ignore output for already-aborted request.」（`vllm/v1/engine/output_processor.py:764`）。例子里 `<eos>` 这一条就死在这里。
3. **abort 已到引擎、但该请求仍有在途步**：调度器的 `update_from_output` 遇到 `request is None or request.is_finished()` 就 `continue`（`vllm/v1/core/sched/scheduler.py:2415-2423`）。【注释】「The request is already finished. This can happen if the request is aborted while the model is executing it (e.g., in pipeline parallelism or in async scheduling).」（`vllm/v1/core/sched/scheduler.py:2416-2418`）。调度器侧怎么处理，见调度器底稿 §判停。

**代价**：GPU 上这些多出来的步是白算的。async scheduling 下总有 1 步在途（上一趟 §5 步骤 7），所以 stop 串命中之后**至少**可能多算 1 步。（设：例子里是 1 步；真实多少取决于 abort 何时到达引擎。）

**和“客户端断开”的 abort 对照**：断开走的是另一条——`generate()` 被取消 → `AsyncLLM.abort(request_id, internal=True)` → **先**在 `OutputProcessor.abort_requests` 里删状态并往信箱塞一个 `finish_reason=ABORT` 的收尾包（`vllm/v1/engine/output_processor.py:602-663`），**再**发给引擎（`vllm/v1/engine/async_llm.py:821-823`、`vllm/v1/engine/async_llm.py:967-977`）。stop 串这条路径不走 `abort_requests`，因为状态已经在 `process_outputs` 里清掉了。

### Q4 单槽信箱：生产者比消费者快时怎么合并 DELTA？

先看信箱怎么写（`vllm/v1/engine/output_processor.py:70-84`）：

- 槽空（或来的是异常）→ 直接放进去并 `ready.set()`（`vllm/v1/engine/output_processor.py:72-74`）。
- 槽里已经有 `RequestOutput` 且新来的也是 → `self.output.add(output, aggregate=self.aggregate)`（`vllm/v1/engine/output_processor.py:75-80`）。
- `aggregate` 只有 `output_kind == DELTA` 才为真（`vllm/v1/engine/output_processor.py:63`）。

`RequestOutput.add` 的合并规则（`vllm/outputs.py:211-255`）：`finished |= next.finished`；对每个 `index` 相同的 completion，**DELTA（aggregate）**：`text` 拼接、`token_ids` 拼接、`logprobs` 拼接，`cumulative_logprob` / `finish_reason` / `stop_reason` 取最新的（`vllm/outputs.py:220-249`）；**非 DELTA**：整个 completion 被新的**替换**（因为新的已经是“到目前为止的全部”）（`vllm/outputs.py:250-252`）；`index` 不同（`n>1`）则追加（`vllm/outputs.py:254-255`）。【注释】「This ensures that request outputs with different request indexes (if n > 1) do not override each other.」（`vllm/v1/engine/output_processor.py:78-79`）。

读（`vllm/v1/engine/output_processor.py:86-100`）：`get()` 在槽空时 `await ready.wait()`，取走后把槽置空并 `ready.clear()`；`get_nowait()` 是同样的取法但不等。`generate()` 用 `q.get_nowait() or await q.get()`（`vllm/v1/engine/async_llm.py:809`）。【注释】「drain queue without await if possible (avoids task switching under load which helps performance).」（`vllm/v1/engine/async_llm.py:807-808`）。

**例（设）**：`generate()` 刚把“法国”交给 SSE 写出，客户端网络慢，写操作在 `await` 里挂着，没回来取信箱。这时 `output_handler` 连续处理了三个引擎包：

- “的” → 槽空，直接放入。
- “首都” → 槽非空，合并：`text="的首都"`、`token_ids=[的, 首都]`。
- “是” → 再合并：`text="的首都是"`、`cumulative_logprob` 换成最新。

`generate()` 回来 `get_nowait()` 一次取走，客户端收到**一个**块“的首都是”，而不是三个。信息没丢，只是粒度变粗。

**为什么没有锁**：【推断】`put` 和 `get` 都是**不含 `await` 的同步代码**，且全在同一个事件循环线程上，所以不会交错；唯一的“等待”点是 `get()` 里的 `await ready.wait()`，那里每轮醒来都重查槽（`while (output := self.output) is None`，`vllm/v1/engine/output_processor.py:88-89`）。

**为什么是单槽 + 合并，不是队列**：提交 `9d72daf4ce` 标题「[V1][Perf] Simpler request output queues (#15156)」，diff 显示它把每请求的 `asyncio.Queue[RequestOutput]` 换成了 `RequestOutputCollector`；提交正文为空。【注释】只说了做什么：「When streaming deltas, RequestOutputs are merged if the producer gets ahead of the consumer.」（`vllm/v1/engine/output_processor.py:58-59`）。【推断】好处：慢客户端的内存占用有上界（每请求至多一个包）；多个增量合成一次唤醒，少几次任务切换。【测试】`tests/v1/engine/test_output_processor.py:1469-1552`：连续 put 2 个、3 个后一次 get，文字 / token_ids / logprobs 被合并、`cumulative_logprob` 取最后一个、`finished` 为真。

**两个特例**：

- 来的是**异常**：无条件覆盖槽（哪怕里面有没读的数据），`get()` 取到后 `raise`（`vllm/v1/engine/output_processor.py:72-74`、`vllm/v1/engine/output_processor.py:92-93`）。槽里已是异常时，后来的 `RequestOutput` 不落入任何分支，被悄悄丢弃（`vllm/v1/engine/output_processor.py:75-84` 的两个 `elif` 都要求槽里是正常输出）。
- **流式输入（streaming input）**请求用子类 `StreamingInputOutputCollector`，它**不跨 chunk 边界合并**，多出来的放 `_backlog`（`vllm/v1/engine/output_processor.py:129-147`）；与本例无关，只点名。

### Q5 `output_handler` 为什么按 `VLLM_V1_OUTPUT_PROC_CHUNK_SIZE` 分块，块与块之间做什么？

**默认 128**：`vllm/envs.py:184`、`vllm/envs.py:1593-1595`。**为什么分块**：【注释】「Split outputs into chunks of at most VLLM_V1_OUTPUT_PROC_CHUNK_SIZE, so that we don't block the event loop for too long.」（`vllm/v1/engine/async_llm.py:901-903`）。`process_outputs` 是同步的，一个包里有几百个请求时，整批跑完中间不会让出事件循环——其它请求的 `generate()` 要等。环境变量的注释把取舍写明了：【注释】「Setting this too high can result in a higher variance of inter-message latencies. Setting it too low can negatively impact TTFT and overall throughput.」（`vllm/envs.py:1589-1592`；同一段开头：「Controls the maximum number of requests to handle in a single asyncio task when processing per-token outputs in the V1 AsyncLLM interface.」`vllm/envs.py:1586-1588`）。

**块与块之间做三件事**（`vllm/v1/engine/async_llm.py:904-936`）：

1. 切片 `engine_core_outputs[start:end]`，调一次 `process_outputs`（`vllm/v1/engine/async_llm.py:905-924`）。
2. `if end < num_outputs: await asyncio.sleep(0)`——**让出一次事件循环**，让已经就绪的 `generate()` 协程先把刚写进信箱的包取走、写给客户端（`vllm/v1/engine/async_llm.py:928-930`）。【注释】「Allow other asyncio tasks to run between chunks」（`vllm/v1/engine/async_llm.py:928`）。最后一块后不 sleep（反正马上回去等下一个包）。
3. 立即发这一块产生的 abort（`vllm/v1/engine/async_llm.py:932-936`），而不是等整批做完。

整包只共用一个 `iteration_stats`，`update_scheduler_stats` 与日志在所有块之后做一次（`vllm/v1/engine/async_llm.py:897-899`、`vllm/v1/engine/async_llm.py:938-945`）。

**例（设）**：一个包里有 300 个请求的输出 → 切成 `[0:128]`、`[128:256]`、`[256:300]` 三块，在第 1、2 块后各 `sleep(0)` 一次。我们的请求若在第 1 块，它的文字在第 2 块处理之前就能被取走。

**读者容易疑惑的点**：分块**不是并行**，也不改变 ZMQ 收包——收包是另一个 task（①），在 `output_handler` 的任何 `await` 点都能插进来继续往无界的 `outputs_queue` 里放（`vllm/v1/engine/core_client.py:1233-1235`）。【注释】「Perform IO in separate task to parallelize as much as possible.」（`vllm/v1/engine/core_client.py:1233`）。

**历史**：【提交】`aea94362c9`「[Frontend][V1] Online serving performance improvements (#12287)」引入该环境变量（改动 `vllm/envs.py`、`vllm/v1/engine/async_llm.py`）；提交正文为空。我只读了 diff：当时用 `np.array_split` 把包**均分**成 `cdiv(n, chunk)` 份，现在是固定步长（300 → 128/128/44）。为什么改，没查到。

### Q6 `stream_interval` 合批是怎么回事？

**它是什么**：配置项 `SchedulerConfig.stream_interval`，默认 `1`，`--stream-interval` 可改（`vllm/config/scheduler.py:284-288`、`vllm/engine/arg_utils.py:1823`）。【文档】「A smaller value (1) makes streaming smoother by sending each token immediately, while a larger value (e.g., 10) reduces host overhead and may increase throughput by batching multiple tokens before sending.」（`vllm/config/scheduler.py:285-288`）。请求也能自带（OpenAI 的 chat / completion 请求里都有这个字段），**只能抬高、不能压低**：`max(请求值, 引擎值)`（`vllm/v1/engine/output_processor.py:303-305`）；【测试】`tests/v1/engine/test_output_processor.py:301-306`，docstring「A per-request stream_interval can raise the interval above the engine default but not below it (values under the default clamp up), without altering the generated text.」

**怎么做**：只在 `make_request_output` 里加一道闸（`vllm/v1/engine/output_processor.py:382-395`）。【注释】「Send output request only when」「1. It has finished, or」「2. It is the first token, or」「3. It has reached the stream interval number of tokens」（`vllm/v1/engine/output_processor.py:385-388`）。闸挡住时返回 `None`，`process_outputs` 就不 `put` 信箱（`vllm/v1/engine/output_processor.py:852`）。**去分词不受影响**，每步仍然照常 `update`；通过时 DELTA 模式取 `output_token_ids[sent_tokens_offset:]` 并把偏移推到当前总数（`vllm/v1/engine/output_processor.py:397-403`）；文字则因为 `_last_output_text_offset` 记着“已发到哪”，自然包含被挡住的那几步（`vllm/v1/engine/detokenizer.py:217-222`）。

**例（设 `stream_interval=3`）**，7 个 token：

| token | 已产出 − 已发 | 动作 |
|---|---|---|
| 1 “法国” | `sent_tokens_offset==0`（第一个） | 发，offset=1 |
| 2 “的” | 1 | 挡 |
| 3 “首都” | 2 | 挡 |
| 4 “是” | 3 ≥ 3 | 发 `[的,首都,是]`，文字“的首都是”，offset=4 |
| 5 “巴黎” | 1 | 挡 |
| 6 “。” | 2 | 挡 |
| 7 `<eos>`（结束） | `finished` | 发 `[巴黎,。,<eos>]`，文字“巴黎。” |

客户端收到 3 包而不是 7 包；第一个 token 仍立即发（首字延迟不受影响）。【注释】`vllm/sampling_params.py:391-395`：「The first and final outputs are always emitted immediately.」

**和信箱合并的区别**：信箱合并是**事后**（消费者没跟上才合），不挡生产；`stream_interval` 是**事前**（生产出来就不创建包），省的是 `RequestOutput` 的构造、信箱写入、`generate()` 唤醒以及下游 SSE 序列化。【推断】依据：提交 `5d6ce2b960` 标题「[Perf] Support stream interval for reducing host overhead (#27869)」，正文空；配置说明里写的就是“reduces host overhead”。

**一个我读代码得出、但没运行验证的细节**：`sent_tokens_offset` 只在 DELTA 分支里更新（`vllm/v1/engine/output_processor.py:397-403`），而闸的第 2 个条件是 `sent_tokens_offset == 0`（`vllm/v1/engine/output_processor.py:391`）。所以 `output_kind=CUMULATIVE` 时 offset 永远是 0，闸永远放行——`stream_interval>1` 对 CUMULATIVE 好像没有效果。OpenAI 服务器流式用 DELTA（`vllm/entrypoints/openai/chat_completion/protocol.py:821-823`），不受影响。见 §7。

### Q7（顺带）还有哪些分支会绕过去分词

- `FINAL_ONLY`（非流式）：中间包直接 `return None`，只在结束时产出一个包（`vllm/v1/engine/output_processor.py:368-372`）。去分词仍然每步在跑。
- 请求被引擎拒绝（`error` 非空，例如引擎暂停）：把异常 `put` 进信箱并结束，不走去分词（`vllm/v1/engine/output_processor.py:767-775`）。
- pooling 模型：`pooling_output` 非空，不去分词（`vllm/v1/engine/output_processor.py:821`、`vllm/v1/engine/output_processor.py:374-380`）。

---

## 4. 建议做成源码页的代码段（6 段 + 1 备选）

下面“批注行”的锚点都在该段范围内；“为什么”的来源等级见各条。

**P1 `output_handler`——分块与反向 abort**：`vllm/v1/engine/async_llm.py:890-936`（47 行）。
- `894`：`outputs = await engine_core.get_output_async()`。包在这里才被取走；之前一直躺在无界队列里。
- `901-907`：分块切片。为什么：【注释】见 Q5。
- `919-924`、`925-926`：`process_outputs` 返回后 `assert not processed_outputs.request_outputs`——AsyncLLM 模式下所有输出都已塞进信箱，返回的列表必须为空（返回列表只给同步 `LLMEngine` 用，`vllm/v1/engine/output_processor.py:867-869`）。
- `928-930`：`if end < num_outputs: await asyncio.sleep(0)`。顺序依赖：必须在 abort 之前还是之后？代码是先 sleep 再 abort（`vllm/v1/engine/async_llm.py:928-936`），没有注释说明这个顺序的理由——【推断】让消费者先取走刚写进去的包。
- `932-936`：abort 在**块内**发，不是批末。

**P2 `process_outputs` 的核心一段**：`vllm/v1/engine/output_processor.py:826-885`（60 行；可在页首另附 `758-765` 的循环头）。
- `762-765`：`request_states.get` 查不到就跳过。这是“已 abort / 已结束的请求回来的尾巴”的第 2 道丢弃线。
- `827-830`：先记 `num_prev_tokens`，再 `update`，第二个参数是“引擎已判 STOP”。顺序依赖：`num_prev_tokens` 必须在 `update` 之前取，否则 `_trim_surplus_tokens` 算不出保留了几个。
- `831-836`：命中 stop 串就改 `finish_reason` / `stop_reason`，并截短 `new_token_ids`。**别名陷阱**：`new_token_ids`（第 782 行取出）和 `engine_core_output.new_token_ids` 是**同一个 list**，`_trim_surplus_tokens` 里 `del output.new_token_ids[num_kept:]` 原地截（`vllm/v1/engine/output_processor.py:913`），所以后面 `make_request_output(new_token_ids, ...)` 看到的已是截短后的（`vllm/v1/engine/output_processor.py:852-853`）。【推断】依据：读代码，赋值与 `del` 作用在同一对象上；`tests/detokenizer/test_stop_string_while_stop_model_terminates.py:97-99` 断言结果 `token_ids` 被截。
- `845`：`logprobs_processor.update_from_output` 在截短**之后**——顺序依赖：logprobs 要和截短后的 token 对齐（【推断】；`_trim_surplus_tokens` 已同步截 `new_logprobs`，`vllm/v1/engine/output_processor.py:911-912`）。
- `852-866`：组装 + 放信箱。
- `880-885`：收尾与 abort 判定，见 Q3。

**P3 `BaseIncrementalDetokenizer.update`**：`vllm/v1/engine/detokenizer.py:142-201`（60 行）。
- `149-151`：没有新 token 就不做事（空包常见：纯 prefill 的步）。
- `153-159`：`stop_terminated` 且不含 stop 串 → 最后一个 token 不解码，稍后原样补进 `token_ids`（`172-176`）。为什么先摘后补：见 Q1/§2——token 序列要完整，文字不要 stop token。【测试】`tests/detokenizer/test_stop_string_while_stop_model_terminates.py:49-102`（docstring 说明“stop 串判断要放在 `stop_terminated` 的提前返回之前”）；对应修复提交 `922d3b401b`「[Bugfix] Handle the edge case in detokenizer where processed tokens contain both `stop` str and `eos` token (#23938)」。
- `165-170`、`176`：`token_offsets` 记录每个 token 结束时的文字长度（只有设了 stop 才记，`165` 的 `if self.stop`）。
- `180-188`：`min_tokens` 门。
- `191-199`：`stop_end`、`bisect_left(..., lo=1)`、删多余 token、截文字。`lo=1` 的含义：`token_offsets[0]` 是本步开始前的长度，不对应任何新 token（【推断】，读代码得出）。

**P4 扣字与 stop 检查**：`vllm/v1/engine/detokenizer.py:131-137` + `vllm/v1/engine/detokenizer.py:207-222` + `vllm/v1/engine/detokenizer.py:392-423`（合计约 55 行，建议拆成两页：先扣字，后 `check_stop_strings`）。
- `134`：`max(len(s) for s in self.stop) - 1`。
- `211`：`0 if finished else self.stop_buffer_length`。
- `217-222`：只有 `last_offset < length` 才前移（文字被截断后可能 `length` 反而更小，此时返回 `""`）。
- `401`：负起点搜索；`405-410`：按“结束位置”选最早；`415-423`：含 / 不含 stop 串的两种截断点。

**P5 `RequestOutputCollector` + `RequestOutput.add`**：`vllm/v1/engine/output_processor.py:54-94`（41 行）+ `vllm/outputs.py:211-255`（45 行），两页并排。
- `63`：`aggregate` 由 `output_kind` 一次定死。
- `72`：`or isinstance(output, Exception)`——异常能顶掉未读数据。
- `75-84`：三个分支；最后一个（Pooling）是替换。
- `88-91`：`while ... await ready.wait()`、取后清槽清事件。
- `vllm/outputs.py:220-249`（累加字段）vs `250-252`（替换）——**DELTA 与非 DELTA 的分界线**，对照看。
- `vllm/outputs.py:239-241`：`cumulative_logprob` 取最新而不是求和——因为它本身就是累计值（【推断】，字段名）。

**P6 `stream_interval` 闸 + 去分词读取**：`vllm/v1/engine/output_processor.py:366-403`（38 行）。
- `368-372`：FINAL_ONLY 先挡掉。
- `382-395`：三条放行条件。
- `397-403`：DELTA 才改 `new_token_ids` 与 `sent_tokens_offset`（疑似 CUMULATIVE 失效处，见 §7）。

**备选 P7 慢路径增量去分词**：`vllm/tokenizers/detokenizer_utils.py:233-277`（45 行）。
- `233-239`：`window_start`——只拼窗口，不拼全部历史（提交 `8d10c38b73`）。
- `245-254`：`prefix_text` 与 `new_text`。
- `269-274`：`� ` 结尾或没长就返回 `""`，偏移**不动**。
- `276-277`：吐字后偏移前移。

---

## 5. 没查到为什么

- **为什么 stop 串在前端判、不在引擎判**：没有注释也没有提交说明；上一趟的推断仍是推断。
- **`VLLM_V1_OUTPUT_PROC_CHUNK_SIZE` 默认为什么是 128**：环境变量注释只写了取舍方向，没给 128 的依据；`aea94362c9` 正文为空。
- **分块从 `np.array_split` 均分改成固定步长是哪个提交、为什么**：没追到。
- **单槽信箱取代 `asyncio.Queue` 的动机**：`9d72daf4ce` 正文为空，只有标题和类 docstring。
- **`DecodeStream`（Rust）内部怎么保证增量正确**：库不在仓库内，没读。
- **`INITIAL_INCREMENTAL_DETOKENIZATION_OFFSET = 5`**：注释自称 arbitrary（`vllm/tokenizers/detokenizer_utils.py:57-58`），没有更多依据。
- **stop 的扣字长度为什么是 L−1**：没有注释；本稿的解释是【推断】。
- **`stream_interval` 在 CUMULATIVE 下是否本来就想失效**：没找到注释或测试；`tests/v1/engine/test_output_processor.py:301` 的用例只用了 DELTA。
- **`SamplingParams.output_text_buffer_length` 为什么还在**：`vllm/sampling_params.py:405`、`vllm/sampling_params.py:632-635` 会算一个同样的“扣字长度”，注释「Number of characters to hold back for stop string evaluation until sequence is finished.」（`vllm/sampling_params.py:632-633`），但整个 `vllm/` 与 `tests/` 里没有别处读它；V1 的真正扣字用的是去分词器自己算的 `stop_buffer_length`。为什么没删，没查到。
- **abort 为什么每块后发、而不是整包后发一次**：没有注释；该写法在 `aea94362c9` 当时就如此。

---

## 6. 读者最容易困惑的地方

1. **“文字晚几个字”但 `token_ids` 不晚**：同一包里 `text` 与 `token_ids` 对不上（Q2 末）。
2. **`new_token_ids` 在 `_trim_surplus_tokens` 里被原地改**：函数名里没有“修改入参”的提示（P2）。
3. **stop 串命中时没有“abort 回执”**：前端先宣布结束，再单向告诉引擎；引擎没有任何回包，前端靠查不到状态丢掉尾巴（Q3）。
4. **“结束”由两处判**：引擎判 EOS / stop_token_ids / 长度；前端判 stop 串。`finish_reason` 以前端最后一次覆盖为准（`vllm/v1/engine/output_processor.py:831-833`）。
5. **`process_outputs` 在 AsyncLLM 里返回空列表**：所有输出早已进信箱（P1）。
6. **信箱合并 ≠ `stream_interval`**：一个在消费端事后合并，一个在生产端事前不发（Q6 末）。
7. **`asyncio.sleep(0)` 看起来多余**：它是让出事件循环的最小写法，作用是让消费者在块间取走输出（Q5）。

对照着看的两对：Q2 的表 ↔ Q3 的步骤（同一个 stop 例子的前后两半）；Q4 的合并例 ↔ Q6 的闸例（同样“少发包”，位置不同）。

---

## 7. 可改进处（均为【观点】）

1. 【观点】`stream_interval>1` 在 `CUMULATIVE` 下疑似不生效（见 Q6 末）；若是疏漏，让 `sent_tokens_offset` 在两种模式下都更新即可。我没有运行验证。
2. 【观点】`SamplingParams.output_text_buffer_length` 看起来是 V1 之前遗留的死字段，同一个概念在去分词器里重复算了一份，容易让读者以为改它会生效。
3. 【观点】`_trim_surplus_tokens` 原地改 `EngineCoreOutput.new_token_ids` 依赖列表别名，若 `process_outputs` 里 `new_token_ids` 将来被改成拷贝（如过滤后的新列表），截短会悄悄失效。让 `make_request_output` 从 `engine_core_output` 重新取，更不易出错。
4. 【观点】去分词与整个 `process_outputs` 都占事件循环线程；分块只改善**延迟抖动**，并不增加吞吐。高并发下它仍是 API 进程的 CPU 瓶颈（【推断】：同步函数、无线程池，见 §1）；`stream_interval` 是目前仓库里唯一直接减负的旋钮。
5. 【观点】文字被扣光的包（例如例子里第 1 步 `text=""`）仍会构造 `RequestOutput` 并唤醒消费者；若包里 `text` 与 `token_ids` 都没有新内容可以不放，但这里 `token_ids` 通常非空，收益有限，只是个观察。
