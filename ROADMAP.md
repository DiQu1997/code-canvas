# ROADMAP.md — 计划、验收标准与进度

> 活文档：每次交付更新。设计理由与被否方案见 HANDOFF.md；工作规程见 AGENTS.md。

## 终局与主线

AI 写代码时代，作者对仓库的认知不退化成"agent 的转述"。控制权来自三个时刻
共用一张底图：**平时**（活画布/预览地图）· **动手前**（计划画布，作者批图不批
文字）· **动手后**（diff 画布 + 机械对照）。核心不变量：**计划 vs 实际的可对照性**。

**北极星测试**：真实修改中埋一处计划外变更，作者只看两张图（不读线性 diff）
限时指出多动了什么。每个大版本交付前跑一次。

## 阶段状态

| 阶段 | 状态 | 验收结果 |
|---|---|---|
| P1 canvas hub | ✅ 2026-08-17 | 生成优先表单（git/路径/粘贴三源）、问答两级、下载、删除、亮色、移动端；systemd 常驻；真机端到端（git URL→15min→画布入库，溯源 10/10） |
| P1.5 生成优先改版 | ✅ 2026-08-17 | 作者裁决"首页必须是生成入口"；hub 测试 32 项 |
| P2 计划画布 | ✅ 2026-08-18 | schema v0.2（plan/幽灵卡/plan_delta）；compare 三层对照（卡级/计划线/--repo 文件覆盖防瞒报）；北极星测试通过：闸门逼出全闭环、埋雷 agent 拒绝配合并主动披露（暴露覆盖洞→已补）；hooks 挂点修正（ExitPlanMode 死锁→首次改码）。**hooks 已验证未挂载（作者：耗 token 暂缓）** |
| P3 结构提取器 | ✅ 2026-08-18 | extract.py（ast 后端、--merge 增量协议、21 项契约测试、v1/core 0.08s、黄金 9/9 交叉覆盖）；零上下文考试 nanovllm-deep **PASS：18/18 溯源、16 分钟**（对比旧基线 22/34 分钟），考生自行走通结构层管线 |
| 预览地图 | ✅ 2026-08-19 转正 | 三轮形态迭代后作者验收：**研究型逻辑地图**（agent 真研究、逻辑分组、highlights 核实）。转正三件套落地：①preview-spec 并入 SKILL.md 规模闸门（取代旧领航图规程）②hub 表单加画布类型（深潜/预览），/generate 支持 preview + src 来源 sidecar ③线路步「→ 深潜这条」一键点单（同来源复用）。hub 测试 42 项全绿；真机验收：hub 预览点单 → vllm-map 8.4 分钟（$3.07 折算）、9 逻辑分区（v1/core 拆二、三目录合一）、5 线路全带 ask、highlights 42/42 核实、0 ERROR、点单按钮真图在位；归档 examples/vllm-map/ |
| P4 IDE 扩展 | 📋 方案已过目 | VS Code 薄壳复用全部资产；杀手交互=点卡跳 file:line；结构层<2s；v1 不做叙事生成/marketplace |
| P5 GitHub App | ⏸ 等团队拉力 | PR 自动 diff 卷 |

## 渲染器/体验增量（2026-08-18~19，作者反馈驱动）

- 阅读顺序：focus 序＝阅读序（①②③ 徽标随步换）、点亮线方向箭头
- step.detail：caption 80 字镜头不动，≤300 字详解点开自取
- 线路观感：route 线沉底层 SVG、站心浅弧、总览静默、图例色点直达
- HUD 磨砂：顶栏/提示/底栏三面板，画布穿行不遮糊
- 移动端：resize + HUD 收纳 pass（步进条两行、focus 缩放下限 0.12、抽屉全屏）

## 评测基线

| 考卷 | 结果 | 溯源 | 用时 | 管线 |
|---|---|---|---|---|
| codex-nav 领航 | PASS | 9/9 | 22 min | 手工（Rust） |
| codex-exec 深潜 | PASS | 15/15 | 34 min | 手工（Rust） |
| nanovllm-deep 深潜 | PASS (warn:18卡>16) | 18/18 | **16 min** | **结构层先行** |
| nanovllm-deep 深潜 v2（2026-08-19） | **PASS 全绿** | 15/15 | 20.7 min | 结构层 + **数据结构四件套**：考生零上下文自发产出 KV 块池快照卡（三态、含惰性失效细节）、struct 线、step.detail；16 卡压线（上场超预算已改）。产物 examples/nanovllm-deep-v2/ |

未跑：requests-nav。评分器容忍面已补：`#`/`\` 续行（sglang 审计与考试各暴露一处）。
真实用户产物审计：sglang 8/9（engine 卡省略中段违规——提取器管线根治此类）。

## 待办池（优先级由作者定）

1. P4 IDE 扩展 v1（模板跳转桥→扩展本体→作者本机装载验收）
2. 生成完成推送通知；问答沉淀（好答案写回 canvas JSON）；画布库搜索；
   用量汇总视图（/stats：按日/按画布聚合 token 与折算成本）
3. requests-nav 基线；模型动物园类模块的 highlights 信号（__init__ 导出/git 频率）
4. 已知渲染债：note 车道线穿行、同卡多 above note 重叠、#sN 直达状态与顺序走不一致

## 会话日志

- **2026-08-17**：交接接手（环境自检全绿）→ vllm 领航图样张 → P1 hub 上线 →
  P1.5 生成优先改版 → P2 计划画布全套 → 画布级问答 → 返回链接
- **2026-08-18**：下载离线版 → 亮色主题 → sglang 溯源修复+自动重渲染 →
  问答选中态 → P2 北极星测试（含 hooks 挂点修正、--repo 覆盖核对）→ hooks
  摘除（作者裁决）→ 移动端 pass → P3 提取器+考试验收 → git 仓库化
  （github.com/DiQu1997/code-canvas，公开）→ 阅读顺序指引 → step.detail
- **2026-08-19**：预览地图三轮迭代 → 线路观感 → HUD 磨砂 → 画布删除 →
  本文件与 AGENTS.md 建立 → **数据结构表达 v1**：实例快照卡
  （kind:"state"：record/array/map 节点、每步全量状态、渲染器自动 diff——
  数组按值比对新值标绿、record/map 按 key 变更标琥珀、state.note 讲变化）
  + struct 关系线（紫虚点，label 必带）+ SKILL"数据结构四件套"规程；
  nano-vllm 黄金样本加 KV 块池快照（基线/分配+前缀命中/抢占回收三态），
  "共享块不清空、账本不动"的不变量肉眼可见 → **快照规程验收**：
  nanovllm-deep v2 考试 PASS 全绿（考生自发产出快照卡/struct 线/detail，
  含惰性失效细节，守恒检查通过；归档 examples/nanovllm-deep-v2/）→
  **上下文窥视**：canvas JSON 顶层 files 映射（文件全文），卡内「▲上文/
  ▼下文」展开条 ±20 行/收起，上下文淡化无行号、锚点钉在摘选行不动；
  extract.py --embed-context 机械生成；nano-vllm demo 富化（5 文件 24KB）
  → 上下文改开关式（一点全量进滚动区，卡高恒定 ≈2.3×核心，滚轮/触屏
  让位原生滚动）→ **用量观测**：claude -p 全链路 --output-format json，
  生成任务分段计时（clone/claude）+ token/折算成本落 .jobs/<id>.{timing,
  result}.json 并显示在任务行；问答指标进 sidecar + 抽屉小字；考试指标
  归档 examinee-metrics.json；preview --recommend 打印指标。成本注明
  为 API 价折算（订阅不按量计费）→ **预览地图转正**（作者验收形态）：
  SKILL 规模闸门改为"先预览后深潜"（研究先行/逻辑分区/线路带 ask，
  旧领航图规程删除）；hub 表单加画布类型（预览默认名 -map、粘贴代码
  不可预览）；/generate 记 src 来源 sidecar（删画布连删）；渲染器底栏
  「→ 深潜这条」：step.ask + 来源 → 确认后一键 /generate 同源深潜；
  存量画布补 src sidecar；真机验收 vllm-map 点单（走新预览链路）。
  注：首张 vllm 研究地图 demo 未归档已丢失（教训：验收样张进 examples/）
  → **事故修复：restart 杀任务**——部署重启 canvas-hub 连带杀死了作者
  在跑的生成任务（systemd 默认 KillMode=control-group 杀整个 cgroup；
  start_new_session 逃不出 cgroup）。三层修复：①服务单元加
  KillMode=process（restart 只杀 server，真实 restart 下任务实证幸存）
  ②job meta 记 pid，list_jobs 对 无status+pid已死 如实标 failed(中断)
  （hub 测试 43 项）③被杀任务重新下单补回。教训：部署前先看 /jobs
  有没有 running 的任务
