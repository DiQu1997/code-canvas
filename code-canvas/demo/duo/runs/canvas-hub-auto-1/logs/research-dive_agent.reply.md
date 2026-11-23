底稿写好了：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/research/manuscript-dive_agent.md`。全文约 2.4 万字符，其中汉字 6600 个，在 1.5 万字以内。

**1. 范围**
- **读了的文件**：
  - `SKILL.md` 全文、`schema.md`、`reader_check.py`、`check_refs.py`、`embed_context.py`、`extract.py`
  - `validate.py` 的规则主体
  - `serve.py` 里拼提示词、`post_pass`、监视器那几段
  - `demo/nano-vllm.json`、`eval/README.md`、`DESIGN.md`、`tests/hub.mjs`
- **看了的提交**：约 10 个（`f4bbe29`、`4b43d42`、`40781a8`、`aa37529`、`6d647ee`、`d481d07`、`9480488` 等），外加 `f4bbe29` 之前的 SKILL 旧版。
- **从 git 历史挖出的 3 个最有价值的"为什么"**：
  - `40781a8`：agent 从不执行 SKILL 里的可选项（embed），所以升成必选并由服务端兜底。
  - `aa37529`：agent 完全自由时会偷懒（vllm-kv 0 个业务对象、5/13 个步卡），所以加了 warn。
  - `f4bbe29`：对比 diff 看到提示词的 tail 从"步骤 1)2)3)"换成"其余由你判断"。
- **重要发现**：SKILL 第 6 节写的"不可商量"，实际上只有 validate 的 ERROR 有机器闸门（§2.7 有逐条对照表）。

**2. 机械核对**
- 锚点 165 个，通过 165。
- 符号级断言 125 条，通过 125。
- 逐字引文 80 条，通过 80。
- 提交哈希 5 个，通过 5，其中 4 个还核了改动文件。

**3. "为什么"的来源等级**
- 注释 13、提交 5、文档 11、测试 2、推断 10，共 41 条；推断占约 24%。
- 另有 5 条【观点】。
- 引文核对在首轮抓到 6 处不是逐字的引文，已改掉。

**4. 没查到为什么**（§7）
- 生成 agent 为什么没有 `--max-turns` 和超时。
- `reader_check` 为什么默认 sonnet。
- `meta.judgment` 为什么只是 warn。
- 行宽为什么有三个数（90 / 100 / 60）。
- 契约里的"focus 卡 ≤4"为什么没有机械检查。
- `DONE` 是给谁看的。
- SKILL 从管线改成判断清单的动机（提交只有标题）。
- `reader_check` 为什么输出文本而不是结构化格式。

**5. 最弱的地方**
- **例子是我设想的**。§2.2 的 `meta.judgment` 和 §3 的整趟 agent 都是【设】，仓库里没有一份真实的 v0.2 产出可对照。样板 `demo/nano-vllm.json` 是旧格式，没有 judgment。
- **几处只能靠推断**：
  - "reader 隔离靠提示词而不是沙盒"。
  - "超时后临时目录残留"。
  - "agent 自己找工具路径可能出错"（SKILL 命令是相对路径，agent 的 cwd 是缓存）。
- **没读 `canvas.html` 的渲染细节**。"judgment 不显示"是靠 grep 无结果加 SKILL 自述得出的。
- **`validate.py` 只读了规则骨架**。"没有检查 ≤5 列和像素字段"是 grep 的结论，不是通读后的结论。
- **验证脚本**：放在 `research/work-research-dive_agent/verify.py`，可用 `python3 -I verify.py <仓库路径> <底稿路径>` 重跑。
- **体量偏小**：约 6600 个汉字。如果讲解员想要 extract 的 `--merge` 或 validate 沙盘真跑的细节，我可以补。