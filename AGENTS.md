# AGENTS.md — 在本仓库工作的规程

> 新 session 必读顺序：本文件 → ROADMAP.md（计划与进度）→ HANDOFF.md（完整认知：
> 定位、设计理由、被否方案、协作文化细节）。改动前先跑下面的环境自检。

## 项目一句话

**AI 写代码，画布是人保持指挥权的地方。** Code Canvas 把读代码/审改动/批计划
变成 2D 交互画布：预览地图（逻辑分组）→ 深潜画布（真实代码）→ 计划/diff 画布
（同底图对照）。产物自包含 HTML，宿主是云盒子上的 hub。

## 环境

- 云盒子：`ssh -i ~/.ssh/diqu-oci.key ubuntu@129.146.63.59`（Python 3.8！代码须兼容）
- Hub（tailscale 域内）：`http://100.74.9.77:8340/`，systemd 服务 `canvas-hub`
- 部署：`rsync code-canvas/ → ~/code-canvas/`；serve.py 变更后 `sudo systemctl
  restart canvas-hub`；模板变更**无需**重渲染存量画布（serve 时自动重渲染）
- 本机测试依赖：playwright 经软链 `code-canvas/node_modules/playwright`
  （npm 因系统时钟证书问题装不了包，勿删软链）；chromium 用
  `CANVAS_TEST_CHROMIUM="$HOME/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"`

## 环境自检（改动前全绿）

```bash
cd code-canvas
python3 validate.py demo/nano-vllm.json                 # 0 errors
python3 render.py demo/nano-vllm.json /tmp/t.html
node tests/interactions.mjs demo/nano-vllm.html         # 59 PASS
node tests/mobile.mjs demo/nano-vllm.html               # 18 PASS（手机卡片流）
node tests/note-follow.mjs                              # 4 PASS
node tests/personalize.mjs demo/nano-vllm.html          # 6 PASS
node tests/hub.mjs                                      # 96 PASS
node tests/plan.mjs                                     # 18 PASS
python3 tests/extract_test.py                           # 19 PASS + 2 SKIP（部分依赖 /tmp clone，缺则 SKIP）
python3 tests/embed_test.py                             # 4 PASS（上下文嵌入契约）
node tests/overview.mjs                                 # 19 PASS（算法总览板：校验/升级合并/交互）
node tests/evidence.mjs                                 # 16 PASS（事实可信层：证据分级/待核实/业务对象/步卡/依据核对）
node tests/walk.mjs                                     # 17 PASS（逐段带读：覆盖校验/段内导航/讲解卡/手机交替排版）
python3 tests/video_test.py                             # 12 PASS（视频脚本校验契约；渲染需本机 manim venv）
```

## 工具链（code-canvas/）

| 工具 | 职责 |
|---|---|
| extract.py | 结构层机械提取（函数卡/call 线/diff 映射/布局，零 LLM 毫秒级；--merge 增量保留叙事） |
| preview.py | 目录级机械摘要（研究材料，不是产物；--recommend 一次轻 LLM） |
| embed_context.py | 画布上下文全文嵌入（溯源核对/行漂移修正/子目录根定位；serve 生成收尾自动跑） |
| check_refs.py | 依据机械核对：[文件, 行, 符号] 须在仓库里成立，核不过的剔除，依据剔光的步卡降级推断（agent 自查 + 服务端入库前兜底） |
| video.py / video-spec.md | 精读带读视频：agent 写 video.json（幕+旁白），video.py 校验（py3.8）/ 渲染（Manim + macOS say 配音，本机） |
| merge_upgrade.py | 升级合并：事实层（代码/行号/卡/线/步骤结构）逐字段锁死 → 修剪编造变量 → validate → 写回（serve 升级任务收尾用） |
| preview-spec.md | 研究型预览地图规程（逻辑分组，agent 研究产物） |
| validate.py | 渲染前强制关卡（ERROR=结构坏必须清零；warn=预算提示逐条自查） |
| render.py | JSON → 自包含 HTML |
| compare.py | 计划 vs diff 同底图对照（--repo 文件覆盖核对防瞒报；exit 2=有偏离） |
| serve.py | 单画布问答桥 / --hub 常驻服务（生成/问答/下载/删除；只绑 tailscale IP） |
| hooks/ | 计划闭环闸门（**已验证、未挂载**——作者裁决：耗 token，等日用价值明确） |
| eval/run.py | 零上下文考试（改 SKILL.md 必跑一张考卷对基线；不许为过题加提示） |

## 质量闸门（每次交付）

1. validate 0 ERROR；渲染后**截图自检**过 SKILL.md 六条清单（顺序走步，不用 #sN 直达）
2. 五套 Playwright 全绿；改渲染器必重渲染全部 demo
3. 卡片代码溯源：深潜类画布 ≥90% token 流可溯源（重排须披露；预览地图是索引物除外）
4. **独立核验 agent 产物**（考生/施工/研究 agent 的输出都要抽查核实，如实报告）
5. git：每步工作 commit + push（`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`）；
   交付后更新 ROADMAP.md 进度日志

## 协作文化（作者明确要求）

- 中文交流，产物中文优先；作者常在手机看结果（hub 是第一交付面）
- 作者抛方向 → 给**有立场的推荐**（不罗列选项）→ 确认后**自主执行到底**，中途不问琐事
- **诚实高于一切**：测试结果如实报、失败不粉饰、发现自己踩了自己写的失败模式要明说
- token 纪律：作者对成本敏感——重活先给预览/预估，能机械做的不用 LLM
- 宁滥勿缺：交接类文档把理由和被否方案一起写
