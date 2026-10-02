# hooks — 计划画布 / diff 画布的强制闭环

把「改码前交计划图、改完交对照图」从自觉变成闸门。挂载到目标仓库的
`.claude/settings.json`（或全局 `~/.claude/settings.json`）：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command",
                    "command": "bash /path/to/code-canvas/hooks/plan-gate.sh" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command",
                    "command": "bash /path/to/code-canvas/hooks/diff-gate.sh" }]
      }
    ]
  }
}
```

工作方式（两道闸门 + 一次对照）：

1. **plan-gate**（第一次改码前，PreToolUse:Edit|Write|MultiEdit）：
   `.canvas/plan.json` 不存在或超过 30 分钟 → 阻断这次改文件，指令 agent
   先按 SKILL.md plan 模式产出计划画布并请作者审批；写 `.canvas/` 本身放行。
   不要挂 ExitPlanMode：plan 模式禁写文件，闸门要的 plan.json 永远写不出来（死锁，
   北极星测试实测）。
2. **diff-gate**（Stop 前）：有计划画布、工作树有改动、但没有更新的
   `.canvas/diff.json` → 阻断收工，指令 agent 产出同底图 diff 画布并跑
   `compare.py --annotate`，把「计划内 / ⚠ 计划外 / ○ 计划未动」如实汇报。
   `stop_hook_active` 防死循环：拦一次后放行，不会无限逼问。

约定：画布产物统一放目标仓库的 `.canvas/`（plan / diff / diff-checked
各一对 json+html）。计划与 diff **同卡 id、同 layout**——同底图是对照的
前提，compare.py 会检查底图漂移。
