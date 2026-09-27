#!/usr/bin/env python3
"""
merge_overview.py <canvas.json> <overview.json>

把 agent 产出的算法总览板（只含 overview 对象，或 {"overview": {...}}）合并进
已有画布：先在临时副本上过 validate（--no-exec），ERROR 清零才写回。
补装任务的服务端收尾用；agent 不直接改画布 JSON。
exit 0 合并成功；1 参数/格式错；2 validate 未过（画布原样不动）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip())
        return 1
    cj, oj = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        d = json.loads(cj.read_text(encoding="utf-8"))
        ov = json.loads(oj.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("读取失败:", e)
        return 1
    if isinstance(ov, dict) and "overview" in ov and len(ov) == 1:
        ov = ov["overview"]
    if not isinstance(ov, dict):
        print("overview.json 必须是对象")
        return 1
    d["overview"] = ov
    tmp = Path(tempfile.mkstemp(suffix=".json", dir=str(cj.parent))[1])
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("validate.py")), str(tmp), "--no-exec"],
                       capture_output=True, text=True)
    errs = [ln for ln in r.stdout.splitlines() if ln.startswith("ERROR")]
    if r.returncode != 0 or errs:
        tmp.unlink()
        print("overview 未过 validate，画布未改：")
        print("\n".join(errs) or r.stdout[-800:])
        return 2
    tmp.replace(cj)
    print("overview 已合并进 {}（flow {} 行 / vars {} 个）".format(cj.name, len(ov.get("flow") or []), len(ov.get("vars") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
