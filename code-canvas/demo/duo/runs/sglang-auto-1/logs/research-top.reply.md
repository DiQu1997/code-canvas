研究完成，底稿已写好，机械核对全部通过。

## 1. 底稿位置和规模
- 路径：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/research/manuscript.md`
- 共约 4.2 万字符，其中约 2.2 万是行内代码和锚点路径；正文约 2 万字，汉字约 6400 个。**如果"3 万字"按全部字符算，这份超了**；超出部分主要是完整路径锚点。
- 结构：一屏结论 → 选主路的理由 → 启动与三条管道 → 主进程 → Scheduler → Detokenizer → 回程 → 中止 → 20 步逐步推演（带设定的数）→ 旁路 → 各框入口 → 没查到为什么 → 可改进处 → 给讲解员的读者提示。
- 主路：`/v1/chat/completions` + `stream=true`，单卡，默认配置（overlap 开、radix cache 开、fcfs、Rust 前端关）。

## 2. 研究范围
- **读的文件约 30 个**，主要是：`http_server.py`、`engine.py`、`serving_base.py`、`serving_chat.py`、`tokenizer_manager.py`、`io_struct.py`、`scheduler.py`、`request_receiver.py`、`ipc_channels.py`、`schedule_batch.py`、`schedule_policy.py`、`batch_result_processor.py`、`output_streamer.py`、`tp_worker.py`、`model_runner.py`、`overlap_utils.py`、`detokenizer_manager.py`、`incremental_detokenize.py`、`mem_cache/` 下几个文件（含 README）、几个配置定义文件、`environ.py`、`docs/.../unified_memory_design.mdx`，以及一个单测文件。
- **提交**：用 `log -S/-G` 搜过约 10 个关键词，具体查看了 12 个提交。绝大多数提交说明只有标题。
- 从 git 历史里挖到的 3 个最有价值的"为什么"：
  1. `8080507c3e`：反分词所需的信息由"每步都发"改成"只在首个输出发一次 header，状态留在 Detokenizer"。diff 里能看到删掉了 `decode_ids_list`、`read_offsets` 两个字段。
  2. `c6fd9a00c7`：流式中间块把文本设为 `None`、推迟拼接，目的是消除 O(n²) 拷贝。和源码注释互相印证。
  3. `63ba2f8d7b` 和 `58ece60703`：`ModelWorkerBatch` 这一层先被引入、后被移除，所以旧资料里的三层批结构现在只剩两层。

## 3. 机械核对结果
脚本在 `work-research-top/` 里，用 `python3 -I` 运行。
- 锚点：306 / 306 通过。每个锚点都配了"该行必须出现的文字"，没有遗漏未配检查的锚点。
- 提交哈希：11 / 11 存在。"某提交改了某文件"的说法 12 / 12 成立。另外 4 处 diff 层面的说法用 git 输出核过，存在 `diff_evidence.txt`。
- 引文：56 / 56 逐字命中源码范围或提交说明。为了让核对不误判，ASCII 双引号只留给逐字原话，正文里的强调一律改成「」。

## 4. 各来源等级的"为什么"条数（按标签计数，不含文首图例）
- 【注释】19，【提交】10，【文档】6，【测试】1，【推断】11；另有【观点】3（可改进处）。
- 【推断】约占 11 / 47 ≈ 23%。
- 10 条【提交】里有几条只是在讲历史（谁在何时引入、何时移除），本身不解释动机。

## 5. 没查到为什么
1. 默认调度策略为什么从 `lpm` 改成 `fcfs`（`ac2387279e` 的说明里没提）。
2. 中止用的后台任务为什么等 2 秒（`de167cf5fa` 把 1 改成 2，没写理由）。
3. 等待超时为什么是 4 秒（`9d6029fb92` 只是把它改成可配置）。
4. 非流式请求为什么也要每 50 个 token 往 Detokenizer 送一次中间结果。
5. 为什么拆成三个进程、为什么 prefill 优先于 decode（底稿里只有【推断】）。
6. `PROMPT_TAIL_LEN = 5` 为什么取 5。
7. 进程间通信为什么默认用 pickle 而不是 msgpack。

## 6. 我认为最弱的地方
- **动机类的"为什么"证据偏薄**：提交说明几乎都只有标题，PR 正文在外网没法看。"为什么拆进程""为什么 prefill 优先"这类读者最常问的宏观问题，只能给推断。
- **推演里的 token 细节是设的**：比如"黎"字被切成两个字节 token。Chat 层 SSE 块的具体字段、对空增量怎么处理，没有逐行核实，表里已经注明。
- **只在顶层点了名、没展开的部分**：overlap 的 CUDA 双流和 WAR barrier、`FutureMap` 的内部机制、`PrefillAdder` 的预算算法。这些框如果被点开，需要另外补研究。
- **篇幅可能超限**：见第 1 条。

研究上下文都保留着，讲解员可以随时回来追问。
