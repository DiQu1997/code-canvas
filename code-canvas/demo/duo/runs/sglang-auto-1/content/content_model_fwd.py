# 顶层「模型前向 + 采样」框的下一层：框里 GPU 那一步怎么跑，以及「图回放」再下一层，直到源码。
# 事实全部来自 research/manuscript-model_fwd.md（HEAD = c569c43446）。
# 例子沿用顶层：prompt 20 token，命中 5、新算 15（位置 5..19），采到 X1、X2；底稿 §2 补设：占 req_to_token 第 3 行，
# 前缀槽 37..41，新分槽 212..226，decode 第 1 步分槽 227；H100、tp=1、FA3、简单采样；对照：decode 时 running 有 5 个请求。
from cv import *

TP = 'python/sglang/srt/managers/tp_worker.py'
SCH = 'python/sglang/srt/managers/scheduler.py'
SBT = 'python/sglang/srt/managers/schedule_batch.py'
OVL = 'python/sglang/srt/managers/overlap_utils.py'
BRP = 'python/sglang/srt/managers/scheduler_components/batch_result_processor.py'
FBI = 'python/sglang/srt/model_executor/forward_batch_info.py'
FM = 'python/sglang/srt/model_executor/forward_mode.py'
MR = 'python/sglang/srt/model_executor/model_runner.py'
CGS = 'python/sglang/srt/model_executor/model_runner_components/cuda_graph_setup.py'
CGC = 'python/sglang/srt/model_executor/cuda_graph_config.py'
ER = 'python/sglang/srt/model_executor/runner/eager_runner.py'
DCG = 'python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py'
PCG = 'python/sglang/srt/model_executor/runner/prefill_cuda_graph_runner.py'
BCR = 'python/sglang/srt/model_executor/runner/base_cuda_graph_runner.py'
CGBR = 'python/sglang/srt/model_executor/cuda_graph_buffer_registry.py'
FCG = 'python/sglang/srt/model_executor/runner_backend/full_cuda_graph_backend.py'
BCGF = 'python/sglang/srt/model_executor/runner_backend_utils/breakable_cuda_graph/breakable_cuda_graph.py'
CGH = 'python/sglang/srt/arg_groups/cuda_graph_hook.py'
MH = 'python/sglang/srt/arg_groups/memory_hook.py'
MOB = 'python/sglang/srt/arg_groups/model_override_base.py'
FA = 'python/sglang/srt/layers/attention/flashattention_backend.py'
BAB = 'python/sglang/srt/layers/attention/base_attn_backend.py'
RA = 'python/sglang/srt/layers/radix_attention.py'
LP = 'python/sglang/srt/layers/logits_processor.py'
SMP = 'python/sglang/srt/layers/sampler.py'
SBI = 'python/sglang/srt/sampling/sampling_batch_info.py'
SPAR = 'python/sglang/srt/sampling/sampling_params.py'
LL = 'python/sglang/srt/models/llama.py'
MP = 'python/sglang/srt/mem_cache/memory_pool.py'
ALLOC = 'python/sglang/srt/mem_cache/allocation.py'
DEV = 'python/sglang/srt/utils/device.py'
TPAD = 'test/registered/unit/model_executor/runner/test_prefill_cuda_graph_padding.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数）
def sb_g(ids, sl, loc):
    return g_kv([('input_ids', ids), ('seq_lens', sl), ('out_cache_loc', loc)])


def fb_g(pos, ext):
    return g_kv([('借用', '4 个张量'), ('positions', pos), ('extend_*', ext)])


def fork_g(d, p, e, dtxt='decode 图？', ptxt='prefill 图？'):
    return g_checks([(dtxt, d), (ptxt, p), ('eager 兜底', e)])


def model_g(q, k, attn):
    return g_kv([('query', q), ('读到', k), ('attention', attn)])


def row_g(decode=False, new=False):
    cells = [cell('37…41', '前缀 5'), cell('212…226', '新 15', 'new' if new and not decode else None)]
    if decode:
        cells.append(cell('227', 'decode', 'new'))
    return g_cells(cells, left='行 3')


def samp_g(c, tok=None):
    return g_checks([('全贪心 → argmax', 'skip' if c else None), ('top-p/k → 融合核', 'skip' if c else None),
                     ('简单 → Gumbel → ' + (tok or '?'), 'ok' if tok else None)])


def fmap_g(tok, sl):
    return g_kv([('token[3]', tok), ('seq_len[3]', sl)])


def stream_g(on=None):
    return g_list(['schedule 流：分槽、写 req_to_token', 'forward 流：取输入 → 前向 → 采样 → 存 future_map',
                   'copy 流：等 forward 流，异步拷回 CPU'], on=on)


def pad_cells(n_real, n_pad):
    return [cell('3', '真'), cell('…', '真×%d' % (n_real - 1))] + [cell('0', '假', 'ghost') for _ in range(n_pad)]


# ================================================================ 第一层：模型前向 + 采样里面
def fwd_scene():
    parts = [
        part('sbatch', '这一批进来', [16, 10, 190, 140], m=[8, 8, 240, 136], kind='ext', n='1', code='ScheduleBatch',
             glyph=sb_g('15 个 · CPU', '[20]', '212…226'),
             say='槽已分好，输入刚上 GPU', why='Scheduler 把它当一次事务', trap='和 Scheduler 是同一个对象',
             card=card(
                 ['Scheduler 这一圈选好的批。进这个框时，它要写哪些 KV 槽（`out_cache_loc`）、占 `req_to_token` 哪一行，都已经在 Scheduler 侧定好了。',
                  '这个框做的第一件事是把**输入 token id** 放到 GPU 上。'],
                 ['`run_batch` 进入 forward 流，第一句 `forward_stream.wait_stream(schedule_stream)`：等 Scheduler 侧分槽、写表的活都排完。' + R(SCH, 5432, 5433),
                  'prefill：15 个 id 先留在固定内存（pinned）CPU 上，到这里 `resolve_forward_inputs` 才在 forward 流上 H2D。' + R(SBT, 2423) + ' ' + R(SCH, 5442, 5444),
                  'decode：`batch.input_ids` 是 `None`，直接从 `future_map.output_tokens_buf[req_pool_indices]` 取上一步采到的 token。' + R(OVL, 165, 166)],
                 [note('注释', 'Scheduler 把这一批当成一次前向里的「事务」：前向期间字段被快照、钉住。',
                       q='Make SB transactional across one forward (overlap and non-overlap).', ref=A(SCH, 5310)),
                  note('推断', 'decode 的输入不经 CPU：上一步的 token 还在 GPU 上，直接拿来用，不用等它拷回 CPU 再拷上来。')],
                 ['这里的 `ScheduleBatch` 和 Scheduler 手里的是**同一个对象**：overlap 下 GPU 还在读它时，Scheduler 已经在为下一圈准备了。',
                  '为什么 prefill 的 H2D 要推迟到 forward 流上做，注释只说了「推迟」，没说原因。']),
             to='model_fwd.src_stream'),
        part('fb', '打包', [336, 10, 190, 140], m=[8, 208, 240, 136], kind='act', n='2', code='ForwardBatch.init_new',
             glyph=fb_g('—', '—'),
             say='大半借用，新算位置', why='不复制，也不许改', trap='看着新，其实多是别名',
             card=card(
                 '把 `ScheduleBatch` 包成模型能吃的 `ForwardBatch`（GPU 张量批）。',
                 ['大部分字段是**别名**：`input_ids / req_pool_indices / seq_lens / out_cache_loc` 直接用 SB 上同一批张量。' + R(FBI, 1029, 1048),
                  '真正新算的主要是 `positions`：decode 是 `seq_lens-1`；extend 是「前缀长 + 偏移」（Triton 核算），另有 `extend_seq_lens / extend_prefix_lens / extend_start_loc`。' + R(FBI, 1216, 1260),
                  '`sampling_info` 也只是引用：Scheduler 事先已把它换成只给这次前向用的拷贝。' + R(FBI, 988)],
                 [note('注释', '打包不许改 SB；要改就显式地覆盖在 ForwardBatch 上。',
                       q='init_new must not mutate ScheduleBatch; use explicit forward overrides.', ref=A(FBI, 952)),
                  note('推断', 'overlap 下 GPU 还在读这些张量时，Scheduler 已经拿同一个 SB 准备下一圈：借用省了拷贝，前提是这边谁都不改它。'),
                  note('注释', 'extend 的长度元数据（设备支持时）由 kernel 写到 GPU，不走 pinned H2D：免得在拷贝引擎上排在大块传输后面，拖慢这次前向。',
                       q='Written by kernels: a pinned host-to-device copy would queue', ref=A(FBI, 1225))],
                 ['`ForwardBatch` 看着是新对象，其实大半是 SB 张量的别名。',
                  '`input_ids` 不是这里造的：进框时就已经解析好了。',
                  'decode 时 extend 专用的字段全是 `None`。' + R(FBI, 972)]),
             to='model_fwd.src_pack'),
        part('fork', '怎么跑：三选一', [656, 10, 200, 140], m=[8, 408, 240, 150], kind='gate', n='3', code='_forward_raw',
             glyph=fork_g(None, None, None),
             say='decode 图 → prefill 图 → eager', why='先问最省事的那条', trap='prefill 默认也走图',
             card=card(
                 '决定这一批怎么在 GPU 上跑：回放录好的 decode 图、回放 prefill 图，还是 eager 直接跑。三条路跑的是同一个模型。',
                 ['先问 decode 图：模式要在白名单里（`DECODE / TARGET_VERIFY / IDLE / DLLM_EXTEND`），且 runner 说能跑；能就直接返回。' + R(MR, 2119, 2146) + ' ' + R(FM, 92, 98),
                  '不行 → 先做 DP / MLP-sync 补齐，再问 prefill 图：是 extend、runner 不是 `EagerRunner`、`can_run_graph` 为真等 6 个条件。' + R(MR, 2154) + ' ' + R(MR, 2164, 2173),
                  '都不行 → `eager_runner.execute`。' + R(MR, 2190, 2193)],
                 [note('注释', 'decode 图那条路自己预先填好静态缓冲，所以排在最前、提前返回；prefill 图和 eager 跑的是「活的」批，要先补齐。',
                       q='cuda-graph path above pre-pads its static buffers and returns', ref=A(MR, 2149, 2150)),
                  note('注释', '`EagerRunner` 启动时总会建出来：图全关时 runner 槽位里放的就是它，图跑不了时它兜底。',
                       q='Always built: it serves both the fully-disabled case (decode/prefill', ref=A(CGS, 428)),
                  note('提交', 'CUDA 上 prefill 默认也开图（breakable）。',
                       q='Enable Breakable Cuda Graph as Default (#29458)', ref='c:0543246184')],
                 ['普通 EXTEND（prefill）**永远进不了 decode 图**：白名单里没有它。',
                  '顶层卡片说「prefill 通常走另一条路」，在 HEAD 上要更正：CUDA 上 prefill 默认也回放图，例子里 15 个 token 走档 16。' + R(CGC, 119, 123),
                  '代码 `:2174` 的注释还写着 "piecewise"，是遗留：分段后端 `tc_piecewise` 已删掉，现在默认是 breakable。' + R(MR, 2174) + ' ' + R(CGC, 185)]),
             to=['model_fwd.graph', 'model_fwd.src_fork']),
        part('model', '模型躯干：逐层 attention', [996, 10, 208, 140], m=[8, 622, 240, 136], kind='act', n='4', code='model.forward',
             glyph=model_g('—', '—', '—'),
             say='每层先写新 k/v，再读历史', why='元数据建一次，层层复用', trap='prefill 图里它不在图里',
             card=card(
                 '跑 transformer 的 L 层。每层 attention 先把本步算出的 k/v 写进 KV 池，再按页表读出这条请求全部历史的 k/v。',
                 ['每次前向开头建一次 attention 元数据：`page_table = req_to_token[req_pool_indices, :最大长度]`。' + R(FA, 1310, 1312),
                  '同时算 `cache_seqlens`（每个请求的总长）和 `cu_seqlens_q`（每个请求的 query 从哪开始）。' + R(FA, 1333, 1338),
                  '调用链：`LlamaAttention` → `RadixAttention` → attention 后端（H100 默认 FA3）→ `forward_extend / forward_decode`。' + R(LL, 294),
                  '每层：先 `store(k, v)` 写到 `out_cache_loc` 的槽。' + R(FA, 1714),
                  '再 `flash_attn_with_kvcache(page_table=…)` 按页表读。' + R(FA, 2066, 2073)],
                 [note('注释', '元数据每次前向只建一次，所有层复用。',
                       q='Initialize forward metadata hence all layers in the forward pass can reuse it.', ref=A(FA, 1041)),
                  note('推断', '先写后读：本步新 token 的 k/v 要先落进池子，读页表时才读得到（它们的槽号在分配阶段已经写进 `req_to_token`）。'),
                  note('注释', 'eager 路径上页表宽度优先用 CPU 镜像里的最大长度，没有就用静态上限，不做 GPU→CPU 同步。',
                       q='Eager (non-cuda-graph) path: max_seq_len_k only feeds Python-side', ref=A(FA, 1046))],
                 ['FA3 默认与否看条件：MHA + Hopper + CUDA≥12.3 + 非投机；Blackwell 默认 `trtllm_mha`。' + R(MOB, 333, 338),
                  'prefill 图里 attention **不在图里**：它是两段图之间的断点（见 [[model_fwd.graph|图回放里面]]）。']),
             to='model_fwd.src_attn'),
        part('kv', '页表 + KV 池', [996, 208, 208, 140], m=[8, 822, 240, 136], kind='store', n='5', code='req_to_token',
             glyph=row_g(),
             say='第 3 行：每个 token 的 KV 在几号槽', why='存槽号，不连续也能找', trap='这个框里不改这张表',
             card=card(
                 '两样东西：`req_to_token` 记「第 r 个请求的第 j 个 token，KV 在几号槽」；KV 池按槽号存每一层的 K/V。',
                 ['`req_to_token` 是 `int32 [size+1, max_context_len]`：行 = 请求，列 = 第几个 token，值 = 槽号。' + R(MP, 422, 424),
                  '这个框里**不写** `req_to_token`：分槽、填表都是 Scheduler 在前向之前做的（`alloc_for_extend / alloc_for_decode`）。' + R(ALLOC, 524) + ' ' + R(ALLOC, 736),
                  '框里只做两件事：每层往 KV 池写本步的 k/v；按 `req_to_token` 那一行读历史。'],
                 [note('注释', '多留一个第 0 行：CUDA graph 补出来的假请求都指向第 0 行，读写落在这里无害。',
                       q='+1 padding row at index 0: cuda-graph padded batches default', ref=A(MP, 416)),
                  note('推断', '表里存槽号而不存 KV 本身：前缀命中的 37…41 和新分的 212…226 不连续也没关系，attention 按槽号去拿。')],
                 ['写和读是**两条独立的线**：写看 `out_cache_loc`（本步新槽），读看页表（整行历史）。',
                  'decode 第 1 步写的是第 20 列（旧的 `seq_lens`），写完 `seq_lens` 才变成 21。' + R(ALLOC, 734, 737)])),
        part('logits', '只取最后一位', [656, 208, 200, 140], m=[8, 1022, 240, 124], kind='act', n='6', code='LogitsProcessor',
             glyph=g_text('—', '还没算'),
             say='每个请求只留 1 行过 lm_head', why='15 行只算 1 行', trap='decode 图里有它，prefill 图外',
             card=card(
                 '把最后一层隐藏状态变成「下一个 token 的分数」（logits）。每个请求只要一行。',
                 ['先挑行：decode 每个请求本来就 1 行；prefill 且不要输入 logprob 时，只取每个请求的最后一个位置 `cumsum(extend_seq_lens) - 1`。' + R(LP, 960, 966),
                  '挑完才过 lm_head。' + R(LP, 807)],
                 [note('注释', 'prefill 不要输入 logprob 时只取最后一位。', q='Prefill without input logprobs.', ref=A(LP, 964)),
                  note('推断', 'lm_head 是「隐藏维 × 词表」的大矩阵乘：15 行只留 1 行，这一步就少算 14 行。'),
                  note('注释', 'prefill 图只录躯干，把 lm_head + logits 留在图外 eager 跑，图就和请求数无关。',
                       q='BCG and Full CG capture only the transformer body (layer_model.forward),', ref=A(PCG, 739))],
                 ['decode 图里**包含** lm_head + logits；prefill 图里**不含**（图外的 eager 尾巴）。',
                  '要了 logprob 的请求会多留几行；本例不要。'])),
        part('sampler', '采样', [336, 208, 190, 140], m=[8, 1210, 240, 150], kind='act', n='7', code='Sampler',
             glyph=samp_g(False),
             say='logits → 每请求 1 个 token', why='每个 TP rank 各采各的', trap='采样不在任何图里',
             card=card(
                 'logits → 每个请求 1 个 token id：`next_token_ids`，GPU 上的 int32 `[bs]`。',
                 ['在图外单独调：`tp_worker` 在 `model_runner.forward` 之后调 `model_runner.sample`。' + R(TP, 769, 773),
                  '先预处理：惩罚项、grammar 掩码、`logit_bias`；掩码用完立刻释放。' + R(MR, 2203) + ' ' + R(MR, 2228),
                  '`Sampler.forward`：全贪心 → argmax；否则 softmax（温度融合，原位覆盖 logits）→ 没有 top-p/top-k/min-p 走简单采样（默认 Gumbel-max），有就走 flashinfer 融合核。' + R(SMP, 212, 233) + ' ' + R(SMP, 299, 300)],
                 [note('注释', 'Gumbel-max：分布上和 multinomial 等价，但省掉拖慢 decode 关键路径的 CPU 侧簿记和合法性断言。',
                       q="avoids multinomial's CPU-side philox offset bookkeeping and", ref=A(SMP, 1052)),
                  note('注释', 'TP 下默认不做 all-reduce 对齐 token：只要最后的 all-reduce、lm_head 和采样核在各 rank 上一致，各自采到的就是同一个。',
                       q='Off by default to save an all-reduce; correct only while the last all-reduce,', ref=A(SMP, 756)),
                  note('推断', '采样不录进 decode 图：路径上有很多主机侧分支（全贪心？要 top-p？）和每步现算的 grammar 掩码。代码里没有明说。')],
                 ['「贪心」在这里就是 `top_k == 1`：温度趋近 0 时参数被改成 `temperature=1.0, top_k=1`。' + R(SPAR, 263, 266),
                  'overlap 下有 grammar 时，采样会被**推迟**到处理完上一批结果之后。' + R(TP, 745, 767)]),
             to=['model_fwd.src_sample', 'model_fwd.src_worker']),
        part('fmap', '留给下一步', [16, 208, 190, 140], m=[8, 1424, 156, 124], kind='store', n='8', code='future_map',
             glyph=fmap_g('—', '—'),
             say='token 留在 GPU 上', why='下一步直接取', trap='下标是 req_pool_idx',
             card=card(
                 'GPU 上两块按 `req_pool_idx` 下标的缓冲：本步采到的 token、新的 `seq_lens`。下一步 decode 从这里取输入。',
                 ['`publish(future_indices, seq_lens + 1)`，`future_indices` 就是 `req_pool_indices`。' + R(SCH, 5447) + ' ' + R(SCH, 5475),
                  'token 经 `_relay_forward_payload` 写进 `output_tokens_buf[req_pool_indices]`。' + R(SCH, 5495, 5497),
                  '下一步：`input_ids = output_tokens_buf[req_pool_indices]`。' + R(OVL, 166)],
                 [note('注释', '下一圈的 input_ids 经 future_map 转交，不从 CPU 来。',
                       q='Next-iter input_ids relayed via future_map.', ref=A(SCH, 5510)),
                  note('推断', '写（publish）和下一步的读（gather）都在 forward 流上，流内天然有序，不用再加事件。')],
                 ['下标是 `req_pool_idx`（本例 3），不是递增的「未来编号」。',
                  '它的 slot 0 也留作补齐用，和 KV 的第 0 行对齐。' + R(OVL, 324)])),
        part('copy', '拷回 CPU', [336, 400, 190, 116], m=[176, 1424, 156, 124], kind='out', n='9', code='copy_stream',
             glyph=g_text('—', '还没拷'),
             say='异步拷，下一圈才读', why='与下一批重叠', trap='下一步输入不走这',
             card=card(
                 '把 `next_token_ids` 等结果拷到 CPU 的固定内存，给 Scheduler 下一圈处理用（记账、判结束、发给反分词）。',
                 ['`copy_stream.wait_stream(forward_stream)`，再在 copy 流上 `non_blocking` 拷，配一个 `copy_done` 事件。' + R(SCH, 5831) + ' ' + R(DEV, 652, 661),
                  'CPU 直到下一圈处理结果时 `copy_done.synchronize()` 才真的等 GPU。' + R(BRP, 403)],
                 [note('注释', '回读放在 copy 流上，和下一批前向重叠。',
                       q='Issue the result readback on copy_stream so it overlaps the next forward.', ref=A(SCH, 5820))],
                 '拷回 CPU 的这份只用来记账；下一步 decode 的输入**不走**这条路，走 future_map。')),
        part('streams', '三条 CUDA 流', [656, 406, 548, 108], m=[8, 1612, 324, 120], kind='bg', code='wait_stream',
             glyph=stream_g(),
             say='一个线程把活排到三条流上就返回', why='流间用 wait_stream 排先后，不用锁', trap='CPU 调前向几乎立刻返回',
             card=card(
                 '这个框里的活分在三条 CUDA 流上排队。调度线程只有一个：它把活排到流上就返回，GPU 内部的先后靠流之间的等待。',
                 ['schedule 流：事件循环本身，分槽、写 `req_to_token`、`prepare_for_decode`。' + R(SCH, 2172),
                  'forward 流：取输入 → 打包 → 前向 → 采样 → 存 future_map。' + R(MR, 503) + ' ' + R(SCH, 5432, 5475),
                  'copy 流：等 forward 流，异步把结果拷回 CPU。' + R(SCH, 1857),
                  '反方向还有一道 WAR 屏障：Scheduler 为下一批改共享缓冲前，schedule 流要等「forward 流读完它们」的事件。' + R(SCH, 2185) + ' ' + R(SCH, 2283)],
                 [note('注释', '两条流要是从池里领到同一条，就没有重叠了，所以会重抽。',
                       q='stream aliases forward_stream, which would eliminate scheduler', ref=A(SCH, 2160)),
                  note('提交', 'WAR 屏障从「等整个前向」收窄到「等读完」，换回 decode 吞吐（据提交标题）。',
                       q='[core] Gate the overlap WAR barrier on forward reads to recover decode throughput (#28363)', ref='c:106d2930a6'),
                  note('注释', '跨流用到的张量要钉住引用，免得 forward 流还在用时被回收。',
                       q='FIXME(lsyin): hacky way to keep a reference to avoid GPU tensors being freed by torch GC', ref=A(SCH, 5286))],
                 ['CPU 调 `forward_batch_generation` 几乎立刻返回：它只是在 forward 流上排好了 kernel。',
                  '只有 Confidential Computing 下才多一个专做 D2H 的线程。' + R(SCH, 1874)]),
             to='model_fwd.src_stream'),
    ]

    wires = [
        wire('w_in', 'sbatch', 'fb', '借用张量', n='1', why='别名，不复制', trap='SB 不许被改',
             card=card('把 `ScheduleBatch` 交给 `ForwardBatch.init_new`。',
                       '在 `forward_batch_generation` 里调用：`ForwardBatch.init_new(batch, self.model_runner, …)`。' + R(TP, 657, 664),
                       [note('推断', '借用而不复制：几个张量不用再拷一份。')],
                       '中间曾有一层 `ModelWorkerBatch`，已经去掉。')),
        wire('w_fb', 'fb', 'fork', 'ForwardBatch', n='2', why='模式决定先问谁',
             card=card('`ForwardBatch` 交给 `ModelRunner._forward_raw`。',
                       '`forward_mode` 是 `EXTEND`（prefill）还是 `DECODE`，决定先问哪个 runner。' + R(MR, 2114, 2123),
                       [note('注释', 'EXTEND 就是常说的 prefill。', q='It is also called "prefill" in common terminology.', ref=A(FM, 22))])),
        wire('w_run', 'fork', 'model', '回放 / eager', n='3', why='同一模型，发 kernel 方式不同',
             card=card('选定的 runner 跑模型 forward。',
                       ['decode 图：`load_batch` 填静态缓冲 → `replay()`，整个模型连 logits 一次回放。' + R(DCG, 2189),
                        'prefill 图：回放躯干段，每层 attention 处断开、eager 调用；lm_head + logits 在图外。' + R(PCG, 2391, 2392),
                        'eager：`model.forward(input_ids, positions, forward_batch)`。' + R(ER, 242)],
                       [note('推断', '三条路跑的是同一个模型、读同一份 KV，差别只在 kernel 怎么发出去：回放录好的图，省掉 CPU 一个个发 kernel 的开销。')]),
             to='model_fwd.graph'),
        wire('w_kvw', 'model', 'kv', '写 k/v', n='4', lp=[1040, 179], mlp=[70, 790], why='先写才读得到',
             card=card('每层把本步算出的 k、v 存进 `out_cache_loc` 指的槽。',
                       '`store(…, KVWriteLoc.for_layer(…), k, v)`；`KVWriteLoc.loc` 就是 `out_cache_loc`。' + R(FA, 1714, 1727) + ' ' + R(MP, 1765, 1788),
                       [note('推断', '写在读之前：本步的新 token 也要被自己和后面的位置看到。')],
                       'prefill 例子写 15 个槽 212…226；decode 第 1 步只写 1 个槽 227。')),
        wire('w_kvr', 'kv', 'model', '按页表读', n='4', lp=[1150, 179], mlp=[200, 790], why='读整行历史',
             card=card('kernel 对第 i 个请求，用 `page_table[i, j]` 取第 j 个历史 token 的槽，到 K/V 池里 gather。',
                       ['`page_table` 就是 `req_to_token` 按 `req_pool_indices` 取出的那几行。' + R(FA, 1154, 1156),
                        '`flash_attn_with_kvcache(q, k_cache, v_cache, page_table, cache_seqlens, cu_seqlens_q, …)`。' + R(FA, 2066, 2073)],
                       [note('注释', '图回放路径上，页表用静态上限的宽度，不用把 GPU 上的长度读回 CPU。',
                             q='Page table uses the static max_num_pages bound (no D2H).', ref=A(FA, 3463))],
                       '读的是整行历史：前缀 5 个 + 本步 15 个 = 20 个槽。')),
        wire('w_hid', 'model', 'logits', '隐藏状态', n='5', fs='l', ts='r', mfs='r', mts='r', mlp=[296, 892],
             card=card('最后一层的隐藏状态交给 `logits_processor`。',
                       '`LlamaForCausalLM.forward` 末尾：`return self.logits_processor(input_ids, hidden_states, self.lm_head, forward_batch, …)`。' + R(LL, 634),
                       [note('推断', 'prefill 时隐藏状态有 15 行（每个位置一行），到下一个框才挑出要的那一行。')])),
        wire('w_lg', 'logits', 'sampler', '[bs, vocab]', n='6', why='两种图之后汇到一处', trap='采样不在图里',
             card=card('`next_token_logits` 交给采样。',
                       ['decode 图：回放出「档位」那么多行，按真实行数切 `[: raw_num_token]` 再往下交。' + R(DCG, 2209, 2213),
                        'prefill 图：logits 本来就在图外算。'],
                       [note('推断', '采样在图外：不管走哪条路，最后都回到同一个 Sampler。')])),
        wire('w_tok', 'sampler', 'fmap', '存进 future_map', ml='存 token', n='7', mlp=[70, 1392], why='GPU 上直接转交', trap='CPU 还没看到',
             card=card('`next_token_ids` 和 `seq_lens+1` 写进 future_map。',
                       ['`publish(future_indices, batch.seq_lens + 1)`。' + R(SCH, 5475),
                        'token 由 `_relay_forward_payload` 写进 `output_tokens_buf`。' + R(SCH, 5495, 5497)],
                       [note('推断', '下一步的输入留在 GPU 上：不用等 D2H，下一步就能发射。')])),
        wire('w_d2h', 'sampler', 'copy', '异步拷回', n='8', mlp=[256, 1392], why='与下一批重叠', trap='下一圈才读',
             card=card('在 copy 流上把结果异步拷到 CPU 固定内存。',
                       '`_copy_result_to_cpu`：`copy_stream.wait_stream(forward_stream)` → `async_d2h`。' + R(SCH, 5817, 5831),
                       [note('注释', '回读放在 copy 流上，和下一批前向重叠。',
                             q='Issue the result readback on copy_stream so it overlaps the next forward.', ref=A(SCH, 5820))])),
    ]

    steps = [
        beat('进框：等 schedule 流，搬输入',
             '第 n 圈，`run_batch` 进入 forward 流，先 `wait_stream(schedule_stream)`：Scheduler 侧分好的 15 个槽 **212…226**、写好的 `req_to_token` 第 3 行，都排在它前面。然后 `resolve_forward_inputs` 把 pinned CPU 上的 **15 个 token id**（prompt 第 5…19 个）H2D 到 GPU。',
             note('注释', 'prefill 的 id 先留在 pinned CPU，H2D 推迟到 forward 流上做。',
                  q='Stay on pinned CPU; H2D is deferred to forward stream via', ref=A(SBT, 2423)),
             focus=['sbatch', 'streams'],
             state={'sbatch': sb_g('15 个 → GPU', '[20]', '212…226'), 'streams': stream_g([0, 1])}),
        beat('打包：大半是别名',
             '`init_new`：`input_ids`、`req_pool_indices=[3]`、`seq_lens=[20]`、`out_cache_loc=[212…226]` 直接借 SB 的张量。新算的只有 `positions=[5,…,19]`（前缀 5 + 偏移 0…14），和 `extend_prefix_lens=[5]`、`extend_seq_lens=[15]`、`extend_start_loc=[0]`。',
             note('注释', '打包不许改 SB。', q='init_new must not mutate ScheduleBatch; use explicit forward overrides.', ref=A(FBI, 952)),
             focus=['fb', 'w_in', 'sbatch'],
             state={'fb': fb_g('5 … 19', '前缀 5 · 新 15'), 'streams': stream_g(1)}),
        beat('分叉：prefill 也走图',
             '`forward_mode=EXTEND` 不在 decode 图的白名单里 → 跳过。补齐之后问 prefill 图：15 个 token，≥15 的最小档是 **16**；`16 ≤ 15×2` → 可以回放，补 **1** 个 padding token。',
             note('注释', '不要求形状精确：按档补齐，只拒绝补得太多的。',
                  q='No exact-shape check: load_batch bucket-pads; only reject', ref=A(PCG, 1564)),
             focus=['fork', 'w_fb', 'w_run'],
             state={'fork': fork_g('no', 'ok', 'skip', dtxt='decode 图？不收 EXTEND', ptxt='prefill 图？15 → 档 16')}),
        beat('每层：先写 15 个，再读 20 个',
             '开头建一次元数据：`page_table = req_to_token[[3], :20] = [37…41, 212…226]`，`cache_seqlens=[20]`，`cu_seqlens_q=[0,15]`。每层先把 15 个新 k/v 写进槽 **212…226**，再让 15 个 query 按页表对 **20 个槽**做因果注意力。prefill 图里 attention 是断点，eager 调 FA3。',
             note('推断', '先写后读：新 token 自己也在要读的 20 个槽里。'),
             focus=['model', 'w_kvw', 'kv', 'w_kvr'],
             state={'model': model_g('15 个', '20 个槽', '断点·eager'), 'kv': row_g(new=True)}),
        beat('只取最后一位',
             '躯干出来的隐藏状态有 15 行；`logits_processor` 只取 `cumsum([15]) - 1 = 14` 这一行过 lm_head → `next_token_logits` 形状 **[1, vocab]**。这一段在 prefill 图**外**，用活的批 eager 跑。',
             note('注释', 'prefill 图只录躯干，lm_head + logits 留在图外。',
                  q='BCG and Full CG capture only the transformer body (layer_model.forward),', ref=A(PCG, 739)),
             focus=['logits', 'w_hid'],
             state={'logits': g_text('[1, vocab]', '只算下标 14 · 图外')}),
        beat('采样 → X1',
             '没有 grammar、惩罚、bias。不是全贪心（temperature>0）；top_p=1、top_k=-1、min_p=0 → 简单情形：softmax（温度融合，原位覆盖 logits）→ Gumbel-max 取 argmax → `next_token_ids=[X1]`，还在 GPU 上。',
             note('注释', 'Gumbel-max 省掉拖慢 decode 关键路径的 CPU 侧簿记。',
                  q="avoids multinomial's CPU-side philox offset bookkeeping and", ref=A(SMP, 1052)),
             focus=['sampler', 'w_lg'],
             state={'sampler': samp_g(True, 'X1')}),
        beat('两个出口：GPU 上留一份，CPU 拷一份',
             '仍在 forward 流上：`publish` 把 `seq_lens+1 = 21` 写进 future_map 第 3 格，X1 写进 `output_tokens_buf[3]`。然后 copy 流等 forward 流，把 X1 异步拷到 CPU 固定内存，配 `copy_done` 事件；`batch.input_ids=None`。CPU 早已返回，去处理上一批了。',
             note('注释', '回读放在 copy 流上，和下一批前向重叠。',
                  q='Issue the result readback on copy_stream so it overlaps the next forward.', ref=A(SCH, 5820)),
             focus=['fmap', 'w_tok', 'copy', 'w_d2h', 'streams'],
             state={'fmap': fmap_g('X1', '21'), 'copy': g_text('X1 → CPU', 'copy_done 还没到', tone='run'),
                    'streams': stream_g([1, 2])}),
        beat('第 n+1 圈 · decode：输入不经 CPU',
             'schedule 流上先分槽 **227**、写 `req_to_token[3,20]=227`、`seq_lens` 20→21。forward 流：`input_ids = output_tokens_buf[[3]] = [X1]`——CPU 这时还没见过 X1。打包：`positions=[20]`（`seq_lens-1`），extend 字段全是 `None`。',
             note('注释', '下一圈的 input_ids 经 future_map 转交。', q='Next-iter input_ids relayed via future_map.', ref=A(SCH, 5510)),
             focus=['sbatch', 'fmap', 'fb', 'streams'],
             state={'sbatch': sb_g('[X1] ← future_map', '[21]', '[227]'), 'fb': fb_g('[20]', 'None'),
                    'streams': stream_g([0, 1])}),
        beat('decode 图：档 1，整段回放',
             '`DECODE` 在白名单里；`can_run_graph`：bs=1 ≤ 256 → 取档 **1**，没有补齐。`load_batch` 把张量拷进静态缓冲，FA3 把 `req_to_token[3, 0:21]` 写进预分配的页表，然后 `replay()`：整个模型 + lm_head + logits 一次回放。',
             note('推断', 'decode 每个请求固定 1 个 token，形状只由 bs 决定；每步变的都是张量里的数据，覆盖进静态缓冲后重放同一串 kernel 就行。'),
             focus=['fork', 'w_run', 'model'],
             state={'fork': fork_g('ok', 'skip', 'skip', dtxt='decode 图？bs 1 → 档 1'),
                    'model': model_g('1 个', '21 个槽', '在图里')}),
        beat('写 227、读 21，采到 X2',
             '图里每层：k/v 写进槽 **227**，按页表读 21 个槽；lm_head 也在图里 → `[1, vocab]`。回到图外 `sample` → **X2**；同样写进 `output_tokens_buf[3]`（`seq_len` 记 22），再异步拷回。',
             note('推断', '采样在图外：图只吐 logits。'),
             focus=['kv', 'model', 'logits', 'sampler', 'fmap'],
             state={'kv': row_g(decode=True), 'logits': g_text('[1, vocab]', 'decode：在图里算'),
                    'sampler': samp_g(True, 'X2'), 'fmap': fmap_g('X2', '22'),
                    'copy': g_text('X2 → CPU', 'copy_done 还没到', tone='run')}),
        beat('对照：5 个请求 → 档 8',
             '如果 running 里有 5 个请求：`_pad_to_bucket(5, [1,2,4,8,…])` = **8**，多出 3 个假请求：`req_pool_indices=0`、`seq_lens=1`、`out_cache_loc=0`，读 `req_to_token` 第 0 行、写 KV 第 0 号槽。回放出 8 行 logits，`[:5]` 切掉假的 3 行再采样。见 [[model_fwd.graph|图回放里面]]。',
             note('注释', '第 0 行是专门留给假请求的。', q='+1 padding row at index 0: cuda-graph padded batches default', ref=A(MP, 416)),
             focus=['fork', 'kv'],
             state={'fork': g_kv([('真实', '5 行'), ('补齐', '3 行 → 第 0 号'), ('档位', '8')])}),
    ]

    return scene(1220, 520, parts, wires, steps, mw=340, mh=1744,
                 cap='顶层「模型前向 + 采样」框里面：第 n 圈 prefill（15 个位置）→ 第 n+1 圈 decode')


# ================================================================ 第二层：图回放里面
def graph_scene():
    parts = [
        part('d_bucket', '选档', [16, 36, 200, 140], m=[8, 30, 196, 130], kind='gate', n='1', code='_pad_to_bucket',
             glyph=g_cells([cell('1'), cell('2'), cell('4'), cell('8'), cell('12'), cell('…256')], left='bs=?'),
             say='补到最近的录过的档', why='图只能按录过的形状回放', trap='decode 按请求数分档',
             card=card(
                 '把真实请求数补到最近的「录过的档」：一张图只能按录制时的形状回放。',
                 ['decode 档位（非投机）：`[1,2,4,8,12]` + 16…256 步 8 + 272…512 步 16 + …，再按 `max_bs` 截断。' + R(CGH, 607, 611),
                  '`max_bs` 默认看显存：H100/A100 档、tp<4 取 256 → 本例共 36 个档。' + R(MH, 137, 148),
                  '取「≥ 实际 bs 的最小档」（`bisect_left`）；超过最大档的批，`can_run_graph` 事先就拒了，走 eager。' + R(BCR, 188, 202)],
                 [note('注释', '图要占显存：实测 8B 模型录到 bs=128，就占掉它 1% 的 KV 池；超过上限的 decode 走 eager。',
                       q='eager, and graphs up to bs=128 cost the 8B 1% of its KV pool.', ref=A(MH, 124)),
                  note('推断', '小档密、大档疏：bs 小时补齐浪费的比例最大，所以分得细。代码没写步长的依据。')],
                 ['decode 按**请求数**分档（每个请求 1 个 token）；prefill 图按**总 token 数**分档。',
                  '会回落 eager 的情形：有 embedding 覆盖、beam、bs 超过 `max_bs`、DP-attention 投票不过等。' + R(DCG, 980, 1060)]),
             to='model_fwd.src_dgraph'),
        part('d_buf', '静态输入缓冲', [336, 36, 220, 140], m=[8, 224, 196, 140], kind='store', n='2', code='buffer_registry',
             glyph=g_text('—', '等这一批的数据'),
             say='地址不变，内容每步覆盖', why='图记住的是地址', trap='假行真的会跑',
             card=card(
                 '录图时就固定下地址的一组张量。每次回放前把这一批的真实数据**覆盖**进去，图读的永远是这几块内存。',
                 ['`buffer_registry.fill_from(forward_batch, raw_bs=5, padded_bs=8, …)`：前 5 行拷真数据。' + R(DCG, 1744, 1752),
                  '补出来的行按规矩填：`req_pool_indices / out_cache_loc / positions` 填 0；`input_ids` 的尾巴不读。' + R(CGBR, 598, 608),
                  '假行的 `seq_lens` 填 `seq_len_fill_value`，FA3 取 1。' + R(FA, 3684, 3686),
                  '之后 attention 后端原地重写元数据：页表写进预分配的 `metadata.page_table`。' + R(DCG, 1864) + ' ' + R(FA, 3294)],
                 [note('推断', '录下来的 kernel 记住的是**地址**：数据换了、地址不变，同一张图就读到这一步的长度和页表。'),
                  note('注释', 'positions 的假行必须清零：留着越界的旧值，flashinfer 的规划会读到非法地址。',
                       q='plan reads the padded tail, so leaving stale out-of-range values there', ref=A(CGBR, 605)),
                  note('提交', '对应的修复。',
                       q='[Fix] Reset positions tensor in CUDA graph runner when batch size differs from captured size (#24401)', ref='c:fde4004429')],
                 '假行不是空着：它们真的跑一遍模型，只是读写都落在第 0 号。'),
             to='model_fwd.src_dgraph'),
        part('zero', '第 0 号', [336, 234, 220, 100], m=[220, 442, 112, 140], kind='store', n='3', code='slot 0',
             glyph=g_text('0 号', '行 / 槽'),
             say='假行都落这里', why='特意留空', trap='真请求拿不到',
             card=card(
                 '三处刻意留出来的「第 0 号」：补齐出来的假请求，读写全落在这里。',
                 ['`req_to_token` 多分一行第 0 行，空闲行从 1 开始发。' + R(MP, 416, 425),
                  'KV 池的第 0 号槽留给假 token 写。' + R(MP, 3657),
                  'FutureMap 的 slot 0 也和它对齐。' + R(OVL, 324)],
                 [note('注释', '假请求的读写落在第 0 行，无害。',
                       q='req_pool_indices to 0, so dummy reads/writes land here harmlessly.', ref=A(MP, 417)),
                  note('注释', 'KV 第 0 号槽专收假 token 的输出。',
                       q='The padded slot 0 is used for writing dummy outputs from padded tokens.', ref=A(MP, 3657))],
                 '真请求永远分不到第 0 行（空闲行从 1 开始），所以假行把它写成什么都没人读。')),
        part('d_replay', '回放整张图', [676, 36, 200, 140], m=[8, 428, 196, 124], kind='act', n='4', code='graph.replay()',
             glyph=g_text('replay()', '整模型 + lm_head + logits'),
             say='一个调用发出整串 kernel', why='省掉逐个发 kernel', trap='采样不在图里',
             card=card(
                 '一个调用把录好的整串 kernel 发给 GPU：整个模型 forward，含 lm_head + logits_processor。',
                 ['`backend.replay(...)`，后端就是 `_graphs[shape_key].replay()`。' + R(DCG, 2189) + ' ' + R(FCG, 257),
                  '录的是整个模型 forward。' + R(DCG, 1555),
                  '启动时每个档先热身两次再录。' + R(FCG, 181, 184)],
                 [note('注释', '热身两次再录：kernel 加载、一次性的初始化不进图。',
                       q='Two warmups so kernels are loaded and one-time setup is paid before capture.', ref=A(FCG, 181)),
                  note('推断', 'decode 一步每个请求只算 1 个 token，CPU 一个个发 kernel 的开销占比大；回放把整串一次交出去。')],
                 '图只吐 logits；采样在图外。')),
        part('d_slice', '切掉假行', [996, 36, 208, 140], m=[8, 616, 196, 124], kind='act', n='5', code='[: raw_num_token]',
             glyph=g_text('—', '等回放结果'),
             say='只留真实请求的行', why='假行的结果没人要', trap='档 1 时什么都不切',
             card=card(
                 '回放吐出「档位」那么多行 logits，只留前面真实请求的行。',
                 '`output.next_token_logits[: self.raw_num_token]`。' + R(DCG, 2209, 2213),
                 [note('推断', '假行的结果没人要：切掉之后，Sampler 只看真实请求。')],
                 '我们那条请求单独 decode 时是档 1，没有假行，这里什么都不切。')),
        part('sampler2', '采样（图外）', [996, 234, 208, 100], m=[8, 1426, 196, 110], kind='out', n='9', code='Sampler',
             glyph=g_text('Sampler', '两条路汇到这里'),
             say='两种图之后都回到这里', why='分支多，留在图外', trap='个别模型把 argmax 录进图',
             card=card(
                 '不管走哪张图，最后都回到同一个采样器（上一层的「采样」框）。',
                 '`tp_worker` 在 `model_runner.forward` 之后单独调 `sample`。' + R(TP, 769, 773),
                 [note('推断', '采样路径有很多主机侧分支（全贪心？要 top-p？）和每步现算的 grammar 掩码，不适合录进固定的图。代码里没有明说。')],
                 '个别模型（Unlimited-OCR v2）把贪心 argmax 录进了 decode 图，说明不是做不到。' + R(MR, 1722, 1723))),
        part('p_bucket', '准入：按 token 数选档', [16, 372, 200, 140], m=[8, 830, 196, 130], kind='gate', n='6', code='can_run_graph',
             glyph=g_checks([('15 → 档 16', None), ('16 ≤ 15×2', None)]),
             say='补得太多就不回放', why='限制补齐浪费', trap='为什么是 2 倍没写',
             card=card(
                 '决定这个 prefill 批能不能回放：按**总 token 数**找档，补得太多就拒。',
                 ['prefill 档位：4…32 步 4、48…256 步 16、288…512 步 32…，上限默认 = `chunked_prefill_size`（H100 档 8192）。' + R(CGH, 576, 590) + ' ' + R(MH, 247, 249),
                  '找 ≥ 15 的最小档 = 16；不比最小档还小，且 `16 > 15×2` 不成立 → 放行。' + R(PCG, 1564, 1571),
                  '其它拒绝条件：有 `input_embeds`、target verify、超过 `max_num_tokens`…' + R(PCG, 1525, 1562)],
                 [note('提交', '避免 prefill 图补齐过多。', q='perf: avoid excessive prefill CUDA graph padding (#31487)', ref='c:6c6175fabd'),
                  note('测试', '档位是 [4,16] 时：5 个 token 要补到 16（超过 10）被拒；8 个补到 16（正好 2 倍）放行。',
                       q='test_rejects_more_than_two_x_token_padding', ref=A(TPAD, 64))],
                 ['倍数为什么取 2，代码和提交都没写。',
                  '要了 logprob 的请求在 breakable 后端照样能回放：尾巴本来就在图外。' + R(PCG, 832)]),
             to='model_fwd.src_pgraph'),
        part('p_body', '躯干分段图', [336, 372, 220, 140], m=[8, 1024, 196, 150], kind='act', n='7', code='replay_layer_forward',
             glyph=g_cells([cell('图', '段'), cell('attn', 'eager', 'pick'), cell('图', '段'), cell('attn', 'eager', 'pick'), cell('…')]),
             say='段间回放，attention 处断开', why='attention 看每个请求的长度', trap='换掉函数再调外层',
             card=card(
                 '只录 transformer 躯干；每层 attention 处图被切开，attention 用当前批的真实数据 eager 调用。',
                 ['回放时先把 `layer_model.forward` 换成替身 `replay_layer_forward`。' + R(PCG, 2421, 2422),
                  '再**照常**调外层 `model.forward`：外层走到「调躯干」那一步就命中替身，替身回放图、交回隐藏状态。' + R(PCG, 2457),
                  '`RadixAttention` 在 prefill 图里走 `_eager_attention`，它带 `@eager_on_graph` 装饰。' + R(RA, 152, 208),
                  '`finally` 里把 `layer_model.forward` 还原。' + R(PCG, 2463, 2464)],
                 [note('注释', '断点之间录图、断点处 eager 调用。', q='Record an eager call between captured segments.', ref=A(BCGF, 277)),
                  note('注释', '请求专属的状态，在断点体内运行时才读。', q='Request-specific state is read inside the body when it runs.', ref=A(BCGF, 283)),
                  note('推断', 'attention 要用每个请求的长度和页表（`cu_seqlens`、页表宽度随批变），形状不固定，所以断在它这里。')],
                 '「换掉函数再调外层 forward」是间接跳转：只看 `model.forward` 会以为没走图。'),
             to='model_fwd.src_pgraph'),
        part('p_tail', 'eager 尾巴', [676, 372, 200, 140], m=[8, 1238, 196, 124], kind='act', n='8', code='logits_processor',
             glyph=g_text('—', '等躯干'),
             say='图外算 logits，只取最后一位', why='图与请求数无关', trap='decode 图正相反',
             card=card(
                 '躯干回放完，外层 forward 接着往下，用活的批 eager 跑 `logits_processor`：只取最后一位 → lm_head。',
                 ['注释原话：回放躯干，eager 跑 LM head + logits_processor。' + R(PCG, 2391, 2392),
                  '只取最后一位：' + R(LP, 964, 966)],
                 [note('注释', '尾巴放图外，图就与请求数无关，不用为每个档预留（请求数 × 词表）那么大的缓冲。',
                       q='graph bs-invariant so req_slots is not bound by an (req_slots, vocab) buffer.', ref=A(PCG, 741))],
                 'decode 图正相反：lm_head + logits 在图**里**。')),
    ]

    wires = [
        wire('g_b2buf', 'd_bucket', 'd_buf', '按档备行', n='1', why='补齐才对得上形状',
             card=card('按选中的档准备那么多行。', '`bs = self._pad_to_bucket(raw_bs, self.capture_bs)`，`padded_num_tokens = bs × 1`。' + R(DCG, 1726, 1732),
                       [note('推断', '图按档录，行数补齐到档位，形状才和录的时候一样。')])),
        wire('g_zero', 'd_buf', 'zero', '假行 → 0', n='3', mlp=[268, 404], why='落在没人用处',
             card=card('假行的 `req_pool_indices`、`out_cache_loc` 都是 0。', '读 `req_to_token` 第 0 行，写 KV 第 0 号槽。' + R(CGBR, 602, 603),
                       [note('推断', '与其让假行跳过计算（那就得改图），不如让它们照常跑，但读写都落在专门留出的第 0 号。')])),
        wire('g_fill', 'd_buf', 'd_replay', '覆盖后回放', n='2', mlp=[64, 396], why='读新数据',
             card=card('缓冲填好、元数据重写完，回放。', R(DCG, 2189),
                       [note('推断', '图里的 kernel 读的就是这些缓冲的地址。')])),
        wire('g_out', 'd_replay', 'd_slice', '[档, vocab]', n='4', why='含假行的结果',
             card=card('回放输出的 logits 行数等于档位。', R(DCG, 2209, 2213), [note('推断', '图的输出形状也是录的时候定下的。')])),
        wire('g_s1', 'd_slice', 'sampler2', '只剩真实行', ml='真实行', n='5', mfs='r', mts='r', mlp=[262, 1060], why='只采真实请求',
             card=card('切好的 logits 交给图外的采样。', R(TP, 769, 773), [note('推断', '图到这里结束，后面全在图外。')])),
        wire('g_p1', 'p_bucket', 'p_body', '补到 16', n='6', why='按 token 档回放',
             card=card('放行后 `load_batch` 按档补齐，执行回放。', R(MR, 2185, 2187), [note('推断', 'prefill 补的是 token，不是请求行。')])),
        wire('g_p2', 'p_body', 'p_tail', '隐藏状态', n='7', why='躯干到此为止',
             card=card('替身函数返回躯干的隐藏状态，外层 forward 继续往下。', R(PCG, 2417, 2419),
                       [note('推断', '外层 forward 本身是 eager 跑的，所以它后面的 logits 自然也在图外。')])),
        wire('g_p3', 'p_tail', 'sampler2', '[1, vocab]', n='8', why='和 decode 汇到一处',
             card=card('prefill 的 logits 交给同一个采样器。', R(TP, 769, 773), [note('推断', '两条路在采样前汇合。')])),
    ]

    steps = [
        beat('本例：1 个请求 → 档 1',
             '我们那条请求单独 decode 时 bs=1：`_pad_to_bucket(1, [1,2,4,8,…])` = **1**，没有假行。下面换一个更挤的时刻看补齐：running 里有 **5** 个请求。',
             note('推断', '档 1 存在，所以单个请求不浪费。'),
             focus=['d_bucket'],
             state={'d_bucket': g_cells([cell('1', 'bs=1', 'pick'), cell('2'), cell('4'), cell('8'), cell('12'), cell('…256')], left='选')}),
        beat('5 个请求 → 档 8',
             '`bisect_left([1,2,4,8,12,…], 5)` 落在 **8**：要备 8 行，其中 3 行是假的。',
             note('推断', '图按档录，形状必须对上。'),
             focus=['d_bucket', 'g_b2buf'],
             state={'d_bucket': g_cells([cell('1'), cell('2'), cell('4'), cell('8', 'bs=5', 'pick'), cell('12'), cell('…256')], left='选')}),
        beat('填缓冲：3 行假的指向第 0 号',
             '`fill_from(raw_bs=5, padded_bs=8)`：前 5 行（含我们的第 3 行）拷真数据；后 3 行 `req_pool_indices=0`、`out_cache_loc=0`、`positions=0`、`seq_lens=1`。它们会读 `req_to_token` 第 0 行、写 KV 第 0 号槽。',
             note('注释', '第 0 行专门留给假请求。', q='req_pool_indices to 0, so dummy reads/writes land here harmlessly.', ref=A(MP, 417)),
             focus=['d_buf', 'g_zero', 'zero'],
             state={'d_buf': g_cells(pad_cells(5, 3), left='8 行'), 'zero': g_text('0 号', '3 个假请求', tone='run')}),
        beat('回放整张图',
             '`replay()`：8 行一起跑完整个模型 + lm_head + logits，输出 **[8, vocab]**。',
             note('推断', '同一串 kernel，读的是刚覆盖的新数据。'),
             focus=['d_replay', 'g_fill'],
             state={'d_replay': g_text('[8, vocab]', '整模型 + lm_head + logits', tone='run')}),
        beat('切掉 3 行，交给采样',
             '`next_token_logits[:5]` → **[5, vocab]**；假行的 3 行扔掉。采样在图外进行。',
             note('推断', '假行只为凑形状，结果没人要。'),
             focus=['d_slice', 'g_out', 'g_s1', 'sampler2'],
             state={'d_slice': g_text('[5, vocab]', '切掉 3 行假的', tone='ok')}),
        beat('prefill：15 个 token → 档 16',
             '回到第 n 圈的 prefill：15 个 token，≥15 的最小档是 **16**；`16 ≤ 30` → 放行，补 1 个 token。',
             note('注释', '只拒绝补得太多的。', q='No exact-shape check: load_batch bucket-pads; only reject', ref=A(PCG, 1564)),
             focus=['p_bucket'],
             state={'p_bucket': g_checks([('15 → 档 16', 'ok'), ('16 ≤ 15×2', 'ok')])}),
        beat('躯干回放，attention 处断开',
             '`layer_model.forward` 被换成替身，外层 `model.forward` 照常调用；替身回放躯干，每层走到 attention 就跳出图，用 `cu_seqlens_q=[0,15]`、页表 20 个槽 eager 调 FA3，再回到下一段图。',
             note('注释', '断点之间录图。', q='Record an eager call between captured segments.', ref=A(BCGF, 277)),
             focus=['p_body', 'g_p1'],
             state={'p_body': g_cells([cell('图', '段', 'mv'), cell('attn', '20 槽', 'pick'), cell('图', '段', 'mv'), cell('attn', '20 槽', 'pick'), cell('…')])}),
        beat('eager 尾巴只算下标 14',
             '外层 forward 接着往下，图外跑 `logits_processor`：15 行隐藏状态只取下标 **14** → lm_head → **[1, vocab]** → 采样得到 X1。',
             note('注释', '尾巴放图外，图就与请求数无关。',
                  q='graph bs-invariant so req_slots is not bound by an (req_slots, vocab) buffer.', ref=A(PCG, 741)),
             focus=['p_tail', 'g_p2', 'g_p3', 'sampler2'],
             state={'p_tail': g_text('[1, vocab]', '只取下标 14', tone='ok')}),
    ]

    groups = [
        group('decode 图：整个模型录进一张图', [6, 8, 1210, 178], m=[2, 4, 336, 746]),
        group('prefill 图（breakable）：只录躯干', [6, 350, 880, 168], m=[2, 804, 214, 566]),
    ]
    return scene(1220, 520, parts, wires, steps, mw=340, mh=1548, groups=groups,
                 cap='decode 图（对照：5 个请求补到档 8）vs prefill 图（15 个 token 补到档 16）')


# ================================================================ 源码页
def src_worker():
    return node('model_fwd.src_worker', '总管源码', 'forward_batch_generation：打包 → 前向 → 采样（或推迟）',
                '整个框的总调度函数。',
                lead='你在「模型前向 + 采样」这个框的总管函数里：上一层图上 ②→③→⑦ 的顺序，就是这里从上到下的顺序。',
                src=TP + ':657-790',
                blocks=[
                    code(TP, [(657, 664), (688, 692)], notes=[
                        at((657, 664), '推断', '打包的唯一入口：ScheduleBatch → ForwardBatch。', has='ForwardBatch.init_new('),
                        at(688, '推断', '只有流水线并行的最后一级才跑完整前向和采样；别的级只传隐藏状态（这一页只讲最后一级）。', has='is_last_rank'),
                        at((689, 692), '推断', '前向：进入 _forward_raw 的三选一。', has='self.model_runner.forward('),
                    ], cap='打包 → 前向'),
                    code(TP, [(745, 790)], notes=[
                        at((747, 757), '推断', '推迟采样的条件：overlap、非投机，并且本批有 grammar 或打开了 SGLANG_ENABLE_DELAY_SAMPLE（默认关）。', has='SGLANG_ENABLE_DELAY_SAMPLE'),
                        at((759, 767), '推断', '不当场采样：把闭包挂在结果上，Scheduler 处理完上一批再调它（grammar 要用上一步刚确定的 token）。', has='delay_sample_func'),
                        at((769, 773), '推断', '常规路径：前向之后在图外采样。', has='self.model_runner.sample('),
                        at((775, 781), '注释', '只做 prefill 的请求（如打分）不采样，造全 0 占位。',
                           q='For prefill-only requests, create dummy token IDs on CPU', has='torch.zeros('),
                    ], cap='采样，或推迟采样'),
                ])


def src_pack():
    return node('model_fwd.src_pack', '打包源码', 'ForwardBatch.init_new：别名借用 + 新算 positions',
                '「打包」框里的两段代码。',
                lead='你在「打包」框里：前一段是借用 ScheduleBatch 张量的那一句，后一段是真正新算的 positions 和 extend 长度。',
                src=FBI + ':1029-1260',
                blocks=[
                    code(FBI, [(1029, 1038)], notes=[
                        at((1033, 1036), '推断', '这 4 个张量直接用 ScheduleBatch 上的同一个对象，没有拷贝。', has='out_cache_loc=batch.out_cache_loc'),
                        at(1038, '注释', '注释原话：从 ScheduleBatch 按引用借来的输入。', q='Inputs aliased by reference from ScheduleBatch', has='aliased'),
                    ], cap='别名借用'),
                    code(FBI, [(1216, 1260)], notes=[
                        at((1217, 1219), '推断', 'decode：positions = clamp(seq_lens - 1)。例子 decode 第 1 步 seq_lens=21 → positions=[20]。', has='clamp_position(batch.seq_lens)'),
                        at((1224, 1233), '注释', '设备支持时，extend 长度由 kernel 写到 GPU，不走 pinned H2D，免得排在大块传输后面。',
                           q='Written by kernels: a pinned host-to-device copy would queue', has='_host_ints_to_device'),
                        at((1242, 1243), '推断', '同时留一份 CPU 镜像，后面 attention 取最大长度时不用从 GPU 读回。', has='extend_seq_lens_cpu = list'),
                        at((1250, 1256), '推断', 'extend：positions = 前缀长 + 偏移。例子：前缀 5、新 15 → [5…19]，extend_start_loc=[0]。', has='compute_position('),
                    ], cap='新算的：positions 与 extend 长度'),
                ])


def src_fork():
    return node('model_fwd.src_fork', '三选一源码', '_forward_raw：decode 图 → prefill 图 → eager',
                '「怎么跑：三选一」框里的判断顺序。',
                lead='你在「怎么跑：三选一」框里：从上往下读，就是先问 decode 图、再问 prefill 图、最后 eager 的顺序。',
                src=MR + ':2113-2193',
                blocks=[
                    code(MR, [(2114, 2123), (2140, 2154), (2164, 2193)], notes=[
                        at((2119, 2123), '推断', 'decode 图判据：模式白名单（只含 DECODE / TARGET_VERIFY / IDLE / DLLM_EXTEND）+ runner 自己的 can_run_graph。普通 prefill 在这里就是 False。', has='can_run_graph = bool('),
                        at((2141, 2146), '推断', '能回放就直接返回，后面都不走。例子 decode 第 1 步：bs=1 → 档 1，从这里返回。', has='return ModelRunnerOutput'),
                        at((2148, 2154), '注释', '只有 decode 图那条路自己预先补齐了；prefill 图和 eager 跑活的批，要先做这一步。',
                           q='cuda-graph path above pre-pads its static buffers and returns', has='_prepare_eager_forward_batch'),
                        at((2164, 2173), '推断', 'prefill 图的 6 个条件并列在这里（decode 图的条件则下沉在 runner 里）。例子 15 个 token → 档 16，全部满足。', has='can_run_graph(forward_batch)'),
                        at(2174, '观点', '注释还写 "piecewise"，是遗留：tc_piecewise 已删除，默认 prefill 图是 breakable。', has='piecewise'),
                        at((2190, 2193), '注释', '兜底：eager runner 内部再按 decode / extend / idle 分发。',
                           q='Eager: decode / extend / idle dispatched inside the runner.', has='eager_runner.execute'),
                    ], cap='_forward_raw（节选）'),
                ])


def src_dgraph():
    return node('model_fwd.src_dgraph', 'decode 图源码', 'decode 图回放：选档、填缓冲、回放、切片',
                '「选档」「静态输入缓冲」框里的代码。',
                lead='你在「图回放里面」的 decode 一侧：选档 → 填缓冲 → 回放 → 切掉假行，最后一段是第 0 行为什么无害。',
                src=DCG + ':1726-2213',
                blocks=[
                    code(BCR, [(188, 202)], notes=[
                        at((197, 200), '推断', '超过最大档的批本该被 can_run_graph 拒掉；这里用断言把约定写明。', has='can_run_graph should have rejected'),
                        at((201, 202), '推断', '取 ≥ 实际 bs 的最小档：5 → 8，1 → 1。', has='bisect.bisect_left'),
                    ], cap='_pad_to_bucket'),
                    code(DCG, [(1726, 1735), (1744, 1752)], notes=[
                        at(1726, '推断', '非投机时每个请求宽度 1：真实 token 数 = 请求数。', has='raw_num_token = raw_bs * self.captured_req_width'),
                        at(1731, '推断', '选档。', has='_pad_to_bucket(raw_bs'),
                        at((1744, 1749), '推断', '把真实数据拷进静态缓冲，并告诉它真实 5 行、补到 8 行；尾巴按登记表的规矩填。', has='fill_from('),
                    ], cap='load_batch（节选）：选档、填缓冲'),
                    code(CGBR, [(598, 608)], notes=[
                        at((602, 603), '推断', '假行的 seq_lens 填哨兵值（FA3 是 1），请求号、写入槽填 0。', has='FILL_SENTINEL'),
                        at((604, 606), '注释', 'positions 的假行必须清零，否则 flashinfer 的规划会越界。',
                           q='triggers an illegal memory access (issue #24361).', has='ZERO'),
                    ], cap='补齐规矩'),
                    code(DCG, [(2189, 2189), (2207, 2213)], notes=[
                        at(2189, '推断', '回放：后端就是对录好的图调 replay()。', has='self.backend.replay('),
                        at((2209, 2213), '推断', '按真实行数切片，假行的 logits 丢掉。', has='[: self.raw_num_token]'),
                    ], cap='回放与切片'),
                    code(MP, [(415, 425)], notes=[
                        at((416, 417), '注释', '多留第 0 行：假请求的读写落在这里无害。',
                           q='+1 padding row at index 0: cuda-graph padded batches default', has='+1 padding row'),
                        at(425, '推断', '空闲行从 1 开始发：真请求永远拿不到第 0 行。例子里我们那条拿到的是第 3 行。', has='range(1, self._alloc_size)'),
                    ], cap='req_to_token：多出来的第 0 行'),
                ])


def src_pgraph():
    return node('model_fwd.src_pgraph', 'prefill 图源码', 'prefill 图：准入上限与「换掉躯干函数」',
                '「准入」「躯干分段图」框里的代码。',
                lead='你在「图回放里面」的 prefill 一侧：先看补齐上限怎么判，再看回放时怎么把躯干函数换成替身。',
                src=PCG + ':1560-2465',
                blocks=[
                    code(PCG, [(1560, 1572)], notes=[
                        at((1564, 1565), '注释', '不要求形状精确：按档补齐，只拒绝补得太多的。',
                           q='No exact-shape check: load_batch bucket-pads; only reject', has='No exact-shape check'),
                        at((1566, 1572), '推断', '例子：15 → 档 16，16 ≤ 30 放行。比最小档还小的批总是放行。', has='_MAX_PREFILL_CUDA_GRAPH_PADDING_FACTOR'),
                    ], cap='can_replay_locally（节选）：2 倍补齐上限'),
                    code(PCG, [(739, 741)], notes=[
                        at((739, 741), '注释', '只录躯干，lm_head + logits 留在图外，图就与请求数无关。',
                           q='BCG and Full CG capture only the transformer body (layer_model.forward),', has='bs-invariant'),
                    ], cap='录什么'),
                    code(PCG, [(2391, 2396), (2417, 2422), (2451, 2465)], notes=[
                        at((2391, 2392), '注释', '注释原话：回放躯干，eager 跑 LM head + logits_processor。',
                           q='BCG / Full: replay the captured body, run the LM head +', has='replay the captured body'),
                        at(2417, '推断', '替身的核心：回放录好的躯干，拿到隐藏状态。', has='self.backend.replay(shape_key'),
                        at((2421, 2422), '推断', '偷梁换柱：把躯干的 forward 换成替身。', has='self.layer_model.forward = replay_layer_forward'),
                        at((2457, 2462), '推断', '照常调外层 model.forward：它走到「调躯干」就命中替身；之后的 logits 在图外跑。', has='self.model_runner.model.forward('),
                        at((2463, 2464), '推断', '无论成败都还原，免得下一次 eager 前向误用替身。', has='original_layer_forward'),
                    ], cap='_execute_body_capture（节选）：换掉躯干函数，再调外层'),
                ])


def src_attn():
    return node('model_fwd.src_attn', 'attention 源码', 'FA3：建页表、写 KV、按页表读 KV，再到 logits',
                '「模型躯干」「页表 + KV 池」框里的代码。',
                lead='你在「模型躯干：逐层 attention」框里：页表每次前向建一次，每层先写后读；最后一段是 logits 只取最后一位。',
                src=FA + ':1041-2076',
                blocks=[
                    code(FA, [(1041, 1056)], notes=[
                        at(1041, '注释', '元数据每次前向建一次，所有层复用。',
                           q='Initialize forward metadata hence all layers in the forward pass can reuse it.', has='all layers'),
                        at((1046, 1056), '推断', '页表宽度优先用 CPU 镜像的最大长度（例子 20），没有就用静态上限：宁可页表宽一点，也不从 GPU 读回。', has='eager_max_k'),
                    ], cap='init_forward_metadata 开头'),
                    code(FA, [(1301, 1312), (1333, 1338)], notes=[
                        at((1310, 1312), '推断', '整条「找 KV」的关键一行：取出这批请求在 req_to_token 里的行。例子：第 3 行前 20 列 = [37…41, 212…226]。', has='self.req_to_token_pool.req_to_token['),
                        at((1333, 1338), '推断', '有前缀时 query 的起点按 extend 长度累加：例子 cu_seqlens_q=[0,15]，max_seq_len_q=15。', has='extend_prefix_lens_cpu'),
                    ], cap='extend：建页表与 cu_seqlens_q'),
                    code(FA, [(1154, 1156)], notes=[
                        at((1154, 1156), '推断', 'decode 同理：例子 decode 第 1 步取第 3 行前 21 列。', has='req_to_token'),
                    ], cap='decode：建页表'),
                    code(FA, [(1714, 1727)], notes=[
                        at((1714, 1727), '推断', '每层先写：本步的 k、v 存进 out_cache_loc 指的槽（例子 212…226）。', has='.store('),
                    ], cap='写 KV'),
                    code(FA, [(2066, 2076)], notes=[
                        at((2066, 2072), '推断', '再读：kernel 用 page_table[i, j] 去 K/V 池里取第 j 个历史 token。', has='page_table=page_table'),
                    ], cap='按页表读 KV'),
                    code(LP, [(960, 966)], notes=[
                        at((964, 966), '注释', 'prefill 不要输入 logprob：只取每个请求最后一个位置。例子 cumsum([15])-1 = 14。',
                           q='Prefill without input logprobs.', has='last_index'),
                    ], cap='logits：只取最后一位'),
                ])


def src_sample():
    return node('model_fwd.src_sample', '采样源码', 'Sampler：贪心 / 简单采样 / 融合核，与 TP 同步',
                '「采样」框里的代码。',
                lead='你在「采样」框里：从上往下是全贪心分支、简单采样判断、温度 softmax、Gumbel-max，以及默认关闭的 TP 同步。',
                src=SMP + ':209-1065',
                blocks=[
                    code(SMP, [(212, 220), (228, 233)], notes=[
                        at(212, '推断', '全贪心（每个请求 top_k ≤ 1）直接 argmax。例子 temperature>0，不走这里。', has='is_all_greedy'),
                        at((229, 233), '推断', '没有 top-p / top-k / min-p 就是简单情形。例子 top_p=1、top_k=-1、min_p=0 → 简单。', has='simple_sampling_case'),
                    ], cap='分支'),
                    code(SMP, [(297, 301)], notes=[
                        at((299, 301), '注释', '温度融合进 softmax，概率原位覆盖 logits，不另占显存。',
                           q='Temperature fused; the probs overwrite the logits in place.', has='sampler_softmax'),
                    ], cap='温度 + softmax'),
                    code(SMP, [(1049, 1064)], notes=[
                        at((1050, 1056), '注释', 'Gumbel-max：和 multinomial 分布等价，但没有拖慢 decode 关键路径的 CPU 侧簿记。',
                           q="avoids multinomial's CPU-side philox offset bookkeeping and", has='exponential_'),
                        at((1059, 1063), '推断', '带种子的请求改走确定性采样。', has='multinomial_with_seed'),
                        at(1064, '推断', '产出 GPU 上的 int32 [bs]：例子 [X1]。', has='to(torch.int32)'),
                    ], cap='sampling_from_probs_torch'),
                    code(SMP, [(752, 760)], notes=[
                        at((755, 758), '注释', '默认不做 all-reduce：各 TP rank 各采各的，靠输入一致、核确定拿到同一个 token；有 grammar 时总会同步。',
                           q='Off by default to save an all-reduce; correct only while the last all-reduce,', has='SYNC_TOKEN_IDS_ACROSS_TP'),
                    ], cap='TP 同步（默认关）'),
                ])


def src_stream():
    return node('model_fwd.src_stream', '三条流源码', 'run_batch 的 overlap 分支：等流、取输入、前向、存 future_map、拷回',
                '「这一批进来」「留给下一步」「拷回 CPU」「三条 CUDA 流」框里的代码。',
                lead='你在「三条 CUDA 流」框里：这一段就是整个框在 Scheduler 侧的外壳，从等 schedule 流到把输入清空。',
                src=SCH + ':5432-5511',
                blocks=[
                    code(SCH, [(5432, 5447), (5466, 5475), (5492, 5511)], notes=[
                        at((5432, 5433), '推断', '进 forward 流，先等 schedule 流上排好的活（分槽 212…226、写 req_to_token）。', has='wait_stream(self.schedule_stream)'),
                        at((5442, 5444), '推断', '取输入：prefill 从 pinned CPU H2D 15 个 id；decode 从 future_map 取上一步的 X1。', has='resolve_forward_inputs'),
                        at(5446, '推断', '前向期间把这一批当事务：sampling_info 换成前向专用拷贝、钉住引用。', has='_forward_isolation'),
                        at(5447, '推断', 'future_map 的下标就是 req_pool_indices（例子 [3]）。', has='future_indices = batch.req_pool_indices'),
                        at((5470, 5472), '推断', '前向 + 采样：CPU 只是排 kernel，几乎立刻返回。', has='forward_batch_generation('),
                        at(5475, '推断', '登记新的 seq_lens：例子 20+1=21。', has='publish(future_indices, batch.seq_lens + 1)'),
                        at(5493, '推断', 'copy_done 事件：Scheduler 下一圈处理结果前在它上面等。', has='copy_done'),
                        at((5495, 5497), '推断', '采到的 token 写进 output_tokens_buf[3]。', has='_relay_forward_payload'),
                        at(5506, '推断', '在 copy 流上异步拷回 CPU。', has='_copy_result_to_cpu'),
                        at((5510, 5511), '注释', '下一圈的 input_ids 经 future_map 转交，这里清空。',
                           q='Next-iter input_ids relayed via future_map.', has='batch.input_ids = None'),
                    ], cap='run_batch overlap 分支（节选）'),
                ])


def nodes():
    graph = node(
        'model_fwd.graph', '图回放里面', 'CUDA graph 回放：decode 图 vs prefill 图',
        '选档、静态缓冲、第 0 号、躯干分段图、eager 尾巴。',
        lead='你在「模型前向 + 采样」里的「怎么跑：三选一」框里面：上一层说它在 decode 图 / prefill 图 / eager 之间选；这里拆开两张图各自怎么回放。上半是 decode 图（用对照：5 个请求补到档 8），下半是 prefill 图（例子里那 15 个 token）。按 ▶ 走一遍。',
        blocks=[graph_scene()],
        children=[src_dgraph(), src_pgraph()])
    top = node(
        'model_fwd', '模型前向 + 采样里面', '模型前向 + 采样：一批请求在 GPU 上怎么算出下一个 token',
        '打包、三选一、逐层 attention、只取最后一位、采样、两个出口。',
        lead='你在顶层「模型前向 + 采样」框里面。顶层说它「算一批，每个请求采 1 个 token」；这里拆开看：输入怎么上 GPU、走哪条路跑、attention 怎么按页表找 KV、token 采出来后怎么一份留在 GPU 给下一步、一份拷回 CPU。按 ▶ 跟着同一条请求（第 3 行、前缀 5 + 新 15）走第 n 圈 prefill 和第 n+1 圈 decode。总调度函数见 [[model_fwd.src_worker|总管源码]]。',
        blocks=[fwd_scene()],
        children=[graph, src_worker(), src_pack(), src_fork(), src_attn(), src_sample(), src_stream()])
    return {'model_fwd': top}
