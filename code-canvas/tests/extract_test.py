#!/usr/bin/env python3
"""extract_test.py — 结构提取器的契约测试。

Hermetic 部分（永远跑）：提取正确性/逐字性、call 线行锚、diff 映射、
增量合并的叙事保留与丢弃、布局稳定、validate 干净。
环境部分（有对应 clone 才跑，否则 SKIP）：黄金样本交叉验证、性能红线。

用法: python3 tests/extract_test.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, ok):
    results.append(("PASS" if ok else "FAIL") + " " + name)


def run(args, cwd=None):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, cwd=cwd)


def sh(cmd, cwd):
    subprocess.run(cmd, shell=True, cwd=cwd, check=True, capture_output=True)


# ---------- hermetic：造一个真实 git 小仓库 ----------
tmp = Path(tempfile.mkdtemp(prefix="extract-"))
repo = tmp / "repo"
(repo / "pkg").mkdir(parents=True)
(repo / "pkg" / "a.py").write_text(
    "def entry(x):\n"
    "    y = helper(x)\n"
    "    return y + 1\n"
    "\n"
    "\n"
    "def helper(x):\n"
    "    return x * 2\n", encoding="utf-8")
(repo / "pkg" / "b.py").write_text(
    "class Store:\n"
    "    @property\n"
    "    def size(self):\n"
    "        return 0\n"
    "\n"
    "    def put(self, k, v):\n"
    "        self.check(k)\n"
    "        return v\n"
    "\n"
    "    def check(self, k):\n"
    "        assert k\n", encoding="utf-8")
sh("git init -q . && git add -A && git commit -qm base", repo)

out1 = tmp / "s1.json"
r = run([str(ROOT / "extract.py"), str(repo / "pkg"), "--repo-root", str(repo),
         "--out", str(out1)])
d1 = json.loads(out1.read_text(encoding="utf-8"))
cards = {c["id"]: c for c in d1["cards"]}

# 提取正确性
check("extracts all functions incl. methods", set(cards) == {"entry", "helper", "size", "put", "check"})
check("decorator included in span", cards["size"]["code"].startswith("    @property"))
check("code verbatim from source",
      cards["helper"]["code"] == "def helper(x):\n    return x * 2"
      and cards["helper"]["file"] == "pkg/a.py:6")
w = {(x["from"]["card"], x["to"]["card"]): x for x in d1["wires"]}
check("call wire entry→helper at rel line 2",
      ("entry", "helper") in w and w[("entry", "helper")]["from"]["line"] == 2)
check("method call wire put→check", ("put", "check") in w)
check("no self wire / no ambiguous wire", ("check", "check") not in w)
check("validate clean on structure", run([str(ROOT / "validate.py"), str(out1)]).returncode == 0)
check("every card has layout", all("layout" in c for c in d1["cards"]))

# ---------- 模拟 agent 叙事层 ----------
d1["regions"].append({"id": "A", "title": "故事线 A", "hue": "blue", "cards": ["entry", "helper"]})
d1["cards"] = list(cards.values())
cards["helper"]["terms"] = [{"line": 1, "token": "helper", "note": "翻倍器"}]
cards["entry"]["blocks"] = [{"name": "调用", "summary": "去翻倍", "lines": [2, 2]}]
cards["put"]["terms"] = [{"line": 2, "token": "check", "note": "先校验"}]
d1["notes"] = [
    {"id": "n-keep", "flavor": "intent", "tag": "N", "text": "留下",
     "anchor": {"card": "entry", "line": 1}, "place": {"side": "left", "of": "entry"}, "step": 0},
    {"id": "n-drop", "flavor": "intent", "tag": "N", "text": "该丢",
     "anchor": {"card": "put", "line": 2}, "place": {"side": "left", "of": "put"}, "step": 0},
]
d1["steps"] = [
    {"title": "好步", "caption": "还成立", "lines": [["entry", 2]], "focus": ["entry"]},
    {"title": "坏步", "caption": "引用了要变的卡", "lines": [["put", 2]], "focus": ["put"]},
]
narrated = tmp / "narrated.json"
narrated.write_text(json.dumps(d1, ensure_ascii=False), encoding="utf-8")
check("narrated canvas validates", run([str(ROOT / "validate.py"), str(narrated)]).returncode == 0)

# ---------- 改一个函数，增量重提取 ----------
(repo / "pkg" / "b.py").write_text(
    "class Store:\n"
    "    @property\n"
    "    def size(self):\n"
    "        return 0\n"
    "\n"
    "    def put(self, k, v):\n"
    "        self.check(k)\n"
    "        self.audit(k)\n"
    "        return v\n"
    "\n"
    "    def check(self, k):\n"
    "        assert k\n"
    "\n"
    "    def audit(self, k):\n"
    "        pass\n", encoding="utf-8")
out2 = tmp / "s2.json"
r2 = run([str(ROOT / "extract.py"), str(repo / "pkg"), "--repo-root", str(repo),
          "--out", str(out2), "--merge", str(narrated), "--diff"])
d2 = json.loads(out2.read_text(encoding="utf-8"))
c2 = {c["id"]: c for c in d2["cards"]}

check("unchanged card keeps narrative", c2["helper"].get("terms", [{}])[0].get("note") == "翻倍器"
      and c2["entry"].get("blocks", [{}])[0].get("name") == "调用")
check("changed card drops line-anchored narrative", "terms" not in c2["put"])
check("changed card reported for re-narration", "put" in r2.stderr and "待重讲" in r2.stderr)
check("new card appears", "audit" in c2)
check("layout stable for surviving cards",
      all(c2[i]["layout"] == cards[i]["layout"] for i in ("entry", "helper", "check")))
check("region survives with valid members",
      d2["regions"] and d2["regions"][0]["cards"] == ["entry", "helper"])
note_ids = {n["id"] for n in d2["notes"]}
check("note on unchanged card kept, on changed dropped",
      "n-keep" in note_ids and "n-drop" not in note_ids)
titles = [s["title"] for s in d2["steps"]]
check("step referencing changed card dropped", "好步" in titles and "坏步" not in titles)
check("diff mapped onto changed card", c2["put"].get("diff", {}).get("added") == [3])
check("merged canvas validates", run([str(ROOT / "validate.py"), str(out2)]).returncode == 0)

# ---------- 环境部分 ----------
nano = Path("/tmp/nano-vllm-check")
if nano.exists():
    out3 = tmp / "nano.json"
    run([str(ROOT / "extract.py"), str(nano / "nanovllm"), "--repo-root", str(nano / "nanovllm"),
         "--out", str(out3)])
    inv = json.loads(out3.read_text(encoding="utf-8"))["cards"]
    spans = {}
    for c in inv:
        f, s = c["file"].rsplit(":", 1)
        spans.setdefault(f, []).append((int(s), int(s) + len(c["code"].split("\n")) - 1))
    golden = json.loads((ROOT / "demo" / "nano-vllm.json").read_text(encoding="utf-8"))
    covered = total = 0
    for c in golden["cards"]:
        m = c.get("file", "")
        if ":" not in m:
            continue
        f, s = m.rsplit(":", 1)
        lo = int(s)
        hi = lo + len(c["code"].split("\n")) - 1
        total += 1
        if any(a <= lo and hi <= b for a, b in spans.get(f, [])) or \
           any(not (hi < a or lo > b) for a, b in spans.get(f, [])):
            covered += 1
    check("golden nano-vllm cards covered by extractor ({}/{})".format(covered, total),
          total > 0 and covered == total)
else:
    print("SKIP golden cross-check (no /tmp/nano-vllm-check)")

vllm_core = Path("/tmp/vllm/vllm/v1/core")
if vllm_core.exists():
    t0 = time.time()
    run([str(ROOT / "extract.py"), str(vllm_core), "--out", str(tmp / "core.json")])
    dt = time.time() - t0
    check("perf red line: v1/core < 1s ({:.2f}s)".format(dt), dt < 1.0)
else:
    print("SKIP perf red line (no /tmp/vllm)")

shutil.rmtree(tmp, ignore_errors=True)
print("\n".join(results))
sys.exit(1 if any(x.startswith("FAIL") for x in results) else 0)
