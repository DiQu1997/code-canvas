#!/usr/bin/env python3
"""embed_context.py 契约测试：正常嵌入 / 行漂移修正 / 对不上跳过 / 子目录根定位。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PASS = []


def check(name, ok):
    PASS.append(ok)
    print("{} {}".format("PASS" if ok else "FAIL", name))


def run_case(repo_layout, cards, repo_arg=None):
    t = Path(tempfile.mkdtemp())
    for rel, text in repo_layout.items():
        p = t / "repo" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    cv = {"meta": {}, "cards": cards, "wires": [], "notes": [],
          "steps": [{"title": "t", "fit": True}]}
    (t / "c.json").write_text(json.dumps(cv))
    r = subprocess.run([sys.executable, str(HERE / "embed_context.py"),
                        str(t / "c.json"), str(t / (repo_arg or "repo"))],
                       capture_output=True, text=True)
    return json.loads((t / "c.json").read_text()), r


d, r = run_case(
    {"a.py": "x=0\ndef f():\n    return 1\ny=2\n",
     "b.py": "pad\npad\ndef g():\n    return 2\n"},
    [{"id": "ok", "name": "f", "file": "a.py:2", "lang": "py", "code": "def f():\n    return 1"},
     {"id": "drift", "name": "g", "file": "b.py:1", "lang": "py", "code": "def g():\n    return 2"},
     {"id": "bad", "name": "z", "file": "a.py:1", "lang": "py", "code": "def nope():\n    pass"}])
check("正常卡嵌入", set(d.get("files", {})) == {"a.py", "b.py"})
check("行漂移机械修正", d["cards"][1]["file"] == "b.py:3")
check("对不上如实跳过", "跳过: bad" in r.stderr and r.returncode == 0)

d, r = run_case(
    {"sub-rs/core/m.py": "def h():\n    return 3\n"},
    [{"id": "s", "name": "h", "file": "core/m.py:1", "lang": "py",
      "code": "def h():\n    return 3"}])
check("仓库根自动定位到子目录", "core/m.py" in d.get("files", {})
      and "子目录" in r.stderr)

sys.exit(0 if all(PASS) else 1)
