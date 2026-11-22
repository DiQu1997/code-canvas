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
