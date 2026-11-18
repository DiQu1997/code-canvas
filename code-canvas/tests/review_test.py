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


# the loop: a finding the second reading says is still open goes round again; nits alone stop the loop
def run_loop(readings):
    """Drive review.main() with canned readers. `readings` is the list of reader answers, in order."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for n in ("research.json", "zoom.json", "scene.json", "zoom.data.json"):
            (work / n).write_text(json.dumps({"audience": "新手"}), encoding="utf-8")
        calls = {"read": 0, "fix": 0}

        def fake_read(canvas, ask, audience, model, zoom=False, previous=None, round_no=1):
            d = json.loads(json.dumps(readings[calls["read"]]))
            calls["read"] += 1
            for i, f in enumerate(d["findings"], 1):
                f["id"] = "r{}-{}".format(round_no, i)
            return d, 1.0

        def fake_agent(prompt, cwd, model, tools="r", add_dirs=(), timeout=0, cli=None):
            calls["fix"] += 1
            ids = [l.split("（")[0][2:] for l in prompt.split("\n") if l.startswith("- r")]
            (work / "resolutions.present.json").write_text(json.dumps([{"id": i, "status": "fixed", "note": "改了"} for i in ids]), encoding="utf-8")
            return "DONE", 2.0
        saved = (reader_check.read, agent_cli.run, review.run_check, review.tool_ok, sys.argv)
        reader_check.read, agent_cli.run, review.run_check, review.tool_ok = fake_read, fake_agent, lambda *x: 0.0, lambda *x: True
        sys.argv = ["review.py", "--work", str(work), "--repo", str(work), "--ask", "问"]
        try:
            rc = review.main()
        finally:
            reader_check.read, agent_cli.run, review.run_check, review.tool_ok, sys.argv = saved
        return rc, calls, json.loads((work / "review.json").read_text(encoding="utf-8"))


import contextlib, io  # noqa: E402
quiet = contextlib.redirect_stdout(io.StringIO())
with quiet:
    rc, calls, r = run_loop([{"retell": [], "findings": [finding("", sev="minor"), finding("", sev="minor")]}])
check("only nits: nothing is sent back and nobody reads twice", rc == 0 and calls == {"read": 1, "fix": 0} and r["rounds"][0]["todo"] == 0)

with quiet:
    rc, calls, r = run_loop([
        {"retell": [], "findings": [finding("", sev="misleads"), finding("", sev="minor")]},
        {"retell": [], "findings": [finding("", sev="minor")], "previous": [{"id": "r1-1", "resolved": True, "note": "ok"}]}])
check("one serious finding, solved: one round of fixes, two readings, then stop on nits",
      calls == {"read": 2, "fix": 1} and r["rounds"][0]["resolved"] == 1 and r["rounds"][1]["todo"] == 0)

with quiet:
    rc, calls, r = run_loop([
        {"retell": [], "findings": [finding("", sev="misleads")]},
        {"retell": [], "findings": [finding("", sev="blocks")], "previous": [{"id": "r1-1", "resolved": False, "note": "还有两处没改"}]},
        {"retell": [], "findings": [finding("", sev="misleads")], "previous": [{"id": "r1-1", "resolved": True, "note": "ok"}, {"id": "r2-1", "resolved": True, "note": "ok"}]}])
check("a finding still open goes round again together with the new serious one",
      calls == {"read": 3, "fix": 2} and r["rounds"][0]["still_open"] == ["r1-1"] and r["rounds"][1]["todo"] == 2 and r["rounds"][1]["carried_over"] == 1)
check("after two rounds it stops and says what is left", r["rounds"][-1]["round"] == "left" and r["rounds"][-1]["serious"] == ["r3-1"])
check("the cost of every agent is added up", r["cost_usd"] == 3 * 1.0 + 2 * 2.0)

sys.exit(1 if FAILS else 0)
