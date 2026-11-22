1. **底稿**在 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/research/manuscript-sched_loop.md`。汉字约 6.6k，去掉行内代码后约 2 万字符，含完整路径的原始长度约 4.2 万字符。路径必须写全才能机械核对，所以原始长度会超 1.5 万。核对脚本在同级目录 `work-research-sched_loop/`（`anchors.py`、`verify.py`、`qcheck.py`）。

2. **研究范围**：
   - **读的文件**：`scheduler.py`（两个事件循环、选批、`PrefillAdder` 调用、`update_running_batch`、`run_batch`、relay/拷回）、`schedule_batch.py`、`schedule_policy.py`、`prefill_budget.py`、`overlap_utils.py`（FutureMap）、`batch_result_processor.py`、`request_retirer.py`、`output_streamer.py`、`new_token_ratio_tracker.py`、`mem_cache/common.py`、`allocator/base.py`、`managers/utils.py`（`copy_to_cpu`）、参数/环境变量定义，以及相关测试。
   - **提交**：查了 12 个，其中 `git show` 读过 diff 的有 6 个。
   - **从历史里挖到的三处**：
     - **FutureMap 的演变**：`512d164916` 的 diff 显示，旧设计是环形缓冲，按 `max_running_requests*(3+max_num_chunks)` 估容量，现在改成按 `req_pool_idx` 索引。
     - **`still_in_batch`**：`d1d317c64d` 把散落各处的 `not req.finished() and not req.is_retracted` 收成一处成员判断。
     - **每圈 `filter_batch()`**：`3b5ae4ea86` 把原先只在 `is_prefill_only` 时才做的过滤改成每步都做。
   - **一处对上一趟的修正**：`e14ef4b658` 加 `inflight_steps`，让按长度结束的请求不再被白发射一步，有集成测试佐证（1 次 prefill + 3 次 decode）。我顺着它重新读了释放逻辑，发现上一趟 §4.1 的「多分的 KV 槽被回收」不准确，已在 §6.3 修正。
   - **没读的部分**：投机解码、mamba、hisparse、dllm、PD、beam 等旁枝只点名。

3. **机械核对**：
   - **锚点**：318 个，行号都落在文件范围内，通过 318。符号是否对得上，是我把每个锚点解析出的行文逐条人工过了一遍。这一遍抓到并修正了约 40 处「短写 `:行号` 跟错文件」的错误，现在全是完整路径。
   - **哈希**：12 个，全部存在，所述改动文件已核。
   - **引文**：75 条英文引文，通过 75。其中 64 条是源码或测试里的单行逐字引文，11 条是提交标题，已对照 `git show` 的标题。核对中发现 4 处引的是已被删除的旧代码或旧测试，我已改成不带引号的转述。

4. **各等级「为什么」条数**（按标签出现次数，已扣掉文头图例）：

   | 等级 | 条数 |
   |---|---|
   | 【注释】 | 35 |
   | 【提交】 | 9 |
   | 【文档】 | 4 |
   | 【测试】 | 2 |
   | 【推断】 | 12 |
   | 【观点】（可改进处） | 4 |

   【推断】占「为什么」来源的 12/62，约 19%。

5. **没查到为什么**（底稿 §11 有完整清单）：
   - prefill 为什么优先于 decode。
   - `batch_is_full` 标志为什么这样设计。
   - `new_token_ratio` 的 0.7、0.14、600 步、20 步为什么取这些值。
   - `_apply_war_barrier` 具体保护哪块共享缓冲。
   - 主路里谁读 `future_map.publish(..., seq_lens+1)` 写进去的值。
   - `release_kv_cache` 注释里「over-allocated」具体指哪些情形。
   - 5 个关键提交（`512d164916`、`d1d317c64d`、`e14ef4b658`、`3b5ae4ea86`、`1c0dc0293d`）只有标题。我只能引标题和 diff 里看得到的内容。

6. **最弱的地方**：
   - §6.3 的结论是我按代码算出来的，没跑过：普通 decode 下 EOS 白算那一步的 KV 槽没有被回收，而是随前缀树 checkpoint 保留。这与上一趟的说法相反，已标【推断】并列出算式。上屏若用「回收」字样，建议改。
   - 许多「为什么」只有【推断】或只有标题级提交说明。
   - 篇幅超出，原因如第 1 点。
