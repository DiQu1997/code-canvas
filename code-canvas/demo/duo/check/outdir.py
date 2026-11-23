# -*- coding: utf-8 -*-
"""outdir.py —— 运行目录（--out）能不能放这儿。run.py 和 preflight.py 共用。py3.8 兼容。

agent 的工作目录就是运行目录。claude 会读工作目录和它每一层上级里的 CLAUDE.md / AGENTS.md，
那些是写给别人的指示（被讲仓库的开发者、本工具的开发者），会混进 agent 的交代里。
所以：运行目录不能在被讲的仓库里，不能在工具目录里，它和它的每一层上级都不能有这些文件。
"""
import os

MEMO = ('CLAUDE.md', 'CLAUDE.local.md', 'AGENTS.md')


def _inside(p, d):
    p, d = os.path.realpath(p), os.path.realpath(d)
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def problems(out, repo, tooldir):
    """返回一串问题（空 = 可以放）。out 可以还不存在。"""
    bad = []
    if _inside(out, repo):
        bad.append('运行目录在被讲的仓库里面（%s）' % os.path.realpath(repo))
    if _inside(out, tooldir):
        bad.append('运行目录在工具目录里面（%s）' % os.path.realpath(tooldir))
    d = os.path.realpath(out)
    while True:
        for m in MEMO:
            f = os.path.join(d, m)
            if os.path.isfile(f):
                bad.append('运行目录或它的上级有 %s' % f)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return bad
