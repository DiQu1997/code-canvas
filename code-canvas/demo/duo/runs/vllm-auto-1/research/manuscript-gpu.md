# “GPU 执行”框内部：一步之内 GPU 上排了哪些活（第二层底稿）

> 仓库 vllm，HEAD = `0e3c8dbdae`。锚点均为仓库相对路径 `文件:行号`，已在 HEAD 上核对。
> 来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。「」内为逐字原文。
> 接着上一趟（顶层）底稿：`UniProcExecutor`→`Worker`→`GPUModelRunner` 同线程直调、`non_block` 只是包 Future、`batch_queue` 深度 2、`update_from_output` 处理旧一步——这些见上一趟 §5 步骤 6-7、§6.9，这里不重讲。
> 例子沿用上一趟与调度器底稿：`request_id = chatcmpl-9f1c…-a1b2c3d4`，prompt 20 token，生成 7 个（末个 `<eos>`），`max_tokens=8`，`block_size=16`（调度器底稿设的），本例排出 S1…S8 八步（S1 排 20 个、其余各 1 个）。**本底稿新补的值一律标“设”。**

---

## 0. 一屏结论

这个框的真身是 `GPUModelRunner`（`vllm/v1/worker/gpu/model_runner.py:209`）——**现在是唯一的 ModelRunner**，旧的 V1 runner 已删除（【提交】`aac1b2a4f3`「[Core] Remove Model Runner V1 (#63373)」；设了旧环境变量时的警告文字「Model Runner V1 has been removed.」，`vllm/platforms/interface.py:1680`）。官方文档里叫它 MRV2。

它不是“收到调度结果就从头算一遍”，而是**一个长寿的、自带状态的对象**，每步只收调度器发来的“差量”，在 GPU 上把输入拼好，发射前向和采样，再把几个整数异步拷回 CPU。要抓住四件事：

1. **常驻请求状态**：每个请求在 worker 里占一个**固定槽位（行号）**，直到结束或被抢占。token 历史、已算数、最近一个采样 token 等都存在按槽位索引的大张量里，大部分在显存（`vllm/v1/worker/gpu/states.py:12-72`）。调度器每步只发差量（新 block、已算 token 数），不重发 token（`vllm/v1/core/sched/output.py:139-147`）。
2. **`last_sampled_tokens`**：一个 `[max_num_reqs, 1]` 的 int64 显存张量（`vllm/v1/worker/gpu/states.py:68-70`）。上一步采样出的 token 由 GPU 内核写进去，下一步的输入拼装内核再从里面读——**CPU 全程不碰这个值**。这就是 async scheduling 能“CPU 先排下一步”的物质基础。
3. **两个 stream、一个 event**：前向、采样、状态更新、输入拼装都在**主 stream**上按序排队；采样结果的 GPU→CPU 拷贝在另一条 **`copy_stream`** 上，结尾记一个**每步独有的 event**；CPU 到“晚一步”才对这个 event `synchronize()`（`vllm/v1/worker/gpu/async_utils.py:140-150`、`vllm/v1/worker/gpu/async_utils.py:192-193`）。
4. **前向与采样拆成两次调用**：`execute_model` 只发射前向、把中间结果存进 `self.execute_model_state` 就返回 `None`；`sample_tokens` 再取出来采样。拆开是为了让结构化输出的“语法掩码”可以**晚于前向**才到（`vllm/v1/engine/core.py:784-818`）。

**最容易误会的一点**：整个 `execute_model` / `sample_tokens` 在引擎主线程里**同步执行**，里面有大量 Python 和 kernel 发射，这段 CPU 时间是真实占用的；“异步”指的是 **GPU 上的计算**不阻塞 CPU、CPU 可以趁 GPU 算第 N 步时去排第 N+1 步，**不是**另有线程在并行跑。

---

## 1. 框里有什么（供画机器图）

### 1.1 部件与各自持有的状态

| 部件（对象） | 住在哪 | 放什么 | 谁写 / 谁读 |
|---|---|---|---|
| `RequestState` `req_states`（`vllm/v1/worker/gpu/states.py:9`） | 槽位索引的大张量，见 §2 | 每槽：`prompt_len`、`prefill_len`、`total_len`、`num_computed_tokens`、`last_sampled_tokens`、`all_token_ids`（`vllm/v1/worker/gpu/states.py:37-70`） | 写：`add_request`（CPU 暂存）、`post_update` 内核（GPU）；读：输入拼装内核 |
| `BlockTables` `block_tables`（`vllm/v1/worker/gpu/block_table.py:34`） | 显存，`[max_num_reqs, max_blocks]`（每个 KV cache group 一张） | 每槽的 block id 行；另有每步“按批次顺序摘出来”的 `input_block_tables` 和 `slot_mappings`（`vllm/v1/worker/gpu/block_table.py:82`、`vllm/v1/worker/gpu/block_table.py:101`、`vllm/v1/worker/gpu/block_table.py:106`） | 写：`append_block_ids`→`apply_write`；读：`gather_block_tables`、`compute_slot_mappings` 内核 |
| `InputBuffers` `input_buffers`（`vllm/v1/worker/gpu/input_batch.py:23`） | 显存，**定长持久缓冲** | `input_ids[max_num_tokens]`(int32)、`positions`(int64)、`query_start_loc`、`seq_lens`（`vllm/v1/worker/gpu/input_batch.py:29-38`） | 每步被输入准备内核覆盖；CUDA graph 回放时直接读（见 §5） |
| `Sampler` `sampler` + `model_state` | 显存按槽位的采样参数（温度、seed 等）；模型本体 | `sampler.add_request(req_index, …)` 把采样参数存到槽位（`vllm/v1/worker/gpu/model_runner.py:1364`） | 采样内核按 `idx_mapping` 取 |
| `CudaGraphManager` `cudagraph_manager` | 已捕获的 graph 表 | 描述符→graph（`vllm/v1/worker/gpu/cudagraph_utils.py:154`、`vllm/v1/worker/gpu/cudagraph_utils.py:561`） | 每步 `dispatch` 选一个 |
| `ExecuteModelState`（`vllm/v1/worker/gpu/model_runner.py:2637`） | runner 的一个**单槽字段** `self.execute_model_state` | 前向的 `input_batch`、`hidden_states`、`attn_metadata`… | `execute_model` 写、`sample_tokens` 读并清空（`vllm/v1/worker/gpu/model_runner.py:2249`、`vllm/v1/worker/gpu/model_runner.py:2296`） |
| `AsyncOutput`（`vllm/v1/worker/gpu/async_utils.py:117`） | 持有 GPU 张量引用 + CPU 的 numpy 目标 + 一个 `copy_event` | 采样 token、`num_sampled` 等的 CPU 副本 | `sample_tokens` 创建；`get_output()` 里 `synchronize` 后读 |
| 两个 stream | `main_stream`（当前默认 stream，缓存）和 `output_copy_stream`（专用于 D2H 拷贝） | — | `vllm/v1/worker/gpu/model_runner.py:668-670`、`vllm/v1/worker/gpu/model_runner.py:252` |
| UVA 暂存池 `UvaBufferPool` | pinned 主机内存，**轮转 2 份** | 给“CPU 写、GPU 读”的小数据中转 | `vllm/v1/worker/gpu/buffer_utils.py:89-120` |

### 1.2 谁把什么交给谁（连线，按一步之内的时间顺序）

1. 引擎主线程 → `execute_model(scheduler_output)`：带着**差量**（新请求 / 旧请求的新 block 与已算数 / 结束与抢占的 id）。
2. 状态同步：删结束的槽、给新请求占槽并**暂存**写入、更新旧请求，然后 `apply_staged_writes` 一次性把暂存的写入灌进 GPU 张量（`vllm/v1/worker/gpu/model_runner.py:1880-1888`）。
3. 看批形状 → 选 CUDA graph 描述符（`vllm/v1/worker/gpu/model_runner.py:1905-1948`）。
4. 输入拼装：GPU 内核从槽位张量里“按本批顺序”摘出 `input_ids / positions / seq_lens / block table / slot_mapping`（`vllm/v1/worker/gpu/model_runner.py:1963-1966`）。
5. 前向（graph 回放或直接调模型）→ 存 `execute_model_state` → 返回 `None`（`vllm/v1/worker/gpu/model_runner.py:2172-2262`）。
6. 引擎主线程 → `sample_tokens(grammar_output)`：取出前向结果，选最后位置的隐状态算 logits → 采样 → 建 `AsyncOutput`（copy_stream 上排拷贝）→ `post_update` 更新槽位（`vllm/v1/worker/gpu/model_runner.py:2279-2424`）。
7. 返回 `AsyncOutput`；晚一步，引擎主线程调 `get_output()` 等 event、拿到 token id（`vllm/v1/worker/gpu/async_utils.py:192-203`）。

### 1.3 一个关键的“时间不连续”（建议画三条时间线）

- **CPU（引擎主线程）**：发射 S1 → 去调度 S2、准备并发射 S2 → 此时才回头等 S1 的拷贝 event → 处理 S1 结果。
- **主 stream**：`前向 S1 → 采样 S1 → 状态更新 S1 → S2 的输入拼装内核 → 前向 S2 …`，全部按发射顺序串行执行。
- **copy_stream**：`等主 stream 到采样结束 → 拷贝 S1 → record(event1)`。

读者最该看明白：**S2 的输入拼装内核在主 stream 上排在 S1 状态更新之后**，所以它读到的 `last_sampled_tokens` 一定已是 S1 的采样结果——哪怕 CPU 发射 S2 的那一刻 S1 还没算完（顺序保证来自“同一 stream 内核按序执行”，这是 CUDA 的基本语义；代码里相关体现见 §3.2）。

---

## 2. 问题一：常驻请求状态是什么？为什么不每步从 SchedulerOutput 重建？

**是什么**：`RequestState` 里一组“第一维 = `max_num_reqs`”的张量，一个请求对应其中的一行（一个**槽位**）。

- 取槽：`free_indices = list(range(max_num_reqs))`，`add_request` 里 `free_indices.pop()`（`vllm/v1/worker/gpu/states.py:30`、`vllm/v1/worker/gpu/states.py:100`）；还槽：`remove_request` 里 `append` 回去（`vllm/v1/worker/gpu/states.py:133`）。注意 `pop()` 弹的是列表**末尾**，所以第一个请求拿到**最大的槽号**。
- **本例（设 `max_num_seqs=1024`；该值来自调度器底稿 §2 的默认档，但本机实际要看你的显存）**：我们的请求是第一个，拿到槽 **1023**。以下例子里“槽 1023”都是这个设定。
- 每槽的字段（`vllm/v1/worker/gpu/states.py:37-70`）：

| 字段 | 位置 | 含义 | 本例初值（S1 同步完） |
|---|---|---|---|
| `all_token_ids[槽, :]` | 默认 pinned 主机内存，GPU 经 UVA 读写（`vllm/v1/worker/gpu/states.py:34-42`） | 这个请求的全部 token（prompt + 已生成） | 前 20 格 = prompt |
| `prompt_len` / `prefill_len` | CPU 为准，复制到 UVA 缓冲 | 用户 prompt 长度 / 本次要预填的长度（抢占恢复后 ≥ prompt_len） | 20 / 20 |
| `total_len` | 显存 | 目前已有多少 token（含已采样） | 20 |
| `num_computed_tokens` | 显存（`.gpu`）+ CPU 乐观镜像（`_np`） | 已算过多少 token | 0 |
| `last_sampled_tokens[槽]` | 显存，`[max_num_reqs,1]` int64 | 上一步这个请求采样出的 token | 未定义（还没采样） |

- 大小直觉（设 `max_model_len=131072`，我自己算的）：`all_token_ids` 是 `1024 × 131072 × int32 ≈ 512 MiB`。【注释】作者承认它可以很大：「NOTE(woosuk): This tensor can be extremely large (e.g., several GBs)」「To save GPU memory, we use UVA instead of GPU by default, but」（`vllm/v1/worker/gpu/states.py:32-35`）；`ngram_gpu` / 后缀解码等要反复扫描才放显存。

**为什么不每步重建**

- 【文档】`docs/design/model_runner_v2.md:13` 说持久批处理的初衷：「Building these tensors from scratch each step is often very slow in Python, especially for large tensors like block tables.」；MRV2 的改法是把“持久状态张量”和“每步输入张量”**解耦**，每步从持久状态里 gather（`docs/design/model_runner_v2.md:33`）。V1 的做法是持久张量直接当模型输入，导致行必须紧凑有序、请求进出时要整体搬移，还得备份一份 `CachedRequestState`（`docs/design/model_runner_v2.md:25`）。
- 【文档】MRV2 的三条规则：固定行数预分配；每个请求在活跃期有**固定的行**；「Treat preemption as completion. On resume, re-add request data as fresh state.」（`docs/design/model_runner_v2.md:36-37`）。代码对应：`finish_requests` 把抢占的 id 并进结束集合一起删槽（`vllm/v1/worker/gpu/model_runner.py:1293-1300`）；恢复时调度器当“新请求”发来，`prefill_token_ids` 里带上已生成的 token（`vllm/v1/core/sched/output.py:50-51`），所以 `prefill_len ≥ prompt_len`（【注释】`vllm/v1/worker/gpu/states.py:48-50`：「Usually, prefill_len equals prompt_len, but in cases such as resumption after」「preemption, prefill_len may be greater.」）。
- **调度器发什么**：旧请求的 `CachedRequestData` 只有 `new_block_ids`、`num_computed_tokens`、`num_output_tokens`；【注释】`new_token_ids` 只在流水线并行时才用：「NOTE(woosuk): new_token_ids is only used for pipeline parallelism.」「When PP is not used, new_token_ids will be empty.」（`vllm/v1/core/sched/output.py:141-142`）。原因很自然（【推断】依据：采样发生在本 worker 的 GPU 上，token 已经写进 `all_token_ids`）：**worker 自己就是这些 token 的产生者**，没必要让 CPU 再发回来。
- 与 async scheduling 的关系（【文档】`docs/design/model_runner_v2.md:138`）：「GPU can derive values (for example with speculative decoding) that CPU may not know yet.」——CPU 在排第 N+1 步时根本**不知道**第 N 步采样出了什么，所以“把 token 再发回 worker”在时序上就不可能；状态必须留在 worker 里自己演进。

**CPU 上还留了一份“乐观镜像”**：`num_computed_tokens_np`，注释写明「Optimistic CPU mirror of num_computed_tokens (upper bound on GPU value).」（`vllm/v1/worker/gpu/states.py:64`）；每步 `update_requests` 用调度器发来的 `num_computed_tokens` 覆盖它（`vllm/v1/worker/gpu/model_runner.py:1384`）。用途是算 `seq_lens_cpu_upper_bound`（【注释】「CPU upper bound on seq_lens; padded entries left at zero.」`vllm/v1/worker/gpu/model_runner.py:1640`），让注意力后端无需把 `seq_lens` 从 GPU 同步回 CPU。【提交】`fe85a92e86`「[Core] Avoid seq_lens_cpu GPU->CPU sync (#40654)」（正文为空，只看到标题）；为什么说“上界”：【推断】投机解码被拒绝时 GPU 上的真值会比调度器乐观值小，而 CPU 只能给上界。

---

## 3. 问题二：一次 `execute_model` 里准备了哪些输入？本例第 1、2 步各是什么形状？

### 3.1 总顺序（每一步都是这样）

`vllm/v1/worker/gpu/model_runner.py:1865-2262`，去掉 PP / 投机 / LoRA / 多模态 / DP 分支后的主干：

1. **同步状态**：`finish_requests`→`free_states`→`add_requests`→`update_requests`→`block_tables.apply_staged_writes()`（`vllm/v1/worker/gpu/model_runner.py:1880-1888`）。顺序有讲究：先删（含抢占的）再加，同一个 id 重复到来（流式输入更新）时 `add_requests` 开头也会先 `_remove_request`（`vllm/v1/worker/gpu/model_runner.py:1319-1322`）。
2. **早退**：`total_num_scheduled_tokens == 0`（例如只是给 worker 送“请求已结束”）→ 直接返回，不跑模型（`vllm/v1/worker/gpu/model_runner.py:1894-1899`）。**本例最后会有这样一步**，见 §8。
3. **`gather_batch_req_state`**：CPU 上算本批的顺序、每请求 token 数、哪些在预填（`vllm/v1/worker/gpu/model_runner.py:1412-1491`）。批内顺序由 `sort_batch_req_ids` 决定：【注释】「Order verification/decode -> short_extend -> prefill;」（`vllm/v1/worker/gpu/model_runner.py:2674`），理由也写了：「split_decodes_and_prefills relies on decode-like requests leading.」（`vllm/v1/worker/gpu/model_runner.py:2675`）。
4. **`dispatch_cg_and_sync_dp`**：选 CUDA graph 描述符（§5）。
5. **`prepare_inputs`**：构造 `InputBatch`（§3.2）。**`prepare_attn`**：摘 block table、算 slot_mapping（`vllm/v1/worker/gpu/model_runner.py:1963-1966`）；随后 `model_state.prepare_attn` 建注意力元数据。
6. **前向**（§5）→ **存 `ExecuteModelState`** → 返回 `None`（非最后 PP 级例外）。

### 3.2 本例 S1 / S2 的形状（单请求；除标“设”外按代码公式手算）

**设**：槽 1023；block 池分给本请求的物理 block 是 `[5, 6]`（设；调度器底稿只说“2 个 block”，没设编号）；`block_size=16`；`max_num_seqs=1024`；默认 `cudagraph_mode=FULL_AND_PIECEWISE`（`vllm/config/vllm.py:530`、`vllm/config/vllm.py:357`，默认 O2 档）。

| 量 | **S1**（预填） | **S2**（第一个 decode） | 怎么来的 |
|---|---|---|---|
| 调度器发来的请求数据 | `scheduled_new_reqs`：`prefill_token_ids`=20 个 token，`block_ids=([5,6],)`，`num_computed_tokens=0` | `scheduled_cached_reqs`：`num_computed_tokens=[20]`，本步无新 block（位置 20 仍在 block 6 内） | `vllm/v1/core/sched/output.py:41-51`、`vllm/v1/core/sched/output.py:139-147` |
| `num_scheduled_tokens` | 20 | 1 | 调度器底稿 §8 |
| 批内是否预填 | 是（`num_computed_prefill_tokens=0 < prefill_len=20`） | 否（20 < 20 不成立） | `vllm/v1/worker/gpu/model_runner.py:1444` |
| `decode_graph_eligible` / 均匀 decode 的 token 数 | `False` / `None` | `True` / `1` | `vllm/v1/worker/gpu/model_runner.py:1463`、`vllm/v1/worker/utils.py:1115-1123` |
| `idx_mapping`（批序号→槽） | `[1023]` | `[1023]` | `vllm/v1/worker/gpu/model_runner.py:1512-1514` |
| `query_start_loc` | `[0, 20, 20, 20, …]`（后面补满到 `max_num_reqs+1`） | `[0, 1, 1, 1, …]` | 补齐是为 FULL graph：【注释】「Pad for full CUDA graph mode.」`vllm/v1/worker/gpu/model_runner.py:1572`，`vllm/v1/worker/gpu/model_runner.py:1574` |
| `input_ids` | `[prompt 的 20 个 token]` 由 `prepare_prefill_inputs` 内核从 `all_token_ids` 拷出 | `[<法国>]` 由 `combine_sampled_and_draft_tokens` 内核从 `last_sampled_tokens[1023]` 写入 | `vllm/v1/worker/gpu/model_runner.py:1594`、`vllm/v1/worker/gpu/model_runner.py:1616`；内核见 §3.3 |
| `positions` | `0…19` | `[20]` | `prepare_pos_seq_lens`：`pos = num_computed_tokens + 块内偏移`（`vllm/v1/worker/gpu/input_batch.py:389-390`） |
| `seq_lens` | `[20, 0, 0…]` | `[21, 0, 0…]` | `seq_len = num_computed_tokens + query_len`（`vllm/v1/worker/gpu/input_batch.py:383`）；没用到的行写 0（`vllm/v1/worker/gpu/input_batch.py:400-402`） |
| `logits_indices`（要对哪几个位置算 logits） | `[19]` | `[0]` | `query_end - num_logits`（`vllm/v1/worker/gpu/input_batch.py:441`） |
| block table 行（本批第 0 行） | `[5, 6, 0…]` | `[5, 6, 0…]` | `gather_block_tables` 把槽 1023 那行摘到批内第 0 行（`vllm/v1/worker/gpu/block_table.py:173`、`vllm/v1/worker/gpu/block_table.py:293`） |
| `slot_mapping` | 位置 0-15 → `5×16+p = 80…95`；16-19 → `6×16+(0…3) = 96…99`；其后补 `-1` | `[100]`（位置 20 → block 索引 1 → 物理 block 6，`6×16+4`） | 公式 `block_numbers * kv_block_size + block_offsets`（`vllm/v1/worker/gpu/block_table.py:427`）；`PAD_SLOT_ID = -1`（`vllm/v1/attention/backends/utils.py:55`） |
| CUDA graph | **PIECEWISE**，token 数 padding 到 **24**（设：默认配置，网格“1、2、4、之后每 8 一档”见 `vllm/config/compilation.py:89-99`；20 向上取到 24 是我按网格公式推的） | **FULL**，1 请求 1 token，**不 padding** | §5 |
| 采样 | 只对第 19 行的隐状态算 logits；`num_sampled=1`（`seq_len == prefill_len`，不算“分块预填中”） | 同 | `vllm/v1/worker/gpu/model_runner.py:1765-1766`；`vllm/v1/worker/gpu/input_batch.py:530-533` |
| 采样后状态更新（`post_update`） | `last_sampled_tokens[1023]=<法国>`；`all_token_ids[1023][20]=<法国>`；`total_len 20→21`；`num_computed_tokens 0→20` | `last=<的>`；`all_token_ids[1023][21]=<的>`；`total_len 21→22`；`num_computed 20→21` | `vllm/v1/worker/gpu/input_batch.py:603-604`、`vllm/v1/worker/gpu/input_batch.py:609`、`vllm/v1/worker/gpu/input_batch.py:630-633` |

**为什么 S1 只对 1 个位置算 logits，而不是 20 个**：前向对 20 个位置都输出隐状态，但预测下一个 token 只需要最后一个。`logits_indices` 选行后才算 lm_head（`vllm/v1/worker/gpu/model_runner.py:1765-1766`）。【推断】动机：词表大，对 prompt 全部位置算 `[20, vocab]` 的 logits 纯属浪费；依据是代码里 lm_head 只作用在选出的行上，没有注释说明。

**补设场景（邻居请求，让“批序号 ≠ 槽号”可见）**（设）：请求 B 先到，已在 decode，槽 1023，已算 50 token；我们的请求后到，拿**槽 1022**。某一步调度器排了 B 的 1 个 decode token + 我们的 20 token 预填：

- `sort_batch_req_ids` 的 key 是 `(not draft_tokens.get(r), num != decode_query_len, num)`（`vllm/v1/worker/gpu/model_runner.py:2676-2681`）→ 批内顺序 `[B, 我们]`（decode 在前、预填在后）。
- `idx_mapping=[1023, 1022]`，`query_start_loc=[0, 1, 21]`；`input_ids` 第 0 格来自 `last_sampled_tokens[1023]`，第 1-20 格来自 `all_token_ids[1022][0:20]`。
- 这就是“解耦”的实际含义：**批内顺序随步变化（由注意力后端偏好决定），而槽位不动**；所有内核都带一个 `idx_mapping` 间接层。

### 3.3 `last_sampled_tokens` 怎么拼进下一步输入

`combine_sampled_and_draft_tokens` 内核（`vllm/v1/worker/gpu/input_batch.py:414-472`），每个请求一个 program：

1. 先写 `logits_indices`：`logits_start = query_end - num_logits`（`vllm/v1/worker/gpu/input_batch.py:441`）。
2. **守卫**：`if seq_len <= prefill_len: return`，【注释】「Handling prefill tokens. No sampled or draft tokens.」（`vllm/v1/worker/gpu/input_batch.py:449-452`）——还在预填的请求，输入已由 `prepare_prefill_inputs` 写好，**不许**被覆盖。S1 就走这条（20 ≤ 20）。
3. 第二个守卫：`first_logit_seq_pos = seq_len - num_logits`，只有 `≥ prefill_len` 才写；【注释】「Keep prompt-tail slots intact; only rewrite generated-token slots.」（`vllm/v1/worker/gpu/input_batch.py:454-456`）。
4. 写入：`input_ids[logits_start] = last_sampled_tokens[槽]`（`vllm/v1/worker/gpu/input_batch.py:457-459`）。S2：`seq_len=21 > 20`，`first_logit_seq_pos = 21−1 = 20 ≥ 20` → 把 `<法国>` 写进 `input_ids[0]`。
5. 有投机草稿 token 时还要把草稿写到查询段末尾（`vllm/v1/worker/gpu/input_batch.py:462-472`）。本例不涉及。

**为什么放在 GPU 内核里而不是 CPU 拼**：CPU 在排 S2 时，S1 的采样结果还在 GPU 上、没人知道（【文档】`docs/design/model_runner_v2.md:47`「MRV2 instead assumes the core model execution loop is a CUDA stream with no CPU synchronization points. CPU entrypoints queue work onto the stream.」）。把“读上一步结果”做成一个排在主 stream 上的内核，CPU 就不必等。

**同一 stream 的顺序保证了什么**（【推断】，依据：`sample_tokens` 里 `post_update` 与下一步 `combine…` 都在默认 stream 上发射，没有任何 CPU 等待；CUDA 同 stream 按序执行是平台语义，不是 vLLM 代码）：S2 的 `combine` 内核一定晚于 S1 的 `post_update` 内核，读到 `<法国>`。

---

## 4. 问题三：CPU 写、GPU 读的数据，怎么不打架？

读者看到 `StagedWriteTensor`、`UvaBufferPool`、`async_tensor_h2d` 会晕。统一的答案是**不让 CPU 和 GPU 同时碰同一块内存**。

- **暂存写**：新请求的 token、block id 等先 `stage_write` 记在 CPU 的数组里（`vllm/v1/worker/gpu/buffer_utils.py:218-255`），每步 `apply_write` 打包成连续缓冲、拷到 GPU，再**一个内核**把这些“差量”写进大张量（`vllm/v1/worker/gpu/buffer_utils.py:257-282`）。【文档】`docs/design/model_runner_v2.md:108-112` 描述了这五步，并说明动机是避免每步整张量 CPU→GPU 拷贝。
- **临时 pinned 拷贝**：【文档】`docs/design/model_runner_v2.md:72`「The CPU may modify `self.states` while GPU is still reading from it via async copy.」；V1 的办法是“异步屏障”（有易漏、重叠少两个缺点，`docs/design/model_runner_v2.md:74-79`）；MRV2 的办法是每次从持久 CPU 数据**新拷一份 pinned 临时张量**再异步拷到 GPU（`docs/design/model_runner_v2.md:94-100`）。代码里就是 `async_tensor_h2d`：不是 pinned 就先拷进新的 pinned 缓冲（`vllm/utils/torch_utils.py:736-740`）。
- **轮转 2 份的 UVA 池**：`UvaBufferPool` 用 `max_concurrency` 份缓冲轮转（`vllm/v1/worker/gpu/buffer_utils.py:113-120`）。【注释】「Default round-robin depth for the UVA buffer pools. Must be >= the number of」「concurrent in-flight steps (engine batch_queue_size).」（`vllm/v1/worker/gpu/buffer_utils.py:22-23`）。`GPUModelRunner` 启动时用 `vllm_config.max_concurrent_batches` 设它（`vllm/v1/worker/gpu/model_runner.py:263`）：开 async scheduling 时 = `pp_size + async_scheduling_depth − 1`，默认 `2`（`vllm/config/vllm.py:649-654`、`vllm/config/scheduler.py:275`）。**直白说**：S1 的 GPU 内核可能还在读 UVA 缓冲 A，CPU 就写 S2 的缓冲 B；若只有 1 份就会写坏。把深度调到 3，池也自动变 3 份。
- 保护这条纪律的开关：环境变量 `VLLM_GPU_SYNC_CHECK=warn|error`。【注释】「If set, enable GPU<->CPU synchronization checking around the worker's」「`execute_model` and `sample_tokens` calls」（`vllm/envs.py:681-682`），`Worker.execute_model/sample_tokens` 都套了 `@with_gpu_sync_check`（`vllm/v1/worker/gpu_worker.py:1503`、`vllm/v1/worker/gpu_worker.py:1511`）。它还会抓“`non_blocking` 却因为目标不是 pinned 而悄悄同步”的拷贝（`vllm/utils/gpu_sync_debug.py:11-14`）。这是“热路径里不许有 CPU 同步点”这条设计原则的**可执行版**。

---

## 5. 问题四：CUDA graph 什么时候用、什么时候退回 eager？

**三档运行模式**：`FULL`（整图回放）、`PIECEWISE`（分段图，注意力等不可捕获的算子留在图外）、`NONE`（eager）。默认 `FULL_AND_PIECEWISE`：【文档】「FULL_AND_PIECEWISE mode: Capture full cudagraph for decode batches and」「piecewise cudagraph for prefill and mixed prefill-decode batches.」（`vllm/config/compilation.py:759-760`）。默认优化级别是 O2（`vllm/config/vllm.py:530`），O2/O3 都配成这个模式（`vllm/config/vllm.py:357`、`vllm/config/vllm.py:378`）；O0 是 `NONE`，O1 是 `PIECEWISE`（`vllm/config/vllm.py:315`、`vllm/config/vllm.py:336`）。graph 在启动阶段 `capture_model` 里捕获好（`vllm/v1/worker/gpu/model_runner.py:1172`），之后每步只**查表**。

**一步里怎么选**（`CudaGraphManager.dispatch`，`vllm/v1/worker/gpu/cudagraph_utils.py:561-606`，文档串「Find the graph to run a batch with, else an eager descriptor.」）：

1. 先看是不是“均匀 decode”：输入里的 `uniform_token_count` 由 `get_uniform_decode_token_count` 给出——**形状均匀且 `decode_graph_eligible`** 才算（`vllm/v1/worker/utils.py:1115-1123`）。【注释】形状判断函数自己警告：「a prompt chunk can have a decode batch's shape.」（`vllm/v1/worker/utils.py:1109-1110`），所以才需要 `decode_graph_eligible`（批里没有预填，或预填的只是“已有上下文上多 1 个 token”的 `prefill_runs_as_decode`，`vllm/v1/worker/gpu/model_runner.py:1463-1475`）。
2. 是 → 取“能装下这些请求数的最小 decode graph”（FULL）；否 → 取“token 数 ≥ 本批 token 数的最小 token 键 graph”（PIECEWISE）。`_is_compatible` 检查 token 数、请求数、LoRA 数、微批数（`vllm/v1/worker/gpu/cudagraph_utils.py:115-142`）。
3. **都没有 → 返回一个 `cg_mode=NONE` 的描述符，即 eager**（`vllm/v1/worker/gpu/cudagraph_utils.py:599-605`）。

**退回 eager 的情形（逐条有代码）**：

| 情形 | 锚点 |
|---|---|
| 配置本身是 NONE（`--enforce-eager`、O0）→ 根本没捕获，`dispatch` 直接落到最后的 eager 描述符 | `vllm/v1/worker/gpu/cudagraph_utils.py:290-292`、`vllm/v1/worker/gpu/cudagraph_utils.py:579` |
| 本批 token 数超过捕获的最大尺寸，或请求数超过 decode graph 容纳量 | `vllm/v1/worker/gpu/cudagraph_utils.py:579-605` |
| profile 运行、或编码器-解码器模型本步有 encoder 输入（要更新交叉注意力缓存） | `vllm/v1/worker/gpu/model_runner.py:1928-1931`、`vllm/v1/worker/gpu/model_runner.py:1941` |
| DP 各 rank 之一要 eager → 全体 eager（collective 对齐形状） | `vllm/v1/worker/gpu/dp_utils.py:183-184`（【注释】「If any rank wants to run eager, all ranks run eager」）；各 rank 形状对齐见 `vllm/v1/worker/gpu/dp_utils.py:250-251` |
| DBO 微批拆分步 | `vllm/v1/worker/gpu/dp_utils.py:318-333` |

**本例**：S1 → 预填，不是 decode-eligible → token 键 PIECEWISE graph，padding 到 24。S2 → 均匀 decode、1 个请求 → FULL decode graph（decode 的请求数网格“1…6 逐个，之后步长变大”，`vllm/config/compilation.py:122-126`；n=1 有现成的）。S3…S8 同 S2。**设**：默认配置、单卡、没改 `cudagraph_capture_sizes`。

**回放有什么特别**：FULL 模式下 `run_fullgraph(batch_desc)` 不传输入张量；【注释】「NOTE(woosuk): Here, we don't need to pass the input tensors,」「because they are already copied to the CUDA graph input buffers.」（`vllm/v1/worker/gpu/model_runner.py:2174-2175`）。这就是 `InputBuffers` 要做成**定长持久缓冲**、而且内核要把没用的行写成固定值的原因：
- `seq_lens` 末尾写 0：【注释】「NOTE(woosuk): We do +1 because the last thread block is used」「to pad unused seq_lens as 0 for full CUDA graphs.」（`vllm/v1/worker/gpu/input_batch.py:401-402`）；
- `slot_mapping` 超出真实 token 的部分写 `-1`：【注释】「Pad remaining slots to -1. This is needed for CUDA graphs.」（`vllm/v1/worker/gpu/block_table.py:376`），这样 padding 的位置不会写进 KV cache；
- 内核里这些“padding 要显式处理”的写法，都是为了让**图里的形状固定、内容合法**。

**注意**：eager/PIECEWISE/FULL 只改变**前向怎么发射**；`prepare_inputs`、采样、`post_update` 都是同一套内核，不进 graph（采样前向结果由 `sample_tokens` 另起一次调用，见 §6）。

【提交】网格与分派在近期有大改：`abf98672ce`「[Core][CUDA Graph] Separate prefill (token) and decode (request) CUDA graph capture sizes (#63406)」、`c74dd2d208`「[Bugfix][MRV2] Dispatch uniform decode to a padded FULL cudagraph (#53407)」（正文均为空，只看到标题）。别拿旧资料里“统一一张 capture size 表”去套。

---

## 6. 问题五：为什么前向和采样要拆成两次调用？`deferred_step` 怎么走？

**现象**：`execute_model` 前向发射完就 `return None`，把东西存进 `self.execute_model_state`（`vllm/v1/worker/gpu/model_runner.py:2249-2262`）；紧接着引擎调 `sample_tokens`，它第一件事是取出并**清空**这个槽（`vllm/v1/worker/gpu/model_runner.py:2282-2296`）。【注释】基类接口写明契约：「If this method returns None, sample_tokens should be called immediately after」「to obtain the ModelRunnerOutput.」（`vllm/v1/worker/worker_base.py:218-219`），还附一句坦白：「Note that this design may be changed in future if/when structured outputs」「parallelism is re-architected.」（`vllm/v1/worker/worker_base.py:221-222`）。

**为什么拆**：为了结构化输出（JSON schema、语法等）。语法掩码要用**上一步已采样出的 token** 才能算出来（【注释】`vllm/v1/engine/core.py:876-877`：「We now have the tokens needed to compute the bitmask for the」「deferred request.」）；而前向（最贵的部分）不依赖掩码，只有采样要用。拆开后，前向可以先发射，掩码晚到再采样。【提交】引入 `sample_tokens` 的是 `0cdbe7b744`「[Core] Async scheduling + structured outputs compatibility (#26866)」（正文为空，只看到标题；`git log -S'def sample_tokens'` 命中它）。

**即便不开 async scheduling，拆开也有用**：同步路径 `step()` 里，先发射前向，**然后在 CPU 上算语法掩码**，再等结果：`execute_model(non_block=True)`→`get_grammar_bitmask`→`_wait_for_output`→必要时 `sample_tokens(grammar_output)`（`vllm/v1/engine/core.py:727-732`）。也就是 CPU 算掩码的时间和 GPU 跑前向的时间重叠了。（代码顺序是事实；“为此而设计”是【推断】。）

**两步的接力靠“单槽”**：`execute_model_state` 只有一个槽，所以**同一个 worker 上，两次 `execute_model` 之间必须夹一次 `sample_tokens`**。【注释】`vllm/v1/engine/core.py:784`：「Workers need a deferred step's sample_tokens() before the next step.」；若上一次 `execute_model` 失败，槽为空，`sample_tokens` 返回 `None`（【注释】「The prior execute_model call must have failed.」`vllm/v1/worker/gpu/model_runner.py:2283`），引擎侧据此重放那次异常（`vllm/v1/engine/core.py:841-843`）。

### `deferred_step` 在本例结构化输出场景下的走法（补设场景 B，设）

**设**：同一个请求另带了 JSON schema（`response_format`），`async_scheduling_depth=2`（默认）。

1. **S1**：调度后 `AsyncScheduler._update_after_schedule` 里：`pending_structured_output_tokens |= request.num_output_placeholders > 0`——此刻占位符为 0 → `False`（`vllm/v1/core/sched/async_scheduler.py:42-43`）；随后占位符 +1（`vllm/v1/core/sched/async_scheduler.py:45-48`）。引擎看到 `pending=False`：立刻 `get_grammar_bitmask` → `sample_tokens`，入队（`vllm/v1/engine/core.py:800-814`）。（第一个 token 的掩码只靠语法起点，不需要任何已生成 token。）
2. **S2**：占位符 =1 > 0 → `pending=True`。`execute_model(S2)` 照常发射前向，但**不** `sample_tokens`：`self.deferred_step = (scheduler_output, exec_future)`，**不入 `batch_queue`**（`vllm/v1/engine/core.py:816-818`、`vllm/v1/engine/core.py:820-822`）。
3. 往下走到“阻塞取旧结果”：`batch_queue.pop()` 取到 S1，等拷贝、`update_from_output`（其中把 token 喂给语法 `accept_tokens`，`vllm/v1/core/sched/scheduler.py:2534`）（`vllm/v1/engine/core.py:837-852`）。
4. 然后处理遗留：队列里已没有与 S2 共享请求的步（`shares_requests_with`，`vllm/v1/core/sched/output.py:345-349`）→ 取 S2 的语法掩码 → `sample_tokens(grammar_output, non_block=True)` → 入队（`vllm/v1/engine/core.py:861-882`）。
5. **S3** 起重复：`deferred_step is None` 才允许再调度（`vllm/v1/engine/core.py:785`），所以**同一时刻最多有一个“只发射了前向”的步**，与单槽 `execute_model_state` 相互咬合。

**效果**：结构化输出请求的每一步，GPU 时间线是 `…采样 S1 → 前向 S2（已排好）→ [等 CPU 算掩码] → 采样 S2 …`。CPU 等 S1 结果、算掩码的时间，被 GPU 的 S2 前向盖住（只要前向比这段 CPU 工作长）。**非结构化输出请求不受影响**，仍是“前向+采样紧挨着发射”。深度 > 2 时的推迟条件更严：要等“队列里没有与它共享请求的步”（`vllm/v1/engine/core.py:859-863` 的注释与条件）。

**没有掩码时**：`sample_tokens` 的 `grammar_output=None`，`sample` 里 `if grammar_output is not None and logits.shape[0] > 0` 才做 `apply_grammar_bitmask`（`vllm/v1/worker/gpu/model_runner.py:1773-1782`）。掩码也走一条独立 stream 异步拷到 GPU（`vllm/v1/worker/gpu/structured_outputs.py:43-47`）。

---

## 7. 问题六：采样结果怎么经 `copy_stream` 拷回 CPU？event 什么时候 synchronize？

### 7.1 在 `sample_tokens` 里的先后（`vllm/v1/worker/gpu/model_runner.py:2326-2424`）

1. `self.sample(...)`：选 `logits_indices` 的行 → `model_state.compute_sample_logits` → 掩码/语法 → `sampler(logits, input_batch)`，得到 `sampled_token_ids` `[num_reqs,1]`、`num_sampled`（`vllm/v1/worker/gpu/model_runner.py:1765-1790`）。全部是排在主 stream 上的内核，CPU 立刻返回。
2. **先建 `AsyncOutput`**（`vllm/v1/worker/gpu/model_runner.py:2377`）：构造函数里 `with stream(copy_stream, main_stream)` 切到拷贝流，**`copy_stream.wait_stream(main_stream)`**（拷贝流等主流排到这里为止的所有工作——即采样内核）→ `async_copy_to_np(sampled_token_ids)`、`num_sampled` 等 → `copy_event.record(copy_stream)`（`vllm/v1/worker/gpu/async_utils.py:147-190`）。
3. **后做 `postprocess_sampled`**（即 `post_update` 内核，更新 `last_sampled_tokens` 等）（`vllm/v1/worker/gpu/model_runner.py:2418-2424`）。
4. 返回 `AsyncOutput`（`vllm/v1/worker/gpu/model_runner.py:2479`）。

### 7.2 “晚一步”的 `synchronize`

引擎在下一轮（队列满了才）`batch_queue.pop()` 取最旧一批，`_wait_for_output(future)`；UniProc 的 `AsyncOutputFuture.result()` 调 `AsyncOutput.get_output()`（`vllm/v1/executor/uniproc_executor.py:32-44`），其第一句就是 **`self.copy_event.synchronize()`**（`vllm/v1/worker/gpu/async_utils.py:192-193`）。只有这里 CPU 会真的等 GPU。之后 `sampled_token_ids.tolist()`，并按 `num_sampled` 把每行**截短**：`del token_ids[num_tokens:]`（`vllm/v1/worker/gpu/async_utils.py:199-202`）——所以分块预填的中间块（`num_sampled=0`）会得到一个**空列表**，不会泄漏一个“假 token”。这个 0 是内核算出来的：`is_chunked_prefilling = seq_len < prefill_len` 时把 `num_sampled` 置 0（`vllm/v1/worker/gpu/input_batch.py:530-533`）。

拷的东西很小（每请求几个整数），所以这一步的代价是**延迟**而不是带宽。（这是我的判断，依据：`AsyncOutput` 拷的是 `sampled_token_ids`、`num_sampled` 等逐请求标量，见 `vllm/v1/worker/gpu/async_utils.py:150-158`。）

### 7.3 逐行“为什么”

| 位置 | 为什么 | 等级 |
|---|---|---|
| 先建 `AsyncOutput` 再 `postprocess_sampled`（`vllm/v1/worker/gpu/model_runner.py:2414-2417`） | 「NOTE: This is intentionally done after creating the AsyncOutput,」「ensuring that `copy_event` is recorded before calling postprocess.」「This sequencing may slightly reduce latency as async D2H copy does not」「need to wait for the postprocess to finish.」 | 【注释】 |
| `AsyncOutput` 里保留 GPU 张量引用（`vllm/v1/worker/gpu/async_utils.py:130-136`） | 「NOTE(woosuk): We must retain references to the GPU tensors,」「as the copy operations are performed on a different CUDA stream than」「the one where the tensors were created.」——否则主 stream 上的张量被分配器回收复用，而拷贝流还没读完。这条注释是【提交】`ae0ce1be27`「[Model Runner V2][BugFix] Keep reference to GPU tensors in AsyncOutput (#29623)」加的（我读了 diff：它加了这条 NOTE，并把原来会覆盖 GPU 张量引用的 `self.num_sampled_tokens = …to("cpu")` 改名为 `num_sampled_tokens_cpu`；提交正文为空） | 【注释】【提交】 |
| 用**可睡眠** event（`vllm/v1/worker/gpu/async_utils.py:139-140`） | 「Blocking (sleep) event to avoid busy-polling the accelerator driver lock.」。这条注释和 `blocking=True` 是【提交】`d3e69fd671`「[Perf] Use blocking CUDA events to avoid busy polling cuda driver lock (#47081)」加的（我读了 diff；正文为空）。【推断】：CPU 在 `synchronize` 里等的同时，输入/输出线程也要用 CUDA 驱动，忙轮询会抢锁；依据只是这条注释和同进程有其它线程这一事实（见上一趟 §4） | 【注释】【提交】【推断】 |
| 每个 `AsyncOutput` **自己新建** event（`vllm/v1/worker/gpu/async_utils.py:140`） | 【提交】`62095e82c1`「[BugFix][MRV2] Fix cuda event reuse race (#39115)」：这次提交把 runner 上共享的 `self.output_copy_event` 删掉、改成构造函数里 `torch.cuda.Event()`（我读了 diff：`model_runner.py` 删 `self.output_copy_event = …`，两处调用不再传 `copy_event=`）。【推断】原因：队列里同时有 2 个在飞的 `AsyncOutput`，共用一个 event 会让 S2 的 `record` 覆盖 S1 的，S1 的 `synchronize` 就等错了。提交正文为空，这段推理是我的 | 【提交】【推断】 |
| 拷贝流上先 `wait_stream(main_stream)`（`vllm/v1/worker/gpu/async_utils.py:148`） | 不加就可能在采样内核还没写完时开始拷。依据是 CUDA 语义，代码无注释 | 【推断】 |

---

## 8. 问题七：请求结束、被抢占时槽位怎么还？本例的“空步”

本例 S8 之后，输出 7 回来是 `<eos>`，调度器 `_free_request`，把 id 加进自己的 `finished_req_ids`（调度器底稿 §8；`vllm/v1/core/sched/scheduler.py:3244`）。这个 id **不会立刻通知 worker**，而是**附在下一次 `SchedulerOutput` 里**（`vllm/v1/core/sched/scheduler.py:1771`，之后清空，`vllm/v1/core/sched/scheduler.py:1915`）。为了让它被送出去，调度器的 `has_requests()` 把“有已结束但还没送出的请求”算作有活（`vllm/v1/core/sched/scheduler.py:3364-3367`）。

所以本例会多一次 `schedule()`，产出一个 `total_num_scheduled_tokens == 0` 的输出：worker 的 `execute_model` 里 `finish_requests` 释放槽 1023，然后命中早退直接返回（`vllm/v1/worker/gpu/model_runner.py:1880`、`vllm/v1/worker/gpu/model_runner.py:1894-1899`）。（这一步的存在是我从两段代码合起来推的，依据是 `has_requests` 的定义与早退分支，没有找到明确描述它的注释或测试。**【推断】**）

槽的“还”只是把号码放回 `free_indices`（`vllm/v1/worker/gpu/states.py:133`），**GPU 张量里的旧内容不清**，下个占用者 `add_request` 会覆盖（`all_token_ids` 等从 0 开始写；`vllm/v1/worker/gpu/states.py:100-115`）。为什么释放槽位时不怕上一步的 GPU 内核还在用这一行：【推断】槽号归还只改 CPU 侧列表；下一个占用者对该行的写入（`apply_staged_writes` 的内核）和新一步的内核都排在**同一条主 stream** 的后面，必然晚于仍在飞的旧内核执行。依据是 §1.3 同 stream 保序；代码里没有针对“槽位复用”的注释或测试。

顺序细节：`_remove_request` 里先 `model_state.remove_request` 再 `req_states.remove_request`，【注释】「Call model_state.remove_request *before* req_states.remove_request」「so the model_state can still look up the slot index.」（`vllm/v1/worker/gpu/model_runner.py:1268-1269`）。`finish_requests` 对 id **排序后**逐个删：【注释】「Sorted so every TP rank frees request slots in the same order.」「Features like batch-sharded sampling derive rank request ownership」「from the slot index.」（`vllm/v1/worker/gpu/model_runner.py:1296-1298`）。

---

## 9. 值得做成源码页的片段（建议 6 段）

> 行号在 HEAD 上核对过；括号里是我觉得最值得批注的行。

### 片段 1：常驻状态的字段与“占槽”
`vllm/v1/worker/gpu/states.py:26-72`（字段）+ `vllm/v1/worker/gpu/states.py:96-121`（`add_request`）。合计约 70 行。
- `:30` `free_indices = list(range(max_num_reqs))`、`:100` `.pop()`：弹末尾，首个请求得最大槽号。【推断】没有注释说明为什么弹末尾（只是 list 的 O(1) 取法最顺手），§11 单列。
- `:32-35` 注释：大张量走 UVA。【注释】
- `:44-52` 注释：`prompt_len` 与 `prefill_len` 不同，抢占恢复后 `prefill_len` 更大，前者用于 prompt logprobs / 频率惩罚，后者决定预填多少。【注释】
- `:64` 「Optimistic CPU mirror of num_computed_tokens (upper bound on GPU value).」。【注释】
- `:68-70` `last_sampled_tokens`，**本页的主角**：形状 `[max_num_reqs, 1]`。
- `:111-115` 所有写入都是 `stage_write` / `stage_write_elem`，**这里还没进 GPU**，要等 `apply_staged_writes`（`vllm/v1/worker/gpu/states.py:120-125`）。读者最易漏看。

### 片段 2：`execute_model` 的开头（同步状态 + 早退 + 选 graph）
`vllm/v1/worker/gpu/model_runner.py:1876-1899` + `vllm/v1/worker/gpu/model_runner.py:1903-1956`。约 75 行。
- `:1880-1883` 四步顺序：先删（含抢占）、再加、再更新。
- `:1888` `block_tables.apply_staged_writes()`：此刻才把所有 block id 写进显存。
- `:1894` 早退：`total_num_scheduled_tokens == 0`（§8 的“空步”）。
- `:1905` `gather_batch_req_state`、`:1933` `dispatch_cg_and_sync_dp`：形状决定一切，包括用不用 graph。
- `:1951` `batch_desc.num_tokens == 0`：DP 下所有 rank 都没活才早退（【注释】「All DP ranks have zero tokens to run.」`vllm/v1/worker/gpu/model_runner.py:1952`）。

### 片段 3：输入拼装——`last_sampled_tokens` 的去向
`vllm/v1/worker/gpu/model_runner.py:1594-1630`（调用点）+ `vllm/v1/worker/gpu/input_batch.py:414-472`（内核）。约 85 行；读内核为主。
- `:1594-1602` 只有 `has_prefill` 才调 `prepare_prefill_inputs`。
- `:1605-1611` `prepare_pos_seq_lens`。
- `:1614-1615` 【注释】「Some input token ids are directly read from the last sampled tokens」（顶层卡片已引）。
- 内核 `:449-452` 守卫 `seq_len <= prefill_len` → return；`:454-459` 第二守卫与写入；`:462-472` 草稿 token（本例跳过）。
- `:509-511` 的 `BLOCK_SIZE=next_power_of_2(num_speculative_steps + num_new_sampled_tokens)`：【注释】「NOTE(woosuk): Add num_new_sampled_tokens to ensure the block covers the」「last sampled token in addition to all draft tokens.」（`vllm/v1/worker/gpu/input_batch.py:507-508`）。

### 片段 4：CUDA graph 分派 + 前向分支
`vllm/v1/worker/gpu/cudagraph_utils.py:561-606` + `vllm/v1/worker/gpu/model_runner.py:2172-2180`、`vllm/v1/worker/gpu/model_runner.py:2212-2219`。约 60 行。
- `vllm/v1/worker/gpu/cudagraph_utils.py:580-587` 先查 token 键候选，再把 decode 候选插到最前；`:588-598` 逐个 `_is_compatible`；`:599-605` 全部不兼容→ NONE。
- `vllm/v1/worker/gpu/model_runner.py:2172` FULL：`run_fullgraph(batch_desc)`，不传输入（`:2174-2175` 注释）。
- `:2212-2214` PIECEWISE：`run_pw_graph`；`:2218-2219` eager：直接 `self.model(**model_inputs)`。
- 读者应注意：三条分支**共用同一份** `input_batch` 和 `attn_metadata`，区别仅是“怎么发射前向”。

### 片段 5：采样→异步拷贝→更新状态
`vllm/v1/worker/gpu/model_runner.py:2376-2424`（约 50 行）+ `vllm/v1/worker/gpu/async_utils.py:130-150`、`vllm/v1/worker/gpu/async_utils.py:190-203`（约 35 行）。
- `vllm/v1/worker/gpu/model_runner.py:2377` 建 `AsyncOutput`；`:2414-2417` 注释（先建 event 再后处理）。
- `vllm/v1/worker/gpu/async_utils.py:139-140` 可睡眠 event；`:147-148` 切流 + `wait_stream`；`:190` `record`；`:193` `synchronize`；`:201-202` 截短。
- 读完后应能回答：**“CPU 在哪一行真正等 GPU？”——只有 `vllm/v1/worker/gpu/async_utils.py:193`。**

### 片段 6：`deferred_step`
`vllm/v1/engine/core.py:784-828` + `vllm/v1/engine/core.py:857-882` + `vllm/v1/core/sched/async_scheduler.py:40-48`。约 75 行。
- `vllm/v1/engine/core.py:785` 守卫 `deferred_step is None`；`:800` `pending_structured_output_tokens` 分叉；`:816-818` 推迟；`:822` 只有没推迟才入队；`:859-863` 补采样的条件；`:878-882` 补采样并入队。
- 对照 `async_scheduler.py:43`（`|= request.num_output_placeholders > 0`）看“谁触发了推迟”。

---

## 10. 没查到为什么

1. **`free_indices.pop()` 为什么弹末尾**：没有注释和测试；首个请求拿 1023 是我读代码推出来的，没找到它是有意的证据。
2. **`copy_stream` 的 `wait_stream(main_stream)` 为什么必须**：无注释，只有 CUDA 语义的推断。
3. **非同步状态下 `execute_model` 为什么返回 `None` 而不是直接返回 `AsyncOutput`**（即为什么前向结果要藏在 runner 的单槽字段里）：只找到基类契约和结构化输出的动机，没有找到关于“多进程 RPC 返回体”的任何说明。
4. **S1 padding 到 24 而不是别的数**：这是我按 `_walk_grid` 公式手推的，没有测试逐值断言；实际捕获网格受平台默认（`CUDAGraphCaptureDefaults`）影响，我没逐平台查。
5. **“上界”一词的精确语义**（`seq_lens_cpu_upper_bound`）：提交 `fe85a92e86` / `9af6a5ed75` 正文都为空，只看到标题。
6. **`StagedWriteTensor` 的 `_WRITE_BLOCK_SIZE=1024` 取值理由**：没找到。（只看到提交标题 `fc2b6ab45e`「[Perf][MRV2] Parallelize staged writes across 1024-element chunks (#62698)」，没有正文。）
7. 本仓库的大部分相关提交**正文为空**，所以【提交】来源多半只有标题；凡标题之外的理由我都另标了【推断】。

---

## 11. 读者最容易困惑的地方（供讲解员挑选）

1. **“异步”不是另一个线程**：整个 `execute_model` + `sample_tokens` 在引擎主线程里同步执行；重叠发生在 GPU 与（主线程做的）调度/输入准备之间。
2. **“槽位”≠“批内序号”**：两个编号不要混。槽位固定，批内序号每步重排（`idx_mapping` 是桥）。
3. **“更新”并不立刻发生**：`add_request` 只是暂存（`stage_write`），要等 `apply_staged_writes` 才进 GPU；不看这一点就读不懂为什么 `execute_model` 开头有那么多 `apply_*`。
4. **`last_sampled_tokens` 谁写谁读**：写 = `post_update` 内核（S_k 的 `sample_tokens` 里排的）；读 = `combine_sampled_and_draft_tokens` 内核（S_{k+1} 的 `execute_model` 里排的）；中间 CPU 没参与，靠同 stream 保序。
5. **预填请求的输入不能被覆盖**：两个守卫（`seq_len <= prefill_len` 与 `first_logit_seq_pos >= prefill_len`）——这是“看起来多余”的典型。
6. **`return None` 的 `execute_model`**：不是出错，是故意的；结果在 `sample_tokens` 才出现。
7. **CPU 乐观镜像**与 GPU 真值：两处 `num_computed_tokens`，一个给 CPU 算上界用，一个给 GPU 内核用。
8. **分块预填的中间块也会“采样”**，只是 `num_sampled` 被内核置 0、结果被丢弃。

## 12. 可改进处【观点】

1. 【观点】`execute_model` 一个函数接近 400 行，混着 PP / DP / LoRA / 微批 / 多模态分支；主干（§3.1 的六步）很难一眼读出。可按“同步状态 / 选形状 / 拼输入 / 发射前向”拆成小函数。
2. 【观点】`ExecuteModelState` 靠“必须紧接 `sample_tokens`”的口头契约连接两次调用（基类 docstring 自己也承认设计可能再变，`vllm/v1/worker/worker_base.py:221-222`）；若让 `execute_model` 返回一个显式的 handle，单槽字段与引擎侧 `deferred_step` 的“同一时刻最多一个”约束就能由类型体现，而不是靠约定。
3. 【观点】`free_indices.pop()` 弹末尾使首个请求得 1023，调试时不直观；若有意，建议注释一句。

---

## 附：只点名、不展开的相关机制

- 流水线并行（`pp_handler`，`vllm/v1/worker/gpu/model_runner.py:1307-1313`、`vllm/v1/worker/gpu/model_runner.py:2299-2323`）：非最后一级 `execute_model` 返回 `IntermediateTensors`，`new_token_ids` 才会被使用。
- 投机解码（`speculator` / `draft_tokens` / 拒绝采样，`vllm/v1/worker/gpu/model_runner.py:2426-2465`）：`num_sampled` 可能 >1、`num_rejected` 非 0，`post_update` 因此用 `query_len − num_rejected` 更新已算数（`vllm/v1/worker/gpu/input_batch.py:627-634`）。
- Gumbel 采样、Triton 采样器（`docs/design/model_runner_v2.md:146-149`）；LoRA、编码器缓存、DP/EP 的 dummy run：入口见 `vllm/v1/worker/gpu/model_runner.py:914`。
