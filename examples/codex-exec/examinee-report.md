# 考生报告 · codex-exec（深潜卷）

> 零上下文 subagent 的原始交付报告。用时 34 分钟。评审结论：PASS（validate 0/0，15/15 卡 token 流可溯源——换行重排已披露，逐字符核查两处分叉均为注释续行符，零编造）。

## Scope 决策
主题明确（一条 shell 命令从模型提出→沙箱执行→结果返回），通读相关文件约 5700 行，判定策展切片 <2000 行，走细粒度管线。纳入：派发、execpolicy 审批、沙箱选型与 argv 包装、spawn/输出回收、Denied→无沙箱升级重试、回填 function_call_output。排除（在 note/caption 中明示）：unified_exec/PTY、apply_patch、MCP、Windows 细节。

## 结构决策
15 卡、4 故事线、17 线、11 步。空间叙事：**上排去程（派发→审批→包装），下排"机房层"回程（spawn→收流→判决→回模型），回程从右往左流，SandboxErr::Denied 是全图唯一的回头路**（step ⑨ 主角）。ToolOrchestrator::run 380 行拆两张连续摘选卡。data 线只画 4 根值旅程。工程实践：写 build.py，所有行号锚点在换行重排后的最终卡文上**程序化搜索计算**，消灭手数行号的整类错误。

## validate 最终结果
0 errors, 0 warnings。

## 截图自检修掉的问题
1. 总览两侧被裁（7861px vs 0.3 缩放下限）→ 5 列×2 排重排 + 超 84 字符行换行收窄 + 去掉非 0 列左置 note
2. note 压 note / above 位 note 不随动态下推被展开卡顶穿 → 删 3 条 note 并入块 explain
3. 点亮的线一端出画 → 调整点亮步 + 拆步（10→11）
4. d-raw 绕行穿卡 → from 端 side:"left"

## 对 SKILL.md 的批评（已在后续版本吸收）
画布总宽预算未提（逆向发现 ≤5 列硬约束）/ 行宽三处口径不一（采用"保持 token 的换行重排+披露"解释）/ note 摆位安全规则靠试错 / 步骤状态累积 vs 直达不一致 / 长函数拆多卡合法性未置可否 / file 行号格式是否通用靠猜
