#!/usr/bin/env python3
# fill.py —— 把交代模板里的 {{名字}} 换成这一趟的参数，打印到标准输出（发给 agent 的就是这份原文）。
# 用法：python3 fill.py <模板.md> 名字=值 [名字=@文件 …]     （值以 @ 开头 = 从文件读）
# 有没填的 {{名字}} 或多给了模板里没有的名字 → 报错退出，不出半成品。
import re
import sys


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__ or '用法: fill.py <模板.md> 名字=值 …')
    with open(argv[1], encoding='utf-8') as fh:
        text = fh.read()
    vals = {}
    for a in argv[2:]:
        if '=' not in a:
            raise SystemExit('参数要写成 名字=值: %r' % a)
        k, v = a.split('=', 1)
        if v.startswith('@'):
            with open(v[1:], encoding='utf-8') as fh:
                v = fh.read().rstrip('\n')
        vals[k] = v
    need = set(re.findall(r'\{\{([A-Z_]+)\}\}', text))
    miss, extra = sorted(need - set(vals)), sorted(set(vals) - need)
    if miss or extra:
        raise SystemExit('没填: %s；多给: %s' % (' '.join(miss) or '无', ' '.join(extra) or '无'))
    sys.stdout.write(re.sub(r'\{\{([A-Z_]+)\}\}', lambda m: vals[m.group(1)], text))


main(sys.argv)
