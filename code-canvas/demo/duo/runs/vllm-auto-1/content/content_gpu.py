# 顶层「GPU 执行」框的下一层：一步之内 GPU 上排了哪些活（HEAD 0e3c8dbdae，单机单卡默认配置）
from cv import *

MR = 'vllm/v1/worker/gpu/model_runner.py'
ST = 'vllm/v1/worker/gpu/states.py'
IB = 'vllm/v1/worker/gpu/input_batch.py'
BT = 'vllm/v1/worker/gpu/block_table.py'
BU = 'vllm/v1/worker/gpu/buffer_utils.py'
AU = 'vllm/v1/worker/gpu/async_utils.py'
CG = 'vllm/v1/worker/gpu/cudagraph_utils.py'
DPU = 'vllm/v1/worker/gpu/dp_utils.py'
UTL = 'vllm/v1/worker/utils.py'
WB = 'vllm/v1/worker/worker_base.py'
CORE = 'vllm/v1/engine/core.py'
ASCH = 'vllm/v1/core/sched/async_scheduler.py'
SOUT = 'vllm/v1/core/sched/output.py'
UNI = 'vllm/v1/executor/uniproc_executor.py'
CFGC = 'vllm/config/compilation.py'
DOC = 'docs/design/model_runner_v2.md'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return ' `%s`' % A(f, a, b)


# ---------------------------------------------------------------- 小图（同一套数：槽 1023，block [5, 6]，S1 预填 20，S2 起每步 1 个）
def g_cpu(a, b, c, on=None):
    return g_list([a, b, c], on=on)


def g_state(slot, tl, last):
    return g_kv([('槽 1023', slot), ('total / 已算', tl), ('last_sampled', last)])


def g_buf(ids, pos, sl, sm):
    return g_kv([('input_ids', ids), ('positions', pos), ('seq_lens', sl), ('slot_mapping', sm)])


def g_fwd(gr, nt):
    return g_kv([('图', gr), ('token 数', nt)])


def g_samp(rows, tok):
    return g_kv([('logits 行', rows), ('采出', tok)])


def g_copy(ev, cpu):
    return g_kv([('event', ev), ('CPU 副本', cpu)])


def _scene():
    parts = [
        part('cpu', '引擎主线程', [10, 20, 170, 160], m=[56, 8, 228, 124], kind='ext', code='EngineCore',
             glyph=g_cpu('execute_model', 'sample_tokens', 'get_output'),
             say='每步调两次，晚一步取', why='只排活，几乎不等 GPU', trap='异步≠另开一个线程',
             card=card(
                 '这一层的调用方：引擎进程的主线程（顶层的「引擎忙循环」）。单卡时经 `UniProcExecutor` → `Worker` 直接调进 `GPUModelRunner`，始终是同一个线程。',
                 ['每一步调两次：`execute_model(scheduler_output)` 发射前向，`sample_tokens(grammar_output)` 发射采样。两次都只是把内核排进 CUDA stream，返回时 GPU 多半还没算完。' + R(CORE, 788, 790) + R(CORE, 812, 814),
                  '晚一步（`batch_queue` 满了）才对最早那批调 `get_output()` 取回 token id。' + R(UNI, 32, 44)],
                 [note('文档', 'MRV2 假定核心执行循环是一条没有 CPU 同步点的 CUDA stream，CPU 入口只往上排活。',
                       q='CPU entrypoints queue work onto the stream.', ref=A(DOC, 47))],
                 ['「异步」不是另开线程：`execute_model` / `sample_tokens` 里的 Python 和内核发射都实打实占着这个线程的 CPU 时间。重叠的是「GPU 在算第 N 步」和「CPU 在排第 N+1 步」。'])),

        part('state', '常驻请求状态', [300, 20, 210, 160], m=[56, 196, 228, 140], kind='store', n='A', code='req_states',
             glyph=g_state('空闲', '— / —', '—'),
             say='每个请求占一行，一直占到结束', why='只收差量，不每步重建', trap='槽号≠批内序号',
             card=card(
                 '每个请求在 worker 里的「常驻档案」：从加入到结束（或被抢占），一直占同一行，这一行叫**槽位**。',
                 ['一组第一维是 `max_num_reqs` 的张量，一个请求一行。', '取槽 `free_indices.pop()`（弹列表末尾），还槽 `append` 回去。本例第一个请求拿到槽 **1023**（设 `max_num_seqs=1024`）。' + R(ST, 30) + R(ST, 100) + R(ST, 133),
                  '每槽存：`prompt_len`、`prefill_len`、`total_len`、`num_computed_tokens`、`last_sampled_tokens`、`all_token_ids`。大部分在显存；`all_token_ids` 太大，默认放主机内存经 UVA 读写。' + R(ST, 32, 70),
                  '调度器每步只发差量（新 block、已算数），其余由 worker 自己往前推。'],
                 [note('文档', '每步从零构建这些张量在 Python 里很慢，block table 这种大张量尤其慢。',
                       q='Building these tensors from scratch each step is often very slow in Python, especially for large tensors like block tables.', ref=A(DOC, 17)),
                  note('文档', 'GPU 能推出 CPU 还不知道的值（比如刚采出的 token）。CPU 排下一步时根本不知道上一步采出了什么，状态只能留在 worker 里自己演进。',
                       q='GPU can derive values (for example with speculative decoding) that CPU may not know yet.', ref=A(DOC, 138))],
                 ['**槽号 ≠ 批内序号**：槽位固定不动，批内顺序每步可能重排，中间靠 `idx_mapping`（批序号 → 槽号）换算。',
                  'CPU 上还有一份 `num_computed_tokens_np`：GPU 真值的乐观镜像（上界），给注意力后端算 `seq_lens` 上界用，省掉一次 GPU→CPU 同步。' + R(ST, 64),
                  '被抢占按「结束」处理：恢复时当新请求重新加进来，带上已生成的 token，所以 `prefill_len` 会大于 `prompt_len`。' + R(ST, 48, 49)]),
             to='gpu.state_src'),

        part('prep', '拼输入内核', [630, 20, 180, 160], m=[56, 400, 228, 136], kind='gate', n='B', code='prepare_inputs',
             glyph=g_checks([('还在预填？', None), ('填 last_sampled', None)]),
             say='按批顺序从槽里摘输入', why='GPU 上拼，CPU 不用等', trap='预填的输入不许被覆盖',
             card=card(
                 '一组 GPU 内核：按本批的顺序，从槽位张量里「摘」出这一步的输入，写进定长缓冲。',
                 ['CPU 先算本批顺序（decode 在前、预填在后）和每个请求几个 token，得到 `idx_mapping`。' + R(MR, 2674, 2675),
                  '`prepare_prefill_inputs`（有预填才跑）从 `all_token_ids` 拷 prompt；`prepare_pos_seq_lens` 算位置和长度；`combine_sampled_and_draft_tokens` 把上一步采出的 token 填进来。' + R(MR, 1593, 1627),
                  '最后那个内核有两道守卫：`seq_len ≤ prefill_len`（还在预填）直接返回；`seq_len − num_logits ≥ prefill_len` 才写 last_sampled。' + R(IB, 448, 459)],
                 [note('文档', '持久状态和每步输入解耦：给定本步顺序（通常由注意力后端决定），从持久状态里 gather 出输入。',
                       q='MRV2 decouples persistent state tensors from per-step input tensors.', ref=A(DOC, 33)),
                  note('推断', '「填上一步的 token」放在 GPU 内核里做：CPU 排 S2 时 S1 的采样结果还在显存里、没人知道；做成一个排在主 stream 上的内核，CPU 就不必等。')],
                 ['两道守卫看着多余，其实是在保护预填请求已经写好的 prompt 输入不被覆盖。' + R(IB, 454),
                  '换个场景看「批序号 ≠ 槽号」（设）：请求 B 先到、占槽 1023、在 decode；本请求后到、占槽 1022、预填 20 个。批内顺序 `[B, 本请求]`，`idx_mapping=[1023, 1022]`，`query_start_loc=[0, 1, 21]`。']),
             to='gpu.prep_src'),

        part('buf', '输入缓冲（定长）', [930, 20, 280, 160], m=[56, 600, 228, 150], kind='store', n='C', code='input_buffers',
             glyph=g_buf('—', '—', '—', '—'),
             say='模型这一步真正读的输入', why='地址不变，图能直接回放', trap='没用到的行写 0 / -1',
             card=card(
                 '这一步模型真正读的输入：`input_ids`、`positions`、`query_start_loc`、`seq_lens`，外加 block table 和 `slot_mapping`。',
                 ['定长持久缓冲：比如 `input_ids` 固定 `max_num_tokens` 长（int32），每步被内核**覆盖**，从不重新分配。' + R(IB, 29, 35),
                  '没用到的部分写成固定值：`seq_lens` 末尾写 0，`slot_mapping` 多出来的写 `-1`，`query_start_loc` 补满到 `max_num_reqs+1`。' + R(MR, 1572, 1574)],
                 [note('注释', 'FULL 图回放时不传输入张量：输入早已在图的输入缓冲里。所以缓冲必须定长、地址不变。',
                       q='because they are already copied to the CUDA graph input buffers.', ref=A(MR, 2175)),
                  note('注释', '多跑一个线程块，专门把没用到的 seq_lens 补成 0，给 FULL 图用。',
                       q='to pad unused seq_lens as 0 for full CUDA graphs.', ref=A(IB, 402))],
                 ['要采样的位置是每个请求查询段的**最后一个**：本例 S1 `logits_indices=[19]`，S2 `[0]`。' + R(IB, 441)])),

        part('fwd', '前向', [930, 300, 280, 150], m=[56, 814, 228, 124], kind='act', n='D', code='model',
             glyph=g_fwd('—', '—'),
             say='按批形状挑一张图回放', why='decode 整图，预填分段', trap='图只管前向，不含采样',
             card=card(
                 '跑模型前向，算出这一步所有 token 的隐状态。',
                 ['先按批形状挑图：均匀 decode → 装得下这么多请求的最小 **FULL** 图；否则 → token 数够用的最小 **PIECEWISE** 图；都不合适 → eager。' + R(CG, 579, 605),
                  '默认模式 `FULL_AND_PIECEWISE`；图在启动时 `capture_model` 里捕获好，每步只查表。' + R(MR, 1172),
                  '本例：S1 预填 → PIECEWISE，token 数补到 24（设：按默认网格推的）；S2…S8 → FULL decode 图，1 个请求，不补齐。'],
                 [note('文档', 'decode 批用整图，预填和预填 / decode 混合批用分段图。',
                       q='FULL_AND_PIECEWISE mode: Capture full cudagraph for decode batches and', ref=A(CFGC, 759)),
                  note('注释', '只看形状不够：一段 prompt 分块可能和 decode 批长得一样，所以还要看 `decode_graph_eligible`。',
                       q="scheduled batch, since a prompt chunk can have a decode batch's shape.", ref=A(UTL, 1110))],
                 ['图只决定**前向**怎么发射；拼输入、采样、`post_update` 每步都是同一套内核，不进图。',
                  '退回 eager 的情形：`--enforce-eager` / O0、token 数超过捕获的最大尺寸、profile 运行，以及 DP 里任一 rank 要 eager 则全体 eager。' + R(DPU, 183, 184)]),
             to='gpu.graph_src'),

        part('ems', '单槽暂存', [630, 300, 180, 150], m=[56, 1002, 228, 116], kind='store', n='E', code='execute_model_state',
             glyph=g_text('空', '没有待采样的前向'),
             say='前向结果先放这，等采样', why='掩码可以晚于前向到', trap='只有一个槽',
             card=card(
                 '前向和采样之间的交接处：runner 上的一个字段 `self.execute_model_state`，只有一个槽。',
                 ['`execute_model` 存进 `input_batch`、`hidden_states`、`attn_metadata` 等，然后返回 `None`；`sample_tokens` 第一件事是取出并**清空**。' + R(MR, 2249, 2262) + R(MR, 2286, 2296),
                  '只有一个槽 → 同一个 worker 上，两次 `execute_model` 之间必须夹一次 `sample_tokens`。'],
                 [note('注释', '拆成两次调用是为了结构化输出：语法掩码要用上一步已采样的 token 才算得出来，而前向用不着掩码。',
                       q='We now have the tokens needed to compute the bitmask for the', ref=A(CORE, 876)),
                  note('注释', '基类接口自己承认：这个设计以后可能会变。',
                       q='Note that this design may be changed in future if/when structured outputs', ref=A(WB, 221))],
                 ['结构化输出时（设本请求带 JSON schema）：从 S2 起引擎**先不调** `sample_tokens`，记成 `deferred_step`；等取回 S1 的 token、算出 S2 的掩码再补调。前向已在 GPU 上跑，CPU 算掩码的时间被它盖住。' + R(CORE, 816, 818),
                  '槽为空时 `sample_tokens` 返回 `None`：表示上一次 `execute_model` 失败了，引擎据此重放那次异常。' + R(MR, 2282, 2284)]),
             to='gpu.defer_src'),

        part('samp', '采样', [300, 300, 210, 150], m=[56, 1182, 228, 124], kind='act', n='F', code='sample_tokens',
             glyph=g_samp('—', '—'),
             say='只对最后一个位置算 logits', why='只要下一个 token', trap='采出的值还在显存里',
             card=card(
                 '只对「要预测下一个 token」的位置算 logits，然后采样。',
                 ['选出 `logits_indices` 那几行隐状态 → `compute_sample_logits` →（有的话）语法掩码 → `sampler`，得到 `sampled_token_ids [num_reqs, 1]` 和 `num_sampled`。' + R(MR, 1765, 1790),
                  '分块预填的中间块也会「采样」，但内核把它的 `num_sampled` 置 0，结果之后会被丢掉。' + R(IB, 530, 533),
                  '全是排在主 stream 上的内核，CPU 立刻返回。'],
                 [note('推断', 'S1 的 20 个位置都有隐状态，但预测下一个 token 只需要最后一个；对全部位置算 `[20, vocab]` 的 logits 纯属浪费。代码里 lm_head 只作用在选出的行上，没有注释说明动机。')],
                 ['采出的 `<法国>` 此刻只在显存里，CPU 要到下一轮 `get_output()` 才知道它。']),
             to='gpu.copy_src'),

        part('copy', 'copy_stream 拷回', [10, 300, 170, 150], m=[56, 1370, 228, 124], kind='bg', n='G', code='AsyncOutput',
             glyph=g_copy('—', '—'),
             say='另一条流拷回，带个 event', why='拷贝不挡主 stream', trap='每步一个新 event',
             card=card(
                 '把采样结果的 CPU 副本和一个「拷完了没有」的 event 打包成一个对象，交给 CPU 晚点来取。',
                 ['每个 `AsyncOutput` **自己新建**一个可睡眠的 event；同一时刻最多 2 个在飞（S1、S2）。' + R(AU, 139, 140),
                  '对象里留着 GPU 张量的引用，直到拷贝完成。' + R(AU, 130, 136),
                  '`get_output()`：`synchronize()` → `tolist()` → 按 `num_sampled` 截短。' + R(AU, 192, 202)],
                 [note('注释', '拷贝跑在另一条 stream 上，必须留住 GPU 张量的引用；否则主 stream 那边可能把还没拷完的显存回收复用（后半句是研究员的解读）。',
                       q='as the copy operations are performed on a different CUDA stream than', ref=A(AU, 131)),
                  note('提交', '原来 runner 上共用一个 event，后来改成每个 `AsyncOutput` 新建。推断原因：两份在飞时，S2 的 record 会覆盖 S1 的，S1 就等错了。',
                       q='[BugFix][MRV2] Fix cuda event reuse race (#39115)', ref='c:62095e82c1')],
                 ['分块预填的中间块 `num_sampled=0`，截短后是空列表，不会冒出一个假 token。' + R(AU, 201, 202)]),
             to='gpu.copy_src'),
    ]

    wires = [
        wire('w_delta', 'cpu', 'state', '差量 · 暂存写', ml='差量', n='1', why='只发变化，token 不重发', trap='暂存≠已进显存',
             card=card(
                 '`execute_model` 的开头：把调度器发来的差量写进常驻状态。',
                 ['顺序：`finish_requests`（含被抢占的）→ `free_states` → `add_requests` → `update_requests` → `block_tables.apply_staged_writes()`。' + R(MR, 1880, 1888),
                  '旧请求只带 `new_block_ids` / `num_computed_tokens` / `num_output_tokens`，不带 token。' + R(SOUT, 139, 147),
                  '写入先 `stage_write` 记在 CPU 数组里；每步 `apply_write` 打包拷到 GPU，再由**一个内核**把差量写进大张量。' + R(BU, 257, 282)],
                 [note('注释', '不开流水线并行时，调度器不发 token：worker 自己就是这些 token 的产生者（后半句是推断）。',
                       q='When PP is not used, new_token_ids will be empty.', ref=A(SOUT, 142)),
                  note('注释', 'CPU 写、GPU 读的暂存缓冲轮转 2 份：S1 的内核可能还在读缓冲 A，CPU 已经在写 S2 的缓冲 B。',
                       q='Default round-robin depth for the UVA buffer pools. Must be >= the number of', ref=A(BU, 22))],
                 ['`add_request` 里的写入**还没进 GPU**，要等 `apply_staged_writes`。' + R(ST, 111, 125),
                  '一个 token 都没排的步（比如只为通知「请求已结束」）改完状态就早退，不跑模型。' + R(MR, 1894, 1899)]),
             to='gpu.exec_src'),
        wire('w_gather', 'state', 'prep', '按槽号摘', ml='按槽摘', n='2', why='在 GPU 上并行 gather',
             card=card(
                 '拼输入的内核按 `idx_mapping` 去槽位张量里读。',
                 ['本例 S1、S2 都是 `idx_mapping=[1023]`：批内第 0 行 = 槽 1023。block table 也一样：`gather_block_tables` 把槽 1023 那行 `[5, 6, 0…]`（设）摘到批内第 0 行。' + R(BT, 173)],
                 [note('文档', '大状态张量大多在显存，gather 在 GPU 上并行跑，开销很低。',
                       q='Large state tensors are mostly stored on GPU memory, so gather runs in parallel on the GPU with low overhead.', ref=A(DOC, 39))])),
        wire('w_fill', 'prep', 'buf', '写进缓冲', ml='写缓冲', n='3', mlp=[255, 568], why='padding 写成固定值',
             card=card(
                 '把摘出来的值写进定长输入缓冲。',
                 ['`slot_mapping` = 物理 block × 16 + 块内偏移：S1 位置 0–15 → 80…95，16–19 → 96…99；S2 位置 20 → 6×16+4 = 100。' + R(BT, 427)],
                 [note('注释', '剩下的 slot 补成 -1，这是 CUDA graph 需要的（研究员的解读：这样 padding 的位置不会写进 KV cache）。',
                       q='Pad remaining slots to -1. This is needed for CUDA graphs.', ref=A(BT, 376))])),
        wire('w_run', 'buf', 'fwd', '图直接读', ml='图直接读', n='4', why='FULL 回放不传参',
             card=card(
                 '前向从定长缓冲取输入。',
                 ['FULL：`run_fullgraph(batch_desc)` 回放，不传输入；PIECEWISE：`run_pw_graph`；eager：直接 `self.model(**model_inputs)`。' + R(MR, 2172, 2219)],
                 [note('推断', '三条分支共用同一份输入和注意力元数据，区别只在「怎么发射前向」。')])),
        wire('w_park', 'fwd', 'ems', '存起来，回 None', ml='存起来', n='5', mlp=[85, 970], why='采样另起一次调用', trap='回 None 不是出错',
             card=card(
                 '前向发射完，把中间结果存进 runner 的单槽字段，`execute_model` 返回 `None`。',
                 ['存的是 `input_batch`、`hidden_states`、`attn_metadata` 等的引用，不是拷贝。' + R(MR, 2249, 2275)],
                 [note('注释', '接口契约：`execute_model` 返回 None 时，必须紧接着调 `sample_tokens` 才能拿到输出。',
                       q='"""If this method returns None, sample_tokens should be called immediately after', ref=A(WB, 218))])),
        wire('w_take', 'ems', 'samp', '取出并清空', ml='取出', n='6', why='第二次调用：sample_tokens',
             card=card(
                 '引擎主线程调 `sample_tokens(grammar_output)`，runner 从单槽里取出前向结果。',
                 ['没有结构化输出时，引擎在 `execute_model` 之后紧接着调；有掩码时先应用掩码再采样。' + R(CORE, 807, 814) + R(MR, 1773, 1782)],
                 [note('注释', '下一步之前，worker 必须先拿到推迟那一步的 sample_tokens()。',
                       q="Workers need a deferred step's sample_tokens() before the next step.", ref=A(CORE, 784))])),
        wire('w_post', 'samp', 'state', 'post_update 写回', ml='写回槽位', n='7', mfs='r', mts='r', mlp=[262, 970],
             why='下一步从这读，CPU 不碰', trap='排在拷贝之后',
             card=card(
                 '采样之后，GPU 内核把结果写回这个请求的槽位——这条线就是「下一步不用等 CPU」的原因。',
                 ['`last_sampled_tokens[槽]` = 最后一个采出的 token；`total_len += num_sampled`；token 追加进 `all_token_ids`；`已算 += query_len − 被拒数`。' + R(IB, 597, 633),
                  '本例 S1：`last=<法国>`、`total 20→21`、`已算 0→20`；S2：`last=<的>`、`total 21→22`、`已算 20→21`。'],
                 [note('注释', '故意排在建 `AsyncOutput` 之后：拷贝的 event 先记下，D2H 拷贝不用等后处理。',
                       q='ensuring that `copy_event` is recorded before calling postprocess.', ref=A(MR, 2415)),
                  note('推断', 'S2 拼输入的内核和 S1 的 `post_update` 排在同一条主 stream 上；CUDA 同一 stream 按序执行，所以 S2 读到的一定是 `<法国>`，中间不需要 CPU 参与。')],
                 ['`last_sampled_tokens`：写 = 第 k 步的 `post_update`；读 = 第 k+1 步的拼输入内核。CPU 两头都不碰。']),
             to='gpu.copy_src'),
        wire('w_copy', 'samp', 'copy', '另一条流拷回', ml='拷回', n='8', cls='dash', why='主 stream 不被拷贝挡住',
             card=card(
                 '在 `copy_stream` 上排「采样结果 → CPU」的拷贝。',
                 ['`AsyncOutput` 构造函数里：切到 `copy_stream` → `wait_stream(main_stream)` → `async_copy_to_np(sampled_token_ids)`、`num_sampled` 等 → `copy_event.record(copy_stream)`。' + R(AU, 147, 190)],
                 [note('推断', '先 `wait_stream`：不等主 stream 排到这里，拷贝可能在采样内核写完之前就开始。代码里没有注释，依据是 CUDA 语义。'),
                  note('推断', '拷贝单独一条流：主 stream 可以接着跑 `post_update` 和下一步的内核，不被 D2H 拷贝挡住。')],
                 ['拷的只是每个请求几个整数，代价在延迟而不在带宽（研究员的判断）。'])),
        wire('w_out', 'copy', 'cpu', 'get_output · 晚一步', ml='晚一步取', n='9', cls='dash', mfs='l', mts='l', mlp=[75, 568],
             why='只在这一行真等 GPU', trap='等的是最早那批',
             card=card(
                 '下一轮 `batch_queue` 满了，引擎取出最早那批，调 `get_output()` 拿 token id。',
                 ['`AsyncOutputFuture.result()` → `AsyncOutput.get_output()`，第一句就是 `copy_event.synchronize()`。' + R(UNI, 38, 44) + R(AU, 193)],
                 [note('注释', '用会睡眠的 event 等，避免忙轮询加速器驱动锁。',
                       q='Blocking (sleep) event to avoid busy-polling the accelerator driver lock.', ref=A(AU, 139))],
                 ['整条链里 CPU 真正等 GPU 的只有这一行；而且它等 S1 的时候，S2 已经排在 GPU 上了。']),
             to='gpu.copy_src'),
    ]

    steps = [
        beat('S1 进来：只有差量，先暂存',
             '引擎主线程调 `execute_model(S1)`。这个请求是**新请求**：20 个 prompt token、block `[5, 6]`（设）、已算 0。worker 先删结束的，再 `add_request`：`free_indices.pop()` 弹出末尾 → 槽 **1023**（设 `max_num_seqs=1024`）。这些写入先暂存在 CPU，`apply_staged_writes` 时才一次灌进显存。',
             note('文档', '每个请求在活跃期占一个固定的行，直到结束或被抢占。',
                  q='Assign each request a permanent row for its active lifetime (until finish or preemption).', ref=A(DOC, 36)),
             focus=['state', 'w_delta', 'cpu'],
             state={'cpu': g_cpu('execute_model(S1)', 'sample_tokens', 'get_output', on=0),
                    'state': g_state('新占', '20 / 0', '未定义')}),
        beat('S1：从槽里摘出预填输入',
             '内核按 `idx_mapping=[1023]` 摘：`input_ids` = 从 `all_token_ids` 拷出的 20 个 prompt token；`positions` 0…19；`seq_lens [20, 0, 0…]`；`slot_mapping` 80…99，其余 `-1`。填 last_sampled 的内核看到 `seq_len 20 ≤ prefill_len 20`，直接返回，不碰预填输入。',
             note('注释', '还在预填的请求没有采样出的 token，不写。',
                  q='Handling prefill tokens. No sampled or draft tokens.', ref=A(IB, 451)),
             focus=['prep', 'w_gather', 'w_fill', 'buf', 'state'],
             state={'prep': g_checks([('20 ≤ 20，还在预填', 'ok'), ('填 last_sampled', 'skip')]),
                    'buf': g_buf('20 个 prompt', '0…19', '[20, 0, 0…]', '80…99, -1…')}),
        beat('S1：前向排上，返回 None',
             '其实在拼输入之前，CPU 已按批形状挑好了图：1 个请求、20 个 token、在预填 → 不是「均匀 decode」→ **PIECEWISE**，补到 **24**（设：按默认网格推的）。前向内核排上主 stream，CPU 不等它算完；把 `input_batch`、`hidden_states` 等存进单槽，`execute_model` **返回 None**。',
             note('注释', 'execute_model 返回 None 时，必须紧接着调 sample_tokens。',
                  q='"""If this method returns None, sample_tokens should be called immediately after', ref=A(WB, 218)),
             focus=['fwd', 'w_run', 'w_park', 'ems'],
             state={'fwd': g_fwd('PIECEWISE', '20 → 补到 24'),
                    'ems': g_text('满', 'S1 的前向', tone='run')}),
        beat('S1：采样，拷贝排到另一条流',
             '引擎主线程接着调 `sample_tokens(None)`（没有结构化输出）：取出并清空单槽 → 只对第 **19** 行隐状态算 logits → 采出 `<法国>`。随即建 `AsyncOutput`：切到 `copy_stream`，先 `wait_stream(main)`，再排「token id 拷到 CPU」，最后 `record(e1)`。全是排队，CPU 马上往下走。',
             note('推断', '只算最后一个位置的 logits：预测下一个 token 只要它，对 20 个位置都算 `[20, vocab]` 是浪费。'),
             focus=['samp', 'w_take', 'w_copy', 'copy', 'ems'],
             state={'cpu': g_cpu('execute_model(S1)', 'sample_tokens(S1)', 'get_output', on=1),
                    'ems': g_text('空', '已被 S1 取走'),
                    'samp': g_samp('[19]', '<法国>（显存里）'),
                    'copy': g_copy('e1 未完成', 'S1 排队中')}),
        beat('S1：post_update 写回槽位',
             '`AsyncOutput` 建好之后，主 stream 上再排 `post_update`：`last_sampled_tokens[1023] = <法国>`，`all_token_ids[1023][20] = <法国>`，`total_len 20→21`，`已算 0→20`。CPU 从 `sample_tokens` 返回，手里只有一个还没完成的 `AsyncOutput`——它**不知道**采出了什么。',
             note('注释', '故意先记 copy_event、再做后处理：D2H 拷贝不用等后处理。',
                  q='ensuring that `copy_event` is recorded before calling postprocess.', ref=A(MR, 2415)),
             focus=['w_post', 'state', 'samp'],
             state={'state': g_state('占用中', '21 / 20', '<法国>')}),
        beat('S2：CPU 不知道 <法国>，照样拼输入',
             'S2 的差量只有 `已算=[20]`：没有新 block，也**不带 token**。填 last_sampled 的内核：`seq_len 21 > 20`，且 `21 − 1 = 20 ≥ 20` → 把 `last_sampled_tokens[1023]` 写进 `input_ids[0]`。它排在 S1 的 `post_update` 后面，同一条 stream 按序执行，读到的一定是 `<法国>`。',
             note('文档', '核心执行循环是一条没有 CPU 同步点的 CUDA stream。',
                  q='MRV2 instead assumes the core model execution loop is a CUDA stream with no CPU synchronization points.', ref=A(DOC, 47)),
             focus=['prep', 'w_gather', 'state', 'w_fill', 'buf', 'cpu'],
             state={'cpu': g_cpu('execute_model(S2)', 'sample_tokens', 'get_output', on=0),
                    'prep': g_checks([('21 ≤ 20，不在预填', 'no'), ('20 ≥ 20，填 last_sampled', 'ok')]),
                    'buf': g_buf('[<法国>] GPU 自填', '[20]', '[21, 0, 0…]', '[100]')}),
        beat('S2：FULL 图回放，再采一个',
             '1 个请求 1 个 token、不在预填 → 均匀 decode → **FULL** decode 图，不补齐。`run_fullgraph` 不传输入，图直接读刚写好的缓冲。`sample_tokens(S2)` 采出 `<的>`；拷贝排在 `copy_stream`，记一个**新** event e2；`post_update` 把槽 1023 改成 `last=<的>`、`total 22`、`已算 21`。',
             note('注释', 'FULL 模式不传输入张量：它们已经在图的输入缓冲里。',
                  q='because they are already copied to the CUDA graph input buffers.', ref=A(MR, 2175)),
             focus=['fwd', 'w_run', 'samp', 'w_post', 'w_copy', 'copy'],
             state={'cpu': g_cpu('execute_model(S2)', 'sample_tokens(S2)', 'get_output', on=1),
                    'fwd': g_fwd('FULL（decode）', '1，不补齐'),
                    'ems': g_text('空', '已被 S2 取走'),
                    'samp': g_samp('[0]', '<的>（显存里）'),
                    'copy': g_copy('e1、e2 都在飞', 'S1、S2 各一份'),
                    'state': g_state('占用中', '22 / 21', '<的>')}),
        beat('晚一步：CPU 这才等 S1',
             'S2 入队后 `batch_queue` 满了（2 = 2），引擎 `pop()` 出最早的 S1，调 `get_output()`：第一句 `copy_event.synchronize()` 等 e1——**这是 CPU 唯一真正等 GPU 的地方**。拿到 `[[<法国>]]`，按 `num_sampled=1` 截短，交给调度器。这时 GPU 上 S2 已经排好了。',
             note('注释', '用可睡眠的 event 等，不忙轮询驱动锁。',
                  q='Blocking (sleep) event to avoid busy-polling the accelerator driver lock.', ref=A(AU, 139)),
             focus=['copy', 'w_out', 'cpu'],
             state={'cpu': g_cpu('execute_model(S2)', 'sample_tokens(S2)', 'get_output(S1)', on=2),
                    'copy': g_copy('e1 已完成', '[[<法国>]]')}),
        beat('收尾：一个空步，把槽还掉',
             '`<eos>`（第 7 个 token）回到 CPU 后，调度器结束请求，但不马上通知 worker：结束的 id 附在**下一次** `SchedulerOutput` 里。那一步一个 token 都没排：`finish_requests` 把槽 1023 放回 `free_indices`，然后 `total_num_scheduled_tokens == 0` → 直接返回，不跑模型。槽里的旧值不清，下一个占用者会覆盖。',
             note('推断', '这个空步是研究员从 `has_requests()` 的定义和早退分支合起来推的，没找到直接描述它的注释或测试。'),
             focus=['state', 'w_delta', 'cpu'],
             state={'cpu': g_cpu('execute_model(空步)', 'sample_tokens（不调）', 'get_output', on=0),
                    'state': g_state('已归还', '旧值没清', '旧值没清'),
                    'prep': None, 'fwd': g_fwd('不跑', '0'), 'samp': g_samp('—', '—'),
                    'copy': g_copy('—', '—')}),
    ]

    return scene(1220, 470, parts, wires, steps, mw=340, mh=1504,
                 groups=[group('主 stream：A→F 按发射顺序串行', [288, 6, 928, 456], m=[48, 184, 244, 1130]), group('copy_stream', [4, 290, 182, 168], m=[48, 1340, 244, 160])],
                 cap='一步之内：A→F 是同一条主 stream 上按序排的活，G 在另一条 copy_stream 上；例子接顶层：槽 1023，S1 预填 20 个，S2 起每步 1 个')


# ---------------------------------------------------------------- 源码页
def _state_src():
    return node(
        'gpu.state_src', '常驻状态源码', '常驻请求状态：字段与占槽',
        '`RequestState` 的字段长什么样、`add_request` 为什么只是「暂存」。',
        lead='你在「GPU 执行」→「常驻请求状态」里。两段源码：这张大表有哪些列；新请求怎么拿到槽 1023、写入为什么要等 `apply_staged_writes`。',
        src=ST + ':26-134',
        blocks=[
            code(ST, [(26, 72)], notes=[
                at(30, '推断', '空槽号列表 `[0 … 1023]`。`pop()` 弹末尾，所以第一个请求拿到 **1023**；没有注释说明这是有意的。', has='self.free_indices = list(range(max_num_reqs))'),
                at((32, 36), '注释', '`all_token_ids` 可能有好几 GB（设 1024 槽 × 131072 长 × int32 ≈ 512 MiB），默认放 UVA 而不是显存。',
                   q='# NOTE(woosuk): This tensor can be extremely large (e.g., several GBs)', has='use UVA instead of GPU by default'),
                at((43, 51), '注释', '`prompt_len` 是用户给的 prompt 长度，`prefill_len` 是要预填的长度；抢占恢复后后者更大。本例两者都是 20。',
                   q='# preemption, prefill_len may be greater. Differentiating between these values', has='prefill_len may be greater'),
                at(64, '注释', 'CPU 上的乐观镜像，是 GPU 真值的上界；每步用调度器发来的已算数覆盖。',
                   q='# Optimistic CPU mirror of num_computed_tokens (upper bound on GPU value).', has='Optimistic CPU mirror'),
                at((67, 70), None, '**本页主角**：`[max_num_reqs, 1]` 的 int64 显存张量。上一步的 `post_update` 写，下一步的拼输入内核读，CPU 全程不碰。本例 S1 之后槽 1023 这一格是 `<法国>`。', has='self.last_sampled_tokens = torch.zeros('),
            ], cap='RequestState.__init__：一行一个请求的大表'),
            code(ST, [(91, 134)], notes=[
                at(100, None, '占槽：本例拿到 1023。之后整个生命周期都在这一行。', has='req_idx = self.free_indices.pop()'),
                at((111, 115), None, '这里只是 `stage_write`：值记在 CPU 的暂存区，**还没进显存**。', has='self.all_token_ids.stage_write(req_idx, 0, all_token_ids)'),
                at((120, 125), None, '`execute_model` 开头调它：把这一步所有暂存写入一次性打包、拷到 GPU、由一个内核写进大张量。', has='def apply_staged_writes(self)'),
                at(133, '推断', '还槽只是把号码放回列表，显存里的旧值不清；下一个占用者 `add_request` 时覆盖。旧内核还在用这一行也不怕：新写入排在同一条主 stream 后面。', has='self.free_indices.append(req_idx)'),
            ], cap='add_request / apply_staged_writes / remove_request'),
        ])


def _exec_src():
    return node(
        'gpu.exec_src', 'execute_model 开头', 'execute_model 的开头：同步状态、早退、挑图',
        '`execute_model` 头 90 行：先把差量灌进常驻状态，再按批形状决定用哪张 CUDA graph。',
        lead='你在「GPU 执行」→「差量 · 暂存写」这条线上。`execute_model` 接近 400 行，这里只看主干的开头：同步状态 → 空步早退 → 挑图 → 拼输入。',
        src=MR + ':1876-1966',
        blocks=[
            code(MR, [(1876, 1899)], notes=[
                at((1880, 1883), None, '顺序：先删（含被抢占的）再加、再更新。本例 S1 走 `add_requests`，S2 起走 `update_requests`。', has='self.finish_requests(scheduler_output)'),
                at(1888, None, '此刻才把这一步暂存的 block id 写进显存。', has='self.block_tables.apply_staged_writes()'),
                at((1894, 1899), None, '一个 token 都没排就不跑模型。本例最后那个「只为还槽」的空步就在这里返回。', has='total_num_scheduled_tokens == 0'),
            ], cap='同步状态 + 空步早退'),
            code(MR, [(1901, 1966)], notes=[
                at((1905, 1907), None, 'CPU 上算本批顺序、每请求 token 数、哪些在预填。S1：1 个请求 20 个、预填；S2：1 个请求 1 个、decode。', has='self.gather_batch_req_state('),
                at((1927, 1931), '注释', '编码器-解码器模型这一步有 encoder 输入时要跑 eager：要更新交叉注意力缓存。',
                   q='# Encoder-decoder models such as Whisper should run eager/non-compiled', has='skip_compiled = True'),
                at((1933, 1949), None, '形状决定一切：挑出这一步用 FULL / PIECEWISE / eager。S1 → PIECEWISE（补到 24，设）；S2 → FULL decode 图。', has='dispatch_cg_and_sync_dp('),
                at((1951, 1954), '注释', 'DP 下所有 rank 都没活才早退。', q='# All DP ranks have zero tokens to run.', has='batch_desc.num_tokens == 0'),
                at((1963, 1966), None, '拼输入（下一页）+ 摘 block table、算 slot_mapping。', has='input_batch = self.prepare_inputs('),
            ], cap='挑图，然后拼输入'),
        ])


def _prep_src():
    return node(
        'gpu.prep_src', '拼输入源码', '拼输入：last_sampled_tokens 怎么进到下一步',
        '三个内核的调用点，和「把上一步采出的 token 填进来」那个内核本身。',
        lead='你在「GPU 执行」→「拼输入内核」里。先看 `prepare_inputs` 里三个内核的调用顺序，再看 `combine_sampled_and_draft_tokens` 内核里的两道守卫。',
        src=MR + ':1592-1627',
        blocks=[
            code(MR, [(1592, 1627)], notes=[
                at((1593, 1602), None, '只有本批有预填才跑：把 prompt 从 `all_token_ids` 拷进 `input_ids`。S1 跑、S2 不跑。', has='if batch_req_state.has_prefill:'),
                at((1605, 1611), None, '位置 = 已算数 + 块内偏移；`seq_len` = 已算数 + 本步 token 数。S1：`0…19` / 20；S2：`[20]` / 21。', has='prepare_pos_seq_lens('),
                at((1614, 1615), '注释', '有些输入 token 直接从上一步采样结果读——CPU 不知道上一个 token 也能排下一步。',
                   q='# Some input token ids are directly read from the last sampled tokens', has='directly read from the last sampled tokens'),
                at(1619, None, '传进去的是整张 `last_sampled_tokens`，内核按 `idx_mapping` 找槽。', has='self.req_states.last_sampled_tokens,'),
            ], cap='prepare_inputs 里的三个内核（按排队顺序）'),
            code(IB, [(436, 472)], notes=[
                at(441, None, '要采样的位置 = 查询段末尾往前 `num_logits` 个。S1：20−1 = 19；S2：1−1 = 0。', has='logits_start = query_end - num_logits'),
                at((450, 452), '注释', '第一道守卫：还在预填就返回。S1：20 ≤ 20，走这里。',
                   q='# Handling prefill tokens. No sampled or draft tokens.', has='if seq_len <= prefill_len:'),
                at((454, 456), '注释', '第二道守卫：只改「生成出来的 token」那几格，不动 prompt 尾巴。S2：21−1 = 20 ≥ 20，通过。',
                   q='# Keep prompt-tail slots intact; only rewrite generated-token slots.', has='first_logit_seq_pos >= prefill_len'),
                at((458, 459), None, '读槽 1023 的 `<法国>`，写进 `input_ids[0]`。这个读排在 S1 的 `post_update` 之后，同一条 stream，保证读到新值。', has='tl.store(input_ids_ptr + logits_start, last_token_id)'),
                at((462, 472), None, '投机解码的草稿 token 写到查询段末尾；本例没有，跳过。', has='if num_draft_tokens > 0:'),
            ], cap='combine_sampled_and_draft_tokens 内核（每个请求一个 program）'),
        ])


def _graph_src():
    return node(
        'gpu.graph_src', 'CUDA graph 分派', 'CUDA graph：挑哪张图，三条前向分支',
        '`dispatch` 怎么挑图、什么时候落到 eager；前向的三条分支共用同一份输入。',
        lead='你在「GPU 执行」→「前向」里。上一段是挑图（S1 挑到 PIECEWISE、S2 挑到 FULL），下一段是按挑出来的模式发射前向。',
        src=CG + ':561-606',
        blocks=[
            code(CG, [(561, 606)], notes=[
                at((570, 577), '注释', '均匀 decode 批取它查询长度下、装得下的最小 decode 图；其他批取最小的兼容 token 键图。',
                   q='"""Find the graph to run a batch with, else an eager descriptor.', has='Find the graph to run a batch with'),
                at(580, None, '先按 token 数查 token 键图的候选（PIECEWISE 用）。S1 是 20 个 token；研究员按默认网格推，落在 24 那张（设）。', has='candidates = self._candidates.get('),
                at((581, 587), None, '是均匀 decode，就把对应请求数的 FULL decode 图插到候选最前面。S2：1 个请求 1 个 token。', has='candidates = [decode_descs[n], *candidates]'),
                at((588, 598), None, '逐个检查 token 数、请求数、LoRA 数、微批数是否兼容，第一个兼容的就用。', has='if _is_compatible('),
                at((599, 605), None, '一个都不合适（或根本没捕获，如 `--enforce-eager`）→ eager。', has='cg_mode=CUDAGraphMode.NONE,'),
            ], cap='CudaGraphManager.dispatch'),
            code(MR, [(2171, 2219)], notes=[
                at((2172, 2180), '注释', 'FULL：直接回放，不传输入——输入已经在图的缓冲里。这就是输入缓冲要定长的原因。',
                   q='# because they are already copied to the CUDA graph input buffers.', has='self.cudagraph_manager.run_fullgraph(batch_desc)'),
                at((2209, 2216), None, 'PIECEWISE：注意力等不能捕获的算子留在图外，其余分段回放。S1 走这里。', has='run_pw_graph('),
                at((2217, 2219), None, 'eager：直接调模型。三条分支用的是同一份 `input_batch` 和 `attn_metadata`。', has='model_output = self.model(**model_inputs)'),
            ], cap='execute_model 里的前向：三条分支'),
        ])


def _copy_src():
    return node(
        'gpu.copy_src', '采样与拷回源码', '采样之后：拷贝、写回槽位、晚一步取',
        '读完能回答：CPU 在哪一行真正等 GPU？`last_sampled_tokens` 在哪一行被写？',
        lead='你在「GPU 执行」→「采样」「copy_stream 拷回」和「post_update 写回」这几处。四段源码按发生顺序：建 `AsyncOutput` → `post_update` → 内核里写槽位 → 晚一步 `get_output()`。',
        src=AU + ':117-203',
        blocks=[
            code(MR, [(2376, 2387), (2413, 2424)], notes=[
                at((2377, 2387), None, '先建 `AsyncOutput`：拷贝在构造函数里就排上了 `copy_stream`。', has='async_output = AsyncOutput('),
                at((2414, 2417), '注释', '故意放在建 `AsyncOutput` 之后：event 先记下，D2H 拷贝不用等后处理。',
                   q='# ensuring that `copy_event` is recorded before calling postprocess.', has='intentionally done after creating the AsyncOutput'),
                at((2418, 2424), None, '再排 `post_update`：更新槽位里的 last_sampled / total_len / 已算数。', has='self.postprocess_sampled('),
            ], cap='sample_tokens 的后半：先拷贝，后写回'),
            code(IB, [(597, 604)], notes=[
                at((599, 603), None, '把最后一个采出的 token 写进这个槽的 `last_sampled_tokens`。S1 之后槽 1023 = `<法国>`。', has='tl.store(last_sampled_tokens_ptr + req_state_idx, token_id)'),
                at(604, None, '`total_len` 加上采出的个数：S1 20→21。', has='total_len + num_sampled'),
            ], cap='post_update 内核：写回槽位'),
            code(AU, [(130, 150), (190, 203)], notes=[
                at((130, 132), '注释', '拷贝在另一条 stream 上，必须留住 GPU 张量的引用。',
                   q='# as the copy operations are performed on a different CUDA stream than', has='We must retain references to the GPU tensors'),
                at((139, 140), '注释', '每个 `AsyncOutput` 新建一个会睡眠的 event：S1、S2 同时在飞也各等各的。',
                   q='# Blocking (sleep) event to avoid busy-polling the accelerator driver lock.', has='torch.Event(blocking=True)'),
                at((147, 148), '推断', '切到 copy_stream，并让它先等主 stream 排到这里为止的活（即采样内核）。不等的话可能拷到没写完的值。', has='copy_stream.wait_stream(main_stream)'),
                at(150, None, '采出的 token id 异步拷进 CPU 的 numpy 数组。', has='async_copy_to_np(sampler_output.sampled_token_ids)'),
                at(190, None, '在 copy_stream 上记下「拷完了」这个点。本例 S1 是 e1。', has='self.copy_event.record(copy_stream)'),
                at(193, None, '**整条链里 CPU 真正等 GPU 的只有这一行**，而且是晚一步才调。', has='self.copy_event.synchronize()'),
                at((199, 202), None, '按 `num_sampled` 截短：分块预填中间块得到空列表，不会冒出假 token。', has='del token_ids[num_tokens:]'),
            ], cap='AsyncOutput：构造时排拷贝，get_output 时才等'),
        ])


def _defer_src():
    return node(
        'gpu.defer_src', '推迟采样', 'deferred_step：结构化输出时，采样推迟一步',
        '单槽暂存为什么存在：前向先发，语法掩码晚到再采样。',
        lead='你在「GPU 执行」→「单槽暂存」里。这一页换个场景（设）：同一个请求带了 JSON schema。看引擎怎么在 S2「只发前向、不采样」，等 S1 的 token 回来再补采样。',
        src=CORE + ':784-882',
        blocks=[
            code(ASCH, [(38, 48)], notes=[
                at(43, None, '谁触发推迟：带结构化输出、而且还有占位符（上一步的 token 还没回来）。S1 时占位符 0 → 不推迟；S2 时 1 → 推迟。', has='pending_structured_output_tokens |= request.num_output_placeholders > 0'),
                at((45, 47), None, '排完一步就加一个占位符。', has='request.num_output_placeholders +='),
            ], cap='AsyncScheduler._update_after_schedule'),
            code(CORE, [(784, 828)], notes=[
                at((784, 785), '注释', '有一步还没采样时不调度新的一步：和 runner 的单槽暂存咬合。',
                   q="# Workers need a deferred step's sample_tokens() before the next step.", has='self.deferred_step is None'),
                at((800, 814), None, '不用等 token：马上算语法掩码、发射采样。S1 走这里（第一个 token 的掩码只靠语法起点）。', has='if not scheduler_output.pending_structured_output_tokens:'),
                at((815, 818), '注释', '要等上一步的 token：前向已经发出去了，只把采样推迟。S2 走这里，**不入** `batch_queue`。',
                   q='# We need to defer sampling until we have processed the model', has='self.deferred_step = (scheduler_output, exec_future)'),
                at((820, 822), None, '只有没推迟的步才入队。', has='batch_queue.appendleft((future, scheduler_output, exec_future))'),
            ], cap='step_with_batch_queue 前半：发射，或推迟'),
            code(CORE, [(856, 882)], notes=[
                at((859, 863), '注释', '队列深度 > 2 时，要等队列里没有和它共享请求的步。',
                   q='# With async scheduling depth > 2, wait until no queued step shares a', has='shares_requests_with'),
                at((876, 880), '注释', 'S1 的 token 已经交给语法了，现在才算得出 S2 的掩码。',
                   q='# We now have the tokens needed to compute the bitmask for the', has='get_grammar_bitmask('),
                at((881, 882), None, '补发采样，入队。GPU 上看是：采样 S1 → 前向 S2（早已排好）→ 等 CPU 算掩码 → 采样 S2。', has='future = self.model_executor.sample_tokens(grammar_output, non_block=True)'),
            ], cap='step_with_batch_queue 后半：补采样'),
        ])


def nodes():
    return {'gpu': node(
        'gpu', 'GPU 执行', 'GPU 执行：一步之内 GPU 上排了哪些活',
        '常驻槽位、拼输入内核、CUDA graph、单槽暂存、copy_stream 和 event：CPU 为什么不用等上一个 token 就能排下一步。',
        lead='你在顶层「GPU 执行」（`GPUModelRunner`）这个框里面。一圈 A→F 是一步之内排在**同一条主 stream** 上的活，G 在另一条 `copy_stream` 上；按 ▶ 接着顶层的例子走 S1、S2，看 `<法国>` 怎么不经过 CPU 就进了下一步。',
        src=MR + ':1865-2424',
        blocks=[_scene()],
        children=[_state_src(), _exec_src(), _prep_src(), _graph_src(), _copy_src(), _defer_src()])}
