#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_skill.py —— 把 duo（研究员 + 讲解员）装成一个 Claude Code skill：canvas-duo。py3.8 兼容。

用法:
  python3 install_skill.py                      # 复制到 ~/.claude/skills/canvas-duo（自包含，约 1 MB，可以整个拷到别的机器）
  python3 install_skill.py --target <目录>       # 装到别处，比如某个项目的 .claude/skills/canvas-duo
  python3 install_skill.py --link               # 不复制，建一个指向本目录的软链（本机开发用：git pull 后 skill 跟着更新）
  python3 install_skill.py --update             # 目标已存在时覆盖更新（只覆盖同名文件，不删目标里多出来的文件）

只装跑一单要用的东西：SKILL.md、run.py、preflight.py、agents/、canvas/（舞台）、check/。
不装 revise.py（它的把关要回归语料和本仓库的 git，只能在本仓库里用）、示例页面和 runs/。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ['SKILL.md', 'run.py', 'preflight.py']
DIRS = ['agents', 'canvas', 'check']
SKIP = shutil.ignore_patterns('__pycache__', '*.pyc', '_off', 'framework-issues-*.md')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default=os.path.join(os.path.expanduser('~'), '.claude', 'skills', 'canvas-duo'))
    ap.add_argument('--link', action='store_true')
    ap.add_argument('--update', action='store_true')
    a = ap.parse_args()
    t = os.path.abspath(os.path.expanduser(a.target))
    exists = os.path.lexists(t)
    if a.link:
        if exists:
            raise SystemExit('目标已存在：%s（软链模式不覆盖；先自己挪走它）' % t)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        os.symlink(HERE, t)
        print('已建软链 %s → %s' % (t, HERE))
        return
    if exists and not a.update:
        raise SystemExit('目标已存在：%s（要覆盖更新加 --update）' % t)
    if os.path.islink(t):
        raise SystemExit('目标是软链：%s（复制模式不往软链里写）' % t)
    os.makedirs(t, exist_ok=True)
    for f in FILES:
        shutil.copy2(os.path.join(HERE, f), os.path.join(t, f))
    for d in DIRS:
        copytree(os.path.join(HERE, d), os.path.join(t, d))
    rev = subprocess.run(['git', '-C', HERE, 'log', '-1', '--format=%h %cd', '--date=short'],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
    with open(os.path.join(t, 'SOURCE.txt'), 'w', encoding='utf-8') as fh:
        fh.write('从 code-canvas 仓库的 demo/duo/ 装来\n来源提交：%s\n安装时间：%s\n'
                 % (rev or '（不是 git 检出，未知）', time.strftime('%Y-%m-%d %H:%M')))
    print('已装到 %s（来源 %s）' % (t, rev or '?'))
    print('下一步：在要讲的仓库里打开 Claude Code，说「用 canvas-duo 讲讲 <你的问题>」，或 /canvas-duo <你的问题>')


def copytree(src, dst):
    if sys.version_info >= (3, 8):
        shutil.copytree(src, dst, ignore=SKIP, dirs_exist_ok=True)
    else:
        raise SystemExit('要 python ≥ 3.8')


if __name__ == '__main__':
    main()
