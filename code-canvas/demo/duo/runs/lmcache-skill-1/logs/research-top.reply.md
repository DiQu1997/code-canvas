## 研究底稿已完成

**1. 底稿位置和规模**
- 路径：`/root/canvas-duo-runs/LMCache-20261123-2359/research/manuscript.md`
- 全文约 4.2 万字符，其中汉字约 6100 个；其余大部分是锚点路径和英文引文。
- 核对脚本和 sidecar 放在同目录下的 `work-research-top/`。

**2. 研究范围**
- **主路的选定：** 这个 HEAD 上进程内的 `LMCacheEngine` 已经被移出仓库，所以主路是 MP 模式。具体是：vLLM 里的 `LMCacheMPConnector`（分调度器和 worker 两半），经 ZMQ 连到独立的 `lmcache server`。服务端用 CUDA IPC 直接读写 vLLM 的显存，数据只放 L1（CPU 内存），不配 L2，chunk 为 256。
- **读过的代码（约 30 个文件）：**
  - vLLM 接入层：connector、metadata、adapter、lookup_state
  - 客户端：`worker_transfer`、futures
  - 服务端：server、zmq 主循环和线程池、affinity_pool、request_handler、lookup / 传输两个模块、session、engine_context
  - 键与哈希：token_hasher、token_codec、custom_types
  - 存储层：distributed 下的 api、storage_manager、prefetch / store / 淘汰三个控制器、L1 base、配置
  - 文档：quickstart、mp/index、chunk 协商、storage_manager 设计文档
  - 测试：3 个
- **提交历史：** 看了约 25 个提交的说明，底稿引用其中 14 个。
- **最有价值的 3 个"为什么"（来自 git 历史）：**
  - `50e45434`：写入完成回调必须走 C++ host callback。直接用 Python 回调会出现 GIL 和 CUDA 驱动锁互相等待的死锁（AB-BA）。
  - `7ba466fe`：默认只存 prompt 里的完整块，生成出来的 token 不存。
  - `ba8acd84` 和 `588c5366`：LOOKUP 不等回执、状态查询也不阻塞，目的是不占住 vLLM 调度线程。

**3. 机械核对结果**
- 锚点 270/270 通过，提交哈希 14/14 通过，引文 54/54 通过。
- 锚点的核法分三种：
  - 约 110 个靠 sidecar 里人工写的预期文字核对；
  - 约 40 个由挂在它上面的引文核对；
  - 其余约 120 个只要求"同一行出现的标识符确实在目标行里"，这个标准偏松，所以我把这部分全部打出来人工看过一遍。
- 过程中修正了 9 处错误锚点：8 处行号偏移，1 处指错了位置（`mp_lookup_state.py:25` → 69）。

**4. "为什么"的来源分布**
- 一共 53 条（不算文件头那行说明）：【注释】27、【提交】9、【文档】6、【测试】3、【推断】8。
- 【推断】约占 15%。另有【观点】4 条，都在"可改进处"一节。
- 注意：【提交】大多只有标题可引，提交说明正文基本是空的。

**5. 没查到为什么的地方**
1. 为什么从"用 vLLM 的 block 哈希"改成"服务端用原始 token 算哈希"（`1419a115`、`b022405c` 都只有标题）。
2. 为什么 STORE 的 `batch_size` 必须是 1。
3. 为什么默认 chunk 是 256。
4. 为什么读锁 TTL 是 300 秒、写锁是 600 秒（只查到 TTL 是为了防客户端崩溃后死锁，具体数值没有理由）。
5. 为什么 GPU / CPU 线程池默认各只有 1 个线程。
6. 为什么没有做逐层流水（`wait_for_layer_load` 是空的）。
7. 为什么默认用 blake3。
8. 为什么把进程内实现移出仓库（`5b9db86f` 只有标题）。

**6. 我认为最弱的地方**
- **vLLM 一侧的行为没在本仓库里核实。** 比如 `WAITING_FOR_REMOTE_KVS`、block 怎么分配、各个回调在什么时候被调用，我是根据 connector 的文档串和注释写的，没读 vLLM 源码。
- **例子里的数字一部分是设定的。** "每个 chunk 36 MiB"依赖我设的 Qwen3-8B 形状，加上"一个对象装该 object group 所有层"这一点（后者有注释支持）。"LOOKUP 结果要隔一两个调度步才拿到"是根据代码推出来的，没有实测。
- **L2、PrefetchController 的多阶段流程、GPU 拷贝内核这几处只给了入口锚点，没展开。** 这是按这一趟的深度刻意留下的。
- **一部分【提交】只是在复述标题**，证据力度比注释弱。

研究上下文都还在，讲解员可以随时回头追问。