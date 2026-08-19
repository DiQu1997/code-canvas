#!/usr/bin/env python3
"""run.py — the zero-context exam harness for code-canvas.

An "exam" gives a fresh agent (claude -p, no conversation context — only the
skill files, a repo, and one realistic user sentence) a canvas-authoring task,
then machine-grades the output. Methodology: eval/README.md.

Usage:
  python3 run.py list                              # show exams
  python3 run.py exam <id> [--dry-run] [--cli "claude -p"]
  python3 run.py grade <out_dir> --repo <repo_dir> [--mode orientation|deep]

`exam` clones the repo (cached under eval/.repos), composes the prompt from
prompt-template.md, launches the examinee CLI, then grades. `grade` scores an
existing output directory: validate.py result, card→source traceability
(token-stream, whitespace/`//`-continuation tolerant), structure budgets, and
screenshot presence. Writes <out_dir>/report.md.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parent
CHROMIUM = os.environ.get("CANVAS_EVAL_CHROMIUM", "/opt/pw-browsers/chromium")


def load_exams():
    return {e["id"]: e for e in json.loads((HERE / "exams.json").read_text(encoding="utf-8"))}


# ---------------- grading ----------------

def norm(s: str) -> str:
    # whitespace-insensitive; tolerate rewrap continuation markers that
    # disclosed 保序换行重排 inserts: `//`（Rust/JS 注释）、`#`（Python 注释）、
    # `\`（Python 代码续行）。两侧等量删除，比较意义不变。
    return re.sub(r"\s+", "", s).replace("//", "").replace("#", "").replace("\\", "")


def grade(out_dir: Path, repo: Path, mode: str | None) -> dict:
    r = {"out": str(out_dir), "checks": [], "verdict": "FAIL"}

    def add(name, ok, detail=""):
        r["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    cj = out_dir / "canvas.json"
    if not cj.exists():
        add("canvas.json 存在", False, "缺失")
        return r
    add("canvas.json 存在", True)

    v = subprocess.run([sys.executable, str(SKILL / "validate.py"), str(cj)],
                       capture_output=True, text=True)
    n_err = len([l for l in v.stdout.splitlines() if l.startswith("ERROR")])
    n_warn = len([l for l in v.stdout.splitlines() if l.startswith("warn")])
    add("validate 无 ERROR", n_err == 0, f"{n_err} errors, {n_warn} warnings")

    d = json.loads(cj.read_text(encoding="utf-8"))
    cards = d.get("cards", [])

    traced = missing = broken = 0
    for c in cards:
        if c.get("kind") in ("state", "district"):
            continue  # 叙事/索引卡：不承诺 file:line 逐字契约，不进溯源分母
        m = re.match(r"([^:]+):(\d+)$", c.get("file", "") or "")
        if not m:
            missing += 1
            continue
        src_path = repo / m.group(1)
        if not src_path.exists():
            broken += 1
            continue
        start = int(m.group(2))
        src = src_path.read_text(errors="replace").split("\n")
        window = norm("\n".join(src[start - 1: start - 1 + len(c["code"].split("\n")) + 60]))
        if window.startswith(norm(c["code"])):
            traced += 1
        else:
            broken += 1
    total = traced + missing + broken  # 只计承诺逐字契约的代码卡
    ratio = traced / total if total else 0
    add("代码可溯源 ≥90%", ratio >= 0.9,
        f"{traced}/{total} 可溯源，{missing} 张无行号标注，{broken} 张对不上")

    n_steps = len(d.get("steps", []))
    add("有故事线步骤（≥3）", n_steps >= 3, f"{n_steps} 步")
    if mode == "orientation":
        add("领航图卡数 ≤9", total <= 9, f"{total} 张")
    elif mode == "deep":
        add("深潜图卡数 ≤16", total <= 16, f"{total} 张")

    shots = list(out_dir.rglob("*.png"))
    add("有自检截图", len(shots) >= 1, f"{len(shots)} 张")
    html = out_dir / "canvas.html"
    add("canvas.html 已渲染", html.exists())

    hard = [c for c in r["checks"] if c["name"] in
            ("canvas.json 存在", "validate 无 ERROR", "代码可溯源 ≥90%", "canvas.html 已渲染")]
    r["verdict"] = "PASS" if all(c["ok"] for c in hard) else "FAIL"
    if r["verdict"] == "PASS" and not all(c["ok"] for c in r["checks"]):
        r["verdict"] = "PASS (warn)"
    return r


def write_report(r: dict, out_dir: Path):
    lines = [f"# 评分：{r['verdict']}", ""]
    for c in r["checks"]:
        lines.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}" + (f" — {c['detail']}" if c["detail"] else ""))
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------- exam ----------------

def run_exam(exam: dict, cli: str, dry: bool):
    repo_dir = HERE / ".repos" / exam["id"].split("-")[0]
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", exam["repo"], str(repo_dir)], check=True)
    out_dir = HERE / "runs" / f"{exam['id']}-{time.strftime('%m%d-%H%M')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = (HERE / "prompt-template.md").read_text(encoding="utf-8").format(
        skill_dir=SKILL, task=exam["task"], repo_dir=repo_dir.resolve(),
        audience=exam["audience"], out_dir=out_dir.resolve(), chromium=CHROMIUM)
    if dry:
        print(f"--- 考题 {exam['id']} 的 examinee prompt ---\n{prompt}")
        return
    print(f"[exam {exam['id']}] examinee 启动（可能要 20–40 分钟）…")
    t0 = time.time()
    proc = subprocess.run([*shlex.split(cli), prompt], capture_output=True, text=True, timeout=3600)
    (out_dir / "examinee-report.md").write_text(proc.stdout or "", encoding="utf-8")
    print(f"[exam {exam['id']}] 完成，用时 {int(time.time() - t0)}s；考生报告存 examinee-report.md")
    r = grade(out_dir, repo_dir, exam.get("mode"))
    write_report(r, out_dir)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pe = sub.add_parser("exam")
    pe.add_argument("id")
    pe.add_argument("--cli", default="claude -p")
    pe.add_argument("--dry-run", action="store_true")
    pg = sub.add_parser("grade")
    pg.add_argument("out_dir", type=Path)
    pg.add_argument("--repo", type=Path, required=True)
    pg.add_argument("--mode", choices=["orientation", "deep"], default=None)
    a = p.parse_args()
    exams = load_exams()
    if a.cmd == "list":
        for e in exams.values():
            print(f"{e['id']:16} {e['mode']:12} {e['repo']}")
    elif a.cmd == "exam":
        run_exam(exams[a.id], a.cli, a.dry_run)
    else:
        write_report(grade(a.out_dir, a.repo, a.mode), a.out_dir)


if __name__ == "__main__":
    main()
