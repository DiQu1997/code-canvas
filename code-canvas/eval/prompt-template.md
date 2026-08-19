你有一个本地 skill：code-canvas，位于 {skill_dir}/。先读它的 SKILL.md，然后完全按照它的流程做事（它引用的 schema.md、validate.py、render.py 等都在同一目录）。

任务：{task}
目标仓库：{repo_dir}

我不在电脑前：SKILL.md 里需要向用户确认的环节（scope、读者画像等），你自行做出合理判断并继续，不要停下来等我。读者画像就当作"{audience}"。

产出要求：
- 全部产出放在 {out_dir}/ 目录：canvas.json、canvas.html，以及你做自检时的截图
- 不要修改 {skill_dir} 下的任何文件，不要做任何 git 提交
- 有无头 chromium 可用：{chromium}（截图示例：{chromium} --headless=new --disable-gpu --no-sandbox --hide-scrollbars --window-size=1500,900 --virtual-time-budget=5000 --screenshot=out.png "file:///路径#s1"）

最后返回一份报告：你的关键决策（scope 怎么定的、卡片和故事线怎么选的）、validate.py 的最终结果、截图自检发现并修掉的问题、以及你觉得 SKILL.md 哪里没写清楚导致你不确定怎么做。
