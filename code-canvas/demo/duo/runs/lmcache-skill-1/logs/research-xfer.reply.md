**1. 底稿**：`/root/canvas-duo-runs/LMCache-20261123-2359/research/manuscript-xfer.md`。约 3.8 万字符，其中汉字约 6000，没超 1.5 万字。结构是：先给七个问题的一句话答案，再讲框内部件和线，然后是 STORE / RETRIEVE 两张时间线，接着逐个回答七问，最后是 6 段源码页候选、没查到为什么、可改进处和给讲解员的提示。

**2. 研究范围**
- 读了：`lmcache_driven_transfer.py` 的 store / retrieve / 注册 / 回调部分，`native_completion.py`，`completion_recorder.cpp`，`affinity_pool.py`，`mq.py`，`object_group_transfer/` 下的 dispatch、direct_copy、staged、planning，CUDA 内核 `mp_mem_kernels_detail.cuh`，event IPC 后端和 `futures.py`，worker 侧 `worker_transfer.py` 和 vLLM 适配器，`storage_manager.py` 的相关接口，一份设计文档，以及多个测试。
- 看了约 15 个提交的说明或 diff（`git show` / `git log -S`）。其中 5 个只有标题或署名行。全仓库的 `-S` 太慢，我只在文件重命名前后的两个路径上查。
- 从 git 历史里挖出的最有价值的三条：
  - `3137ce0b`：引入亲和线程池时，同一提交删掉了每设备锁 `transfer_lock`，被删注释写着 “to avoid deadlocks with the implicit CUDA driver lock”。演化顺序是全局锁 → 每设备锁 → 亲和线程无锁。
  - `831e0502`：“A store whose copies or completion preparation failed left its staging reservations to expire with the write TTL…”。这说明 `abort_write` 是后加的，此前失败的 store 只能等写锁超时。
  - `5f699d3f`：被删掉的旧注释只说 “Store is not batched…”，没给原因。这也是 `batch_size=1` 没查到为什么的证据。
- 代码里还有几点是上一趟没讲到的：
  - 完成 event 录在 `finish_write` / `finish_read_by_owner` 回调**之前**，有测试钉死顺序（`tests/v1/multiprocess/test_l1_owner_completion.py:193`）。
  - 拷贝有 direct / native plan / staged 三条路径。例子里每层每个 block 刚好 32 KiB，恰在 direct 的门槛上。
  - 「要么全存」只管入队期异常和 block id 不够；某个 key 已在 L1 或没空间不算失败。
  - skip 是 block 粒度、向下取整。
- 更正了上一趟：RETRIEVE 说明卡里「读对象」和「等 event」的顺序写反了，代码是先等 event。

**3. 机械核对**（脚本 `research/work-research-xfer/check.py`，用 `python3 -I` 跑）
- 锚点 189/189 通过：文件存在且行号在范围内。
- 另有 111 条「锚点行上有指定符号 / 文字」的抽查，111/111 通过。
- 仓库原文引文 33/33 通过，逐字且在引用的行范围内。
- 提交哈希 10/10 存在；14 条「提交确实改了某文件」全通过。
- 提交说明和 diff 里的引文 14/14 在对应提交里找到。
- 脚本不核对「分支 / 顺序 / 因果」这类叙述断言，那些靠我读代码。

**4. 「为什么」的来源等级**（文中标记计数，含少量非「为什么」的行文标注）
- 【注释】27，【提交】10，【文档】3，【测试】5，【推断】11，【观点】约 6。
- 【推断】占「为什么」约 20%，主要集中在 event 为什么要等、事件顺序的理由、GPU 暂存缓冲、STORE 与 RETRIEVE 的队头阻塞、skip 取整是否实际发生这几处，文中都写了依据。

**5. 没查到为什么**（§7）
- store 的 `batch_size` 为什么必须是 1。
- STORE 为什么在最前面就建完成 event。
- 下溢守卫失败时为什么导出真 event，而不是 docstring 说的空句柄。
- 排空线程为什么是 5 ms，`max_batch_size` 为什么是 4。
- staged 路径为什么要经过 GPU 暂存缓冲。
- 完成 event 为什么录在提交回调之前。
- 5 个提交（`4d96107c`、`fc143b0a`、`50e45434`、`38dddf92`、`cec6092d`）说明里没写原因。

**6. 最弱的地方**
- 例子具体走哪条拷贝路径取决于 CUDA / 驱动版本和构建，我只能推演，没有实际运行验证。
- 「GPU 异步执行出错不在 try 里」「同流队头阻塞」「完成 event 先于提交」这类并发后果，是从代码结构和 CUDA 语义推出来的，没有测试直接覆盖。
- 一批提交说明只有标题，修复前的失败现象只能靠注释还原。
- 6 段源码页里的逐行批注是我按行号写的，没有再逐行回对一遍。