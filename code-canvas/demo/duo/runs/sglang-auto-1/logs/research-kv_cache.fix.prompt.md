交付后的独立机械核对（`/home/user/code-canvas/code-canvas/demo/duo/check/spot_manuscript.py`，对 HEAD）发现底稿 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/sglang-auto-1/research/manuscript-kv_cache.md` 里有核不过的锚点或提交哈希（BAD），以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：

  BAD 底稿第 162 行  base.py:259  —— 路径有歧义（35 个同名）
  BAD 底稿第 252 行  unified_radix_cache.py:1669  —— 路径有歧义（2 个同名）
  BAD 底稿第 300 行  schedule_batch.py:1215  —— 路径有歧义（2 个同名）
  ??? 底稿第 51 行  torch.zeros((size+1, max_context_len), dtype=torch.int32)
  ??? 底稿第 51 行  write(indices, values)
  ??? 底稿第 52 行  free_slots = range(1, size+1)
  ??? 底稿第 58 行  arange(1, size+1)
  ??? 底稿第 108 行  req.prefix_len = device_prefix_len
  ??? 底稿第 116 行  FIXME: To work around some bugs in logprob computation, we need to ensure each
  ??? 底稿第 116 行  request has at least one token. Later, we can relax this requirement and use `input_len`.
  ??? 底稿第 117 行  git show 1e495e0847:python/sglang/srt/managers/schedule_batch.py
  ??? 底稿第 117 行  Need at least one token to compute logits
  ??? 底稿第 150 行  alloc_token_slots(tree_cache, extend_num_tokens)
  ??? 底稿第 154 行  [101..105, 2001..2015]
  ??? 底稿第 154 行  kv_allocated_len = kv_committed_len = 20
  ??? 底稿第 158 行  _storage(tree_cache).take_tokens(num, make_room=_room_in(tree_cache))
  ??? 底稿第 185 行  kv_indices_orig = req_to_token[row, :len]
  ??? 底稿第 191 行  req_to_token_pool.write((row, slice(cache_protected_len, len(new_indices))), new_indices[cache_protected_len:])
  ??? 底稿第 201 行  dup_start = max(0, prev_prefix_len - total_prefix_length)
  ??? 底稿第 205 行  dup_start=max(0, 5-0)=5
  ??? 底稿第 211 行  so LRU eviction can discard output KV without
  ??? 底稿第 211 行  also losing the reusable prompt KV.
  ??? 底稿第 211 行  hit_count drives eviction order, so an
  ??? 底稿第 211 行  extra bump would silently promote every prompt node.
  ??? 底稿第 216 行  node_end > inserted_len
  ??? 底稿第 223 行  alloc_token_slots(tree_cache, bs * token_per_req)
  ??? 底稿第 224 行  req_to_token_pool.write((req_pool_indices, locs), out_cache_loc)
  ??? 底稿第 224 行  seq_lens = seq_lens + 1
  ??? 底稿第 234 行  release_kv_cache(req, tree_cache, checkpoint=...)
  ??? 底稿第 236 行  min(kv_committed_len, len(origin_input_ids) + len(output_ids_through_stop))
  ??? 底稿第 239 行  tree_cache.free_kv_row(req.kv, [(cache_protected_len, owned_kv_len)])
  ??? 底稿第 241 行  _release_overallocated_kv_indices(req, owned_kv_len, kv_allocated_len, ...)
  ??? 底稿第 241 行  [owned_kv_len, kv_allocated_len)
  ??? 底稿第 246 行  cache_protected_len = page_aligned_len ≈ owned_kv_len
  ??? 底稿第 249 行  kv_committed_len = 20+4 = 24
  ??? 底稿第 249 行  owned = min(24, 25) = 24
  ??? 底稿第 325 行  return_logprob and logprob_start_len >= 0
  ??? 底稿第 330 行  token_ids = origin_input_ids + output_ids

请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；??? 里真是引文却对不上原文的，照原文改。不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。