# 传输模块（STORE / RETRIEVE 的服务端）下一层底稿

> 研究对象：LMCache 仓库 HEAD = `b27f6bd9e2`。本稿是上一趟顶层底稿（下称「上一趟」，`research/manuscript.md`）里 ⑦「传输模块」的补充研究，只讲这个框内部。
> 锚点格式：`文件:行号` 或 `文件:起-止`，都在 HEAD 上成立。引文用中文弯引号括起，逐字照抄原文，后面紧跟出处。
> 「为什么」的来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 路径缩写（只在叙述里用，所有锚点都写全路径）：**LDT** = `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py`。

---

## 0. 先看这个（七个问题的一句话答案）

这个框的本质：**亲和线程只负责"排队下单"，真正的搬运发生在 GPU 上一条专属 CUDA 流里；"拷完之后的收尾"由另一条线程在流回调触发后做。** 所以 STORE / RETRIEVE 函数返回时数据还没动完。

1. **服务端的流怎么等 vLLM 的 event？** worker 在自己的流上录一个"可跨进程"的 event，把它的 IPC 句柄（bytes）塞进 ZMQ 消息；服务端用句柄导入 event，让**自己的流**等它（只排队，不阻塞 CPU）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894`。
2. **48 个分页 block 怎么变成 3 个对象？** 每个 chunk 对应 16 个 block id，内核把它们逐块写进对象的 `[kv][layer][token][nh*hs]` 布局里的 token 槽位（block j → token j×16..j×16+15）。默认有三条拷贝路径（direct / native plan / staged loop），例子里的形状恰好卡在 direct 的 32 KiB 门槛上（§5 Q2）。
3. **`finish_write` 是谁、在哪、什么时候？** 流走到那个位置后，CUDA 驱动线程执行一个 C++ host 函数（不拿 GIL），把消息放进 C++ 缓冲；`DeviceHostFuncDispatcher` 排空线程每 5 ms 轮询取走，再调 Python 的 `finish_write`。
4. **「要么全存要么全不存」？** 只对「入队阶段出异常 / block id 不够」成立：整单放弃并 `abort_write`。"某些 key 本来就在 L1 或 L1 没空间"**不算失败**，那些块被跳过、其余照存。
5. **`skip_first_n_tokens` 怎么生效？** 换算成"跳过前 N 个 block"（向下取整），内核里 `flat_block_idx < skip_prefix_n_blocks` 的线程块直接 return；整块被跳过的 chunk 连暂存拷贝都省。
6. **亲和线程池？** 按 ZMQ 客户端身份把请求绑到固定线程，FIFO。同一 worker 的 STORE 与 RETRIEVE 在 CPU 上串行、在 GPU 上也同一条流里串行（队头阻塞），但没有数据依赖。默认全局只有 **1** 个亲和线程。
7. **完成 event 录在哪？** 录在所有拷贝**之后**、`finish_write` / `finish_read_by_owner` 回调**之前**。所以 worker 看到"STORE 完成"时，对象可能还没对读者可见（最多晚一个排空周期）。

---

## 1. 框里有什么

### 1.1 部件与它们持有的状态

| # | 部件 | 跑在哪 | 持有 / 读写的状态 | 锚点 |
|---|---|---|---|---|
| a | **请求入口** `store` / `retrieve` 处理函数 | 亲和池线程（只到"入队"为止） | 读 key、block id、event 句柄；写：无持久状态 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:714-725`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1111-1123` |
| b | **注册表** `_cache_contexts: dict[int, ContextEntry]` + `_lock` | 任意线程（只在锁内做 dict 操作） | instance_id → `ContextEntry`（cache_context、model_name、world_size、last_seen、event_backend） | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:191-223`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:259-267` |
| c | **`cache_context`**（每个 vLLM 实例一份） | 被 a 使用 | ① 服务端自己的一条 CUDA 流 ② 4 槽 GPU 暂存缓冲 ③ 1<<20 个 int64 的 block id 缓冲 ④ "已导入 event"持有表 ⑤ 指向 vLLM KV 张量的指针表 | `lmcache/v1/platform/devices/cuda/cache_context.py:416-420`，`lmcache/v1/platform/devices/cuda/cache_context.py:444-453`，`lmcache/v1/platform/base/cache_context.py:81-98` |
| d | **event IPC 后端** `event_backend` | 同上 | create / export / import / record / wait / query；导出过的 event 留在 2048 项环里 | `lmcache/v1/platform/base/event_ipc.py:221-252`，`lmcache/v1/platform/base/event_ipc.py:164-166` |
| e | **拷贝规划 + 内核**：`transfer_kv_per_object_group` → direct / native plan / staged loop → CUDA 内核 | 入队在亲和线程；执行在 GPU | 读：block id、`MemoryObj`、`skip_first_n_tokens`；写：GPU 流上的任务 | `lmcache/v1/multiprocess/object_group_transfer/dispatch.py:31-111` |
| f | **流回调链**：`submit_callback_to_stream` → C++ host 函数 → C++ 缓冲 → `DeviceHostFuncDispatcher` | 驱动线程 + 排空线程 | 5 种 kind：`finish_write` / `abort_write` / `finish_read_prefetched` / `finish_read_by_owner` / `release_imported_event` | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-297`，`lmcache/v1/multiprocess/native_completion.py:62-153` |

框外但紧挨着的两端（不展开）：
- **存储层**（L1 的 `reserve_write` / `finish_write` / `unsafe_read` / `finish_read_*`）：见上一趟 §5.7；本框只在 `lmcache/v1/distributed/storage_manager.py:289-413` 与 `lmcache/v1/distributed/storage_manager.py:710-720` 的接口上调用。
- **worker 侧的"完成 future"** `DeviceMessagingFuture`：`lmcache/v1/transport/futures.py:260-269`，轮询方是 `lmcache/integration/vllm/vllm_multi_process_adapter.py:373-387`。

### 1.2 执行体一览（谁在哪条线程 / 流）

| 执行体 | 干什么 | 个数 |
|---|---|---|
| worker 前向线程 | 录 event、发 STORE / RETRIEVE、`get_finished` 轮询 | 每 worker 1 |
| ZMQ 主循环线程 | 收请求、把 STORE / RETRIEVE 丢进亲和池、把返回值编码发回 | 1（见上一趟 §2.1） |
| **亲和池线程** | 跑 `store` / `retrieve` 到"全部入队"为止 | 默认 1（`lmcache/v1/multiprocess/config.py:59`） |
| **服务端 CUDA 流** | GPU 上真正执行等待、拷贝、回调的顺序队列 | 每个注册实例 1 条 |
| CUDA 驱动线程 | 流走到回调点时执行 C++ host 函数 | 驱动内部 |
| **`DeviceHostFuncDispatcher`** | 取走回调消息，调 Python 处理函数 | 1，每 5 ms 轮询（`lmcache/v1/multiprocess/native_completion.py:67`） |

### 1.3 框内的连线

| 线 | 从 → 到 | 方式 | 交什么 | 锚点 |
|---|---|---|---|---|
| X1 | worker → 亲和线程 | ZMQ 异步 RPC | key、instance_id、block id、**event IPC 句柄** | `lmcache/v1/client/worker_transfer.py:719-722`，`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:299-300` |
| X2 | 亲和线程 → 服务端流 | 入队（等 event、拷贝、录 event、挂回调） | 内核 / memcpy / event / host 函数 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-1018` |
| X3 | 亲和线程 → ZMQ 主线程 | 返回值 → 输出队列 + eventfd | `(完成 event 句柄, bool)` | `lmcache/v1/multiprocess/transport/zmq_impl/mq.py:313-314` |
| X4 | 服务端流 → 驱动线程 → C++ 缓冲 | CUDA 下是 `cudaLaunchHostFunc`（HIP 下 `hipLaunchHostFunc`） | kind + msgpack 载荷 | `csrc/cuda/completion_recorder.cpp:28-49`，`csrc/cuda/completion_recorder.h:20-24` |
| X5 | C++ 缓冲 → 排空线程 → L1 | 轮询 + 解码 + 调处理函数 | `dict[ObjectKey,int]` 等 | `lmcache/v1/multiprocess/native_completion.py:114-144` |
| X6 | 服务端流 ↔ vLLM KV | CUDA IPC 指针直接读写 | KV 数据 | `lmcache/v1/multiprocess/object_group_transfer/dispatch.py:69-111` |
| X7 | 服务端 → worker | ZMQ 回复里带完成 event 句柄；worker 导入后 `query` | event | `lmcache/v1/transport/futures.py:288-302`，`lmcache/v1/transport/futures.py:415-433` |

---

## 2. 例子（沿用上一趟，补设的标「设」）

沿用上一趟 §4.1：Qwen3-8B、TP=1、36 层、8 KV 头、head_dim 128、bf16、vLLM block = 16 token、chunk = 256；请求 A 存 `[0,768)` = 3 个 chunk = **48 个 block id**；请求 B 取回 `[0,768)`。

本稿补设并推算的数（推算均基于上述设定）：
- 每个对象形状 `[2, 36, 256, 1024]` bf16 = 2×36×256×1024×2 B = 37,748,736 B = **36 MiB**（与上一趟一致）。
- 每个 block、每层、每个 K/V 平面：16 token × 1024 × 2 B = **32,768 B = 32 KiB**；这**恰好等于** direct 路径的下限 `32 << 10`（`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:42`，比较用的是 `<`，`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:113`），所以不被挡掉。
- 暂存缓冲 4 槽 × 36 MiB = 144 MiB / 实例（设；槽数 4 见 `lmcache/v1/platform/devices/cuda/cache_context.py:448`）。
- 设 A 的 block 编号 b0…b47（上一趟写法）；b0–b15 → 对象 h0，b16–b31 → h1，b32–b47 → h2。
- **补设 B′（只为讲 skip）**：prompt 同 B，但 vLLM 本地 APC 命中前 **320 token（20 个 block）**，LMCache 命中 768。

---

## 3. 一次 STORE 的时间线（请求 A，3 个 chunk）

| 步 | 谁 / 在哪 | 做什么 | 锚点 |
|---|---|---|---|
| S0 | worker 前向线程 | `wait_for_save`：在**当前流**录 event → 导出句柄 → 发 STORE → 拿到 `DeviceMessagingFuture`，**不等** | `lmcache/integration/vllm/lmcache_mp_connector.py:1193-1201`，`lmcache/v1/client/worker_transfer.py:647-649`，`lmcache/v1/client/worker_transfer.py:719-725` |
| S1 | ZMQ 主线程 | `affinity_key = hash(identity)`，提交给亲和池 | `lmcache/v1/multiprocess/transport/zmq_impl/mq.py:299-300` |
| S2 | 亲和池线程 | 找注册项（没有就回 `(b"", False)`）；算每个 object group 的 `ObjectKey`；`blocks_per_chunk=16` | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:797-835` |
| S3 | 同上 | 进入 `torch_dev.stream(cache_context.stream)`；先建一个自己的完成 event | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:840-844` |
| S4 | 同上 | 守卫：每组 block id 数 < chunk 数 × 每 chunk block 数 → 整单放弃 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:853-872` |
| S5 | 同上 | 全空块掩码（在降采样前算）；`downsample_and_stage_block_ids`：裁剪 + 把 block id 拷进 GPU 缓冲 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:874-889` |
| S6 | 同上 | **导入 vLLM 的 event → 服务端流等它** → 持有导入对象 → 紧跟着在流上挂 `release_imported_event` 回调 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-900` |
| S7 | 同上 | 对每个 object group：`reserve_write` → 记 owner → `transfer_object_group`（D2H，`batch_size=1`，`skip=0`） | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:944-987` |
| S8 | 同上 | `finally`：**录完成 event** → 全成功挂 `finish_write`，否则挂 `abort_write` | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1001-1018` |
| S9 | 同上 → ZMQ 主线程 | 导出完成 event 句柄，返回 `(句柄, store_succeeded, 掩码)`；主线程编码发回。**此刻 GPU 多半还在拷** | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1080-1084`，`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:313-314` |
| S10 | GPU 流（按顺序） | 等 vLLM event → `release_imported_event` 回调 → 3 个 chunk 的拷贝 → 完成 event → `finish_write` 回调 | 测试把这个顺序钉死：`tests/v1/multiprocess/test_l1_owner_completion.py:193` |
| S11 | 驱动线程 → 排空线程 | host 函数入 C++ 缓冲；排空线程轮询取走 → `_finish_write` → `StorageManager.finish_write` → 对象转正 | `csrc/cuda/completion_recorder.cpp:28-33`，`lmcache/v1/multiprocess/native_completion.py:114-144`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:331-339` |
| S12 | worker 前向线程 | 每步 `get_finished`：RPC 回复到了 **且** `event.query()` 为真才算完；结果为假只记日志 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:2179-2191`，`lmcache/v1/transport/futures.py:415-433` |

> 读者提示：S9 之后到 S11 之间，**没有任何一行 Python 在等这次拷贝**。"时间不连续"最集中的地方就是 S8→S10→S11：三次交接分属三个执行体（亲和线程、GPU 流、排空线程）。

---

## 4. 一次 RETRIEVE 的时间线（请求 B，3 个 chunk；B′ 的差别单列）

| 步 | 谁 / 在哪 | 做什么 | 锚点 |
|---|---|---|---|
| R0 | worker 前向线程 | `start_load_kv`（前向之前）录 event → 发 RETRIEVE（带 `skip_first_n_tokens`）；vLLM 把请求挂在"等远端 KV"，不参与本步前向 | `lmcache/integration/vllm/lmcache_mp_connector.py:1112-1120`，`lmcache/v1/client/worker_transfer.py:791-801` |
| R1 | 亲和池线程 | 找注册项、算 key、发 `MP_RETRIEVE_SUBMITTED` / `START` 观测事件 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1174-1225` |
| R2 | 同上 | 进流、建完成 event、同样的 block id 下溢守卫（失败 → `False`） | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1237-1263` |
| R3 | 同上 | 裁剪并暂存 block id；**导入 event、流等它**、持有 + 挂释放回调 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1265-1278` |
| R4 | 同上 | 取 `read_owners`；算 `retained_keys`；**先**把"要放的锁"打包成 `read_completion`，**再**入队任何 GPU 工作 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1306-1318` |
| R5 | 同上 | 逐 object group：`unsafe_read`（读的是 LOOKUP 已锁住的对象，不新加锁）→ 缺任何 key 就 `break` 并标失败；否则 `transfer_object_group`（H2D，`batch_size=4`，带 skip） | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1321-1350` |
| R6 | 同上 | `finally`：**录完成 event** → 只要有 `retained_keys` 就挂 `finish_read_by_owner`（**成败都挂**）→ 返回 `(句柄, retrieve_succeeded)` | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1369-1377`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1408-1411` |
| R7 | GPU 流 | 等 event → 拷贝 → 完成 event → 排空线程放 3 把读锁 | 同上 |
| R8 | worker | `get_finished` 里 RPC + event 都完成才算 recving 完成；结果假 → `error_block_ids`，vLLM 重算 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:2193-2208` |

**B′ 的差别**（设：vLLM 命中 320 token）：调度器算 `start = 320 // 256 * 256 = 256`，`end = 768`，`skip_first_n_tokens = 320 − 256 = 64`（`lmcache/integration/vllm/lmcache_mp_metadata.py:618-644`）。服务端于是只处理 h1、h2 两个对象、32 个 block id，其中前 `64/16 = 4` 个 block（vLLM 自己已有的）不写。详见 Q5。

> 更正上一趟：上一趟 §3 的说明卡把 RETRIEVE 写成"① 算 key → `unsafe_read`；② 等 event → 入队拷贝"。代码里**等 event 在前**（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1269-1272`），`unsafe_read` 在后（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1330-1332`）。

---

## 5. 七个问题逐个答

### Q1 服务端的流怎么等 vLLM 的 event？event 怎么跨进程？

**怎么等**：
- `import_event(句柄)` → `wait_event(event, 服务端流)`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894`；RETRIEVE 同样：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1269-1272`。
- 后端实现就是 `event.wait(stream)`：`lmcache/v1/platform/base/event_ipc.py:250-252`。这是**流级等待**：只在服务端流里插一个依赖，CPU 立刻往下走。

**跨进程怎么传**：
- worker 端 `Event(interprocess=True)`，录在当前流上：`lmcache/v1/platform/base/event_ipc.py:221-223`，`lmcache/v1/client/worker_transfer.py:647-649`；导出成 bytes 句柄：`lmcache/v1/platform/base/event_ipc.py:225-238`。
- 句柄作为 STORE / RETRIEVE 的一个参数随 ZMQ 消息走：`lmcache/v1/client/worker_transfer.py:719-722`。
- 服务端 `Event.from_ipc_handle(device, handle)` 导入：`lmcache/v1/platform/base/event_ipc.py:240-244`。
- 回程对称：服务端自己的完成 event（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:844`）导出后放进回复，worker 在 `_on_raw_future_complete` 里导入：`lmcache/v1/transport/futures.py:295-302`。
- 底层用的是 torch 的 interprocess event（对应 CUDA IPC event 句柄）——这是我对底层 API 的背景知识，仓库里没有写【推断】。

**为什么要等**：STORE 要读的 KV 是 vLLM 流上的前向内核写的，拷贝在另一条流上，不加依赖就会读到半成品。直接证据是两处注释。【注释】“Producer event that orders reads of the engine KV cache.”（`lmcache/v1/client/worker_transfer.py:702`）；【注释】“In this mode the server copies the blocks on its own stream after the forward pass that produced them, and the engine never waits for that copy.”（`lmcache/v1/client/worker_transfer.py:812-814`）。"所以必须等"这一步是我的推理【推断】。RETRIEVE 的 event 则是【注释】“Producer event that orders writes to the engine KV cache.”（`lmcache/v1/client/worker_transfer.py:773`）。为什么写之前也要等，没有注释；我的理解是新分配的 block 可能还被 vLLM 流上更早的内核引用【推断】。

**两个容易漏的生命周期细节**：
1. 导入的 event 对象必须活到"流消费完这个等待"。【注释】“A stream wait queued on an imported event refers to it until the stream has consumed the wait, so the import must outlive the handler that queued it.”（`lmcache/v1/platform/base/cache_context.py:82-86`）。做法：`hold_imported_event` 登记，再在流上紧挨着等待挂 `release_imported_event` 回调：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:895-900`。【提交】引入于 `4d96107c`「[Fix][MP] Hold imported IPC events (#5439)」，提交说明只有标题；同时加了 AMD 侧的专门测试 `.buildkite/k3_tests/amd/imported_event_lifetime.py`。
2. 反方向：服务端导出的 event 要"多留一会"。【注释】“Persist the reference to the IPC events for a while so that the inference engine won't access the dangling references to the events.”（`lmcache/v1/platform/base/event_ipc.py:164-165`），容量 2048：`lmcache/v1/platform/base/event_ipc.py:166`。【提交】`fc143b0a`「Retain exported IPC events so peers can import their handles (#5280)」。

> 读者提示：vLLM 路径里 worker 的流**从不**等服务端的完成 event。`wait_for_layer_load` 直接 `return`（`lmcache/integration/vllm/lmcache_mp_connector.py:1122-1133`），`wait_on_stream` 只被 SGLang 连接器调用（`lmcache/integration/sglang/unified_lmcache_mp_connector.py:995`）。vLLM 靠"请求挂起 + `get_finished` 轮询 event.query()"保证没人读半截数据【推断：由上面两处加 `lmcache/v1/transport/futures.py:415-433` 推出】。

### Q2 48 个分页 block 怎么拼成 3 个连续对象？

**对象里的布局**（内核端的权威写法）：
- 【注释】“LMCache is using 2LTD all the times”（`csrc/cuda/mp_mem_kernels_detail.cuh:162`）。偏移公式：`token*spt + layer*chunk*spt + kv*nl*chunk*spt`（`csrc/cuda/mp_mem_kernels_detail.cuh:163-165`），即 `[kv][layer][token][nh*hs]`。对象形状由 `get_layout_desc` 登记：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:77-104`。
- vLLM 一侧（flash attention，`NL_X_TWO_NB_BS_NH_HS`）是"每层一个 `[2, NB, BS, NH, HS]` 张量"：块偏移 `block_id*scalars_per_block + kv*nb*scalars_per_block`（`csrc/cuda/mp_mem_kernels_detail.cuh:63-66`）。

**内核怎么拼**（staged 路径的内核；grid 的 y 维是"所有对象的所有 block"）：
- grid = `(kv_size, total_blocks, nl)`：`csrc/cuda/mp_mem_kernels_detail.cuh:440`。例子里 retrieve 一批 3 个 chunk：(2, 48, 36)；store 每次 1 个 chunk：(2, 16, 36)（设值代入）。
- 线程块 y 下标 `flat_block_idx` → `obj_idx = flat/16`、`block_idx_in_object = flat%16`，读 `engine_block_ids[flat]` 找到分页 block，写到对象内 token 偏移 `block_idx_in_object * bs`：`csrc/cuda/mp_mem_kernels_detail.cuh:320-332`。
- 所以 b0..b15 → h0 的 token 0..255，**b 编号不需要连续**，只需要按顺序给；这就是"48 个 block id 平铺成 3 个连续对象"的全部秘密。

**三条拷贝路径**（`lmcache/v1/multiprocess/object_group_transfer/dispatch.py:69-111`，每个同类型"run"各选一次）：
| 路径 | 条件 | 做法 |
|---|---|---|
| direct | 宿主内存对象 + 批量 memcpy 可用 + 格式 / 几何合格 + 每层块 ≥ 32 KiB | 每个 chunk 一次 `cudaMemcpyBatchAsync`，**无暂存缓冲、无内核**：`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:258-264` |
| native plan | 宿主内存对象 + 扩展有 `execute_object_group_transfer` | 暂存拷贝 + 内核整批一次 native 调用、一次释放 GIL：`lmcache/v1/multiprocess/object_group_transfer/staged.py:231-238` |
| staged loop | 兜底（含 GDS 等非宿主内存对象） | 每批：暂存拷贝 ↔ 内核，逐批 Python 发射：`lmcache/v1/multiprocess/object_group_transfer/staged.py:318-413` |

- staged 的方向：RETRIEVE 先 `stage`（宿主 → GPU 暂存）再内核（暂存 → 分页）；STORE 先内核（分页 → 暂存）再 `stage`（暂存 → 宿主）：`lmcache/v1/multiprocess/object_group_transfer/staged.py:399-413`。
- direct 的选择**没有配置开关**。【文档】“There is no configuration knob: the direct path is taken whenever it can be.”（`docs/design/v1/multiprocess/lmcache_driven_transfer_copy_paths.md:232`）。
- 32 KiB 门槛的来由【注释】“Smallest per-layer paged block the direct path moves. Below it, per-entry cost dominates and the staged kernel path wins”（`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:38-39`），并附 H200 实测：4–16 KB 块 direct 慢 1.5–4 倍（`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:40-41`）。
- 例子落在哪条：flash attention 格式 1 在文档支持列表里（`docs/design/v1/multiprocess/lmcache_driven_transfer_copy_paths.md:161`），每层块 32 KiB 刚好过线 → **CUDA ≥ 12.8 的构建走 direct，否则走 native plan / staged**。这是我的推演【推断】，依据是上面三处锚点；具体哪条取决于读者的 CUDA / 驱动版本。

**为什么 staged 要多一块 GPU 暂存缓冲，而不是内核直接写宿主内存？** 没查到明确说法。文档只说默认路径是"`cudaMemcpyAsync` 到临时缓冲 + 内核"（`docs/design/v1/multiprocess/lmcache_driven_transfer_copy_paths.md:144-147`），并说内核在块很少时只有约 24 GB/s、约为暂存拷贝线速的一半（`docs/design/v1/multiprocess/lmcache_driven_transfer_copy_paths.md:150-152`）。我的猜测是大块连续 DMA 吃满带宽、内核只做 GPU 内部的 scatter【推断】。

**小心 `block_ids_host` 的"被改过"**：`downsample_and_stage_block_ids` 就地改写传入的列表（`lmcache/v1/multiprocess/object_group_transfer/planning.py:125`），所以 STORE 里传给 `block_ids_host` 的 `gpu_block_ids`（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:986`）已经是裁剪后的；而"全空块掩码"必须在它**之前**算：【注释】“Computed on the raw block ids before downsampling mutates them.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:876-877`）。

### Q3 STORE 返回后，`finish_write` 是谁、在哪、什么时候调？为什么绕 C++？

**链条**（逐段锚点）：
1. 亲和线程在 S8 调 `submit_callback_to_stream(cupy_stream, "finish_write", l1_owners)`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1006-1011`。它在 Python 里把载荷 msgpack 编码，然后调 C++：`lmcache/v1/multiprocess/native_completion.py:147-153`。
2. C++ `record_completion_on_stream` 在流上发 host 函数：`csrc/cuda/completion_recorder.cpp:35-49`。
3. 流走到这个位置时，**CUDA 驱动线程**跑 `completion_host_callback`，只做"把消息 push 进带互斥锁的缓冲"，不碰 Python / GIL：`csrc/cuda/completion_recorder.cpp:12-15`，`csrc/cuda/completion_recorder.cpp:28-33`。
4. **`DeviceHostFuncDispatcher`**（一条 `PeriodicThread`）每 5 ms `drain_recorded_completions`，按 kind 查表、解码、调处理函数：`lmcache/v1/multiprocess/native_completion.py:62-78`，`lmcache/v1/multiprocess/native_completion.py:114-144`。
5. `_finish_write` → `StorageManager.finish_write`：staging 对象转正，除非该 key 已存在（此时保留旧的、丢掉新的）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:331-339`，`lmcache/v1/distributed/storage_manager.py:353-361`。

所以答案是：**排空线程**，在拷贝完成之后、最多再晚约一个轮询周期（5 ms，构造时没传参数，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:271`，是固定值）。

**为什么绕 C++？**
- 【注释】“Route finish_write / finish_read_prefetched through a C++ host callback so the driver thread doesn't acquire the GIL.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-270`）。
- 【注释】“Extends PR #2952's AB-BA fix (GIL x driver lock) to Server.store/retrieve.”（`lmcache/v1/multiprocess/native_completion.py:27`）。
- 【提交】`50e45434`「[MP][Bugfix] Fix store/retrieve deadlock by routing completion callbacks through C++ host callback (#3336)」——提交说明只有标题；该提交新增了 `native_completion.py`、`completion_recorder.cpp` 和测试 `tests/v1/multiprocess/test_completion_recorder.py`（`git show --stat` 可见）。
- 组合起来：Python 线程持 GIL 去调需要驱动锁的 CUDA 调用，同时驱动线程持驱动锁在回调里等 GIL，就是 AB-BA 死锁。"GIL × 驱动锁"这个说法来自注释；"谁拿谁等"的细节是我按注释还原的【推断】。

**处理函数抛异常会怎样？** 被捕获、计数、记日志，**不重试**：`lmcache/v1/multiprocess/native_completion.py:133-144`。后果推断：`finish_write` 没成功的对象留在 staging，直到写锁 TTL（默认 600 s，`lmcache/v1/distributed/config.py:429`）【推断，依据 L1 状态机见上一趟 §4.5】。

**为什么同一批里还有 `release_imported_event`？** 见 Q1。同一套机制，同一条排空线程。

### Q4 「要么全存要么全不存」具体怎么做到的？中途失败会怎样？

先厘清"全"指什么。看三类情形：

| 情形 | 发生在哪 | 结果 |
|---|---|---|
| ① 入口没注册 / block id 数不够 | 入队之前 | 什么都没预留；返回 `False`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:797-809`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:853-872` |
| ② 入队阶段抛 Python 异常（任一 object group 的 reserve 或拷贝） | `try` 内 | `store_succeeded` 保持 `False` → `stored_count = 0` → 对**已预留**的全部 key 挂 `abort_write`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:998-1018` |
| ③ 某些 key 预留不成功（已在 L1、或 L1 没空间） | `reserve_write` 返回字典里缺这些 key | **不算失败**：缺的 key 在 `memory_objs` 里是 `None`，拷贝时被跳过，其余照存、照提交：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:973-975` |

- 【注释】docstring 明说 “All-or-nothing.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:782`），并解释 “the whole store is skipped and nothing is committed; a subsequent retrieve misses and the engine recomputes.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:784-786`）。提交点注释：“Fail closed: commit the reserved objects only when every chunk copied successfully; otherwise the whole store is skipped.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1003-1004`）。
- 为什么 block id 不够要整单放弃：【注释】“A short list (e.g. a caller/protocol bug) would otherwise drive the transfer kernel to read out-of-bounds GPU memory, so skip the whole store and commit nothing rather than caching a partial or garbage entry.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:847-850`）。
- 这个守卫检查的是**裁剪前**的 id：“Checked on the raw block ids, before cutting drops the per-chunk blocks that sliding-window groups do not need.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:851-852`）。【提交】最初来自 `cec6092d`「[Core] add support for hybrid memory allocator (#3419)」（该提交首次在 `gpu_transfer.py` 加入 Fail closed 注释，提交说明只有署名行）。

**"中途失败"以前和现在不同**：
- 现在：入队前先把每个预留的 owner 记下来（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:939-940`，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:964`），失败时在**同一条流上、已入队的部分拷贝排空之后**调 `abort_write`，把预留对象丢掉（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1014-1018`，`lmcache/v1/distributed/storage_manager.py:393-413`）。
- 以前：【提交】`831e0502`：“A store whose copies or completion preparation failed left its staging reservations to expire with the write TTL, which blocks every later writer of those keys.”
- 兜底：如果"记 owner"本身失败，预留只能等写锁 TTL：【注释】“If owner capture fails, uncaptured reservations retain their write-TTL lifetime.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:789-790`）。
- 测试把"成功 / 失败 × 1 或 2 个 L1"的路径都覆盖了：`tests/v1/multiprocess/test_l1_owner_completion.py:35-38`；注意它的自述 “this is a CPU contract test, not a CUDA ordering test”（`tests/v1/multiprocess/test_l1_owner_completion.py:46-47`）。

**没覆盖的**：GPU 上**异步**执行出错（内核或 memcpy 在 GPU 上才失败）不在这个 `try` 里，因为 `try` 只包着"入队调用"【推断：由 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:943-1000` 的结构得出】。

**返回值别误读**：返回的 `bool` 只表示"没有致命错误"，不是"每个 chunk 都存了"（docstring 说得很明白，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:770-774`）。想知道哪些 chunk 真存了要看第三个返回值掩码：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1065-1079`。比如（设）若 B 的 STORE 覆盖 `[0,1024)`（实际调度器只发 `[768,1024)`，见上一趟 B8），前 3 个 key 已在 L1，预留不成功→跳过，只存新的 h3′。

**RETRIEVE 的对称做法**：缺 key → `break` + 标失败（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1333-1336`）；**锁无论成败都放**，因为 `read_completion` 在入队前已打包（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1314-1318`），回调挂在 `finally` 里且只看 `retained_keys`（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1371-1376`）。测试：`tests/v1/multiprocess/test_lmcache_driven_transfer_skip.py:397-432`。worker 收到失败 → 这批 block 记入 `error_block_ids`，vLLM 重算：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2200-2202`。

### Q5 RETRIEVE 的 `skip_first_n_tokens` 在拷贝里怎么生效？

**从哪来**：调度器把 `skip_first_n_tokens = num_vllm_hit_tokens − start_token_idx`（`lmcache/integration/vllm/lmcache_mp_metadata.py:644`），而 `start_token_idx` 是 vLLM 命中数向下取整到 chunk（`lmcache/integration/vllm/lmcache_mp_metadata.py:618-622`）。所以 skip 始终小于一个 chunk、**从 `key.start` 起算**（docstring：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1153-1155`）。

**为什么要有**：【注释】“The server must skip writing to these positions to avoid a cross-stream data race: the retrieve writes on the LMCache CUDA stream while concurrent requests may read these APC-shared blocks on the vLLM stream.”（`lmcache/integration/vllm/lmcache_mp_metadata.py:640-643`）。【提交】`38dddf92`「[Correctness]: Avoid overwriting APC overlap (#2671)」，说明只有标题；diff 显示它给当时的内核加了 `skip_prefix_n_tokens` 参数（**token 粒度**）。现在是 **block 粒度**的 `skip_prefix_n_blocks`。

**怎么生效**（B′：skip = 64，一批 2 个 chunk，批内下标从 0 起）：
1. 服务端把 `skip_first_n_tokens` 原样传给 `transfer_object_group`（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1346`；STORE 固定传 0，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:983`）。
2. staged 路径：`effective_start = max(批起点 token, skip)` = 64；`skip_tokens_in_chunk = 64`：`lmcache/v1/multiprocess/object_group_transfer/staged.py:384-388`。
3. 换算成 block：`calculate_num_blocks(64) = 64×slots/tpb // bs = 4`（**向下取整**，`lmcache/v1/kv_layer_groups.py:666-669`）。
4. 内核：`flat_block_idx < skip_prefix_n_blocks` 直接 return：`csrc/cuda/mp_mem_kernels_detail.cuh:321-323`。前 4 个 block（vLLM 已有的 token 256–319）不写。
5. **整个 chunk 都被盖住**时：`first = skip_tokens_in_chunk // chunk` 个对象连暂存拷贝都不做：`lmcache/v1/multiprocess/object_group_transfer/staged.py:390-400`。测试：【测试】“The skip covers chunk 0 and one block of chunk 1: chunk 0 is not staged, chunk 1 keeps its slot, and the kernel skips five blocks.”（`tests/v1/multiprocess/test_staged_loop.py:113-114`）。
6. 整批都在 skip 之前：`effective_start >= batch_end_token` → `continue`：`lmcache/v1/multiprocess/object_group_transfer/staged.py:385-386`。
7. direct 路径按 chunk 做同一算术，把 `skip_blocks` 随 `DirectCopyObject` 交给 C++：`lmcache/v1/multiprocess/object_group_transfer/direct_copy.py:226-253`。

**两个可能让人意外的点**：
- 被跳过的 block 所在的 chunk 仍会整块从宿主搬到暂存（staged 路径）：内核的字节统计注释写得很直白，【注释】“because a launch may skip leading blocks (skip_prefix_n_blocks) that the staging copies still carried.”（`csrc/cuda/mp_mem_kernels.cu:86-88`）。direct 路径按 block 跳（C++ 里循环从 `b = skip` 开始，`csrc/cuda/mp_mem_kernels.cu:676`），不多搬。
- skip 向下取整成 block：如果某个 skip 不是 block 的整数倍，那个残块会被重写。vLLM 的 APC 命中总是整块，所以实际不发生【推断】。

### Q6 GPU 亲和线程池：怎么固定？STORE 和 RETRIEVE 会互相影响吗？

**怎么固定**：
- 每个 ZMQ 请求的 key = `hash(客户端 identity)`：`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:299`。池里第一次见到某个 key 就绑到"下一个空闲槽位"（`_next_slot % 线程数`），之后永远是它：`lmcache/v1/multiprocess/affinity_pool.py:131-145`。槽位不够时多个 key 共享一条线程并只警告一次：`lmcache/v1/multiprocess/affinity_pool.py:156-166`。
- 每条线程一个 `queue.Queue`，`_worker` 逐个取出执行：`lmcache/v1/multiprocess/affinity_pool.py:107-125`。【注释】“Within each worker, tasks execute sequentially in FIFO order.”（`lmcache/v1/multiprocess/affinity_pool.py:6`）。
- 默认只有 1 条：`lmcache/v1/multiprocess/config.py:59`；文档：【文档】“Requests from the same vLLM instance are always dispatched to the same thread, eliminating GPU transfer lock contention.”（`docs/source/mp/configuration.rst:104-106`）。
- 提交史：【提交】`3137ce0b`「[MP][Feat] support worker-affinity in the MQ thread pool (#2842)」新增 `affinity_pool.py`，同一提交里**删掉**了 `GPUCacheContext` 的每设备锁，被删注释写着 “to avoid deadlocks with the implicit CUDA driver lock”（commit `3137ce0b` 的 diff）。即：全局锁 → 每设备锁 → 亲和线程（无锁）。后来【提交】`d1b63716`「Route affinity thread pool on dense worker rank instead of hashed identity (#3905)」把"哈希取模"改成"按首次出现顺序占槽"，提交说明里的相关句子是 “fixing affinity pool thread assignment bug” 与 “mapping affinity_key to slot in a round robin manner”（commit `d1b63716`）；【测试】名字也对得上：`test_modulo_colliding_keys_get_distinct_workers`（`tests/v1/multiprocess/test_affinity_pool.py:76`）。具体是什么 bug（推测为取模后不同 key 撞槽）说明里没写【推断】。

**这条线程保护的是什么**：
- 【注释】“so that all operations for a given vLLM instance land on one thread, eliminating the need for per-instance locks on the shared temporary GPU buffer.”（`lmcache/v1/multiprocess/affinity_pool.py:8-10`）。
- 具体就是：每个实例**一份**暂存缓冲（4 槽）和**一份** block id 缓冲，`stage_block_ids` 每次都覆盖写它（`lmcache/v1/platform/base/cache_context.py:374`）。【注释】“Single-thread assumption: no lock needed.”（`lmcache/v1/platform/devices/cuda/cache_context.py:418-419`）。
- 为什么"线程串行 + 一条流"就够：前一次操作的内核先于后一次对缓冲的覆盖写在同一条流里排队【推断：由"每实例一条流" `lmcache/v1/platform/devices/cuda/cache_context.py:453` 加上本节两条注释推出】。

**STORE 与 RETRIEVE 会互相影响吗？** 会，但只是排队，不涉及数据：
1. **CPU 侧**：同一 worker 的两种请求同在一个队列，后到的要等前一个"入队完成"（不是"拷完"）才开始；入队里最慢的是逐 object group 的 `reserve_write` 和拷贝发射。
2. **GPU 侧**：它们进同一条服务端流，所以 RETRIEVE 的拷贝要等排在前面的 STORE 拷贝**物理完成**才开始，即使两者的 block 毫无交集（队头阻塞）【推断：同流顺序执行的 CUDA 语义 + 一实例一条流】。
3. **数据侧**：STORE 读的是刚算完的 block，RETRIEVE 写的是新分配的 block，vLLM 保证不重叠；唯一的耦合是 L1：STORE 的 key 只有等 `finish_write` 才对读者可见（见 Q7）。
4. **跨 worker**：默认 `max_gpu_workers=1`，所有 worker 的 STORE / RETRIEVE 共用一条线程（TP>1 时每个 rank 的 CPU 入队也被串行化）；调大后才能并行。池创建时就会提示去核对客户端数：`lmcache/v1/multiprocess/affinity_pool.py:93-101`。

### Q7 返回给 worker 的"完成 event"录在流的哪个位置？

**位置**（STORE）：`[等 vLLM event] → [release_imported_event 回调] → [所有 D2H] → ★完成 event → [finish_write / abort_write 回调] → [MP_STORE_END 观测事件]`。
- 代码顺序：等待 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:894`；释放回调 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:896-900`；拷贝 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:978-987`；**录 event** `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1002`；`finish_write` 回调 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1007-1011`；结束事件 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1036-1051`。
- 【测试】把顺序钉死：`assert order == ["callback", "copy", "copy", "record", "callback"]`（`tests/v1/multiprocess/test_l1_owner_completion.py:193`）。
- RETRIEVE 同构：录 event `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1370`，然后 `finish_read_by_owner` `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1372-1376`。
- 失败路径：block id 下溢时在**没有任何设备工作**的流位置上直接录一个 event（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:867-871`），worker 立刻能看到完成；入口没注册则返回空句柄 `b""`，对应"没有设备工作，RPC 回来即终态"：【注释】“An empty handle means the remote side submitted no device work, so completion of the original future is also terminal completion of this future.”（`lmcache/v1/transport/futures.py:264-268`）。

**为什么放在回调之前**：worker 要知道的是"这些 vLLM block 现在可以回收 / 可以用了"，那只取决于拷贝；提交 / 放锁是服务端内部的事【推断：没有注释说明顺序的理由，是我按 worker 的用途反推的】。

**后果**（读者最容易踩）：
- STORE：worker 看到"完成"时，对象还没转正，要再等排空线程一轮（≤ 约 5 ms + 队列）。紧随其后到达的 LOOKUP 可能还看到 miss——只是少一次复用，不会读到坏数据【推断】。
- RETRIEVE：vLLM 已经能开始用这批 block，读锁稍后才放。锁有 TTL 兜底（读锁默认 300 s，`lmcache/v1/distributed/config.py:432`），所以回调晚一点无碍。
- 完成 event 在流上的位置也决定了 worker 的 `query()` 何时变真：`lmcache/v1/transport/futures.py:415-433`。

---

## 6. 值得做成源码页的代码段（6 段）

> 行号均为 HEAD。每段给出：范围、在做什么、逐行批注（行号 → 要点 → 为什么及等级）。

### P1 STORE 前半：进流 → 守卫 → 等 event → 挂释放回调
**`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:837-900`**（64 行）
做什么：进入服务端流的上下文，先建一个自己的完成 event，过守卫，处理 block id，等 vLLM 的 event。
- **840-844**：`torch_dev.device(...)` + `torch_dev.stream(cache_context.stream)`，之后所有内核 / memcpy / 回调都默认发到这条流。完成 event 在**最前面**建好，后面每个出口都能导出它（建在前面的理由没查到，见 §7）。
- **846-852（注释）+ 853-858**：下溢守卫。注释原文见 Q4。注意是在裁剪前的原始 id 上检查。
- **867-871**：守卫失败也会 `record_event` 再导出一个真 event，而不是返回空句柄（理由没查到，§7 第 3 条）。
- **874-885**：全空块掩码，在 `downsample` 之前算。【注释】“Computed on the raw block ids before downsampling mutates them.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:876-877`）
- **887-889**：`downsample_and_stage_block_ids` 就地改写列表，并把 id 拷进 GPU 缓冲（`lmcache/v1/multiprocess/object_group_transfer/planning.py:125`，`lmcache/v1/platform/base/cache_context.py:374`）。
- **891-894**：★ 导入 + 流等待。最关键的 4 行。
- **895-900**：持有导入对象 + 挂 `release_imported_event`。【注释】见 Q1。

### P2 STORE 后半：预留 → 拷 → 录 event → 提交或放弃
**`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:937-1018`**（82 行；可从 943 起截成 76 行）
- **937-942**：四个局部状态：`reserved_dict`（本组预留）、`all_dict`（累计）、`l1_owners`（owner 表，**拷贝前**就记）、`store_succeeded=False`（默认失败，只有走到 998 才翻成真）。
- **960-964**：`reserve_write` 之后立刻 `l1_owners.update(...)`。【注释】“Captured before any copy runs, so a failed store aborts per owner.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:939`）。
- **973-975**：没预留到的 key 变成 `None`，后面的拷贝跳过它。**这就是"部分 key 不成功不算失败"的位置**。
- **977-983**：`batch_size=1`、`skip=0`；那句 `NOTE: batch_size must stay 1 for store.` 没写原因（§7）。
- **999-1000**：`except Exception` 只记日志，**不重抛**——失败靠 `store_succeeded` 传递。
- **1002**：★ `finally` 里最先做的是**录完成 event**，然后才决定挂哪个回调。
- **1005-1018**：`stored_count = len(all_dict) if store_succeeded else 0`；非零挂 `finish_write`；否则如果有 owner 就挂 `abort_write`。依据见 Q4 引的 1003-1004 注释。
- **1019-1035**：日志里的 token 数只算真存了的 chunk（观测用）。

### P3 RETRIEVE 的核心：先打包锁，再读，再拷，成败都放锁
**`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1306-1377`**（72 行）
- **1307-1313**：`read_owners`（谁上的读锁）、`retained_keys`（要放哪些锁）。滑窗模型只锁窗口内后缀（1280-1282 注释），全注意力则是全部。
- **1314-1318**：【注释】“Capture every retained lock before enqueuing GPU work, including groups that a later transfer failure may leave unvisited.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1314-1315`）——放锁的清单**必须在入队之前**定下来，否则中途失败会漏放。
- **1330-1336**：`unsafe_read` 的"unsafe"= 不新加锁；缺任何 key 就 `break`，不拷半截。
- **1339-1340**：【注释】“None-pad the skipped prefix so block-id slicing is unchanged.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1339`）（只对滑窗有用；全注意力 skip=0）。
- **1341-1350**：`batch_size=cache_context.max_batch_size`（4）与 `skip_first_n_tokens`——注意与 STORE 的 `1` / `0` 对照。
- **1366-1368**：异常也只标失败。
- **1370-1376**：`finally`：先录 event，再挂 `finish_read_by_owner`；条件是 `if retained_keys`，**与成败无关**。
- 对照看 R3（1265-1278）的"等 event"，和 P1 的 891-900 是同一段话。

### P4 流回调链：注册表 + 排空线程
**`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-297`** + **`lmcache/v1/multiprocess/native_completion.py:114-153`**（29 + 40 行）
- **LDT 269-270**：【注释】“Route finish_write / finish_read_prefetched through a C++ host callback so the driver thread doesn't acquire the GIL.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-270`）
- **LDT 272-296**：五个 kind 各配一个"处理函数 + 载荷类型"：`dict[ObjectKey,int]`、`list[ObjectKey]`、`L1WriteCompletion`、`tuple[int,int]`。载荷类型交给 `msgspec` 的解码器（`lmcache/v1/multiprocess/native_completion.py:84-87`），所以**载荷要能被 msgpack 编码再解码**。
- **NC 117-120**：`drain_recorded_completions()` 出错也只记日志返回——线程不能死。
- **NC 125-132**：未知 kind 只警告并丢弃。
- **NC 133-144**：解码 + 调用放进 `try`，异常计数；**一条消息出错不影响后面的**。
- **NC 147-153**：`submit_callback_to_stream` 只有两行：编码、交给 C++。这是亲和线程唯一碰到的部分。
- 配合看 `csrc/cuda/completion_recorder.cpp:28-33`（驱动线程上只 push）。

### P5 staged 循环 + 内核：skip 和布局怎么落地
**`lmcache/v1/multiprocess/object_group_transfer/staged.py:370-413`**（44 行）+ **`csrc/cuda/mp_mem_kernels_detail.cuh:308-333`**（26 行）
- **staged 370-378**：批里有 `None`：retrieve 抛错；store 跳过**整批**（所以 STORE 必须 `batch_size=1`，否则一个 `None` 会连累别的 chunk——这是我的推断，原因没查到，见 §7）。
- **staged 384-388**：skip 的换算入口（Q5 第 2 步）。
- **staged 390-393**：【注释】“On retrieve the kernel skips the chunks before the first one it writes, so they are not staged.”（`lmcache/v1/multiprocess/object_group_transfer/staged.py:390-391`）
- **staged 399-413**：retrieve = 先 stage 后 kernel；store = 先 kernel 后 stage。
- **kernel 321-323**：★ `flat_block_idx < skip_prefix_n_blocks` 就 return。
- **kernel 324-332**：`obj_idx`、`block_idx_in_object`、`engine_block_ids[flat]`、对象内偏移 `block_idx_in_object * bs`——布局转换的全部。

### P6 亲和池：绑定与执行
**`lmcache/v1/multiprocess/affinity_pool.py:107-145`**（39 行）
- **107-125**：`_worker` 逐个取任务；若返回 `DeferredResponse`（ROCm 主机等待路径用），线程**立刻**空出来，future 之后由别处完成（`lmcache/v1/multiprocess/affinity_pool.py:12-14`）。
- **131-145**：`_slot_for_key`：已绑定就返回；第一次见就占 `_next_slot % _num_workers`；`is_overflow` 在槽被占满之后才为真。
- 【测试】“Tasks with the same affinity key execute sequentially.”（`tests/v1/multiprocess/test_affinity_pool.py:163`）。

---

## 7. 没查到为什么（如实）

1. **`NOTE: batch_size must stay 1 for store.`（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:977`）为什么必须是 1？** 该行由 `5f699d3f` 加入；被它替换掉的旧代码里有一句注释，【提交】“Store is not batched, so we always use batch_idx=0”（commit `5f699d3f` 的 diff，在旧文件 `lmcache/v1/multiprocess/modules/gpu_transfer.py`），只说"store 不分批"，没说原因。我的猜测：staged 循环里批内任一 `None` 会跳过整批（`lmcache/v1/multiprocess/object_group_transfer/staged.py:370-378`），而 STORE 的 `None` 是常态（key 已存在），批 > 1 会连累好的 chunk【推断】。
2. **STORE 里为什么在最前面就建完成 event（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:844`）？** 没有注释。可能是让每个出口都有 event 可导出【推断】。
3. **下溢守卫失败时为什么导出一个真 event 而不是空句柄？** docstring 说"没有设备工作时句柄为空"（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:774`），但守卫分支（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:867-871`）并非如此。没查到理由。
4. **`4d96107c`、`fc143b0a`、`50e45434`、`38dddf92`、`cec6092d` 的提交说明只有标题（或只有署名）**：这些修复的具体失败现象只能从代码注释和测试推断，无法从提交史还原。
5. **为什么 staged 路径要经过 GPU 暂存缓冲**：见 Q2，只有推断。
6. **排空线程间隔 5 ms 的取舍**：`drain_interval_seconds=0.005` 是 `50e45434` 引入的默认值（`lmcache/v1/multiprocess/native_completion.py:67`），没有注释说明为何是 5 ms，也没有 CLI 参数。
7. **完成 event 为什么录在提交 / 放锁回调之前**：没有注释，见 Q7（推断）。
8. **`max_batch_size = 4` 的来由**：`lmcache/v1/platform/devices/cuda/cache_context.py:448` 写死 4，没查到依据。
9. PR / issue 正文无法访问；以上"提交"等级的引用只看了提交说明和 diff。

---

## 8. 可改进处【观点】

1. 【观点】排空线程按 5 ms 轮询，会给"对象可见"和"锁释放"加一个固定尾巴；若 C++ 缓冲有条件变量 / eventfd 可以改为事件驱动（`lmcache/v1/multiprocess/native_completion.py:106-112`）。
2. 【观点】`finish_write` 抛异常后没有重试也没有显式 `abort`，留给写锁 TTL（默认 600 s）去兜；可考虑在排空线程异常处理里补一次 `abort_write`。
3. 【观点】完成 event 与"提交完成"是两个事实，但 worker 只看到前者。如果需要"STORE 之后立刻可被 LOOKUP 命中"的语义，需要把 event 放到回调之后或另给一个信号。
4. 【观点】`prepare_read_completion` / `finish_read_by_owner` 已被标 `lmcache_deprecate`，附 TODO（`lmcache/v1/distributed/storage_manager.py:1399-1400`），读者看到时不必当成长期接口。
5. 【观点】下溢守卫的返回值与 docstring 的"空句柄"约定不一致（§7 第 3 条），统一后 worker 侧的分支会更少。

---

## 9. 给讲解员的读者层提示

- **最容易困惑的两处**：①"函数返回了，事情还没做完"（S9→S11）；②"event 有两个，方向相反"：vLLM→服务端（让服务端流等），服务端→vLLM（让 worker 轮询）。建议两个 event 用不同的名字 / 颜色，并在时间线里标出各自录在流的哪里。
- **最值得并排对照的两件事**：STORE 的 `batch_size=1, skip=0` 对 RETRIEVE 的 `batch_size=4, skip=N`（P2 对 P3）；`finish_write`（提交）对 `finish_read_by_owner`（放锁）——都是回调，都在 `finally` 里挂，但 STORE 的回调**区分成败**（finish / abort），RETRIEVE 的**不区分**。
- **别讲错的点**：①"全存或全不存"只管入队期异常与 block 不足，**不管"key 已存在"**；②`unsafe_read` 的"unsafe"是"不新加锁"；③skip 是 block 粒度，不是 token 粒度；④`max_gpu_workers` 默认 1，亲和池线程数不是"每 worker 一个"，而是"首次出现的客户端依次占槽"。
- **例子里的数**（均为设值推算）：对象 36 MiB；每块每层每平面 32 KiB（恰在 direct 的 32 KiB 门槛）；暂存 4 × 36 MiB；B′ 的 skip = 64 token = 4 block；kernel grid (2, 48, 36)（retrieve 一批 3 个 chunk）。
- **可以在下一层不展开的**：unfull chunk（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1479`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1564`，提交 `30dc05fb`）、滑窗 / Mamba 的 object group 拆分、ROCm 主机等待（`lmcache/v1/multiprocess/rocm_host_event_wait.py`，默认关，`lmcache/v1/multiprocess/config.py:118`）、GDS / NIC 引擎（`lmcache/v1/multiprocess/object_group_transfer/engines.py:27-52`）。
- **运维可调汇总**：`--max-gpu-workers`（默认 1）；direct 无开关；`max_batch_size=4`、`_MAX_BLOCK_IDS=1<<20`、`_EXPORTED_EVENT_RING_SIZE=2048`、`_DIRECT_COPY_MIN_BLOCK_BYTES=32<<10`、排空间隔 5 ms 都是硬编码；写锁 TTL 600 s / 读锁 TTL 300 s（`lmcache/v1/distributed/config.py:429`、`lmcache/v1/distributed/config.py:432`）。
