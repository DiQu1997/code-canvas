#!/usr/bin/env python3
"""新读者挑错：起一个没有上下文、看不到仓库的读者，只读这张画布，报告读不懂的地方。

Usage: python3 reader_check.py canvas.json [--out reader_report.md] [--model sonnet]

读者拿到的是去掉 files（上下文全文）的 canvas.json——和真实读者在画布上看到的内容一致。
输出是一份 markdown 报告：他复述的主角一生、部件职责，以及读不懂/可疑/矛盾的清单。
"""
import argparse, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PROMPT = """你是一位有经验的工程师，第一次接触这个代码库。你手里只有一张"代码画布"：
当前目录下的 canvas.json（字段含义：cards 是代码卡，code 是原文，blocks 是分块大纲，
notes 是行级注释，wires 是连线，regions 是故事线，steps 是按顺序的导读步，
overview/scenarios/boxes/objects/gaps 是辅助说明）。**不要读任何其他文件，不要找仓库**——
你要检验的正是"只看这张画布能不能读懂"。

认真读完以后，用中文写一份报告，四节：

## 1. 主角的一生
这段代码处理的核心对象是什么？它从哪来、依次经过哪些部件（具体到函数）、在哪里被改变、
最后去哪？写成 5-10 步。

## 2. 部件
每个部件一句话：它干什么、用什么数据结构/判据、它**不是**什么（和谁容易混淆）。

## 3. 我读不懂的地方
列出 3-8 个具体问题：哪一步/哪张卡/哪个说法让你卡住、缺了什么背景、哪里跳跃。
每条写清位置（step 序号或卡 id）和你需要什么才能懂。只有真的懂了才少写。

## 4. 可疑或矛盾
画布里你觉得说不通、前后矛盾、或者像是猜测的说法（没有就写"无"）。

最后一行单独写：自评理解度 X/10。
"""


ZOOM_FIELDS = """当前目录下的 canvas.json 是一张"缩放图"的数据（字段含义：modules 是系统层的模块方框，
anatomy 是每个模块放大后的内部结构——primitive 是形状，items 是部件，fields 是它持有的数据结构；
channels 是模块间的连线；cards 是挂在部件上的函数卡，code 是源码原文，blocks 是分块摘要；
info 是模块说明；stories 是一个主角从头到尾的故事线，每步落在某张卡的某行或某个部件上）"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("canvas", type=Path)
    ap.add_argument("--out", type=Path, default=Path("reader_report.md"))
    ap.add_argument("--model", default=os.environ.get("READER_MODEL", "sonnet"))
    ap.add_argument("--cli", default=os.environ.get("READER_CLI", "claude"))
    ap.add_argument("--zoom", action="store_true", help="canvas is a zoom-mode spec (build_zoom.py --dump output)")
    a = ap.parse_args()
    data = json.loads(a.canvas.read_text(encoding="utf-8"))
    data.pop("files", None)
    (data.get("meta") or {}).pop("judgment", None)   # 读者不看作者的自述，只看画布本身
    td = Path(tempfile.mkdtemp(prefix="reader-"))
    (td / "canvas.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    prompt = PROMPT
    if a.zoom or (data.get("meta") or {}).get("mode") == "zoom":
        prompt = PROMPT.split("当前目录下的 canvas.json")[0] + ZOOM_FIELDS + "。**不要读任何其他文件" + PROMPT.split("。**不要读任何其他文件", 1)[1]
    cmd = [a.cli, "-p", prompt, "--model", a.model, "--allowedTools", "Read", "--output-format", "text"]
    r = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=900)
    out = r.stdout.strip() or ("(读者没有输出) " + r.stderr[-2000:])
    a.out.write_text(out + "\n", encoding="utf-8")
    shutil.rmtree(td, ignore_errors=True)
    print(f"wrote {a.out}")
    sys.exit(0 if r.returncode == 0 else 1)


if __name__ == "__main__":
    main()
