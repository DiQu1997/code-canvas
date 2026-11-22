#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""revise.py —— 作者对某次运行的成品提了反馈：派一位改版员 agent 从头到尾落实，系统机械把关。py3.8 兼容。

只做调度和机械把关，不做任何判断：改内容还是改呈现、怎么改，全由改版员决定。

用法:
  python3 -I revise.py --run <运行目录> --feedback "<作者原话>" [--image <截图>]... \\
      --corpus redis=<clone> --corpus sglang=<clone> --work <工作目录（要在仓库外面）> [--model opus] [--budget 20]

<运行目录> 里要有 content/（内容文件）、research/（底稿）、report.json（仓库路径和 HEAD）。

流程:
  1 舞台目录必须是干净的（git 没有未提交的改动），否则不开工——出了问题要能整体撤回
  2 改版员 agent（交代模板 agents/reviser.md）：自己判断、改、加自检、截图核对、跑回归
  3 把关：check/regress.py 全档（用改过的舞台重建所有 demo 页面 + 全部自检）
       没过 → 接着改版员的会话让它改一次 → 再跑全档
       还没过 → 把舞台和内容目录里被改过的已跟踪文件撤回到改动前，报告里写清
  4 过了：重建好的页面拷回 demo 目录（regress.py --install）；出 report.md
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run as R  # noqa: E402  复用 run.py 的 agent 启动 / 记账 / 取报告

STAGE = os.path.join(HERE, 'canvas')
REGRESS = os.path.join(HERE, 'check', 'regress.py')
GIT_ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))


def git(*a):
    return subprocess.run(['git', '-C', GIT_ROOT] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def changed(paths):
    """这些目录下 git 看到的改动（已跟踪的修改 + 新文件）。"""
    out = git('status', '--porcelain', '--', *paths).stdout
    return [l for l in out.split('\n') if l.strip()]


def regress(args, out, install=False):
    cmd = [sys.executable, '-I', REGRESS, '--out', out] + sum([['--corpus', c] for c in args.corpus], [])
    if install:
        cmd.append('--install')
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    R.write(out + '.txt', p.stdout)
    return p.returncode == 0, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--feedback', required=True)
    ap.add_argument('--image', action='append', default=[])
    ap.add_argument('--corpus', action='append', default=[])
    ap.add_argument('--work', required=True)
    ap.add_argument('--model', default='opus')
    ap.add_argument('--budget', type=float, default=20.0)
    ap.add_argument('--cli-bin', default='claude')
    a = ap.parse_args()
    run_dir, work = os.path.abspath(a.run), os.path.abspath(a.work)
    if work.startswith(GIT_ROOT + os.sep):
        raise SystemExit('--work 要放在仓库外面（agent 在仓库里会读到给开发者看的 AGENTS.md）')
    content = os.path.join(run_dir, 'content')
    info = json.loads(R.read(os.path.join(run_dir, 'report.json')))
    repo = info['repo']
    page = info.get('page') or ''
    if not os.path.exists(page):
        page = '（找不到上次的成品页；按下面的构建命令自己构建）'
    dirty = changed([STAGE, content])
    if dirty:
        raise SystemExit('舞台或内容目录有没提交的改动，先提交再开工（出问题要能整体撤回）：\n' + '\n'.join(dirty))
    base = git('rev-parse', '--short', 'HEAD').stdout.strip()

    os.makedirs(os.path.join(work, 'logs'), exist_ok=True)
    R.OUT, R.REPO, R.CONTENT = work, repo, content
    R.A = argparse.Namespace(cli_bin=a.cli_bin, budget=a.budget)
    imgs = []
    for i, p in enumerate(a.image):
        dst = os.path.join(work, 'feedback-%d%s' % (i + 1, os.path.splitext(p)[1] or '.png'))
        shutil.copyfile(p, dst)
        imgs.append(dst)
    R.write(os.path.join(work, 'feedback.md'), a.feedback + '\n')
    corp = ' '.join('--corpus %s' % c for c in a.corpus)
    rq = 'python3 -I %s %s --out %s/regress-quick --quick' % (REGRESS, corp, work)
    rf = 'python3 -I %s %s --out %s/regress-full' % (REGRESS, corp, work)
    prompt = R.tpl('reviser.md', FEEDBACK=a.feedback,
                   IMAGES='\n'.join('- `%s`' % p for p in imgs) or '（没有附图）',
                   RUN=run_dir, CONTENT=content, STAGE=STAGE, DUO=HERE, REPO=repo, PAGE=page, WORK=work,
                   REGRESS_QUICK=rq, REGRESS_FULL=rf)
    R.log('改版开始：基线 %s；反馈：%s' % (base, a.feedback[:60]))
    t0 = time.time()
    rec = R.run_agent('reviser', prompt, 'present', a.model)
    steps = [rec]
    touched = changed([STAGE, content])
    gate_ok, gate_out, reverted = False, '', []
    if rec.get('ok') and touched:
        R.log('改版员改动了 %d 处；把关：回归全档' % len(touched))
        gate_ok, gate_out = regress(a, os.path.join(work, 'gate-1'), install=True)
        if not gate_ok and rec.get('session'):
            bad = gate_out[gate_out.find('没过：'):][:5000] if '没过：' in gate_out else gate_out[-3000:]
            fix = R.run_agent('reviser.fix', (
                '系统把关（回归全档，用你改过的舞台重建了所有 demo 页面并跑了全部自检）没过：\n\n%s\n\n'
                '完整输出在 `%s`。请修好（只修让回归没过的地方，别的不要动），修完自己再跑一次全档确认全绿。'
                '最终回复只写：原因是什么、改了什么、全档结果。' % (bad, os.path.join(work, 'gate-1.txt'))),
                'present', a.model, resume=rec['session'], base_cost=float(rec.get('cost_session_total') or 0))
            steps.append(fix)
            gate_ok, gate_out = regress(a, os.path.join(work, 'gate-2'), install=True)
        if not gate_ok:
            tracked = [l[3:] for l in changed([STAGE, content]) if not l.startswith('??')]
            if tracked:
                git('checkout', '--', *tracked)
                reverted = tracked
            R.log('把关没过，已撤回 %d 个已跟踪文件的改动' % len(reverted))
    elif rec.get('ok'):
        R.log('改版员没有改动任何文件')
    rep = ['# 改版报告', '', '- 反馈：%s' % a.feedback, '- 截图：%s' % ('、'.join('`%s`' % p for p in imgs) or '无'),
           '- 基线提交：`%s`；运行目录：`%s`' % (base, run_dir),
           '- 花费：**$%.2f**；用时 %.0f 分钟' % (R.SPENT[0], (time.time() - t0) / 60), '',
           '| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |', '|---|---|---|---|---|---|']
    for r in steps:
        rep.append('| %s | %s | $%.2f | %d 分 | %s | %s |' % (r['step'], ','.join(r.get('models') or []) or r.get('model_req'),
                   r.get('cost') or 0, (r.get('secs') or 0) // 60, r.get('tools', '-'), '成功' if r.get('ok') else '失败'))
    rep += ['', '## 改动（git status，改版员交卷时）', ''] + ['    ' + l for l in touched] + ['']
    rep += ['## 把关', '', '**%s**' % ('通过，重建的页面已拷回 demo 目录' if gate_ok else ('没过，已撤回' if reverted else '没跑或没过')), '',
            '```', gate_out.strip()[-4000:], '```', '']
    if reverted:
        rep += ['撤回的文件：'] + ['- `%s`' % f for f in reverted] + ['（新建的未跟踪文件没有删，留在原处）', '']
    rep += ['## 改版员的报告（原文）', '', R.read(os.path.join(work, 'logs', 'reviser.reply.md'))]
    R.write(os.path.join(work, 'report.md'), '\n'.join(rep) + '\n')
    R.log('改版结束：%s；花费 $%.2f' % ('通过' if gate_ok else '没通过', R.SPENT[0]))
    return 0 if gate_ok else 1


if __name__ == '__main__':
    sys.exit(main())
