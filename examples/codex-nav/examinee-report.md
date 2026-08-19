# 考生报告 · codex-nav（领航卷）

> 零上下文 subagent 的原始交付报告。用时 22 分钟。评审结论：PASS（validate 0/0，9/9 卡逐字可溯源，架构断言抽查属实）。

## 关键决策

**Scope：领航图，不深潜。** codex-rs 是 ~102 个 crate 的 Rust workspace，任务是"整体认识"，命中 SKILL.md 规模闸门的第二档，直接按领航图规格做：卡片 = 子系统（9 张），卡内是入口函数/核心类型的原文连续摘选（6–12 行），file 标 `路径:起始行`；最后一步列出深潜主题清单。

**卡片选择**（先读了 cli/tui/app-server/core/protocol/sandboxing/rollout 的真实源码定位入口）：cli main()（argv[0] 分发的多工具入口）、tui handle_tui_event()、app-server process_request()、core submission_loop()（引擎心脏）、ModelClient::stream()、struct ToolCall、enum SandboxType、struct Submission、struct RolloutRecorder。读代码时发现的最重要架构事实——**TUI 和 exec 也不直连 core，而是进程内起一个 app-server 说同一套 JSON-RPC**——这成了故事线 A 的支点。apply-patch/MCP 没单独开卡（≤9 上限），收进 tools 卡的 term 和 caption。

**故事线 3 条**（架构关切，不按目录）：A 一次对话的主流程（cli→tui→app-server→core→client）、B 工具与安全边界（ToolCall→审批→三平台沙箱）、C 契约与持久化（SQ/EQ 的 Submission/Event、JSONL rollout）。8 步按读者问题排。

## validate 最终结果
0 errors, 0 warnings（三轮全零）。

## 截图自检修掉的问题
1. 总览取景裁切 + 背景卡不可见（fitAll 0.3 下限 × 5545px 宽画布）→ 6 列压 5 列、note 车道省一条
2. core→tools 调用线穿卡 → side:"left" 改左侧 C 形绕线
3. d-resume 标签被 note 盖 + 与 d-rollout 对向重复 → 删，保留单根代表线
4. d-sse/d-event 长距离绕线穿卡 → 显式指定两端 side
5. 总览 caption 方位描述与实际布局不符 → 改写

## 对 SKILL.md 的批评（已在后续版本吸收）
宽度预算不可知 / above 位 note 压卡 / 行宽 60-100 口径矛盾 / q 尾缀依赖块 / 深潜菜单与 caption 预算打架 / expand 跨步保留未文档化
