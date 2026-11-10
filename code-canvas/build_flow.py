#!/usr/bin/env python3
"""Build a logic-line canvas: flow.json (+ repo) -> self-contained HTML.

Usage: python3 build_flow.py flow.json --repo /path/to/repo [--out flow.html] [--check] [--dump data.json]

Reads the real source for every node's file + line ranges, validates the tree, runs the
line-coverage check (children + omitted must cover every code line of their parent), and
injects the data into template/flow.html. Exit 1 on any ERROR. See flow-spec.md.
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template" / "flow.html"
KINDS = {"step", "decision", "loop", "write", "wait", "return", "box", "external"}
LAYOUTS = {"line", "flow", "loop", "dispatch", "statemachine"}
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

    def walk(n, par, depth):
        nid = n.get("id")
        if not nid:
            R.e(f"node without id under {par}: {str(n)[:80]}")
            return
        if nid in nodes:
            R.e(f"duplicate node id {nid}")
        nodes[nid] = n
        parent[nid] = par
        n["_depth"] = depth
        for c in n.get("children") or []:
            walk(c, nid, depth + 1)
    walk(root, None, 0)

    def line_text(f, ln):
        s = src(f)
        if s is not None:
            return s[ln - 1] if 1 <= ln <= len(s) else None
        return (files.get(f) or {}).get(str(ln))

    for nid, n in nodes.items():
        kind = n.get("kind") or ("step" if nid != "root" else None)
        n["kind"] = kind
        kids = n.get("children") or []
        where = f"node {nid}"
        if nid != "root":
            if kind not in KINDS:
                R.e(f"{where}: kind must be one of {sorted(KINDS)}")
            if not n.get("title"):
                R.e(f"{where}: title missing")
            elif len(n["title"]) > 30:
                R.w(f"{where}: title {len(n['title'])} chars (≤26) — gets clamped")
            if len(str(n.get("code") or "")) > 44:
                R.w(f"{where}: code label long ({len(n['code'])})")
            if n.get("lane") and n["lane"] not in lanes:
                R.e(f"{where}: unknown lane {n['lane']}")
            if kind == "box" and not n.get("contract"):
                R.e(f"{where}: box needs contract {{in,out,effect}}")
            if kind == "box" and kids:
                R.e(f"{where}: a box has no children")
        for k in ("reads", "writes"):
            for s in n.get(k) or []:
                if s not in states:
                    R.e(f"{where}: {k} unknown state {s}")
        if kids:
            if nid != "root" and not n.get("problem"):
                R.w(f"{where}: has children but no problem (page header)")
            if len(kids) > 12:
                R.e(f"{where}: {len(kids)} children (max 12) — group them")
            elif len(kids) > 9 or len(kids) < 3:
                R.w(f"{where}: {len(kids)} children (aim for 3-9)")
            lay = n.get("layout") or "line"
            if lay not in LAYOUTS:
                R.e(f"{where}: layout must be one of {sorted(LAYOUTS)}")
            ids = {c.get("id") for c in kids}
            for e in n.get("edges") or []:
                if e[0] not in ids or e[1] not in ids:
                    R.e(f"{where}: edge {e[0]}->{e[1]} must connect its own children")
            if lay in ("flow", "statemachine") and not n.get("edges"):
                R.e(f"{where}: layout {lay} needs edges")
        # source
        rs = ranges_of(n)
        f = n.get("file")
        if nid != "root" and kind != "external" and not (f and rs):
            if not kids:
                R.e(f"{where}: needs file + lines")
        if f and rs:
            if src(f) is None and f not in files:
                R.e(f"{where}: file not found: {f}")
                continue
            total = 0
            for (x, y) in rs:
                if x > y or line_text(f, x) is None or line_text(f, y) is None:
                    R.e(f"{where}: range {x}-{y} outside {f}")
                    continue
                total += y - x + 1
                fl = files.setdefault(f, {})
                for ln in range(x, y + 1):
                    t = line_text(f, ln)
                    if t is not None:
                        fl[str(ln)] = t.replace("\t", "    ").rstrip()
            if not kids and kind != "box":
                if total > 80:
                    R.e(f"{where}: leaf with {total} lines (max 80) — split into steps")
                elif total > 45:
                    R.w(f"{where}: leaf with {total} lines (aim ≤45)")
            # the identifier named on the card should appear in its lines
            ident = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(n.get("code") or ""))
            if ident:
                body = "\n".join((files.get(f) or {}).get(str(ln), "") for (x, y) in rs for ln in range(x, y + 1))
                if not any(i in body for i in ident):
                    R.w(f"{where}: none of {ident[:3]} appear in {f}:{rs} — wrong lines?")

    # coverage: children (same file, inside the parent's span) + omitted must cover every code line of the parent
    for nid, n in nodes.items():
        f, rs, kids = n.get("file"), ranges_of(n), n.get("children") or []
        om = n.get("omitted") or []
        for o in om:
            if not (isinstance(o, list) and len(o) >= 3 and str(o[2]).strip()):
                R.e(f"node {nid}: omitted entries are [from, to, reason]")
        if not (f and rs and kids):
            if om and not kids:
                R.w(f"node {nid}: omitted only makes sense on a node with children")
            continue
        own = {ln for (x, y) in rs for ln in range(x, y + 1)}
        cov = set()
        for c in kids:
            if c.get("file") == f:
                for (x, y) in ranges_of(c):
                    cov |= set(range(x, y + 1))
                    outside = [ln for ln in range(x, y + 1) if ln not in own]
        for o in om:
            if isinstance(o, list) and len(o) >= 2:
                cov |= set(range(o[0], o[1] + 1))
        fl = files.get(f) or {}
        missing = [ln for ln in sorted(own - cov) if not TRIVIAL.match(fl.get(str(ln), "")) and not COMMENT.match(fl.get(str(ln), ""))]
        code_lines = [ln for ln in own if not TRIVIAL.match(fl.get(str(ln), "")) and not COMMENT.match(fl.get(str(ln), ""))]
        n["_cov"] = {"lines": len(code_lines), "omitted": len([ln for ln in code_lines if any(o[0] <= ln <= o[1] for o in om if isinstance(o, list) and len(o) >= 2)])}
        if missing:
            R.e(f"node {nid}: {len(missing)} code lines of {f} not covered by any child or omitted: {compress(missing)}")

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
        if c.get("id") not in seen_top and c.get("kind") != "external":
            R.w(f"walk never visits top-level stage {c.get('id')}")
    if wk and not spec.get("example"):
        R.w("example {title, token, setup} missing")

    for n in nodes.values():
        n.pop("_depth", None)
    spec["files"] = files
    for m in R.err:
        print("ERROR", m)
    for m in R.warn:
        print("WARN ", m)
    leaves = sum(1 for n in nodes.values() if not n.get("children"))
    nlines = sum(len(v) for v in files.values())
    print(f"{len(R.err)} errors, {len(R.warn)} warnings · {len(nodes) - 1} steps ({leaves} leaves) · {len(files)} files · {nlines} source lines · walk {len(wk)}")
    if not a.check:
        out = a.out or a.spec.with_suffix(".html")
        data = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
        out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*FLOW_DATA*/null", data, 1), encoding="utf-8")
        (a.dump or out.with_suffix(".data.json")).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    sys.exit(1 if R.err else 0)


if __name__ == "__main__":
    main()
