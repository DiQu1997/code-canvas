#!/usr/bin/env python3
"""零上下文读者：只读这张画布（看不到仓库），带着读者的提问，交回一份**问题清单**。

Usage: python3 reader_check.py canvas.json --ask "读者的原话" [--audience "读者是谁"]
                              [--out findings.json] [--previous findings.json] [--model opus] [--zoom]

读者不打分。每条问题写明位置、类别、画布里的原话、哪里不对、需要什么、归谁改、有多严重——
作者逐条给出下落（改了 / 用依据反驳 / 说明理由不改）。给了 --previous（上一轮的清单），读者先逐条
核对那些问题现在有没有真的解决，再只列新问题。
清单写到 --out（JSON），同名 .md 是给人看的版本。

读者拿到的是去掉 files（上下文全文）和作者自述（meta.judgment）的 canvas.json——和真实读者
在画布上看到的内容一致。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import agent_cli

KINDS = {
    "unanswered": "提问里问到的事，画布没回答或答偏了",
    "scope": "没说清讲的是哪份代码、和实际运行的是不是同一份",
    "contradiction": "画布自己前后矛盾",
    "mismatch": "说明和它指向的代码对不上",
    "background": "用了没解释的词或概念",
    "gap": "跳步：中间缺了一环",
    "unsupported": "没有依据的断言，像是猜的",
}
SEVERITY = {
    "misleads": "读者会带着错误的理解离开：前后矛盾、说明和代码对不上、范围没说清、数值算不上——这几类默认就是这一档，除非只是措辞",
    "blocks": "读者会卡住读不下去，或者提问里问到的事没得到回答",
    "minor": "不影响理解：措辞、排版、可有可无的补充",
}
OWNER = {"research": "事实缺了或错了，要回到代码里查", "presentation": "事实在，讲法的问题"}
NEEDS_CHECK = ("contradiction", "mismatch", "unsupported")   # 读者只能怀疑，要对着仓库判定

FIELDS = {
    "canvas": """当前目录下的 canvas.json 是一张"代码画布"的数据（字段含义：cards 是代码卡，code 是原文，blocks 是分块大纲，
notes 是行级注释，wires 是连线，regions 是故事线，steps 是按顺序的导读步，
overview/scenarios/boxes/objects/gaps 是辅助说明）""",
    "zoom": """当前目录下的 canvas.json 是一张"缩放图"的数据（字段含义：meta.overview 是读者最先看到的说明；scene 是最上层的动画——
entities 是画面上的实体，steps 是一步一步的故事，每步的 at 指向某张卡的某行；modules 是模块，anatomy 是每个模块放大后的
内部结构——primitive 是形状，items 是部件，fields 是它持有的数据结构；channels 是模块间的连线；cards 是挂在部件上的函数卡，
code 是源码原文，blocks 是分块摘要，steps 是伪代码；info 是模块说明）""",
    "flow": """当前目录下的 canvas.json 是一组"逐层放大的结构图"的数据（字段含义：meta.question 是它要回答的问题；root 是部件树。
有 children + edges 的部件是一张结构图：children 是这一层的部件，edges 是它们之间的关系 [from, to, 标签, 类型, 编号]；
有 steps 的部件是一个函数，steps 是它的伪代码，omitted 是省略的行及原因；files 是对应的源码原文；
walk 是用一个具体例子走一遍，每步落在某个部件或某步伪代码上，带此刻的值）""",
}

PROMPT = """你是这样一位读者：{audience}。第一次接触这个代码库。你是带着一个问题来的：

  {ask}

{fields}。**不要读任何其他文件，不要找仓库**——要检验的正是"只看这张画布，能不能把这个问题弄懂"。

认真读完：顺着故事一步一步走，每一步都点开它指向的代码核对；数值自己算一遍；每个说法问一句"画布里哪里能证明它"。

然后交回一份问题清单。**不要打分，不要写总体评价**。只写具体的问题，每条都要让作者知道去哪里、改什么：

- where：位置（卡 id / 故事第几步 / 模块 id / overview）
- kind：{kinds}
- quote：画布里有问题的那句原话（没有原话——比如缺了一环——就写空）
- problem：具体哪里不对、或者你卡在哪
- needs：需要什么才能解决（补一张卡 / 补一句说明 / 改顺序 / 给依据 / 统一说法……）
- owner：{owners}
- severity：{severities}

要求：
- 提问里问到的每一件事都要过一遍：答了没有、答在哪、够不够。没答或答偏的记为 unanswered。
- 画布讲的范围和实际运行的代码是不是同一份？没说清的记为 scope。
- 只写你真的遇到的问题。没有问题的地方不要凑数；同一个问题只写一条。
- 你看不到仓库，所以"和代码对不上"只能指画布里收录的那几行代码；怀疑但无法在画布内判定的，记为 unsupported。
{previous}
另外写 retell：用 5–10 步复述主角的一生（从哪来、依次经过谁、在哪被改变、最后去哪）——这是你读懂了多少的证据，
作者会拿它对照自己想讲的。

最后只输出一个 JSON 代码块，不要别的话：

```json
{{"retell": ["第 1 步……", "……"],{prev_shape}
 "findings": [{{"where": "", "kind": "", "quote": "", "problem": "", "needs": "", "owner": "", "severity": ""}}]}}
```
"""

PREVIOUS = """
这张画布按上一轮读者的问题清单改过。上一轮的问题是：

{items}

先逐条核对：这个问题现在真的解决了吗？去它说的那个位置看。写进 previous：每条 {{"id", "resolved": true / false, "note": 一句依据}}。
作者说"不改"或"反驳"的那几条附了理由——理由站得住就算解决，站不住就不算。
然后 findings 里**只写新问题**（包括这次改出来的新问题），不要重复上一轮已经列过的。
"""


def clean(d: dict, prev_ids=()) -> list:
    """The list of format problems: a finding that names an unknown kind / owner / severity is not usable."""
    bad = []
    for i, f in enumerate(d["findings"]):
        if not isinstance(f, dict):
            bad.append("findings[{}] 不是对象".format(i))
            continue
        for k, allowed in (("kind", KINDS), ("owner", OWNER), ("severity", SEVERITY)):
            if f.get(k) not in allowed:
                bad.append("findings[{}].{} = {!r} 不在 {} 里".format(i, k, f.get(k), sorted(allowed)))
        for k in ("where", "problem", "needs"):
            if not str(f.get(k) or "").strip():
                bad.append("findings[{}].{} 是空的".format(i, k))
    if prev_ids:
        got = {p.get("id"): p for p in d.get("previous") or [] if isinstance(p, dict)}
        for i in prev_ids:
            if i not in got or not isinstance(got[i].get("resolved"), bool):
                bad.append("previous 里缺 {} 的核对结果（resolved: true / false）".format(i))
    return bad


SEV_SHORT = {"misleads": "会让读者理解错", "blocks": "会把读者卡住", "minor": "小瑕疵"}


def to_md(d: dict, ask: str) -> str:
    order = {"misleads": 0, "blocks": 1, "minor": 2}
    fs = sorted(d["findings"], key=lambda f: order.get(f.get("severity"), 3))
    L = ["# 读者的问题清单", "", "提问：" + ask, ""]
    if d.get("previous"):
        ok = sum(1 for p in d["previous"] if p.get("resolved"))
        L += ["## 上一轮的 {} 条：解决了 {} 条".format(len(d["previous"]), ok), ""]
        L += ["- {} **{}**：{}".format("✓" if p.get("resolved") else "✗", p.get("id"), p.get("note", "")) for p in d["previous"]] + [""]
    L += ["{}{} 条：".format("新问题 " if d.get("previous") else "", len(fs)) + " · ".join(
        "{} {}".format(SEV_SHORT[s], sum(1 for f in fs if f.get("severity") == s)) for s in order), ""]
    for f in fs:
        L += ["## {} [{}] {} · {}".format(f.get("id", ""), SEV_SHORT.get(f.get("severity"), "?"), f.get("where", ""), f.get("kind", "")), ""]
        if f.get("quote"):
            L += ["> " + str(f["quote"]).replace("\n", " "), ""]
        L += [str(f.get("problem", "")), "", "**需要**：{}　**归**：{}".format(f.get("needs", ""), f.get("owner", "")), ""]
        c = f.get("check")
        if c:
            L += ["**核查（对着仓库）**：{} —— {}".format(c.get("verdict"), c.get("fact", "")), ""]
        r = f.get("resolution")
        if r:
            L += ["**下落**：{} —— {}".format(r.get("status"), r.get("note", "")), ""]
    L += ["## 读者的复述", ""] + ["{}. {}".format(i, s) for i, s in enumerate(d.get("retell") or [], 1)]
    return "\n".join(L) + "\n"


def read(canvas: Path, ask: str, audience: str, model: str, zoom=False, previous=None, round_no=1):
    """Run the reader. Returns (findings dict with ids assigned, cost). Raises ValueError / RuntimeError."""
    data = json.loads(canvas.read_text(encoding="utf-8"))
    mode = (data.get("meta") or {}).get("mode")
    kind = "flow" if mode == "flow" else "zoom" if (zoom or mode == "zoom") else "canvas"
    if kind != "flow":
        data.pop("files", None)
    (data.get("meta") or {}).pop("judgment", None)   # 读者不看作者的自述，只看画布本身
    td = Path(tempfile.mkdtemp(prefix="reader-"))
    (td / "canvas.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    opts = lambda m: " / ".join("{}（{}）".format(k, v) for k, v in m.items())
    prev_txt, prev_shape, prev_ids = "", "", []
    if previous:
        lines = []
        for f in previous:
            r = f.get("resolution") or {}
            lines.append("- {}（{}）：{}{}".format(f["id"], f.get("where", ""), f.get("problem", ""),
                                               "\n  作者的下落：{} —— {}".format(r.get("status"), r.get("note", "")) if r else ""))
            prev_ids.append(f["id"])
        prev_txt = PREVIOUS.format(items="\n".join(lines))
        prev_shape = '\n "previous": [{"id": "", "resolved": true, "note": ""}],'
    prompt = PROMPT.format(audience=audience, ask=ask, fields=FIELDS[kind], kinds=opts(KINDS), owners=opts(OWNER),
                           severities=opts(SEVERITY), previous=prev_txt, prev_shape=prev_shape)
    try:
        text, cost = agent_cli.run(prompt, td, model, tools="Read")
    finally:
        shutil.rmtree(str(td), ignore_errors=True)
    d = agent_cli.last_json(text, "findings")
    bad = clean(d, prev_ids)
    if bad:
        raise ValueError("问题清单格式不对：\n  " + "\n  ".join(bad))
    for i, f in enumerate(d["findings"], 1):
        f["id"] = "r{}-{}".format(round_no, i)
    return d, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("canvas", type=Path)
    ap.add_argument("--ask", required=True, help="the reader's question, verbatim")
    ap.add_argument("--audience", default="有经验的工程师", help="who the reader is")
    ap.add_argument("--out", type=Path, default=Path("findings.json"))
    ap.add_argument("--previous", type=Path, help="last round's findings (with resolutions): check each one, then list only new problems")
    ap.add_argument("--model", default=os.environ.get("READER_MODEL", "opus"))
    ap.add_argument("--zoom", action="store_true", help="canvas is a zoom-mode spec (build_zoom.py --dump output)")
    a = ap.parse_args()
    prev = json.loads(a.previous.read_text(encoding="utf-8"))["findings"] if a.previous else None
    try:
        d, cost = read(a.canvas, a.ask, a.audience, a.model, a.zoom, prev, 2 if prev else 1)
    except (ValueError, RuntimeError) as e:
        print(e)
        return 1
    d["cost_usd"] = cost
    a.out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    a.out.with_suffix(".md").write_text(to_md(d, a.ask), encoding="utf-8")
    n = lambda s: sum(1 for f in d["findings"] if f["severity"] == s)
    print("wrote {} · {} findings（理解错 {} / 卡住 {} / 小瑕疵 {}）· ${:.2f}".format(
        a.out, len(d["findings"]), n("misleads"), n("blocks"), n("minor"), cost))
    return 0


if __name__ == "__main__":
    sys.exit(main())
