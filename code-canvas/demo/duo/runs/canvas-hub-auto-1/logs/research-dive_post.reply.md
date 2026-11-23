1. **底稿路径与规模**：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/research/manuscript-dive_post.md`。去掉空白约 1.4 万字符（含锚点和代码引文），汉字约 5 千。结构是：先给全貌，再讲框内部件和状态，然后逐个回答你列的问题，接贯穿例子（主线加 4 个变体），最后是 6 页源码页建议、没查到的清单和可改进处。

2. **研究范围**：
   - 读了 `serve.py` 的 `post_pass`、`spawn_generate`、`_validate_and_write`、`_run_snippet` 和 `maybe_rerender`，以及 `embed_context.py`、`check_refs.py`、`render.py` 全文、`validate.py` 的 ERROR 清单和关键段落。
   - 另读了 `SKILL.md`、`schema.md`、`ROADMAP.md` 相关条目、`eval/run.py`，以及 `embed_test.py`、`evidence.mjs`、`hub.mjs` 里的用例。
   - 看了 13 个提交说明（读的 `git show`，未用 `log -L`）：`690022d`、`40781a8`、`4b43d42`、`1c9fabe`、`79cbff8`、`f4bbe29`、`d481d07` 等。
   - 用自己写的小脚本在 `demo/nano-vllm.json` 里的真实数据上复算了例子，没有运行仓库里的任何脚本。
   - 3 个最有价值的 git / 文档发现：
     - 子目录根定位的来历：`690022d` 加了仓库根自动定位，起因是 `40781a8` 里 codex 的卡路径相对 `codex-rs/` 子目录；`ROADMAP.md:144` 说是「考生违规但可用子目录根兜住」。
     - embed 的 `norm` 忽略 `//`、`#`、`\`：来自 `eval/run.py:41` 的注释，是为了容忍超宽行换行重排，`ROADMAP.md:52` 说这是被真实案例逼出来的。
     - embed 的退出码被忽略：`690022d` 写了「预览图等无代码卡时自动跳过不碰状态码」。
   - 额外发现：
     - embed 会猜子目录根，check_refs 不会（`code-canvas/check_refs.py:43`），两者是不同提交加的。
     - validate 从不读仓库，所以编造的卡代码拦不住。
     - embed 失败会让 validate 的语料变小，间接导致 `rc=4`。
     - 沙盘真跑只是 `python -I`，不是文件系统或网络沙箱，且没设 `cwd`。
     - embed 注释说「唯一匹配点」，代码取的是第一个命中。

3. **机械核对**（脚本在 `work-research-dive_post/verify.py`，用 `python3 -I` 跑）：

   | 项目 | 通过 / 总数 |
   |---|---|
   | 锚点 | 123 / 123（只查文件存在、行号不越界，另人工扫过各行内容；`code-canvas/tests/evidence.mjs` 的行号是人工核后改成 80-86 的，脚本没有单独复核） |
   | 提交哈希 | 7 / 7（含 HEAD，且改动了所述文件） |
   | 「」引文 | 37 / 37（逐字在仓库文件或提交说明里；非原文的我改用了“”） |

4. **「为什么」的来源等级**（按标签统计，一处可带多个标签）：【注释】10、【提交】9、【文档】3、【测试】2、【推断】9。
   - 以上合计 33 处，【推断】占 27%。【观点】6 条（可改进处），【设】8 处。
   - 【推断】集中在：embed 为何排在 validate 前、降级为何不拒收、漂移多半源于 agent 报错行号、validate 崩溃也会 `rc=4`、缺口来自分工、`lstrip("./")` 的影响。

5. **没查到为什么**：
   - 生成收尾为什么真跑沙盘（不带 `--no-exec`），而问答补丁带了；
   - 沙盘为什么不设 `cwd`，而 `/run` 设了 `/tmp`；
   - `n + 40` 的 40；
   - check_refs 的 ±3 行窗口；
   - 服务端 validate 为什么不传 `--strict`；
   - embed「唯一匹配点」与取第一个命中的出入是有意还是疏漏；
   - check_refs 为什么不跟 embed 一样猜子目录根。

6. **最弱的地方**：
   - 变体 B 的前提是我设想真实仓库把代码放在 `nanovllm/` 包下，离线无法核实，已标【设】。
   - 贯穿例子以仓库自带样板 `demo/nano-vllm.json` 为替身，它是旧格式，没有 boxes、scenarios、trace，所以沙盘次数（7 次）、5 文件 / 9 卡、行号和 93 行这些数字是真的，但「agent 真实会产出什么」是设想。
   - 沙盘真跑的风险只是读代码推断，没有实际跑过。
   - 与 `manuscript-dive_agent.md` 有一处出入：它写“嵌入 4 文件 / 9 卡”，样板实际是 5 文件 / 9 卡，我在 §3 开头标出了，需要讲解员统一。
   - 篇幅压到上限，砍掉了 embed 的 400KB 字符数怪癖和一些次要的 warn 细节。