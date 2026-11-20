#!/usr/bin/env python3
"""Build a zoom canvas: spec JSON (+ repo) -> self-contained HTML.

Usage: python3 build_zoom.py zoom.json --repo /path/to/repo [--out zoom.html] [--check]

The spec references code by file + line ranges; this script reads the repo, fills
cards[].code with the real lines (dedented, tabs expanded, ⋯ between ranges),
validates every cross-reference, and injects the data into template/zoom.html.
Exit code 1 if any ERROR. See zoom-spec.md for the format.
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg_extract  # noqa: E402
TEMPLATE = HERE / "template" / "zoom.html"
PRIMS = {"cfg", "loop", "pipeline", "dispatch", "pool", "statemachine", "flow", "tree", "layers", "grid", "structs"}
SHAPES = {"queue", "list", "stack", "map", "table", "set", "pool", "ring", "wheel", "tree", "bitmap", "array", "lock", "flag", "scalar"}
KINDS = {"thread", "queue", "component", "store", "external", "loop", "api", "state"}
CH_KINDS = {"flow", "call", "queue", "return", "async", "control", "data", "error"}


class Report:
    def __init__(self):
        self.err, self.warn = [], []

    def e(self, m): self.err.append(m)
    def w(self, m): self.warn.append(m)


def card_ranges(c):
    if c.get("ranges"):
        return [tuple(r) for r in c["ranges"]]
    if c.get("lines"):
        return [tuple(c["lines"])]
    return []


def fill_code(spec, repo, R):
    cache = {}
    for c in spec.get("cards", []):
        cid = c.get("id", "?")
        f = c.get("file")
        rs = card_ranges(c)
        if not f or not rs:
            R.e(f"card {cid}: needs file and lines/ranges")
            continue
        p = (repo / f) if repo else None
        if p is None or not p.is_file():
            if c.get("code"):
                continue
            R.e(f"card {cid}: file not found: {f}")
            continue
        if f not in cache:
            cache[f] = p.read_text(encoding="utf-8", errors="replace").split("\n")
        src = cache[f]
        out = []
        for i, (a, b) in enumerate(rs):
            if a < 1 or b > len(src) or a > b:
                R.e(f"card {cid}: range {a}-{b} outside {f} (1-{len(src)})")
                continue
            if i:
                out.append([0, "⋯"])
            out += [[n, src[n - 1].replace("\t", "    ").rstrip()] for n in range(a, b + 1)]
        body = [t for n, t in out if n and t.strip()]
        ind = min((len(t) - len(t.lstrip()) for t in body), default=0)
        c["code"] = [[n, t[ind:] if n else t] for n, t in out]
        total = sum(1 for n, _ in c["code"] if n)
        if total > 90:
            R.e(f"card {cid}: {total} lines (max 90) — split it or use ranges to elide")
        elif total > 70:
            R.w(f"card {cid}: {total} lines — long; consider eliding")
        wide = sum(1 for n, t in c["code"] if len(t) > 130)
        if wide:
            R.w(f"card {cid}: {wide} lines >130 cols get truncated")
        # signature sanity: the function/class name in the title should appear in the code
        nm = c.get("name") or re.sub(r"\(.*$", "", str(c.get("title", ""))).split(".")[-1].split("::")[-1].strip()
        if nm and len(nm) > 2 and not any(nm in t for _, t in c["code"]):
            R.w(f"card {cid}: name '{nm}' not found in its lines {rs} — wrong range?")


def derive_cfg(spec, R):
    """cfg modules: attach a mechanically derived control tree to every card their items point at."""
    cards = {c.get("id"): c for c in spec.get("cards") or []}
    names = {}
    for c in cards.values():
        nm = c.get("name") or re.sub(r"\(.*$", "", str(c.get("title", ""))).split(".")[-1].split("::")[-1].strip()
        if nm:
            names[nm] = c.get("id")
    for m in spec.get("modules") or []:
        A = m.get("anatomy") or {}
        if A.get("primitive") != "cfg":
            continue
        for it in A.get("items") or []:
            cs = it.get("card")
            cs = cs if isinstance(cs, list) else [cs] if cs else []
            if len(cs) != 1 or cs[0] not in cards:
                R.e(f"module {m.get('id')}/{it.get('id')}: a cfg item needs exactly one card (the function it draws)")
                continue
            c = cards[cs[0]]
            if "cfg" in c:
                continue
            tree, ok = cfg_extract.card_cfg(c, names)
            c["cfg"] = tree
            if not ok:
                R.w(f"card {c.get('id')}: cfg structure is only derived for Python — drawn as its blocks in order")
            elif not tree:
                R.w(f"card {c.get('id')}: no statements found for the cfg skeleton")


def validate(spec, R):
    meta = spec.get("meta") or {}
    if meta.get("mode") != "zoom":
        R.w("meta.mode should be 'zoom'")
    j = meta.get("judgment")
    if not j:
        R.w("meta.judgment missing (scope/shape/protagonist/parts/cut)")
    mods = spec.get("modules") or []
    if not mods:
        R.e("no modules")
    ids = [m.get("id") for m in mods]
    for d in {i for i in ids if ids.count(i) > 1}:
        R.e(f"duplicate module id {d}")
    MOD = {m.get("id"): m for m in mods}
    cards = {c.get("id"): c for c in spec.get("cards") or []}
    used = {}

    def use(cid, where):
        for x in (cid if isinstance(cid, list) else [cid]):
            if x not in cards:
                R.e(f"{where}: card '{x}' not defined")
            elif x in used:
                R.e(f"card '{x}' attached twice ({used[x]} and {where})")
            else:
                used[x] = where

    boxes = []
    lay = spec.get("layout") or {}
    CW, RH, G = lay.get("colw", 330), lay.get("rowh", 260), lay.get("gap", 44)
    for m in mods:
        mid = m.get("id")
        if m.get("kind") and m["kind"] not in KINDS:
            R.w(f"module {mid}: unknown kind {m['kind']}")
        pos = m.get("pos")
        if not (isinstance(pos, list) and len(pos) == 2):
            R.e(f"module {mid}: pos [col,row] required")
            continue
        ext = m.get("kind") == "external" or not m.get("anatomy")
        sw, sh = m.get("span") or ([.55, .5] if ext else [1, 1])
        boxes.append((mid, pos[0] * CW, pos[1] * RH, sw * CW - G, sh * RH - G))
        if len(str(m.get("title", ""))) > 26:
            R.w(f"module {mid}: title long ({len(m['title'])} chars)")
        if len(str(m.get("oneliner", ""))) > 44 and not ext:
            R.w(f"module {mid}: oneliner >44 chars gets cut at L0")
        if m.get("card"):
            use(m["card"], f"module {mid}")
        A = m.get("anatomy")
        if not A:
            continue
        if A.get("primitive") not in PRIMS:
            R.e(f"module {mid}: anatomy.primitive must be one of {sorted(PRIMS)}")
        its = A.get("items") or []
        anchors = {"center"} if A.get("center") else set()
        for it in its:
            if not it.get("id") or not it.get("label"):
                R.e(f"module {mid}: item needs id+label: {it}")
            anchors.add(it.get("id"))
            if len(str(it.get("label", ""))) > 22:
                R.w(f"module {mid}/{it.get('id')}: label long")
            if it.get("card"):
                use(it["card"], f"{mid}/{it['id']}")
        if A.get("center") and A["center"].get("card"):
            if A.get("primitive") not in ("loop", "dispatch", "pool"):
                R.w(f"module {mid}: center only drawn for loop/dispatch/pool")
            use(A["center"]["card"], f"{mid}/center")
        for f in A.get("fields") or []:
            anchors.add(f.get("name"))
            if f.get("shape") and f["shape"] not in SHAPES:
                R.w(f"module {mid}: field {f.get('name')} unknown shape {f['shape']}")
            if f.get("card"):
                use(f["card"], f"{mid}/field {f.get('name')}")
        for e in A.get("edges") or []:
            a, b = (e[0], e[1]) if isinstance(e, list) else (e.get("from"), e.get("to"))
            if a not in anchors or b not in anchors:
                R.e(f"module {mid}: edge {a}->{b} references unknown item/field")
        n = len(its)
        if A.get("primitive") == "loop" and n > 9:
            R.w(f"module {mid}: loop with {n} stations is crowded (≤8)")
        if n > 12:
            R.w(f"module {mid}: {n} items — consider a sub-split")
        if not its and not A.get("fields"):
            R.e(f"module {mid}: anatomy has no items or fields")
    for i in range(len(boxes)):
        for k in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[k]
            if a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]:
                R.e(f"modules {a[0]} and {b[0]} overlap — fix pos/span")
    for c in spec.get("channels") or []:
        if c.get("from") not in MOD or c.get("to") not in MOD:
            R.e(f"channel {c.get('from')}->{c.get('to')}: unknown module")
        if c.get("kind") and c["kind"] not in CH_KINDS:
            R.w(f"channel {c.get('from')}->{c.get('to')}: unknown kind {c['kind']}")
        if len(str(c.get("label", ""))) > 24:
            R.w(f"channel {c.get('from')}->{c.get('to')}: label long")
    for g in spec.get("groups") or []:
        for x in g.get("modules") or []:
            if x not in MOD:
                R.e(f"group {g.get('title')}: unknown module {x}")
    for cid, c in cards.items():
        if cid not in used:
            R.e(f"card '{cid}' not attached to any module/item/field (unreachable)")
        else:
            c["module_hint"] = used[cid]
        lines = {n for n, _ in c.get("code") or [] if n}
        if lines:
            covered = set()
            for bk in c.get("blocks") or []:
                a, b = (bk[0], bk[1]) if isinstance(bk, list) else bk["lines"]
                if a not in lines or b not in lines:
                    R.e(f"card {cid}: block {a}-{b} not inside card lines")
                covered |= {n for n in lines if a <= n <= b}
            if not c.get("blocks"):
                R.e(f"card {cid}: no blocks (summaries are the function level)")
            elif len(covered) < .6 * len(lines):
                R.w(f"card {cid}: blocks cover {len(covered)}/{len(lines)} lines")
        # ---- pseudo-code: steps + omitted must cover every code line of the card ----
        steps = c.get("steps")
        if steps is not None:
            SK = {"step", "decision", "loop", "write", "wait", "return", "call"}
            txt = {n: t for n, t in c.get("code") or [] if n}
            cov = set()
            if not (3 <= len(steps) <= 26):
                R.w(f"card {cid}: {len(steps)} pseudo-code steps (aim 4-20)")
            last = 0
            for i, st in enumerate(steps):
                sw = f"card {cid} step {i + 1}"
                if not str(st.get("title") or "").strip():
                    R.e(f"{sw}: title missing")
                elif len(st["title"]) > 64:
                    R.w(f"{sw}: title {len(st['title'])} chars (max ~56): {st['title']}")
                elif re.search(r"[\u4e00-\u9fff]", st["title"] + str(st.get("note") or "")):
                    R.w(f"{sw}: pseudo-code is written in English: {st['title']}")
                if st.get("kind") and st["kind"] not in SK:
                    R.e(f"{sw}: kind must be one of {sorted(SK)}")
                if st.get("call") and st["call"] not in cards:
                    R.e(f"{sw}: call '{st['call']}' is not a card id")
                sl = st.get("lines")
                if not (isinstance(sl, list) and len(sl) == 2 and sl[0] <= sl[1]):
                    R.e(f"{sw}: lines [a,b] required"); continue
                if sl[0] not in txt or sl[1] not in txt:
                    R.e(f"{sw}: lines {sl} not inside the card's lines"); continue
                if sl[0] < last:
                    R.w(f"{sw}: lines {sl} go backwards (steps should follow the source order)")
                last = sl[0]
                cov |= set(range(sl[0], sl[1] + 1))
            for o in c.get("omitted") or []:
                if not (isinstance(o, list) and len(o) >= 3 and str(o[2]).strip()):
                    R.e(f"card {cid}: omitted entries are [from, to, reason]"); continue
                cov |= set(range(o[0], o[1] + 1))
            def is_code(t):
                t = t.strip()
                return bool(t) and not t.startswith(("#", "//", "/*", "*", '"""', "'''")) and bool(re.search(r"[A-Za-z0-9]", t))
            missing = [n for n in sorted(txt) if is_code(txt[n]) and n not in cov]
            if missing:
                runs, a0 = [], None
                for n in missing:
                    if a0 is None: a0 = b0 = n
                    elif n == b0 + 1: b0 = n
                    else: runs.append((a0, b0)); a0 = b0 = n
                runs.append((a0, b0))
                R.e(f"card {cid}: {len(missing)} code lines not covered by any step or omitted: " + ", ".join(f"{x}" if x == y else f"{x}-{y}" for x, y in runs))
    stories = spec.get("stories") or ([spec["story"]] if spec.get("story") else [])
    if not stories and not spec.get("scene") and not spec.get("_has_scene"):
        R.w("no story — a protagonist walk is what ties the levels together")
    for s in stories:
        for i, st in enumerate(s.get("steps") or []):
            at = st.get("at") or {}
            where = f"story '{s.get('title', '')}' step {i + 1}"
            if not st.get("text"):
                R.e(f"{where}: text missing")
            if "card" in at:
                c = cards.get(at["card"])
                if not c:
                    R.e(f"{where}: unknown card {at['card']}")
                    continue
                lines = {n for n, _ in c.get("code") or [] if n}
                for key in ("line", "line2"):
                    if key in at and lines and at[key] not in lines:
                        R.e(f"{where}: {key} {at[key]} not in card {at['card']}")
            elif "item" in at:
                m, _, it = str(at["item"]).partition("/")
                A = (MOD.get(m) or {}).get("anatomy") or {}
                names = {x.get("id") for x in A.get("items") or []} | {x.get("name") for x in A.get("fields") or []} | ({"center"} if A.get("center") else set())
                if it not in names:
                    R.e(f"{where}: unknown item {at['item']}")
            elif "module" in at:
                if at["module"] not in MOD:
                    R.e(f"{where}: unknown module {at['module']}")
            else:
                R.e(f"{where}: at needs card/item/module")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--dump", type=Path, help="write the filled spec (code included) here; default <out stem>.data.json")
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    R = Report()
    # an animated scene (scene.json beside the spec, or spec["scene"]) becomes the top level of the canvas
    sib = a.spec.with_name("scene.json")
    if "scene" not in spec and sib.exists():
        spec["scene"] = json.loads(sib.read_text(encoding="utf-8"))
    fill_code(spec, a.repo, R)
    validate(spec, R)
    derive_cfg(spec, R)
    sc = spec.get("scene")
    if sc:
        mods = {m["id"] for m in spec.get("modules") or []}
        cards = {c["id"]: c for c in spec.get("cards") or []}
        ents = {e["id"] for e in sc.get("entities") or []}
        if not any(e.get("module") for e in sc.get("entities") or []):
            R.e("scene: no entity has a module — nothing to zoom into")
        for e in sc.get("entities") or []:
            if e.get("module") and e["module"] not in mods:
                R.e(f"scene entity {e['id']}: unknown module {e['module']}")
        for w in sc.get("wires") or []:
            for end in (w["a"], w["b"]):
                if end[0] not in ents:
                    R.e(f"scene wire {w['id']}: unknown entity {end[0]}")
        for i, st in enumerate(sc.get("steps") or []):
            at = st.get("at") or {}
            if at.get("card"):
                c = cards.get(at["card"])
                if not c:
                    R.e(f"scene step {i + 1}: unknown card {at['card']}")
                elif at.get("line") not in {n for n, _ in c.get("code") or [] if n}:
                    R.e(f"scene step {i + 1}: line {at.get('line')} not in card {at['card']}")
            for f in st.get("focus") or []:
                if f not in ents:
                    R.e(f"scene step {i + 1}: focus on unknown entity {f}")
        # every op must refer to things that exist, in an order that makes sense (replay the scene)
        ET = {"thread", "ext", "queue", "store", "log", "grid", "bar", "group", "tree", "fn"}
        E = {e["id"]: e for e in sc.get("entities") or []}
        for e in E.values():
            if e.get("type") not in ET:
                R.e(f"scene entity {e['id']}: type must be one of {sorted(ET)}")
            for k in ("x", "y", "w", "h"):
                if not isinstance(e.get(k), (int, float)):
                    R.e(f"scene entity {e['id']}: {k} missing")
            if e.get("type") == "grid" and not (e.get("cols") and e.get("rowsN")):
                R.e(f"scene entity {e['id']}: grid needs cols and rowsN")
            if e.get("type") == "log" and not (e.get("range") and e.get("markers")):
                R.e(f"scene entity {e['id']}: log needs range and markers")
            if e.get("type") == "bar" and not e.get("total"):
                R.e(f"scene entity {e['id']}: bar needs total")
            if e.get("segVars") is not None:
                sv = e["segVars"]
                if e.get("type") != "bar":
                    R.e(f"scene entity {e['id']}: segVars only applies to a bar")
                elif not (isinstance(sv, list) and sv and all(isinstance(q, list) and len(q) == 2 for q in sv)):
                    R.e(f"scene entity {e['id']}: segVars must be a list of [var, color] pairs")
                else:
                    for q in sv:
                        if q[0] not in (e.get("vars") or {}):
                            R.e(f"scene entity {e['id']}: segVars names {q[0]}, which is not one of its vars")
            if e.get("type") == "store" and not e.get("rows"):
                R.e(f"scene entity {e['id']}: store needs rows")
            if e.get("type") == "tree":
                seen = set()
                for nd in e.get("nodes") or []:
                    if not nd.get("id"): R.e(f"scene entity {e['id']}: a tree node has no id")
                    if nd.get("parent") and nd["parent"] not in seen: R.e(f"scene entity {e['id']}: node {nd.get('id')} listed before its parent {nd['parent']}")
                    if nd.get("val") is not None and len(nd["val"]) != len(nd.get("key") or []): R.e(f"scene entity {e['id']}: node {nd.get('id')} has {len(nd.get('key') or [])} key cells but {len(nd['val'])} value cells")
                    for sid in nd.get("sets") or []:
                        if sid not in {x["id"] for x in e.get("sets") or []}: R.e(f"scene entity {e['id']}: node {nd.get('id')} is in unknown set {sid}")
                    seen.add(nd.get("id"))
                if not e.get("nodes"): R.e(f"scene entity {e['id']}: tree needs nodes (at least the root)")
                elif sum(1 for nd in e["nodes"] if not nd.get("parent")) != 1: R.e(f"scene entity {e['id']}: tree needs exactly one root (a node without parent)")
        boxes = [e for e in E.values() if e.get("type") != "group" and all(isinstance(e.get(k), (int, float)) for k in "xywh")]
        for i, p in enumerate(boxes):
            for q in boxes[i + 1:]:
                if p["x"] < q["x"] + q["w"] and q["x"] < p["x"] + p["w"] and p["y"] < q["y"] + q["h"] and q["y"] < p["y"] + p["h"]:
                    R.e(f"scene: entities {p['id']} and {q['id']} overlap")
        W = {w["id"] for w in sc.get("wires") or []}
        # phone layout: the same entities re-arranged for a narrow screen; it must hold together on its own
        ph = sc.get("phone")
        if ph is not None:
            num = lambda v: isinstance(v, (int, float))
            pe, stacked = {}, {}
            for k, ov in (ph.get("entities") or {}).items():
                if k not in E: R.e(f"scene phone.entities: unknown entity {k}"); continue
                if "cols" in ov or "rowsN" in ov or "type" in ov or "id" in ov: R.e(f"scene phone.entities.{k}: only placement/looks may change (not id/type/cols/rowsN)")
                pe[k] = dict(E[k], **ov)
            for k, e0 in E.items(): pe.setdefault(k, dict(e0))
            for i, sk in enumerate(ph.get("stacks") or []):
                ids = sk.get("ids") or []
                if len(ids) < 2: R.e(f"scene phone.stacks[{i}]: a stack needs at least 2 ids")
                if not all(num(sk.get(k)) for k in "xywh"): R.e(f"scene phone.stacks[{i}]: x/y/w/h missing")
                for q in ids:
                    if q not in E: R.e(f"scene phone.stacks[{i}]: unknown entity {q}")
                    elif q in stacked: R.e(f"scene phone.stacks: entity {q} is in two stacks")
                    else:
                        stacked[q] = i
                        pe[q].update({k: sk.get(k) for k in "xywh"})
            for k, ov in (ph.get("wires") or {}).items():
                if k not in W: R.e(f"scene phone.wires: unknown wire {k}"); continue
                for end in (ov.get("a"), ov.get("b")):
                    if end is not None and (not isinstance(end, list) or not end or end[0] not in E): R.e(f"scene phone.wires.{k}: bad anchor {end}")
            vis, seen = [], set()
            for k, e1 in pe.items():
                if e1.get("type") == "group" or e1.get("hide") or not all(num(e1.get(q)) for q in "xywh"): continue
                if k in stacked:
                    if stacked[k] in seen: continue
                    seen.add(stacked[k])
                vis.append(e1)
            for i, p1 in enumerate(vis):
                for p2 in vis[i + 1:]:
                    if p1["x"] < p2["x"] + p2["w"] and p2["x"] < p1["x"] + p1["w"] and p1["y"] < p2["y"] + p2["h"] and p2["y"] < p1["y"] + p1["h"]:
                        R.e(f"scene phone: entities {p1['id']} and {p2['id']} overlap")
            if vis:
                pw = max(v["x"] + v["w"] for v in vis) - min(v["x"] for v in vis)
                if pw > 380: R.w(f"scene phone: layout is {pw:.0f} wide — over ~380 it gets scaled down on a phone and text shrinks")
        toks, alive = sc.get("tokens") or {}, set()
        OPS = {"spawn", "move", "gone", "tok", "state", "var", "mark", "wire", "segs", "batch", "alloc", "free", "hash", "pulse", "flash", "run",
               "node", "split", "nset", "member", "del", "path", "probe", "ptr"}
        TREE_OPS = {"node", "split", "nset", "member", "del", "path", "probe", "ptr"}
        # tree entities are replayed too: which nodes exist right now, who their parent is, how many key cells they hold
        TN = {e["id"]: {nd["id"]: {"parent": nd.get("parent"), "n": len(nd.get("key") or [])} for nd in e.get("nodes") or [] if nd.get("id")}
              for e in E.values() if e.get("type") == "tree"}
        TSETS = {e["id"]: {x["id"] for x in e.get("sets") or []} for e in E.values() if e.get("type") == "tree"}
        def chk_tree(o, where):
            op, T = o["op"], TN.get(o.get("ent"))
            if T is None:
                R.e(f"{where}: op {op} needs ent = a tree entity"); return
            def need(k):
                v = o.get(k)
                if v not in T: R.e(f"{where}: {op} refers to tree node {v!r}, which does not exist at this point"); return None
                return v
            if op == "node":
                if not o.get("id") or o["id"] in T: R.e(f"{where}: node needs a new id (got {o.get('id')!r})"); return
                if o.get("parent") not in T: R.e(f"{where}: node {o['id']}: parent {o.get('parent')!r} does not exist"); return
                if o.get("val") is not None and len(o["val"]) != len(o.get("key") or []): R.e(f"{where}: node {o['id']}: key and val must have the same number of cells")
                T[o["id"]] = {"parent": o["parent"], "n": len(o.get("key") or [])}
            elif op == "split":
                n = need("node")
                if n is None: return
                k = o.get("at")
                if not (isinstance(k, int) and 0 < k < T[n]["n"]): R.e(f"{where}: split at must be inside the node's key (1..{T[n]['n'] - 1}), got {k}"); return
                if not o.get("new") or o["new"] in T: R.e(f"{where}: split needs a new node id in 'new' (got {o.get('new')!r})"); return
                T[o["new"]] = {"parent": T[n]["parent"], "n": k}
                T[n] = {"parent": o["new"], "n": T[n]["n"] - k}
            elif op == "nset":
                n = need("node")
                if n is not None and o.get("key") is not None: T[n]["n"] = len(o["key"])
            elif op == "member":
                need("node")
                if o.get("set") not in TSETS.get(o["ent"], ()): R.e(f"{where}: member: unknown set {o.get('set')!r} on tree {o['ent']}")
            elif op == "del":
                n = need("node")
                if n is None: return
                if any(v["parent"] == n for v in T.values()): R.e(f"{where}: del {n}: it still has children (delete leaves first)")
                elif T[n]["parent"] is None: R.e(f"{where}: del {n}: cannot delete the root")
                else: del T[n]
            elif op == "path":
                for n in o.get("nodes") or []:
                    if n not in T: R.e(f"{where}: path refers to tree node {n!r}, which does not exist at this point")
            elif op == "ptr":
                if not o.get("id"): R.e(f"{where}: ptr needs id (the pointer's name; omit node to drop it)")
                if o.get("node") is not None: need("node")
            elif op == "probe":
                if o.get("node") is not None:
                    need("node")
                    if not isinstance(o.get("key"), list): R.e(f"{where}: probe needs key (the lookup key cells)")
        def chk(o, where):
            op = o.get("op")
            if op not in OPS:
                R.e(f"{where}: unknown op {op}"); return
            if op in TREE_OPS:
                chk_tree(o, where); return
            if op == "flash" and o.get("node") is not None and o.get("ent") in TN and o["node"] not in TN[o["ent"]]:
                R.e(f"{where}: flash refers to tree node {o['node']!r}, which does not exist at this point")
            if "tok" in o:
                if o["tok"] not in toks: R.e(f"{where}: unknown token {o['tok']}")
                elif op == "spawn": alive.add(o["tok"])
                elif o["tok"] not in alive: R.e(f"{where}: token {o['tok']} used before spawn (or after gone)")
                if op == "gone": alive.discard(o["tok"])
            for k in ("at", "to"):
                if k in o:
                    loc = o[k]
                    if not (isinstance(loc, list) and loc and loc[0] in E): R.e(f"{where}: {k} must be [entity] or [store, row]; got {loc}")
                    elif E[loc[0]].get("type") == "store" and (len(loc) < 2 or loc[1] not in {r["id"] for r in E[loc[0]].get("rows") or []}): R.e(f"{where}: store {loc[0]} needs a row id, got {loc}")
                    elif E[loc[0]].get("type") in ("group", "log", "grid", "bar", "tree"): R.e(f"{where}: tokens cannot sit in a {E[loc[0]]['type']} ({loc[0]})")
            if "ent" in o and o["ent"] not in E: R.e(f"{where}: unknown entity {o['ent']}")
            for k in ("via", "wire"):
                if k in o and o[k] not in W: R.e(f"{where}: unknown wire {o[k]}")
            if op == "wire" and o.get("id") not in W: R.e(f"{where}: unknown wire {o.get('id')}")
            if op == "state" and o.get("s") not in ("run", "blocked", "idle"): R.e(f"{where}: state s must be run / blocked / idle")
            if op == "mark" and "ent" in o and o["ent"] in E and o.get("k") not in {m["id"] for m in E[o["ent"]].get("markers") or []}: R.e(f"{where}: unknown marker {o.get('k')}")
        for j, o in enumerate(sc.get("setup") or []):
            chk(o, f"scene setup op {j + 1}")
        steps = sc.get("steps") or []
        if not (8 <= len(steps) <= 24):
            R.w(f"scene has {len(steps)} steps (aim 10-20)")
        for i, st in enumerate(steps):
            if not str(st.get("text") or "").strip(): R.e(f"scene step {i + 1}: text missing")
            if len(str(st.get("text") or "")) > 150: R.w(f"scene step {i + 1}: text {len(st['text'])} chars (max ~120)")
            if not st.get("beats"): R.w(f"scene step {i + 1}: nothing changes on screen (no beats)")
            if not st.get("focus"): R.w(f"scene step {i + 1}: no focus")
            for bi, beat in enumerate(st.get("beats") or []):
                for o in (beat if isinstance(beat, list) else [beat]):
                    chk(o, f"scene step {i + 1} beat {bi + 1}")
        if sum(1 for st in steps if (st.get("at") or {}).get("card")) < .6 * max(1, len(steps)):
            R.w("scene: fewer than 60% of the steps point at code (at.card + line)")
    for m in R.err:
        print("ERROR", m)
    for m in R.warn:
        print("WARN ", m)
    nlines = sum(sum(1 for n, _ in c.get("code") or [] if n) for c in spec.get("cards") or [])
    print(f"{len(R.err)} errors, {len(R.warn)} warnings · {len(spec.get('modules') or [])} modules · {len(spec.get('cards') or [])} cards · {nlines} code lines")
    if not a.check:
        out = a.out or a.spec.with_suffix(".html")
        data = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
        html = TEMPLATE.read_text(encoding="utf-8").replace("/*ZOOM_DATA*/null", data, 1)
        out.write_text(html, encoding="utf-8")
        dump = a.dump or out.with_suffix(".data.json")
        dump.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out} ({len(html) // 1024} KB)")
    sys.exit(1 if R.err else 0)


if __name__ == "__main__":
    main()
