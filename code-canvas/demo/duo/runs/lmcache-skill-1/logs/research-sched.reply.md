底稿已写完：`/root/canvas-duo-runs/LMCache-20261123-2359/research/manuscript-sched.md`。里面汉字约 5.4k，不含代码段的全文约 1.75 万字符（含全路径锚点共约 4 万字符），在篇幅限制内。

## 1. 内容
- 框内五个部件、连线和线程：回调入口、每请求 tracker、工单工厂、查找适配器加 `LookupState`、随步元数据。
- tracker 字段表：每个字段在哪一行被谁写。
- 状态机（含 `BYPASS_LMCACHE`），以及请求 A、B 逐步走的两张表。
- 讲解员的 7 个问题全部回答，另加抢占和加载失败一问。
- 6 段源码页，每段带逐行批注。
- 故障与降级表、没查到为什么、可改进处、给讲解员的提示。

## 2. 研究范围
- **读过的文件**：连接器、元数据、适配器相关段、`mp_lookup_state.py` 全文、服务端 `lookup.py` 的 LOOKUP/FREE/QUERY/END_SESSION 路径、`session.py`、`group_view.py`、`token_hasher.py`。
- **读过的测试**：`test_apc_covered_lookup.py`、`test_mp_nonblocking_lookup.py`、`test_mp_tracker_store_bound.py`、`test_mp_connector_kv_roles.py`、`test_vllm_mp_adapter.py` 的相关用例。
- **提交**：共引用 11 个，其中 `752e01fd`、`4f7d283a`、`60538a08`、`23cca679`、`38dddf92`、`7ba466fe` 看了 diff 或改动文件列表，其余只看了说明。
- **从 git 历史挖出的 3 条最有价值的**：
  - `752e01fd`：改之前 `request_finished` 对所有生产者一律返回 `self._can_store`；现在只对下过 STORE 单的请求延迟，并新增 scheduler 到 worker 的 `delay_free_req_ids` 通道。
  - `4f7d283a`：`computed_tokens` 外层加了 `num_engine_computed_tokens` 下界。测试例子是 APC 命中 112 被取整成 64，旧公式漏存 `[192,256)`。
  - `60538a08`：LOOKUP 跳过 vLLM 已覆盖的前缀，随之引出 `apc_covered_tokens`、`need_full_lookup`、`pending_free_ranges`。上一趟完全没提这套机制，我用测试的 docstring 把它讲通了。

## 3. 机械核对
脚本 `work-research-sched/verify.py`，用 `python3 -I` 跑，结果如下：

| 项目 | 结果 |
|---|---|
| 锚点 | 219 个，范围全部合法 |
| 关键锚点的符号/文字抽查 | 173 条，全过 |
| 带锚点引文 | 40 条，全过 |
| 提交说明里的无锚点引文 | 全部能在对应提交说明里找到 |
| 提交哈希 | 11 个，全部存在 |
| 提交改动文件 | 声称的文件都在改动列表里 |

核对中改掉了 6 处问题：4 处引文跨行，2 处行号偏差。

## 4. 各来源等级的「为什么」
标签出现次数，其中有的是同一条的复述：

| 等级 | 次数 |
|---|---|
| 注释 | 27 |
| 提交 | 5 |
| 文档 | 2 |
| 测试 | 13 |
| 推断 | 15 |
| 观点 | 6 |

推断约占 25%，不含观点。

## 5. 没查到为什么
- 非阻塞 LOOKUP 的提交（`ba8acd84`、`588c5366`）无正文，仓库里也没有实测的步数。
- `60538a08` 标题里的「non-pin」方案是什么、为什么没选。
- APC 为什么会在两次 poll 之间缩水。
- 取消的请求为什么不放 LOOKUP 的读锁：`free_lookup_locks` 的 docstring 明说可用于此，但 `request_finished` 没调。
- LOOKUP 为什么直接用 300 秒的 `mq_timeout`。
- `build_connector_meta` 里为什么先 retrieve 后 new/cached。
- `request_finished` 先 `cleanup_lookup_result` 再 `end_session`，使 `end_session` 里的 drain 在这条路径上不触发，是否有意。

## 6. 最弱的地方
- **vLLM 侧行为无法在仓库验证**：异步加载期间不调度该请求、alloc 回调与 `build_connector_meta` 同一步、block 按需分配，都来自接口 docstring 和上一趟，我标了推断或「设」。
- **「LOOKUP 最快隔一步」是推断**：来自代码结构，没有实测。
- **一个有后果的发现，只有测试 docstring 佐证**：被 APC 覆盖的前缀按「命中」计入 `num_stored_tokens`，哪怕 LMCache 里其实没有。我标成了推断加改进点。
- **新设的例子**：C（整段命中）、B″（APC 缩水）、B 被抢占、分块预填，只是用代码规则代入算数，没在真实服务上跑过。
- **没覆盖的部分**：lazy offload、unfull chunk、Mamba/SWA/多 KV group 只点名；多服务端的细节只讲了取最小值。