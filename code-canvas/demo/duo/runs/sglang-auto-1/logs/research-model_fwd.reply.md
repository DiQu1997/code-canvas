底稿写完了，机械核对全部通过。

**1. 底稿**
- 路径：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/research/manuscript-model_fwd.md`。
- 规模：约 4750 个汉字，加上路径、行号和英文引文共约 5 万字节，远低于 1.5 万字的上限。
- 结构：全貌表（5 个部件 + 状态放哪）→ 例子设定 → 逐问逐答（§3.1–3.9，覆盖讲解员列的 5 个问题）→ 例子在框内走一遍 → 6 段源码页建议（另有 2 段可选）→ 没查到为什么 → 可改进处 → 对上一趟底稿的更正 → 读者提示。

**2. 研究范围**
- 读了约 35 个源码文件，主要是 `tp_worker`、`forward_batch_info`、`model_runner`、decode/prefill/eager 三个 runner、graph 后端与配置、FlashAttention 后端、`logits_processor`、`sampler`、`sampling_batch_info`、`scheduler` 的 overlap 分支、`overlap_utils`，以及 3 个测试文件。
- 为查"为什么"看了约 20 条提交。其中多数只有标题，没有正文。
- 从 git 历史里挖到的最有价值的 3 处：
  - `0543246184`「Enable Breakable Cuda Graph as Default」，加上 `cuda_graph_config.py` 里 `tc_piecewise` 已删除的报错，推翻了上一趟"prefill 通常不走图"的说法（见下）。
  - `999bad5aba`（修复 grammar 掩码在 overlap 下漏显存），解释了 `model_runner.py:2228` 为什么要立刻把 `grammar_mask` 置 None。
  - `dbec2f1847` → `f5a2faf2b8` → `1519a89cfd`（overlap 从独立线程演变为 `FutureMap` 加 CUDA 流），另有 WAR 屏障的两条提交 `163b970127`、`106d2930a6`。
- 最重要的更正：HEAD 上 CUDA 默认的 prefill 后端是 breakable 图。例子里的 15 个 token 会走图回放，补到 16 档。`model_runner.py:2174` 的 "piecewise" 注释是遗留。

**3. 机械核对**（脚本 `work-research-model_fwd/verify.py`，用 `python3 -I` 跑）

| 项 | 通过 / 总数 |
|---|---|
| 锚点范围（含简写解析） | 316 / 316 |
| 语义锚点（行上有所述符号或文字） | 267 / 267 |
| commit 哈希（存在，且改动了所述文件） | 15 / 15 |
| 逐字引文（含提交标题） | 78 / 78 |

- 锚点另做了覆盖检查：每个锚点的起始行都有对应的语义检查项。
- 核对中改掉了跨行引文、过期行号，以及自动展开简写时把 `[3,:21]` 误当成锚点的问题。

**4. "为什么"的来源等级**（正文内标注的数量）
- 【注释】25，【提交】8，【测试】2，【文档】0，【推断】5；【观点】5 条另算。
- 【推断】约占 12%。
- 一部分【提交】只有标题，没有正文。

**5. 没查到为什么**
- decode 档位 `1,2,4,8,12` 再 8 步长的依据，以及 prefill 档位的依据。
- prefill padding 上限为什么是 2×。
- FA3 的 `seq_len_fill_value` 为什么取 1。
- 采样为什么不放进 decode 图。
- `SGLANG_ENABLE_DELAY_SAMPLE` 为什么默认关。
- overlap 线程为什么被去掉。
- input id 的 H2D 为什么推迟到 forward 流。

**6. 最弱的地方**
- 提交证据太薄：多数只有标题，PR 正文读不到，很多"为什么"停在【推断】或"没查到"。
- 只深挖了一条主路：FA3 + 单卡 + 非投机 + 纯文本。流水线并行、投机解码、DP-attention、LoRA、多模态、MLA 后端只点了名。
- "forward 流内有序，所以下一步 gather 排在 publish 之后"这条同步结论，是我从代码读出来的推断，没有显式事件或注释佐证。
- 例子里的槽位号（37–41、212–227）、`r=3`、36 个档都是我设的。decode 档数是按默认公式算出来的，没有实机跑。
- 相对上一趟，源码页多了几处为照顾"逐行可核对"而写进正文的细粒度行号，可读性略受影响。
