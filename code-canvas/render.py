#!/usr/bin/env python3
"""Render a Code Canvas JSON into a self-contained HTML file.

Usage: python3 render.py canvas.json output.html
"""
import json
import sys
from pathlib import Path

PLACEHOLDER = "/*CANVAS_DATA*/null"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip())
    data_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(data_path.read_text(encoding="utf-8"))

    template = (Path(__file__).parent / "template" / "canvas.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        sys.exit("template/canvas.html is missing the data placeholder")

    # </script> inside string data must not terminate the script tag
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out_path.write_text(template.replace(PLACEHOLDER, payload), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
