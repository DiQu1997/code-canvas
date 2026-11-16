#!/usr/bin/env python3
"""
check_research.py <research.json> --repo <仓库根>

调研闸门：research.json 是调研方交给呈现方的事实库（格式见 research-spec.md）。
呈现方不重读仓库，所以这里把能机械核对的都核一遍：必填项、每条依据 [文件, 行号, 符号]、
函数行段、模式词表与槽位、例子的每一步。0 ERROR 才放行，并把仓库当时的提交写进 repo.commit。

exit 0 通过；1 有 ERROR 或参数 / 格式错。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from check_refs import ref_ok

# 模式词表：pattern → 必填槽位。research-spec.md 第 3 节的表与此一一对应。
PATTERNS = {
    "queue": ["producer", "consumer", "capacity"],
    "stack": ["push", "pop"],
    "map": ["key", "value", "lifetime"],
    "tree": ["node", "key", "ops"],
    "linked": ["node", "links", "ops"],
    "ring": ["slots", "cursor"],
    "pool": ["unit", "alloc", "free", "exhausted"],
    "counter": ["what", "inc", "dec", "limit"],
    "log": ["append", "cursors"],
    "thread": ["runs", "blocks_on"],
    "coroutine": ["suspend", "resume", "state"],
    "switch": ["saved", "swap", "fresh"],
    "loop": ["stations", "exit"],
    "pipeline": ["stages"],
    "dispatch": ["on", "branches"],
    "statemachine": ["states", "transitions"],
    "call": ["caller", "callee"],
    "enqueue": ["from", "to", "via"],
    "callback": ["registered", "invoked"],
    "lock": ["protects", "holders"],
    "refcount": ["counted", "inc", "dec", "zero"],
    "evict": ["candidates", "order", "trigger"],
    "retry": ["condition", "backoff"],
    "other": ["missing"],
}
SHAPES = {"flow", "lifecycle", "loop", "operation", "transform", "structure"}


class Report:
    def __init__(self):
        self.errors, self.warns = [], []

    def e(self, msg):
        self.errors.append(msg)

    def w(self, msg):
        self.warns.append(msg)


def filled(v) -> bool:
    return bool(v.strip()) if isinstance(v, str) else bool(v)


def check(d: dict, repo: Path, R: Report) -> None:
    for k in ("question", "audience"):
        if not filled(d.get(k)):
            R.e("{}: 必填".format(k))

    def refs(where, lst, need=True):
        if not lst:
            if need:
                R.e("{}: 没有依据（refs）".format(where))
            return
        for r in lst:
            why = ref_ok(repo, r)
            if why:
                R.e("{}: 依据 {} 核不过——{}".format(where, json.dumps(r, ensure_ascii=False), why))

    sc = d.get("scope") or {}
    for k in ("covers", "default_path"):
        if not filled(sc.get(k)):
            R.e("scope.{}: 必填".format(k))
    refs("scope", sc.get("refs"))

    sh = d.get("shape") or {}
    if sh.get("kind") not in SHAPES:
        R.e("shape.kind: 必须是 {} 之一".format(" / ".join(sorted(SHAPES))))
    if not filled(sh.get("why")):
        R.e("shape.why: 必填")
    pr = d.get("protagonist") or {}
    for k in ("what", "instance"):
        if not filled(pr.get(k)):
            R.e("protagonist.{}: 必填".format(k))
    if not d.get("background"):
        R.w("background: 空——读者真的不缺任何背景吗")

    mods = {}
    for i, m in enumerate(d.get("modules") or []):
        w = "modules[{}]".format(m.get("id", i))
        for k in ("id", "name", "role", "not"):
            if not filled(m.get(k)):
                R.e("{}.{}: 必填".format(w, k))
        mods[m.get("id")] = m
        refs(w, m.get("refs"))
    if not 3 <= len(mods) <= 12:
        R.e("modules: 应有 3–12 个，现在 {} 个".format(len(mods)))

    funcs = {}
    for i, f in enumerate(d.get("functions") or []):
        w = "functions[{}]".format(f.get("id", i))
        for k in ("id", "name", "file", "does"):
            if not filled(f.get(k)):
                R.e("{}.{}: 必填".format(w, k))
        funcs[f.get("id")] = f
        if f.get("module") not in mods:
            R.e("{}: module {} 不在 modules 里".format(w, f.get("module")))
        ln = f.get("lines")
        if not (isinstance(ln, list) and len(ln) == 2 and all(isinstance(x, int) for x in ln) and 0 < ln[0] <= ln[1]):
            R.e("{}.lines: 应为 [起始行, 结束行]".format(w))
            continue
        p = repo / str(f.get("file", "")).lstrip("./")
        if not p.is_file():
            R.e("{}: 文件不存在 {}".format(w, f.get("file")))
            continue
        src = p.read_text(encoding="utf-8", errors="replace").split("\n")
        if ln[1] > len(src):
            R.e("{}: 行段 {} 越界（全文 {} 行）".format(w, ln, len(src)))
            continue
        # 行段要对准真实函数：名字的最后一段出现在行段开头几行（签名可能折行、带装饰器）
        short = re.split(r"[.:#]+", str(f.get("name", "")).strip("()"))[-1].strip("()")
        if short and short not in "\n".join(src[ln[0] - 1:ln[0] + 4]):
            R.e("{}: 行段 {} 的开头没有 {}——没对准函数".format(w, ln, short))
    if len(funcs) < 8:
        R.e("functions: 至少 8 个，现在 {} 个".format(len(funcs)))
    for fid, f in funcs.items():
        for c in f.get("calls") or []:
            if c not in funcs:
                R.w("functions[{}].calls: {} 不在 functions 里".format(fid, c))

    mechs = d.get("mechanisms") or []
    for i, m in enumerate(mechs):
        w = "mechanisms[{}]".format(m.get("id", i))
        for k in ("id", "name", "summary", "deviation"):
            if not filled(m.get(k)):
                R.e("{}.{}: 必填".format(w, k))
        if m.get("module") not in mods:
            R.e("{}: module {} 不在 modules 里".format(w, m.get("module")))
        pat = m.get("pattern")
        if pat not in PATTERNS:
            R.e("{}.pattern: {} 不在词表里（都不贴切就用 other）".format(w, pat))
        else:
            slots = m.get("slots") or {}
            for s in PATTERNS[pat]:
                if not filled(slots.get(s)):
                    R.e("{}: {} 模式缺槽位 {}".format(w, pat, s))
        refs(w, m.get("refs"))
    if len(mechs) < 2:
        R.e("mechanisms: 至少 2 个，现在 {} 个".format(len(mechs)))

    for i, h in enumerate(d.get("handoffs") or []):
        w = "handoffs[{}]".format(i)
        for k in ("from", "to"):
            if h.get(k) not in mods:
                R.e("{}.{}: {} 不在 modules 里".format(w, k, h.get(k)))
        refs(w, h.get("refs"))

    wk = d.get("walkthrough") or {}
    steps = wk.get("steps") or []
    if not filled(wk.get("setup")):
        R.e("walkthrough.setup: 必填（例子的设定，哪些值是假设的）")
    if not 8 <= len(steps) <= 30:
        R.e("walkthrough.steps: 应有 8–30 步，现在 {} 步".format(len(steps)))
    with_ref = 0
    for i, s in enumerate(steps):
        w = "walkthrough.steps[{}]".format(i + 1)
        if not filled(s.get("text")):
            R.e("{}.text: 必填".format(w))
        if s.get("function") and s["function"] not in funcs:
            R.e("{}: function {} 不在 functions 里".format(w, s["function"]))
        if s.get("ref"):
            with_ref += 1
            why = ref_ok(repo, s["ref"])
            if why:
                R.e("{}: 依据 {} 核不过——{}".format(w, json.dumps(s["ref"], ensure_ascii=False), why))
    if steps and with_ref * 10 < len(steps) * 6:
        R.e("walkthrough: 只有 {}/{} 步带依据，至少要六成".format(with_ref, len(steps)))

    for i, p in enumerate(d.get("predictions") or []):
        w = "predictions[{}]".format(i)
        for k in ("if", "then"):
            if not filled(p.get(k)):
                R.e("{}.{}: 必填".format(w, k))
        refs(w, p.get("refs"))
    if "open_questions" not in d:
        R.w("open_questions: 没写——真的全都查清了吗（查清了写空数组）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("research", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args()
    repo = a.repo.resolve()
    try:
        d = json.loads(a.research.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("读取失败:", e)
        return 1
    if not isinstance(d, dict):
        print("research.json 顶层应是对象")
        return 1
    R = Report()
    check(d, repo, R)
    for m in R.errors:
        print("ERROR", m)
    for m in R.warns:
        print("WARN ", m)
    print("{} errors, {} warnings · {} modules · {} mechanisms · {} functions · {} steps".format(
        len(R.errors), len(R.warns), len(d.get("modules") or []), len(d.get("mechanisms") or []),
        len(d.get("functions") or []), len((d.get("walkthrough") or {}).get("steps") or [])))
    if R.errors:
        return 1
    # 钉住提交：呈现方读的必须是同一份代码
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                             universal_newlines=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    if sha:
        d.setdefault("repo", {})["commit"] = sha
        a.research.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
