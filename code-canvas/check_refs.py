#!/usr/bin/env python3
"""
check_refs.py <canvas.json> <仓库根>

依据（refs）机械核对：画布里写的每条 [文件, 行号, 符号] 必须在仓库里成立——
文件存在、行号不越界、符号出现在该行 ±3 行内。核不过的依据就地剔除并逐条报出；
写着"已确认"（ev 缺省或 fact）的步卡 trace，若依据全被剔除，降级为 infer。
读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。

依据出现在：steps[].trace.refs、gaps[].refs、objects[].refs。
exit 0（剔除也算正常完成）；1 参数/格式错。
"""
import json
import sys
from pathlib import Path


def ref_ok(repo: Path, ref):
    if not (isinstance(ref, list) and len(ref) in (2, 3) and isinstance(ref[0], str)
            and isinstance(ref[1], int) and ref[1] > 0):
        return "格式应为 [文件, 行号, 符号]"
    p = (repo / ref[0].lstrip("./")).resolve()
    try:
        p.relative_to(repo)
    except ValueError:
        return "路径越出仓库"
    if not p.is_file():
        return "文件不存在"
    lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    if ref[1] > len(lines):
        return "行号越界（全文 {} 行）".format(len(lines))
    if len(ref) == 3 and ref[2]:
        win = "\n".join(lines[max(0, ref[1] - 4):ref[1] + 3])
        if ref[2] not in win:
            return "符号「{}」不在该行 ±3 行内".format(ref[2])
    return None


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 1
    cj, repo = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    try:
        d = json.loads(cj.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("读取失败:", e)
        return 1
    holders = []   # (位置描述, 持有 refs 的对象, 是否 trace)
    for i, s in enumerate(d.get("steps") or []):
        if isinstance(s.get("trace"), dict):
            holders.append(("steps[{}].trace".format(i), s["trace"], True))
    for key in ("gaps", "objects"):
        for i, g in enumerate(d.get(key) or []):
            if isinstance(g, dict):
                holders.append(("{}[{}]".format(key, i), g, False))
    total = kept = 0
    bad, downgraded = [], []
    for where, h, is_trace in holders:
        refs = h.get("refs")
        if not refs:
            continue
        good = []
        for r in refs:
            total += 1
            why = ref_ok(repo, r)
            if why:
                bad.append("{} {} — {}".format(where, json.dumps(r, ensure_ascii=False), why))
            else:
                good.append(r)
        kept += len(good)
        h["refs"] = good
        if is_trace and not good and h.get("ev", "fact") == "fact":
            h["ev"] = "infer"
            downgraded.append(where)
    cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("依据核对：{} 条，通过 {}，剔除 {}".format(total, kept, total - kept))
    for b in bad:
        print("  剔除", b)
    for w in downgraded:
        print("  降级", w, "：依据全部核不过，ev → infer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
