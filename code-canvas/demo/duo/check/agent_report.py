#!/usr/bin/env python3
"""从一次 claude -p 的 stream-json 日志里取出 agent 的报告（原文，不改一字）。py3.8 兼容。

为什么需要：result 事件里的 result 只是**最后一条**文字。agent 交完报告后如果又回应了一次
后台任务的通知（例如它自己挂的监视器结束了），最后一条就只是一句「这条通知可以忽略」，
真正的报告在前面。

取法：所有 assistant 文字块里，取**最后一条够长的**（默认 ≥ 600 字符）当报告；
如果最终那条和它不是同一条，附在后面。

用法: python3 -I agent_report.py <步骤.jsonl> [最短字符数]
"""
import json
import sys


def texts(path):
    out, seen = [], set()
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get('type') != 'assistant':
                continue
            msg = ev.get('message') or {}
            for i, c in enumerate(msg.get('content') or []):
                if isinstance(c, dict) and c.get('type') == 'text' and c.get('text', '').strip():
                    key = (msg.get('id'), i)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(c['text'])
    return out


def report(path, min_len=600):
    ts = texts(path)
    if not ts:
        return ''
    long_ = [t for t in ts if len(t) >= min_len]
    main = long_[-1] if long_ else ts[-1]
    if ts[-1] is not main and ts[-1] != main:
        return main + '\n\n---\n（之后的最后一条回复）\n\n' + ts[-1]
    return main


if __name__ == '__main__':
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    sys.stdout.write(report(sys.argv[1], n) + '\n')
