# 舞台问题（讲解员 · dive_render 这一趟）

1. **`code()` 源码页上的 `at(..., q=...)` 在 `.html` 文件上会 BuildError**（和顶层记过的 REF_RE 扩展名问题同源）。
   - 现象：`do_note` 给 code 批注补的 ref 是 `<文件>:a-b`，再过 `REF_RE`，`.html` 不在白名单 → `证据锚点格式不对`。
   - 最小复现：`code('code-canvas/template/canvas.html', [(1035, 1035)], notes=[at(1035, '注释', 'x', q='measure natural card widths')])`。
   - 绕法：源码页上「注释」级批注不写 `q`，改用 `has=` 写注释原文（构建会核对子串），等级仍标「注释」；构建不再逐字核对引文，只核子串。
   - 说明卡里的 note 也引不了 canvas.html 的注释（`ref=` 同样 BuildError），只能改引 DESIGN.md / HANDOFF.md / ROADMAP.md / 提交，或降级成「推断」并内链到源码页。
2. 文字里的 `` `engine/llm_engine.py:49` ``（被讲画布数据里的路径，不在本仓库）会被当成锚点，构建直接报「仓库 HEAD 里取不到文件」并让整个槽位失败——希望这种情况降级为 WARN + 行内代码，而不是整页失败。
