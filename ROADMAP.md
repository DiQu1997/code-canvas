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
- 上下文一体滚动窗（2026-08-20，作者裁决"双滚动区割裂"）：上文+核心+下文
  连续排布单窗滚动、滚轮滑离核心区；vim 相对行号锚定核心区（0 不随滚动变）；
  核心区左侧色带；线锚感知容器滚动并夹在窗口边缘、滚动 rAF 重钉
- 移动端卡片流（2026-08-24，作者裁决"大图手机看不了，resize 救不了"）：
  <700px 同数据换投影——步进驱动垂直流（focus 序竖排 + ①② 序号节、
  点亮线变可点关系条、行级 note 内联卡下、总览=故事线目录）；块/沙盘/
  问答/上下文全功能复用（点击委托双绑定、drawWires 流态跳过）；
  「2D 画布」逃生口；desktop 阈值往返无损。mobile.mjs 13 项

## 评测基线

| 考卷 | 结果 | 溯源 | 用时 | 管线 |
|---|---|---|---|---|
| codex-nav 领航 | PASS | 9/9 | 22 min | 手工（Rust） |
| codex-exec 深潜 | PASS | 15/15 | 34 min | 手工（Rust） |
| nanovllm-deep 深潜 | PASS (warn:18卡>16) | 18/18 | **16 min** | **结构层先行** |
| nanovllm-deep 深潜 v2（2026-08-19） | **PASS 全绿** | 15/15 | 20.7 min | 结构层 + **数据结构四件套**：考生零上下文自发产出 KV 块池快照卡（三态、含惰性失效细节）、struct 线、step.detail；16 卡压线（上场超预算已改）。产物 examples/nanovllm-deep-v2/ |
| nanovllm-deep 深潜 v3（2026-09-15，盒子首考） | **PASS 全绿 0 warn** | 12/12 | 24.2 min | 全家桶管线：考生零上下文自发产出**机制级综述**（about 5/5、preview 3/3——"Gumbel-max 除以指数噪声后 argmax""抢占代价是 KV 重算换 decode 永远推进"段位）+ 沙盘 1 + 快照卡 + 上下文 6 文件 + 9 截图。$7.25 折算。产物 examples/nanovllm-deep-v3/ |

未跑：requests-nav。评分器容忍面已补：`#`/`\` 续行（sglang 审计与考试各暴露一处）。
真实用户产物审计：sglang 8/9（engine 卡省略中段违规——提取器管线根治此类）。

## 待办池（优先级由作者定）

1. P4 IDE 扩展 v1（模板跳转桥→扩展本体→作者本机装载验收）
2. 长任务控制面（监视器已上线）：超时看门狗（timeout 90m）、任务取消 ✕、
   failed 任务一键重跑（meta 已存 ask/来源）、生成完成推送通知；
   问答沉淀（好答案写回 canvas JSON）；画布库搜索；
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
  有没有 running 的任务 → **任务监视器**（作者裁决：长任务先要可观测）：
  生成任务 claude 改 stream-json 落盘（NDJSON，尾行 result 事件=指标信封，
  盒子实测）；GET /jobs/<id>/monitor：机械阶段进度（克隆→结构层→画布
  JSON→渲染→截图→入库，工作目录文件推断零成本）+ agent 活动流（轮数+
  最近 3 个工具调用）；任务行点击展开详情、5s 自刷；对正在跑的真实任务
  实测（阶段随 agent 推进变绿）。hub 测试 49 项。长任务后续（作者已过目
  待点单）：超时看门狗、任务取消 ✕、失败任务一键重跑、完成推送
- **2026-08-20**：任务监视器（阶段进度+活动流）→ 上下文第四轮迭代：
  一体滚动窗（作者三轮反馈线：±20行→限高滚动→开关全量→**单窗连续**）；
  作者 nano-vllm 学习画布重跑入库（16.5min · 47 turns · $4.66 折算 · 0 err 0 warn）
  → **块沙盘 v1**（作者点单：局部运行 block + 有价值输入集）：block.run
  =自包含确定性 harness（__INPUT__ 占位、run.note 披露蒸馏简化）+ 3-5 个
  预置输入（label/note/expected）；**expected 由 validate 真跑核验**
  （不符即 ERROR，掐死编造输出）；静态画布点 chips 看预录、连 hub 可改
  输入实跑（POST /run 隔离解释器 8s 超时）；「试」按钮进块条；黄金样本
  配前缀命中（3 输入）与 prefill 批组装（4 输入）两个沙盘；SKILL 5c 规程；
  测试 36+53 全绿
- **2026-08-24**：作者 nano-vllm 画布补装沙盘（agent 5.5min/$2.11：
  调度/缓存探测/块分配三块 13 输入，validate 真跑核验 0 err，
  机械 diff 证叙事零改动）→ **问答画布补丁**（作者点单：问答 agent
  可以边答边画）：回答尾部 canvas-patch 协议——show（即时镜头：聚焦+
  高亮，不落盘）+ add_note/add_wire/add_card/set_layout（持久：过
  validate 才写回，紫虚线标记问答产出，刷新查看/一键撤销还原含卡位）；
  协议只在 live 模式注入，含卡片清单与纪律（≤3 操作、不改已有卡、
  错引用整包拒收）；实弹验证：抢占回流问题 → agent 自主 show 带看 +
  沉淀一根 call 线（36.5s/$0.16）；顺手修存量 bug：canvasContext 在
  快照卡上崩（画布级问答在含 state 卡画布上一直是坏的）。hub 测试 59 项
  → **移动端卡片流**：作者裁决 2D 大图在手机上不可读、resize 非解——
  同一份 canvas 数据在 <700px 渲染成步进驱动的垂直卡片流（详见渲染器
  增量节）；新 tests/mobile.mjs 13 项全绿，桌面五套 36+4+6+18+59 不回归
- **2026-08-31**：作者报"新生成画布看不到上下文"——根因是**管线缺口**
  （--embed-context 在 SKILL 里只是可选注释，考生从不用）而非渲染 bug。
  根治：①embed_context.py 转正为正式工具（逐卡溯源核对+行漂移修正+
  超大文件上限，语言无关，单测 3 项）②SKILL 管线第 7 步"嵌入上下文
  （必选）"③validate 警告 file:line 卡无 files 映射 ④生成提示词显式
  该步骤。补装：codex 9/9 卡（卡路径相对 codex-rs 子目录而非仓库根——
  考生违规但可用子目录根兜住）；deepseek-harness 实为预览地图（8 分区
  无代码卡，上下文不适用，5 条线路均可点单深潜）。另修 hub 测试时间
  脆性：stale 重渲染用"回拨一天"模拟陈旧，仅当模板当天改过才成立，
  改为回拨到 2000 年。hub 59 项全绿 → **作者裁决：保障应在 service 不在
  skill**——生成任务结束后、写 status 前，serve 端机械执行 embed_context
  + 重渲染兜底（不信任 agent 守规程；embed 幂等、自动定位 monorepo 子目录
  根、预览图自动跳过；修 SKILL_DIR 含空格路径未引号）。embed_test 4 项 +
  hub 63 项全绿（含"agent 忘嵌入→服务端补上"端到端）
- **2026-09-14**：**综述层**（作者反馈"块描述太短、故事线开始前要 preview"）：
  ①card.about 卡级综述（≤160 字，卡头下常驻：干什么/哪个模块/什么机制/
  故事位置，重点卡必写）②region.preview 故事线预告（≤240 字：路径/涉及
  模块/核心算法/读完懂什么），渲染器在该线第一步自动亮出、手机总览目录
  逐线展示；问答上下文带 about；schema/SKILL/validate 预算齐；黄金样本
  手工补 3 预告 + 6 综述。排障连抓两个真 bug：①测试崩溃泄漏 serve 进程
  占固定端口 → 后续 run 静默对僵尸服务器测试（修：端口随 pid、崩溃收尸
  并吐已收集结果）②serve job_id 秒级碰撞 → 同秒多单共享 prompt/status/
  工作目录互相踩（修：毫秒后缀）。hub 63×3 稳定零泄漏；全套
  42+4+6+18+63+15 绿
  → **综述改机制优先**（作者裁决："不能只说干什么，重点是怎么干"）：
  about 上限 160→240、preview 240→320；规程改写——about 机制句必写
  （数据结构/判据/步骤），纯职责定位句不合格；preview 须把核心机制一段
  讲透。vllm 与 kafka 画布按新规程补装（agent 读源码核实，机制以真实
  实现为准）
- **2026-09-15**：作者问 service 的 agent 能否自发产出同质量综述 →
  补机械闸门（validate 覆盖警告 + 考试评分 2 软指标）→ 盒子首次开考连
  暴两个潜伏 bug（run.py 3.10 注解语法炸 3.8；无 skip-permissions 考生
  被静默锁死，$2.28 学费换考生一条有价值反馈：方法论没写运行前提）→
  第三考 **PASS 全绿 0 warn**：零上下文考生仅凭 SKILL 自发写出机制级
  综述，与专用提示词补装版同档。结论：service 的 agent 已同质量
- **2026-09-17**：**分区卡点单**（作者点单：预览图的每个模块要能一键
  生成新画布）：district 卡底部两按钮——「🗺 子预览」（模块内再拆逻辑
  线出地图）/「→ 代码细讲」（直接深潜）；ask 由 模块名+构成路径+定位句
  机械拼装，画布自动命名 <原图>-<模块>[-map]，同来源复用（src sidecar），
  confirm 防误触；hub 66 项全绿（子预览/深潜两种点单端到端），全套回归
  42+15+18+4+6 绿。至此预览图三级点单齐活：线路级（→深潜这条）、
  模块级（子预览/细讲）、问答级（边答边画）
- **2026-09-18**：**精读模式**（作者点单：画布内原地精读）：卡头「⛶」进入
  ——真卡搬进阅读容器（沙盘/上下文/问答/说明全保留），字号加大、全块展开
  连续读、容器内滚动与画布缩放天然隔离（覆盖层在 vp 之外）、代码可选中；
  点块条/块内代码=选块（蓝框）+ 侧栏同步 作用/解释/设计理由/不变量/失败
  情况（block.detail 新可选字段，旧数据兼容）+ 块内行级断言 + 问这个块
  （问答绑块）；背景画布淡化留位、连线暂隐、全局导航收起；Esc/✕ 退出
  逐项还原（折叠态/收起态/DOM 原位/镜头零位移）；窄屏解释下置。排障
  两笔：精读中 ensureVisible 追已搬走的卡把镜头甩飞（守卫）、退出
  appendChild 改变 DOM 序连锁殃及后续选择器（记 nextSibling 原位归还）。
  interactions 52 + mobile 18，全套 66+18+4+6 绿
  → **Codex 生成引擎**（作者点单：后台 agent 不止 claude code）：/generate
  加 engine(claude|codex)+model；codex 走 `codex exec --json`（ChatGPT
  订阅登录，不走 API）；表单引擎选择 + 模型 id（实测 gpt-5.6-sol ✔、
  gpt-6-astra 需 CLI ≥0.155 已升级 ✔）；任务行显示 codex(model)；指标
  信封是 claude 专属——codex 任务只有分段计时（如实降级）；问答桥仍
  claude（server 级 --cli 可换）。hub 71 项全绿；nano-vllm-codex 实弹
  首跑发出（质量待核验）
  → **评测污染发现**（诚实记录）：codex 首跑 nano-vllm 118 秒"全绿"
  ——核验发现整卷照抄黄金样本（about 6/6、preview 3/3、沙盘 2/2 逐字同；
  demo 与考题同仓库=答案泄露）。产物已删。连带修正：claude v3 考试的
  部分 about 也与 golden 高度相似，"零上下文自发"结论打折（其原创部分
  ——sampler Gumbel-max、prepare_prefill 沙盘等——仍真实）。教训入册：
  **nanovllm 考卷已污染，评 agent 能力必须用 golden 之外的仓库**；
  codex 干净重测已下单（requests，golden 无此仓库）
  → **codex 干净重测（requests，golden 外仓库）**：6.3 分钟出卷，
  validate 0 错、溯源 9/9 真原文、about 9/10 + preview 3/3 全为原创
  且达机制级（"prepare_request 合并会话级 cookie/header/认证得到
  PreparedRequest""按 URL 前缀挑适配器…末响应与 history 重组"）、
  state 卡 1、上下文 2 文件覆盖 9 卡（服务端兜底）。短板如实：沙盘
  0 个（claude 考生通常配 1-2）、过程一次 apply_patch 失误自行恢复。
  结论：codex(gpt-5.6-sol) 能产出合格深潜画布，速度约为 claude 的
  1/3 用时，完整度略低。产物 requests-codex 留库供作者检阅
  → **点单面板**（作者点单：预览里的深潜选择要能选模型）：三处点单入口
  （线路深潜/分区子预览/分区细讲）统一升级为面板——任务书 + 引擎
  （Claude / Codex+模型 id）+ 确认，选择记 localStorage 下次沿用，Esc
  可取消。hub 74 项全绿（引擎模型直达任务、选择记忆）
- **2026-09-25**：作者反馈三连修：① 首页任务列表限高 300px 滚动区（poll
  重渲染保滚动位置）；② 画布库按来源仓库分区（src sidecar 尾名，区标题+
  数量，区序=区内最新；无来源归「其他」）；③ 故事线点单整条打包——lineAsk
  机械收集 route 沿线全部版块（线两端+同线各步 focus）组联合任务书，点明
  跨版块交接是重点，无 ask 的线路步也可点单
  → **磁盘事故**：416 单任务的仓库克隆从不清理（Linux 内核一单 2G+），
  .jobs 攒 21G 写爆 45G 盘。修复：git 任务收尾必删克隆（工作目录小文件
  保留，有恢复实案）+ hub 启动 sweep_job_clones 清扫漏网（在跑不动）；
  盒子手工清 60 个克隆释放 21G（100%→55%）。hub 77 项全绿
- **2026-09-25（二）**：**共享仓库缓存**（作者裁决：不该重复 clone，用完不必删）：
  同一 git_url 共用一份浅克隆（.repos/<尾名>-<url哈希>，--repos-dir 指到
  /mnt/data 大盘），四种取用姿势——首单 clone、闲时刷新（fetch+reset+clean，
  失败用旧缓存）、同仓任务在跑则照用不折腾、缓存未就绪则等 .ready（30 分钟）；
  SPAWN_LOCK 保判定原子；启动清扫改为 30 天未用的缓存。昨天的"收尾即删"随之
  移除。顺带抓出存量 bug：gen 分号串没包 {}，clone 失败后 agent 照样在错误
  目录开跑。hub 82 项全绿（git 桩四场景+失败落 status）
  → **磁盘写满次生灾害**：codex 刷新令牌写满盘，auth.json 截成 0 字节
  （claude 凭证幸存）；当天 10 单受害任务重试全 401。处置：止损+device-auth
  重登流程+登录恢复后补单。缓存实测：linux 7 单共用一份 2.1G 克隆（在
  /mnt/data），根盘稳 55%
- **2026-09-26**：**读点取景**（作者反馈：进一步时画面太大，要直接 zoom 到这
  一节该读的那几行）：setStep 镜头不再整卡取景——读点 = lines ∪ unfold 块 ∪
  本步 note 及其锚点（note 解释的那行/卡头），按读点包围盒取景；缩到可读比例
  （桌面 .85、手机 .5）以下就改为怼住阅读起点（第一个读点），其余靠平移；
  无读点的老步子对准首个 focus 卡顶部。黄金样本各步比例 .85–1.15（原整卡
  取景 .4–.6），截图核验 ②③ 步构图。interactions 55 全绿；claude 引擎默认
  模型升 opus-5.5（盒子 CLI 升 2.1.283 实测）
  → **问答两修**（作者反馈）：① 中文输入法选词的回车被当成发送——keydown 判
  isComposing/229；② 问答说"这里看不到"——根因是 prompt 明写"只根据给出的
  上下文"且 hub 模式下 CLI 不在仓库目录。改为像 coding agent 一样研究：
  canvas_repo 从来源 sidecar 找本地仓库（盒子路径/共享缓存；缓存未建则后台
  clone 到临时目录再原子改名），CLI 以仓库为 cwd + 只读工具（Read/Grep/Glob），
  服务端注入研究规程（上下文只是起点，上下文没有的必须查仓库并附 文件:行号，
  禁止"看不到"）；无仓库时明说。踩坑：--allowedTools 是可变参数，prompt 放它
  后面被吞（真机报错抓出，prompt 改为紧跟 -p）。真机核验：问 nano-vllm
  "seq.num_blocks 怎么算的"，答出 sequence.py:56-57 的 property 及 num_tokens/
  block_size 的来源链（llm_engine.py/config.py），$0.12、3 轮。interactions 57 /
  hub 85 全绿
  → **问答回答 markdown 渲染**（作者：得支持 rich）：自包含单文件不引库，内置
  md() 子集（围栏代码/表格/列表/标题/段落 + 粗体/行内码/链接），先整体 HTML
  转义再转换（模型输出里的标签一律当文本）；表格/长代码横向滚动。interactions
  59 全绿（子集渲染 + 不吐原始 HTML 两断言），截图核验作者例子
- **2026-09-26（二）**：**算法总览板 overview**（作者：要一个高层、不涉代码细节、
  讲逻辑的 overview 让读者 know what to expect；且 demo 必须由零 history 的生产
  agent 做）：schema/SKILL 4c 规格（problem/idea/flow 直达链接/vars 真实标识符+
  谁写谁读/example 带数字/pitfalls）、validate 机械核对（链接指向真实卡块步、
  变量名须在卡片代码∪引用文件里出现）、merge_overview 修剪+闸门、模板覆盖板
  （首次自动展开、流程行直达、代码里变量悬停/点击回板）、serve 补装任务模式
  （POST /c/<name>/overview；agent 只产 overview.json；合并被拒 status 3）。
  **生产 agent 实测 vllm-sched**：claude opus-5.5、16 轮、132 秒、$0.78——
  9 行机制级 flow 全部带直达、11 个变量各有谁写谁读、两步带数字例子、5 条
  真陷阱；首版被闸门拦（is_prefill_chunk 不在画布快照里）→ 规则改为对
  卡片∪files 核对 + 服务端修剪而非整份作废，剩 10 个变量合并上线。
  测试：overview 18 / hub 91 全绿
- **2026-10-01**：**对标试验：架构地图提示词（linearuncle gist）vs 我们的预览地图**——
  同仓库（Code Canvas 自身 79cbff8）、同模型 opus-5.5、零 history 盒子 agent 各跑
  一遍：A（提示词，agent 现场自建可视化）19 分钟 $4.92，46 节点/82 关系/10 业务
  对象/5 场景 33 步，自建 check.py 核 351 引用；B（我们）4 分钟 $2.01，8 分区/
  5 路线。准确性：A 抽 15 步语义全对；B highlights 38/38 存在、1 处过度概括。
  A 强在证据分级、每步讲透、业务对象；我们强在快/省/稳定渲染/手机/可续接。
  A 顺带挖出我们 7 个真问题（全部核实）
  → **吸收落地**：schema「事实可信层」（ev 证据分级+need / gaps 三类待核实 /
  objects 业务对象 / steps[].trace 步卡），preview-spec v0.2 必写，validate 闸门，
  check_refs.py 依据机械核对（剔假出处、剔光则降级推断），模板徽标/虚点线/总览
  板扩为算法·架构总览/HUD 步卡；补装总览泛化为「升级到最新规程」通道
  （merge_upgrade 事实层逐字段锁死）；生成改服务端入库（done⇔已入库，rc 4/5/6）；
  问答取缓存 touch；hooks README/docstring/AGENTS 计数对齐
  → **生产 agent 升级三张 vllm 画布**（opus-5.5，各约 5 分钟，合计 $6.59）：
  vllm-map（10 待核实/8 业务对象/5 步卡）、vllm-sched（9/5/11）、vllm-kv（补出
  算法总览 + 10/5/8）；依据 224 条两道核对全过；抽 5 条语义核验全对（含发现
  vllm 自身 hash_block_tokens 文档声称 LRU 实无装饰器）。agent 还如实标出两张
  深潜画布的卡片是旧版 vllm 快照（drift + 线标待核实）——重生成待作者定
- **2026-10-02**：**vllm 全库 18 张画布按新规程处理完毕**（生产 agent，盒子上限流批跑脚本）：
  vllm-sched / vllm-kv 用 vllm HEAD 重生成，其余 16 张走升级通道；18 张校验 0 错误，
  依据全部两道核对通过，抽 8 条语义核验全对。第一轮 4 并发撞 Max 订阅会话额度
  （17 单成 7，3 单跑到一半被掐 ≈$9.6 白花）→ 第二轮脚本加「额度命中→重排+暂停
  20 分钟」、并发降 3，10/10 一次过。合计约 $61（API 价折算，订阅不按量计费）
  → **发现并修**：从零重生成的深潜图可信层比升级版还薄（vllm-kv 0 对象 / 5/13 步卡）
  ——SKILL 4d 只写"推荐"。改为必写（含 objects、≥半数步卡、不许拿 unknown 偷懒），
  validate 对深潜缺 gaps / 步卡不足半数 warn，生成提示词点名，升级缺层口径对齐；
  两张补升级后 vllm-kv 10/6/12-12、vllm-sched 9/5/11-15，watermark 偷懒的 unknown
  被纠正为有依据的行为条目
  → 待作者定：vllm-distributed / vllm-entrypoints / vllm-omni-entry / vllm-sched-budget
  被 agent 标为卡片代码旧于 HEAD，可重生成（每张约 $4-5 + 升级 $2）
- **2026-10-03**：**对标 Code Trail（作者另一个项目，同盒子 :8444）+ 逐段带读落地**。
  Code Trail = 线性带读课程：每块 ≤60 行、每块 ≤1 新概念、讲解逐段覆盖每一行、
  写作 agent 只能写自己的草稿、独立审读 agent（全新上下文、只读、error 必附证据）
  → 打回修改 ≤3 轮、仓库钉死 commit、持久队列 + 额度暂停。作者反馈：我们每步给一大块
  不知从何读起，Code Trail 的讲解正是需要的。实测我们每步 65-240 行 / 讲解 150-260 字
  且在底栏，Code Trail 每块 8-57 行 / 750-1600 字且贴代码
  → **steps[].read 逐段带读**：一张卡一段连续代码 ≤60 行，walk 首尾相接覆盖每一行
  （validate 强制），focus 问题 / answer 回答 / takeaway 设计点；桌面范围外变暗、←→
  段间推进、讲解卡贴代码右侧、镜头跟随；手机「代码段 + 讲解」交替；SKILL 必写，升级
  通道列为最重要补写项。walk.mjs 17 PASS。**生产 agent 升级 vllm-sched**（opus-5.5，
  7 分钟 $2.52）：12/12 代码故事步写出带读，每步 20-59 行 / 4-9 段 / 615-1033 字，
  抽两步逐段对照代码语义全对。顺带：手机卡片流浮动按钮竖排挡代码 → 只留问画布 +
  总览横排。待办：其余画布补带读、独立审读 agent、画布钉 commit
