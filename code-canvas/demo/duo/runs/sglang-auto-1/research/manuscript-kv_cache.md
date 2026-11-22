# 「KV 缓存 + 前缀树」这个框的里面：补充底稿

> 仓库 HEAD = `c569c43446`。上一趟顶层底稿记作「顶层底稿」，只在需要时引它的 §。
> 锚点一律是仓库根目录相对路径 `文件:行号`。来源等级：【注释】【提交】【文档】【测试】【推断】；可改进处标【观点】。
> 例子沿用顶层底稿 §8：prompt 20 个 token，树里已有「聊天模板开头」5 个 token，`page_size` 视为 1。新补的数值一律标「设」。
> 本稿没有实跑过任何代码，所有结论来自读源码和 git 历史。

---

## 0. 先给结论

**一句话**：这个框 = 三张表 + 一本账 + 一棵树。三张表是「请求行表」「空闲槽表」「树」，一本账是每条请求身上的 `req.kv`（`ReqKvInfo`）。

| 部件 | 一句话 | 谁创建 / 持有 |
|---|---|---|
| `req_to_token_pool`（`ReqToTokenPool`） | 二维整数表。第 r 行 = 一条请求；第 j 列 = 它第 j 个 token 的 KV 槽号 | 类：`python/sglang/srt/mem_cache/memory_pool.py:382` |
| `token_to_kv_pool_allocator` | 一个「空闲槽号」张量，`alloc(n)` 取前 n 个，`free` 还回去 | `python/sglang/srt/mem_cache/allocator/token.py:28` |
| `tree_cache`（默认 `UnifiedRadixCache`） | 基数树：token 前缀 → 槽号。管匹配、插入、加锁、淘汰 | 默认工厂：`python/sglang/srt/mem_cache/registry.py:153` |
| `req.kv`（`ReqKvInfo`） | 每条请求的账：自己占哪一行、哪一段槽归树、哪一段归自己 | `python/sglang/srt/managers/schedule_batch.py:274` |

**一条请求会和这个框打 6 次交道**（读者最该先记住这条时间线）：

1. **匹配**：`init_next_round_input` → `match_kv_cache`，只记下「命中几个、停在树的哪个节点」，**不拷贝槽号**。
2. **入批加锁**：`PrefillAdder` 先临时锁、算预算，通过后正式 `lock(last_node)`。
3. **prefill 分槽**：只给没命中的部分要槽，把「命中槽号 + 新槽号」写进请求行。
4. **prefill 结束立刻写树**（`checkpoint`）：把 prompt 插进树，行里的槽改指树里那份，锁移到新节点。
5. **decode 每步 1 槽**：写进请求行；树不参与。
6. **结束**：再 `checkpoint` 一次把输出补进树，然后释放「树不管」的槽并解锁。

**对顶层底稿 / 顶层说明卡的两处修正**（请讲解员留意，顶层图的卡片有一句「缓存是结束时顺手插的」）：

- 树**不是只在结束时写**。prefill 一做完（请求还在跑），`checkpoint_kv_cache` 就把 prompt 插进树了：`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:545-548`。分块 prefill 的块与块之间也会写：`python/sglang/srt/managers/scheduler.py:4224-4228`。结束时只是第二次 `checkpoint`，补上输出那一段。所以另一条同开头的请求，在第一条还没结束时就能命中它的 prompt。
- 顶层底稿 §8 第 17 步说「多分的槽一并回收」。按我读到的代码，overlap 多发那一步的槽，只要落在 `owned_kv_len` 之内就**留在树里**，只有越过 `owned_kv_len` 的才由 `_release_overallocated_kv_indices` 归还（见 §8）。这是我读公式推出的，没实跑，标【推断】。

---

## 1. 默认走哪套树实现（先划范围）

- 普通（全注意力）模型默认 `UnifiedRadixCache`。选择链最后一行兜底：`python/sglang/srt/mem_cache/registry.py:153`（`return create_unified_radix_cache(ctx)`）；`--disable-radix-cache` 才走另一支：`python/sglang/srt/mem_cache/registry.py:93`。开关默认 `False`：`python/sglang/srt/arg_groups/fields/memory.py:78`（help 原文 "Disable RadixAttention for prefix caching."）。
- 老的单文件实现 `python/sglang/srt/mem_cache/radix_cache.py:282`（`RadixCache`）现在是 `PureSWARadixCache`、FlexKV 的父类（`python/sglang/srt/mem_cache/pure_swa_radix_cache.py:14`、`python/sglang/srt/mem_cache/storage/flexkv/flexkv_radix_cache.py:74`）；调度器还拿它的 `create_simulated()` 当「纯数据结构」做排队去重（§3.5）。
- `UnifiedRadixCache` 是「组件化」的：Full（全注意力）、SWA（滑窗）、Mamba（循环状态）各一个组件挂在同一棵树上。【文档】`python/sglang/srt/mem_cache/unified_cache/components/README.md:3`："unifies Full-attention, Sliding-Window-Attention (SWA), and Mamba/SSM caching into a single radix tree."
- **本稿只讲「普通全注意力模型」这一条路**：树里只有 Full 一个组件。SWA / Mamba / HiCache（主机内存、磁盘分层）/ 会话缓存 / LMCache 都点到为止。读源码时看到 `window`、`mamba`、`hicache`、`linker`、`write_back` 字样可以直接跳过。
- 默认实现什么时候从老 `RadixCache` 换成 Unified，我没查到明确的那次提交，见 §12。

---

## 2. 四样东西各自长什么样

### 2.1 `req_to_token_pool`：请求行表

- 就是一个 `torch.zeros((size+1, max_context_len), dtype=torch.int32)`：`python/sglang/srt/mem_cache/memory_pool.py:422`。`write(indices, values)` 一行：`python/sglang/srt/mem_cache/memory_pool.py:429`。
- **行号 0 是垫行，不分给请求**：`free_slots = range(1, size+1)`（`python/sglang/srt/mem_cache/memory_pool.py:425`）。【注释】`python/sglang/srt/mem_cache/memory_pool.py:416`：原文："+1 padding row at index 0: cuda-graph padded batches default"（下一行接 "req_pool_indices to 0, so dummy reads/writes land here harmlessly."）。
- 取行：`alloc_rows` 从空闲列表**尾部**切（`python/sglang/srt/mem_cache/memory_pool.py:463`）。【注释】`python/sglang/srt/mem_cache/memory_pool.py:461`：原文："Pop from the tail: O(need_size), unlike a prefix pop which is"。还行：`free_rows` 追加回尾部（`python/sglang/srt/mem_cache/memory_pool.py:476`）。
- 行数不够时抛错，报错文案建议调小 `--max-running-requests`：`python/sglang/srt/mem_cache/allocation.py:315-337`（`alloc_req_slots`）。

### 2.2 `token_to_kv_pool_allocator`：空闲槽表（`page_size=1` 的最简版）

- `clear()` 把 `free_pages` 设成 `arange(1, size+1)`：`python/sglang/srt/mem_cache/allocator/token.py:44`。槽 0 也是垫位。【注释】`python/sglang/srt/mem_cache/allocator/token.py:43`：原文："The padded slot 0 is used for writing dummy outputs from padded tokens."
- `alloc(n)`：不够返回 `None`；够就切走前 n 个：`python/sglang/srt/mem_cache/allocator/token.py:54-63`。`free(idx)` 拼回去：`python/sglang/srt/mem_cache/allocator/token.py:65-75`。
- 它**只管「号」**，不碰显存里的 K/V 张量；README 的分层表里 allocator 一行是 "which slots are free"：`python/sglang/srt/mem_cache/README.md:32`，radix cache 一行是 "what to keep and what to evict"：`python/sglang/srt/mem_cache/README.md:36`。
- `page_size > 1` 时走分页版 `python/sglang/srt/mem_cache/allocator/paged.py`，按页取。`--page-size` 默认 `None`：`python/sglang/srt/arg_groups/fields/schedule.py:163`，怎么落成具体数我没追到（§12）。本稿例子取 1。

### 2.3 树：节点里存什么

- 节点 `UnifiedTreeNode`：`children`（dict）、`parent`、`key`（这一段边上的 token）、`component_data[Full].value`（这一段每个 token 对应的槽号张量）、`last_access_time`、`hit_count`、`priority`：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:118-150`（`:124` `children`，`:126` `key`，`:130` `component_data`，`:138` `hit_count`，`:144` `priority`）。
- **一个节点 = 树上的一段「边」**：`key` 是 N 个 token，`value` 就是 N 个槽号，一一对应。所以「命中 5 个」= 走过的节点 `value` 长度之和。
- 命名空间：`child_key_at` 把 `extra_key`（例如 LoRA 名）和 `cache_salt` 拼进字典键：`python/sglang/srt/mem_cache/radix_cache.py:227-249`。不同 `extra_key` 的请求走不同分支，互相命中不了。
- 页对齐：`page_aligned(page_size)` 把 key 截到整页：`python/sglang/srt/mem_cache/radix_cache.py:148-152`；匹配和插入都先对齐：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1002`、`:1298`。

### 2.4 账本：`req.kv`（`ReqKvInfo`）——谁拥有哪一段槽

读者问「一条请求的 KV 槽号存在哪」时最该看这里。类注释自己就把话说清了：`python/sglang/srt/managers/schedule_batch.py:274-286`。

| 字段 | 含义 | 锚点 |
|---|---|---|
| `req_pool_idx` | 请求在 `req_to_token` 里的行号（有行 = `holds_kv`） | `python/sglang/srt/managers/schedule_batch.py:278` |
| `cache_protected_len` | 「树拥有行的 [0, 这里)」——命中的或自己插进去的 | `python/sglang/srt/managers/schedule_batch.py:282` |
| `cache_inserted_len` | 本请求已经插过树的长度（之后只对更长的部分算「命中」） | `python/sglang/srt/managers/schedule_batch.py:285` |
| `kv_committed_len` | KV 内容已提交到这里（≤ `kv_allocated_len`） | `python/sglang/srt/managers/schedule_batch.py:286` |

【注释】`python/sglang/srt/managers/schedule_batch.py:280`：原文："The request's own KV is [cache_protected_len, kv_allocated_len)." —— 一句话分账：**行里 `[0, cache_protected_len)` 的槽归树；`[cache_protected_len, kv_allocated_len)` 归请求自己。** 释放时只还后一段（`python/sglang/srt/mem_cache/common.py:421`）。

写账本的只有几个小方法：`set_row_len`（`python/sglang/srt/managers/schedule_batch.py:361`）、`allocate_decode`（`:366`）、`protect_prefix`（`:405`）、`record_insert`（`:409`）。

---

## 3. 匹配：「命中 5 个」是怎么走出来的

### 3.1 谁调、调了什么

`Req.init_next_round_input`（`python/sglang/srt/managers/schedule_batch.py:1161`）→ `match_kv_cache`（`python/sglang/srt/mem_cache/common.py:184`）→ `UnifiedRadixCache.match_prefix`（`python/sglang/srt/mem_cache/unified_radix_cache.py:700`）→ `UnifiedTreeCore.match_prefix`（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:997`）→ `_match_prefix_helper`（`:1016`）。入口在调度器排队阶段：`python/sglang/srt/managers/schedule_policy.py:326`（`r.init_next_round_input(self.tree_cache)`）。

### 3.2 在树上怎么走（真正回答「5 个」）

`_match_prefix_helper` 是一个 while 循环：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1061-1083`。

1. 用请求 key 当前偏移处的 token（`page_size=1` 就是一个 token）当字典键，去当前节点的 `children` 里找：`:1061`。
2. 找到子节点后，用 `child.key.match_at(key, key_offset, ...)` 数「这条边和请求从偏移处起有几个 token 相同」：`:1068`。`match_at` 不是逐个比，而是倍增窗口 + 二分找第一个不同的位置。【注释】`python/sglang/srt/mem_cache/radix_cache.py:194`：原文："Exponential search for the first diverging token: gallop in doubling"。
3. 相同长度 < 这条边的长度（只吃了边的一半）：**当场把这个节点劈成两半**（`_split_node`，`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1070-1071`），前半成为新的中间节点，匹配停在这里（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1075` `break`）。
4. 否则整条边吃完，把它的 `value` 追加进 `value` 列表，沿边往下（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1077-1083`）。

例子：树里有节点 A（5 个 token，槽 101..105，设）。请求前 5 个 token 同 A，第 6 个 token 在 A 的 `children` 里没有 → 循环结束，`value=[A.value]`，命中 5。

**劈节点发生在「匹配」而不是「插入」阶段**，读者最想不到：哪怕只是读，也会改树的形状。为什么要在匹配时劈：匹配要锁住「命中的那一段」，锁的锚点是 `last_node`，所以命中边界上必须有个节点。【推断】（`_split_node` 在 match 里被调用是事实 `python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1071`；动机是我的推断，依据是匹配结果里的 `last_device_node` 就是之后 `lock` 用的锚点，见 §4。）

### 3.3 匹配的返回值：只记「到哪」，不拷槽号

- `match_kv_cache` 把结果存到请求上：`req.prefix_len = device_prefix_len`（`python/sglang/srt/mem_cache/common.py:209`）、`req.last_node`、`req.best_match_node` 等（`:210-218`）。**没有槽号数组**。
- 槽号到分配时才从树上读：`prefix_device_indices` 沿 `last_node` 往上收各节点 `value` 再反转拼起来：`python/sglang/srt/mem_cache/unified_radix_cache.py:5070-5084`，底层 `collect_full_device_indices`：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1239-1255`。
- 【文档】`python/sglang/srt/mem_cache/base_prefix_cache.py:728`：原文："valid from match until allocation writes them into the row." —— 这就是为什么匹配之后、分配之前**必须给这条路径加锁**：否则这一段可能被淘汰，槽号读到的就是别人的。
- 命中后树上沿途**所有祖先**的 `last_access_time` 都被刷新：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1182-1186`。也就是说 LRU 的「最近使用」在**匹配时**就更新，不等请求结束。

### 3.4 为什么最多命中「输入长度 − 1」个

- 做法：`_compute_max_prefix_len` 先令 `max_prefix_len = input_len - 1`（`python/sglang/srt/managers/schedule_batch.py:1240`），再套其他上限；`init_next_round_input` 把它当 `key_limit` 传给匹配（`python/sglang/srt/managers/schedule_batch.py:1194`、`:1205`）。【注释】`python/sglang/srt/managers/schedule_batch.py:1239`：原文："NOTE: the matched length is at most 1 less than the input length to enable logprob computation"。
- 【提交】这个「−1」最早进来的提交是 `1e495e0847`（2024-09-03，"[Fix] Fix select by ensuring each request has at least one token (#1318)"）。它新增的注释原文："FIXME: To work around some bugs in logprob computation, we need to ensure each"（下一行 "request has at least one token. Later, we can relax this requirement and use `input_len`."）。**当年自己就标成了 FIXME，是绕 bug 的**，不是模型的硬性要求。
- 【提交】同一个提交的上下文里已有一条更本质的旧注释，原文："Need at least one token to compute logits"（`git show 1e495e0847:python/sglang/srt/managers/schedule_batch.py` 第 187 行；那时它只在 `max_new_tokens > 0` 时对 `input_len - 1` 取 min）。读法：prefill 至少要有 1 个 token 真正过模型，才有最后一个位置的 logits 去采第一个输出 token。
- 【推断】综合：「−1」服务两个目的——① 至少算 1 个 token 才拿得到 logits（本质）；② 绕 logprob 的老 bug（FIXME）。所以**完全相同的请求第二次来，命中 19 而不是 20**，要重算 1 个 token。
- 其他上限（同一个函数）：要返回 prompt logprob 时，命中不能越过 `logprob_start_len`：`python/sglang/srt/managers/schedule_batch.py:1246-1247`；dLLM 另有块对齐上限（`:1248-1256`，本稿不展开）；末尾 `max(max_prefix_len, 0)`：`:1257`。
- 有一类请求**根本不匹配也不写树**：带 `positional_embed_overrides` 的请求，匹配用的 token 被换成空数组、`key_limit=None`（`python/sglang/srt/managers/schedule_batch.py:1196-1200`），`skip_radix_cache_insert` 为真（`python/sglang/srt/managers/schedule_batch.py:826`）。【注释】`python/sglang/srt/managers/schedule_batch.py:825`：原文："Override embeds give KV that the token ids alone do not determine."（树的键只有 token id，KV 却还取决于 embeds）。

### 3.5 排队阶段还有一棵「纯数据结构」的树（点一句）

缓存感知的调度策略（如 LPM）会先对每条等待请求匹配真树，再用 `RadixCache.create_simulated()` 造的一棵**不带内存池的假树**（`python/sglang/srt/managers/schedule_policy.py:256`）做「批内去重」：命中很少、前缀相同的请求只放一个先跑，让共享前缀只算一次。阈值默认 32 / 32（环境变量 `IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD`、`IN_BATCH_PREFIX_CACHING_DEPRIORITIZE_THRESHOLD`）：`python/sglang/srt/managers/schedule_policy.py:117-125`；逻辑在 `python/sglang/srt/managers/schedule_policy.py:585`，注释原文 "In-batch prefix caching: of the requests with almost nothing cached that"（`:591`）。它只影响排队顺序，不动真树。

---

## 4. 入批：加锁 + 预算

- `add_one_req` 先用 `with self._lock_node(req.last_node):` **临时**锁住命中路径，在锁里算预算、判断能否收这条请求：`python/sglang/srt/managers/schedule_policy.py:2054`；`_lock_node` 是个上下文管理器，退出时 `unlock`：`python/sglang/srt/managers/schedule_policy.py:1798-1812`。【注释】`python/sglang/srt/managers/schedule_policy.py:2052`：原文："The temporary pin excludes this prefix from the evictable budget."
- 预算公式很简单：**可用 = 分配器空闲槽 + 树里可淘汰的槽**：`python/sglang/srt/mem_cache/prefill_budget.py:144-153`。被锁的算 `protected`，不进「可淘汰」。所以命中的 5 个 token 一旦被锁，就**不再被算成「能腾出来的空间」**，否则预算虚高，真分槽时腾不出来。【推断】（依据：上面那条注释 + `inc_lock_ref` 把 token 数从 evictable 挪到 protected 的记账，`python/sglang/srt/mem_cache/unified_cache/components/full.py:333-337`。）
- 通过预算后 `_commit_prefill_admission` 做**正式锁**：`req.lock = self.tree_cache.lock(req.last_node)`：`python/sglang/srt/managers/schedule_policy.py:2361`。`lock` 返回一张「回执」`TreeLock`，解锁必须拿同一张回执：`python/sglang/srt/mem_cache/unified_radix_cache.py:1507-1519`；回执节点与解锁节点不符直接断言：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:833-838`。

### 锁到底锁什么

Full 组件的 `acquire_component_lock`：从 `last_node` **一路往上到根**，每个祖先 `lock_ref += 1`；节点 `lock_ref` 第一次 0→1 时，把它的 token 数从 `evictable` 记到 `protected`，并从「可淘汰叶子集合」里摘掉：`python/sglang/srt/mem_cache/unified_cache/components/full.py:326-342`（`:333-337` 记账，`:338` 加一，`:339` 摘叶子）。释放对称：`python/sglang/srt/mem_cache/unified_cache/components/full.py:344-389`。

【文档】`python/sglang/srt/mem_cache/unified_cache/components/README.md:203`：Full 是 Path-lock，原文："walks from node to root, `lock_ref += 1` on every ancestor."

---

## 5. prefill 分槽：「15 个」是怎么分的

`prepare_for_extend` → `alloc_for_extend`：`python/sglang/srt/managers/schedule_batch.py:2464`、`python/sglang/srt/mem_cache/allocation.py:363`。普通路径（`page_size==1`，非 CP/NPU）顺序：

| 步 | 做什么 | 锚点 |
|---|---|---|
| a | 读命中前缀的槽号：新请求读树路径；已有行的（分块的后续块）直接读自己行前 `prefix_len` 列 | `python/sglang/srt/mem_cache/allocation.py:352-360`、`:376` |
| b | 要请求行：`alloc_req_slots` → `storage.acquire(reqs)`；失败直接抛错 | `python/sglang/srt/mem_cache/allocation.py:395`、`:315-337` |
| c | 要 KV 槽：`alloc_token_slots(tree_cache, extend_num_tokens)` | `python/sglang/srt/mem_cache/allocation.py:432` |
| d | 写请求行：`row[0:prefix_len] = 命中槽号`，`row[prefix_len:seq_len] = 新槽号` | `python/sglang/srt/mem_cache/allocation.py:524`（`write_cache_indices` 定义 `:70-120`） |
| e | 记账：`req.kv.set_row_len(seq_len)` | `python/sglang/srt/mem_cache/allocation.py:558` |

例子：prefix_len=5（槽 101..105，设），extend=15。c 步从空闲表取 15 个，设为 2001..2015。d 步后，请求行 r（设 7）前 20 列 = `[101..105, 2001..2015]`。账本 `kv_allocated_len = kv_committed_len = 20`。

### c 步里「先腾后取」

`alloc_token_slots` → `_storage(tree_cache).take_tokens(num, make_room=_room_in(tree_cache))`（`python/sglang/srt/mem_cache/allocation.py:196-201`）。`take_tokens` 里两行就是顺序：先 `make_room(num_tokens)`，再 `allocator.alloc(num_tokens)`：`python/sglang/srt/mem_cache/static_kv_storage.py:277-278`。

`make_room` = `evict_from_tree_cache`（`python/sglang/srt/mem_cache/common.py:247`）→ `capacity.evict_to_free_tokens`（`python/sglang/srt/mem_cache/capacity.py:282`）→ 普通分配器落到 `allocator.evict_to_free_tokens`（`python/sglang/srt/mem_cache/capacity.py:135-136`）：**只有 `available_size() < num_tokens` 才去树里淘汰**：`python/sglang/srt/mem_cache/allocator/base.py:259-260`。淘汰量是「缺口」：`python/sglang/srt/mem_cache/unified_radix_cache.py:832-838`。

所以：**没满就不淘汰**，缓存一直攒到池子满了才被「按需」挤出去。【推断】（依据是 `python/sglang/srt/mem_cache/allocator/base.py:259` 的条件判断；没找到注释说明动机。）

淘汰完还不够：`alloc_token_slots` 抛 `RuntimeError`（报错文案以 "Out of memory." 开头）：`python/sglang/srt/mem_cache/allocation.py:204-213`。这不是常态，因为前面有预算（§4）和 decode 预检（§7）挡着。【注释】`python/sglang/srt/mem_cache/allocation.py:325`（`alloc_req_slots` 文档）：原文："and should surface rather than be masked."——行分配失败意味着准入预算算错了，要报错而不是遮掩。

---

## 6. prefill 做完：写树（`checkpoint`）

### 6.1 什么时候写

- prefill 最后一块做完、请求没结束：`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:545-548` 把 `checkpoint_kv_cache` 放进 `kv_ops`。条件 `not batch.decoding_reqs or req not in batch.decoding_reqs`（混合批里正在 decode 的那些不走这儿）。
- 分块 prefill 的中间块：`Scheduler.stash_chunked_request` → `checkpoint_kv_cache`：`python/sglang/srt/managers/scheduler.py:4224-4228`。
- 请求结束：`release_kv_cache` 里 `tree_cache.checkpoint(req, up_to=owned_kv_len)`：`python/sglang/srt/mem_cache/common.py:419`。
- 三处都汇到 `UnifiedRadixCache.checkpoint` → `checkpoint_into_tree` → `_checkpoint_into_tree`：`python/sglang/srt/mem_cache/unified_radix_cache.py:1629`、`:1649`、`:1657`。
- 【文档】`python/sglang/srt/mem_cache/base_prefix_cache.py:579`：原文："Called at every checkpoint of a running request and once more when"（下一行接 "it finishes (``req.finished()``), when the tree also takes over the"）。**这就是官方对「中途写 + 结束再写」的说明。**
- 同一处文档说 "Nothing here frees a"（`python/sglang/srt/mem_cache/base_prefix_cache.py:581`，续 "slot"）。**这句不严格**：插入遇到重复段会释放重复的槽（见 6.3）。以代码为准。
- `checkpoint_kv_cache` 还做 `req.prefix_len = req.extend_end`（`python/sglang/srt/mem_cache/common.py:244`），让下一块从这里接着算；`skip_radix_cache_insert` 的请求不写（`python/sglang/srt/mem_cache/common.py:241`、`:410`）。
- 【提交】`checkpoint` 的名字和这段文档来自 `50be533d09`（2026-10-01，"[mem_cache] Rename `cache_unfinished_req` to `checkpoint_req` and count cache hits only when a request finishes (#41520)"；`git blame` 显示 `base_prefix_cache.py:576-583` 的文档字符串由它写入）。也就是说「中途写」并不新：改名前叫 `cache_unfinished_req`，该函数名最早见于 `7623091d97`（2024-08-07，"RadixCache method adjust (#977)"）。两个提交都只有标题，没有正文。

### 6.2 写什么：`_checkpoint_into_tree`（dense 路径）

按代码顺序（`python/sglang/srt/mem_cache/unified_radix_cache.py:1657-1830`）：

1. `token_ids = req.full_untruncated_fill_ids[:up_to]`（`:1661`）；从请求行读出这些 token 的槽号 `kv_indices_orig = req_to_token[row, :len]`（`:1669-1671`）。
2. 各组件有机会把「可缓存长度」往小压（`:1682-1690` 一带）；dense 只有 Full，不压。
3. 对齐到页，并**复制**槽号：`values = kv_indices[:page_aligned_len].to(dtype=torch.int64, copy=True)`（`:1723-1725`）。树存的是**自己的拷贝**，不是请求行的视图。【推断】（请求行之后会被覆盖、回收；注释没说，是我的理由。）
4. 调 `self.insert(insert_params)`（`:1740`），参数里带 `prev_prefix_len=req.kv.cache_protected_len`、`inserted_len=req.kv.cache_inserted_len`（`:1674-1680`）。
5. `req.kv.record_insert(page_aligned_len)`（`:1760`）。
6. 在 prompt 边界再切一刀（6.4）。
7. **把请求行改指树里那份槽**：`req_to_token_pool.write((row, slice(cache_protected_len, len(new_indices))), new_indices[cache_protected_len:])`（`:1800-1803`）。
8. **移锁**：先 `self.unlock(req.lock)`（`:1805`），再 `req.lock = self.lock(new_last_node)`（`:1812`）；`req.kv.protect_prefix(len(new_indices))`（`:1823`）；`req.last_node = new_last_node`（`:1824`）。

第 7、8 步的意义：从此「这些槽归树」，请求只是租用——`cache_protected_len` 往后挪，释放时不再还这一段。

### 6.3 `insert` 内部：走、劈、新建叶子，以及「去重」

入口 `UnifiedRadixCache.insert`（`python/sglang/srt/mem_cache/unified_radix_cache.py:772`）→ 分阶段游走：`UnifiedTreeCore.begin_insert`（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1290`）→ WALK / OVERLAP / COMMIT / TAIL（`:1409` `_advance_insert`）。重入会直接断言 "re-entrant insert"（`python/sglang/srt/mem_cache/unified_radix_cache.py:776`）。

- **WALK**（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1443`）：沿 key 往下，遇到重叠只覆盖节点前半就**劈开**（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1456-1458`，`_split_node` 本体 `python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1640-1710`）。劈开时「后半」保留原来的访问时间。【注释】`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1690`：原文："Splitting does not access the suffix; retain its recency and place"，即不让一次写把后半的 LRU 顺序弄乱。
- **OVERLAP**（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1498`）：这个节点上，请求**自己算出来的槽**和树里已有的重复了 → 请求这一份要释放。`dup_start = max(0, prev_prefix_len - total_prefix_length)`（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1517`）：只有**请求自己新算的那一段**才算重复（命中来的那段本来就是树的，不能再还一次）。释放动作 `FreeDeviceKV([...])`（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1540`），由 `python/sglang/srt/mem_cache/unified_radix_cache.py:1928-1932` 真正还给分配器。
  - 这就是**并发相同 prompt 的去重**：两条请求同时 prefill 同一段，各自算了各自的槽；先写树的留下，后写的发现重叠，释放自己那份，并在 6.2 第 7 步把请求行改指树里那份。
- **COMMIT**（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1558`）：key 还有剩就在当前节点下 `_add_new_node` 挂新叶子，`value=value.clone()`：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1570`、`:1731`。

例子：A(5, 槽 101..105) → 请求 prompt 20 个 → WALK 走过 A，OVERLAP 对 A 的 5 个：`dup_start=max(0, 5-0)=5`、`consumed_from=5`，无重复 → COMMIT 在 A 下挂叶子 B（15 个 token，槽 2001..2015）。树成了 root→A(5)→B(15)。

### 6.4 为什么还要「在 prompt 边界再切一刀」

- 代码：`python/sglang/srt/mem_cache/unified_radix_cache.py:1762-1787`。仅当树里只有 Full 组件（`:1772-1773`）。把 prompt 部分单独再 `insert` 一次，让 prompt 和输出成为两个节点。第二次 `insert` 的 `prev_prefix_len` / `inserted_len` 都设成 `len(prompt_key)`（`:1781-1782`）。
- 【注释】`:1762`：原文："Split the leaf at the prompt boundary so eviction can drop the output"；`:1763-1764` 接 "KV without the prompt. prev_prefix_len keeps the overlapping indices" / "from being freed as duplicates."
- 【提交】`0a57403468`（2026-09-12，"[Perf] Optimize Qwen3-VL unique-image serving on H100 (#36411)"）在老 `python/sglang/srt/mem_cache/radix_cache.py` 里首次加入这段，注释更长，原文："so LRU eviction can discard output KV without"，续行 "also losing the reusable prompt KV."；又写 "hit_count drives eviction order, so an"，续行 "extra bump would silently promote every prompt node."。**为什么：** 输出通常没人复用，prompt 才会；不切的话 LRU 只能整条叶子一起丢，把可复用的 prompt 也丢了。当时第二次 insert 用 `chunked=True` 防止重复计命中，现在换成了 `inserted_len`。【推断】（换法是我对比两版代码的读法，提交说明没写。）
- 如果 prefill 后已经 checkpoint 过，prompt 已经单独是一个节点，这一刀就是空操作。

### 6.5 `hit_count` 什么时候 +1

不在匹配，而在**插入**时：重叠节点上 `node_end > inserted_len` 才 `_inc_hit_count_and_check`：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1548`；注释 `:1546` 原文："Nodes this request already inserted were counted back then."（`cache_inserted_len` 就为此而设）。`hit_count` 只对 `lfu` / `slru` 策略有用，默认 `lru` 不看（【文档】`docs/docs/advanced_features/radix_eviction_policy.mdx` 的策略表）。

---

## 7. decode：每步 1 个槽

- `prepare_for_decode` → `alloc_for_decode(self, token_per_req=1)`：`python/sglang/srt/managers/schedule_batch.py:3346`；默认实现 `alloc_for_decode_default`：`python/sglang/srt/mem_cache/allocation.py:681`。
- `page_size==1`：`alloc_token_slots(tree_cache, bs * token_per_req)`（`:697`），即**整批一次要 bs 个**，走的还是 §5 的「先腾后取」。
- 写请求行：`req_to_token_pool.write((req_pool_indices, locs), out_cache_loc)`，其中 `locs = seq_lens_gpu.clone()`（`python/sglang/srt/mem_cache/allocation.py:734-738`）。**新槽写在第 seq_len 列（还没 +1 的长度）**，随后 `seq_lens = seq_lens + 1`（`python/sglang/srt/managers/schedule_batch.py:3353-3354`）。
- 账本：`req.kv.allocate_decode(1)`：`python/sglang/srt/mem_cache/allocation.py:759`；`kv_allocated_len` 和 `kv_committed_len` 同时 +1，**在这一步前向跑之前就提交了**（`python/sglang/srt/managers/schedule_batch.py:366-370`，文档字符串原文："allocation, before the step runs."，续上一行 "commits them at"）。
- 例子：行 r 第 20 列写入新槽 2016（设）；`seq_len` 20→21；`allocated=committed=21`。
- **decode 期间树完全不参与**（不匹配、不插入）；只在「槽不够」时通过 `make_room` 间接让树淘汰叶子。
- 槽不够到淘汰也救不了，是**调度器**在 decode 前预检并「回撤」（retract）请求：`check_decode_mem`（`python/sglang/srt/managers/schedule_batch.py:2996-3016`）→ `capacity.check_decode_capacity`（`python/sglang/srt/mem_cache/capacity.py:291`），不够则 `retract_decode`（`python/sglang/srt/managers/scheduler.py:5229`）。被回撤的请求**不写树**，直接 `checkpoint=False` 释放：`python/sglang/srt/managers/scheduler_components/request_retirer.py:134`，旁边有个 TODO：`python/sglang/srt/managers/scheduler_components/request_retirer.py:132`，原文："TODO (csy): for preempted requests, we may want to insert into the tree"（现状：被抢占请求算好的 KV 就丢了）。回撤细节归「调度器」那个框，本稿不展开。

---

## 8. 结束：哪些槽留下、哪些还回去

入口：请求被 `RequestRetirer.release` 释放时调 `release_kv_cache(req, tree_cache, checkpoint=...)`：`python/sglang/srt/managers/scheduler_components/request_retirer.py:111`；prefill 阶段就结束的请求：`python/sglang/srt/managers/scheduler_components/batch_result_processor.py:541`（`checkpoint=discard_finish_reason is None`）。本体 `python/sglang/srt/mem_cache/common.py:385-433`，顺序：

1. `owned_kv_len = req.owned_kv_len()`（`:409`）：树该存多长。定义（`python/sglang/srt/managers/schedule_batch.py:990-1002`）：`min(kv_committed_len, len(origin_input_ids) + len(output_ids_through_stop))`。`output_ids_through_stop` = `output_ids[:finished_len]`，**包含**停止 token（`python/sglang/srt/managers/schedule_batch.py:947-951`；`finished_len = matched_pos + 1` 在 `:1336`）。（`strip_thinking_cache` 开启时只留 prompt，见 `:993-994`。）
2. `req.refresh_fill_ids()`（`python/sglang/srt/mem_cache/common.py:418`）。【注释】`python/sglang/srt/mem_cache/common.py:417`：原文："The fill-id array lags output_ids until the next prepare_for_decode." —— 否则插树用的 token 序列少了最后几个输出。
3. `tree_cache.checkpoint(req, up_to=owned_kv_len)`（`:419`）：把 prompt + 输出插进树（§6）。
4. **`tree_cache.free_kv_row(req.kv, [(cache_protected_len, owned_kv_len)])`（`:421`）**：只还「请求自己的」那段。【注释】`:420`：原文："The protected prefix is not this req's to free."
5. `tree_cache.unlock(req.lock)`（`:422`）。
6. `_release_overallocated_kv_indices(req, owned_kv_len, kv_allocated_len, ...)`（`:424`）：把 `[owned_kv_len, kv_allocated_len)`（分了但不该存的）还回去：`python/sglang/srt/mem_cache/common.py:435-464`。
7. `release_row(req)` 还请求行，`req.kv.mark_kv_released()`（`:431-433`）。

**哪些留、哪些还**（dense，`page_size=1`）：

- 第 3 步之后 `cache_protected_len = page_aligned_len ≈ owned_kv_len`，第 4 步通常是空区间——**全部留在树里**，没有槽被还。
- 真正还回去的有两类：① `page_size > 1` 时尾部不满一页的槽（树只收整页：`python/sglang/srt/mem_cache/unified_radix_cache.py:1723`）；② 第 6 步的「多分的」。
- ② 的例子：prompt 20，decode 4 步产出 X1..X4，X5=EOS 在第 4 步被采出；输出共 5 个 token（含 EOS），`finished_len=5`。
  - 若 overlap 没有多发一步：`kv_committed_len = 20+4 = 24`，`owned = min(24, 25) = 24`；树存 24 个 token（prompt + X1..X4），EOS 的 KV 从未被算过。
  - 若 overlap 已多发一步 decode（以 X5 为输入、算出 X5 的 KV）：`committed=25`，`owned=min(25,25)=25`，X5 的 KV 也进树，第 6 步无事可做。【推断】（只由 `owned_kv_len` 的公式推出，没实跑。）
  - 第 6 步真有东西可还的情形：非投机解码时，只有「停止点早于最后一个已提交 token」。【注释】`python/sglang/srt/mem_cache/common.py:445`：原文："A stop landing before the last committed token does the same, via"（续 "effective_kv_committed_len()."），与其下 `:447-450` 的断言配套。
- 读者通常会问「为什么先插树再还槽」：插树要用请求行里的槽号（`kv_indices_orig` 读自请求行，`python/sglang/srt/mem_cache/unified_radix_cache.py:1669`），还了就读不到；插树过程还可能**自己**还掉一部分重复槽（6.3）。这是顺序依赖。【推断】（代码顺序如此，没有注释明说。）

---

## 9. 显存满了先淘汰谁，正在用的怎么防

### 9.1 谁来淘汰、什么时候

只有一个触发点：**分配时槽不够**（§5 的 `make_room`）。没有后台定时淘汰。

### 9.2 候选集：「可淘汰叶子」

- 默认策略 `lru`：`python/sglang/srt/arg_groups/fields/memory.py:51`（`] = "lru"`）。
- 候选只有「叶子」。`_is_device_leaf`：`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:2659-2675`：不是根、自己没被淘汰（`:2666`）；**任何一个组件的 `lock_ref` 都为 0**（`:2668`）；**没有孩子还持有 KV**（`:2670-2674`）。【注释】`:2660`：原文："D-leaf: Full device value present, no child with Full KV on device,"。
- 【文档】`docs/docs/advanced_features/radix_eviction_policy.mdx:11`：原文："nodes whose KV is present, unlocked (no in-flight request holds them), and not shadowed by a child that still holds KV."；`:13`：原文："Once a leaf is evicted, its parent may become a leaf and re-enter the candidate set, so eviction walks a branch from its tip toward the root."
- **为什么只淘汰叶子**：中间节点的 KV 是它下面所有后代 KV 的前提；先丢中间节点，后代成了没法匹配到的「悬空」数据。叶子优先、一路往根推，保证「树里每个节点的整条祖先链都在」。【推断】（文档只说了「会这样做」，没说为什么。）

### 9.3 怎么挑：一个按优先级排的堆

Full 组件：`_evict_device_start` 把所有「可淘汰叶子」放进堆（`heapify`，`python/sglang/srt/mem_cache/unified_cache/components/full.py:211-219`）；`_evict_device_next_node` 每次弹出堆顶（`:241`）；刚淘汰的叶子的**父节点**若也变成叶子，就压回堆里（`:235`）。排序键是策略的优先级值：`lru` 即 `last_access_time`，最小（最久没用）的先走：`python/sglang/srt/mem_cache/evict_policy.py:17-19`。（没开会话缓存时，堆键就是 `tree_core.eviction_strategy.get_priority`：`python/sglang/srt/mem_cache/unified_cache/components/full.py:65-68`；开了会话缓存才会多一层「会话引用数」排在前面，本稿不展开。）

【文档】`python/sglang/srt/mem_cache/unified_cache/components/README.md:67`：Full 的淘汰由叶子集合驱动，原文："not a Full LRU"（只有辅助组件才有链表 LRU）。

淘汰叶子本身：`evict_device_leaf`（`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:2071`），没开 HiCache 时走 `_delete_unbacked_device_leaf`（`:2195`）：释放槽号、从父节点摘除、连带删掉变空的祖先。`UnifiedRadixCache._evict_device_walk`（`python/sglang/srt/mem_cache/unified_radix_cache.py:1275`）里大段 `write_back` / `pending` 代码只在 HiCache 开启时才走，**可以跳过**。

### 9.4 正在用的前缀怎么防

三道防线，叠加：

1. **锁**：路径上每个节点 `lock_ref>0` → 不是叶子候选（`_is_device_leaf` 第三个条件）。加锁时还主动把它们从候选集合里 `discard`：`python/sglang/srt/mem_cache/unified_cache/components/full.py:339`。
2. **整条路径锁到根**：锁在叶子上，祖先全被锁（§4），不会先轮到。
3. **预算**：入批时的临时锁保证被命中的部分不被当成可腾出的空间（§4）。

文档对「保护」有个限定：【文档】`docs/docs/advanced_features/radix_eviction_policy.mdx:15`：原文："A policy only scores. It does not decide how much to free, and it cannot pin KV in memory"，后文 "a node protected by the policy is still evicted if reclaiming everything else is not enough." —— **策略层的保护是软的，锁才是硬的。**

### 9.5 其他可调

`--radix-eviction-policy`：`lru`（默认）、`lfu`、`slru`、`priority`、`tlru` 等；`--radix-eviction-policy-config` 传 JSON 参数：`python/sglang/srt/arg_groups/fields/memory.py:25-72`。`SGLANG_RADIX_FORCE_MISS`（默认 `False`）：把每次匹配当 0 命中，用来测不开缓存的开销：`python/sglang/srt/environ.py:803`，使用点 `python/sglang/srt/mem_cache/common.py:205`。【推断】（用途是我从名字和用法推的。）

---

## 10. 逐步推演：同一条请求，这个框里的状态怎么变

设定沿用顶层底稿 §8；**以下槽号、行号、节点名均为「设」**。

| 时刻 | 动作 | 树 | 请求行 r=7 | 账本 `req.kv` | 分配器 |
|---|---|---|---|---|---|
| 0 | 初始 | root→A(5 token，槽 101..105) | — | — | 空闲 N 个 |
| 1 | 匹配（限长 19） | A 全吃，第 6 个 token 没孩子 → 命中 5；沿路 A、root 的 `last_access_time` 刷新 | — | `prefix_len=5`，`last_node=A`，`protect_prefix(5)`（`python/sglang/srt/managers/schedule_batch.py:1215`） | — |
| 2 | 临时锁→预算→正式锁 | A.lock_ref=1，5 个 token 从 evictable 挪 protected | — | `req.lock` 指 A | — |
| 3 | `alloc_for_extend` | 不变 | `[101..105, 2001..2015]` | `protected=5`，`allocated=committed=20` | −15（槽 2001..2015），行 7 被占 |
| 4 | prefill 结束 → checkpoint | 挂叶子 B(15 token，槽 2001..2015)：root→A→B | 不变（值本来相同） | 锁从 A 移到 B；`protected=20`，`inserted=20` | 不变 |
| 5 | decode 第 1 步 | 不变 | 第 20 列 = 2016 | `allocated=committed=21` | −1 |
| 6 | decode 第 2..4 步 | 不变 | 第 21..23 列 = 2017..2019 | `allocated=committed=24`（EOS 在第 4 步采出） | 共 −4 |
| 7 | 结束 → checkpoint（`up_to=24`） | 挂叶子 C(4 token：X1..X4，槽 2016..2019)：root→A→B→C（overlap 多发一步则 C 为 5 个） | 第 20..23 列改写为树里那份（值相同） | 锁 B→C；`protected=24` | 不变 |
| 8 | `free_kv_row[(24,24)]` + unlock + 还行 | C 解锁，成可淘汰叶子 | 行 7 还回空闲表 | 记为已释放 | 无槽归还 |

**下一条同样的请求**（顶层底稿说命中 19）：限长 = 20−1 = 19。走 A（5）、B（15 个 token，只吃 14 个）→ 在 B 上**劈开**：B1(14，槽 2001..2014) + B2(1，槽 2015)；命中 5+14=19，只需算最后 1 个 token。劈开后树：root→A→B1→B2→C。锁在 B1 上（B2、C 无人锁）。

**如果这时显存满了**：叶子集合 = {C}（B2 有孩子 C，不是叶子）。C 先被淘汰；B2 变成叶子被压进堆，下一个可能轮到它。A、B1 被锁，不会被动。

---

## 11. 值得做成源码页的代码段

每页给「范围 + 逐行批注」。

### 页 1：匹配与「−1」

**1a** `python/sglang/srt/managers/schedule_batch.py:1238-1257`（`_compute_max_prefix_len`，20 行）

- `:1239` NOTE：命中最多比输入少 1。【注释】
- `:1240` `max_prefix_len = input_len - 1`：魔数 −1，为什么见 §3.4（【提交】`1e495e0847` + 【推断】）。
- `:1246-1247` `return_logprob and logprob_start_len >= 0` → 压到 `logprob_start_len`。为什么：要返回从某位置起的 prompt logprob，那些位置必须真过一遍模型。【推断】
- `:1257` `max(max_prefix_len, 0)`：短 prompt 也不会出现负数。

**1b** `python/sglang/srt/mem_cache/common.py:184-232`（`match_kv_cache`，49 行）

- 默认 `token_ids = origin_input_ids + output_ids`——被回撤再来的请求，已生成的也当 key。【推断】
- `:205` `SGLANG_RADIX_FORCE_MISS`：测试 / 基准钩子。
- `:209` `req.prefix_len = device_prefix_len`；`:210-218` 记 `last_node` 等：**只记位置不记槽号**，原因见 §3.3【文档】。
- 读者易错：函数叫 match，却在 `match_prefix` 里**会劈树**（`unified_tree_core.py:1071`）。

### 页 2：在树上走 + 劈节点

`python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1061-1095`（35 行）

- `:1061` `child_key in node.children`：键是首 token / 首页。
- `:1064-1066` HiCache 死节点 break：dense 路径不触发，**可跳过**。
- `:1068` `match_at`：倍增 + 二分。【注释】`radix_cache.py:194`。
- `:1070-1075` 劈节点后 `break`：命中边界必须落在节点上。【推断】
- `:1077-1078` `if not child.evicted: value.append(...)`：evicted 仅 HiCache 才可能。
- `:1085-1095` 交给 `_match_post_processor`，其中 `:1182-1186` 刷新沿路时间。

### 页 3：「先腾后取」

四个小函数连着读：

- `python/sglang/srt/mem_cache/allocation.py:196-215`（`alloc_token_slots`）：`:200` 取，`:204-213` 取不到就报错并打印可用 / 可淘汰量。
- `python/sglang/srt/mem_cache/static_kv_storage.py:269-278`（`take_tokens`）：**`:277` 先 `make_room`，`:278` 再 `alloc`**。顺序依赖：不先腾，`alloc` 就返回 `None`。
- `python/sglang/srt/mem_cache/allocator/base.py:248-261`（`evict_to_free_tokens`）：`:259` 只有不够才淘汰（懒）。
- `python/sglang/srt/mem_cache/allocator/token.py:54-63`（`alloc`）：`:58` 不够返回 None；`:61-62` 从头切。

### 页 4：写树

**4a** `python/sglang/srt/mem_cache/unified_radix_cache.py:1657-1680`（准备）+ `:1723-1760`（插入）

- `:1661` `full_untruncated_fill_ids[:up_to]`。
- `:1669-1671` 从请求行读槽号。
- `:1674-1680` `prev_prefix_len` / `inserted_len` 来自账本——去重和计命中都靠它。
- `:1725` `copy=True`：树要自己的拷贝。【推断】
- `:1740` `self.insert`。

**4b** `python/sglang/srt/mem_cache/unified_radix_cache.py:1762-1787`（prompt 边界切一刀）+ `:1791-1824`（改指、移锁）

- `:1772-1773` 只有单 Full 组件才切。
- `:1762` 注释；【提交】`0a57403468`。
- `:1800-1803` 请求行改指树里的槽。
- `:1805` 先 `unlock` 旧锁，`:1812` 再锁新节点。顺序为什么这样：没找到注释或提交说明（§12）。【推断】：两步之间同一线程内没有分配动作，不会有淘汰插进来。

**4c** `python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:1498-1541`（OVERLAP，去重）+ `:1558-1583`（COMMIT，新叶子）

- `:1517` `dup_start`：只有请求自己新算的才算重复。
- `:1540` `FreeDeviceKV`：真正还槽。
- `:1570` `_add_new_node`，`:1731` `value.clone()`。

### 页 5：结束释放

`python/sglang/srt/mem_cache/common.py:385-433`（49 行，**整段读**）

- `:388` 断言 `holds_kv` 与 `is_kv_released` 互斥。
- `:409` `owned_kv_len`、`:410` `skip_radix_cache_insert`。
- `:417-418` `refresh_fill_ids` +【注释】。
- `:419` checkpoint → `:421` free → `:422` unlock → `:424` over-allocated → `:431` 还行。顺序依赖：§8。
- `:420` 注释 "The protected prefix is not this req's to free."

### 页 6：锁与淘汰

- `python/sglang/srt/mem_cache/unified_cache/components/full.py:316-342`（Full 路径锁，27 行）：`:333-337` 首次加锁挪账、`:338` `lock_ref += 1`、`:339` 摘叶子集合。
- `python/sglang/srt/mem_cache/unified_cache/unified_tree_core.py:2659-2675`（`_is_device_leaf`，17 行）：`:2668` 有锁就不是；`:2670-2674` 有孩子带 KV 也不是。
- `python/sglang/srt/mem_cache/unified_cache/components/full.py:211-247`（堆）：`:219` heapify、`:235` 父节点回压、`:241` 弹堆顶。

---

## 12. 没查到为什么

- **默认实现何时从 `RadixCache` 换成 `UnifiedRadixCache`（对普通全注意力模型）**：只看到 `afa3c06d1f`（"Using UnifiedRadixTree by default for SWA, Mamba, and DSA models (#30468)"）标题只提 SWA / Mamba / DSA；`registry.py` 兜底那行的改动提交没定位到。我只能确认**现在**是这样。
- **`--page-size` 默认 `None` 之后在哪落成具体数**：只看到字段定义（`python/sglang/srt/arg_groups/fields/schedule.py:163`），没追到解析处。
- **为什么 `checkpoint` 在 prefill 一结束就写树**：文档只说「每个检查点都写」，没解释动机；`50be533d09` 只有标题。【推断】：分块 prefill 与并发相同前缀要尽早可见；无直接证据。
- **`strip_thinking_cache` 与 "#22373"**（`python/sglang/srt/mem_cache/common.py:442-443`）：只看到注释里的编号，没读到相关提交正文。
- **`unlock(req.lock)` 为何在 `lock(new_last_node)` 之前**（`python/sglang/srt/mem_cache/unified_radix_cache.py:1805`、`:1812`）：代码没注释，没找到相关提交。
- **为什么先插树再还槽 / 为什么树存槽号的拷贝**：代码没注释，上面都是推断。
- 提交说明整体偏薄：本稿涉及的关键提交（`1e495e0847`、`50be533d09`、`7623091d97`、`0a57403468`、`afa3c06d1f`）正文基本是空的，**「为什么」主要靠代码注释和 diff 里新增的注释**。PR / issue 正文读不到。

## 13. 可改进处【观点】

- 【观点】`BasePrefixCache.checkpoint` 的文档说 "Nothing here frees a" slot（`python/sglang/srt/mem_cache/base_prefix_cache.py:581`），实现里重叠段会 `FreeDeviceKV`（§6.3），文档应改成「只会释放重复的槽」。
- 【观点】被回撤（retract）的请求直接丢掉算好的 KV（`request_retirer.py:132` 的 TODO 已提），内存吃紧时反而增加重算。
- 【观点】`UnifiedRadixCache` 的普通路径夹在大量 SWA / Mamba / HiCache 分支里，新人读起来很吃力；`radix_cache.py` 的老实现反而更适合教学。
- 【观点】「树只在结束时写」这类说法容易误导（顶层说明卡就有）；建议在 `release_kv_cache` 旁加一句「prefill 后已写过一次」。

## 14. 给讲解员的读者提示

- **最容易困惑的 3 处**：① 匹配不拷槽号，只记「到哪」，槽号分配时才从树上读（所以必须先锁）；② 树在 prefill 后就写了，结束时只是补输出；③ 匹配也会劈节点。
- **适合对照着看的两件事**：「请求行 `req_to_token`（按位置）」和「树节点 `value`（按前缀）」——同一批槽号，从两个方向索引。checkpoint 的作用就是让两边指向同一份。
- **小机器图的连线建议只画这几根**：分配器 → 请求行（分槽）；树 → 请求行（读命中槽）与请求行 → 树（checkpoint 时改指）；树 → 分配器（淘汰 / 去重时还槽）；账本 `req.kv` 同时指向行和树节点（`last_node`、`lock`）。
- 「命中 5、算 15」可直接用 §10 的表；「命中 19」用表后那一段（B 被劈成 B1/B2）。
- 本稿所有「设」的数值：槽号 101..105、2001..2019，行号 7，节点名 A/B/C/B1/B2。
