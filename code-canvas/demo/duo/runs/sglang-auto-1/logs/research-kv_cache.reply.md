我写好了底稿，机械核对已过。核对脚本只能证明锚点指向的行存在、引文逐字吻合，不能证明我对代码的理解是对的。

## 1. 底稿路径和规模
- 底稿：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/research/manuscript-kv_cache.md`
- 规模：约 5600 个汉字，全部字符含路径和代码约 3.4 万。如果按全部字符算，是超过 1.5 万的，请讲解员按需裁剪。
- 脚本在 `.../research/work-research-kv_cache/`：`verify.py`（核对）、`dump.py`（逐锚点打印目标行，供人工复核）。
- 全程没有实跑任何代码，所有结论来自读源码和 git 历史。

## 2. 研究范围
- **读的文件**：`memory_pool.py`（请求行表）、`allocator/token.py`、`allocator/base.py`、`allocation.py`、`static_kv_storage.py`、`capacity.py`、`common.py`、`registry.py`、`unified_radix_cache.py`（匹配、插入、checkpoint、淘汰入口）、`unified_tree_core.py`（节点、匹配、插入、劈节点、叶子、加锁）、`components/full.py`、`prefill_budget.py`、`schedule_policy.py`、`schedule_batch.py`（`ReqKvInfo`、命中长度上限、`owned_kv_len`）、`batch_result_processor.py`、`request_retirer.py`、`radix_cache.py` 的 `RadixKey`，以及 README 和 `radix_eviction_policy.mdx`。
- **范围取舍**：默认树实现是 `UnifiedRadixCache`，不是更好读的 `radix_cache.py`。本稿只讲普通全注意力模型这一条路，SWA、Mamba、HiCache 分支只点名。
- **git 历史**：约 10 次 `git log -S`、3 次 `blame`、6 次 `show`，涉及 5 个提交。
- **从历史里挖出的三条"为什么"**：
  1. 命中长度"输入长度 − 1"来自 `1e495e0847`（2024-09）。当年作者就写了 FIXME，说是为绕 logprob 的 bug，还留着更早的旧注释 "Need at least one token to compute logits"。
  2. 在 prompt 边界把叶子再切一刀，来自 `0a57403468`，原注释把动机写明了：让 LRU 能只丢输出的 KV，不连带丢掉可复用的 prompt。
  3. `checkpoint` 的文档和 "fill-id 滞后" 注释来自 `50be533d09`，该提交由 `git blame` 确认。该提交的名字说明"中途写树"并不新，之前叫 `cache_unfinished_req`。

## 3. 机械核对结果
- **锚点**：291 个，全部指向存在的文件和行范围。核对中发现并改掉了 4 处简写行号指错文件、1 处行号偏一行（`unified_radix_cache.py:777` → `776`）。
- **逐条人工复核**：脚本只能查"行存在"，所以我另行打印了全部 290 条锚点的目标行，逐条读过。
- **引文**：54 条，53 条由脚本直接核过；剩下 1 条（"Need at least one token to compute logits"）出自 `1e495e0847` 当时的历史文件，我用 `git show` 单独核实了。
- **哈希**：6 个，全部存在，改动的文件和日期也对。脚本把 `7623091d97` 报成日期不符，是它自己取错了同一行里的第一个日期，该提交实际是 2024-08-07。

## 4. 各来源等级的"为什么"数量（粗略计数，已去掉图例）
| 等级 | 条数 |
|---|---|
| 【注释】 | 约 18 |
| 【提交】 | 约 6 |
| 【文档】 | 约 9 |
| 【测试】 | 0（没找到对应用例，也没去翻测试目录） |
| 【推断】 | 约 18 |
| 【观点】 | 4 |

【推断】约占 35%，偏高，原因见第 6 点。

## 5. 「没查到为什么」清单（底稿 §12）
- 默认实现何时从旧 `RadixCache` 换成 `UnifiedRadixCache`。只找到 `afa3c06d1f`，但它的标题只提 SWA、Mamba、DSA。
- `--page-size` 默认 `None` 之后在哪落成具体数。
- 为什么 `checkpoint` 在 prefill 一结束就写树，而不是只在结束时写。
- `strip_thinking_cache` 注释里的 "#22373"。
- `unlock` 为什么在 `lock` 之前。
- 为什么先插树再还槽，以及为什么树存槽号的拷贝而不是视图。

此外，关键提交的正文基本是空的，PR 和 issue 读不到，"为什么"主要靠代码注释和 diff 里新增的注释。

## 6. 底稿最弱的地方
- **修正了顶层的一处说法**：树在 prefill 做完（请求还在跑）就写了一次，分块之间也写，结束时只是补输出。顶层说明卡写的是"结束时才写进树"，这条需要讲解员同步改。证据是代码路径加官方文档字符串，但"为什么要这么设计"只是推断。
- **结束时那一步的槽数没实跑过**：树最终存 24 个 token 还是 25 个，取决于 overlap 是否多发了一步 decode。我只能由 `owned_kv_len` 的公式推出两种情形，已标【推断】。顶层 §8 第 17 步"多分的槽一并回收"，按我读到的公式，这些槽多半留在树里，同样没实跑过。
- **"为什么只淘汰叶子""为什么匹配时就劈节点"**：文档只说了行为，没说动机，动机是我的推断。
- **所有槽号、行号、节点名都是"设"的**，没有真实运行的数据。
- **默认路径夹在大量 SWA、Mamba、HiCache 分支里**，源码页的行范围里有不少可跳过的行，我已逐行标注，但读者仍可能觉得吃力。
