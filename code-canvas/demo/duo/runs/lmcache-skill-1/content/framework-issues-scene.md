# 舞台问题（讲解员，顶层）

1. 锚点扩展名白名单不含 `.rst`（build.py `REF_RE`）。本仓库的设计文档在 `docs/source/**/*.rst`，
   `note('文档', …, q=…, ref='docs/source/mp/index.rst:25')` 直接 BuildError「证据锚点格式不对」，整个顶层退回临时骨架。
   最小复现：任意 `note('文档','x', q='Process isolation', ref='docs/source/mp/index.rst:23')`。
   绕开：把这几条「文档」级理由降成「推断」，在文字里写明出处。

2. 页面底部「按顺序读，下一页 ›」那张卡：在顶层全景截图（d1-overview）和下层页（d6-dive）上，卡的上半部分是一个空的蓝色描边条，
   「下一页」的标题落在描边条外面下方，看起来像错位。复现：本项目 `--all` 的 d1-overview.png / d6-dive.png 右下角。未处理。
