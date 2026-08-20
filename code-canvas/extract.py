#!/usr/bin/env python3
"""extract.py — 结构提取器：零 LLM、毫秒级，从源码机械提取结构层画布。

结构层 = 卡片（函数/方法，原文逐字）+ call 线（调用点行级锚定）+
diff 行映射 + 粗布局 {col, band}。叙事层（故事线/note/explain/terms/steps）
一概不产出——那是 agent 的层，用 --merge 增量保留。

后端：Python 标准库 ast（v1 只支持 .py；提取器接口与语言无关，
其他语言以后加 tree-sitter 后端，不动输出契约）。

用法:
  python3 extract.py <文件或目录...> [--out canvas.json]
      [--repo-root DIR]     卡片 file 字段相对它写（默认：git 根或公共父目录）
      [--diff [BASE]]       叠加 git diff 行映射（默认对 HEAD），meta.mode="diff"
      [--merge OLD.json]    增量合并：代码未变的卡保留旧叙事与布局；
                            变了的卡丢弃行锚叙事（blocks/terms）并在报告中列出
      [--title 标题]

退出码 0；结构报告打印到 stderr。输出保证过 validate.py 0 ERROR。
兼容 Python 3.8。
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------- 发现与解析 ----------

def discover(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(x for x in p.rglob("*.py")
                          if not any(part.startswith(".") or part in ("__pycache__", "node_modules")
                                     for part in x.parts))
        elif p.suffix == ".py":
            out.append(p)
    return out


def parse_functions(path, repo_root):
    """一个文件里的函数/方法卡素材。跳过函数内嵌套 def（属于父函数的代码）。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    rel = str(path.resolve().relative_to(repo_root))
    funcs = []

    def add(node, cls):
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        end = node.end_lineno
        funcs.append({
            "name": node.name, "cls": cls,
            "qual": (cls + "." + node.name) if cls else node.name,
            "file": rel, "start": start, "end": end,
            "code": "\n".join(lines[start - 1:end]),
            "node": node,
        })

    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(top, None)
        elif isinstance(top, ast.ClassDef):
            for item in top.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(item, top.name)
    return funcs


def assign_ids(funcs):
    """稳定、可读的卡 id：函数名 → 撞名加类名 → 再撞加文件名。"""
    def slug(s):
        return re.sub(r"[^a-z0-9_]", "_", s.lower())
    by_name = {}
    for f in funcs:
        by_name.setdefault(f["name"], []).append(f)
    for f in funcs:
        if len(by_name[f["name"]]) == 1:
            f["id"] = slug(f["name"])
        elif f["cls"] and len([x for x in by_name[f["name"]] if x["cls"] == f["cls"]]) == 1:
            f["id"] = slug(f["cls"] + "_" + f["name"])
        else:
            f["id"] = slug(Path(f["file"]).stem + "_" + (f["cls"] + "_" if f["cls"] else "") + f["name"])
    seen = {}
    for f in funcs:  # 最后兜底去重
        if f["id"] in seen:
            seen[f["id"]] += 1
            f["id"] = "{}_{}".format(f["id"], seen[f["id"]])
        else:
            seen[f["id"]] = 0
    return funcs


# ---------- call 线 ----------

def call_wires(funcs):
    """调用点行 → 被调卡。被调名歧义（多张卡同名方法）时不画——线是断言不是猜测。"""
    by_name = {}
    for f in funcs:
        by_name.setdefault(f["name"], []).append(f)
    wires, seen_pair = [], set()
    for f in funcs:
        for node in ast.walk(f["node"]):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            tail = callee.id if isinstance(callee, ast.Name) else (
                callee.attr if isinstance(callee, ast.Attribute) else None)
            if not tail:
                continue
            targets = by_name.get(tail, [])
            if len(targets) != 1 or targets[0] is f:
                continue
            t = targets[0]
            if (f["id"], t["id"]) in seen_pair:
                continue
            seen_pair.add((f["id"], t["id"]))
            wires.append({
                "id": "w-{}-{}".format(f["id"], t["id"]), "kind": "call",
                "from": {"card": f["id"], "line": node.lineno - f["start"] + 1},
                "to": {"card": t["id"]},
            })
    return wires


# ---------- git diff 行映射 ----------

def diff_hunks(repo_root, rel, base):
    cmd = ["git", "-C", str(repo_root), "diff", "--unified=0", base, "--", rel]
    r = subprocess.run(cmd, capture_output=True, text=True)
    hunks = []  # (new_start, new_count, removed_lines[])
    removed = []
    for ln in r.stdout.split("\n"):
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            removed = []
            hunks.append((int(m.group(1)), int(m.group(2) or "1"), removed))
        elif ln.startswith("-") and not ln.startswith("---"):
            removed.append(ln[1:])
    return hunks


def apply_diff(funcs, repo_root, base):
    """把文件级 hunks 映射进各函数卡的相对行号。"""
    by_file = {}
    for f in funcs:
        by_file.setdefault(f["file"], []).append(f)
    touched = 0
    for rel, fl in by_file.items():
        for new_start, new_count, removed in diff_hunks(repo_root, rel, base):
            for f in fl:
                lo, hi = f["start"], f["end"]
                d = f.setdefault("diff", {"added": [], "removed": []})
                if new_count > 0:
                    for ln in range(new_start, new_start + new_count):
                        if lo <= ln <= hi:
                            d["added"].append(ln - lo + 1)
                if removed:
                    # 纯删除：new_start 是其后的 head 行；替换：贴在新增块之前
                    anchor = new_start if new_count == 0 else new_start - 1
                    if lo - 1 <= anchor <= hi:
                        d["removed"] += [{"after": max(anchor - lo + 1, 0), "code": c} for c in removed]
    for f in funcs:
        if "diff" in f and not (f["diff"]["added"] or f["diff"]["removed"]):
            del f["diff"]
        elif "diff" in f:
            touched += 1
    return touched


# ---------- 粗布局 ----------

def layout(funcs, wires):
    """BFS 深度定列（入度 0 为根），列内按 (file, start) 定带。≤5 列封顶。"""
    ids = {f["id"] for f in funcs}
    callers = {i: set() for i in ids}
    callees = {i: set() for i in ids}
    for w in wires:
        callers[w["to"]["card"]].add(w["from"]["card"])
        callees[w["from"]["card"]].add(w["to"]["card"])
    col = {}
    frontier = sorted(i for i in ids if not callers[i]) or sorted(ids)
    for i in frontier:
        col[i] = 0
    depth = 0
    while frontier and depth < 4:
        depth += 1
        nxt = []
        for i in frontier:
            for j in sorted(callees[i]):
                if j not in col:
                    col[j] = depth
                    nxt.append(j)
        frontier = nxt
    for i in ids:
        col.setdefault(i, 4)
    order = {f["id"]: (f["file"], f["start"]) for f in funcs}
    per_col = {}
    for i in sorted(ids, key=lambda x: order[x]):
        c = min(col[i], 4)
        per_col.setdefault(c, []).append(i)
    pos = {}
    for c, members in per_col.items():
        for band, i in enumerate(members):
            pos[i] = {"col": c, "band": band}
    return pos


# ---------- 增量合并 ----------

NARRATIVE_CARD_FIELDS = ("blocks", "terms", "plan", "plan_delta", "collapsed")


def merge_old(cards, doc, old):
    """代码未变的卡：叙事字段 + 布局原样保留；变了的：行锚叙事（blocks/terms）
    丢弃并报告。锚在变卡/消失卡上的 note 与引用它们的 step 同样丢弃——
    行号还在范围内不代表叙事还成立。regions 过滤失效成员。"""
    old_cards = {c["id"]: c for c in old.get("cards", [])}
    report = {"unchanged": [], "changed": [], "new": [], "removed": [], "dropped": []}
    ids = {c["id"] for c in cards}

    # 第一遍：判定每张卡的命运，转移叙事与布局
    changed = set()
    for c in cards:
        o = old_cards.get(c["id"])
        if o is None:
            report["new"].append(c["id"])
            continue
        if o.get("layout"):
            c["layout"] = o["layout"]  # 布局稳定优先于重排
        if o.get("code") == c["code"]:
            for k in NARRATIVE_CARD_FIELDS:
                if k in o:
                    c[k] = o[k]
            if o.get("name"):
                c["name"] = o["name"]
            report["unchanged"].append(c["id"])
        else:
            if "collapsed" in o:
                c["collapsed"] = o["collapsed"]
            changed.add(c["id"])
            report["changed"].append(c["id"])
    report["removed"] = sorted(set(old_cards) - ids)
    stale = changed | set(report["removed"])

    def anchor_ok(cid, ln):
        if cid in stale:
            return False
        card = next((c for c in cards if c["id"] == cid), None)
        return card and 1 <= ln <= len(card["code"].split("\n"))

    # 第二遍：画布级元素按卡的命运过滤
    for r in old.get("regions", []):
        members = [m for m in r.get("cards", []) if m in ids]
        if members:
            doc["regions"].append(dict(r, cards=members))
    for n in old.get("notes", []):
        a = n.get("anchor")
        p = n.get("place") or {}
        bad = (a and not anchor_ok(a.get("card"), a.get("line", 1))) \
            or (p.get("of") and p["of"] not in ids)
        if bad:
            report["dropped"].append("note:" + n.get("id", "?"))
        else:
            doc["notes"].append(n)
    note_ids = {n.get("id") for n in doc["notes"]}
    wire_ids = {w["id"] for w in doc["wires"]}
    for s in old.get("steps", []):
        refs_ok = all(cid in ids for cid in (s.get("expand") or [])) \
            and all(anchor_ok(cid, ln) for cid, ln in (s.get("lines") or [])) \
            and all(fid in ids or fid in note_ids for fid in (s.get("focus") or [])) \
            and all(wid in wire_ids for wid in (s.get("wires") or []))
        if refs_ok:
            doc["steps"].append(s)
        else:
            report["dropped"].append("step:" + s.get("title", "?"))
    if old.get("meta"):
        for k in ("title", "subtitle", "audience"):
            if old["meta"].get(k):
                doc["meta"][k] = old["meta"][k]
    return report


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="structure.json")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--diff", nargs="?", const="HEAD", default=None, metavar="BASE")
    ap.add_argument("--merge", default=None, metavar="OLD_JSON")
    ap.add_argument("--title", default=None)
    ap.add_argument("--embed-context", action="store_true",
                    help="把卡片引用的文件全文嵌入 files 映射（渲染后卡内可展开上下文）")
    ns = ap.parse_args()

    files = discover(ns.paths)
    if not files:
        sys.exit("没有找到 .py 文件")
    if ns.repo_root:
        root = Path(ns.repo_root).resolve()
    else:
        r = subprocess.run(["git", "-C", str(files[0].parent), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
        root = Path(r.stdout.strip()).resolve() if r.returncode == 0 else \
            Path(*__import__("os").path.commonprefix([str(f.resolve()) for f in files]).rpartition("/")[0:1]).resolve()

    funcs = []
    for f in files:
        funcs += parse_functions(f, root)
    assign_ids(funcs)
    wires = call_wires(funcs)
    touched = apply_diff(funcs, root, ns.diff) if ns.diff else 0
    pos = layout(funcs, wires)

    cards = []
    for f in funcs:
        c = {"id": f["id"],
             "name": (f["cls"] + "." if f["cls"] else "") + f["name"] + "()",
             "file": "{}:{}".format(f["file"], f["start"]),
             "lang": "py", "layout": pos[f["id"]], "collapsed": True,
             "code": f["code"]}
        if f.get("diff"):
            c["diff"] = {"added": sorted(set(f["diff"]["added"])), "removed": f["diff"]["removed"]}
        cards.append(c)

    title = ns.title or "{} 结构层".format(root.name)
    doc = {"meta": {"title": title,
                    "subtitle": "extract.py 机械提取 · {} 卡 {} 线 · 叙事层待补".format(len(cards), len(wires))},
           "regions": [], "cards": cards, "wires": wires, "notes": [], "steps": []}
    if ns.diff:
        doc["meta"]["mode"] = "diff"

    report = None
    if ns.merge:
        old = json.loads(Path(ns.merge).read_text(encoding="utf-8"))
        report = merge_old(cards, doc, old)
        if old.get("meta", {}).get("mode") and not ns.diff:
            doc["meta"].pop("mode", None)

    if ns.embed_context:
        doc["files"] = {}
        for rel in sorted({c["file"].rsplit(":", 1)[0] for c in cards}):
            fp = root / rel
            if fp.exists():
                doc["files"][rel] = fp.read_text(encoding="utf-8", errors="replace")

    if not doc["steps"]:
        doc["steps"] = [{"title": "总览（结构层）",
                         "caption": "机械提取的结构骨架：{} 张函数卡、{} 根调用线。叙事层（故事线/注释/步进）待 agent 补写。".format(
                             len(cards), len(wires)), "fit": True}]

    Path(ns.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote {}  ({} cards, {} wires{})".format(
        ns.out, len(cards), len(wires), ", {} 卡有 diff".format(touched) if ns.diff else ""), file=sys.stderr)
    if report:
        print("merge: {} 未变(叙事保留) / {} 已变(行锚叙事丢弃) / {} 新增 / {} 移除 / 丢弃 {}".format(
            len(report["unchanged"]), len(report["changed"]), len(report["new"]),
            len(report["removed"]), report["dropped"] or "无"), file=sys.stderr)
        if report["changed"]:
            print("  待重讲: " + ", ".join(report["changed"]), file=sys.stderr)


if __name__ == "__main__":
    main()
