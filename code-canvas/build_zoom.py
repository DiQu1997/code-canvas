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
TEMPLATE = HERE / "template" / "zoom.html"
PRIMS = {"loop", "pipeline", "dispatch", "pool", "statemachine", "flow", "tree", "layers", "grid", "structs"}
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
    stories = spec.get("stories") or ([spec["story"]] if spec.get("story") else [])
    if not stories:
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
    fill_code(spec, a.repo, R)
    validate(spec, R)
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
