#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regress.py —— 舞台的回归：用当前舞台把 demos.json 里的每个页面重建一遍、和入库的页面比字节、跑自检。py3.8 兼容。

用法: python3 -I regress.py --corpus redis=<clone> --corpus sglang=<clone> --out <目录> [--quick] [--install] [--only 名字,...]
  --quick    快档（改版时反复跑）：每页只跑一个轻的自检；不带就是全档（收尾把关，含 --all，十几分钟）
  --install  全部通过后，把重建出来的页面拷回 demo 目录（改了渲染器要重渲染全部 demo）
退出码：任何构建出错（strict 的那几个算上 WARN / 未填槽位）或任何自检有 FAIL → 1
最后一行是一句话的汇总。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DUO = os.path.dirname(HERE)
STAGE = os.path.join(DUO, 'canvas')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', action='append', default=[])
    ap.add_argument('--out', required=True)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--install', action='store_true')
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    corp = dict(kv.split('=', 1) for kv in a.corpus)
    cfg = json.load(open(os.path.join(HERE, 'demos.json'), encoding='utf-8'))
    only = set(filter(None, a.only.split(',')))
    bad, lines, built = [], [], []
    for d in cfg['demos']:
        if only and d['name'] not in only:
            continue
        if d['corpus'] not in corp:
            bad.append('%s：没给 --corpus %s=<路径>' % (d['name'], d['corpus']))
            continue
        od = os.path.join(a.out, d['name'])
        cmd = [sys.executable, '-I', os.path.join(STAGE, 'build.py'), corp[d['corpus']], od]
        if d['content']:
            cmd += ['--content', os.path.join(DUO, d['content'])]
        if d['strict']:
            cmd.append('--strict')
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        os.makedirs(od, exist_ok=True)
        with open(os.path.join(od, 'build.log'), 'w', encoding='utf-8') as fh:
            fh.write(p.stdout)
        crash = 'Traceback' in p.stdout
        if crash or (d['strict'] and p.returncode):
            bad.append('%s：构建%s（见 %s/build.log）' % (d['name'], '崩溃' if crash else '没过 --strict', od))
        for pg in d['pages']:
            src, dst = os.path.join(od, pg['file']), os.path.join(DUO, pg['html'])
            if not os.path.exists(src):
                bad.append('%s：没产出 %s' % (d['name'], pg['file']))
                continue
            same = open(src, 'rb').read() == open(dst, 'rb').read()
            built.append((src, dst))
            chk = pg['quick' if a.quick else 'full']
            sd = os.path.join(a.out, d['name'] + '-shots-' + pg['file'].split('.')[0])
            args = ['node', os.path.join(STAGE, chk[0]), src, sd] + chk[1:]
            try:
                q = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                   timeout=40 * 60, cwd=STAGE)
                out, rc = q.stdout, q.returncode
            except subprocess.TimeoutExpired as e:
                out, rc = (e.output or '') + '\n超时', -1
            with open(sd + '.log', 'w', encoding='utf-8') as fh:
                fh.write(out)
            fails = [l for l in out.split('\n') if l.startswith('FAIL')]
            m = re.findall(r'(\d+) PASS / (\d+) FAIL', out)
            tail = [l for l in out.strip().split('\n') if l.strip()][-1:] or ['（没有输出）']
            summ = ('%s PASS / %s FAIL' % m[-1]) if m else tail[0][:80]   # shot.js（v1）没有 PASS/FAIL 汇总，取它的结论行
            lay = re.findall(r'排版问题 (\d+) 条', out)
            if lay and lay[-1] != '0':
                summ += '，排版问题 %s 条' % lay[-1]
                bad.append('%s/%s：排版问题 %s 条' % (d['name'], pg['html'], lay[-1]))
            if fails or rc != 0 or '超时' in out:
                bad.append('%s/%s：%s' % (d['name'], pg['html'], summ))
                for l in fails[:8]:
                    bad.append('    ' + l[:220])
            lines.append('%-30s %-8s %s  [%s]' % (pg['html'], '没变' if same else '有变化', summ, ' '.join(chk)))
    for l in lines:
        print(l)
    if bad:
        print('\n没过：')
        for b in bad:
            print('  ' + b)
    elif a.install:
        for src, dst in built:
            shutil.copyfile(src, dst)
        print('\n已把 %d 个重建页面拷回 demo 目录' % len(built))
    print('\n回归%s：%d 个页面，%s' % ('（快档）' if a.quick else '（全档）', len(lines), '全部通过' if not bad else '有 %d 处没过' % len([b for b in bad if not b.startswith('    ')])))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
