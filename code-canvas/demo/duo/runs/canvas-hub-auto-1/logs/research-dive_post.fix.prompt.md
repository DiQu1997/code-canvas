交付后的独立机械核对（`/home/user/code-canvas/code-canvas/demo/duo/check/spot_manuscript.py`，对 HEAD）发现底稿 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/research/manuscript-dive_post.md` 里有核不过的锚点或提交哈希（BAD），以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：

  BAD 底稿第 151 行  engine/scheduler.py:72  —— 文件不在 HEAD
  BAD 底稿第 151 行  engine/scheduler.py:75  —— 文件不在 HEAD
  ??? 底稿第 38 行  echo $rc > .status
  ??? 底稿第 92 行  grep -n "err(" validate.py
  ??? 底稿第 104 行  stdout.strip() != expected.strip()
  ??? 底稿第 131 行  cp {w} {hj} && {render} {hj} {hh} >> {lg} 2>&1 && { {sidecar}:; } || rc=6
  ??? 底稿第 141 行  w = /home/ubuntu/hub/.jobs/j20261123-101530-482/canvas.json
  ??? 底稿第 141 行  R = /home/ubuntu/hub/.repos/nano-vllm-155c1586
  ??? 底稿第 148 行  0 errors, K warnings
  ??? 底稿第 160 行  ["engine/scheduler.py", 94, "preempt"]
  ??? 底稿第 160 行  ["engine/scheduler.py", 88, "preempt"]
  ??? 底稿第 176 行  if validate; then cp && render && { sidecar :; } || rc=6; else rc=4; fi
  ??? 底稿第 182 行  lines[max(0, 行-4):行+3]

请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；??? 里真是引文却对不上原文的，照原文改。不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。