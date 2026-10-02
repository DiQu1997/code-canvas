#!/usr/bin/env python3
"""
merge_upgrade.py <画布库里的 canvas.json> <agent 升级后的副本>

升级任务的服务端收尾：事实层锁死、叙事层放开。
1. 逐字段比对事实层——cards 的 id/code/file/lang/kind/layout/blocks 行段、
   wires 的 id/kind/from/to/route、steps 的数量与 focus/lines/wires/unfold/
   expand/storyline、files、regions 的 id。任何一处变了 → 整份拒收（exit 2）。
2. 机械修剪：overview.vars 里在代码（卡片 ∪ files）中不存在的变量名剔除并记录。
3. 副本过 validate，ERROR 清零才原子写回画布库（exit 0）；否则 exit 2，原画布不动。
exit 1：参数/格式错。
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CARD_FACTS = ("code", "file", "lang", "kind", "layout")
WIRE_FACTS = ("kind", "from", "to", "route")
STEP_FACTS = ("focus", "lines", "wires", "unfold", "expand", "storyline")


def block_shape(bs):
    return [(b.get("name"), b.get("lines"), block_shape(b.get("children"))) for b in (bs or [])]


def fact_diffs(old: dict, new: dict) -> list:
    out = []
    oc = {c.get("id"): c for c in old.get("cards", [])}
    nc = {c.get("id"): c for c in new.get("cards", [])}
    if set(oc) != set(nc):
        out.append("卡片集合变了：少了 {} / 多了 {}".format(sorted(set(oc) - set(nc)), sorted(set(nc) - set(oc))))
    for cid in set(oc) & set(nc):
        for k in CARD_FACTS:
            if oc[cid].get(k) != nc[cid].get(k):
                out.append("卡 {} 的 {} 被改动".format(cid, k))
        if block_shape(oc[cid].get("blocks")) != block_shape(nc[cid].get("blocks")):
            out.append("卡 {} 的 blocks 行段被改动".format(cid))
    ow = {w.get("id"): w for w in old.get("wires", [])}
    nw = {w.get("id"): w for w in new.get("wires", [])}
    if set(ow) != set(nw):
        out.append("连线集合变了：少了 {} / 多了 {}".format(sorted(set(ow) - set(nw)), sorted(set(nw) - set(ow))))
    for wid in set(ow) & set(nw):
        for k in WIRE_FACTS:
            if ow[wid].get(k) != nw[wid].get(k):
                out.append("线 {} 的 {} 被改动".format(wid, k))
    os_, ns = old.get("steps", []), new.get("steps", [])
    if len(os_) != len(ns):
        out.append("步数变了：{} → {}".format(len(os_), len(ns)))
    for i, (a, b) in enumerate(zip(os_, ns)):
        for k in STEP_FACTS:
            if a.get(k) != b.get(k):
                out.append("第 {} 步的 {} 被改动".format(i, k))
    if old.get("files") != new.get("files"):
        out.append("files 上下文映射被改动")
    if {r.get("id") for r in old.get("regions", [])} != {r.get("id") for r in new.get("regions", [])}:
        out.append("regions 的 id 集合变了")
    return out


def prune_vars(d: dict) -> list:
    ov = d.get("overview")
    if not isinstance(ov, dict) or not isinstance(ov.get("vars"), list):
        return []
    corpus = "\n".join(c.get("code") or "" for c in d.get("cards", [])) + "\n" \
        + "\n".join(str(t) for t in (d.get("files") or {}).values())
    keep, dropped = [], []
    for v in ov["vars"]:
        name = (v.get("name") if isinstance(v, dict) else None) or ""
        if name and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", corpus):
            keep.append(v)
        else:
            dropped.append(name or "?")
    ov["vars"] = keep
    return dropped


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 1
    hub_j, up_j = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        old = json.loads(hub_j.read_text(encoding="utf-8"))
        new = json.loads(up_j.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("读取失败:", e)
        return 1
    diffs = fact_diffs(old, new)
    if diffs:
        print("升级被拒：事实层被改动（画布未改）")
        for x in diffs:
            print("  -", x)
        return 2
    dropped = prune_vars(new)
    if dropped:
        print("剔除代码里不存在的变量:", ", ".join(dropped))
    tmp = Path(tempfile.mkstemp(suffix=".json", dir=str(hub_j.parent))[1])
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("validate.py")), str(tmp)],
                       capture_output=True, text=True)
    errs = [ln for ln in r.stdout.splitlines() if ln.startswith("ERROR")]
    if r.returncode != 0 or errs:
        tmp.unlink()
        print("升级未过 validate（画布未改）：")
        print("\n".join(errs) or r.stdout[-800:])
        return 2
    tmp.replace(hub_j)
    added = [k for k in ("overview", "gaps", "objects") if new.get(k) and not old.get(k)]
    traces = sum(1 for s in new.get("steps", []) if s.get("trace"))
    print("升级已合并进 {}（新增 {}；步卡 {} 步）".format(hub_j.name, "、".join(added) or "无新顶层", traces))
    return 0


if __name__ == "__main__":
    sys.exit(main())
