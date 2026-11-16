#!/usr/bin/env python3
"""Review loop rules that do not need an agent: a checker verdict or a rebuttal only counts with a ref that
holds in the repo; an edit that breaks the gate is rolled back; a second reading must rule on every earlier finding."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import agent_cli      # noqa: E402
import reader_check   # noqa: E402
import review         # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name)
    FAILS += 0 if ok else 1


def finding(i, kind="mismatch", sev="minor", owner="presentation"):
    return {"id": i, "where": "卡 x", "kind": kind, "quote": "q", "problem": "p", "needs": "n", "owner": owner, "severity": sev}


with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    # the checker
    answer = {"checks": [
        {"id": "a", "verdict": "canvas_wrong", "fact": "返回 1", "refs": [["a.py", 2, "return 1"]]},
        {"id": "b", "verdict": "canvas_right", "fact": "没错", "refs": [["a.py", 2, "return 2"]]},   # the ref does not hold
        {"id": "c", "verdict": "nonsense", "fact": "", "refs": []}]}
    real = agent_cli.run
    agent_cli.run = lambda *x, **k: ("```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```", 0.5)
    F = [finding("a"), finding("b"), finding("c"), finding("d", kind="gap")]
    cost = review.run_check(F, repo / "canvas.json", repo, "m")
    agent_cli.run = real
    check("a verdict with a ref that holds is kept", F[0]["check"]["verdict"] == "canvas_wrong" and cost == 0.5)
    check("a verdict whose ref does not hold becomes cannot_tell", F[1]["check"]["verdict"] == "cannot_tell" and F[1]["check"]["refs"] == [])
    check("an unknown verdict is dropped", "check" not in F[2])
    check("a finding the reader could settle himself is not sent to the checker", "check" not in F[3])

    # resolutions
    rp = repo / "res.json"
    rp.write_text(json.dumps([
        {"id": "a", "status": "fixed", "note": "改了"},
        {"id": "b", "status": "rebutted", "note": "没问题", "refs": []},
        {"id": "c", "status": "rebutted", "note": "看这里", "refs": [["a.py", 1, "def f"]]},
        {"id": "d", "status": "done"}]), encoding="utf-8")
    res = review.load_resolutions(rp, repo)
    check("fixed is accepted", res.get("a", {}).get("status") == "fixed")
    check("a rebuttal with no code ref is not a resolution", "b" not in res)
    check("a rebuttal with a ref that holds is accepted", res.get("c", {}).get("status") == "rebutted")
    check("an unknown status is ignored", "d" not in res)
    check("a missing resolutions file means no resolutions", review.load_resolutions(repo / "nope.json", repo) == {})

    # an edit that breaks the gate is rolled back
    f = repo / "zoom.json"
    f.write_text("before", encoding="utf-8")
    ok = review.guarded([f], lambda: f.write_text("after", encoding="utf-8"), lambda: False)
    check("a failed edit is rolled back", not ok and f.read_text(encoding="utf-8") == "before" and not (repo / "zoom.json.before-review").exists())
    ok = review.guarded([f], lambda: f.write_text("after", encoding="utf-8"), lambda: True)
    check("a good edit is kept", ok and f.read_text(encoding="utf-8") == "after")

    def boom():
        raise RuntimeError("agent died")
    f.write_text("before", encoding="utf-8")
    check("an agent that dies leaves the file as it was", not review.guarded([f], boom, lambda: True) and f.read_text(encoding="utf-8") == "before")

# the reader's answer
good = {"retell": ["a"], "findings": [finding("x")]}
check("a well-formed list has no format problems", reader_check.clean(good) == [])
bad = {"retell": [], "findings": [dict(finding("x"), severity="8/10")]}
check("a severity outside the three levels is rejected", len(reader_check.clean(bad)) == 1)
second = dict(good, previous=[{"id": "r1-1", "resolved": True, "note": "ok"}])
check("a second reading must rule on every earlier finding", reader_check.clean(second, ["r1-1"]) == [] and len(reader_check.clean(second, ["r1-1", "r1-2"])) == 1)
md = reader_check.to_md(dict(second, findings=[dict(finding("r2-1"), check={"verdict": "canvas_wrong", "fact": "f"}, resolution={"status": "fixed", "note": "n"})]), "问")
check("the markdown shows the earlier findings, the check and the resolution", "解决了 1 条" in md and "canvas_wrong" in md and "fixed" in md)

sys.exit(1 if FAILS else 0)
