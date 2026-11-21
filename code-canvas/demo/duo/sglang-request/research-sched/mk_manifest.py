import re,sys
ms=sys.argv[1]; out=sys.argv[2]
t=open(ms,encoding='utf-8').read()
M = """
memory_hook.py:136-143|chunked_prefill_size=8192
schedule.py:77-87|16384
schedule.py:92-109|"fcfs"
scheduler.py:2235-2305|def event_loop_overlap
scheduler.py:2253|self.ingest_requests()
scheduler.py:2272-2275|get_next_batch_to_run
scheduler.py:4339|def get_next_batch_to_run
scheduler.py:4383-4420|merge_batch
scheduler.py:4651-4661|self.waiting_queue.reorder
scheduler.py:4686-4705|get_prefill_adder_class
scheduler.py:4955-4978|prepare_for_extend
scheduler.py:4446-4456|update_running_batch
scheduler.py:1406|self.waiting_queue
scheduler.py:1410|self.running_batch
scheduler.py:1413|self.running_reqs
scheduler.py:1417|self.prefill_launched_reqs
scheduler.py:1467|self.chunked_reqs
request_list.py:69|Every live request is in at most one list,
request_list.py:229-248|def move(
scheduler.py:1603|NewTokenRatioTracker
scheduler.py:1421|self.last_batch
scheduler.py:4935-4942|move(
scheduler.py:4443-4456|Run prefill first if possible
scheduler.py:4444|Run prefill first if possible
schedule.py:227-230|enable_mixed_chunk
schedule.py:62-68|By default, this is disabled except for profiled Qwen3-VL serving configurations on Hopper.
scheduler.py:4422|_should_defer_prefill
scheduler.py:1493-1509|def _should_defer_prefill
scheduler.py:4355-4363|stash_chunked_request
scheduler.py:4224-4228|checkpoint_kv_cache
common.py:235-244|def checkpoint_kv_cache
scheduler.py:4403-4411|merge_batch
scheduler.py:4413-4418|Every step, not only after a merge
scheduler.py:4413|Every step, not only after a merge: overlap merges a prefill's requests
schedule.py:109|"fcfs"
schedule_policy.py:355-359|CacheAgnosticPolicy.FCFS
request_list.py:175-181|self._items.extend(items)
request_list.py:183-191|def reorder
scheduler.py:1550|SchedulePolicy(
schedule.py:92-108|"uniboost"
schedule_policy.py:187-203|class CacheAwarePolicy
schedule_policy.py:735-746|-r.num_matched_prefix_tokens
schedule_policy.py:585-646|IN_BATCH_PREFIX_CACHING_DEPRIORITIZE_THRESHOLD
schedule_policy.py:117-127|IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD
schedule_policy.py:591|In-batch prefix caching: of the requests with almost nothing cached that
schedule_policy.py:537-548|WAITING_QUEUE_PREFIX_MATCH_MAX
schedule_policy.py:546|Turn off the expensive prefix matching and sorting when the #queue is large.
schedule_policy.py:143|Arbitrary; bounds per-round prefix work on the waiting queue.
schedule_policy.py:180|BOUNDED_LPM_WINDOW_SIZE = 64
schedule_policy.py:272-305|BOUNDED_LPM_WINDOW_SIZE
schedule_policy.py:345|Cache-agnostic policies sort without matching; match anyway so the load
schedule_policy.py:346|snapshot sees num_matched_prefix_tokens. Decode never prefills.
schedule_policy.py:501-535|def _touch_waiting_prefixes
schedule_policy.py:507|Head last, so LRU evicts in reverse admission order; cache-aware policies
schedule_policy.py:146|WAITING_PREFIX_REFRESH_INTERVAL = 32
environ.py:705|SGLANG_ENABLE_WAITING_PREFIX_REFRESH = EnvBool(True)
scheduler.py:4506|def get_new_batch_prefill
scheduler.py:4586-4591|def _get_new_batch_prefill_raw
scheduler.py:4591-4646|batch_is_full
scheduler.py:4606-4609|batch_is_full or len(self.waiting_queue) == 0
scheduler.py:4636-4645|running_batch.batch_is_full = True
scheduler.py:4478-4504|def get_num_allocatable_reqs
scheduler.py:1316-1322|pp_max_micro_batch_size
scheduler.py:4647|After the early returns, so a full-batch pass does not scan the queue.
scheduler.py:4395|running_batch.batch_is_full = False
scheduler.py:4416-4418|running_batch.filter_batch()
scheduler.py:4602|An empty batch has no request left to leave, and only a leaving
schedule_policy.py:1009|class PrefillAdder
prefill_budget.py:144-157|def remaining_total
prefill_budget.py:159-171|def has_capacity
schedule_policy.py:1050|self.rem_input_tokens = rem_input_tokens
schedule_policy.py:1385|self.rem_input_tokens -= compute_charge
schedule_policy.py:1390-1391|self.rem_chunk_tokens -= compute_charge
schedule.py:88-91|If not specified, there is no limit.
schedule_policy.py:2033|self.prefill_max_requests
schedule_policy.py:1095-1100|total_offset
schedule_policy.py:1174-1185|new_token_ratio
prefill_budget.py:91|Fixed token pool. Offsets include pending allocations and decode headroom.
test_prefill_adder.py:436|A chunked continuation must not take KV reserved for running decodes;
schedule_policy.py:92|Clip the estimation of max_new_tokens for the request whose max_new_tokens is very large.
environ.py:708|SGLANG_INIT_NEW_TOKEN_RATIO = EnvFloat(0.7)
schedule.py:158-161|Use a larger value if you see requests being retracted frequently.
schedule_policy.py:96|CLIP_MAX_NEW_TOKENS
environ.py:712|SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION = EnvInt(4096)
schedule_policy.py:94|Note that this only clips the estimation in the scheduler but does not change the stop
schedule_policy.py:1385-1391|self.rem_chunk_tokens -= compute_charge
schedule_policy.py:2272-2277|Without chunking, allow the first request even above the input cap.
memory_hook.py:310|Tokens the activation working set scales with (per serving mode).
memory_hook.py:318-320|activation_tokens = max(cfg.max_prefill_tokens, 2048)
scheduler.py:4772|for req in candidates:
scheduler.py:4775-4778|adder.budget_state() == AddReqResult.OTHER
scheduler.py:4800-4813|running_batch.batch_is_full = True
scheduler.py:4845|req.init_next_round_input(self.tree_cache
schedule_policy.py:944-951|class AddReqResult
scheduler.py:4867-4895|adder.add_one_req(
scheduler.py:4893-4894|unschedulable.append(req)
scheduler.py:4897-4911|self._abort_unavailable(
schedule_policy.py:2210-2213|return self.budget_state()
schedule_policy.py:1304-1319|def budget_state
scheduler.py:4920|Empty passes are the key signal for head-of-line blocking.
scheduler.py:4533-4570|Schedule prefill: #picked=
schedule_policy.py:2036|if self.chunked_req_waits:
schedule_policy.py:2215-2229|def _prefill_charge
schedule_policy.py:2217|Reserve page_size for page-alignment overhead: the paged allocator may
schedule_policy.py:1351|TODO(lsyin): check this workaround logic, which only ensures the prefill will not out of memory, and may be too conservative
schedule_policy.py:2052|The temporary pin excludes this prefix from the evictable budget.
schedule_policy.py:2231-2353|def _select_prefill_admission
prefill_budget.py:211-215|total_tokens + state_slots * self.state_slot_cost < self.remaining_total
schedule_policy.py:1239-1244|def _refused
schedule_policy.py:2272|Without chunking, allow the first request even above the input cap.
schedule_policy.py:2356-2396|def _commit_prefill_admission
schedule_policy.py:1321-1392|def _update_prefill_budget
scheduler.py:4779-4793|can_schedule_lora_req
scheduler.py:4850-4866|enable_hicache_storage
schedule_batch.py:1202-1215|match_kv_cache(
common.py:184-224|def match_kv_cache
common.py:209|req.prefix_len = match_result.device_prefix_len
schedule_policy.py:2244|extend_len = len(req.full_untruncated_fill_ids) - prefix_len
schedule_batch.py:1135-1136|self.extend_end - self.prefix_len
schedule_batch.py:2406|r.get_fill_ids()[r.prefix_len :]
schedule_batch.py:2464|alloc_for_extend(
schedule_batch.py:1239|NOTE: the matched length is at most 1 less than the input length to enable logprob computation
schedule_batch.py:1238-1240|input_len - 1
schedule_policy.py:2361|req.lock = self.tree_cache.lock(req.last_node)
schedule.py:58-61|Setting this to -1 means disabling chunked prefill.
memory_hook.py:80-181|chunked_prefill_size=16384
schedule_policy.py:2291-2320|is_chunked = True
schedule_policy.py:2293|Only one unfinished chunked request can be tracked; a
scheduler.py:4934-4938|move([new_chunked_req], self.chunked_reqs)
scheduler.py:4707-4720|resumed_req.init_next_round_input()
common.py:242-244|tree_cache.checkpoint(req, up_to=req.extend_end)
schedule_policy.py:1675-1797|def _add_chunked_req
prefill_budget.py:267|Park the continuation rather than take running decodes' headroom.
scheduler.py:4751|A parked chunk found no budget in this pass: admit no one ahead of it,
scheduler.py:4718-4720|move([resumed_req], self.prefill_launched_reqs)
batch_result_processor.py:447-449|is_middle_chunk = req is batch.chunked_req
batch_result_processor.py:587-590|we don't want to stream the request currently being chunked.
scheduler.py:4876-4895|if res != AddReqResult.CONTINUE:
scheduler.py:4877-4880|batch_is_full_after_rejection
schedule_policy.py:2025-2028|def batch_is_full_after_rejection
scheduler.py:5194-5282|def update_running_batch
scheduler.py:5198-5200|_reqs_finishing_inflight
scheduler.py:5207|kv_full_retract_flag = not batch.check_decode_mem()
schedule_batch.py:2996-3016|def check_decode_mem
scheduler.py:5209|The queued result frees the finishing requests' KV at the end of
scheduler.py:5229|batch.retract_decode(
schedule_batch.py:3019-3115|def retract_decode
schedule_batch.py:3116-3155|def _get_decode_retraction_order
schedule_batch.py:3127|(len(req.output_ids), -len(req.origin_input_ids))
schedule_batch.py:3124|TODO(lsyin): improve retraction policy for radix cache
schedule_batch.py:3029-3031|first_iter
schedule_batch.py:3033|Always keep at least one request
schedule_batch.py:3082-3103|aborted last request
schedule_batch.py:3057|release memory and don't insert into the tree because we need the space instantly
request_retirer.py:134|release_kv_cache(req, self.tree_cache, checkpoint=False)
schedule_batch.py:1845-1847|evict_from_tree_cache(tree_cache, num_tokens)
schedule_batch.py:1845|NOTE(lsyin): we should use the newly evictable memory instantly.
environ.py:711|SGLANG_RETRACT_DECODE_STEPS = EnvInt(20)
schedule_batch.py:1530-1557|def reset_for_retract
scheduler.py:5270-5271|is_retracted=True
common.py:194-195|req.origin_input_ids + req.output_ids
schedule_batch.py:1141-1160|def refresh_fill_ids
schedule_batch.py:3037-3076|sorted_indices.pop()
new_token_ratio_tracker.py:43-50|min(1.0, new_estimate_ratio)
scheduler.py:5251|self.new_token_ratio_tracker.current = new_token_ratio
scheduler.py:5273|decay_step()
new_token_ratio_tracker.py:37-38|self.current = max(self.current - self.decay, self.min)
environ.py:708-710|SGLANG_NEW_TOKEN_RATIO_DECAY_STEPS = EnvInt(600)
new_token_ratio_tracker.py:22-35|decay = (init - min_ratio)
scheduler.py:6119-6120|self.new_token_ratio_tracker.reset()
scheduler.py:4411|running_batch.merge_batch(last_batch)
schedule_batch.py:3495|def merge_batch
scheduler.py:4383-4396|last_batch.filter_batch
scheduler.py:4383-4456|Run prefill first if possible
scheduler.py:4598-4661|self.waiting_queue.reorder
scheduler.py:4636|A chunked request skips this check: it keeps its req_to_token row across
scheduler.py:4772-4895|adder.add_one_req(
schedule_policy.py:2243-2323|is_chunked = True
prefill_budget.py:144-171|def has_capacity
prefill_budget.py:193-217|def check_prefill
prefill_budget.py:283-311|self.current_offset += immediate
scheduler.py:5198-5232|retract_decode(
scheduler.py:5268-5282|batch.prepare_for_decode()
schedule_batch.py:3019-3060|release_req
schedule_policy.py:343-390|_determine_active_policy
environ.py:708-711|SGLANG_RETRACT_DECODE_STEPS
scheduler.py:3936|move([req], self.waiting_queue)
schedule_policy.py:591-592|share a prefix, only one runs
schedule_policy.py:92-93|This can prevent the server from being too conservative.
schedule_policy.py:2217-2218|consume one extra page per request (see alloc_extend), which
schedule_policy.py:2293-2294|continuation cut short by memory leaves chunk budget to spare.
scheduler.py:5209-5210|decode the rest next step instead of retracting them.
new_token_ratio_tracker.py:34-35|self.current = max(self.current - self.decay, self.min)
new_token_ratio_tracker.py:21-32|decay = (init - min_ratio)
schedule.py:77|max_prefill_tokens
allocation.py:363-371|Allocate KV cache for extend batch and write to req_to_token_pool.
"""
base={'schedule.py':'python/sglang/srt/arg_groups/fields/schedule.py','scheduler.py':'python/sglang/srt/managers/scheduler.py',
'schedule_policy.py':'python/sglang/srt/managers/schedule_policy.py','schedule_batch.py':'python/sglang/srt/managers/schedule_batch.py',
'environ.py':'python/sglang/srt/environ.py','common.py':'python/sglang/srt/mem_cache/common.py','memory_hook.py':'python/sglang/srt/arg_groups/memory_hook.py',
'request_list.py':'python/sglang/srt/managers/scheduler_components/request_list.py','prefill_budget.py':'python/sglang/srt/mem_cache/prefill_budget.py',
'new_token_ratio_tracker.py':'python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py',
'request_retirer.py':'python/sglang/srt/managers/scheduler_components/request_retirer.py',
'batch_result_processor.py':'python/sglang/srt/managers/scheduler_components/batch_result_processor.py',
'allocation.py':'python/sglang/srt/mem_cache/allocation.py','test_prefill_adder.py':'test/registered/unit/managers/test_prefill_adder.py'}
mp={}
for l in M.strip().split('\n'):
    k,v=l.split('|',1); mp[k]=v
need=[]
for m in re.finditer(r'`((?:python|test)/[^`:]+):(\d+)(?:-(\d+))?`',t):
    key=f"{m.group(1)}:{m.group(2)}"+(f"-{m.group(3)}" if m.group(3) else "")
    if key not in need: need.append(key)
rev={v:k for k,v in base.items()}
rows=[];miss=[]
for key in need:
    path,rng=key.rsplit(':',1)
    short=f"{rev[path]}:{rng}"
    if short in mp: rows.append(f"{key}\t{mp[short]}")
    else: miss.append(short)
open(out,'w',encoding='utf-8').write('\n'.join(rows)+'\n')
print(len(need),len(rows),'missing',miss)
