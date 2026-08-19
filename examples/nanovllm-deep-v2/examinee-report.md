All delivered to the run directory; git shows no changes outside it (no source edits, no commits). Here's the report.

---

# 报告：nano-vllm「请求的一生 + KV cache」画布

## 交付物
`eval/runs/nanovllm-deep-0819-0801/` 下：
- `canvas.json`（16 卡 / 18 线 / 4 故事线 / 10 步）、`canvas.html`（自包含，106 KB）
- `shot-s0.png … shot-s9.png`（总览 + 9 个 step 的自检截图）
- `structure.json`（第 2 步 `extract.py` 机械提取的结构层中间产物，保留作溯源）

## 关键决策

**Scope 怎么定的。** 用户问题落在 `nanovllm/engine/` + `layers/attention.py` + `layers/sampler.py`，约 700 行——远低于 SKILL 的 2000 行闸门，所以直接出**细粒度单张画布**，不做领航图。按 SKILL 第 2 步跑了 `extract.py`（全量 58 卡 / 46 线），再策展砍到 16 张卡。读者画像按你给的设定写进 `meta.audience`。

**故事线怎么选的。** 我没有照搬同仓库已有的 `demo/nano-vllm.json`。它把 KV cache 只讲了"逻辑块账本"一层。你的问题里"KV cache 是怎么管理的"其实有**两层**，demo 缺了物理层，所以我把它作为差异化重点补上，四条线：
- **A 请求进入 & 引擎循环**：`generate → add_request → step`（"进入引擎"这一问的字面入口）
- **B 调度决策**：`schedule / postprocess / preempt`
- **C 前向：算出 token**：`run / prepare_decode / sampler`
- **D KV cache 管理（物理写入 + 逻辑账本）**：`attn / store_kvcache_kernel / allocate_kv_cache`（物理层：triton kernel 按 `slot` 散写、启动时算 `num_kvcache_blocks` 开张量）+ `can_allocate / allocate / hash_blocks`（逻辑层）+ 一张 `kind:"state"` 实例快照卡。

**贯穿全图的主线**是 `slot_mapping`——一条 data 线从 `prepare_decode:10` 流到 `attn:5`，把"逻辑块号 → 物理槽位 → 显存地址"串起来（`store_kvcache_kernel` 里 `cache_offsets = slot*D`）。这是我认为最能回答"KV 到底怎么落盘"的证据链，也是 demo 没有的。

**布局**：5 列（A/B/C 各一列 3 卡，D 占两列），调用方向左→右。实例快照卡是宽卡，放在最右列 col 4——宽卡在最右只会向画外延伸，放内列会把整张图撑宽。

## validate.py 最终结果
`0 errors, 3 warnings`——三条都是**有意保留**的：
1. `schedule:18` 101 字符：verbatim 源码行（含 `# only allow chunked prefill…` 行内注释），不改写。
2. `postprocess:9` 104 字符：verbatim 的 EOS 判定行，是关键逻辑，不拆。
3. col 4 有 4 张卡：第 4 张是宽的实例快照卡，放最右列是正确取舍（见上）。

**披露的换行重排**（SKILL 硬约束要求）：均为 token 数不变、顺序不变的合法续行——
- `sampler.forward` 第 5 行：`div_(...)` 括号内断行；
- `allocate_kv_cache`：`getattr(...)`/`int(...)`/`torch.empty(...)` 括号内断行 + `block_bytes` 反斜杠续行；
- `attn.forward`：`flash_attn_*` 的 kwargs 续行统一缩进（仅空白）。
另有两处**连续摘选**（长函数取片段，`file` 标了 `路径:起始行`）：`prepare_decode` 只取构造 `slot_mapping` 的循环（1–10 行，省去搬 GPU 的 tensor 化尾巴）；`store_kvcache` 取 triton kernel（真正做散写的地方）而非薄封装。所有行锚都在最终卡文上重算。

## 截图自检发现并修掉的问题
1. **caption/背景 note 超预算**：首轮 13 条 warning，多为 caption > 95 字符、`bg0`/`bg-slot` 超字数上限。逐条压缩到预算内（细节移到 `detail` 里，caption 保持"一眼镜头"）。
2. **step ⑤ 的 `allocate_kv_cache` 被底栏挡住**（关键的 `block_bytes` 公式行落在 caption dock 后面）：根因是 col 3 三张卡竖叠太高，框不下。**拆成两步**——⑤ 只讲 attention 写/读 KV（`attn` + `store_kv`），⑥ 单独讲显存池（`allocate_kv_cache` + 快照）。重截后两步都完整入镜、公式清晰。
3. 复核过：无元素压盖、点亮的线两端可见且锚在正确行、折叠卡块色条可读、每张卡都被某步聚焦过。

**一个已知的取景现实（未改，与 demo 同款）**：step ⑧/⑨ 是"左边代码=因、右边快照=效果"的跨列步，镜头较宽、快照格子偏小。这是"因在左、果在右"叙事的固有张力，demo 也如此；精确变化由快照的 `note` 和 step `detail` 兜底叙述。

## SKILL.md 哪里让我不确定
1. **`file` 字段的相对基准自相矛盾**：SKILL 第 146–148 行明说"路径相对仓库根"，但参考 demo `nano-vllm.json` 用的是包内相对（`engine/llm_engine.py`）。我按 SKILL 文字选了仓库根（`nanovllm/engine/...`）。两者对静态画布无功能差异，但若用 `serve.py --repo` 做块级问答，基准不同会影响文件解析——建议 SKILL 明确一个。
2. **列内卡数上限 vs 实例快照卡**：SKILL 说"一列不超过 3 张卡"，但每张图又几乎必须有一张宽 `state` 卡。宽卡该不该计入这个上限、该放哪列，没写。我的判断是放最右列、接受一条 warn，但这属于我自己拍的。
3. **截图自检要求"以顺序走为准"，但无头 chromium 只能 `#sN` 直达**（`expand`/`unfold` 是累积的，直达画面比顺序走少了前几步的展开）。SKILL 没给无头环境下复现"顺序态"的手段，我只能按直达截图自检，并据此判断取景——这点值得在 SKILL 里点明或提供一个"截图前重放到第 N 步"的入口。
4. **跨子系统的调用线画不画**：`run → attn` 中间隔着 `run_model → model → 各层`，是多跳。SKILL 只说"删到只剩故事在场的"，没说多跳调用该直连还是省略。我选了直连一根 `call` 线并在 `detail` 里说明跳步，但这也是一处自由裁量。
