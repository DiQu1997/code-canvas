#!/usr/bin/env python3
"""Build a logic-line canvas: flow.json (+ repo) -> self-contained HTML.

Usage: python3 build_flow.py flow.json --repo /path/to/repo [--out flow.html] [--check] [--dump data.json]

Reads the real source for every node's file + line ranges, validates the tree, runs the
line-coverage check (a function's pseudo-code steps + omitted must cover every code line), and
injects the data into template/flow.html. Exit 1 on any ERROR. See flow-spec.md.
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template" / "flow.html"
NKINDS = {"thread", "loop", "component", "function", "queue", "store", "state", "external", "outside"}
SKINDS = {"step", "decision", "loop", "write", "wait", "return", "call"}
EKINDS = {"flow", "call", "queue", "return", "async", "data", "error"}
SHAPES = {"queue", "list", "stack", "map", "table", "set", "pool", "ring", "wheel", "tree", "bitmap", "array", "lock", "flag", "scalar"}
TRIVIAL = re.compile(r"^[\s{}()\[\];,]*$|^\s*(else|end|fi|done|try|finally|do)\s*[:{]?\s*$|^\s*\}\s*else\s*\{\s*$")
COMMENT = re.compile(r"^\s*(#(?!\s*(include|define|if|ifdef|ifndef|else|elif|endif|pragma|undef)\b)|//|/\*|\*|\"\"\"|''')")


class Report:
    def __init__(self): self.err, self.warn = [], []
    def e(self, m): self.err.append(m)
    def w(self, m): self.warn.append(m)


def ranges_of(n):
    if n.get("ranges"):
        return [tuple(r) for r in n["ranges"]]
    if n.get("lines"):
        return [tuple(n["lines"])]
    return []


def compress(nums):
    nums = sorted(nums)
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dump", type=Path)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    R = Report()
    meta = spec.get("meta") or {}
    if meta.get("mode") != "flow":
        R.w("meta.mode should be 'flow'")
    for k in ("title", "question", "answer"):
        if not meta.get(k):
            R.e(f"meta.{k} missing")
    if not meta.get("judgment"):
        R.w("meta.judgment missing")
    lanes = {l.get("id") for l in spec.get("lanes") or []}
    states = {s.get("id") for s in spec.get("state") or []}
    for s in spec.get("state") or []:
        if s.get("shape") and s["shape"] not in SHAPES:
            R.w(f"state {s.get('id')}: unknown shape {s['shape']}")
    root = spec.get("root")
    if not root or not root.get("children"):
        R.e("root with children required")
        root = root or {"id": "root", "children": []}
    root.setdefault("id", "root")

    files = dict(spec.get("files") or {})   # already-filled data (re-render without a repo)
    cache = {}

    def src(f):
        if f in cache:
            return cache[f]
        p = (a.repo / f) if a.repo else None
        if p and p.is_file():
            cache[f] = p.read_text(encoding="utf-8", errors="replace").split("\n")
        else:
            cache[f] = None
        return cache[f]

    nodes, parent = {}, {}

    def walk(n, par):
        nid = n.get("id")
        if not nid:
            R.e(f"node without id under {par}: {str(n)[:80]}")
            return
        if nid in nodes:
            R.e(f"duplicate node id {nid}")
        nodes[nid] = n
        parent[nid] = par
        for c in n.get("children") or []:
            walk(c, nid)
    walk(root, None)

    def line_text(f, ln):
        s = src(f)
        if s is not None:
            return s[ln - 1] if 1 <= ln <= len(s) else None
        return (files.get(f) or {}).get(str(ln))

    def load(f, rs, where):
        if src(f) is None and f not in files:
            R.e(f"{where}: file not found: {f}")
            return False
        ok = True
        for (x, y) in rs:
            if x > y or line_text(f, x) is None or line_text(f, y) is None:
                R.e(f"{where}: range {x}-{y} outside {f}")
                ok = False
                continue
            fl = files.setdefault(f, {})
            for ln in range(x, y + 1):
                t = line_text(f, ln)
                if t is not None:
                    fl[str(ln)] = t.replace("\t", "    ").rstrip()
        return ok

    def is_code(f, ln):
        t = (files.get(f) or {}).get(str(ln), "")
        return not TRIVIAL.match(t) and not COMMENT.match(t)

    CW, RH, GX, GY = 272, 176, 84, 58
    for nid, n in nodes.items():
        where = f"node {nid}"
        kind = n.get("kind") or ("component" if nid != "root" else None)
        n["kind"] = kind
        kids = n.get("children") or []
        steps = n.get("steps") or []
        if nid != "root":
            if kind not in NKINDS:
                R.e(f"{where}: kind must be one of {sorted(NKINDS)}")
            if kind == "outside":
                if n.get("ref") and n["ref"] not in nodes:
                    R.e(f"{where}: outside.ref {n['ref']} is not a node id")
                if not n.get("ref") and not n.get("title"):
                    R.e(f"{where}: outside needs ref or title")
                continue
            if not n.get("title"):
                R.e(f"{where}: title missing")
            elif len(n["title"]) > 22:
                R.w(f"{where}: title {len(n['title'])} chars (≤18) — gets clamped")
            if kids and steps:
                R.e(f"{where}: a node is either a diagram (children) or a function (steps), not both")
            if not kids and not steps and kind in ("component", "thread", "loop", "function"):
                R.w(f"{where}: {kind} with neither children nor steps — a dead end; give it steps (if it is one function) or mark it external/queue/store")
        # ---- diagram level ----
        if kids:
            real = [c for c in kids if c.get("kind") != "outside"]
            if len(real) > 12:
                R.e(f"{where}: {len(real)} parts on one diagram (max 12) — group them")
            elif len(real) > 9 or len(real) < 3:
                R.w(f"{where}: {len(real)} parts (aim 3-9)")
            if nid != "root" and not (n.get("problem") or n.get("desc")):
                R.w(f"{where}: diagram without problem/desc (page header)")
            ids = {c.get("id") for c in kids}
            boxes = []
            for c in kids:
                pos = c.get("pos")
                if not (isinstance(pos, list) and len(pos) == 2):
                    R.e(f"node {c.get('id')}: pos [col,row] required on a diagram")
                    continue
                out = c.get("kind") == "outside"
                sw, sh = c.get("span") or ([.62, .36] if out else [1, 1])
                boxes.append((c.get("id"), pos[0] * CW, pos[1] * RH + ((RH - GY) * .32 if out and not c.get("span") else 0), sw * CW - GX, sh * RH - GY))
            for i in range(len(boxes)):
                for k in range(i + 1, len(boxes)):
                    a_, b_ = boxes[i], boxes[k]
                    if a_[1] < b_[1] + b_[3] and b_[1] < a_[1] + a_[3] and a_[2] < b_[2] + b_[4] and b_[2] < a_[2] + a_[4]:
                        R.e(f"{where}: {a_[0]} and {b_[0]} overlap — fix pos/span")
            if boxes:
                wcols = (max(b_[1] + b_[3] for b_ in boxes) - min(b_[1] for b_ in boxes)) / CW
                if wcols > 4.3:
                    R.w(f"{where}: diagram is {wcols:.1f} columns wide — more than ~4 shrinks on screen; use rows too")
            edges = n.get("edges") or []
            touched, numbered = set(), 0
            for e in edges:
                fr, to = (e[0], e[1]) if isinstance(e, list) else (e.get("from"), e.get("to"))
                ek = (e[3] if isinstance(e, list) and len(e) > 3 else e.get("kind") if isinstance(e, dict) else None)
                en = (e[4] if isinstance(e, list) and len(e) > 4 else e.get("n") if isinstance(e, dict) else None)
                lab = (e[2] if isinstance(e, list) and len(e) > 2 else e.get("label") if isinstance(e, dict) else "")
                if isinstance(e, dict) and e.get("sides") and not re.fullmatch(r"[lrtb]{2}", e["sides"]):
                    R.e(f"{where}: edge sides must be two of l r t b")
                if fr not in ids or to not in ids:
                    R.e(f"{where}: edge {fr}->{to} must connect parts of this diagram")
                    continue
                if ek and ek not in EKINDS:
                    R.w(f"{where}: edge {fr}->{to} unknown kind {ek}")
                if len(str(lab or "")) > 9:
                    R.w(f"{where}: edge {fr}->{to} label {len(lab)} chars (≤7 fits between two boxes; put detail in the edge's note)")
                touched |= {fr, to}
                numbered += en not in (None, "")
            if not edges:
                R.e(f"{where}: a diagram needs edges — the relations are the point")
            for c in kids:
                if c.get("id") not in touched:
                    R.w(f"{where}: part {c.get('id')} has no edge — how does it relate to the others?")
            if len(edges) >= 3 and numbered * 2 < len(edges):
                R.w(f"{where}: only {numbered}/{len(edges)} edges numbered — number the main sequence so the order can be read off the picture")
            if len(edges) > 16:
                R.w(f"{where}: {len(edges)} edges — likely unreadable")
            for g in n.get("groups") or []:
                for x in g.get("nodes") or []:
                    if x not in ids:
                        R.e(f"{where}: group {g.get('title')} unknown node {x}")
        # ---- source ----
        rs, f = ranges_of(n), n.get("file")
        if f and rs:
            if not load(f, rs, where):
                continue
            ident = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(n.get("code") or ""))
            if ident:
                body = "\n".join((files.get(f) or {}).get(str(ln), "") for (x, y) in rs for ln in range(x, y + 1))
                if not any(i in body for i in ident):
                    R.w(f"{where}: none of {ident[:3]} appear in {f}:{rs} — wrong lines?")
        # ---- function level: pseudo-code steps + coverage ----
        om = n.get("omitted") or []
        for o in om:
            if not (isinstance(o, list) and len(o) >= 3 and str(o[2]).strip()):
                R.e(f"{where}: omitted entries are [from, to, reason]")
        if steps:
            if not (f and rs):
                R.e(f"{where}: a function (steps) needs file + lines")
                continue
            if len(steps) > 14:
                R.e(f"{where}: {len(steps)} pseudo-code steps (max 14)")
            elif len(steps) < 3:
                R.w(f"{where}: only {len(steps)} steps")
            own = {ln for (x, y) in rs for ln in range(x, y + 1)}
            if len(own) > 220:
                R.w(f"{where}: function spans {len(own)} lines — consider splitting into a diagram of sub-parts")
            cov = set()
            for i, st in enumerate(steps):
                sw = f"{where} step {i + 1}"
                if not st.get("title"):
                    R.e(f"{sw}: title missing")
                if st.get("kind") and st["kind"] not in SKINDS:
                    R.e(f"{sw}: kind must be one of {sorted(SKINDS)}")
                if st.get("call") and st["call"] not in nodes:
                    R.e(f"{sw}: call {st['call']} is not a node id")
                sl = st.get("lines")
                if not (isinstance(sl, list) and len(sl) == 2):
                    R.e(f"{sw}: lines [a,b] required")
                    continue
                sf = st.get("file") or f
                if sf != f:
                    load(sf, [tuple(sl)], sw)
                    continue
                if sl[0] not in own or sl[1] not in own:
                    R.e(f"{sw}: lines {sl} outside the function's lines {rs}")
                cov |= set(range(sl[0], sl[1] + 1))
            for o in om:
                if isinstance(o, list) and len(o) >= 2:
                    cov |= set(range(o[0], o[1] + 1))
            code_lines = [ln for ln in sorted(own) if is_code(f, ln)]
            missing = [ln for ln in code_lines if ln not in cov]
            n["_cov"] = {"lines": len(code_lines), "omitted": len([ln for ln in code_lines if any(o[0] <= ln <= o[1] for o in om if isinstance(o, list) and len(o) >= 2)])}
            if missing:
                R.e(f"{where}: {len(missing)} code lines of {f} not covered by any step or omitted: {compress(missing)}")

    # walk
    wk = spec.get("walk") or []
    if len(wk) < 10:
        R.w(f"walk has {len(wk)} steps (aim 15-40)")
    seen_top = set()
    for i, s in enumerate(wk):
        where = f"walk step {i + 1}"
        n = nodes.get(s.get("node"))
        if not n:
            R.e(f"{where}: unknown node {s.get('node')}")
            continue
        if not s.get("text"):
            R.e(f"{where}: text missing")
        if s.get("step") is not None:
            if not (isinstance(s["step"], int) and 1 <= s["step"] <= len(n.get("steps") or [])):
                R.e(f"{where}: step {s['step']} — node {n['id']} has {len(n.get('steps') or [])} pseudo-code steps")
        if s.get("line") is not None:
            if not any(x <= s["line"] <= y for (x, y) in ranges_of(n)):
                R.e(f"{where}: line {s['line']} not inside node {n['id']} lines {ranges_of(n)}")
        for k in (s.get("state") or {}):
            if k not in states:
                R.e(f"{where}: unknown state {k}")
        p = s["node"]
        while parent.get(p) not in (None, "root"):
            p = parent[p]
        seen_top.add(p)
    for c in root.get("children") or []:
        if c.get("id") not in seen_top and c.get("kind") not in ("external", "outside", "queue", "store", "state"):
            R.w(f"walk never visits top-level part {c.get('id')}")
    if wk and not spec.get("example"):
        R.w("example {title, token, setup} missing")

    spec["files"] = files
    for m in R.err:
        print("ERROR", m)
    for m in R.warn:
        print("WARN ", m)
    diagrams = sum(1 for n in nodes.values() if n.get("children"))
    fns = sum(1 for n in nodes.values() if n.get("steps"))
    nlines = sum(len(v) for v in files.values())
    print(f"{len(R.err)} errors, {len(R.warn)} warnings · {diagrams} diagrams · {fns} functions with pseudo-code · {len(files)} files · {nlines} source lines · walk {len(wk)}")
    if not a.check:
        out = a.out or a.spec.with_suffix(".html")
        data = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
        out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*FLOW_DATA*/null", data, 1), encoding="utf-8")
        (a.dump or out.with_suffix(".data.json")).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    sys.exit(1 if R.err else 0)


if __name__ == "__main__":
    main()
