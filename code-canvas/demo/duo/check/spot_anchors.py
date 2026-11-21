#!/usr/bin/env python3
"""Independent check of a presenter's anchors.json against a repo checkout.
usage: python3 -I spot_anchors.py <anchors.json> <repo-root> [<canvas.html>]
- every item: file exists, 1 <= a <= b <= len(file)
- kind=code: text must equal repo lines a..b (trailing whitespace ignored)
- if canvas.html given: every code line (non-blank, >=12 chars) must appear
  somewhere in the html text (html-unescaped), to catch a sidecar that is not
  what the page really shows.
"""
import sys, json, os, html, random, re

def main():
    aj, repo = sys.argv[1], sys.argv[2]
    page = sys.argv[3] if len(sys.argv) > 3 else None
    items = json.load(open(aj, encoding='utf-8'))
    cache = {}
    def lines(f):
        if f not in cache:
            p = os.path.join(repo, f)
            if not os.path.isfile(p):
                cache[f] = None
            else:
                cache[f] = open(p, encoding='utf-8', errors='replace').read().split('\n')
        return cache[f]
    bad = []; ncode = nref = nlines = 0
    codes = []
    for i, it in enumerate(items):
        f, a, b = it.get('file'), it.get('a'), it.get('b', it.get('a'))
        L = lines(f) if f else None
        if L is None:
            bad.append((i, 'nofile', f)); continue
        if not (isinstance(a, int) and isinstance(b, int) and 1 <= a <= b <= len(L)):
            bad.append((i, 'range', '%s:%s-%s' % (f, a, b))); continue
        if it.get('kind') == 'code':
            ncode += 1
            t = it.get('text')
            if isinstance(t, str): t = t.split('\n')
            src = L[a-1:b]
            if t is None or len(t) != len(src):
                bad.append((i, 'len', '%s:%s-%s page=%s repo=%d' % (f, a, b, None if t is None else len(t), len(src)))); continue
            for k, (x, y) in enumerate(zip(t, src)):
                nlines += 1
                if x.rstrip() != y.rstrip():
                    bad.append((i, 'text', '%s:%d page=%r repo=%r' % (f, a+k, x[:80], y[:80]))); break
            codes.append((f, a, b, t))
        else:
            nref += 1
    print('items=%d code=%d ref=%d code_lines=%d bad=%d' % (len(items), ncode, nref, nlines, len(bad)))
    for x in bad[:40]: print('  BAD', x)
    files = {}
    for it in items: files[it.get('file')] = files.get(it.get('file'), 0) + 1
    print('files:', sorted(files.items(), key=lambda kv: -kv[1])[:12])
    # coverage of performEvictions body by code anchors
    cov = set()
    for f, a, b, t in codes:
        if f == 'src/evict.c': cov.update(range(a, b+1))
    pe = set(range(543, 835))
    print('evict.c lines shown as code: %d ; performEvictions 543-834 covered: %d/%d' % (len(cov), len(cov & pe), len(pe)))
    if page:
        raw = open(page, encoding='utf-8').read()
        txt = html.unescape(raw)
        # also try JSON-escaped form (content embedded as JS data)
        miss = 0; tot = 0
        for f, a, b, t in codes:
            for k, x in enumerate(t):
                s = x.strip()
                if len(s) < 12: continue
                tot += 1
                if s in txt or s in raw: continue
                js = json.dumps(s, ensure_ascii=False)[1:-1]
                if js in raw or js in txt: continue
                js2 = json.dumps(s)[1:-1]
                if js2 in raw: continue
                miss += 1
                if miss <= 10: print('  NOT-IN-PAGE %s:%d %r' % (f, a+k, s[:70]))
        print('code lines found in page: %d/%d' % (tot - miss, tot))
    random.seed(7)
    for f, a, b, t in random.sample(codes, min(6, len(codes))):
        print('  sample %s:%d-%d | %s' % (f, a, b, t[0].strip()[:70]))
    sys.exit(1 if bad else 0)
main()
