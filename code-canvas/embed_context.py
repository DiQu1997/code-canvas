#!/usr/bin/env python3
"""embed_context.py — 给画布 JSON 嵌入引用文件全文（上下文窥视的数据层）。

对每张带 `file: "路径:起始行"` 的代码卡：
1. 溯源核对：卡内 code 必须与仓库文件在该行起的窗口对得上
   （空白不敏感，容忍 `//`/`#`/`\\` 续行重排——与评分器同款）
2. 行漂移修正：对不上时在全文里搜索唯一匹配点，机械修正 file 行号
   （仓库比画布新时的常见情形）
3. 全部通过的文件写进顶层 files 映射；对不上的卡如实跳过并列出

语言无关（纯文本匹配）。幂等：重跑只是重算 files。
用法: python3 embed_context.py canvas.json <仓库根> [--max-file-kb 400]
兼容 Python 3.8。退出码：0 成功；2 没有任何卡可嵌入。
"""
import argparse
import json
import re
import sys
from pathlib import Path


def norm(s):
    return re.sub(r"\s+", "", s).replace("//", "").replace("#", "").replace("\\", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("canvas")
    ap.add_argument("repo")
    ap.add_argument("--max-file-kb", type=int, default=400)
    ns = ap.parse_args()
    jp, repo = Path(ns.canvas), Path(ns.repo)
    d = json.loads(jp.read_text(encoding="utf-8"))

    # 仓库根自动定位：卡路径若相对某一级子目录（如 monorepo 的 codex-rs/），
    # 挑"能命中最多卡路径"的根
    paths = {m.group(1) for c in d.get("cards", [])
             for m in [re.match(r"([^:]+):\d+$", c.get("file") or "")] if m}
    if paths:
        def score(r):
            return sum(1 for p in paths if (r / p).exists())
        best, bs = repo, score(repo)
        if bs < len(paths):
            for sub in sorted(repo.iterdir()):
                if sub.is_dir() and not sub.name.startswith("."):
                    s = score(sub)
                    if s > bs:
                        best, bs = sub, s
        if best != repo:
            print("仓库根自动定位到子目录: {}".format(best.name), file=sys.stderr)
            repo = best

    files, ok, skip, fixed = {}, 0, [], []
    for c in d.get("cards", []):
        m = re.match(r"([^:]+):(\d+)$", c.get("file") or "")
        if not m or not c.get("code"):
            continue
        src = repo / m.group(1)
        if not src.exists():
            skip.append((c.get("id"), "文件不存在 " + m.group(1)))
            continue
        text = src.read_text(errors="replace")
        if len(text) > ns.max_file_kb * 1024:
            skip.append((c.get("id"), "文件超 {}KB".format(ns.max_file_kb)))
            continue
        lines = text.split("\n")
        start, n = int(m.group(2)), len(c["code"].split("\n"))

        def match(at):
            return norm("\n".join(lines[at - 1: at - 1 + n + 40])).startswith(norm(c["code"]))

        if not match(start):
            head = norm(c["code"].split("\n")[0])
            hit = next((i + 1 for i, ln in enumerate(lines)
                        if head and norm(ln) == head and match(i + 1)), None)
            if hit is None:
                skip.append((c.get("id"), "与仓库对不上，跳过"))
                continue
            c["file"] = "{}:{}".format(m.group(1), hit)
            fixed.append((c.get("id"), start, hit))
            start = hit
        files[m.group(1)] = text
        ok += 1

    if not ok:
        print("没有任何卡可嵌入（{} 张跳过：{}）".format(len(skip), skip), file=sys.stderr)
        sys.exit(2)
    d["files"] = files
    jp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("嵌入 {} 文件 / {} 卡，共 {:.0f} KB".format(
        len(files), ok, sum(len(v) for v in files.values()) / 1024), file=sys.stderr)
    for cid, a, b in fixed:
        print("  行漂移修正: {} {}→{}".format(cid, a, b), file=sys.stderr)
    for cid, why in skip:
        print("  跳过: {} — {}".format(cid, why), file=sys.stderr)


if __name__ == "__main__":
    main()
