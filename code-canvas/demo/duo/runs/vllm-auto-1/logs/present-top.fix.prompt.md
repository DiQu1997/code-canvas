你做的顶层图之后，系统按你的申请派了几位讲解员做下层。他们对照各自那份补充底稿，认为你的顶层有下面这些事实问题（他们不许改你的文件，所以交回给你）：

### 来自 outproc

# 顶层「输出处理」相关的说法问题（来自 outproc 下层讲解员；都不是硬错，属于说法不全，优先级低）

1. 部件 `outproc` 说明卡「容易看错的地方」第 2 条
   - 原话：「有 stop 串时流式文字会「晚几个字」：扣住最长 stop 串长度减 1 个字符不发，防止把半截 stop 串发出去。」
   - 问题：漏了条件。只有在 `include_stop_str_in_output=False`（默认）时才扣；为 True 时扣住数是 0。
   - 依据：manuscript-outproc.md Q2「扣多少」，`vllm/v1/engine/detokenizer.py:133-136`。
   - 建议：改成「有 stop 串（且不要求输出 stop 串，默认如此）时……」。

2. 部件 `outproc` 说明卡「容易看错的地方」第 1 条
   - 原话：「……这里判 stop 串，命中后要**反向**发 ABORT 让引擎停算。」
   - 问题：如果 stop 串和引擎的 EOS / 长度在同一步命中（`engine_core_output.finished` 为真），前端不发 ABORT。
   - 依据：manuscript-outproc.md Q3 第 4 点，`vllm/v1/engine/output_processor.py:881-885`。
   - 建议：句末补「（引擎同一步已自己判停时不发）」，或保持原样（顶层可接受的简化）。

### 来自 sched

# 顶层图需要改的地方（来自「调度器」下一层，依据 manuscript-sched.md）

## 1. 推演最后一步「第 7 个 token 是 <eos>，收尾」：引擎队列和 GPU 的状态
- 顶层哪里：推演第 12 步（最后一步）的 `state`：`'engine': bq()`（空队列）、`'gpu': g_text('空闲', '没有请求')`；what 里也没提多排的那一步。
- 为什么不对：底稿 §8「EOS 却躲不掉」：调度器排第 8 步（S8）时输出 7 还在飞，所以输出 7（`<eos>`）被处理的那一刻，S8 已经在 `batch_queue` 里、在 GPU 上跑；它的结果之后回来，因请求已不在 `requests` 里而被丢弃（`vllm/v1/core/sched/scheduler.py:2403`、`:2415-2423`，EOS 走这条分支是底稿标的推断）。
- 建议：这一步把 engine 小图画成队列里还剩一格 `第8步 / 1 tok`（或标「白算」），gpu 画成「第8步 在跑（结果会被丢）」；what 末尾加一句「此时第 8 步已经排出去了，它的结果回来时请求已结束，被丢掉——下一层『调度器』里有细节」。

## 2.（可选）「一轮一轮重复」那一步的措辞
- 顶层哪里：推演第 11 步 what「第 7–10 步每轮重复……」。
- 说明：不是错误，只是读者从下一层回来会看到 S1…S8 的编号（第 k 次 `schedule()`）；若顶层愿意，可在这一步顺带写一句「顶层的第 1 轮 = 下一层的 S1」，两层编号就对上了。下一层已在推演第 2 步写了「S1，顶层的『第 1 轮』」。

请逐条对照底稿核实（补充底稿在 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research` 下的 manuscript-*.md）：确实不准的，在 `content_scene.py` 里改准（只改说法和数，不重做图、不动坐标和结构）；你认为没错的，不改，并说明理由。改完 `--strict` 构建一次、`--check` 自检一次，确认 WARN 0、排版问题 0。不要改别的 content_*.py。最终回复只写：每条改了没有、为什么。