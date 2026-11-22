# 「模型前向 + 采样」框内部：补充底稿

> 研究对象：sglang 仓库 HEAD = `c569c43446`。这是顶层底稿（`manuscript.md`）里 §4.6 / §8 第 8 步那个框的下一层。
> 锚点相对仓库根目录；只写 `:行号` 的简写，指同一条目里前面最近出现的完整路径。
> 每条「为什么」后标来源：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 例子里凡标「设」的数都是为了讲解假设的，不是代码常量。
> 顶层已讲清的部分（Scheduler 事件循环、`ScheduleBatch` 怎么造、三层显存单元、结果处理）这里不重复，写「见上一趟 §x」。

---

## 0. 先给结论

这个框一次调用做的事：**把 CPU 侧选好的一批请求（`ScheduleBatch`）在 GPU 上算一遍，给每个请求吐出 1 个 token id，再把「下一步要用的输入」留在 GPU 上、把「CPU 要看的结果」异步拷回来。**

框里按顺序有 5 个部件：

| # | 部件 | 一句话 | 入口锚点 |
|---|---|---|---|
| ① | `TpModelWorker.forward_batch_generation` | 总管：打包 → 前向 → 采样（或推迟采样） | `python/sglang/srt/managers/tp_worker.py:624` |
| ② | `ForwardBatch.init_new` | 打包：把 `ScheduleBatch` 变成模型能吃的张量批 | `python/sglang/srt/model_executor/forward_batch_info.py:937` |
| ③ | `ModelRunner._forward_raw` | 三路分叉：decode 图回放 / prefill 图回放 / eager | `python/sglang/srt/model_executor/model_runner.py:2093` |
| ④ | 模型 `forward`：逐层 attention + `LogitsProcessor` | 按 `req_to_token` 找 KV、算出每个请求最后一个位置的 logits | `python/sglang/srt/models/llama.py:611`（以 Llama 为例） |
| ⑤ | `ModelRunner.sample` → `Sampler` | logits → `next_token_ids` | `python/sglang/srt/model_executor/model_runner.py:2231` |

框的「外壳」在 `Scheduler.run_batch`：它决定**在哪条 CUDA 流上**调 ①、调完把 token 登记进 `FutureMap`、再发起 GPU→CPU 异步拷贝（`python/sglang/srt/managers/scheduler.py:5388`；见 §3.9）。

读者最容易想错的三件事：
1. **`ForwardBatch` 大半不是「新造」的，而是 `ScheduleBatch` 张量的别名。**真正新算的主要是 `positions` 和 extend 专用的几个长度张量（§3.1）。
2. **prefill 也能用 CUDA graph，而且 CUDA 上默认就开**（上一趟写「prefill 通常走另一条路」，在 HEAD 上要修正，见 §8）。只是它的图和 decode 的图**长得不一样**：prefill 图只录「transformer 躯干」，attention 是图的「断点」，logits 尾巴在图外 eager 跑（§3.4）。
3. **采样在图外。**decode 图里包含 lm_head + logits_processor，但不含 `Sampler`；采样是 `tp_worker` 在图之后单独调的（§3.8）。

---

## 1. 状态放哪、谁读谁写

读者要画「线」的时候，最关键的是下面几个**常驻对象**——框里的部件几乎只是在它们之间搬数据：

| 状态 | 住在哪 | 框里谁写 | 框里谁读 |
|---|---|---|---|
| `req_to_token`（`int32 [size+1, max_context_len]`，行=请求、列=第几个 token、值=KV 槽位号） | `ReqToTokenPool`：`python/sglang/srt/mem_cache/memory_pool.py:382`，张量创建 `:422` | **不写**（分配时由 Scheduler 侧 `alloc_for_extend/decode` 写：`python/sglang/srt/mem_cache/allocation.py:524`、`:736`） | attention 后端建 `page_table` |
| KV 池（每层的 K/V 大数组） | `token_to_kv_pool`（见上一趟 §4.5） | 每层 attention 把本步 k/v 写到 `out_cache_loc` 指的槽 | attention 按 `page_table` 读 |
| 图的静态输入缓冲 | `buffer_registry`（decode：`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1744`） | `load_batch` 每次回放前覆盖 | 被录好的图读 |
| `FutureMap.output_tokens_buf / new_seq_lens_buf`（按 `req_pool_idx` 下标的 GPU 张量） | `python/sglang/srt/managers/overlap_utils.py:309` | 本步采样出的 token、`seq_lens+1`（`:749`、`:634`） | 下一步 `resolve_forward_inputs` 当 `input_ids`（`:166`） |
| `SamplingBatchInfo`（温度/top-p/top-k 张量 + grammar 掩码 + 惩罚项） | `python/sglang/srt/sampling/sampling_batch_info.py:130` | `_preprocess_logits` 里更新掩码、改 logits | `Sampler` |

---

## 2. 例子设定（接上一趟 §8）

沿用上一趟：prompt 20 个 token，前缀树命中 5 个，`prefix_len=5`、`extend_len=15`，`page_size=1`，单张 80GB GPU，Llama 风格模型。**以下均为设：**
- 这条请求占 `req_to_token` 的第 `r=3` 行；命中的 5 个前缀 token 的 KV 槽是 `[37,38,39,40,41]`；`alloc_for_extend` 给 15 个新 token 分到槽 `[212..226]`；第一个 decode 步再分到槽 `227`。
- 服务用默认配置（H100 级，tp=1，无投机、无 LoRA、纯文本、请求不要 logprob）。
- 采样参数：temperature>0，top_p=1，top_k=-1，min_p=0（「简单采样」）。
- 为让"padding"看得见，另设一个对照：decode 时 running 里有 5 个请求而不是 1 个。

---

## 3. 逐问逐答

### 3.1 `ScheduleBatch` → `ForwardBatch`：多了哪些张量？

**先给结论：大部分是别名，新算的很少。**

- `ForwardBatch.init_new` 里 `ret = cls(...)` 把 `input_ids / req_pool_indices / seq_lens / out_cache_loc / seq_lens_sum` 直接用 `batch` 上的同一批张量：`python/sglang/srt/model_executor/forward_batch_info.py:1029-1048`，其中注释 "Inputs aliased by reference from ScheduleBatch"（`:1038`）。类定义处也分了 "Borrowed from ScheduleBatch" 几组（`:445`、`:465`、`:517`、`:548`、`:577`）。
- `sampling_info` 也是引用 `batch.sampling_info`（`:1106`），而这时它已被 Scheduler 换成「仅供 forward 用的拷贝」：注释 "ScheduleBatch.sampling_info is already swapped to the forward-only"（`:988`），换的地方是 `Scheduler._forward_isolation`（`python/sglang/srt/managers/scheduler.py:5337` 的 `copy_for_forward()`）。
- **`input_ids` 不是 `init_new` 造的**，在它之前就已解析好：prefill 时 `prepare_for_extend` 把 id 留在**固定内存（pinned）CPU** 上，注释 "Stay on pinned CPU; H2D is deferred to forward stream via"（`python/sglang/srt/managers/schedule_batch.py:2423`），到 `run_batch` 里 `resolve_forward_inputs` 才在 forward 流上 H2D（`python/sglang/srt/managers/overlap_utils.py:142`）；decode 时 `batch.input_ids` 是 `None`，从 `future_map.output_tokens_buf[batch.req_pool_indices]` 取上一步采到的 token（`:166`）。

**`init_new` 真正新算的：**

| 张量 | 何时有 | 怎么来 | 锚点 |
|---|---|---|---|
| `positions` | 总是 | decode：`clamp_position(batch.seq_lens)` = `clamp(seq_lens-1, min=0)`；extend：`compute_position`（Triton 核，按 `prefix_len + 偏移` 填） | `python/sglang/srt/model_executor/forward_batch_info.py:1219`、`:1251`；`python/sglang/kernels/ops/attention/clamp_position.py:52`；`python/sglang/kernels/ops/attention/position.py:6` |
| `extend_seq_lens`、`extend_prefix_lens`（device int32）及 `*_cpu` 镜像 | 仅 extend | 从 Python 列表 H2D | `python/sglang/srt/model_executor/forward_batch_info.py:1222-1240`（注释 "Main path: H2D from host lists; populate *_cpu mirrors."） |
| `extend_start_loc`、`extend_num_tokens` | 仅 extend | 同上 `compute_position` 返回 | `:1251-1256` |
| `extend_is_final_cpu`、`contains_last_prefill_chunk` | 仅 extend | 逐请求判断是否最后一块 chunk | `:1012` |
| DP/MLP-sync 元数据、`num_token_non_padded` | 开 DP-attention 等才有意义 | `_init_num_token_non_padded`、`init_mlp_sync_metadata` | `:1159` 起 |

decode 时 extend 专用字段全是 `None`：注释 "extend-mode-only fields are None on decode/idle"（`:972`）。

**例子（prefill 15 个位置）：**

| 字段 | 值（设） | 说明 |
|---|---|---|
| `forward_mode` | `EXTEND` | `python/sglang/srt/model_executor/forward_mode.py:23`，注释 `It is also called "prefill" in common terminology.`（`:22`） |
| `input_ids` | 15 个 token id（prompt 的第 5..19 个） | 只有「没命中缓存」的那 15 个 |
| `req_pool_indices` | `[3]` | |
| `seq_lens` | `[20]` | 含前缀 |
| `out_cache_loc` | `[212..226]` | 15 个新槽 |
| `positions` | `[5,6,…,19]` | `prefix_len + 0..14` |
| `extend_prefix_lens` / `extend_seq_lens` / `extend_start_loc` | `[5]` / `[15]` / `[0]` | |

**为什么 `init_new` 强调「不改 ScheduleBatch」？** 注释 "init_new must not mutate ScheduleBatch; use explicit forward overrides."（`python/sglang/srt/model_executor/forward_batch_info.py:952`）【注释】。没有写原因；依据 Scheduler 侧的 `_forward_isolation` 文档 "Make SB transactional across one forward (overlap and non-overlap)."（`python/sglang/srt/managers/scheduler.py:5310`）【注释】，推断：overlap 下 forward 还在 GPU 上读它的时候，Scheduler 已经在拿同一个 `ScheduleBatch` 准备下一轮了，所以 forward 侧不能随手改【推断】。

> 历史：中间曾有 `ModelWorkerBatch` 一层，已去掉（见上一趟 §4.6 的提交 `63ba2f8d7b` / `58ece60703`）。

### 3.2 `_forward_raw` 的三路分叉：谁先判、判什么

`python/sglang/srt/model_executor/model_runner.py:2093-2193`，顺序是：

1. **先问 decode 图**：`can_run_graph = forward_mode.is_cuda_graph() and decode_cuda_graph_runner and decode_cuda_graph_runner.can_run_graph(forward_batch)`（`:2119-2123`）。`is_cuda_graph` 只对 `DECODE / TARGET_VERIFY / IDLE / DLLM_EXTEND` 为真（`python/sglang/srt/model_executor/forward_mode.py:92-98`）——**普通 EXTEND 永远不会进 decode 图**。
2. 能 → `decode_cuda_graph_runner.execute(...)`，**直接 return**（`python/sglang/srt/model_executor/model_runner.py:2140-2146`）。
3. 不能 → 先 `self._prepare_eager_forward_batch(forward_batch)`（`:2154`）。注释说明了为何放在这：decode 图那条路自己预填静态缓冲所以提前返回；prefill 图和 eager 都跑「活」的 batch，需要这一步先做 DP/MLP-sync padding（`:2148-2153`，"cuda-graph path above pre-pads its static buffers and returns"）【注释】。
4. **再问 prefill 图**：是 extend、prefill 图 runner 不是 `EagerRunner`、`can_run_graph` 为真，且没有 `token_indices_to_pool`、CP 允许（`:2164-2173`）→ `prefill_cuda_graph_runner.execute`（`:2185`），并把 `can_run_graph=True`（`:2188`）。
5. **否则 eager**：`self.eager_runner.execute(...)`（`:2190-2193`），注释 "Eager: decode / extend / idle dispatched inside the runner."。

**`EagerRunner` 永远会被造出来**：注释 "Always built: it serves both the fully-disabled case (decode/prefill" "runners point at it) and the eager fallback when a cg runner can't run a"（`python/sglang/srt/model_executor/model_runner_components/cuda_graph_setup.py:428-430`）【注释】。图被关掉时，runner 槽位里放的是它（prefill：`python/sglang/srt/model_executor/model_runner_components/cuda_graph_setup.py:545`），它的 `can_run_graph` 恒为 False（`python/sglang/srt/model_executor/runner/eager_runner.py:166`）。

**名不副实提醒**：`python/sglang/srt/model_executor/model_runner.py:2174` 的注释还写 "Prefill cuda graph (piecewise)."，但 `tc_piecewise` 后端已被删除（`python/sglang/srt/model_executor/cuda_graph_config.py:185` 的报错 "tc_piecewise was removed; select breakable, full, or disabled"）。现在默认的 prefill 图是 **breakable**（§3.4）。

**启动时谁建这些 runner**：`Scheduler` 初始化里 `self.tp_worker.init_cuda_graphs()`（`python/sglang/srt/managers/scheduler.py:1198`）→ `capture_cuda_graphs`：先 `EagerRunner`，再 prefill 图，再 decode 图（"prefill before decode, so both coalesce onto the"：`python/sglang/srt/model_executor/model_runner_components/cuda_graph_setup.py:436`）。

### 3.3 decode 为什么能用 CUDA graph？档位是怎么回事？

**能用的原因（先结论）：** decode 每个请求固定 1 个 token，形状只由「请求数 bs」决定；而每步变的东西（序列长度、KV 在哪、写到哪）都以**张量里的数据**存在，不是 kernel 的形状/参数。把这些张量放在固定地址的静态缓冲里，回放前覆盖内容，图就能「重放同一串 kernel、读到新数据」。（这一条是我从代码结构归纳出来的【推断】：没有哪处注释专门解释「为什么 decode 能用图」；依据是下面这些代码——形状只取决于 bs、可变信息全走静态缓冲。）

锚点（「怎么做」）：
- 非投机时每请求宽度 1：`raw_num_token = raw_bs * self.captured_req_width`（`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1726`）。
- 回放前 `load_batch`：选档 `bs = self._pad_to_bucket(raw_bs, self.capture_bs)`（`:1731`）→ `self.buffer_registry.fill_from(...)` 把 `ForwardBatch` 的张量拷进静态缓冲（`:1744`）→ `_init_replay_attn_metadata` 原地重写 attention 元数据（`:1864`；FA3 的实现 `_apply_cuda_graph_metadata`：`python/sglang/srt/layers/attention/flashattention_backend.py:3328`，页表由 `normal_decode_set_metadata` 写进预分配的 `metadata.page_table`：`:3294`）。
- `execute`：`output = self.backend.replay(self._replay_graph_key, forward_batch)`（`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:2189`），后端就是一句 `self._graphs[shape_key].replay()`（`python/sglang/srt/model_executor/runner_backend/full_cuda_graph_backend.py:257`）；回来按真实行数切片：`output.next_token_logits[: self.raw_num_token]`（`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:2210`）。
- 录制：每个档位先热身两次再录（`python/sglang/srt/model_executor/runner_backend/full_cuda_graph_backend.py:181-184`，注释 "Two warmups so kernels are loaded and one-time setup is paid before capture."）。录的 `forward` 是**整个模型 forward，含 lm_head + logits_processor**（`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1555`）。**采样不在图里。**

**判断是否可回放**（`can_run_graph`，`:980-1060`）。会回落 eager 的主要情形：
- `replace_embeds` 不为空（`:984`，注释 "Disable for token embedding overrides (dynamic per-request)"）；
- `beam` 不为空（`:1007`）；
- `batch_size > max_bs`（`:1031`：`cuda_graph_bs <= self.max_bs`）；
- DP-attention 下全局投票不通过（`require_mlp_sync`）；encoder-decoder 混批、TBO 不支持、ngram 宽度不符等（`:1034-1060`）。

**档位（batch size 桶）：**
- 默认来源：`generate_decode_cuda_graph_batch_sizes`（`python/sglang/srt/arg_groups/cuda_graph_hook.py:596-627`）。非投机：`[1,2,4,8,12] + range(16,257,8) + range(272,512,16) + range(512,max_bs+1,32)`（`:607-611`），再按 `max_bs` 截断，并保证 `max_bs` 本身在表里（`:625`）。投机解码另有一套更密的（`python/sglang/srt/arg_groups/cuda_graph_hook.py:613-621`）。
- `max_bs` 默认看显存：H100/A100 档（`gpu_mem < 90*1024`）tp<4 取 256，否则 512（`python/sglang/srt/arg_groups/memory_hook.py:137-148`）；若用户给了更小的 `max_running_requests` 而 `max_bs` 是默认值，会被压到它（`:186-191`）。
- **例子**：H100 tp=1 → `max_bs=256` → 36 个档：`1,2,4,8,12,16,24,32,…,256`。
- 取档：`_pad_to_bucket` 用 `bisect_left` 取「≥ 实际 bs 的最小档」，且断言不超过最大档——"can_run_graph should have rejected this batch"（`python/sglang/srt/model_executor/runner/base_cuda_graph_runner.py:188-202`）。

**padding 行里装什么——这是最容易看懵的地方。**
对照例子：5 个请求 → 取 8 档 → 多出 3 个「假请求」。缓冲区注册表（`cuda_graph_buffer_registry.py`）规定了假行的填充策略（docstring `python/sglang/srt/model_executor/cuda_graph_buffer_registry.py:598-608`）：
- `req_pool_indices / out_cache_loc / positions` → 填 0；
- `seq_lens` → 填 `seq_len_fill_value`（FA3 后端这个值是 1：`python/sglang/srt/layers/attention/flashattention_backend.py:3684-3686`）；
- `input_ids` → 只覆盖前 `raw_n` 个，尾部「不会被读」。

结果：假请求读 `req_to_token` **第 0 行**、写 KV 的**第 0 号槽**。这之所以无害，是因为三处刻意留了「第 0 号」：
- `ReqToTokenPool` "+1 padding row at index 0: cuda-graph padded batches default" "req_pool_indices to 0, so dummy reads/writes land here harmlessly."（`python/sglang/srt/mem_cache/memory_pool.py:416-417`），空闲槽从 1 开始（`:425`）【注释】；
- KV 池 "The padded slot 0 is used for writing dummy outputs from padded tokens."（`:3657`）【注释】；
- `FutureMap` "slot 0 mirrors KV padding row so"（`python/sglang/srt/managers/overlap_utils.py:324`）【注释】。

注册表注释还记了一个踩过的坑：`positions` 的假行必须清零，否则 flashinfer 的 verify 规划会读到陈旧的越界值 → 非法内存访问（"issue #24361"：`python/sglang/srt/model_executor/cuda_graph_buffer_registry.py:604-606`）【注释】；对应修复提交 `fde4004429` "[Fix] Reset positions tensor in CUDA graph runner when batch size differs from captured size (#24401)"【提交】。

**为什么档位这么稀（小的密、大的疏）？** 代码只给了「每档的成本」：`python/sglang/srt/arg_groups/memory_hook.py:123-124` 注释 "Measured on L40S with Qwen3 0.6B to 8B. Decode above the cap runs" "eager, and graphs up to bs=128 cost the 8B 1% of its KV pool."【注释】——图越多占显存越多。具体步长（12→16→24…）的选择理由没查到，见 §6。

### 3.4 prefill 为什么「通常不能」？——先纠正：HEAD 上 CUDA 默认就开

- 默认：`default_prefill_backend()` 在 CUDA 上返回 `Backend.BREAKABLE`（`python/sglang/srt/model_executor/cuda_graph_config.py:119-123`），docstring "Enable BCG by default on CUDA; other platforms remain opt-in."【注释】。对应提交 `0543246184` "Enable Breakable Cuda Graph as Default (#29458)"【提交】（只有标题）。decode 默认是 `FULL`（`:150`）。
- 但有一串「不兼容就自动关」的规则（`python/sglang/srt/arg_groups/cuda_graph_hook.py:170-`）：KDA 线性注意力、压缩 KV（DeepSeek-V4 等）、CP、DCP、两批重叠（TBO）、没验证过的 a2a、多模态模型（注释 "Multimodal prefill replay faults under BCG; allowlisted archs opt back in."：`:235`）。这些情况才「prefill 走 eager」。
- `full` 后端是 prefill 的另一个选项，实验性：日志 "cuda_graph_config[prefill].backend='full' is experimental. "（`:501`）；配置注释说得直白 "Opt-in: the padding waste is the" "operator's call."（`python/sglang/srt/model_executor/cuda_graph_config.py:59-60`）【注释】。

**breakable 图长什么样：**
- 只录「transformer 躯干」。注释："BCG and Full CG capture only the transformer body (layer_model.forward)," "not the LM head + logits_processor — the eager tail keeps the captured" "graph bs-invariant so req_slots is not bound by an (req_slots, vocab) buffer."（`python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py:739-741`）【注释】。
- **图的档位按「总 token 数」，不是请求数**：`generate_prefill_cuda_graph_batch_sizes`：`4..32` 步 4、`48..256` 步 16、`288..512` 步 32、…、上限 `max_bs`（`python/sglang/srt/arg_groups/cuda_graph_hook.py:576-590`）；`max_bs` 默认 = `chunked_prefill_size`（`python/sglang/srt/arg_groups/memory_hook.py:247-249`；H100 档 `chunked_prefill_size=8192`：`:143`）。
- **attention 是图的「断点」**：`RadixAttention.forward` 在 prefill 图作用域内走 `self._eager_attention(...)`（`python/sglang/srt/layers/radix_attention.py:152-204`），它被 `@eager_on_graph` 装饰（`:208`）。`eager_on_graph` 的文档："Record an eager call between captured segments."（`python/sglang/srt/model_executor/runner_backend_utils/breakable_cuda_graph/breakable_cuda_graph.py:277`），"Request-specific state is read inside the body when it runs."（`:283`）【注释】。所以图被切成「每层 attention 之间的一段段」，段内回放、attention 本身 eager 调用。为什么断在 attention：它依赖每个请求的长度/页表这些「请求专属状态」，形状随批变化（`cu_seqlens`、页表宽度）——这是我的归纳【推断】，注释只说了「请求专属状态在断点体内读」。
- **replay 的间接跳转（最容易读懵）**：`_execute_body_capture`（`python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py:2382-2465`）先把 `self.layer_model.forward = replay_layer_forward`（`python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py:2422`），再**照常调用** `self.model_runner.model.forward(...)`（`:2457`）；模型 forward 里「调躯干」的那一步命中了被换掉的函数，执行 `self.backend.replay(...)`（`:2417`）拿到隐藏状态，然后外层 forward 自己往下走——**用「活的」batch 元数据 eager 跑 `logits_processor`**；`finally` 里还原（`:2464`）。注释：`# BCG / Full: replay the captured body, run the LM head +` / `# logits_processor eagerly.`（`:2391-2392`）。

**什么时候允许回放**（`can_run_graph` → `can_replay_locally`，`:1575`、`:1473-1572`），主要：
- 有 `input_embeds` / `replace_embeds` 不行（`:1525-1528`）；`is_target_verify` 不行（`:1545`）；
- `return_logprob` 且不是「eager 尾巴」的后端不行（`:1552`）；
- `num_tokens > max_num_tokens` 不行（`:1562`）；
- **padding 浪费上限**：先找 ≥ `num_tokens` 的最小档；若不是「比最小档还小」，且 `padded > num_tokens * 2` 则拒绝（`:1564-1571`，常量 `_MAX_PREFILL_CUDA_GRAPH_PADDING_FACTOR = 2`：`:201`）。注释 "No exact-shape check: load_batch bucket-pads; only reject"（`:1564`）。
  - 【提交】`6c6175fabd` "perf: avoid excessive prefill CUDA graph padding (#31487)"（只有标题）。
  - 【测试】`test/registered/unit/model_executor/runner/test_prefill_cuda_graph_padding.py`：档位 `[4,16]` 时，5 个 token 被拒（`test_rejects_more_than_two_x_token_padding`）、8 个被接受（`test_accepts_two_x_token_padding`）。
  - 为什么选 2 没查到，见 §6。

**例子**：prefill 15 个 token → 最小档 ≥15 是 16（`range(4,33,4)` 含 16）→ `16 ≤ 15*2` 通过 → 回放，补 1 个 padding token【设：默认配置、纯文本、无 logprob】。请注意：同一份代码里，**同样 15 个 token 若客户端要了 logprob（`return_logprob`），breakable 后端依然可以**（因为尾巴 eager，`_uses_eager_prefill_tail`：`python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py:832`）。

### 3.5 eager 路径（兜底）

`EagerRunner.execute` 按模式分发：mixed 先改成 EXTEND（"A mixed batch is extend-shaped"：`python/sglang/srt/model_executor/runner/eager_runner.py:207`），再 `_execute_decode / _execute_idle / _execute_extend`（`:201-218`）。每个都是：`load_batch`（拷进 eager 静态缓冲，`:171`）→ 若 `needs_forward_metadata_init()` 就 `attn_backend.init_forward_metadata(forward_batch)`（`:232`、`:306`）→ `model.forward(input_ids, positions, forward_batch)`（`:242`、`:339`）。

**元数据每次 forward 只做一次、全部层复用**：FA3 `init_forward_metadata` 的 docstring "Initialize forward metadata hence all layers in the forward pass can reuse it."（`python/sglang/srt/layers/attention/flashattention_backend.py:1041`）。

### 3.6 attention 怎么按 `req_to_token` 找到这条请求的 KV？

调用链（以默认的 FA3 为例）：`LlamaAttention.forward` → `self.attn(q, k, v, forward_batch)`（`python/sglang/srt/models/llama.py:294`）→ `RadixAttention.forward` → `get_attn_backend().forward(...)`（`python/sglang/srt/layers/radix_attention.py:204`）→ `AttentionBackend.forward` 按模式分到 `forward_decode / forward_extend`（`python/sglang/srt/layers/attention/base_attn_backend.py:404-446`）。

**FA3 是不是默认？** 要看条件：MHA 架构 + Hopper + CUDA ≥12.3 + 非投机（或 topk=1）→ `"fa3"`（`python/sglang/srt/arg_groups/model_override_base.py:333-338`）；Blackwell 默认 `trtllm_mha`，其余多是 `flashinfer`/`triton`。例子取 H100 → FA3。

**找 KV 的三步：**
1. **每次 forward 一次：建 `page_table`。** `metadata.page_table = req_to_token_pool.req_to_token[forward_batch.req_pool_indices, : metadata.max_seq_len_k]`（decode：`python/sglang/srt/layers/attention/flashattention_backend.py:1154`；extend：`:1310`）；同时 `cache_seqlens_int32 = seq_lens`（`:1146`）、`cu_seqlens_k`（`:1151`）、extend 的 `cu_seqlens_q = cumsum(extend_seq_lens)`（`:1336`，因为本例有前缀）。
   `max_seq_len_k` 来自 CPU 镜像 `seq_lens_cpu.max()`，没有就退到静态上限 `max_context_len`，目的是不触发 GPU→CPU 同步：注释 "Eager (non-cuda-graph) path: max_seq_len_k only feeds Python-side" "page-table slicing and the scheduler_metadata heuristic -- never the"（`:1046-1048`）【注释】。图回放路径则用固定宽度："Page table uses the static max_num_pages bound (no D2H)."（`:3463`）【注释】。
2. **每层：先写、后读。** 先把本步的 k、v 存到 `out_cache_loc` 指的槽：`self.kv_layers.layer(layer.layer_id).store(layer, KVWriteLoc.for_layer(forward_batch, layer, ...), k, v, ...)`（extend `:1714`，decode `:2391`）；`KVWriteLoc.loc` 就是 `out_cache_loc`（`python/sglang/srt/mem_cache/memory_pool.py:1765-1788`）。
3. **读：** `flash_attn_with_kvcache(q=..., k_cache=key_cache, v_cache=value_cache, page_table=page_table, cache_seqlens=..., cu_seqlens_q=...)`（extend `python/sglang/srt/layers/attention/flashattention_backend.py:2066-2073`，decode `:2612-2618`）。kernel 对第 i 个请求，用 `page_table[i, j]` 取第 j 个历史 token 的槽，到 `k_cache/v_cache` 里 gather。

**顺序依赖**：`store` 必须在 `flash_attn_with_kvcache` 之前（代码顺序 `:1714` < `:2066`）——因为本步 15 个新 token 的 k/v 要先落到池里，attention 读 `page_table` 时才读得到它们（注意本步新 token 的槽也已经由分配阶段写进了 `req_to_token`）。这一条是我从代码顺序读出来的【推断】。

**例子（prefill）：** `req_to_token[3, 0:20] = [37,38,39,40,41, 212,213,…,226]`；`page_table = 该行前 20 个`；`cache_seqlens=[20]`；`cu_seqlens_q=[0,15]`；`max_seq_len_q=15`（`:1335`）。15 个 query 对这 20 个槽做因果注意力。
**例子（decode 第 1 步）：** 先在 Scheduler 侧 `alloc_for_decode` 分到槽 227，`req_to_token[3, 20] = 227`——注意写的列号是 **旧** `seq_lens`（20）：`locs = seq_lens_gpu.clone()`、`req_to_token_pool.write((req_pool_indices, locs), out_cache_loc)`（`python/sglang/srt/mem_cache/allocation.py:734-737`），之后 `prepare_for_decode` 才 `seq_lens = seq_lens + 1`（`python/sglang/srt/managers/schedule_batch.py:3353`）。所以 forward 看到：`seq_lens=[21]`、`positions=[20]`（`seq_lens-1`）、`page_table=req_to_token[3, 0:21]`、`out_cache_loc=[227]`、1 个 query。

### 3.7 从隐藏状态到 logits：只取每个请求的最后一个位置

`LlamaForCausalLM.forward` 末尾 `return self.logits_processor(input_ids, hidden_states, self.lm_head, forward_batch, aux_hidden_states)`（`python/sglang/srt/models/llama.py:634`）。`LogitsProcessor._get_pruned_states` 先挑行：
- decode：全部行（每请求 1 行）（`python/sglang/srt/layers/logits_processor.py:910-939`）；
- extend 且不要输入 logprob："Prefill without input logprobs."，`last_index = cumsum(extend_seq_lens) - 1; pruned_states = hidden_states[last_index]`（`:964-966`）。

之后才过 lm_head：`logits = self._get_logits(pruned_states, lm_head, logits_metadata)`（`:807`）。所以例子里 prefill 的 15 个位置只有**第 15 个**（下标 14）进 lm_head，输出 `next_token_logits` 形状 `[1, vocab]`。这与 §3.4 引的【注释】一致（尾巴 eager 才能让图与请求数无关）：尾巴只处理 `[bs, …]` 而不是 `[num_tokens, …]`。

### 3.8 采样器：从 logits 到 `next_token_ids`

**调用位置**：`tp_worker.forward_batch_generation` 里 `model_runner.forward` 之后：
- 一般路径：`batch_result.next_token_ids = self.model_runner.sample(logits_output, forward_batch)`（`python/sglang/srt/managers/tp_worker.py:769-773`）；
- prefill-only（`max_new_tokens=0` 之类的嵌入/打分请求）：不采样，造全 0 的占位 token（注释 "For prefill-only requests, create dummy token IDs on CPU"：`:775`）；
- 推迟采样：见 §3.9。

**`ModelRunner.sample`**（`python/sglang/srt/model_executor/model_runner.py:2231`）：
1. `_preprocess_logits`（`:2203`）：`sampling_info.update_regex_vocab_mask()` 用各请求的 grammar 状态现算本步掩码（`python/sglang/srt/sampling/sampling_batch_info.py:436`）→ `apply_logits_bias`（`:492`）依次做：惩罚项（`:480`）、grammar 掩码（`:496`）、`logit_bias`（`:488-490`）。随后立刻 `sampling_info.grammar_mask = None`（`python/sglang/srt/model_executor/model_runner.py:2228`）。注释 "Release the vocab_mask GPU tensor immediately after it has been applied"（`python/sglang/srt/model_executor/model_runner.py:2223`）并说明不释放会因为 `delay_sample_func` 闭包和 `batch_record_buf` 持有引用而稳定漏显存【注释】；对应修复 `999bad5aba` "Fix VRAM leak in overlap scheduling with structured output (#20640) (#20697)"【提交】。
2. 调 `self.sampler(...)`（`:2285`）。传入的 `positions`：decode 用 `forward_batch.positions`，prefill 用 `forward_batch.seq_lens - 1`，注释 "For prefill, we only use the position of the last token."（`:2291`）。它只给「带种子采样」用（`python/sglang/srt/layers/sampler.py:174` 的参数说明 "Used for seeded sampling"）。

**`Sampler.forward`**（`python/sglang/srt/layers/sampler.py:152`），按顺序：
1. `_preprocess_logits`：自定义 logit processor + `sanitize_nan_logits`（`:143-150`）。
2. **全贪心分支**：`sampling_info.is_all_greedy` → `sampler_argmax(logits)`（`:212`、`:220`）。「贪心」在这里就是 `top_k == 1`：`temperature` 趋近 0 时 `SamplingParams` 把它改成 `temperature=1.0, top_k=1`（`python/sglang/srt/sampling/sampling_params.py:263-266`，注释 "top_k = 1 means greedy sampling"），`is_all_greedy = all(k <= 1 ...)`（`python/sglang/srt/sampling/sampling_batch_info.py:775`，由 `from_schedule_batch` 经 `**_top_k_flags(...)` 填入：`python/sglang/srt/sampling/sampling_batch_info.py:363`）。
3. **否则**：`simple_sampling_case = not (need_top_p or need_top_k or need_min_p)`（`python/sglang/srt/layers/sampler.py:229-233`；`need_top_p_sampling = any(top_p != 1.0)`：`python/sglang/srt/sampling/sampling_batch_info.py:361`）。
   - 先 softmax，温度融合在内，且**概率覆盖 logits 原位**：注释 "Temperature fused; the probs overwrite the logits in place."（`python/sglang/srt/layers/sampler.py:299-300`）。
   - 简单情形 → `sampling_from_probs_torch`（`python/sglang/srt/layers/sampler.py:386`）：默认走 **Gumbel-max**（`probs / Exp(1)` 取 argmax；`SGLANG_OPT_USE_GUMBEL_SAMPLE` 默认 True：`python/sglang/srt/environ.py:2272`；代码 `python/sglang/srt/layers/sampler.py:1050-1056`）。注释给的理由："avoids multinomial's CPU-side philox offset bookkeeping and" "distribution-validity assert that stall the decode critical path."（`:1052-1053`）【注释】；提交 `be00a543a7` "perf: use Gumbel-max trick in the main sampler to cut decode CPU dispatch (#38117)"【提交】。`python/sglang/srt/environ.py:2270-2271` 另给了一条理由：`torch.multinomial` 的设备端合法性断言会破坏 draft 图回放【注释】。有 `sampling_seed` 时改走 `multinomial_with_seed(torch.log(probs), seed, positions)`（`python/sglang/srt/layers/sampler.py:1059-1062`）。
   - 复杂情形（有 top-p/top-k/min-p）→ flashinfer 融合核 `top_k_top_p_sampling_from_probs(probs, top_ks, top_ps, filter_apply_order="joint")`（`:440`）；若有种子则强制切 "pytorch" 后端："Seeded draws must not depend on the row's batch slot; FlashInfer's seeds do,"（`:406`）【注释】。
4. 需要 logprob 时另算（`output_logprob_processor`）；本例不需要。
5. **TP 同步（默认关）**：`self._sync_token_ids_across_tp(...)`（`:335`；定义 `:752`）只在环境变量 `SGLANG_SYNC_TOKEN_IDS_ACROSS_TP` 或本批有 grammar 时对 token id 做 `all_reduce(MIN)`。注释 "Off by default to save an all-reduce; correct only while the last all-reduce," "the lm_head matmul and every sampling kernel agree across ranks"（`:756-758`）【注释】——也就是：**每个 TP rank 各自采样，靠「输入一致 + 核确定」得到同一个 token**，不是 rank0 采完广播。【测试】`test/registered/unit/layers/test_sampler_token_id_sync.py` 的 `test_plain_batches_never_sync`、`test_two_rank_group_still_reduces_with_min`（`:86`、`:90`）。历史：标志位 `022614d26e` "Add some flags to allow sync token ids across TP ranks (#3060)"【提交】。

**产出**：GPU 上形如 `[bs]` 的整型张量 `next_token_ids`（`sampled_index.view(-1).to(torch.int32)`：`python/sglang/srt/layers/sampler.py:1064`）。

**为什么采样不放进 decode 图？** 没有注释明说。依据：采样路径里有大量 Python 侧分支（`is_all_greedy`、`need_top_p_sampling` 这些**主机侧布尔标志**），还有 grammar 掩码这类每步现算的东西；同类的「主机侧标志」有注释说明动机——例如 `npu_top_k_top_p_eligible` 的注释 "device avoids a scalar synchronization in the per-token sampling path"（`python/sglang/srt/sampling/sampling_batch_info.py:196`，这条注释只针对那一个字段）。我据此推断：分支多 + 数据依赖，不适合录进固定的图【推断】。有一个特例：个别模型（`uocr_greedy_graph`，Unlimited-OCR v2）把贪心 argmax 录进了 decode 图（捕获尾钩子：`python/sglang/srt/model_executor/model_runner.py:1722-1723`；采样处取录好的结果：`python/sglang/srt/model_executor/model_runner.py:2280-2282`），说明不是技术上不可能。

### 3.9 两条 CUDA 流是怎么排序的？（再加 `FutureMap` 和推迟采样）

**先给图景：** Scheduler 的整个事件循环跑在 `schedule_stream` 里（`python/sglang/srt/managers/scheduler.py:2172` 的 `StreamContext(self.schedule_stream)`）；模型前向跑在另一条 `forward_stream`（`python/sglang/srt/model_executor/model_runner.py:503` 创建）；结果拷回 CPU 用第三条 `copy_stream`（`python/sglang/srt/managers/scheduler.py:1857-`）。调度主线程只有一个，它把活「分发」到不同流上就返回（只有在 Confidential Computing 下才多一个专做 D2H 拷贝的 worker 线程：`python/sglang/srt/managers/scheduler.py:1874`）；GPU 内的先后顺序靠流间的 `wait_stream` / 事件，不靠锁。

**逐步（均在 `run_batch` overlap 分支，`python/sglang/srt/managers/scheduler.py:5432-5511`）：**
1. 进入 `with self.forward_stream_ctx:`，**`self.forward_stream.wait_stream(self.schedule_stream)`**（`:5433`）：forward 流要等 schedule 流上此前排的所有活（分槽、`prepare_for_decode`、H2D 拷贝）。
2. `resolve_forward_inputs(batch, future_map)`（`:5442`，在 forward 流上）：prefill 把 pinned CPU 的 id H2D；decode 从 `output_tokens_buf` gather 上一步的 token（`python/sglang/srt/managers/overlap_utils.py:127-166`）。docstring："Prefill: H2D copy from pinned CPU staging (prefill_input_ids_cpu)." / "Decode/spec_v2: gather from FutureMap (last iter's sampled token)."
3. `_forward_isolation(batch, overlap=True)` 包住前向：把 `sampling_info` 换成 forward 专用拷贝，并把 `(batch, 字段快照)` 钉进 `batch_record_buf` 两轮（`python/sglang/srt/managers/scheduler.py:5285-5306`、`:5337`）。注释 "FIXME(lsyin): hacky way to keep a reference to avoid GPU tensors being freed by torch GC"（`:5286`）【注释】——schedule 流上产生的张量，forward 流还在用时不能被分配器回收。
4. `batch_result = self.model_worker.forward_batch_generation(batch, ...)`（`:5470`）——**CPU 只负责排 kernel，几乎立刻返回**。
5. `future_map.publish(future_indices, batch.seq_lens + 1)`（`:5475`；`future_indices = batch.req_pool_indices`，`:5447`）；采样出的 token 经 `_relay_forward_payload` 写进 `output_tokens_buf[req_pool_indices]`（`:5495`、`:5752`；写入处 `python/sglang/srt/managers/overlap_utils.py:640`、`python/sglang/srt/managers/overlap_utils.py:800`）。这些都发生在**同一条 forward 流**上，所以下一步 forward 流上的 gather 天然排在它之后（stream 内有序）——【推断】（代码里没有显式事件，依据是两处都在 `forward_stream_ctx` 内）。
6. `batch.input_ids = None`（`python/sglang/srt/managers/scheduler.py:5511`，注释 "Next-iter input_ids relayed via future_map." `:5510`）：下一轮 decode 的输入不从 CPU 来。
7. `_copy_result_to_cpu`（`:5506`，定义 `:5817`）：`copy_stream.wait_stream(forward_stream)`（`:5831`），在 `copy_stream` 上把 `next_token_ids` 等异步拷到**固定内存**（`async_d2h`：`python/sglang/srt/utils/device.py:652-661`，`host.copy_(tensor, non_blocking=True)` + `record_stream`），配 `copy_done` 事件（`python/sglang/srt/managers/scheduler.py:5493`）。docstring "Issue the result readback on copy_stream so it overlaps the next forward."（`:5820`）【注释】。
8. **CPU 处理上一批结果**：`event_loop_overlap` 里顺序是 `run_batch(N)`（`:2281`）→ `pop_and_process()`（`:2292`，处理 N-1）→ `launch_batch_sample_if_needed`（`:2300`）。处理时先 `result.copy_done.synchronize()`（prefill `python/sglang/srt/managers/scheduler_components/batch_result_processor.py:403`；decode `:1157`）——CPU 在这里才真的等 GPU。**所以「GPU 算第 N 批」和「CPU 处理第 N-1 批」重叠**（上一趟 §4.1 已讲，这里补流的细节）。

**还有一条反向约束：WAR 屏障。** Scheduler 为下一批改 `req_to_token` 等共享缓冲（在 schedule 流上），不能抢在 forward 流**读完**它们之前。`run_batch` 之后调 `_apply_war_barrier()`（`python/sglang/srt/managers/scheduler.py:2283`）：`self.schedule_stream.wait_event(ev)`（`python/sglang/srt/managers/scheduler.py:2185`），其中 `ev` 是 runner 在「共享缓冲读完」的时刻发布的事件（decode：`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:772` 的 `_publish_read_done`；prefill：`python/sglang/srt/model_executor/runner_utils/shared_read_event.py:25`）。没有发布事件就退回等整个 forward（"coarse"，`SGLANG_FORCE_COARSE_WAR_BARRIER`：`python/sglang/srt/environ.py:769-771`）。【提交】`163b970127` "[core] WAR barrier for overlap schedule buffer writes, without fwd occupancy cost (#26380)"、`106d2930a6` "[core] Gate the overlap WAR barrier on forward reads to recover decode throughput (#28363)"（都只有标题；从标题看，后者把「等整个 forward」收窄到「等读完」以换回 decode 吞吐）。

**两条流别撞成一条**：CUDA 流从固定的轮转池里领，可能恰好领到同一条。注释 "stream aliases forward_stream, which would eliminate scheduler" "overlap."（`python/sglang/srt/managers/scheduler.py:2160-2161`）【注释】，随后 `allocate_distinct_stream(...)` 重抽（`:2166`）。

**推迟采样（delay sample）：** `python/sglang/srt/managers/tp_worker.py:745-767`：overlap 且非投机，并且（本批有 grammar，或环境变量 `SGLANG_ENABLE_DELAY_SAMPLE` 为真，默认 False：`python/sglang/srt/environ.py:765`）→ 不当场采样，把 `sample_batch_func` 闭包挂到 `batch_result.delay_sample_func`（`python/sglang/srt/managers/tp_worker.py:766`）。Scheduler 在**处理完上一批结果之后**才去调它：`launch_batch_sample_if_needed`（`python/sglang/srt/managers/scheduler.py:5845-5860`，同样在 forward 流上、先 `wait_stream(schedule_stream)`）。理由写在循环里："It depends on the result of the last batch (e.g., grammar), so we run it after the last batch is processed."（`python/sglang/srt/managers/scheduler.py:2298`）【注释】——grammar 掩码要用上一步刚确定的 token 推进状态机。环境变量的注释（`python/sglang/srt/environ.py:762-764`）说明它也可以对所有 decode 打开，"allowing CPU result processing to overlap with subsequent forward computation"【注释】。

**历史：** overlap 最早是一个单独线程（`dbec2f1847` "Launch a thread to overlap CPU and GPU (#1687)"，2024-10）；后来引入 `FutureMap`（`f5a2faf2b8` "Introduce `FutureMap` (#10715)"，2025-09）；再去掉线程（`1519a89cfd` "Remove overlap thread (#11210)"，2025-10-07）。三条提交说明都没有正文，所以「为什么去掉线程」没有明文，见 §6。

---

## 4. 例子在框里走一遍（接上一趟 §8 第 8、9 步）

**第 n 圈 · prefill（15 个位置）**

| 步 | 在哪 | 发生了什么 | 值（设） |
|---|---|---|---|
| a | `run_batch`，forward 流 | `wait_stream(schedule_stream)`；`resolve_forward_inputs`：pinned CPU 的 15 个 id H2D → `batch.input_ids` | 15 个 id |
| b | `forward_batch_generation` | `ForwardBatch.init_new`：别名借用 + 新算 `positions=[5..19]`、`extend_*` | §3.1 表 |
| c | `_forward_raw` | `EXTEND` → 非 `is_cuda_graph` → 跳过 decode 图 → `_prepare_eager_forward_batch` → prefill 图 `can_run_graph`：15 → 档 16，`16 ≤ 30` ✔ | 补 1 个 padding token |
| d | prefill runner `execute` | `load_batch` 填静态缓冲 → `replay_layer_forward` 回放躯干，每层 attention 作为断点 eager 调 FA3（先 `store` 15 个 k/v 到槽 212..226，再按 `page_table` 读 20 个槽） | `cu_seqlens_q=[0,15]` |
| e | 外层 `LlamaForCausalLM.forward` | `logits_processor` eager：取下标 14 → lm_head → `[1, vocab]` | |
| f | `ModelRunner.sample` | 掩码/偏置（本例无）→ softmax+温度（原位）→ Gumbel-max → `X1` | `next_token_ids=[X1]` 在 GPU 上 |
| g | `run_batch` 收尾 | `publish(seq_lens+1)`、`output_tokens_buf[3]=X1`、`copy_stream` 异步拷回、`batch.input_ids=None` | |

**第 n+1 圈 · decode 第 1 步（running 里 1 个请求）**

| 步 | 在哪 | 发生了什么 | 值（设） |
|---|---|---|---|
| a | schedule 流（`prepare_for_decode`） | 分槽 227、写 `req_to_token[3,20]=227`、`seq_lens` 20→21 | |
| b | forward 流 | `resolve_forward_inputs`：`input_ids = output_tokens_buf[[3]] = [X1]` | CPU 此时还没见到 X1 |
| c | `init_new` | `positions=[20]`（`clamp(seq_lens-1)`） | |
| d | `_forward_raw` | `DECODE` → `can_run_graph`：bs=1 ≤ 256 → 取档 1 → `load_batch` 填缓冲、FA3 把 `page_table[0, 0:21]` 写进静态页表 → `graph.replay()` | 无 padding |
| e | 模型（图内） | 每层：`store` k/v 到槽 227；按页表读 21 个槽；最后 lm_head → `[1, vocab]` | |
| f | `sample` | → `X2` | |

**对照**：若 running 有 5 个请求 → `_pad_to_bucket(5, [1,2,4,8,12,…])` = 8；多出 3 行：`req_pool_indices=0`、`seq_lens=1`、`out_cache_loc=0`，它们读 `req_to_token` 第 0 行、写 KV 第 0 槽，logits 回来后被 `[: raw_num_token]` 切掉（`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:2210`）。

---

## 5. 值得做成源码页的代码段（建议 6 段，另有 2 段可选）

每段都是「读者从头读到尾就能明白这一块」的最小单位；行号为 HEAD。

**页 1 · 总管：打包 → 前向 → 采样（或推迟）**
`python/sglang/srt/managers/tp_worker.py:654-692` + `:741-790`（可作两屏）。
- `:657` `ForwardBatch.init_new(...)`：打包的唯一入口。
- `:688` `if self.pp_group.is_last_rank:`：非末级流水线 rank 走 `else`（`:794`），不采样、只传隐藏状态——这个框只讲末级。
- `:689` `self.model_runner.forward(...)`；`:745-767` 推迟采样的条件（为什么只对 grammar/开关）；`:769-773` 常规采样；`:775-789` prefill-only 不采样、造占位 token。

**页 2 · 三路分叉**
`python/sglang/srt/model_executor/model_runner.py:2113-2193`。
- `:2119-2123` decode 图判据（`is_cuda_graph` 是模式白名单）；`:2142` 直接 return；
- `:2148-2154` 注释解释为什么 eager 准备放在 decode 图返回之后；
- `:2164-2173` prefill 图的六个条件；`:2174` 过时的 "piecewise" 注释；`:2190` eager 兜底。

**页 3 · decode 图回放：选档、填缓冲、取回**
`python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1722-1752`（选档与 `fill_from`）+ `:2189-2215`（回放与切片）；配 `python/sglang/srt/model_executor/cuda_graph_buffer_registry.py:598-608` 的 docstring 当旁注。
- `python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1726` 非投机 `raw_num_token = raw_bs * 1`；`:1731` 选档；`:1744` 填缓冲；
- `:2189` `backend.replay`；`:2210` `[: self.raw_num_token]` 把 padding 行切掉；
- 旁注：padding 行为什么指向第 0 行（`python/sglang/srt/mem_cache/memory_pool.py:416-417`）。

**页 4 · prefill 图的「偷梁换柱」**
`python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py:2391-2465`。
- `:2391-2392` 注释说明「回放躯干、eager 跑 lm_head+logits」；
- `:2422` 把 `layer_model.forward` 换成 `replay_layer_forward`；`:2457` 照常调 `model.forward`；`:2464` `finally` 还原；
- 旁注：admission 的 2× padding 上限 `:1564-1571`。

**页 5 · attention：写 KV、按页表读 KV**
`python/sglang/srt/layers/attention/flashattention_backend.py:1140-1156`（decode 建 `page_table`）+ `:1301-1343`（extend 建 `page_table` / `cu_seqlens_q`）+ `:1714-1730`（`store`）+ `:2066-2076`（`flash_attn_with_kvcache`）。
- `:1052-1055` `eager_max_k` 为什么优先用 CPU 镜像；`:1154` `req_to_token[req_pool_indices, :max_seq_len_k]` 是整条「找 KV」的关键一行；`:1714` 写 → `:2066` 读 的顺序。

**页 6 · 采样器**
`python/sglang/srt/layers/sampler.py:209-234` + `:295-306` + `:335` + `:1039-1065`。
- `:212` 全贪心；`:229` `simple_sampling_case`；`:299-300` 概率原位覆盖 logits；`:335` TP 同步（默认关）；`:1050-1056` Gumbel-max。

**可选页 7 · 两条流：`run_batch` 的 overlap 分支**
`python/sglang/srt/managers/scheduler.py:5428-5511`：`:5433` `wait_stream`、`:5446` `_forward_isolation`、`:5470` 前向、`:5475` `publish`、`:5493-5506` 异步拷回、`:5511` `batch.input_ids = None`。

**可选页 8 · `init_new` 的新算张量**
`python/sglang/srt/model_executor/forward_batch_info.py:1212-1262`：decode 的 `positions` vs extend 的 `positions/extend_start_loc`，以及 `:1225` 注释（"Written by kernels: a pinned host-to-device copy would queue"，大意是：元数据改用 kernel 写，免得 pinned H2D 拷贝在拷贝引擎上排在大块传输之后拖慢本次 forward）。

---

## 6. 没查到为什么

- **decode 档位为什么是 `1,2,4,8,12` 然后 8 步长、再 16、再 32**：只有成本注释（§3.3）和「把逻辑集中到统一函数并放宽 max bs」的提交 `05a3526654` "Restruct gpu_memory_settings in a unify function and relax max_cuda_graph_bs (#10372)"；步长的选择依据没查到。
- **prefill 档位 `4..32` 步 4、`48..256` 步 16 … 为什么这样**：没查到。
- **padding 上限为什么是 2×**：提交 `6c6175fabd` 只有标题，测试只验证行为，没写数值依据。
- **`seq_len_fill_value` 为什么 FA3 取 1**：`get_cuda_graph_seq_len_fill_value` 直接 `return 1`（`python/sglang/srt/layers/attention/flashattention_backend.py:3684-3686`），无注释。
- **为什么不把采样录进 decode 图**：没有明文（§3.8 只是推断）。
- **`SGLANG_ENABLE_DELAY_SAMPLE` 为什么默认关**：环境变量注释只说了开了有什么好处，没说为什么默认不开。
- **overlap 线程为什么被去掉（`1519a89cfd`）**：提交说明只有标题和 co-author，没有正文。
- **`prepare_for_extend` 为什么要把 input id 的 H2D 推迟到 forward 流**：注释只说「推迟到 forward 流」，没说为什么。
- 没有读 PR / issue 正文（离线）；上面的【提交】证据大多只有标题。

## 7. 可改进处【观点】

- 【观点】`python/sglang/srt/model_executor/model_runner.py:2174` 的 "Prefill cuda graph (piecewise)." 已过时，容易误导读者以为还在用 torch.compile 分段图；应改成 "breakable / full"。
- 【观点】`_forward_raw` 的 prefill 图条件有 6 个并列判断散在 `if` 里，而 decode 图的判断下沉到 `can_run_graph`；若把「除模式外的准入条件」也统一放进 runner，主函数会更好读。
- 【观点】`python/sglang/kernels/ops/attention/position.py:46` 注释 "NOTE: This can be slow for large bs"（每个 program 为求起点循环累加前面所有请求的长度，O(bs)）——大 batch 的 prefill 可考虑前缀和。
- 【观点】`init_new` 一个函数 300 多行，混着 mamba、DP、CP、mrope、spec 等专用字段；对第一次读的人，真正通用的只有 `python/sglang/srt/model_executor/forward_batch_info.py:1012-1106` 和 `python/sglang/srt/model_executor/forward_batch_info.py:1212-1262` 两段。

## 8. 对上一趟底稿的更正 / 补充

1. **更正（§4.6、§8 第 8 步）**：上一趟写「prefill 通常走另一条路，可用时走分段 CUDA graph，不然 eager」。HEAD 上：CUDA 默认 prefill 后端就是 `breakable`（`python/sglang/srt/model_executor/cuda_graph_config.py:119-123`），例子里的 15 个 token 在默认配置下**会走图回放**（档 16）。`tc_piecewise` 已删除，`python/sglang/srt/model_executor/model_runner.py:2174` 的 "piecewise" 字样是遗留。
2. **补充**：`future_map` 的下标是 `req_pool_idx`（`future_indices = batch.req_pool_indices`：`python/sglang/srt/managers/scheduler.py:5447`；`python/sglang/srt/managers/overlap_utils.py:324`），不是递增的「未来编号」。
3. **补充**：decode 图里**包含** lm_head + logits_processor，但**不含**采样；采样在 `tp_worker`（§3.8）。
4. 上一趟引的 `python/sglang/srt/managers/tp_worker.py:657`、`python/sglang/srt/managers/tp_worker.py:689-692`、`:771-773`、`python/sglang/srt/managers/scheduler.py:5433`、`python/sglang/srt/managers/scheduler.py:5442-5444`、`:5470`、`:5475`、`:5506` 在 HEAD 上仍成立。

## 9. 给讲解员的读者提示

- **最容易困惑的地方**：①`ForwardBatch` 看着是新对象，其实多是别名（§3.1）；②prefill 回放里「换掉 `layer_model.forward` 再调外层 forward」这个间接跳转（§3.4）；③padding 行为什么指向「第 0 行 / 第 0 槽」（§3.3）；④「CPU 直到 `copy_done.synchronize()` 才看到 token，而下一步的输入根本没经过 CPU」（§3.9）。
- **最好对照着看的两件事**：decode 图 vs prefill 图（图里装什么、按什么分档、padding 是请求行还是 token、attention 是否断点、lm_head 在图内还是图外）；「写 KV（`out_cache_loc`）」与「读 KV（`page_table`）」是两条独立的线，都落在同一个 KV 池上。
- **三条流**（schedule / forward / copy）与**两个对象**（`FutureMap` 传 GPU 上的 token，`copy_done` 事件传给 CPU）建议分开画：前者是「GPU 内部的接力」，后者才是「回到 CPU」。
- 本框还有分支这份底稿没展开：流水线并行非末级 rank（`python/sglang/srt/managers/tp_worker.py:794`）、投机解码、DP-attention、LoRA、多模态。读者想点进去再问我。
