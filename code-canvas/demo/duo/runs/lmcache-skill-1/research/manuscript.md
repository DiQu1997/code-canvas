# LMCache：KV cache 怎么存下来、下一个请求又怎么取回来复用（顶层底稿）

> 研究对象：LMCache 仓库 HEAD = `b27f6bd9e2`。
> 本稿用途：给讲解员做顶层「机器图 + 说明卡 + 逐步推演」。每个部件只落到关键锚点，内部机制给出入口，不展开到底。
> 锚点格式：`文件:行号` 或 `文件:起-止`，都在 HEAD 上成立。引文用 “ ” 括起，逐字照抄原文（英文不翻译），后面紧跟出处。
> “为什么”的来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。

---

## 0. 一句话结论（先看这个）

LMCache 现在是一个**独立的缓存服务进程**。vLLM 里装一个「连接器」（`LMCacheMPConnector`），它分两半：

- **调度器那一半**只管「记账和下单」：算出哪些 token 该存、哪些可以从 LMCache 取，把这些决定写进每一步的元数据里。
- **GPU worker 那一半**只管「发货单」：在每次前向计算前后，把「请把这些 GPU 块拷走 / 拷回来」的请求通过 ZMQ 发给 LMCache 服务。

LMCache 服务收到请求后，**自己**动手：通过 CUDA IPC 直接读写 vLLM 的 GPU 显存，把 KV 拷到自己的 CPU 内存（L1）里；下一次有相同前缀的请求来，先「查 + 上读锁」，再把 L1 里的数据拷回 vLLM 新分配的 GPU 块。

身份怎么认？**服务端**把 token 序列按 256 个一块切开，算滚动哈希（blake3），哈希 + 模型名 + rank 等组成 `ObjectKey`。相同前缀 → 相同哈希链 → 命中。

---

## 1. 主路是哪条？为什么选它

这个仓库支持很多组合。本稿讲透的是**默认配置下最常见的那一条**：

| 维度 | 主路取值 | 依据 |
|---|---|---|
| 部署形态 | MP（多进程）模式：独立的 `lmcache server` + vLLM 里的 `LMCacheMPConnector` | 进程内（InProcess）实现已从本仓库移除：提交 `5b9db86f` 标题 “Remove in-tree InProcess implementation”（commit `5b9db86f`）；快速上手文档只剩 MP：`docs/source/getting_started/quickstart.rst:45-47` |
| 推理引擎 | vLLM | 快速上手示例就是 vLLM：`docs/source/getting_started/quickstart.rst:67` |
| 请求传输 | ZMQ（DEALER/ROUTER） | 默认 `transport` 是 `"zmq"`：`lmcache/v1/multiprocess/config.py:43` |
| 数据怎么搬 | `lmcache_driven`：服务端拿 CUDA IPC 句柄自己拷 | 服务端默认 `--supported-transfer-mode` 是 `lmcache_driven`：`lmcache/v1/multiprocess/config.py:530`；客户端默认 `auto`，在 CUDA 上解析成 lmcache_driven：`lmcache/integration/vllm/vllm_multi_process_adapter.py:131-139` |
| 硬件 | NVIDIA GPU，TP=1，单一 KV cache group（普通全注意力模型） | 讲解用的最简情形 |
| 存储层 | 只有 L1（CPU 固定内存），**没有 L2** | `--l2-adapter` 默认是空列表：`lmcache/v1/distributed/l2_adapters/config.py:443-446` |
| 块大小 | chunk = 256 token | `lmcache/v1/multiprocess/config.py:52` |
| 只存什么 | 只存 prompt 里**完整的** 256-token 块 | 见 §5.3 |

其它模式（gRPC、engine_driven、L2、P2P、CacheBlend、lazy offload、unfull chunk……）只在 §8 点名，说明从主路哪里岔出去。

---

## 2. 全景：参与者有哪些

### 2.1 进程与线程

**进程 A：vLLM 调度器进程**（1 个）
- 里面有 `LMCacheMPConnector`，角色是 `SCHEDULER`：`lmcache/integration/vllm/lmcache_mp_connector.py:832-836`。
- 它持有一个 `LMCacheMPSchedulerAdapter`（ZMQ 客户端 + 查找状态）。
- 运行在 vLLM 调度线程上，**这条线程就是推理的关键路径**，所以这里的代码处处在避免阻塞（§5.1）。

**进程 B：vLLM GPU worker 进程**（TP 几就有几个；主路 TP=1，就是 1 个）
- 里面有 `LMCacheMPConnector`，角色是 `WORKER`：`lmcache/integration/vllm/lmcache_mp_connector.py:865-883`。
- 它持有一个 `LMCacheMPWorkerAdapter` 和一个 `LMCacheDrivenTransferContext`（CUDA IPC 传输上下文）。
- 运行在模型前向的线程上。

**进程 C：LMCache 服务进程**（每个节点一个，可被多个 vLLM 共享）
- 由 `lmcache server` 启动；核心是 `MPCacheServer`（一个“拼装器”，把若干模块拼起来）：`lmcache/v1/multiprocess/server.py:85-86`。
- 线程：
  - **ZMQ 主循环线程** `mq-server-thread`：收包、分派、发回复：`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:210-212`。
  - **GPU 亲和线程池**（默认 1 个线程）：跑 STORE / RETRIEVE：`lmcache/v1/multiprocess/transport/zmq_impl/server.py:135-136`，默认值 `lmcache/v1/multiprocess/config.py:59`。
  - **普通 CPU 线程池**（默认 1 个线程）：跑 LOOKUP / QUERY_PREFETCH_STATUS / FREE_LOOKUP_LOCKS / END_SESSION：`lmcache/v1/multiprocess/transport/zmq_impl/server.py:139-140`，默认值 `lmcache/v1/multiprocess/config.py:63`。
  - **DeviceHostFuncDispatcher 排空线程**：GPU 流上的拷贝完成后，由它调用 `finish_write` / `finish_read`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-293`。
  - 后台：L1 淘汰线程（`L1EvictionController`）、L1→L2 的 `StoreController`、L2→L1 的 `PrefetchController` 循环、Session 过期清理线程。主路下没有 L2，后两者基本闲着。

### 2.2 模块 / 对象（建议画进机器图的 9 个部件）

| # | 部件 | 在哪个进程 | 一句话职责 | 入口锚点 |
|---|---|---|---|---|
| ① | **调度侧连接器** `LMCacheMPConnector`(SCHEDULER) + 每请求一个 `LMCacheMPRequestTracker` | A | 每步决定：这个请求能从 LMCache 取多少 token？哪些块该存？ | `lmcache/integration/vllm/lmcache_mp_connector.py:1377`，`lmcache/integration/vllm/lmcache_mp_metadata.py:69` |
| ② | **调度侧适配器** `LMCacheMPSchedulerAdapter` + `LookupState` | A | 发 LOOKUP、轮询结果、发 FREE_LOOKUP_LOCKS / END_SESSION | `lmcache/integration/vllm/vllm_multi_process_adapter.py:575` |
| ③ | **worker 侧连接器 + 适配器** `LMCacheMPWorkerAdapter` | B | 按元数据发 STORE / RETRIEVE，轮询完成 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:1277` |
| ④ | **CUDA IPC 传输上下文** `LMCacheDrivenTransferContext` | B | 注册显存句柄、导出 CUDA event、把请求包装成“设备感知 future” | `lmcache/v1/client/worker_transfer.py:525` |
| ⑤ | **ZMQ 请求服务器** `MessageQueueServer` + 两个线程池 | C | 按操作名分派到处理函数；决定哪个线程跑 | `lmcache/v1/multiprocess/transport/zmq_impl/mq.py:181` |
| ⑥ | **查找模块** `LookupModule` | C | LOOKUP：算哈希、提交预取（= 查 L1 + 上读锁）；QUERY 返回命中数 | `lmcache/v1/multiprocess/modules/lookup.py:142` |
| ⑦ | **传输模块** `LMCacheDrivenTransferModule` | C | REGISTER_KV_CACHE / STORE / RETRIEVE：真正的 GPU↔CPU 拷贝 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:226` |
| ⑧ | **共享上下文** `MPCacheServerContext`：`SessionManager` + `TokenHasher` + `LayoutDescRegistry` | C | token → 哈希 → `ObjectKey`；记住每个请求的哈希链和锁 | `lmcache/v1/multiprocess/engine_context.py:361` |
| ⑨ | **存储管理** `StorageManager` → `PrefetchController` → `L1Manager`（+ 淘汰控制器） | C | 两阶段写入、带 TTL 的读锁、按前缀命中、内存分配 | `lmcache/v1/distributed/storage_manager.py:131`，`lmcache/v1/distributed/l1_manager/base.py:142` |

外加一个**被共享的存储单元**：vLLM 的 paged KV cache（GPU 显存，按 block 编号）。它属于 vLLM，但 LMCache 服务通过 CUDA IPC 直接读写它——这是理解整套设计的关键。

### 2.3 关键数据结构（会在连线上流动的东西）

| 名字 | 是什么 | 锚点 |
|---|---|---|
| `LoadStoreOp` | 调度器下给 worker 的一张“工单”：打包好的 token、要搬的 GPU block 编号、token 区间 `[start,end)`、`skip_first_n_tokens` | `lmcache/integration/vllm/vllm_multi_process_adapter.py:483-502` |
| `IPCCacheServerKey` | 线上传输的 key：模型名、world_size、worker_id、**原始 token（打包）**、区间、request_id、cache_salt | `lmcache/v1/common/custom_types.py:49-88` |
| `ObjectKey` | 存储层真正的身份：`chunk_hash` + `model_name` + `kv_rank` + `object_group_id` + `cache_salt` + tags | `lmcache/v1/distributed/api.py:226-250` |
| `MemoryObj` | L1 里一块内存，装一个 chunk（256 token）在一个 object group 下**所有层**的 KV | 布局见 `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:77-104` |
| `Session` | 服务端每请求一份：token 序列 + 已算好的滚动哈希 + 这次 lookup 锁了什么 | `lmcache/v1/multiprocess/session.py:32` |

---

## 3. 连线：谁把什么交给谁（建议画成线）

> 读者最容易困惑的是“时间上不连续”：一次存储/读取跨越**多个调度步、三个进程、四五个线程**。下面每条线都标了“同步还是异步”。

| 线 | 从 → 到 | 交接方式 | 交的是什么 | 锚点 |
|---|---|---|---|---|
| L1 | vLLM 调度器 → ① | 函数回调（vLLM 的 KV connector 接口） | `get_num_new_matched_tokens` / `update_state_after_alloc` / `build_connector_meta` / `request_finished` | `lmcache/integration/vllm/lmcache_mp_connector.py:1377`、`lmcache/integration/vllm/lmcache_mp_connector.py:1600`、`lmcache/integration/vllm/lmcache_mp_connector.py:1694`、`lmcache/integration/vllm/lmcache_mp_connector.py:1754` |
| L2 | ① → ② | 同进程函数调用 | “查一下这串 token 命中多少” | `lmcache/integration/vllm/lmcache_mp_connector.py:1463`、`lmcache/integration/vllm/lmcache_mp_connector.py:1478` |
| L3 | ② → ⑤⑥ | ZMQ 异步 RPC：LOOKUP（**不等回执**），之后 QUERY_PREFETCH_STATUS 轮询 | `IPCCacheServerKey`（worker_id=None） | `lmcache/integration/vllm/vllm_multi_process_adapter.py:874-885` |
| L4 | ① → ③ | **跨进程**：随 vLLM 的 `SchedulerOutput` 一起下发的 `LMCacheMPConnectorMetadata` | 一组 `LoadStoreOp`（方向 STORE 或 RETRIEVE） | `lmcache/integration/vllm/lmcache_mp_metadata.py:666-672` |
| L5 | ③ → ④ → ⑤⑦ | ZMQ 异步 RPC：STORE / RETRIEVE，附带一个 **CUDA event 的 IPC 句柄** | key（带 worker_id）+ block 编号 + event 句柄 | `lmcache/v1/client/worker_transfer.py:719-722`、`lmcache/v1/client/worker_transfer.py:791-798` |
| L6 | ⑦ ↔ vLLM GPU 显存 | **CUDA IPC 直接读写**（服务端的 CUDA 流先等 vLLM 的 event） | KV 数据本身 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894` |
| L7 | ⑥⑦ → ⑧ | 同进程函数调用 | token → 哈希 → `ObjectKey` | `lmcache/v1/multiprocess/engine_context.py:578-622` |
| L8 | ⑥⑦ → ⑨ | 同进程函数调用；**提交写入/释放读锁走 GPU 流回调**（异步） | `reserve_write` / `finish_write` / `submit_prefetch_task` / `finish_read` | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:960`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1009`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1374` |
| L9 | ⑤ → ③④ | ZMQ 回复 + 一个**完成 event 的句柄** | worker 用“RPC 已回 + event 已完成”判断真正结束 | `lmcache/v1/transport/futures.py:260-269` |
| L10 | ③ → vLLM | 函数返回值（`get_finished`） | 已完成的 sending / recving 请求 id | `lmcache/integration/vllm/lmcache_mp_connector.py:1228-1258` |

**要对照着看的两件事**：L3（调度器发 LOOKUP）和 L5（worker 发 RETRIEVE）是**两个不同进程**发出的、对同一批 key 的两次 RPC。前者上读锁，后者用锁、放锁。中间隔着至少一次 vLLM 调度步。锁“谁上谁放”是这套设计最容易看错的地方（§5.4）。

---

## 4. 逐步推演：一个具体例子

### 4.1 设定（全部是为讲解而设的，不是代码常量，除非标了锚点）

- 服务端：`lmcache server --l1-size-gb 20 --eviction-policy LRU`（chunk 用默认 256，`lmcache/v1/multiprocess/config.py:52`；端口默认 5555，`lmcache/v1/multiprocess/config.py:49`）。
- vLLM：Qwen3-8B，TP=1，KV block 大小 16 token（设），开启前缀缓存（APC），`kv_role = kv_both`。
- 设 Qwen3-8B 的 KV 形状为 36 层、8 个 KV 头、head_dim 128、bf16（设，模型公开配置，非本仓库内容）。
- **请求 A**：prompt 1000 token，生成 100 token。
- **请求 B**：prompt 1200 token，其中前 900 token 与 A 完全相同；B 到来时 vLLM 的 APC 已把 A 的块挤掉了（设），所以 vLLM 本地命中 0。

由这些设定推出的数：
- chunk 协商：所需对齐 = lcm(16) = 16，256 是 16 的倍数 → 最终 chunk = 256（规则见 `docs/source/mp/chunk_size_negotiation.rst:12-17`）。
- 每个 chunk 占 vLLM 的 256 / 16 = **16 个 block**（`lmcache/integration/vllm/vllm_multi_process_adapter.py:685`）。
- 每 token 的 KV 字节 = 2(K,V) × 36 × 8 × 128 × 2 B = 147,456 B = 144 KiB；每个 chunk 的 `MemoryObj` ≈ 256 × 144 KiB = **36 MiB**（推断：基于“一个对象装该 object group 所有层”这一布局，`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:84-86`，加上上面设的模型形状）。
- L1 20 GiB ≈ 568 个 chunk；淘汰在用量 80% 时触发、一次淘汰 20%（默认值 `lmcache/v1/distributed/config.py:491`、`lmcache/v1/distributed/config.py:494`）。

### 4.2 启动阶段（只做一次）

1. **调度器进程**建 `LMCacheMPSchedulerAdapter`，向服务端要 chunk 大小（顺便协商）：`lmcache/integration/vllm/vllm_multi_process_adapter.py:659`。若 256 不是 vLLM block 大小的倍数就直接报错：`lmcache/integration/vllm/vllm_multi_process_adapter.py:677-684`。
2. **worker 进程**在 vLLM 分配完 KV 显存后调用 `register_kv_caches`：`lmcache/integration/vllm/lmcache_mp_connector.py:1015`。
   - 包装成可跨进程的句柄 `wrap_kv_caches`：`lmcache/v1/client/worker_transfer.py:610`。
   - 发 `REGISTER_KV_CACHE`：`lmcache/v1/client/worker_transfer.py:615`。
3. **服务端** `register_kv_cache`（装饰器缺省即 SERIALIZED，在 ZMQ 主循环线程上跑：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:534-535`，`lmcache/v1/multiprocess/request_handler.py:45-46`）：导入句柄，建 `cache_context`（之后 STORE/RETRIEVE 直接用它访问 vLLM 显存），并把该 (模型, world_size) 的布局登记到 `LayoutDescRegistry`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:629`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:658`。

> 读者提示：注册之后，**vLLM 的 GPU 显存就“同时属于两个进程”**。后面所有的拷贝，vLLM 进程都不动手，只告诉服务端“拷哪几个 block”。

### 4.3 请求 A：第一次，全部 miss，然后存

**步骤 A1 — 调度器问“能从 LMCache 取多少？”**（vLLM 调 `get_num_new_matched_tokens(A, num_computed_tokens=0)`）
- 为 A 新建 tracker，状态 `PREFETCHING`：`lmcache/integration/vllm/lmcache_mp_metadata.py:165`。
- 计算查找区间：1000 token 向下取整到 chunk → `end = 768`（3 个 chunk）；不足一个 chunk 的尾巴 232 token 不查：`lmcache/integration/vllm/vllm_multi_process_adapter.py:860-863`。
- 组装 `IPCCacheServerKey(worker_id=None, start=0, end=768, token_bytes=1000×4 字节)`，发 LOOKUP，**不等回执**：`lmcache/integration/vllm/vllm_multi_process_adapter.py:865-885`。
- 马上 `check_lookup_result`：LOOKUP 刚发出、回执和状态都还没回来（默认不阻塞等待），所以几乎总是返回 `(None, True)`，意思是“我还要时间，你下一步再问我”：`lmcache/integration/vllm/lmcache_mp_connector.py:1478-1480`。

**步骤 A2 — 服务端处理 LOOKUP**（普通线程池上）
- 打包 token 每个 4 字节（`lmcache/v1/common/token_codec.py:19`），按 256 token = 1024 字节一段做 blake3 滚动哈希：`hash_i = blake3(hash_{i-1} ‖ chunk_i)`，32 字节：`lmcache/v1/common/token_hasher.py:43-53`。得到 `[h0, h1, h2]`：`lmcache/v1/multiprocess/modules/lookup.py:251`。
- 建 Session 并记下 token：`lmcache/v1/multiprocess/modules/lookup.py:300-301`。
- 把 3 个哈希展开成 `ObjectKey`（kv_rank=0、object group 0），提交预取任务：`lmcache/v1/multiprocess/modules/lookup.py:382-393`。
- 没有 L2 → 预取在**当前线程**上同步完成：对 L1 逐个 `reserve_read`，全部 `KEY_NOT_EXIST`：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:718-722`。

**步骤 A3 — 调度器之后再问**
- LOOKUP 回执到了之后，适配器才发 `QUERY_PREFETCH_STATUS`；状态回复到了，下一次被 vLLM 问到时才拿得到结果（所以通常要隔一两个调度步）：`lmcache/integration/vllm/mp_lookup_state.py:25-41`。
- `QUERY_PREFETCH_STATUS` 回来 0 → `get_num_new_matched_tokens` 返回 `(0, False)`：`lmcache/integration/vllm/lmcache_mp_connector.py:1532-1533`。
- vLLM 给 A 分配 ceil(1000/16) = 63 个 block，调 `update_state_after_alloc(A, blocks, 0)`：tracker 记下 63 个 block 编号，状态 `PREFETCHING → READY`：`lmcache/integration/vllm/lmcache_mp_connector.py:1636-1657`。

**步骤 A4 — 调度器生成本步元数据**（`build_connector_meta`）
- `_process_new_requests` 对 A 调 `GetStoreMetadata`：`lmcache/integration/vllm/lmcache_mp_connector.py:2008`。
- 计算（`lmcache/integration/vllm/lmcache_mp_metadata.py:398-428`）：
  - `computed_tokens = max(1000 + max(0,0), 0+1000) = 1000`
  - `allocated_tokens = 63 × 16 = 1008`
  - `min(1000 总 token, 1008, 1000) = 1000`，再按 prompt 长度封顶 = 1000
  - `num_staging = 1000 − 0 = 1000` → `num_chunks = 3`
- 产出 `LoadStoreOp(start=0, end=768, block_ids=[[b0..b47]])`（48 = 3×16 个 block），并把 `num_stored_tokens` 记为 768：`lmcache/integration/vllm/lmcache_mp_metadata.py:456`。`store_scheduled = True`：`lmcache/integration/vllm/lmcache_mp_connector.py:2020`。

**步骤 A5 — worker 前向结束时发 STORE**（`wait_for_save`）
- 在**当前 CUDA 流**上录一个 event（此时 A 的前向计算已入队）：`lmcache/integration/vllm/lmcache_mp_connector.py:1193`，`lmcache/v1/client/worker_transfer.py:648`。
- 组装 key（这次带 `worker_id=0`）：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2571-2574`；导出 event 的 IPC 句柄、发 `STORE`：`lmcache/v1/client/worker_transfer.py:719-722`。
- **worker 不等**，直接返回让 vLLM 继续下一步。

**步骤 A6 — 服务端执行 STORE**（GPU 亲和线程上）
- 用 Session 里的 token 算 `[0,768)` 的哈希（与 A2 算出的是同一条链），得到 3 个 `ObjectKey`：`lmcache/v1/multiprocess/engine_context.py:611-622`。
- 检查 block 数够不够：每组至少 3×16=48 个，不够就整单放弃：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:846-871`。
- 导入 vLLM 的 event，让**服务端自己的 CUDA 流**等它：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894`。
- 向 L1 申请 3 个 36 MiB 的对象（处于“写锁定、对读者不可见”的 staging 状态）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:960-962`。
- 入队 D2H 拷贝内核（GPU block → CPU 固定内存）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:978-986`。
- 在同一流上挂一个回调 `finish_write`：拷完后才把 3 个对象“转正”：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1003-1011`。
- 立即返回 `(完成 event 句柄, True)`。

**步骤 A7 — 拷贝在 GPU 上完成后**
- 驱动线程触发回调 → 排空线程调 `StorageManager.finish_write` → `L1Manager.finish_write`：staging 对象进入 `_objects`，从此对读者可见：`lmcache/v1/distributed/l1_manager/base.py:649`。
- 同时通知监听者（StoreController、淘汰控制器）：`lmcache/v1/distributed/l1_manager/base.py:658`。

**步骤 A8 — worker 轮询完成**
- 每步 `get_finished` 轮询：RPC 回复已到 **且** 服务端的完成 event 已完成，才算完：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2179-2183`，`lmcache/v1/transport/futures.py:415-433`。

**步骤 A9 — 解码阶段**
- 每步都会再调 `GetStoreMetadata`，但封顶是 prompt 长度 1000：`1000 − 768 = 232 < 256` → 不再存：`lmcache/integration/vllm/lmcache_mp_metadata.py:423-428`。
- 生成的 100 个 token 不存（默认）。

**步骤 A10 — A 结束**
- `request_finished`：因为 `store_scheduled=True`，返回 `delay_free=True`，告诉 vLLM“先别回收这 63 个 block，等我说好”：`lmcache/integration/vllm/lmcache_mp_connector.py:1794`、`lmcache/integration/vllm/lmcache_mp_connector.py:1824`。
- 发 `END_SESSION`：`lmcache/integration/vllm/lmcache_mp_connector.py:1821`。服务端删 Session，并把这次涉及的 key 都 “touch” 一下（刷新 LRU）：`lmcache/v1/multiprocess/modules/lookup.py:623`、`lmcache/v1/multiprocess/modules/lookup.py:646`。
- worker 在 `get_finished` 里报告 A 为 finished_sending 后，vLLM 才回收 A 的 block：`lmcache/integration/vllm/lmcache_mp_connector.py:1252-1256`。

**A 走完之后的状态**：L1 里有 3 个对象 `{h0, h1, h2}`（各 36 MiB，共 108 MiB），状态 ready、无锁。

### 4.4 请求 B：命中前 768 token，取回复用

**步骤 B1 — 调度器 LOOKUP**
- 1200 token → `end = 1024`（4 个 chunk）。
- 服务端算出 `[h0', h1', h2', h3']`。B 前 900 token 与 A 相同，所以前 3 个 chunk（0–767）的内容完全一样，滚动哈希也一样：`h0'=h0, h1'=h1, h2'=h2`。第 4 个 chunk（768–1023）里 768–899 与 A 相同、900 之后不同，而且 A 根本没存过第 4 块 → `h3'` 不存在。
- 预取：对 4 个 key 逐个 `reserve_read`，前 3 个成功并**各上 1 把读锁**（TP=1 → 读者数 1）：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1274-1278`，`lmcache/v1/distributed/l1_manager/base.py:328-337`。
- 按“前缀”策略算命中：3 个 chunk → 768 token：`lmcache/v1/multiprocess/modules/lookup.py:784`、`lmcache/v1/multiprocess/modules/lookup.py:844`。
- Session 记下“这次锁了前 3 块”：`lmcache/v1/multiprocess/modules/lookup.py:800-804`。

**步骤 B2 — 调度器拿到 768**
- `num_vllm_hit_tokens = 0`，`num_lmcache_hit_tokens = 768`，`num_stored_tokens = 768`（这 3 块不用再存）：`lmcache/integration/vllm/lmcache_mp_connector.py:1491-1543`。
- `need_to_load = 768 − 0 = 768` → 返回 `(768, True)`。第二个值 `True` 的意思是“**异步加载**，加载期间别算这个请求”：`lmcache/integration/vllm/lmcache_mp_connector.py:1398-1400`、`lmcache/integration/vllm/lmcache_mp_connector.py:1575`。

**步骤 B3 — vLLM 分配 block**
- vLLM 为 B 分配 block 并调 `update_state_after_alloc(B, blocks, 768)`。
- `needs_retrieve()` 为真（768 > 0），状态 `PREFETCHING → WAITING_FOR_LOAD`：`lmcache/integration/vllm/lmcache_mp_connector.py:1648-1655`。
- 释放“vLLM 自己已有的那部分”的读锁：`free_end = num_vllm_hit_tokens = 0` → 不需要释放：`lmcache/integration/vllm/lmcache_mp_connector.py:1669-1690`。

**步骤 B4 — 调度器下 RETRIEVE 工单**
- `_process_retrieve_requests`：`start = 0`，`end = 768`，`skip_first_n_tokens = 0`，block = B 的前 48 个；然后状态置 `READY`：`lmcache/integration/vllm/lmcache_mp_metadata.py:618-644`，`lmcache/integration/vllm/lmcache_mp_connector.py:1980-1987`。

**步骤 B5 — worker 发 RETRIEVE**（`start_load_kv`，在前向之前）
- 录 event、发 `RETRIEVE`：`lmcache/integration/vllm/lmcache_mp_connector.py:1112-1120`，`lmcache/v1/client/worker_transfer.py:791-798`。
- 这一步 B **不参与前向**（vLLM 把它挂在等远端 KV 的状态里——这是 vLLM 的行为，本仓库注释称之为 `WAITING_FOR_REMOTE_KVS`：`lmcache/integration/vllm/lmcache_mp_connector.py:1414`）。

**步骤 B6 — 服务端执行 RETRIEVE**（GPU 亲和线程上）
- 重新算出 3 个 `ObjectKey`；**不再上锁**，直接读已锁住的对象 `unsafe_read`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1330-1336`，`lmcache/v1/distributed/storage_manager.py:715`。
- 等 vLLM 的 event、入队 H2D 拷贝（CPU → B 的 48 个 GPU block）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1269-1272`、`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1340-1349`。
- 在流上挂 `finish_read_by_owner` 回调：拷完后释放 3 把读锁：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1371-1377`。
- 返回完成 event 句柄。

**步骤 B7 — 加载完成，B 开始算**
- worker 的 `get_finished` 报告 B 为 finished_recving：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2193-2197`。
- vLLM 之后调度 B，`num_computed_tokens = 768`，只需计算剩下 1200 − 768 = **432 个 token**（而不是 1200）。省掉了 64% 的 prefill。

**步骤 B8 — B 自己的新块也会被存**
- `GetStoreMetadata`：`computed = 432 + max(0, 768) = 1200`，`allocated = 75 × 16 = 1200`，`num_staging = 1200 − 768 = 432` → 1 个 chunk → STORE `[768, 1024)`。
- 服务端 `reserve_write` 只申请 `h3'` 这一个新对象。前 3 块已存在不会重复写（即使重复发，L1 也会以 `KEY_NOT_WRITABLE` 拒绝：`lmcache/v1/distributed/l1_manager/base.py:527-529`）。

### 4.5 推演里出现过的“状态机”，汇总一下

调度侧 tracker 的状态：`lmcache/integration/vllm/lmcache_mp_metadata.py:53-66`

```
PREFETCHING --update_state_after_alloc--> WAITING_FOR_LOAD (需要取)
PREFETCHING --update_state_after_alloc--> READY            (不需要取)
WAITING_FOR_LOAD --build_connector_meta 下发 RETRIEVE--> READY
READY --异步加载失败--> BYPASS_LMCACHE --下一次 alloc--> READY
```

L1 对象的状态：`lmcache/v1/distributed/l1_manager/base.py:142-191`

```
None --reserve_write--> write_locked staging（读者不可见）
staging --finish_write--> ready
ready --reserve_read--> read_locked(count=n) --finish_read 减到 0--> ready
staging --写锁 TTL 过期--> 可被淘汰
```

---

## 5. 各部件的说明卡素材（怎么做 / 为什么 / 容易看错）

### 5.1 ① 调度侧连接器 + ② 调度侧适配器

**干什么**
- `get_num_new_matched_tokens`：返回“还能从外部 KV 拿多少 token”。返回 `None` 表示还没查完，vLLM 下一步会再问：`lmcache/integration/vllm/lmcache_mp_connector.py:1395-1397`。
- `update_state_after_alloc`：记录 block 编号，推进状态，释放用不上的锁：`lmcache/integration/vllm/lmcache_mp_connector.py:1600`。
- `build_connector_meta`：每步把 RETRIEVE / STORE 工单打包下发：`lmcache/integration/vllm/lmcache_mp_connector.py:1711-1713`。
- `request_finished`：决定要不要让 vLLM 延迟回收 block，并结束 Session：`lmcache/integration/vllm/lmcache_mp_connector.py:1754`。

**为什么 LOOKUP 不等回执、状态也不阻塞地查？**
- 【注释】“Do not wait for the acknowledgement here: that is a full server round trip on the scheduler thread.”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:874-875`）
- 【提交】这条行为来自 `ba8acd84`，标题 “defer LOOKUP acknowledgement off the scheduler thread”（commit `ba8acd84`）；之后 `588c5366` 又把状态查询也改为不阻塞，标题 “Poll lookup status without blocking the scheduler”（commit `588c5366`）。默认值 `nonblocking_lookup_status = True`：`lmcache/integration/vllm/vllm_multi_process_adapter.py:129`。
- 两个提交的说明只有标题，没有更多原因。

**为什么整段 prompt 都命中时要“少报一个 token”？**
- 【注释】“Without this, num_computed_tokens would equal request.num_tokens,”（`lmcache/integration/vllm/lmcache_mp_connector.py:1556`），后文说会触发调度器的 `assert num_new_tokens > 0`：`lmcache/integration/vllm/lmcache_mp_connector.py:1555-1558`。
- 【提交】“handle full-prompt-hit in MP connector to avoid scheduler assert failure”（commit `12aaf1dd`）。
- 直观理解【推断】：vLLM 至少要算一个 token 才能出第一个输出 token 的 logits；依据只是上面那条 assert 的存在。

**为什么只存 prompt 的整块，不存生成的 token？**
- 【提交】“Cap MP stores at the request's prompt length unless lmcache.mp.save_decode_cache is enabled.”（commit `7ba466fe`）——这句在提交说明里跨了两行，此处按原文连写。
- 【测试】“Warm prompts must not write new chunks merely because decode fills one.”（`tests/v1/test_mp_connector_mm_keys.py:443`）
- 【推断】生成的 token 很少被别的请求原样复用，存它们只占 L1、挤掉更有用的 prompt 块。仓库里没找到这句理由的原文。

**为什么 `computed_tokens` 取 max，而不是相加？**
- 【注释】“Both values represent a prefix of tokens whose KV data is already available (either from vLLM APC or from LMCache), so they must NOT be summed (that would double-count the overlapping prefix).”（`lmcache/integration/vllm/lmcache_mp_metadata.py:380-382`）
- 【测试】“APC hit 112 floors to 64; chunk [192, 256) must still be stored.”（`tests/v1/test_mp_tracker_store_bound.py:61`）

**为什么 `request_finished` 有时返回 True（延迟回收 block）？**
- 因为 STORE 是异步的：服务端可能还在读这些 GPU block。【注释】“Keep the blocks only while a store may still read them.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1791`）
- 【提交】只在真的发过 STORE 时才延迟：“Delay frees only for requests with a scheduled store”（commit `752e01fd`）。
- 反过来，`get_finished` 只能报告“返回过 True 的”请求：【注释】“asserts on a finished_sending report for a request it freed.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1253`）

**容易看错的地方**
- **“lookup 只是查询”是错的**：LOOKUP 会在服务端**上读锁**。【注释】“LMCache, which will essentially 'lock' the KV cache chunks in the LMCache”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:843`）。所以后面凡是不取的块都必须显式放锁（`free_lookup_locks`），否则要等 TTL 才放。
- `update_state_after_alloc` 可能对同一请求调两次，`blocks` 是**全部** block 而不是新增的，所以只追加超出已记录数量的部分：`lmcache/integration/vllm/lmcache_mp_connector.py:1617-1638`。
- 一个请求的 key 里带的是**全部 token**（不是只有要查的那段），因为服务端要从头算滚动哈希：`lmcache/integration/vllm/lmcache_mp_metadata.py:289`。为了不让“每步重打包整串 token”拖慢调度器，打包结果是增量增长的：【注释】“A request emits one op per scheduler step, so repacking the whole sequence each time would put the context length back on the scheduler's critical path.”（`lmcache/integration/vllm/lmcache_mp_metadata.py:134-136`）
- vLLM 自己的 APC 命中和 LMCache 命中会重叠：取回时只从 `num_vllm_hit_tokens` 所在的 chunk 开始，且用 `skip_first_n_tokens` 跳过 vLLM 已有的那几个 token（见 §5.6）。

**里面值得单独研究的机制（入口）**
- 查找状态机 `LookupState`（多服务器、超时、回调驱动）：`lmcache/integration/vllm/mp_lookup_state.py:69`。
- APC 部分命中 / “APC 缩水后重新整段查找”的逻辑：`lmcache/integration/vllm/lmcache_mp_connector.py:1497-1530`。
- 抢占（preemption）处理和 `BYPASS_LMCACHE` 失败回退：`lmcache/integration/vllm/lmcache_mp_connector.py:1415-1445`。

### 5.2 ③ worker 侧适配器 + ④ CUDA IPC 传输上下文

**干什么**
- `start_load_kv`（前向之前）：收集本步所有 RETRIEVE 工单，录一个 event，批量提交：`lmcache/integration/vllm/lmcache_mp_connector.py:1077-1120`。
- `wait_for_save`（前向之后）：收集所有 STORE 工单，录一个 event，批量提交：`lmcache/integration/vllm/lmcache_mp_connector.py:1165-1201`。
- `get_finished`：轮询所有 future，报告完成：`lmcache/integration/vllm/vllm_multi_process_adapter.py:2113`。

**为什么要传一个 CUDA event？**
- 拷贝在**服务端进程的 CUDA 流**上做，而数据由 **vLLM 进程的 CUDA 流**写出。两个进程的流之间没有天然顺序，所以 vLLM 录一个 event 导出给服务端，服务端的流先 `wait_event` 再拷：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894`。
- 【注释】参数说明写的是 “Producer event that orders reads of the engine KV cache.”（`lmcache/v1/client/worker_transfer.py:702`）
- 好处【推断】：vLLM 一侧不需要 `synchronize()`，只是“录一个 event + 发一条消息”，GPU 上的等待在服务端流里完成，不卡推理线程。

**为什么结果是“设备感知 future”？**
- 服务端回复的不是“拷完了”，而是“拷贝已入队，这是完成 event”。worker 必须同时等 RPC 回复和这个 event。【注释】“The `query`, `wait`, and `result` methods pend on both the original future and the device event”（`lmcache/v1/transport/futures.py:263-264`）

**容易看错的地方（名不副实）**
- `wait_for_save` 名字里有 wait，但它**不等**：只是提交 STORE 后立即返回。它的文档串是 vLLM 接口的原文 “This prevents overwrites of paged KV buffer before saving done.”（`lmcache/integration/vllm/lmcache_mp_connector.py:1171`）——在本实现里，真正防止“块被覆盖”的是 §5.1 的延迟回收，以及抢占时的 `flush_inflight_stores`（见下）。
- `wait_for_layer_load` 和 `save_kv_layer` 在主路上都是空的（直接 return），没有逐层流水：`lmcache/integration/vllm/lmcache_mp_connector.py:1122-1133`、`lmcache/integration/vllm/lmcache_mp_connector.py:1135-1163`。注释说它“will be useful for layer-by-layer pipelining”（`lmcache/integration/vllm/lmcache_mp_connector.py:1128`），但当前没实现。
- 抢占时的坑：被抢占请求的 block 会被 vLLM 立刻回收、同一步就分给别人；如果服务端还在读，就会把**别人的 KV 存成这个请求的 key**。【注释】“When the scheduler preempts a request it frees those blocks immediately and may hand them to another request in the same step,”（`lmcache/v1/client/worker_transfer.py:814-815`）。所以调度器发现有抢占时会让 worker 在前向前 `flush_inflight_stores`：`lmcache/integration/vllm/lmcache_mp_connector.py:1707`，`lmcache/integration/vllm/vllm_multi_process_adapter.py:2441`。

**故障时怎么收场**
- 服务端不健康时：STORE 直接丢（缓存少一点而已），RETRIEVE 把 block 标成出错交给 vLLM 重算：`lmcache/integration/vllm/vllm_multi_process_adapter.py:1903-1915`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1967-1971`。
- 【注释】“A failed request ends with a false result, so losing the server costs the cache and not the engine.”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:376-377`）

**入口**
- KV 显存怎么包装成跨进程句柄（CUDA IPC / VMM / fd 通道）：`lmcache/v1/client/worker_transfer.py:610`；服务端导入：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1086`。
- 心跳与服务端重启后的重新注册：`lmcache/integration/vllm/vllm_multi_process_adapter.py:1782`。

### 5.3 ⑤ ZMQ 请求服务器与线程模型

**干什么**
- 一个 ROUTER socket 收所有客户端的请求：`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:192`。
- 每个处理函数用装饰器声明“怎么跑”：`SERIALIZED`（主循环上直接跑）或 `BLOCKING`（丢到线程池）：`lmcache/v1/multiprocess/request_handler.py:18-23`。
- STORE / RETRIEVE 声明了 `requires_client_affinity=True`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:714-719`。它们进 GPU 亲和池，按客户端身份路由：`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:299`。

**为什么要“亲和”线程池？**
- 【注释】“This is used for GPU-bound request handlers (STORE / RETRIEVE) so that all operations for a given vLLM instance land on one thread, eliminating the need for per-instance locks on the shared temporary GPU buffer.”（`lmcache/v1/multiprocess/affinity_pool.py:8-10`）
- 【提交】引入于 “support worker-affinity in the MQ thread pool”（commit `3137ce0b`）；后来修过分配方式：“Route affinity thread pool on dense worker rank instead of hashed identity”（commit `d1b63716`）——现在是“第一次见到的客户端绑定到下一个空闲槽位”，槽位用完才共享：`lmcache/v1/multiprocess/affinity_pool.py:131-142`。
- 【测试】“Tasks with the same affinity_key always run on the same thread.”（`tests/v1/multiprocess/test_affinity_pool.py:23`）
- 额外的效果：同一个 worker 的 STORE 和 RETRIEVE 在服务端按到达顺序串行执行。【注释】“Within each worker, tasks execute sequentially in FIFO order.”（`lmcache/v1/multiprocess/affinity_pool.py:6`）

**为什么回复要经过队列 + eventfd，而不是线程池里直接发？**
- 【注释】“because blocking handler callbacks run on ThreadPoolExecutor threads, and zmq sockets are not thread-safe.”（`lmcache/v1/multiprocess/transport/zmq_impl/mq.py:197-198`）

**为什么独立进程（而不是嵌在 vLLM 里）？**
- 【文档】“**Process isolation** -- LMCache and vLLM run in separate processes”（`docs/source/mp/index.rst:23`）；“**No GIL contention or Python overhead on the inference path**”（`docs/source/mp/index.rst:25`）；“**Shared caching across pods**”（`docs/source/mp/index.rst:28`）。

**运维可调**
- `--max-gpu-workers` / `--max-cpu-workers`（默认都是 1）：`lmcache/v1/multiprocess/config.py:59`、`lmcache/v1/multiprocess/config.py:63`。亲和池线程数少于客户端数时会打警告，多个 worker 会被串行化：`lmcache/v1/multiprocess/affinity_pool.py:157-166`。

**入口**
- gRPC 传输（另一套实现，同一套处理函数）：`lmcache/v1/multiprocess/transport/server_factory.py`（文件级入口）。
- 模块发现与拼装（`DiscoverableModule`、依赖排序）：`lmcache/v1/multiprocess/server.py:214`。

### 5.4 ⑥ 查找模块（LOOKUP / QUERY / FREE / END_SESSION）

**干什么**
- `lookup`：算哈希 → 建 Session → （可选的加载策略判断）→ 提交预取：`lmcache/v1/multiprocess/modules/lookup.py:205-417`。
- `query_prefetch_status`：拿结果，**只交付一次**：`lmcache/v1/multiprocess/modules/lookup.py:420-449`。
- `free_lookup_locks`：释放用不上的读锁：`lmcache/v1/multiprocess/modules/lookup.py:486`。
- `end_session`：删 Session，并 touch 涉及的 key：`lmcache/v1/multiprocess/modules/lookup.py:611-646`。

**锁的生命周期（最容易看错，建议单独一张卡）**
1. 上锁：调度器的 LOOKUP（服务端预取里 `reserve_read`）。
2. 用锁：worker 的 RETRIEVE（`unsafe_read`，不再加锁）。
3. 放锁，三条路之一：
   - RETRIEVE 拷完后的流回调 `finish_read_by_owner`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1371-1377`；
   - vLLM 自己已有、不取的那段：调度器 `free_lookup_locks`：`lmcache/integration/vllm/lmcache_mp_connector.py:1678`；
   - 谁都没放（客户端崩了）：读锁 TTL 到期（默认 300 秒）：`lmcache/v1/distributed/config.py:432`。

**为什么要有 FREE_LOOKUP_LOCKS？**
- 【注释】“Calling this prevents those chunks from holding read locks until TTL expiry.”（`lmcache/integration/vllm/vllm_multi_process_adapter.py:1025-1026`）
- 【提交】引入于 “add free_locks api to MP mode”（commit `36c3e8a6`），说明里只有 “add free_blocks api to explicitly free locked chunks”（commit `36c3e8a6`）。

**为什么锁带 TTL？**
- 【文档】“Each object has two ``TTLLock`` instances (read and write) with configurable timeouts to prevent deadlocks from crashed clients.”（`docs/source/mp/index.rst:370-371`）

**为什么锁计数是“读者数”而不是 1？**
- TP>1 时，每个 TP worker 各自 RETRIEVE、各自放一次锁，所以 LOOKUP 一次要上 n 把。【注释】“one per worker that consumes a read lock for the same key (e.g. MLA models with TP > 1).”（`lmcache/v1/distributed/l1_manager/base.py:307-309`）
- 主路 TP=1，所以是 1。

**为什么 query 结果“只交付一次”？**
- 【注释】“This is the only path that moves lookup state into the session, so ``query_prefetch_status`` and ``free_lookup_locks`` cannot disagree about what a lookup locked.”（`lmcache/v1/multiprocess/modules/lookup.py:754-756`）

**入口**
- KV 加载策略（`--kv-load-policy`，可插拔地决定“这个请求要不要用缓存”）：`lmcache/v1/multiprocess/modules/lookup.py:325`。
- 未满块（unfull chunk）的加锁：`lmcache/v1/multiprocess/modules/lookup.py:846`。

### 5.5 ⑧ Session + TokenHasher：token 怎么变成 key

**干什么**
- 客户端只发**原始 token**（每个打包成 4 字节大端 uint32）：`lmcache/v1/common/custom_types.py:72`、`lmcache/v1/common/token_codec.py:19`。
- 服务端为每个请求建 Session，增量地算滚动哈希，并缓存：`lmcache/v1/multiprocess/session.py:172-196`。
- 哈希 → `ObjectKey`：每个 chunk 哈希 × 每个 kv_rank × 每个 object group 一个 key：`lmcache/v1/distributed/api.py:853-912`。

**为什么是“滚动”（链式）哈希？**
- 每块的哈希把前一块的哈希也喂进去：`lmcache/v1/common/token_hasher.py:46-52`。所以第 i 块的 key 隐含了“前面所有 token 都相同”——这正是 KV 可复用的前提（KV 依赖整个前缀）。【推断】，依据是 KV cache 的因果性和哈希构造本身。
- 因此只能做“前缀命中”，中间断了后面全部不算：预取默认策略就是 `"prefix"`：`lmcache/v1/distributed/api.py:743`。

**为什么让服务端算哈希、客户端发原始 token？**
- 历史：MP 模式早期用 vLLM 的 block 哈希（“hash mode”）；`1419a115` 引入 token 模式（标题 “Introducing Token Base Connector for MP mode”，commit `1419a115`），紧接着 `b022405c` 删掉 hash mode（标题 “Clear Hash Mode API for MP mode”，commit `b022405c`）。
- 两个提交都没写原因。见 §9“没查到为什么”。

**为什么 Session 要加锁？**
- 【注释】“Thread-safe: all public methods are protected by an internal lock to allow concurrent access from multiple TP worker threads.”（`lmcache/v1/multiprocess/session.py:39-40`）
- 【提交】“add thread safety to Session for concurrent TP worker access”（commit `1b16510b`）。

**容易看错**
- `request_id` 在 key 里但**不参与相等比较**：它只用来找 Session。【注释】“The request_id field is for session tracking and is NOT included in equality/hash comparisons”（`lmcache/v1/common/custom_types.py:63-64`）。
- 名不副实：`TokenHasher` 的文档串仍写着 “(hash mode)”（`lmcache/v1/common/token_hasher.py:82`），但 hash mode 早已删除。
- `cache_salt` 参与身份：不同用户/租户的相同 prompt 不会互相命中：`lmcache/v1/common/custom_types.py:79-88`。

**入口**
- 多模态（图片占位 token 用多模态哈希替换后再算 key）：`lmcache/integration/vllm/lmcache_mp_metadata.py:266-287`。
- Session 过期清理（默认 600 秒）：`lmcache/v1/multiprocess/session.py:227`。

### 5.6 ⑦ 传输模块：STORE / RETRIEVE 的服务端

**STORE 的关键动作顺序**（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:745-1084`）
1. 找注册上下文；找不到就返回空 event + False（不留悬空的 future）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:798-809`。
2. 算 `ObjectKey`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:817-819`。
3. block 数不够 → 整单放弃：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:846-871`。
4. 让自己的流等 vLLM 的 event：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:891-894`。
5. `reserve_write` 申请 L1 对象：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:960-962`。
6. 入队 D2H 拷贝：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:978-986`。
7. 全成功 → 流回调 `finish_write`；否则 → 流回调 `abort_write`：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1003-1018`。

**为什么提交写入要挂在 CUDA 流回调上，而不是拷完再在 Python 里调？**
- 必须等 GPU 真的拷完才能让对象对读者可见，否则别人会读到半截数据【推断】，依据是 L1 状态机“staging 对读者不可见、finish_write 才转正”的设计：`lmcache/v1/distributed/l1_manager/base.py:151-154`。
- 为什么经过 C++ 而不是直接 Python 回调：【注释】“Route finish_write / finish_read_prefetched through a C++ host callback so the driver thread doesn't acquire the GIL.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:269-270`）
- 【注释】“Extends PR #2952's AB-BA fix (GIL x driver lock) to Server.store/retrieve.”（`lmcache/v1/multiprocess/native_completion.py:27`）
- 【提交】“Fix store/retrieve deadlock by routing completion callbacks through C++ host callback”（commit `50e45434`）。即：Python 线程持 GIL 等 CUDA 驱动锁，驱动线程持驱动锁等 GIL → 死锁。

**为什么“要么全存，要么全不存”？**
- 【注释】“Fail closed: commit the reserved objects only when every chunk copied successfully; otherwise the whole store is skipped.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1003-1004`）
- block 数不足的检查：【注释】“A short list (e.g. a caller/protocol bug) would otherwise drive the transfer kernel to read out-of-bounds GPU memory,”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:847-848`）

**RETRIEVE 为什么要 `skip_first_n_tokens`？**
- 场景：vLLM APC 已有前 k 个 token 的 block，这些 block 可能正被**其他请求**同时读。如果 LMCache 的拷贝把它们重写一遍，就是跨流数据竞争。
- 【注释】“The server must skip writing to these positions to avoid a cross-stream data race: the retrieve writes on the LMCache CUDA stream while concurrent requests may read these APC-shared blocks on the vLLM stream.”（`lmcache/integration/vllm/lmcache_mp_metadata.py:640-643`）
- 【提交】“Avoid overwriting APC overlap”（commit `38dddf92`）。
- 主路例子里 vLLM 命中 0，所以 skip = 0。

**容易看错**
- STORE 的 `batch_size` 必须是 1：【注释】“NOTE: batch_size must stay 1 for store.”（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:977`）。原因没写（见 §9）。
- 服务端日志 “Stored %d of %d requested tokens” 打印的是本次 STORE 实际提交的 token 数：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:1055-1061`。

**入口**
- GPU 拷贝内核与布局转换（`transfer_object_group`、`downsample_and_stage_block_ids`）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:978`。
- 混合模型（滑动窗口、Mamba 状态）的 object group 拆分：`docs/design/v1/distributed/storage_manager.md:12-19`。
- 实例存活/回收（worker 崩溃后清理注册）：`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:420`。

### 5.7 ⑨ StorageManager / PrefetchController / L1Manager

**干什么**
- `StorageManager` 是门面，内部拼装 L1 管理器、淘汰控制器、StoreController、PrefetchController：`lmcache/v1/distributed/storage_manager.py:148-271`。
- 写入两阶段：`reserve_write` → `finish_write`：`lmcache/v1/distributed/storage_manager.py:289`、`lmcache/v1/distributed/storage_manager.py:353`。
- 读取：`submit_prefetch_task`（查 + 锁）→ `unsafe_read`（读）→ `finish_read`（放锁）：`lmcache/v1/distributed/storage_manager.py:565`、`lmcache/v1/distributed/storage_manager.py:710`、`lmcache/v1/distributed/storage_manager.py:1424`。
- L1 的内存是 CPU 固定内存（pinned DRAM），默认懒增长：`lmcache/v1/distributed/memory_manager/l1_memory_manager.py:2`，`lmcache/v1/distributed/config.py:951-955`。

**为什么写入要分两阶段（staging）？**
- 【注释】“Staging objects are kept apart from the resident objects: readers of the key do not see them, and writers with different tags may stage the same key at the same time.”（`lmcache/v1/distributed/l1_manager/base.py:151-154`）
- 数据从 GPU 拷到这块内存需要时间，拷的过程中不能被读【推断】。

**为什么没 L2 时预取是同步的？**
- 【文档】“A spec that allows no L2 adapter, or one submitted with no L2 adapter attached, is served on the calling thread and its result is available when the call returns.”（`docs/design/v1/distributed/storage_manager.md:50-52`）
- 代码：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:718-723`。
- 所以主路下，第一次 QUERY 通常就能拿到结果。

**为什么 key 要按 (object group × kv rank) 成“行”？**
- 【文档】“A hybrid model stores each chunk of a request as several objects: one per *object group* (full attention, sliding window, recurrent state, ...) and one per *kv rank* (tensor-parallel shard).”（`docs/design/v1/distributed/storage_manager.md:12-14`）
- 主路只有 1 行（1 个 group × 1 个 rank）。

**淘汰**
- 后台线程在用量超过 80% 时淘汰 20%（`lmcache/v1/distributed/config.py:491-495`），只挑没被读锁锁住的对象：`lmcache/v1/distributed/storage_controllers/eviction_controller.py:186`，`lmcache/v1/distributed/l1_manager/base.py:969-984`。
- 读锁就是“正在被某个请求用着，不许淘汰”的标记；这也是为什么 lookup 一定要上锁：查到和真正取之间隔着几个调度步，中间不能被淘汰【推断】。

**入口（读者可能会点进去的）**
- L1 状态机全图：`lmcache/v1/distributed/l1_manager/base.py:142-191`。
- 预取控制器的三阶段（锁 L1 → 问 L2 → 加载）：`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1-15`。
- 淘汰策略（LRU / ARC / IsolatedLRU）：`lmcache/v1/distributed/storage_controllers/eviction_controller.py:88`。
- 写入放置策略（多 L1）：`lmcache/v1/distributed/storage_manager.py:314`。

---

## 6. 不变量与正确性保证（汇总）

1. **`num_stored_tokens` 永远是 chunk 大小的整数倍**：【注释】“NOTE: the invariant here is that `num_stored_tokens` should always be a multiple of `lmcache_tokens_per_chunk`”（`lmcache/integration/vllm/lmcache_mp_metadata.py:374-375`）。
2. **chunk 边界必须对齐 vLLM 每个 KV group 的 block 边界**，否则按 block 切片会错位：启动时检查 `lmcache/integration/vllm/lmcache_mp_connector.py:954-967`。【文档】“This keeps chunk boundaries aligned with every vLLM KV cache group”（`docs/source/mp/chunk_size_negotiation.rst:5-6`）。
3. **对读者可见的对象一定是完整拷完的**：staging + 流回调 `finish_write`（§5.6、§5.7）。
4. **正在被用的对象不会被淘汰**：读锁 + `is_key_evictable`。
5. **被服务端读着的 GPU block 不会被 vLLM 回收**：`delay_free` + 抢占时 `flush_inflight_stores`。
6. **一个服务端只有一个最终 chunk 大小**：【文档】“One MP server has one immutable final chunk size.”（`docs/source/mp/chunk_size_negotiation.rst:33`）

---

## 7. 故障模型（会坏在哪、怎么收场）

| 坏在哪 | 收场 | 锚点 |
|---|---|---|
| 服务端挂了 / 超时 | 适配器标为不健康：lookup 返回 0、STORE 丢弃、RETRIEVE 报 block 出错让 vLLM 重算；心跳恢复后重新协商 chunk、重新注册 | `lmcache/integration/vllm/vllm_multi_process_adapter.py:915`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1903`、`lmcache/integration/vllm/vllm_multi_process_adapter.py:1967`；`docs/source/mp/chunk_size_negotiation.rst:52-66` |
| 异步加载失败 | tracker 进 `BYPASS_LMCACHE`，这次请求本地重算 | `lmcache/integration/vllm/lmcache_mp_connector.py:1415-1445` |
| 客户端崩了，锁没放 | 读锁 300 秒、写锁 600 秒后过期 | `lmcache/v1/distributed/config.py:429-433` |
| L1 满了 | `reserve_write` 返回 OUT_OF_MEMORY，那几个 chunk 不存；淘汰线程后台腾空间 | `lmcache/v1/distributed/l1_manager/base.py:555-564` |
| 服务端重启后 worker 还没重新注册就发 STORE | 返回空 event + False，不悬挂 | `lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:798-809` |

设计取向一句话：**宁可少缓存、多重算，也不让推理引擎崩或读到错数据**。

---

## 8. 旁路与其它模式（只点名，标出从哪岔出去）

| 模式 | 从主路哪里岔出去 | 入口 |
|---|---|---|
| **L2 存储**（磁盘、S3、Redis/Valkey、NIXL、Mooncake…） | 写：`L1Manager.finish_write` 通知 `StoreController`，后台异步推到 L2。读：`PrefetchController` 在 L1 miss 时向 L2 查并加载回 L1 | `lmcache/v1/distributed/storage_controllers/store_controller.py:127`，`lmcache/v1/distributed/storage_controllers/prefetch_controller.py:1234` |
| **engine_driven 传输**（非 CUDA 设备） | worker 端自己 gather/scatter，PREPARE/COMMIT 两步走，代替 STORE/RETRIEVE | `lmcache/v1/client/worker_transfer.py:837`，`lmcache/v1/multiprocess/modules/engine_driven_transfer.py` |
| **gRPC 传输** | 替换 ⑤，处理函数不变 | `lmcache/v1/multiprocess/transport/server_factory.py` |
| **多服务器**（每节点一个） | 调度器向每台发 LOOKUP，取最小命中；worker 只连本节点 | `lmcache/integration/vllm/mp_lookup_state.py:366-401`，`lmcache/integration/vllm/lmcache_mp_connector.py:866-873` |
| **eager prefetch** | 请求一进等待队列就发 LOOKUP | `lmcache/integration/vllm/lmcache_mp_connector.py:1577` |
| **lazy offload** | STORE 不立即发，进缓冲按策略释放 | `lmcache/integration/vllm/lazy_offload_manager.py` |
| **未满块（unfull chunk）** | 允许存/取不满 256 的尾块 | `lmcache/integration/vllm/lmcache_mp_metadata.py:462` |
| **P2P 跨节点共享 CPU 内存** | 另一个服务端作为 L2 来源 | `lmcache/v1/multiprocess/modules/p2p_controller.py` |
| **CacheBlend**（非前缀复用） | `--engine-type blend`，替换查找与取回 | `docs/source/mp/index.rst:126-137` |

---

## 9. 没查到为什么（如实列出）

1. **为什么从“vLLM block 哈希”改成“服务端用原始 token 算哈希”**。提交 `1419a115`、`b022405c` 只有标题。【推断】可能的理由：服务端统一哈希算法与 chunk 大小（chunk 大小可协商、与 vLLM block 不同）、引擎无关（SGLang 等也能接）、多模态 token 替换可在客户端完成——但这些都没有原文依据。
2. **为什么 STORE 的 `batch_size` 必须是 1**（`lmcache/v1/multiprocess/modules/lmcache_driven_transfer.py:977`）。注释只说必须，没说为什么。
3. **为什么默认 chunk 是 256**（`lmcache/v1/multiprocess/config.py:52`）。没找到说明。
4. **为什么读锁 TTL 默认 300 秒、写锁 600 秒**（`lmcache/v1/distributed/config.py:429-433`）。只知道 TTL 是为了防客户端崩溃死锁，具体数值没理由。
5. **为什么 GPU / CPU 线程池默认都只有 1 个线程**（`lmcache/v1/multiprocess/config.py:59-63`）。没找到说明。
6. **为什么 `wait_for_layer_load` / `save_kv_layer` 不做逐层流水**。只有一句“will be useful”，没有说明为何没实现。
7. **为什么默认 blake3**（`lmcache/v1/multiprocess/config.py:514`）。没找到选型说明。
8. **InProcess 实现为什么被移出本仓库**。提交 `5b9db86f` 只有标题；MP 的好处在文档里有（§5.3），但“为什么删掉 InProcess”本身没写。

---

## 10. 可改进处【观点】

- 【观点】`wait_for_save` 不等待，名字容易误导；读者需要额外知道“真正的保护在 delay_free 和 flush”。
- 【观点】查找=上锁 这一隐式副作用分散在调度器、worker、服务端三处，锁的“上/用/放”没有一个集中的地方描述；新读者很容易漏放锁。
- 【观点】`TokenHasher` 文档串里残留 “hash mode” 字样，和当前实现不一致。
- 【观点】默认单线程的 GPU 亲和池在多 vLLM 实例共享一个服务端时会把它们串行化（代码会打警告，但默认值本身偏保守）。

---

## 11. 给讲解员的读者层提示

- **最容易困惑**：同一批 key 被“三方”碰：调度器上锁（LOOKUP）、worker 触发用锁（RETRIEVE）、服务端 GPU 流回调放锁。建议把“锁的生命周期”和“请求 B 的时间线”对照着看。
- **时间上不连续**：请求 B 的 LOOKUP 和 RETRIEVE 至少隔一个调度步；B 的前向又比 RETRIEVE 晚至少一步。A 的 STORE 返回时，数据其实还没拷完。
- **效果在别处**：`finish_write` / `finish_read` 不在 STORE / RETRIEVE 函数里同步执行，而是在 GPU 拷完后由另一个线程执行。
- **最直观的收益数**：例子里 B 的 1200 token prefill 只需算 432 个。
