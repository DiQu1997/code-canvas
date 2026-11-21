#!/usr/bin/env python3
"""独立核对研究员底稿里的机械事实（不信它自己报的通过数）。py3.8 兼容。

用法: python3 -I spot_manuscript.py <manuscript.md> <仓库路径> [--sample N] [--seed S]

核三样，全部对 HEAD：
- 锚点  `路径:行` / `路径:行-行`：文件在 HEAD 存在，行号在文件长度之内
- 提交  7–40 位十六进制（出现在反引号里，或紧跟「提交 / commit / c:」）：确实是一个 commit
- 引文  反引号 / 「」/ 引号 / Markdown 引用块里、以 ASCII 为主且 ≥ 16 字符的原话：
        能在工作区（= HEAD）的文件里逐字找到（git grep -F），或者在底稿提到的某个提交的说明里逐字找到

另外随机抽 N 个锚点，把仓库里那几行打印出来，供人对着底稿读（机械核对管不了「说的对不对」）。
退出码：有核不过的 = 1。
"""
import os
import random
import re
import subprocess
import sys


def git(repo, *a):
    return subprocess.run(['git', '-C', repo, '-c', 'safe.directory=*', '-c', 'core.hooksPath=/dev/null'] + list(a),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


EXT = r'(?:py|pyi|rs|go|c|h|cc|cpp|hpp|js|mjs|ts|tsx|java|kt|md|rst|txt|toml|yaml|yml|json|proto|sh|cfg|ini|conf|html|css|sql|cmake|mk|in)'
ANCH = re.compile(r'(?<![\w/.-])((?:[\w.+-]+/)*[\w.+-]+\.' + EXT + r'|(?:[\w.+-]+/)*(?:Makefile|Dockerfile)):(\d+)(?:\s*[-–~]\s*(\d+))?')
HASH_BT = re.compile(r'`(?:c:)?([0-9a-f]{7,40})`')
HASH_CTX = re.compile(r'(?:提交|commit|c:)\s*[`（(]?\s*([0-9a-f]{7,40})\b', re.I)


def ascii_ratio(s):
    return sum(1 for ch in s if ord(ch) < 128) / float(max(1, len(s)))


def main():
    argv = sys.argv[1:]
    n_sample, seed = 12, 1
    if '--sample' in argv:
        i = argv.index('--sample'); n_sample = int(argv[i + 1]); del argv[i:i + 2]
    if '--seed' in argv:
        i = argv.index('--seed'); seed = int(argv[i + 1]); del argv[i:i + 2]
    if len(argv) != 2:
        raise SystemExit(__doc__)
    ms, repo = argv
    text = open(ms, encoding='utf-8').read()
    lines = text.split('\n')

    dirty = git(repo, 'status', '--porcelain').stdout.decode('utf-8', 'replace').strip()
    if dirty:
        print('注意：仓库工作区不干净（引文是在工作区里找的）：\n' + dirty[:400])
    tree = set(git(repo, 'ls-tree', '-r', '--name-only', 'HEAD').stdout.decode('utf-8', 'replace').split('\n'))
    base = {}
    for p in tree:
        base.setdefault(os.path.basename(p), []).append(p)
    cache = {}

    def flen(p):
        if p not in cache:
            r = git(repo, 'show', 'HEAD:' + p)
            cache[p] = r.stdout.decode('utf-8', 'replace').split('\n') if r.returncode == 0 else None
        return cache[p]

    # ---- 锚点
    anchors, bad_a, short = [], [], 0
    for ln, L in enumerate(lines, 1):
        for m in ANCH.finditer(L):
            path, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            full = path
            if path not in tree:
                c = [p for p in tree if p.endswith('/' + path)]
                if len(c) == 1:
                    full = c[0]; short += 1
                else:
                    bad_a.append((ln, m.group(0), '文件不在 HEAD' if not c else '路径有歧义（%d 个同名）' % len(c)))
                    continue
            src = flen(full)
            if src is None:
                bad_a.append((ln, m.group(0), '取不到文件内容（部分克隆缺这个 blob？）'))
                continue
            n = len(src) - (1 if src and src[-1] == '' else 0)
            if not (1 <= a <= b <= n):
                bad_a.append((ln, m.group(0), '行号超出（文件共 %d 行）' % n))
                continue
            anchors.append((ln, full, a, b))
    print('锚点 %d 处（去重 %d），通过 %d，不通过 %d；其中 %d 处写的是省略前缀的路径（按唯一后缀匹配）'
          % (len(anchors) + len(bad_a), len(set(x[1:] for x in anchors)) + len(set(x[1] for x in bad_a)),
             len(anchors), len(bad_a), short))
    for ln, s, why in bad_a[:40]:
        print('  BAD 底稿第 %d 行  %s  —— %s' % (ln, s, why))

    # ---- 提交
    hs = set(HASH_BT.findall(text)) | set(HASH_CTX.findall(text))
    ok_h, bad_h, msgs, skipped = [], [], {}, 0
    for h in sorted(hs):
        r = git(repo, 'cat-file', '-t', h)
        if r.returncode == 0 and r.stdout.strip() == b'commit':
            ok_h.append(h)
            msgs[h] = git(repo, 'log', '-1', '--format=%B', h).stdout.decode('utf-8', 'replace')
        elif h.isdigit():       # 纯数字：是提交就算提交（哈希可以碰巧全是数字），不是就当普通数字，不算错
            skipped += 1
        else:
            bad_h.append(h)
    print('提交 %d 个，通过 %d，不通过 %d%s' % (len(ok_h) + len(bad_h), len(ok_h), len(bad_h),
                                      '（另有 %d 个纯数字串不是提交，按普通数字忽略）' % skipped if skipped else ''))
    for h in bad_h:
        print('  BAD 不是一个 commit: %s' % h)

    # ---- 引文
    cands = []
    for ln, L in enumerate(lines, 1):
        if L.lstrip().startswith('>'):
            cands.append((ln, L.lstrip()[1:].strip().strip('`"“”')))
        for pat in (r'`([^`\n]+)`', r'「([^」\n]+)」', r'“([^”\n]+)”', r'"([^"\n]+)"'):   # 先成对切，再按长度筛
            for m in re.finditer(pat, L):
                cands.append((ln, m.group(1).strip()))
    seen, quotes = set(), []
    for ln, q in cands:
        q = q.strip()
        if len(q) < 16 or ascii_ratio(q) < 0.9 or ' ' not in q or q in seen:
            continue
        if ANCH.search(q) and len(ANCH.sub('', q).strip()) < 8:
            continue
        seen.add(q)
        quotes.append((ln, q))
    allmsg = '\n'.join(msgs.values())
    norm_msg = re.sub(r'\s+', ' ', allmsg)
    ncache = {}

    def norm_file(p):       # 把注释折行拼回一句：去掉行首的 # * // 之类，再压空白
        if p not in ncache:
            try:
                t = open(os.path.join(repo, p), encoding='utf-8', errors='replace').read()
            except (IOError, OSError):
                t = ''
            t = re.sub(r'\n[ \t]*(?:#+|\*+|//+!?|--|>+)?[ \t]*', ' ', t)
            ncache[p] = re.sub(r'\s+', ' ', t)
        return ncache[p]

    def found(x):
        if git(repo, 'grep', '-q', '-F', '-e', x).returncode == 0:
            return True
        nx = re.sub(r'\s+', ' ', x)
        w = nx.split(' ')
        probes = []
        for i in (0, max(0, len(w) // 2 - 2), max(0, len(w) - 4)):   # 开头 / 中间 / 结尾各取几个词去找候选文件
            k = i + 1
            while k < len(w) and len(' '.join(w[i:k])) < 18:
                k += 1
            probes.append(' '.join(w[i:k]))
        for pr in probes:
            r = git(repo, 'grep', '-l', '-F', '-e', pr)
            for f in r.stdout.decode('utf-8', 'replace').split('\n')[:30]:
                if f and nx in norm_file(f):
                    return True
        return False

    ok_q, bad_q = 0, []
    for ln, q in quotes:
        if re.sub(r'\s+', ' ', q) in norm_msg:
            ok_q += 1
            continue
        pieces = [q]
        if '...' in q or '…' in q:      # 中间省略过的原话：每一段都要找得到
            pieces = [x.strip() for x in re.split(r'\s*(?:\.\.\.|…)\s*', q) if len(x.strip()) >= 12] or [q]
        if all(found(x) for x in pieces):
            ok_q += 1
        else:
            bad_q.append((ln, q))
    print('疑似引文 %d 条（反引号 / 引号 / 引用块里的英文原话，含代码片段），逐字找到 %d，找不到 %d'
          % (len(quotes), ok_q, len(bad_q)))
    for ln, q in bad_q[:60]:
        print('  ??? 底稿第 %d 行  %s' % (ln, q[:150]))
    if bad_q:
        print('  （找不到 ≠ 一定是编的：可能是跨行原话、行内代码拼写、或作者自己举的例子——逐条人工看）')

    # ---- 抽样打印
    rnd = random.Random(seed)
    uniq = sorted(set(anchors), key=lambda x: (x[1], x[2], x[3], x[0]))
    pick = rnd.sample(uniq, min(n_sample, len(uniq)))
    print('\n==== 随机抽 %d 个锚点，对着底稿读 ====' % len(pick))
    for ln, p, a, b in sorted(pick):
        print('\n--- 底稿第 %d 行: %s' % (ln, lines[ln - 1].strip()[:260]))
        src = flen(p)
        for i in range(a, min(b, a + 11) + 1):
            print('    %s:%d | %s' % (os.path.basename(p), i, src[i - 1][:150]))
        if b > a + 11:
            print('    …（到 %d 行）' % b)
    sys.exit(1 if (bad_a or bad_h) else 0)


if __name__ == '__main__':
    main()
