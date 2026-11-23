# 舞台问题（讲解员 · 顶层这一趟）

1. **锚点只认少数扩展名**：`build.py` 的 `REF_RE` 只收 `c|h|conf|tcl|md|json|py|sh|txt|lua|yml|yaml`。
   本项目的依据有不少落在 `.mjs`（`code-canvas/tests/hub.mjs`）和 `.html`（`code-canvas/template/canvas.html`）。
   - 最小复现：`note('测试', 'x', ref='code-canvas/tests/hub.mjs:473')` → `BuildError: 证据锚点格式不对`，整个顶层退回临时骨架。
   - 文字里写 `` `code-canvas/template/canvas.html:1031-1080` `` → WARN「像锚点但扩展名不在识别范围内」。
   - 绕法：测试证据只能写成纯文字（`code-canvas/tests/hub.mjs 第 473 行`），`note` 等级被迫从「测试」改成「推断」；canvas.html 锚点也改成纯文字，读者点不开原文。
   - 影响下一层：「浏览器排版」那条钻取申请（dive_render）的源码几乎都在 canvas.html 里，源码页 `code('code-canvas/template/canvas.html', …)` 很可能也挂不上。
