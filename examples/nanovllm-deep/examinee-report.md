All deliverables are in place; I only wrote to `runs/` and scratchpad (skill files were read-only). No git commits made. Here's the final report.

---

# 报告：nano-vllm 深潜画布

## 交付物
全部在 `eval/runs/nanovllm-deep-0818-1918/`：
- **canvas.json**（18 卡 / 19 线 / 10 步，39 KB）、**canvas.html**（89 KB，自包含、无外链、单 `<script>`）
- **shot-s0…s9.png**：总览 + 每步各一张自检截图
- `structure.json`：extract.py 的结构层中间产物（保留以留痕管线）

构建脚本在 scratchpad（`build.py`），未写入 skill 目录任何文件，未做 git 提交。

## 关键决策

**Scope（我替你定的）**：你的问题是两个交织的子系统，我就把画布拆成两条并行故事线，而不是一条线性流程：
- **故事线 A（蓝）· 请求的一生**：`add_request → generate 驱动循环 → step → schedule → run(prepare/run_model) → Sampler → postprocess`，闭环回到"吐 token"。
- **故事线 B（绿）· KV cache 分页管理**：`BlockManager 池 → can_allocate/allocate（前缀复用）→ prepare_prefill 的 slot_mapping → store_kvcache/Attention → hash_blocks（登记）→ may_append（decode 增长）→ deallocate/preempt（回收与抢占）`。

代码量 ~767 行、主题明确，落在 SKILL 的"≤2000 行 → 直接细粒度画布"闸门内，所以没走领航图。

**卡片取舍**：extract.py 全量提取 58 卡，我策展到 18 张——砍掉 tensor-parallel 的 shm/loop、cudagraph capture、Block/Sequence 的一堆 property 等与这两个问题无关的卡；`can_append` 不单独立卡，并进 schedule 的 decode 块 + preempt 步讲。

**布局**：用"双泳道"——上半带（band 0–2）是 A 的控制+前向，下半带（band 3–4）是 B 的显存管理，两条故事线各自成片、又靠跨带的线相连（这正是要表达的"交织"）。列按调用方向 0→4。

**故事线/步进**：10 步按"读者的问题"排，不按文件序：怎么进 → 循环三件事 → 调度选路 → prefill 要显存(分页+前缀) → 前向出 logits → **KV 在哪读写(slot_mapping 之旅)** → 采样 → 落库/登记/收尾 → decode 续显存 or 抢占（回归风险步，SKILL 规定必选）。数据线只画了 4 条真正的"值的旅程"：`block_table`、`slot_mapping`、`logits`、`token_id`。

## validate.py 最终结果
**0 errors, 10 warnings**（全部为可接受的预算类，非结构错误）：
- 6 条 = `prepare_prefill` 的 `搬上 GPU` 块内 torch.tensor 长行（默认折叠，展开时才可能超边界，未处理）；
- 1 条 = `schedule` 第 18 行 105 字符（尾部注释，105 属临界，折叠在 prefill 块内）；
- 3 条 = 列内 >3 卡（col1=4、col2=5、col3=4）——这是双泳道设计的必然：同一列上下两带各放 2–3 张，靠 band 分隔不重叠，截图已确认无压盖。

## 截图自检发现并修掉的问题
1. **宽行截断**：`store_kvcache`(132 字)、`Attention.forward` 的 flash_attn 调用行(108–111 字) 在 step⑥ 里冲出视口右缘，把 `block_tables` 变元注释挡在屏外。→ 按 SKILL 允许的**保序换行重排**（括号内断行 / 反斜杠续行，token 顺序不变），10 处长行拆行，重排后所有锚点由子串重新程序化计算。修完 step⑥ 两张焦点卡完整入框。
2. **有卡从未被 focus**：自检发现 `BlockManager.__init__`（块池）和 `may_append`（decode 增长）没有任何 step 聚焦。→ 把块池并进 step④、把 `may_append` 并进 step⑨（decode 的两种结局：追块 or 抢占），现每张卡至少被聚焦一次。
3. **高亮打到空行**：step③ 第二个高亮 `schedule` 行 29 是空行。→ 改为行 33（`# decode` 标记），现在 `# prefill`/`# decode` 两个分支标记同时高亮。

## extract.py 的一个坑（值得记一笔）
`extract.py` 的 `discover()` 会跳过任何路径段以 `.` 开头的目录。目标仓库在 `.repos/nanovllm/` 下，于是**传目录时它一个 .py 都找不到**（只报 5 卡，全来自我额外显式传的两个文件）。绕过办法：把 engine 下的文件逐个显式传参（显式文件走另一分支，不受该过滤影响）。SKILL 没提这个前提。

## SKILL.md 哪里让我不确定
1. **`file` 路径基准自相矛盾**：SKILL 硬约束写"路径相对仓库根"，但官方 demo `nano-vllm.json` 用的是 `engine/llm_engine.py`（相对包目录）。我按 SKILL 正文走仓库根（`nanovllm/engine/...`），extract.py 也是这么产的——但和 demo 不一致，容易误导。
2. **步骤锚 `#sN` 的 N 从几起**：SKILL 只举例 `#s2`，没写第 0 步（总览）是 `#s0` 还是 `#s1`。实测 `#s0`=总览。
3. **`>100 字符`与"卡片代码=原文"的张力**：SKILL 一边说"卡内必须原文、不改写不省略"，一边允许"保序换行重排"，但没界定重排到什么程度算越界、是否必须在报告披露（我按披露处理了）。对 nano-vllm 这种单行密集的代码，几乎每张前向卡都撞线，这条边界该写清。
4. **双子系统的布局范式缺省**：SKILL 的布局经验规则默认单一调用链（"入口 col0、被调 col+1"）。像本例这种两条并行故事线该怎么摆（我自创了"泳道分带"），SKILL 没有范式，只能自行发挥——而这直接触发了 ">3 卡/列"警告，说明规则与双故事线场景没对齐。
5. **`focus` 卡数上限**：schema 说"邻域卡片 ≤4"，但 validate.py 并不校验 focus 的卡数；到底是硬约束还是软建议、note 算不算进这个预算，不明确。

整体上 SKILL 的核心管线（结构层机械提取 → 叙事层策展 → 验证 → 截图自检）是顺的，卡我最久的是上面第 1、3、4 点。
