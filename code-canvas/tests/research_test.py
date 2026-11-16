#!/usr/bin/env python3
"""check_research.py contract: a well-formed research passes and gets the commit pinned;
a wrong ref, a function range that misses the function, a pattern outside the vocabulary,
a missing slot and a missing default_path are each an ERROR."""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, ok):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name)
    FAILS += 0 if ok else 1


SRC = "\n".join(
    ["class Tree:", "    def match(self, key):", "        return self.walk(key)", "",
     "    def walk(self, key):", "        node = self.root", "        return node", "",
     "    def split(self, node, n):", "        return node", ""]
    + ["def f%d(x):\n    return x\n" % i for i in range(8)]) + "\n"


def base():
    fn = [{"id": "match", "name": "Tree.match", "file": "tree.py", "lines": [2, 3], "module": "tree", "does": "查", "calls": ["walk"]},
          {"id": "walk", "name": "Tree.walk", "file": "tree.py", "lines": [5, 7], "module": "tree", "does": "走"},
          {"id": "split", "name": "Tree.split", "file": "tree.py", "lines": [9, 10], "module": "tree", "does": "分裂"}]
    for i in range(5):
        fn.append({"id": "f%d" % i, "name": "f%d" % i, "file": "tree.py", "lines": [12 + i * 3, 13 + i * 3], "module": "sched", "does": "x"})
    mod = lambda i, n: {"id": i, "name": n, "role": "r", "not": "n", "refs": [["tree.py", 1, "class Tree"]]}
    return {
        "question": "树怎么工作", "audience": "新手",
        "scope": {"covers": "tree.py", "default_path": "默认就是 Tree", "refs": [["tree.py", 1, "class Tree"]]},
        "shape": {"kind": "structure", "why": "问的是数据结构"},
        "protagonist": {"what": "树", "instance": "key=[1,2]"},
        "background": [{"term": "树", "explain": "…"}],
        "modules": [mod("tree", "Tree"), mod("sched", "S"), mod("pool", "P")],
        "mechanisms": [
            {"id": "m1", "pattern": "tree", "module": "tree", "name": "匹配", "summary": "s", "deviation": "d",
             "slots": {"node": "n", "key": "k", "ops": "o"}, "refs": [["tree.py", 2, "match"]]},
            {"id": "m2", "pattern": "other", "module": "pool", "name": "别的", "summary": "s", "deviation": "d",
             "slots": {"missing": "缺一个什么"}, "refs": [["tree.py", 9, "split"]]}],
        "functions": fn,
        "handoffs": [{"from": "sched", "to": "tree", "how": "调用", "refs": [["tree.py", 3, "walk"]]}],
        "walkthrough": {"setup": "key=[1,2]", "steps": [
            {"text": "第 %d 步" % i, "function": "walk", "ref": ["tree.py", 6, "self.root"]} for i in range(8)]},
        "predictions": [], "open_questions": [],
    }


def run(d, repo):
    p = repo / "research.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "check_research.py"), str(p), "--repo", str(repo)],
                       capture_output=True, universal_newlines=True)
    return r.returncode, r.stdout, json.loads(p.read_text(encoding="utf-8"))


with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    (repo / "tree.py").write_text(SRC, encoding="utf-8")
    for c in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", td] + c, check=True)
    rc, out, d = run(base(), repo)
    check("well-formed research passes", rc == 0 and "0 errors" in out)
    check("the commit is pinned on pass", len((d.get("repo") or {}).get("commit", "")) == 40)

    def bad(name, mutate, needle):
        x = base()
        mutate(x)
        rc, out, d = run(x, repo)
        check(name, rc == 1 and needle in out and "repo" not in d)

    bad("a ref whose symbol is not near the line is an error",
        lambda x: x["mechanisms"][0]["refs"].__setitem__(0, ["tree.py", 9, "match"]), "核不过")
    bad("a function range that misses the function is an error",
        lambda x: x["functions"][1].__setitem__("lines", [6, 7]), "没对准函数")
    bad("a pattern outside the vocabulary is an error",
        lambda x: x["mechanisms"][0].__setitem__("pattern", "radix"), "不在词表里")
    bad("a missing slot is an error",
        lambda x: x["mechanisms"][0]["slots"].pop("key"), "缺槽位 key")
    bad("scope.default_path is required",
        lambda x: x["scope"].__setitem__("default_path", ""), "scope.default_path")
    bad("a walkthrough step pointing at an unknown function is an error",
        lambda x: x["walkthrough"]["steps"][0].__setitem__("function", "nope"), "不在 functions 里")
    bad("too few steps with a ref is an error",
        lambda x: [s.pop("ref") for s in x["walkthrough"]["steps"][:5]], "至少要六成")

sys.exit(1 if FAILS else 0)
