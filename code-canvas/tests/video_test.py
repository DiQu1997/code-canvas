#!/usr/bin/env python3
"""video.py --check 的契约：脚本只能引用画布里真实的卡/行，sim 方块与道名要自洽。"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video  # noqa: E402

CANVAS = {"cards": [{"id": "run", "name": "schedule()", "file": "s.py:640", "lang": "py",
                     "code": "\n".join("line{}".format(i) for i in range(1, 21))}]}
GOOD = {"title": "t", "scenes": [
    {"kind": "title", "text": "T", "sub": "s", "beats": [{"say": "开场。" * 20}]},
    {"kind": "flow", "items": ["a", "b"], "beats": [{"say": "第一步。" * 20, "item": 1}]},
    {"kind": "sim", "lanes": ["running", "waiting"], "budget": {"name": "b", "total": 20},
     "items": [{"id": "R1", "lane": "running"}],
     "beats": [{"say": "扣预算。" * 20, "ops": [{"op": "consume", "n": 3}, {"op": "add", "item": "W1", "lane": "waiting"},
                                              {"op": "move", "item": "R1", "lane": "waiting", "at": 0},
                                              {"op": "mark", "item": "W1", "state": "good"}]}]},
    {"kind": "table", "cols": ["a", "b"], "beats": [{"say": "记账。" * 20, "row": ["1", "2"], "hl": [1]}]},
    {"kind": "code", "card": "run", "lines": [3, 12], "beats": [{"say": "看代码。" * 20, "hl": [4, 6]}]},
    {"kind": "recap", "items": ["x"], "beats": [{"say": "回顾。" * 20, "item": 0}]},
]}

results = []


def case(name, mutate, expect):
    v = copy.deepcopy(GOOD)
    mutate(v)
    E, _ = video.check(v, CANVAS)
    ok = (not E) if expect is None else any(expect in e for e in E)
    results.append(("PASS " if ok else "FAIL ") + name + ("" if ok else "  → " + "; ".join(E)))


case("well-formed script passes", lambda v: None, None)
case("code scene must reference a real card", lambda v: v["scenes"][4].update(card="ghost"), "不存在")
case("code lines must stay inside the card", lambda v: v["scenes"][4].update(lines=[15, 25]), "越界")
case("code scene capped at 16 lines", lambda v: v["scenes"][4].update(lines=[1, 20]), "≤16")
case("highlight must sit inside the shown lines", lambda v: v["scenes"][4]["beats"][0].update(hl=[1, 2]), "不在 lines")
case("sim op on a box not on screen", lambda v: v["scenes"][2]["beats"][0]["ops"].append({"op": "focus", "item": "R9"}), "不在画面上")
case("sim unknown op rejected", lambda v: v["scenes"][2]["beats"][0]["ops"].append({"op": "teleport", "item": "R1"}), "未知 op")
case("sim move to unknown lane", lambda v: v["scenes"][2]["beats"][0]["ops"].append({"op": "move", "item": "R1", "lane": "done"}), "不存在")
case("consume needs a budget", lambda v: v["scenes"][2].pop("budget"), "没有 budget")
case("table row width must match cols", lambda v: v["scenes"][3]["beats"][0].update(row=["1"]), "cols 有")
case("flow item index checked", lambda v: v["scenes"][1]["beats"][0].update(item=5), "item 越界")
case("every beat needs narration", lambda v: v["scenes"][0]["beats"][0].update(say=""), "缺 say")
print("\n".join(results))
sys.exit(1 if any(r.startswith("FAIL") for r in results) else 0)
