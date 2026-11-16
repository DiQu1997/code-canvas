"""Run one headless agent (the claude CLI) and get back its text and what it cost.

Shared by the reader, the checker and the review loop, which all start a fresh agent with a prompt,
a working directory and a set of tools.
"""
import json
import os
import subprocess

READ_ONLY = "Read,Grep,Glob"


def run(prompt, cwd, model, tools=READ_ONLY, add_dirs=(), timeout=1800, cli=None):
    """tools: a comma-separated allow-list, or None for every tool (the agent may edit files and run commands).
    Returns (text, cost_usd). Raises RuntimeError when the agent fails."""
    cmd = [cli or os.environ.get("READER_CLI", "claude"), "-p", prompt, "--model", model, "--output-format", "json"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    cmd += ["--allowedTools", tools] if tools else ["--dangerously-skip-permissions"]
    r = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=timeout)
    try:
        env = json.loads(r.stdout)
    except ValueError:
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError("agent failed: " + (r.stderr or r.stdout)[-600:])
        return r.stdout, 0.0   # a CLI that prints plain text (a stub, or another engine)
    if env.get("is_error"):
        raise RuntimeError("agent failed: " + str(env.get("result"))[:600])
    return str(env.get("result") or ""), float(env.get("total_cost_usd") or 0)


def last_json(text, key):
    """The last JSON object in the text (fenced or bare) that has `key`."""
    import re
    for b in reversed(re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S) + [text]):
        try:
            d = json.loads(b)
        except ValueError:
            continue
        if isinstance(d, dict) and key in d:
            return d
    raise ValueError("no JSON object with {!r} in the agent's answer".format(key))
