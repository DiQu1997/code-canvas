#!/usr/bin/env bash
# diff-gate.sh — Stop hook。
# 闸门：存在计划画布、工作树已有改动、但还没交 diff 画布时，不许收工。
# stop_hook_active 防死循环：本 hook 已经拦过一次就放行。
# exit 2 = 阻断收工，stderr 喂回给 agent 作为指令。
input="$(cat 2>/dev/null)"
case "$input" in *'"stop_hook_active":true'*) exit 0;; esac

d=".canvas"
[ -f "$d/plan.json" ] || exit 0
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
  exit 0
fi
if [ -f "$d/diff.json" ] && [ "$d/diff.json" -nt "$d/plan.json" ]; then
  exit 0
fi
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cat >&2 <<MSG
改动已发生但还没交 diff 画布（code-canvas 规程，skill 在 $SKILL_DIR）：
1. 按 $SKILL_DIR/SKILL.md 的 diff 模式产出 .canvas/diff.json——与
   .canvas/plan.json 同底图（相同卡 id 与 layout），覆盖 git diff 里
   全部被改的文件，风险步必选。
2. python3 $SKILL_DIR/validate.py 清零、$SKILL_DIR/render.py 渲染 .canvas/diff.html。
3. 跑 python3 $SKILL_DIR/compare.py .canvas/plan.json .canvas/diff.json \
     --repo . --annotate .canvas/diff-checked.json，渲染 diff-checked.html，
   把「计划内 / ⚠ 计划外 / ○ 计划未动 / ⚠ 画布未覆盖」对照结论如实汇报给作者。
MSG
exit 2
