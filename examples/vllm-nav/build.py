#!/usr/bin/env python3
# Builds the vLLM navigator canvas.json by slicing VERBATIM line ranges from a
# cloned vllm checkout and computing every anchor/term/step line number by
# searching within the extracted card text (per SKILL.md: "行号锚点用脚本算").
import json, re, sys, os

REPO = "/tmp/vllm"

# card_id -> (name, rel_path, start_line, end_line, lang)
SLICES = {
    "entry":    ("LLM.generate()",              "vllm/entrypoints/llm.py",            418, 428, "py"),
    "engine":   ("EngineCore.step()",           "vllm/v1/engine/core.py",            583, 596, "py"),
    "worker":   ("GPUModelRunner.execute_model()","vllm/v1/worker/gpu_model_runner.py",4294,4304,"py"),
    "sample":   ("Sampler.forward()",           "vllm/v1/sample/sampler.py",          73,  82, "py"),
    "output":   ("OutputProcessor.process_outputs()","vllm/v1/engine/output_processor.py",598,608,"py"),
    "sched":    ("Scheduler.schedule()",        "vllm/v1/core/sched/scheduler.py",   476, 487, "py"),
    "kvmgr":    ("KVCacheManager.get_computed_blocks()","vllm/v1/core/kv_cache_manager.py",232,244,"py"),
    "attn":     ("AttentionImpl.forward()",     "vllm/v1/attention/backend.py",      990,1002, "py"),
    "executor": ("Executor.execute_model()",    "vllm/v1/executor/abstract.py",      223, 229, "py"),
}

def read_slice(path, a, b):
    with open(os.path.join(REPO, path), encoding="utf-8") as f:
        lines = f.read().split("\n")
    return "\n".join(lines[a-1:b])  # 1-indexed inclusive

def find_line(code, token):
    """1-indexed line of first whole-word occurrence of token in code."""
    pat = re.compile(r"\b" + re.escape(token) + r"\b")
    for i, ln in enumerate(code.split("\n"), 1):
        if pat.search(ln):
            return i
    raise SystemExit(f"token {token!r} not found in card")

cards_code = {}
cards = []
for cid, (name, path, a, b, lang) in SLICES.items():
    code = read_slice(path, a, b)
    cards_code[cid] = code
    cards.append({
        "id": cid, "name": f"{cid} · {name}",
        "file": f"{path}:{a}", "lang": lang,
        "layout": None,  # filled below
        "collapsed": True, "code": code,
    })
cards = {c["id"]: c for c in cards}

# --- layout (col, band) ---
LAYOUT = {
    "entry":  (0,0), "engine": (1,0), "worker": (2,0), "sample": (3,0), "output": (4,0),
    "sched":  (1,1), "kvmgr":  (2,1), "attn":   (3,1),
    "executor": (0,2),
}
for cid,(col,band) in LAYOUT.items():
    cards[cid]["layout"] = {"col": col, "band": band}

# highlight cards (heart of the story) not collapsed
for cid in ("engine","sched","kvmgr"):
    cards[cid]["collapsed"] = False

# --- terms (变元注释) : (card, token, note) ; line computed ---
TERMS = [
    ("entry","sampling_params","每条 prompt 的采样参数：温度、top-p、max_tokens 等"),
    ("engine","scheduler_output","本步要算什么的清单：哪些请求、各几个 token、用哪些 KV 块"),
    ("sched","num_computed_tokens","该请求已算过的 token 数；与目标数的差就是这步要补算的量"),
    ("kvmgr","KVCacheBlocks","一组 KV 缓存块的句柄——可能是前缀缓存命中的旧块，也可能新借"),
    ("worker","scheduler_output","调度器发来的这步计划：worker 据此组 batch、取 KV 块、跑前向"),
    ("attn","kv_cache","本层所有序列的 KV，按定长块存放，物理上并不连续"),
    ("attn","attn_metadata","块表 + 序列长度：告诉 kernel 每个 query 去哪些块取 KV"),
    ("sample","sampling_metadata","各请求的采样设置打包成 GPU 张量：温度、top-k/p、惩罚项"),
    ("executor","collective_rpc","对所有 worker 进程集体调用同名方法并收集结果——分布式统一入口"),
    ("output","EngineCoreOutput","引擎每步产出的原始结果：新 token id、logprobs、完成标志（还不是文字）"),
]
for cid, tok, note in TERMS:
    cards[cid].setdefault("terms", []).append(
        {"line": find_line(cards_code[cid], tok), "token": tok, "note": note})

card_list = [cards[c] for c in
             ["entry","engine","worker","sample","output","sched","kvmgr","attn","executor"]]

# --- regions ---
regions = [
    {"id":"A","title":"故事线 A · 一次生成请求的主流程","blurb":"请求进，文本出",
     "hue":"blue","cards":["entry","engine","worker","sample","output"]},
    {"id":"B","title":"故事线 B · 为什么 vllm 快","blurb":"批处理连续，显存分页",
     "hue":"amber","cards":["sched","kvmgr","attn"]},
]

# --- wires ---
def L(card, tok):  # line of token in a card
    return find_line(cards_code[card], tok)

wires = [
    {"id":"w-entry-engine","kind":"call",
     "from":{"card":"entry"},"to":{"card":"engine"}},
    {"id":"w-engine-worker","kind":"call",
     "from":{"card":"engine","line":L("engine","execute_model")},"to":{"card":"worker"}},
    {"id":"w-engine-sched","kind":"call",
     "from":{"card":"engine","line":L("engine","schedule")},"to":{"card":"sched"}},
    {"id":"w-sched-kv","kind":"call",
     "from":{"card":"sched"},"to":{"card":"kvmgr"}},
    {"id":"d-kv-attn","kind":"data","label":"KV blocks",
     "from":{"card":"kvmgr","side":"right"},"to":{"card":"attn","side":"left"}},
    {"id":"w-worker-attn","kind":"call",
     "from":{"card":"worker"},"to":{"card":"attn"}},
    {"id":"w-engine-exec","kind":"call",
     "from":{"card":"engine","side":"left"},"to":{"card":"executor"}},
    {"id":"d-worker-sample","kind":"data","label":"logits",
     "from":{"card":"worker"},"to":{"card":"sample"}},
    {"id":"d-sample-out","kind":"data","label":"token ids",
     "from":{"card":"sample"},"to":{"card":"output"}},
    {"id":"d-out-entry","kind":"data","label":"RequestOutput",
     "from":{"card":"output","side":"left"},"to":{"card":"entry","side":"right"}},
]

# --- notes ---
notes = [
    {"id":"bg0","flavor":"bg","tag":"背景 · 这是什么仓库",
     "text":"vLLM：高吞吐 LLM 推理引擎，当前是 v1 引擎（vllm/v1）。三问读懂它——请求怎么排队执行、显存怎么省着用、前向怎么摊到多卡。",
     "place":{"corner":"nw"},"step":0},
    {"id":"n-step","flavor":"intent","tag":"NOTE · core.py:594",
     "text":"一个 step = 调度一次 + 执行一次前向：所有在跑的请求被打成一个 batch 一起算。",
     "anchor":{"card":"engine","line":L("engine","schedule")},
     "place":{"side":"above","of":"engine"},"step":2},
    {"id":"n-cb","flavor":"intent","tag":"NOTE · scheduler.py:480",
     "text":"调度器眼里没有 prefill/decode 之分：每个请求只有 num_computed_tokens，每步尽量往前追。",
     "anchor":{"card":"sched","line":L("sched","num_computed_tokens")},
     "place":{"side":"above","of":"sched"},"step":3},
    {"id":"n-paged","flavor":"bg","tag":"背景 · 分页 KV 缓存",
     "text":"显存像操作系统分页：KV 切成定长块从空闲队列借还；相同前缀的块直接复用（前缀缓存）。",
     "anchor":{"card":"kvmgr","line":1},
     "place":{"side":"above","of":"kvmgr"},"step":4},
    {"id":"n-attn","flavor":"intent","tag":"NOTE · backend.py:996",
     "text":"分页注意力：kv_cache 不连续，attn_metadata 记着每个 token 的块位置，kernel 按块寻址。",
     "anchor":{"card":"attn","line":L("attn","kv_cache")},
     "place":{"side":"left","of":"attn"},"step":5},
    {"id":"n-exec","flavor":"bg","tag":"背景 · 分布式执行",
     "text":"并行边界：executor 用 collective_rpc 把前向广播到每张卡的 worker，TP/PP 都在这一层。",
     "anchor":{"card":"executor","line":L("executor","collective_rpc")},
     "place":{"side":"above","of":"executor"},"step":6},
]

# --- steps ---
steps = [
    {"title":"总览 · vllm 是怎么长的",
     "caption":"领航图：上排=请求主流程 entry→engine→worker→sample→output，中排三张卡=vllm 为何快，左下=分布式执行。灰线调用、琥珀线数据，按 ▶ 走。",
     "fit":True},
    {"title":"① 请求怎么进引擎","storyline":"A",
     "caption":"LLM.generate 收下 prompts 和 sampling_params，批量交给 v1 引擎——离线推理和 OpenAI API 服务器最终都汇到这里。",
     "wires":["w-entry-engine"],"lines":[["entry",L("entry","sampling_params")]],
     "expand":["entry"],"focus":["entry","engine"]},
    {"title":"② 引擎的一步：调度 + 前向","storyline":"A",
     "caption":"step() 是心跳：先 schedule() 定这步算什么，再 execute_model() 跑一次批量前向，所有在跑的请求共享这一步。",
     "wires":["w-engine-sched","w-engine-worker"],
     "lines":[["engine",L("engine","schedule")],["engine",L("engine","execute_model")]],
     "expand":["engine"],"focus":["engine","sched","worker","n-step"]},
    {"title":"③ 连续批处理：没有 prefill/decode 之分","storyline":"B",
     "caption":"vllm 的第一个魔法。调度器不分预填充/解码阶段——每个请求只有 num_computed_tokens，每步尽量把它追到目标。天然覆盖分块预填充、前缀缓存、投机解码。",
     "wires":["w-engine-sched"],
     "lines":[["sched",L("sched","num_computed_tokens")]],
     "expand":["sched"],"focus":["engine","sched","n-cb"]},
    {"title":"④ 分页显存：KV 是一页页借来的","storyline":"B",
     "caption":"第二个魔法。KVCacheManager 把 KV 缓存当操作系统内存来分页：定长块从空闲队列借还，碎片几乎为零；命中相同前缀直接复用旧块。",
     "wires":["w-sched-kv"],"lines":[["kvmgr",1]],
     "expand":["kvmgr"],"focus":["sched","kvmgr","n-paged"]},
    {"title":"⑤ 分页注意力：直接读非连续的 KV 块","storyline":"B",
     "caption":"分页显存要注意力配合：forward 拿 kv_cache + attn_metadata 按块表寻址取每个 query 的 KV——这就是 PagedAttention。",
     "wires":["d-kv-attn","w-worker-attn"],
     "lines":[["attn",L("attn","kv_cache")],["attn",L("attn","attn_metadata")]],
     "expand":["attn"],"focus":["kvmgr","attn","worker","n-attn"]},
    {"title":"⑥ 前向：一次调度喂给多卡 worker","storyline":"A",
     "caption":"GPUModelRunner 按 scheduler_output 组 batch 跑模型；executor 用 collective_rpc 把前向广播到每张卡，TP/PP 都在这层。",
     "wires":["w-engine-exec"],
     "lines":[["worker",L("worker","scheduler_output")]],
     "expand":["worker","executor"],"focus":["engine","worker","executor","n-exec"]},
    {"title":"⑦ 采样与返回","storyline":"A",
     "caption":"logits 交 Sampler 采出 token id；OutputProcessor 去标记化成文字、包成 RequestOutput 回到调用方，一步闭环。",
     "wires":["d-worker-sample","d-sample-out","d-out-entry"],
     "lines":[["sample",L("sample","sampling_metadata")],["output",L("output","EngineCoreOutput")]],
     "expand":["sample","output"],"focus":["entry","worker","sample","output"]},
    {"title":"⑧ 从哪儿深潜",
     "caption":"值得深潜：① 调度器取舍（分块预填充/抢占）② KV 块池与前缀缓存 ③ PagedAttention kernel ④ 分布式 executor ⑤ 投机解码。挑一个我再深潜。",
     "fit":True},
]

canvas = {
    "meta":{"title":"vLLM 领航图",
            "subtitle":"vllm-project/vllm · v1 引擎 · 9 个子系统",
            "audience":"有经验的后端/ML 工程师，第一次读 vllm；中文；准确优先"},
    "regions":regions,"cards":card_list,"wires":wires,"notes":notes,"steps":steps,
}

out = sys.argv[1] if len(sys.argv) > 1 else "vllm-nav.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(canvas, f, ensure_ascii=False, indent=2)
print(f"wrote {out}  ({len(card_list)} cards, {len(wires)} wires, {len(notes)} notes, {len(steps)} steps)")
