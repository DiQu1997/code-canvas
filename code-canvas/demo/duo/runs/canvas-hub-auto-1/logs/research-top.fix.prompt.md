交付后的独立机械核对（`/home/user/code-canvas/code-canvas/demo/duo/check/spot_manuscript.py`，对 HEAD）发现底稿 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/research/manuscript.md` 里有核不过的锚点或提交哈希（BAD），以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：

  BAD 底稿第 299 行  engine/llm_engine.py:49  —— 文件不在 HEAD
  BAD 不是一个 commit: 155c1586
  ??? 底稿第 55 行  <id>.prompt / .log / .status / .meta.json / .result.json / .timing.json
  ??? 底稿第 56 行  meta / regions / cards / wires / notes / steps / files
  ??? 底稿第 56 行  overview / scenarios / boxes / gaps / objects
  ??? 底稿第 64 行  subprocess.Popen(["bash","-c",script], start_new_session=True)
  ??? 底稿第 66 行  claude -p ... "$(cat <id>.prompt)"
  ??? 底稿第 66 行  $(cat <id>.prompt)
  ??? 底稿第 82 行  {ask, name?, git_url}
  ??? 底稿第 82 行  preview / zoom / engine / model
  ??? 底稿第 110 行  echo $rc > status
  ??? 底稿第 113 行  T0=...; { prep; } >> log && cd <缓存> && { gen; } || { 记 clone 耗时; echo $rc > status; }
  ??? 底稿第 115 行  Popen(["bash","-c",script], cwd=..., start_new_session=True)
  ??? 底稿第 167 行  [ -f <workdir>/canvas.json ]
  ??? 底稿第 168 行  embed_context.py canvas.json <仓库>
  ??? 底稿第 169 行  check_refs.py canvas.json <仓库>
  ??? 底稿第 171 行  {"git_url": ...}
  ??? 底稿第 172 行  echo $rc > <id>.status
  ??? 底稿第 236 行  160 + band × 280
  ??? 底稿第 259 行  preview / zoom / engine
  ??? 底稿第 262 行  src = {"kind": "git", "git_url": ".../nano-vllm"}
  ??? 底稿第 263 行  name = "nano-vllm"
  ??? 底稿第 264 行  engine = "claude"
  ??? 底稿第 267 行  job_id = j20261123-101530-482
  ??? 底稿第 271 行  rm -rf; git clone --depth 1 ... && touch nano-vllm-155c1586.ready
  ??? 底稿第 273 行  {"id": "j20261123-101530-482", "name": "nano-vllm", "ask": "讲讲调度器怎么工作", "source": "https://github.com/GeeeekExplorer/nano-vllm", "mode": "deep", "pid"
  ??? 底稿第 274 行  {"ok": true, "job": {...}}
  ??? 底稿第 286 行  meta.judgment / overview / scenarios / boxes / gaps / objects
  ??? 底稿第 299 行  {"id": "step", "name": "LLMEngine.step()", "file": "engine/llm_engine.py:49", "layout": {"col": 0, "band": 0}, "code": "def step(self):\n    seqs, is_
  ??? 底稿第 303 行  focus = [step, schedule, run, postprocess]
  ??? 底稿第 303 行  wires = [w-sched, w-run, w-post, d-token]
  ??? 底稿第 303 行  lines = [[step,2],[step,4],[step,5]]
  ??? 底稿第 310 行  {"git_url": "https://github.com/GeeeekExplorer/nano-vllm"}
  ??? 底稿第 406 行  cp ... && render ... || rc=6

请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；??? 里真是引文却对不上原文的，照原文改。不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。