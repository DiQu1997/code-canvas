#!/usr/bin/env python3
"""compare.py — 同底图对照：计划画布 vs diff 画布。

计划说要动哪些卡、实际动了哪些卡，机械对出三类差集：
  计划内   计划标了 add/modify/remove，diff 里也动了
  ⚠ 计划外  diff 里动了，计划没标——控制感的核心信号
  ○ 计划未动 计划标了，diff 里没动
计划线（plan:"add"/"remove"）按 (from.card → to.card) 对照实际线。

用法:
  python3 compare.py plan.json diff.json [--annotate out.json]

--annotate 把对照结果写回 diff 的副本：计划外的卡注入 plan_delta:"offplan"、
计划未动的卡注入 plan_delta:"missed"，渲染后卡头出对照徽标。
退出码：0 = 全对上；2 = 存在计划外 / 计划未动 / 计划线未落地。
"""
import json
import sys
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def changed_cards(diff_doc):
    """diff 画布里实际动了的卡：有非空 diff 标记。"""
    out = set()
    for c in diff_doc.get("cards", []):
        d = c.get("diff") or {}
        if d.get("added") or d.get("removed"):
            out.add(c["id"])
    return out


def wire_pairs(doc, only_plan=None):
    """(from.card, to.card, kind) 集合。only_plan 过滤 plan 值。"""
    out = set()
    for w in doc.get("wires", []):
        if only_plan is not None and w.get("plan") != only_plan:
            continue
        out.add(((w.get("from") or {}).get("card"), (w.get("to") or {}).get("card"), w.get("kind")))
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description="同底图对照：计划画布 vs diff 画布")
    ap.add_argument("plan_json")
    ap.add_argument("diff_json")
    ap.add_argument("--annotate", default=None, metavar="OUT_JSON")
    ap.add_argument("--repo", default=None, metavar="DIR",
                    help="仓库路径：交叉核对 git 改动文件是否都被 diff 画布覆盖（防漏报/瞒报）")
    ns = ap.parse_args()
    annotate = ns.annotate
    plan, diff = load(ns.plan_json), load(ns.diff_json)

    if (plan.get("meta") or {}).get("mode") != "plan":
        print("warn   {} 的 meta.mode 不是 \"plan\"".format(ns.plan_json))
    if (diff.get("meta") or {}).get("mode") != "diff":
        print("warn   {} 的 meta.mode 不是 \"diff\"".format(ns.diff_json))

    planned = {c["id"]: c["plan"] for c in plan.get("cards", []) if c.get("plan")}
    actual = changed_cards(diff)
    diff_ids = {c["id"] for c in diff.get("cards", [])}

    # remove 计划的满足条件：卡从 diff 画布消失，或还在但有 diff 标记
    def planned_done(cid, action):
        if action == "remove":
            return cid not in diff_ids or cid in actual
        return cid in actual

    matched = sorted(cid for cid, a in planned.items() if planned_done(cid, a))
    missed = sorted(cid for cid, a in planned.items() if not planned_done(cid, a))
    offplan = sorted(actual - set(planned))

    # 同底图检查：共有卡的 layout 必须一致
    plan_layout = {c["id"]: (c.get("layout") or {}) for c in plan.get("cards", [])}
    diff_layout = {c["id"]: (c.get("layout") or {}) for c in diff.get("cards", [])}
    layout_drift = sorted(cid for cid in set(plan_layout) & set(diff_layout)
                          if (plan_layout[cid].get("col"), plan_layout[cid].get("band"))
                          != (diff_layout[cid].get("col"), diff_layout[cid].get("band")))

    # 覆盖核对：git 里改了的文件必须出现在 diff 画布的某张卡上。
    # compare 只能看见画进画布的东西——这道检查防的是"改了但没画"（疏忽或隐瞒）
    uncovered = []
    if ns.repo:
        import subprocess
        r = subprocess.run(["git", "-C", ns.repo, "diff", "--name-only", "HEAD"],
                           capture_output=True, text=True)
        changed_files = {f.strip() for f in r.stdout.split("\n") if f.strip()}
        canvas_files = {(c.get("file") or "").split(":")[0] for c in diff.get("cards", [])}
        uncovered = sorted(f for f in changed_files if f not in canvas_files)

    # 计划线对照
    actual_pairs = wire_pairs(diff)
    wire_miss, wire_undeleted = [], []
    for pair in wire_pairs(plan, only_plan="add"):
        if pair not in actual_pairs:
            wire_miss.append(pair)
    for pair in wire_pairs(plan, only_plan="remove"):
        if pair in actual_pairs:
            wire_undeleted.append(pair)

    label = {"add": "新建", "modify": "将改", "remove": "将删"}
    print("== 同底图对照：{} vs {} ==".format(Path(ns.plan_json).name, Path(ns.diff_json).name))
    print("计划 {} 处卡片变更，实际动了 {} 张卡\n".format(len(planned), len(actual)))
    for cid in matched:
        print("  计划内     {} ({})".format(cid, label[planned[cid]]))
    for cid in offplan:
        print("  ⚠ 计划外    {} —— 计划没说要动这张卡".format(cid))
    for cid in missed:
        print("  ○ 计划未动  {} ({}) —— 计划说了但没发生".format(cid, label[planned[cid]]))
    for f, t, k in sorted(wire_miss):
        print("  ⚠ 计划线未落地  {} → {} ({})".format(f, t, k))
    for f, t, k in sorted(wire_undeleted):
        print("  ⚠ 计划删线未删  {} → {} ({})".format(f, t, k))
    for cid in layout_drift:
        print("  ⚠ 底图漂移  {} 的 layout 两边不一致（计划与 diff 应同底图）".format(cid))
    for f in uncovered:
        print("  ⚠ 画布未覆盖  {} —— git 里改了这个文件，diff 画布没有对应的卡".format(f))

    clean = not (offplan or missed or wire_miss or wire_undeleted or uncovered)
    print("\n结论：{}".format("实际与计划一致" if clean else "实际偏离了计划——上面 ⚠/○ 逐条核对"))

    if annotate:
        marks = {cid: "offplan" for cid in offplan}
        marks.update({cid: "missed" for cid in missed if cid in diff_ids})
        out = json.loads(json.dumps(diff))  # deep copy
        for c in out.get("cards", []):
            if c["id"] in marks:
                c["plan_delta"] = marks[c["id"]]
        Path(annotate).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("annotated → {}（{} 处对照徽标）".format(annotate, len(marks)))

    sys.exit(0 if clean else 2)


if __name__ == "__main__":
    main()
