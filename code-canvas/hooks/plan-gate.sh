#!/usr/bin/env bash
# plan-gate.sh — PreToolUse:Edit|Write|MultiEdit hook。
# 闸门：没有新鲜的计划画布（.canvas/plan.json，30 分钟内）之前，不许改仓库文件。
# .canvas/ 下的写入放行——产出计划画布本身需要写文件。
# （最初挂在 ExitPlanMode 上，北极星测试发现死结：plan 模式禁写文件，
#   闸门要求的 plan.json 永远写不出来。正确挂点是第一次改码动作。）
# exit 2 = 阻断本次工具调用，stderr 喂回给 agent 作为指令。
input="$(cat 2>/dev/null)"
fp=$(printf '%s' "$input" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {}) or {}
    print(ti.get('file_path') or ti.get('notebook_path') or '')
except Exception:
    print('')" 2>/dev/null)
case "$fp" in */.canvas/*|.canvas/*) exit 0;; esac

d=".canvas"
if [ -f "$d/plan.json" ] && [ -z "$(find "$d/plan.json" -mmin +30 2>/dev/null)" ]; then
  exit 0
fi
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cat >&2 <<MSG
改码前必须先有计划画布（code-canvas 规程，skill 在 $SKILL_DIR）：
1. 读 $SKILL_DIR/SKILL.md 与 schema.md，按 plan 模式产出 .canvas/plan.json——
   meta.mode:"plan"；将改/将删的卡放现状原文 + plan 标记；将新建的函数用
   幽灵卡（plan:"add"）；计划加/删的线打 wire.plan；
   step 序 = 治什么病 → 每处变更 → 风险预判。
2. python3 $SKILL_DIR/validate.py 清零后用 $SKILL_DIR/render.py 渲染成
   .canvas/plan.html，向作者呈交计划。
3. 卡 id 与 layout 将被事后的 diff 画布复用（同底图），起名时想着这一点。
产出计划画布之后再重新执行你要做的修改。
MSG
exit 2
