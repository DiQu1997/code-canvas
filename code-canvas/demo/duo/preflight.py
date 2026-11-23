#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight.py —— 开跑 run.py 之前的环境预检（换一台机器、换一个仓库时先跑它）。py3.8 兼容。

用法:
  python3 -I preflight.py --repo <仓库路径> --out <运行目录> [--cli-bin claude] [--probe]

逐条打印 OK / WARN / FAIL；有 FAIL 退出码 1（不要开跑），只有 WARN 退出码 0（把 WARN 讲给用户听）。
  --probe  真起一次 claude（便宜一档模型，上限 $0.50，实测 $0.04；第一次冷启动可能到 $0.1）：在运行目录里用 Read 读仓库里一个文件的第一行。
           验的是登录可用、无人值守模式下 agent 能读到工作目录外的仓库——这两条不真跑查不出来。

查的东西：python ≥ 3.8；git、仓库有 HEAD、工作区干净（构建和核对读的是 HEAD，未提交的改动 agent 看得到、核对时对不上）；
运行目录的位置（check/outdir.py）；node、playwright 包、浏览器能启动（agent 截图自检要用；
找不到时用环境变量 CANVAS_TEST_PW / CANVAS_TEST_CHROMIUM 指）；claude 命令在、版本支持 --max-budget-usd 和 stream-json。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, 'canvas')
sys.path.insert(0, os.path.join(HERE, 'check'))
from outdir import problems as out_problems  # noqa: E402

N = {'OK': 0, 'WARN': 0, 'FAIL': 0}


def say(level, msg):
    N[level] += 1
    print('%-4s %s' % (level, msg))


def run(cmd, **kw):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           encoding='utf-8', errors='replace', **kw)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cli-bin', default='claude')
    ap.add_argument('--probe', action='store_true')
    a = ap.parse_args()
    repo, out = os.path.abspath(a.repo), os.path.abspath(a.out)

    if sys.version_info >= (3, 8):
        say('OK', 'python %d.%d' % sys.version_info[:2])
    else:
        say('FAIL', 'python %d.%d，要 ≥ 3.8' % sys.version_info[:2])

    g = ['git', '-C', repo, '-c', 'safe.directory=*']
    rc, head = run(g + ['rev-parse', '--short=10', 'HEAD'])
    if rc != 0:
        say('FAIL', '读不到仓库 HEAD（不是 git 仓库，或还没有提交）：%s' % repo)
    else:
        rc2, top = run(g + ['rev-parse', '--show-toplevel'])
        say('OK', '仓库 %s @ %s' % (repo, head.strip()))
        if rc2 == 0 and os.path.realpath(top.strip()) != os.path.realpath(repo):
            say('WARN', '--repo 不是仓库根（根在 %s）；锚点按仓库根算相对路径，建议直接用根' % top.strip())
        rc, st = run(g + ['status', '--porcelain', '--untracked-files=no'])
        dirty = [x for x in st.splitlines() if x.strip()] if rc == 0 else []
        if dirty:
            say('WARN', '工作区有 %d 个文件改了没提交：构建和核对读的是 HEAD，agent 读的是工作区，改过的地方会对不上。'
                        '最好先提交（或换个干净的 worktree）' % len(dirty))
        else:
            say('OK', '工作区干净')

    bad = out_problems(out, repo, HERE)
    if bad:
        for b in bad:
            say('FAIL', '运行目录不能放这里：' + b)
    else:
        say('OK', '运行目录 %s%s' % (out, '（已存在：再跑 = 接着上次）' if os.path.isdir(out) else ''))

    rc, v = run(['node', '--version'])
    if rc != 0:
        say('FAIL', '没有 node（agent 截图自检要用）')
    else:
        say('OK', 'node %s' % v.strip())
        probe = ("const r=require('module').createRequire(%s);"
                 "const {chromium}=r(process.env.CANVAS_TEST_PW||'playwright');"
                 "(async()=>{const e=process.env.CANVAS_TEST_CHROMIUM;"
                 "const b=await chromium.launch(e?{executablePath:e}:{});"
                 "const p=await b.newPage();await p.setContent('<p>ok</p>');"
                 "console.log('BROWSER '+b.version());await b.close();})()"
                 ".catch(x=>{console.log('ERR '+String(x).split('\\n')[0]);process.exit(1);});"
                 ) % json.dumps(os.path.join(STAGE, 'shot_scene.js'))
        rc, o = run(['node', '-e', probe], timeout=120)
        if rc == 0 and 'BROWSER' in o:
            say('OK', 'playwright + 浏览器能启动（%s）' % o.strip().split('BROWSER ')[-1])
        elif 'Cannot find module' in o:
            say('FAIL', '从 %s 找不到 playwright 包：装一个，或用环境变量 CANVAS_TEST_PW 指到包的路径' % STAGE)
        else:
            say('FAIL', '浏览器起不来：%s——用环境变量 CANVAS_TEST_CHROMIUM 指到一个 Chromium 可执行文件'
                % (o.strip().splitlines() or ['?'])[-1][:300])

    cli = shutil.which(a.cli_bin) or (a.cli_bin if os.path.isfile(a.cli_bin) else None)
    if not cli:
        say('FAIL', '找不到 claude 命令（%s）；不在 PATH 里就用 --cli-bin 指' % a.cli_bin)
    else:
        rc, v = run([cli, '--version'], timeout=60)
        rc2, h = run([cli, '--help'], timeout=60)
        need = [x for x in ('--max-budget-usd', 'stream-json', '--resume', '--disallowedTools') if x not in h]
        if rc != 0 or need:
            say('FAIL', 'claude（%s）%s；更新一下 claude' % (v.strip()[:60], '缺参数 ' + ' '.join(need) if need else '跑不起来'))
        else:
            say('OK', 'claude %s' % v.strip()[:60])
            if a.probe and N['FAIL'] == 0:
                probe_claude(cli, repo, out)

    print('预检：%d OK / %d WARN / %d FAIL' % (N['OK'], N['WARN'], N['FAIL']))
    sys.exit(1 if N['FAIL'] else 0)


def probe_claude(cli, repo, out):
    rc, files = run(['git', '-C', repo, '-c', 'safe.directory=*', 'ls-files'])
    pick = None
    for f in files.splitlines():
        p = os.path.join(repo, f)
        if f.endswith(('.md', '.py', '.txt', '.c', '.go', '.rs', '.js', '.ts', '.java')) and os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as fh:
                    first = fh.readline().strip()
            except (OSError, UnicodeDecodeError):
                continue
            if 8 <= len(first) <= 120 and first.isprintable():
                pick = (p, first)
                break
    if not pick:
        say('WARN', '--probe：仓库里没找到合适的文件来试读，跳过')
        return
    if not os.path.isdir(out):
        os.makedirs(out)
    cwd = tempfile.mkdtemp(prefix='probe-', dir=out)      # 和 agent 一样：工作目录在运行目录里、仓库在外面
    prompt = '用 Read 工具读文件 %s 的第一行，只回复那一行的原文，不要别的字。' % pick[0]
    cmd = [cli, '-p', prompt, '--model', 'sonnet', '--output-format', 'json',
           '--allowedTools', 'Read', '--disallowedTools', 'Agent,Task,WebFetch,WebSearch', '--max-budget-usd', '0.50']
    rc, o = run(cmd, cwd=cwd, timeout=300)
    try:
        r = json.loads(o.strip().splitlines()[-1])
    except (ValueError, IndexError):
        r = {}
    got, cost = str(r.get('result', '')), r.get('total_cost_usd')
    if pick[1] in got:
        say('OK', '--probe：claude 无人值守读到了仓库文件（花费 $%s）' % ('%.3f' % cost if cost is not None else '?'))
    else:
        say('FAIL', '--probe：claude 没读到仓库文件（退出码 %d）：%s' % (rc, (got or o)[-300:].replace('\n', ' ')))


if __name__ == '__main__':
    main()
