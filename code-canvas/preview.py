#!/usr/bin/env python3
"""preview.py — 秒级预览图：面对陌生大仓库时的第一张图（机械层）。

金字塔最底层：预览（秒级，本脚本）→ 领航（分钟级 LLM）→ 深潜（点单）。
卡片 = 顶层模块（目录），卡内是该模块「值得拿出来」的 def/class 签名行
（真实源码行，按扇入/入口名/类规模启发式挑选），线 = 模块间调用聚合。
meta.mode:"preview"——这是索引物不是代码阅读物，不声称逐字溯源契约。

推荐故事线（唯一花 token 的一步，可选）：--recommend 把机械摘要喂给一次
claude -p，要 3-5 条故事线推荐，渲染成 steps（每条点亮相关模块卡）。

用法:
  python3 preview.py <包根目录> [--out preview.json] [--top 12]
      [--recommend [CLI]]    默认 "claude -p"
兼容 Python 3.8。
"""
import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import parse_functions  # noqa: E402

ENTRY_NAMES = {"main", "run", "serve", "start", "execute", "step", "schedule",
               "forward", "generate", "__call__", "handle", "process", "loop"}
# 通用容器/字符串动词：按名字匹配的扇入统计会把 list.append 之类算进来，
# 既污染「值得拿出来」的挑选也污染模块间 ×N 计数——整体排除出名字匹配
GENERIC_NAMES = {"append", "pop", "set", "get", "cat", "join", "extend", "add",
                 "update", "remove", "clear", "copy", "items", "keys", "values",
                 "put", "close", "open", "read", "write", "send", "recv", "next",
                 "count", "index", "sort", "split", "strip", "format", "info"}


def discover_modules(root):
    """顶层子目录（含 .py 的）为模块；根下散装 .py 归入 <root 名> 模块。"""
    mods = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith((".", "_")) \
                and child.name not in ("tests", "test", "__pycache__", "node_modules", "docs", "examples", "benchmarks"):
            files = [f for f in child.rglob("*.py")
                     if "__pycache__" not in f.parts and "test" not in f.name]
            if files:
                mods[child.name] = files
    loose = [f for f in root.glob("*.py") if not f.name.startswith("_")]
    if loose:
        mods["(根)"] = loose
    return mods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default="preview.json")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--recommend", nargs="?", const="claude -p", default=None)
    ns = ap.parse_args()
    root = Path(ns.root).resolve()

    mods = discover_modules(root)
    # 解析全部函数（extract 的解析器，毫秒级/文件）
    mod_funcs, mod_loc = {}, {}
    for name, files in mods.items():
        fl, loc = [], 0
        for f in files:
            fl += parse_functions(f, root)
            try:
                loc += sum(1 for _ in f.open(errors="replace"))
            except OSError:
                pass
        mod_funcs[name], mod_loc[name] = fl, loc

    # 只留最大的 top 个模块，其余归并说明
    keep = sorted(mods, key=lambda m: -mod_loc[m])[: ns.top]
    dropped = [m for m in mods if m not in keep]

    # 全局函数名 → 模块（唯一命名才算，歧义不算——线是断言）
    owner = {}
    for m in keep:
        for f in mod_funcs[m]:
            owner.setdefault(f["name"], set()).add(m)
    unique_owner = {n: list(ms)[0] for n, ms in owner.items()
                    if len(ms) == 1 and n not in GENERIC_NAMES}

    # 扇入统计 + 模块间调用聚合
    import ast
    fan_in = defaultdict(int)
    mod_calls = defaultdict(int)
    for m in keep:
        for f in mod_funcs[m]:
            for node in ast.walk(f["node"]):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                tail = callee.id if isinstance(callee, ast.Name) else (
                    callee.attr if isinstance(callee, ast.Attribute) else None)
                tgt = unique_owner.get(tail)
                if tgt:
                    fan_in[tail] += 1
                    if tgt != m:
                        mod_calls[(m, tgt)] += 1

    def slug(s):
        return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_") or "root"

    # 每模块挑「值得拿出来」的签名行
    cards = []
    for m in keep:
        def score(f):
            s = fan_in.get(f["name"], 0) * 3
            if f["name"] in ENTRY_NAMES:
                s += 8
            if f["cls"] is None:
                s += 2          # 顶层函数优先于深处方法
            s += min(f["end"] - f["start"], 40) / 40
            s -= max(len(Path(f["file"]).parts) - 3, 0) * 4  # 深埋的厂商/kernel 目录降权
            return -s
        picked, seen_names = [], set()
        for f in sorted(mod_funcs[m], key=score):
            if f["name"] in seen_names or f["name"].startswith("__") \
                    or f["name"] in GENERIC_NAMES:
                continue
            seen_names.add(f["name"])
            picked.append(f)
            if len(picked) >= 6:
                break
        lines, by_file = [], defaultdict(list)
        for f in picked:
            by_file[f["file"]].append(f)
        for fp in sorted(by_file):
            lines.append("# ── {}".format(fp))
            for f in sorted(by_file[fp], key=lambda x: x["start"]):
                sig = f["code"].split("\n")[len(f["node"].decorator_list)].strip()
                lines.append(("  " + sig)[:96])
        nfiles = len(mods[m])
        cards.append({
            "id": slug(m),
            "name": "{}/ · {} 文件 · {:,} 行".format(m, nfiles, mod_loc[m]),
            "file": str(Path(root.name) / m),
            "lang": "py", "collapsed": len(cards) >= 4,
            "code": "\n".join(lines) or "# （无签名可展示）",
            "_mod": m,
        })

    id_of = {c["_mod"]: c["id"] for c in cards}
    wires = []
    for (a, b), n in sorted(mod_calls.items(), key=lambda kv: -kv[1])[:18]:
        wires.append({"id": "w-{}-{}".format(id_of[a], id_of[b]), "kind": "call",
                      "label": "×{}".format(n) if n > 1 else None,
                      "from": {"card": id_of[a]}, "to": {"card": id_of[b]}})
        if wires[-1]["label"] is None:
            del wires[-1]["label"]

    # 布局：按调用图 BFS
    ids = [c["id"] for c in cards]
    callers = {i: set() for i in ids}
    for w in wires:
        callers[w["to"]["card"]].add(w["from"]["card"])
    col = {}
    frontier = [i for i in ids if not callers[i]] or ids[:1]
    for i in frontier:
        col[i] = 0
    depth = 0
    while frontier and depth < 4:
        depth += 1
        nxt = []
        for w in wires:
            if w["from"]["card"] in frontier and w["to"]["card"] not in col:
                col[w["to"]["card"]] = depth
                nxt.append(w["to"]["card"])
        frontier = nxt
    per_col = defaultdict(int)
    for c in cards:
        k = col.get(c["id"], 4)
        c["layout"] = {"col": k, "band": per_col[k]}
        per_col[k] += 1
        del c["_mod"]

    total_loc = sum(mod_loc.values())
    doc = {
        "meta": {"title": "{} 预览图".format(root.name),
                 "subtitle": "秒级机械预览 · {} 模块 {:,} 行 · 签名是真实源码行".format(len(cards), total_loc),
                 "mode": "preview"},
        "regions": [], "cards": cards, "wires": wires,
        "notes": [{"id": "bg0", "flavor": "bg", "tag": "预览图 · 怎么读",
                   "text": "卡片=顶层模块，卡内是按扇入/入口挑的签名行（真实源码）。这是地图的地图：先有概念，再从推荐故事线点单深潜。"
                           + ("未展示 {} 个小模块。".format(len(dropped)) if dropped else ""),
                   "place": {"corner": "nw"}, "step": 0}],
        "steps": [{"title": "总览 · 这个仓库分几块",
                   "caption": "{} 个模块按调用方向排开，线上 ×N 是调用聚合数。按 ▶ 看推荐的故事线。".format(len(cards)),
                   "fit": True}],
    }

    if ns.recommend:
        digest = "\n".join(
            "[{}] {}\n{}".format(c["id"], c["name"], c["code"]) for c in cards)
        prompt = (
            "下面是一个 Python 仓库的机械预览摘要（模块 + 关键签名）。你是资深工程师，"
            "为第一次接触它的人推荐 3-5 条「值得深入的故事线」。只输出 JSON 数组，每项："
            '{"title":"≤14字","pitch":"≤70字，这条线讲什么、为什么值得","modules":["模块id",…],'
            '"ask":"≤50字，交给画布生成器的一句主题"}。modules 只能用摘要里的 id。\n\n' + digest)
        r = subprocess.run(ns.recommend.split() + [prompt], capture_output=True, text=True, timeout=300)
        m = re.search(r"\[.*\]", r.stdout, re.S)
        recs = json.loads(m.group(0)) if m else []
        marks = "①②③④⑤"
        for i, rec in enumerate(recs[:5]):
            mods_ok = [x for x in rec.get("modules", []) if x in id_of.values()]
            # 只点亮 focus 模块之间的聚合线，其余淡出——推荐步不许出毛线球
            lit = [w["id"] for w in wires
                   if w["from"]["card"] in mods_ok and w["to"]["card"] in mods_ok][:5]
            doc["steps"].append({
                "title": "{} {}".format(marks[i], rec["title"]),
                "caption": rec["pitch"],
                "focus": mods_ok or None,
                "expand": mods_ok[:2],
                "wires": lit,
                "ask": rec.get("ask", ""),
            })
            if not mods_ok:
                doc["steps"][-1].pop("focus")
        print("推荐故事线 {} 条".format(len(recs[:5])), file=sys.stderr)

    Path(ns.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote {}  ({} cards, {} wires, {} steps)".format(
        ns.out, len(cards), len(wires), len(doc["steps"])), file=sys.stderr)


if __name__ == "__main__":
    main()
