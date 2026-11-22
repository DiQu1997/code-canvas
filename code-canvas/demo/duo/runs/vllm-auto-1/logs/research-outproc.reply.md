底稿已写完：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research/manuscript-outproc.md`。约 5200 个汉字，全文含锚点和英文引文共 31k 字符，在 1.5 万字以内。

**1. 内容**
- 先是 9 个部件的表：谁读写哪些状态，各落到锚点。
- 然后拿上一趟的例子走主线（无 stop 串）。
- Q1 到 Q7 逐个回答你列的问题，另加一条“哪些分支绕过去分词”。
- 其中补设了几个新例子，都标“设”：
  - stop=`巴黎。`，逐步展示扣 2 个字符和命中截断；
  - 信箱合并；
  - stream_interval=3；
  - 300 个请求按 128 分块。
- 第 4 节是 6 段建议做成源码页的代码段，外加 1 段备选，每段有逐行批注。
- 另有“没查到为什么”“读者易困惑”“可改进处”三节。
- 单卡下 GPU Worker 的位置、请求 ID 随机化这些，直接指回上一趟，没有重复。

**2. 研究范围**
- 读了 `output_processor.py`、`detokenizer.py`、`detokenizer_utils.py`、`outputs.py` 的相关部分。
- 读了 `async_llm.py` 的 generate 和 output_handler，`core_client.py` 的收帧 task，`core.py` 的 abort 路径。
- 读了调度器里“已结束请求的输出直接丢弃”那几行，以及 `envs.py`、`config/scheduler.py`、`sampling_params.py`。
- 读了 `tests/detokenizer/` 和 `tests/v1/engine/test_output_processor.py` 里的相关用例。
- 查了大约 20 个提交的说明或 diff。这个仓库里相关提交的正文几乎全是空的，只有标题和 diff，所以从 git 历史里挖出的“为什么”很少。最有价值的三条：
  - `9d72daf4ce`：diff 显示信箱取代的是每请求的 `asyncio.Queue[RequestOutput]`。
  - `aea94362c9`：当时分块用 `np.array_split` 均分，现在是固定步长（300 个请求 → 128/128/44）。
  - `8d10c38b73`：标题写“降低 69% CPU”，diff 是慢路径去分词只拼窗口、不拼全部历史，说明窗口长度是热点。
- 更有用的来源其实是注释和测试：`envs.py` 里分块大小的取舍说明，`core.py:2296` 的“abort 同时进两个队列”，以及 `test_stop_strings.py` 里 stop 串落在 token 中间的用例。

**3. 机械核对**（脚本和检查表在 `work-research-outproc/`）
- 锚点 190/190 通过（这是格式化后的 `文件:行号` 锚点；页面批注里的纯数字行号我手工逐条对过，脚本不检）。
- 提交哈希 8/8 通过（含文件改动核对；另有 HEAD 一个，只核了存在）。
- 引文 42/42 逐字匹配（空白和注释符归一化后）。
- 另有 125 条关键符号 / 文字抽查，125/125 通过。
- 第一次跑发现的问题已修：`_trim_surplus_tokens` 等几处行号写错，以及几处不是引文却用了 `「」`。

**4. “为什么”的来源等级**
- 【注释】30，【测试】10，【推断】13，【提交】5，【文档】2，另有【观点】7（可改进处）。
- 【推断】占“为什么”总数（60）的约 22%。
- 主要的推断：stop 扣字为什么是 L−1，信箱为什么不用加锁，以及单槽优于队列的好处。

**5. 没查到为什么**
- stop 串为什么在前端判。
- 分块默认值为什么是 128，以及为什么从均分改成固定步长。
- 单槽信箱取代 `asyncio.Queue` 的动机。
- 去分词器 `DecodeStream` 的内部算法（外部 Rust 库，没读）。
- 窗口常数 5 的依据（注释自称 arbitrary）。
- stop 扣字长度为什么是 L−1。
- stream_interval 在 CUMULATIVE 模式下是否本来就不生效。
- `SamplingParams.output_text_buffer_length` 为什么还留着（全仓库没有别处读它）。
- abort 为什么每个分块后发，而不是整批处理完发一次。

**6. 最弱的地方**
- **stream_interval 在 CUMULATIVE 下疑似失效**：这是我读代码推出来的，没运行验证。`sent_tokens_offset` 只在 DELTA 分支更新，闸门条件又包含 `== 0`，所以 CUMULATIVE 下永远放行。底稿里已如实标成“未验证”。
- **stop 命中后到底多算几步没法定量**：只能说“至少可能多 1 步”，具体取决于 abort 何时到达引擎，例子里的 1 步是设的。
- **abort 路径只读到 `abort_requests` 为止**：我没有穷举 `_free_request` 的所有下游，不能保证它在别处不产生输出。
- **例子里“巴”被拆成两个字节 token 是举例**：取决于具体分词器，底稿里标了“设”，但没在真实分词器上验证。
- **提交说明基本为空**，所以“为什么”大多靠注释和推断，宏观取舍的证据偏弱。