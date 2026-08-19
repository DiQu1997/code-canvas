#!/usr/bin/env python3
"""validate.py — machine-check a canvas JSON before rendering.

Errors are structural breakages the renderer cannot survive gracefully
(dangling references, out-of-range lines, malformed block trees).
Warnings are curation-budget violations from schema.md's hard limits.

Usage: python3 validate.py canvas.json [--strict]   (--strict: warnings fail too)
"""
import json
import re
import sys
from pathlib import Path

E, W = [], []


def err(msg): E.append(msg)
def warn(msg): W.append(msg)


def check_blocks(blocks, lo, hi, path, nlines):
    prev_end = lo - 1
    for b in sorted(blocks or [], key=lambda x: x.get("lines", [0])[0]):
        name = b.get("name") or "<unnamed>"
        p = f"{path} › {name}"
        lines = b.get("lines")
        if not name or not isinstance(lines, list) or len(lines) != 2:
            err(f"{p}: 块缺 name 或 lines=[start,end]")
            continue
        s, e = lines
        if not (1 <= s <= e <= nlines):
            err(f"{p}: 行段 [{s},{e}] 超出代码范围 1–{nlines}")
        if s <= prev_end:
            err(f"{p}: 与前一个同级块重叠（起于 {s}，上一块止于 {prev_end}）")
        if not (lo <= s and e <= hi):
            err(f"{p}: 行段 [{s},{e}] 越出父块范围 [{lo},{hi}]")
        prev_end = e
        if len(b.get("summary") or "") > 24:
            warn(f"{p}: summary 超 20 字（{len(b['summary'])}）")
        if len(b.get("explain") or "") > 140:
            warn(f"{p}: explain 超 120 字（{len(b['explain'])}）")
        check_blocks(b.get("children"), s, e, p, nlines)


def block_names(blocks):
    for b in blocks or []:
        yield b.get("name")
        yield from block_names(b.get("children"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__.strip())
    d = json.loads(Path(args[0]).read_text(encoding="utf-8"))

    cards = {c.get("id"): c for c in d.get("cards", [])}
    if not cards:
        err("没有任何卡片")
    if len(cards) != len(d.get("cards", [])):
        err("卡片 id 重复")
    regions = {r.get("id"): r for r in d.get("regions", [])}
    mode = (d.get("meta") or {}).get("mode") or ""

    seen_in_region = {}
    for r in d.get("regions", []):
        for cid in r.get("cards", []):
            if cid not in cards:
                err(f"region {r.get('id')}: 成员卡 {cid} 不存在")
            elif cid in seen_in_region:
                err(f"卡 {cid} 同时属于 region {seen_in_region[cid]} 和 {r.get('id')}")
            seen_in_region[cid] = r.get("id")

    for cid, c in cards.items():
        code = c.get("code") or ""
        lines = code.split("\n")
        n = len(lines)
        if not code and c.get("kind") != "district":
            err(f"卡 {cid}: 没有代码")
        if "layout" not in c or "col" not in c["layout"] or "band" not in c["layout"]:
            err(f"卡 {cid}: 缺 layout.col / layout.band")
        for i, t in enumerate(lines):
            if len(t) > 100:
                warn(f"卡 {cid} 第 {i+1} 行长 {len(t)} 字符（>100，卡片会到测量上限被截断）")
        # plan / plan_delta：模式绑定字段
        if "plan" in c:
            if mode != "plan":
                err(f"卡 {cid}: plan 字段只在 meta.mode:\"plan\" 里合法（当前 mode={mode or '无'}）")
            elif c["plan"] not in ("add", "modify", "remove"):
                err(f"卡 {cid}: plan 必须是 add | modify | remove")
            if c.get("diff"):
                err(f"卡 {cid}: 计划画布的卡不能有 diff（计划尚未发生）")
        if "plan_delta" in c:
            if mode != "diff":
                err(f"卡 {cid}: plan_delta 只在 mode:\"diff\" 里合法")
            elif c["plan_delta"] not in ("offplan", "missed"):
                err(f"卡 {cid}: plan_delta 必须是 offplan | missed")
        diff = c.get("diff") or {}
        for ln in diff.get("added") or []:
            if not (isinstance(ln, int) and 1 <= ln <= n):
                err(f"卡 {cid}: diff.added 行 {ln} 越界（共 {n} 行）")
        for j, r in enumerate(diff.get("removed") or []):
            a = r.get("after")
            if not (isinstance(a, int) and 0 <= a <= n):
                err(f"卡 {cid}: diff.removed[{j}].after {a} 越界（0–{n}）")
            if not r.get("code"):
                warn(f"卡 {cid}: diff.removed[{j}] 缺被删原文 code")
        check_blocks(c.get("blocks"), 1, n, f"卡 {cid}", n)
        for j, tm in enumerate(c.get("terms") or []):
            ln, tok = tm.get("line"), tm.get("token") or ""
            if not (isinstance(ln, int) and 1 <= ln <= n):
                err(f"卡 {cid} terms[{j}]: 行号 {ln} 越界")
            elif not re.search(r"\b" + re.escape(tok) + r"\b", lines[ln - 1]):
                err(f"卡 {cid} terms[{j}]: 第 {ln} 行找不到整词 “{tok}”")
            if len(tm.get("note") or "") > 70:
                warn(f"卡 {cid} terms[{j}] ({tok}): note 超 60 字（{len(tm['note'])}）")
        n_intent = 0  # counted below via notes
    cols = {}
    for c in cards.values():
        cols.setdefault(c.get("layout", {}).get("col"), []).append(c["id"])
    for k, ids in cols.items():
        if len(ids) > 3:
            warn(f"第 {k} 列有 {len(ids)} 张卡（>3）: {', '.join(ids)}")

    wire_ids = set()
    for w in d.get("wires", []):
        wid = w.get("id") or "<no-id>"
        if wid in wire_ids:
            err(f"线 {wid}: id 重复")
        wire_ids.add(wid)
        if w.get("kind") not in ("call", "data", "route"):
            err(f"线 {wid}: kind 必须是 call | data | route（route=预览图故事线路）")
        for end in ("from", "to"):
            spec = w.get(end) or {}
            cid = spec.get("card")
            if cid not in cards:
                err(f"线 {wid}: {end}.card {cid} 不存在")
            elif "line" in spec:
                n = len(cards[cid]["code"].split("\n"))
                if not (1 <= spec["line"] <= n):
                    err(f"线 {wid}: {end}.line {spec['line']} 越界（卡 {cid} 共 {n} 行）")
        if w.get("kind") == "data" and not w.get("label"):
            warn(f"线 {wid}: data 线建议带值名 label")
        if "plan" in w:
            if mode != "plan":
                err(f"线 {wid}: plan 字段只在 meta.mode:\"plan\" 里合法")
            elif w["plan"] not in ("add", "remove"):
                err(f"线 {wid}: plan 必须是 add | remove")

    if mode == "plan" and not any("plan" in c for c in cards.values()) \
            and not any("plan" in w for w in d.get("wires", [])):
        warn("mode:\"plan\" 但没有任何 plan 标记——这张计划画布没有计划任何变更")

    note_ids = set()
    intent_per_card = {}
    for nt in d.get("notes", []):
        nid = nt.get("id") or "<no-id>"
        note_ids.add(nid)
        if nt.get("flavor") not in ("intent", "bg"):
            err(f"note {nid}: flavor 必须是 intent | bg")
        a = nt.get("anchor")
        if a:
            cid = a.get("card")
            if cid not in cards:
                err(f"note {nid}: anchor.card {cid} 不存在")
            else:
                n = len(cards[cid]["code"].split("\n"))
                if not (1 <= a.get("line", 0) <= n):
                    err(f"note {nid}: anchor.line {a.get('line')} 越界")
            if nt.get("flavor") == "intent":
                intent_per_card[cid] = intent_per_card.get(cid, 0) + 1
        p = nt.get("place") or {}
        if p.get("of") and p["of"] not in cards:
            err(f"note {nid}: place.of {p['of']} 不存在")
        limit = 90 if (nt.get("flavor") == "bg" and p.get("corner")) else 70
        if len(nt.get("text") or "") > limit:
            warn(f"note {nid}: 文字超限（{len(nt['text'])} 字，上限约 {limit-10}）")
    for cid, k in intent_per_card.items():
        if k > 3:
            warn(f"卡 {cid} 有 {k} 条 intent note（>3）")

    steps = d.get("steps", [])
    for i, s in enumerate(steps):
        sp = f"step[{i}] {s.get('title','')}"
        if s.get("storyline") and s["storyline"] not in regions:
            err(f"{sp}: storyline {s['storyline']} 不存在")
        for wid in s.get("wires") or []:
            if wid not in wire_ids:
                err(f"{sp}: 线 {wid} 不存在")
        if len(s.get("wires") or []) > 5:
            warn(f"{sp}: 点亮 {len(s['wires'])} 根线（>5）")
        if len(s.get("lines") or []) > 6:
            warn(f"{sp}: 高亮 {len(s['lines'])} 行（>6）")
        for ref in s.get("lines") or []:
            cid, ln = ref
            if cid not in cards:
                err(f"{sp}: lines 引用卡 {cid} 不存在")
            elif not (1 <= ln <= len(cards[cid]["code"].split("\n"))):
                err(f"{sp}: lines [{cid},{ln}] 行号越界")
        for cid in s.get("expand") or []:
            if cid not in cards:
                err(f"{sp}: expand 卡 {cid} 不存在")
        for cid, bname in s.get("unfold") or []:
            if cid not in cards:
                err(f"{sp}: unfold 卡 {cid} 不存在")
            elif bname not in set(block_names(cards[cid].get("blocks"))):
                err(f"{sp}: 卡 {cid} 没有名为 “{bname}” 的块")
        for fid in s.get("focus") or []:
            if fid not in cards and fid not in note_ids:
                err(f"{sp}: focus {fid} 既不是卡也不是 note")
        if len(s.get("caption") or "") > 95:
            warn(f"{sp}: caption 超 80 字（{len(s['caption'])}）")
        if len(s.get("detail") or "") > 320:
            warn(f"{sp}: detail 超 300 字（{len(s['detail'])}）")
        for ref in s.get("order") or []:
            if ref not in cards and ref not in note_ids:
                err(f"{sp}: order 引用 {ref} 既不是卡也不是 note")

    for m in E:
        print(f"ERROR  {m}")
    for m in W:
        print(f"warn   {m}")
    print(f"\n{len(E)} errors, {len(W)} warnings")
    sys.exit(1 if E or (strict and W) else 0)


if __name__ == "__main__":
    main()
