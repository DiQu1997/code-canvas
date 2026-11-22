交付后的独立机械核对（`/home/user/code-canvas/code-canvas/demo/duo/check/spot_manuscript.py`，对 HEAD）发现底稿 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/vllm-auto-1/research/manuscript-gpu.md` 里有核不过的锚点或提交哈希（BAD），以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：

  BAD 底稿第 289 行  cudagraph_utils.py:580-587  —— 路径有歧义（2 个同名）
  BAD 底稿第 290 行  model_runner.py:2172  —— 路径有歧义（2 个同名）
  BAD 底稿第 296 行  model_runner.py:2377  —— 路径有歧义（2 个同名）
  BAD 底稿第 297 行  async_utils.py:139-140  —— 路径有歧义（2 个同名）
  BAD 底稿第 298 行  async_utils.py:193  —— 路径有歧义（2 个同名）
  BAD 底稿第 302 行  core.py:785  —— 路径有歧义（4 个同名）
  ??? 底稿第 6 行  request_id = chatcmpl-9f1c…-a1b2c3d4
  ??? 底稿第 17 行  [max_num_reqs, 1]
  ??? 底稿第 32 行  [max_num_reqs, max_blocks]
  ??? 底稿第 46 行  input_ids / positions / seq_lens / block table / slot_mapping
  ??? 底稿第 71 行  all_token_ids[槽, :]
  ??? 底稿第 77 行  1024 × 131072 × int32 ≈ 512 MiB
  ??? 底稿第 82 行  prefill_len ≥ prompt_len
  ??? 底稿第 111 行  num_computed_prefill_tokens=0 < prefill_len=20
  ??? 底稿第 114 行  [0, 20, 20, 20, …]
  ??? 底稿第 115 行  [prompt 的 20 个 token]
  ??? 底稿第 122 行  seq_len == prefill_len
  ??? 底稿第 123 行  num_computed_tokens 0→20
  ??? 底稿第 123 行  num_computed 20→21
  ??? 底稿第 129 行  (not draft_tokens.get(r), num != decode_query_len, num)
  ??? 底稿第 130 行  idx_mapping=[1023, 1022]
  ??? 底稿第 130 行  query_start_loc=[0, 1, 21]
  ??? 底稿第 138 行  if seq_len <= prefill_len: return
  ??? 底稿第 140 行  input_ids[logits_start] = last_sampled_tokens[槽]
  ??? 底稿第 140 行  first_logit_seq_pos = 21−1 = 20 ≥ 20
  ??? 底稿第 155 行  pp_size + async_scheduling_depth − 1
  ??? 底稿第 197 行  git log -S'def sample_tokens'
  ??? 底稿第 241 行  self.output_copy_event = …
  ??? 底稿第 285 行  BLOCK_SIZE=next_power_of_2(num_speculative_steps + num_new_sampled_tokens)
  ??? 底稿第 341 行  query_len − num_rejected

请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；??? 里真是引文却对不上原文的，照原文改。不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。