改了 115 处，删了 0 处。

- **BAD（歧义路径）**：把 109 处裸写的 `canvas.html:行号` 全部改成 `code-canvas/template/canvas.html:行号`。
- **???（引文对不上原文）**：照原文改了 6 处：`cam = { x: 0, y: 0, s: .8 }`、`* { box-sizing: border-box; margin: 0; padding: 0; }`、`.blk.folded > .bcode { display: none; }`、`width: auto !important`、`s.wires ? !activeCards.has(c.id.slice(5)) : false`、`Math.max(c._baseY, prevBottom + 60)`。
- **没动的 ???**：其余 ??? 条目是我自己的公式、举例数值、示意性写法，不是对原文的引用，没改。
- **重跑自查**：我自己的核对脚本重跑通过，锚点 134/134、引文 34/34、哈希 4/4。另有 HEAD 的 79153e7 只是版本号，不算改动，核对时显示 False。