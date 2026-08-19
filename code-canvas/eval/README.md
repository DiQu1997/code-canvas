# code-canvas 评测：零上下文考试

回答一个问题：**新开对话、只有 skill 文件的 agent，能不能稳定产出合格的画布？**

## 方法论（诚实考试三原则）

1. **零上下文**：考生（`claude -p` 子进程）没有任何设计讨论的记忆，手里只有
   skill 目录、目标仓库、一句真实用户会说的话（`prompt-template.md`）。
   方法论必须完全由 skill 文件承载——这正是被考的东西。
2. **考题固定**：`exams.json` 是常设考题集（领航卷 + 深潜卷 × 多形态仓库）。
   每次修改 SKILL.md / 渲染器后重跑，分数变化即回归信号。
   **不要为了过题往 prompt 里加提示**——那是把答案写进考卷。
3. **机器打分为地板，人审为天花板**：`run.py grade` 自动查——validate
   无 ERROR、卡片代码对源仓库的 token 流可溯源 ≥90%（容忍披露过的换行重排）、
   结构预算（领航 ≤9 卡 / 深潜 ≤16 卡）、截图存在。内容质量（故事线切得
   对不对、注释是否到点）仍需人工抽查，机器分只保证"结构上不是坏的"。

## 用法

```bash
python3 eval/run.py list                 # 看考题
python3 eval/run.py exam codex-nav       # 完整考一场（clone→考生→打分，20–40 分钟）
python3 eval/run.py exam codex-nav --dry-run   # 只看考卷不花钱
python3 eval/run.py grade <产出目录> --repo <仓库> --mode orientation  # 只打分
```

考生 CLI 可换：`--cli "codex exec"`。产出与 `report.md` 落在 `eval/runs/`。

## 基线（2026-08-16，openai/codex）

| 考卷 | 结果 | 溯源 | validate | 用时 |
|---|---|---|---|---|
| codex-nav（领航） | PASS | 9/9 | 0/0 | 22 min |
| codex-exec（深潜） | PASS | 15/15 | 0/0 | 34 min |

两卷的完整评审（含截图与内容抽查结论）见 DESIGN.md 评测记录一节。
