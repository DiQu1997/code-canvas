#!/usr/bin/env python3
"""
review.py --work <工作目录> --repo <仓库根> --ask "读者的原话" [--model claude-opus-5-5]

独立检查一张缩放图，并把问题派回去改。工作目录里要有 research.json、zoom.json（和 scene.json）。

  1. 读者（看不到仓库，带着提问）交问题清单                    → findings.json
  2. 核查者（能看仓库）把读者只能怀疑的那几条对着代码判定真假     → 每条的 check
  3. 归调研方的问题：一个调研 agent 回到代码里查清，更新 research.json（过 check_research）
  4. 归呈现方的问题 + 核查判定画布错了的：一个呈现 agent 改 zoom.json / scene.json（过 build_zoom）
  5. 读者复核：上一轮每条是否真的解决，再列新问题                → findings2.json
  6. 还有没改干净的、或者新的严重问题：再走一遍 2–5（最多两轮）。只剩小瑕疵就停  → review.json

每条问题都有下落（resolution）：fixed 改了 / rebutted 用代码依据反驳 / wontfix 说明理由不改 / unaddressed 没人管。
改坏了（闸门或构建不过）就恢复改之前的文件。每个 agent 都是新起的，只读交接文件和它需要的那几行代码。
exit 0：检查跑完（不管发现多少问题）；1：参数错或读者没交回清单。
"""
import argparse
import functools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import agent_cli
import reader_check
from check_refs import ref_ok

HERE = Path(__file__).resolve().parent
print = functools.partial(print, flush=True)   # progress goes to the job log as it happens
VERDICTS = ("canvas_wrong", "canvas_right", "cannot_tell")
STATUSES = ("fixed", "rebutted", "wontfix")
MAX_ROUNDS = 2   # 改一轮、复核；还有严重问题或没改干净的，再改一轮

CHECK = """你是核查者。当前目录是仓库。一位只看得到画布、看不到仓库的读者，对画布里的几处说法起了疑心；
你的任务是对着真实代码，逐条判定谁对。画布的数据在 {canvas}（cards[].code 是它收录的源码）。

{items}

每条去读相关的代码（画布收录的那几行不够就读文件的其余部分），然后判定：
- canvas_wrong：画布说错了。fact 写代码实际是怎么做的。
- canvas_right：画布没错，是读者看不到全貌。fact 写清楚为什么没错（这句话会被补进画布）。
- cannot_tell：代码里判定不了（比如取决于运行时）。fact 写卡在哪。
canvas_wrong 和 canvas_right 必须带依据 refs：[文件（相对仓库根）, 行号, 该行 ±3 行内出现的一段原文]。

只输出一个 JSON 代码块：

```json
{{"checks": [{{"id": "", "verdict": "", "fact": "", "refs": [["path/to/file", 123, "symbol"]]}}]}}
```
"""

FIX_RESEARCH = """阅读 {skill}/research-spec.md。你是调研方。调研文件是 {work}/research.json（已经通过过机械核对），
仓库在当前目录。呈现方照它做了一张画布；读者和核查者交回了问题，其中下面几条是事实层面的——调研里缺了或错了：

{items}

逐条回到代码里查清，更新 research.json：补函数、补机制、补预测、改掉错的地方、把核查者查到的事实写进去。
只动和这些问题有关的部分。改完跑 python3 {skill}/check_research.py {work}/research.json --repo . 到 0 ERROR。

然后把每条的下落写进 {work}/resolutions.research.json：
[{{"id": "", "status": "fixed / rebutted / wontfix", "note": "查到了什么、写在 research.json 的哪里", "refs": [["文件", 行号, "符号"]]}}]
fixed：查清并写进了调研。rebutted：问题不成立，note 写理由，必须带 refs。wontfix：超出读者提问的范围，note 写理由。
完成后打印 DONE。"""

FIX_PRESENT = """阅读 {skill}/zoom-spec.md 和 {skill}/scene-spec.md。你是呈现方。画布是 {work}/zoom.json 和 {work}/scene.json，
调研是 {work}/research.json（调研方刚按问题清单补过，以它为准），仓库在当前目录。读者的提问是：

  {ask}

读者和核查者交回了问题。下面是要你处理的：

{items}

逐条改画布。核查者判定 canvas_wrong 的，按它给的事实改；判定 canvas_right 的，把它给的理由补进画布，让下一个读者不再起疑。
故事里前后矛盾的地方，对着代码把顺序和数值重新理一遍，不要只改措辞。
**同一个说法在画布里通常不止出现一次**：overview、模块的 oneliner / info（role、not、fields、methods、pitfalls）、anatomy 里部件的
label 和 sub、channels 的 label、卡的 blocks 和伪代码 steps、scene 的 intro 和每一步的 text / value，都可能写着同一句话。
每改一条之前，先在 zoom.json 和 scene.json 里把这个说法的所有出现位置搜出来（按关键词 grep，不要只找被点名的那一处），一起改掉；
下落的 note 里列出你改了哪几处。只动和这些问题有关的部分。
改完跑 python3 {skill}/build_zoom.py {work}/zoom.json --repo . --out {work}/zoom.html 到 0 ERROR；改过画面的步骤，
用 node {skill}/zoom_shot.js {work}/zoom.html {work}/shots 截图看一眼。

然后把每条的下落写进 {work}/resolutions.present.json：
[{{"id": "", "status": "fixed / rebutted / wontfix", "note": "改了哪里（卡 id / 第几步）", "refs": []}}]
rebutted 必须带代码依据 refs。完成后打印 DONE。"""


def item_text(f: dict) -> str:
    s = "- {}（{} · {}）\n  原话：{}\n  问题：{}\n  需要：{}".format(
        f["id"], f.get("where", ""), f.get("kind", ""), f.get("quote") or "（无）", f.get("problem", ""), f.get("needs", ""))
    c = f.get("check")
    if c:
        s += "\n  核查者的判定：{} —— {}".format(c["verdict"], c.get("fact", ""))
    r = f.get("resolution")
    if r:
        s += "\n  调研方的下落：{} —— {}".format(r["status"], r.get("note", ""))
    if f.get("reopened"):
        s += "\n  上一轮改过，但读者复核说没解决：{}".format(f["reopened"])
    return s


def run_check(findings: list, canvas: Path, repo: Path, model: str) -> float:
    """Ask the checker about the findings a reader cannot settle; attach each verdict as finding['check']."""
    todo = [f for f in findings if f["kind"] in reader_check.NEEDS_CHECK]
    if not todo:
        return 0.0
    repo = repo.resolve()
    prompt = CHECK.format(canvas=canvas, items="\n".join(item_text(f) for f in todo))
    text, cost = agent_cli.run(prompt, repo, model, add_dirs=[canvas.parent])
    got = {c.get("id"): c for c in agent_cli.last_json(text, "checks")["checks"] if isinstance(c, dict)}
    for f in todo:
        c = got.get(f["id"])
        if not c or c.get("verdict") not in VERDICTS:
            continue
        refs = [r for r in c.get("refs") or [] if ref_ok(repo, r) is None]
        verdict = c["verdict"]
        if verdict != "cannot_tell" and not refs:   # a verdict with no ref that holds is only an opinion
            verdict = "cannot_tell"
        f["check"] = {"verdict": verdict, "fact": str(c.get("fact") or ""), "refs": refs}
    return cost


def load_resolutions(path: Path, repo: Path) -> dict:
    repo = repo.resolve()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("status") not in STATUSES:
            continue
        refs = [x for x in r.get("refs") or [] if ref_ok(repo, x) is None]
        status = r["status"]
        if status == "rebutted" and not refs:   # a rebuttal has to point at code
            continue
        out[r.get("id")] = {"status": status, "note": str(r.get("note") or ""), "refs": refs}
    return out


def guarded(files, run, ok) -> bool:
    """Run an edit; if `ok()` is false afterwards (or the edit raised), put the files back."""
    backups = [(f, f.with_name(f.name + ".before-review")) for f in files if f.exists()]
    for f, b in backups:
        shutil.copyfile(str(f), str(b))
    good = False
    try:
        run()
        good = ok()
    except (RuntimeError, ValueError, subprocess.SubprocessError) as e:
        print("  edit failed:", str(e)[:300])
    if not good:
        for f, b in backups:
            shutil.copyfile(str(b), str(f))
    for _, b in backups:
        b.unlink()
    return good


def tool_ok(args, cwd) -> bool:
    r = subprocess.run([sys.executable] + [str(x) for x in args], cwd=str(cwd), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    if r.returncode != 0:
        print("  " + r.stdout.strip().replace("\n", "\n  ")[-900:])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--ask", required=True)
    ap.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args()
    work, repo = a.work.resolve(), a.repo.resolve()
    rj, zj, sj, data = work / "research.json", work / "zoom.json", work / "scene.json", work / "zoom.data.json"
    build = [HERE / "build_zoom.py", zj, "--repo", repo, "--out", work / "zoom.html"]
    if not (rj.exists() and zj.exists()):
        print("需要 research.json 和 zoom.json")
        return 1
    if not tool_ok(build, repo):
        print("画布构建不过，没法检查")
        return 1
    audience = str(json.loads(rj.read_text(encoding="utf-8")).get("audience") or "有经验的工程师")
    costs = {}

    # the reader
    try:
        d, costs["reader"] = reader_check.read(data, a.ask, audience, a.model, zoom=True)
    except (ValueError, RuntimeError) as e:
        print("读者没交回清单：", e)
        return 1
    (work / "findings.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    fresh, carried, rounds = d["findings"], [], []
    serious = lambda f: f["severity"] != "minor" or (f.get("check") or {}).get("verdict") in ("canvas_wrong", "canvas_right")
    sev = lambda fs: {s: sum(1 for f in fs if f["severity"] == s) for s in reader_check.SEVERITY}

    for n in range(1, MAX_ROUNDS + 1):
        # the checker rules on what the reader could only suspect
        try:
            costs["checker%d" % n] = run_check(fresh, data, repo, a.model)
        except (ValueError, RuntimeError) as e:
            print("checker failed:", str(e)[:300])
        for f in fresh:
            if (f.get("check") or {}).get("verdict") == "canvas_wrong":   # a confirmed error is never a nit
                f["severity"] = "misleads"
        R = {"round": n, "new_findings": len(fresh), "severity": sev(fresh), "carried_over": len(carried),
             "checked": {v: sum(1 for f in fresh if (f.get("check") or {}).get("verdict") == v) for v in VERDICTS}}
        rounds.append(R)
        print("round {}: {} new findings {}, {} carried over, checker {}".format(n, len(fresh), R["severity"], len(carried), R["checked"]))

        # what has to be dealt with: what the last reading says is still open, everything that is not a nit,
        # and everything the checker ruled on
        todo = carried + [f for f in fresh if serious(f)]
        R["todo"] = len(todo)
        if not todo:
            break
        for f in todo:
            f.pop("resolution", None)

        # research: facts that are missing or wrong, unless the checker has already supplied the fact
        for_research = [f for f in todo if f["owner"] == "research" and (f.get("check") or {}).get("verdict") not in ("canvas_wrong", "canvas_right")]
        if for_research:
            rp = work / "resolutions.research.json"
            if rp.exists():
                rp.unlink()

            def edit():
                _, costs["research_fix%d" % n] = agent_cli.run(
                    FIX_RESEARCH.format(skill=HERE, work=work, items="\n".join(item_text(f) for f in for_research)),
                    repo, a.model, tools=None, add_dirs=[work, HERE], timeout=2400)
            if guarded([rj], edit, lambda: tool_ok([HERE / "check_research.py", rj, "--repo", repo], repo)):
                res = load_resolutions(rp, repo)
                for f in for_research:
                    if f["id"] in res:
                        f["resolution"] = res[f["id"]]

        # presentation: everything in todo ends up on the canvas, including what research just found
        pp = work / "resolutions.present.json"
        if pp.exists():
            pp.unlink()

        def edit():
            _, costs["present_fix%d" % n] = agent_cli.run(
                FIX_PRESENT.format(skill=HERE, work=work, ask=a.ask, items="\n".join(item_text(f) for f in todo)),
                repo, a.model, tools=None, add_dirs=[work, HERE], timeout=3000)
        if guarded([zj, sj], edit, lambda: tool_ok(build, repo)):
            pres = load_resolutions(pp, repo)
            for f in todo:
                if f["id"] in pres:
                    f["resolution"] = pres[f["id"]]
        else:
            tool_ok(build, repo)   # rebuild the restored canvas so zoom.data.json matches zoom.json again
        for f in todo:
            f.setdefault("resolution", {"status": "unaddressed", "note": "", "refs": []})
        R["resolutions"] = {s: sum(1 for f in todo if f["resolution"]["status"] == s) for s in STATUSES + ("unaddressed",)}
        (work / "findings.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

        # the reader again: was each one really solved, and what is new
        try:
            nxt, costs["reader%d" % (n + 1)] = reader_check.read(data, a.ask, audience, a.model, zoom=True, previous=todo, round_no=n + 1)
        except (ValueError, RuntimeError) as e:
            print("reading after round {} failed: {}".format(n, str(e)[:300]))
            break
        out = work / "findings{}.json".format(n + 1)
        out.write_text(json.dumps(nxt, ensure_ascii=False, indent=1), encoding="utf-8")
        out.with_suffix(".md").write_text(reader_check.to_md(nxt, a.ask), encoding="utf-8")
        verdict = {p["id"]: p for p in nxt.get("previous") or []}
        carried = [f for f in todo if not verdict.get(f["id"], {}).get("resolved")]
        for f in carried:
            f["reopened"] = str(verdict.get(f["id"], {}).get("note") or "")
        fresh = nxt["findings"]
        R["resolved"], R["still_open"] = len(todo) - len(carried), [f["id"] for f in carried]
        print("round {}: {} of {} resolved; still open {}".format(n, R["resolved"], len(todo), R["still_open"]))
    else:
        # ran out of rounds: say what is left rather than pretend it is clean
        left = [f for f in fresh if f["severity"] != "minor"]
        rounds.append({"round": "left", "still_open": [f["id"] for f in carried], "new_findings": len(fresh),
                       "severity": sev(fresh), "serious": [f["id"] for f in left]})

    (work / "findings.md").write_text(reader_check.to_md(d, a.ask), encoding="utf-8")
    summary = {"ask": a.ask, "audience": audience, "costs": costs, "cost_usd": round(sum(costs.values()), 4), "rounds": rounds}
    (work / "review.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
