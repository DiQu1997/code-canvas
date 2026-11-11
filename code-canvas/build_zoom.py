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
    # an animated scene (scene.json beside the spec, or spec["scene"]) becomes the top level of the canvas
    sib = a.spec.with_name("scene.json")
    if "scene" not in spec and sib.exists():
        spec["scene"] = json.loads(sib.read_text(encoding="utf-8"))
    fill_code(spec, a.repo, R)
    validate(spec, R)
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
