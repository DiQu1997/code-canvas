#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""code_blocks.py —— 量尺：一张已有画布的源码页里，每段代码是什么（逻辑 / 结构定义 / 注释 / 日志）。py3.8 兼容。
只读，不改任何东西。**不是闸门**，没接进 run.py：只用来在改交代前后各量一次，看「数据」有没有露出来。

用法: python3 -I code_blocks.py <舞台目录> <内容目录> <仓库路径> [--list]
  输出一行 JSON（全画布合计）；--list 再逐块列出，并列出「逻辑里用到、定义却没露出」的字段。

读数时要知道它的窄处（如实）：
  - 只认 .py。别的语言（C++ 头文件、proto、SQL、配置）整段算进 other，里面的字段定义**数不到**。
  - 「结构」行只数：类头、类体里的字段声明、__init__ / __post_init__ 里的 self.x = …。
    数据库表、消息格式、磁盘文件格式这类「广义的数据」它不认识。
  - 「定义有没有露出」只跟踪同一个类里的 self.字段；传进来的对象、别的类的字段不跟踪。
  所以它会低估露出的数据，适合比同一张画布的前后两版，不适合当绝对分数。
"""
import ast
import glob
import importlib
import json
import os
import re
import subprocess
import sys

STAGE, CONTENT, REPO = [os.path.abspath(x) for x in sys.argv[1:4]]
LIST = '--list' in sys.argv
sys.path[:0] = [CONTENT, STAGE]
import cv  # noqa: E402


def show(path):
    p = subprocess.run(['git', '-C', REPO, '-c', 'safe.directory=*', 'show', 'HEAD:' + path],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.decode('utf-8', 'replace') if p.returncode == 0 else None


# ---- 把树走一遍，收集 (节点id, code 块)
def walk(n, out):
    if not isinstance(n, dict) or n.get('t') == 'slot':
        return
    for b in n.get('blocks') or []:
        if isinstance(b, dict) and b.get('t') == 'code':
            out.append((n['id'], b))
    for c in n.get('children') or []:
        walk(c, out)


blocks = []
cs = importlib.import_module('content_scene')
walk(cs.root(cv.node('root', '全景', 't', 't')), blocks)
for f in sorted(glob.glob(os.path.join(CONTENT, 'content_*.py'))):
    name = os.path.basename(f)[:-3]
    if name == 'content_scene':
        continue
    m = importlib.import_module(name)
    if hasattr(m, 'nodes'):
        for v in m.nodes().values():
            walk(v, blocks)

LOGROOT = {'logger', 'logging', 'log', 'LOG', '_logger', 'LOGGER', 'warnings', 'print', 'logger_', 'console'}


def root_name(e):
    while isinstance(e, (ast.Attribute, ast.Call, ast.Subscript)):
        e = e.func if isinstance(e, ast.Call) else e.value
    return e.id if isinstance(e, ast.Name) else None


class Info(object):
    """一个 .py 文件：每行的类别 + 每个类的状态字段在哪定义。"""

    def __init__(self, src):
        self.lines = src.split('\n')
        self.cat = {}            # 行号 -> 类别
        self.fields = {}         # 类名 -> {字段: [定义行]}
        self.methods = {}        # 类名 -> set(方法名 / property)
        self.uses = []           # (行号, 类名, 字段)
        try:
            self.tree = ast.parse(src)
        except SyntaxError:
            self.tree = None
            return
        self.visit(self.tree, None, None)

    def mark(self, node, cat, header_only=False):
        a = node.lineno
        b = a if header_only else getattr(node, 'end_lineno', a)
        for i in range(a, b + 1):
            self.cat.setdefault(i, cat)

    def visit(self, node, cls, fn):
        body = getattr(node, 'body', None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) \
                    and isinstance(first.value.value, str):
                self.mark(first, 'doc')
        if isinstance(node, ast.ClassDef):
            for d in node.decorator_list:
                self.mark(d, 'ds')
            for i in range(node.lineno, node.body[0].lineno):
                self.cat.setdefault(i, 'ds')
            self.fields.setdefault(node.name, {})
            self.methods.setdefault(node.name, set())
            for st in node.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.methods[node.name].add(st.name)
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    self.mark(st, 'ds')
                    self.fields[node.name].setdefault(st.target.id, []).append(st.lineno)
                elif isinstance(st, ast.Assign):
                    self.mark(st, 'ds')
                    for t in st.targets:
                        if isinstance(t, ast.Name):
                            self.fields[node.name].setdefault(t.id, []).append(st.lineno)
            for st in node.body:
                self.visit(st, node.name, None)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for st in ast.walk(node):
                if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and root_name(st.value) in LOGROOT:
                    self.mark(st, 'log')
                if cls and isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    ts = st.targets if isinstance(st, ast.Assign) else [st.target]
                    for t in ts:
                        for tt in (t.elts if isinstance(t, ast.Tuple) else [t]):
                            if isinstance(tt, ast.Attribute) and isinstance(tt.value, ast.Name) and tt.value.id == 'self':
                                if node.name in ('__init__', '__post_init__', '__new__'):
                                    self.mark(st, 'ds')
                                    self.fields[cls].setdefault(tt.attr, []).append(st.lineno)
                if cls and isinstance(st, ast.Attribute) and isinstance(st.value, ast.Name) and st.value.id == 'self':
                    self.uses.append((st.lineno, cls, st.attr))
            for st in node.body:
                if isinstance(st, ast.ClassDef):
                    self.visit(st, cls, node.name)
            return
        for st in getattr(node, 'body', []) or []:
            if isinstance(st, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(st, cls, fn)

    def line_cat(self, i):
        s = self.lines[i - 1].strip() if 0 < i <= len(self.lines) else ''
        if not s:
            return 'blank'
        if s.startswith('#'):
            return 'comment'
        return self.cat.get(i, 'logic')


INFO = {}
tot = dict(blank=0, comment=0, doc=0, log=0, ds=0, logic=0, other=0)
nblocks = nparts = nnotes = 0
has_ds_block = 0
pages = {}
shown = {}            # 文件 -> set(行)   全画布
shown_page = {}       # (节点, 文件) -> set(行)
rows = []
for nid, b in blocks:
    f = b['file']
    if f not in INFO:
        src = show(f)
        INFO[f] = (Info(src) if (src is not None and f.endswith('.py')) else None, src)
    info, src = INFO[f]
    nblocks += 1
    nnotes += len(b.get('notes') or [])
    pages.setdefault(nid, 0)
    pages[nid] += 1
    c = dict(blank=0, comment=0, doc=0, log=0, ds=0, logic=0, other=0)
    for a, z in b['parts']:
        nparts += 1
        for i in range(a, z + 1):
            shown.setdefault(f, set()).add(i)
            shown_page.setdefault((nid, f), set()).add(i)
            if info is None or info.tree is None:
                c['other'] += 1
            else:
                c[info.line_cat(i)] += 1
    for k in c:
        tot[k] += c[k]
    n = sum(c.values())
    if c['ds'] >= 3:
        has_ds_block += 1
    rows.append((nid, f, b['parts'], n, c, len(b.get('notes') or [])))

# ---- 逻辑里用到的 self.字段，定义有没有露出
used = {}
for (nid, f), ls in shown_page.items():
    info = INFO[f][0]
    if info is None or info.tree is None:
        continue
    for (ln, cls, attr) in info.uses:
        if ln in ls and attr not in info.methods.get(cls, ()):
            used.setdefault((nid, f, cls, attr), None)
same = anywhere = nodef = 0
miss = []
for (nid, f, cls, attr) in used:
    defs = INFO[f][0].fields.get(cls, {}).get(attr)
    if not defs:
        nodef += 1
        continue
    if any(d in shown_page[(nid, f)] for d in defs):
        same += 1
    elif any(d in shown.get(f, ()) for d in defs):
        anywhere += 1
    else:
        miss.append((nid, cls, attr))
known = len(used) - nodef

total = sum(tot.values())
res = dict(blocks=nblocks, parts=nparts, pages=len(pages), notes=nnotes, lines=total, cats=tot,
           blocks_with_ds=has_ds_block,
           fields_used=len(used), fields_def_known=known, def_on_same_page=same, def_elsewhere_on_canvas=anywhere,
           def_not_shown=len(miss), def_unknown=nodef)
print(json.dumps(res, ensure_ascii=False))
if LIST:
    for nid, f, parts, n, c, k in rows:
        print('%-28s %-52s %-34s %3d行 逻辑%3d 结构%3d 注释/文档%3d 日志%2d 批注%2d' % (
            nid, f[-52:], str(parts)[:34], n, c['logic'], c['ds'], c['comment'] + c['doc'], c['log'], k))
    print('没露出定义的字段:', ', '.join('%s.%s' % (c, a) for _, c, a in sorted(set(miss)))[:1500])
