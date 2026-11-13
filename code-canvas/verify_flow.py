#!/usr/bin/env python3
"""Code-aware fact check for a logic-line canvas.

Usage: python3 verify_flow.py flow.data.json --repo /path/to/repo [--out verify_report.md] [--model opus]

Starts a separate checker that CAN read the repository. It goes through the step tree and the
example walk and reports every claim the code does not support: wrong order, wrong thread/owner,
a step attributed to the wrong lines, a branch that does not exist, an example value that cannot
be produced. The author must fix each one or justify it in meta.judgment.revisions.
"""
import argparse, json, os, subprocess, sys, tempfile, shutil
from pathlib import Path

PROMPT = """你是代码审查者。任务：核对一张"逻辑线图"说的每一件事是否与真实代码一致。

图的数据在 {canvas}（JSON）：root 是一棵部件树。每个部件有 title / desc（人话）、code（标识符）、kind、file + lines/ranges（声称对应的源码）；
有 children + edges 的部件是一张结构图：children 是这一层的部件，edges 是 [from, to, 标签, 类型, 编号]（编号 = 声称的发生顺序）；
有 steps 的部件是一个函数：steps 是伪代码（title、lines、depth 缩进、kind、call），omitted 是声称可以省略的行及原因；
walk 是用一个具体例子走一遍，每步有 node（落在哪个部件）、step（第几步伪代码）、text、value、state。
仓库在 {repo}。**你必须打开真实文件核对，不要相信图里的任何说法。**不要读 JSON 里的 files 字段，直接读仓库。

逐项检查，重点找这几类错误：
1. 归属错：这件事其实不在它标的行段里发生（在别的函数 / 别的线程 / 更早或更晚）。
2. 关系错：图上画了 A→B 的线，代码里 A 并不调用 / 写入 B（或方向反了、类型不对：说是入队其实是直接调用）；编号顺序与真实执行顺序不符；伪代码里分支条件与代码相反、缩进层级（谁在循环 / 分支里）不对。
3. 执行者错：说某件事由 A 线程 / 部件做，代码表明是 B；把两个线程的事画进了同一个部件。
4. 省略了不该省略的：omitted 里的行其实改了主线状态或做了决定。
5. 遗漏：主线上有一个改状态或做决定的步骤、一个队列 / 表、或两个部件之间的一条关键关系，图里完全没有。
6. 例子走不通：walk 里的值按代码算不出来，或走了代码不会走的分支。
7. 黑盒契约与实现不符。

先通读顶层和 walk，再挑最容易出错的地方深入（跨线程交接、队列、回调、提前返回、循环退出条件）。至少核对 12 个节点和整条 walk。

输出中文 markdown：
## 事实错误
每条一行：`[节点 id 或 walk 步号] 图里说 → 代码实际（文件:行）→ 建议怎么改`。只写你打开文件确认过的。没有就写"无"。
## 存疑
你怀疑但没能确认的（说明卡在哪）。
## 核对过且正确
列出你核对过的节点 id（一行）。
最后一行：`错误数: N`
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("canvas", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("verify_report.md"))
    ap.add_argument("--model", default=os.environ.get("VERIFY_MODEL", "opus"))
    ap.add_argument("--cli", default=os.environ.get("VERIFY_CLI", "claude"))
    a = ap.parse_args()
    data = json.loads(a.canvas.read_text(encoding="utf-8"))
    data.pop("files", None)
    (data.get("meta") or {}).pop("judgment", None)
    td = Path(tempfile.mkdtemp(prefix="verify-"))
    cj = td / "canvas.json"
    cj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    prompt = PROMPT.format(canvas=cj, repo=a.repo.resolve())
    cmd = [a.cli, "-p", prompt, "--model", a.model, "--allowedTools", "Read,Grep,Glob", "--add-dir", str(td), "--output-format", "text"]
    r = subprocess.run(cmd, cwd=a.repo, capture_output=True, text=True, timeout=1800)
    out = r.stdout.strip() or ("(检查者没有输出) " + r.stderr[-2000:])
    a.out.write_text(out + "\n", encoding="utf-8")
    shutil.rmtree(td, ignore_errors=True)
    print(f"wrote {a.out}")
    print(out.splitlines()[-1] if out else "")
    sys.exit(0 if r.returncode == 0 else 1)


if __name__ == "__main__":
    main()
