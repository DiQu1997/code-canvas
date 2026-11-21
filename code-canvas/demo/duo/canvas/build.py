#!/usr/bin/env python3
# build.py —— 把内容树 + 仓库 HEAD 上的真实代码 组装成 canvas.html / anchors.json
# 用法：python3 -I build.py <repo> [outdir] [--strict] [--content <内容目录>]
#   outdir 默认是内容目录（不给 --content 时就是本脚本所在目录）；多人并行时各自给不同的 outdir，互不覆盖。
#   --strict：有未填槽位 / WARN / 模块出错时退出码 1（最终装配时用）。
#   --content <目录>：换一个项目。content_*.py 从那个目录读，舞台（cv / 模板 / 自检）还是本目录这一套。
#       那个目录里没有 content_a.py 时是「顶层是机器图」的项目：content_scene.py 给 META + root(base)，只出 scene.html。
#       下层各页（又一张机器图 / 源码页）放在 root(base) 返回的 children 里，或用 cv.slot 留给别的 content_*.py 填。
# 代码一律由本脚本按「文件:行号」从 git HEAD 取，内容文件里不写任何代码正文。
# 装配：顶层模块（content_a.py，或顶层是机器图时的 content_scene.py）给 META + 树；树里的 cv.slot(id) 由其它 content_*.py 的
#       nodes() -> {槽位id: 节点} 填；没人填 / 模块出错 / 内容构建失败 → 「未展开」占位页。
import sys, os, re, json, html, glob, subprocess, importlib, traceback

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


class BuildError(Exception):
    pass


_argv = sys.argv[1:]
CONTENT = HERE
if '--content' in _argv:
    _i = _argv.index('--content')
    if _i + 1 >= len(_argv) or _argv[_i + 1].startswith('--'):
        raise SystemExit('--content 后面要跟内容目录')
    CONTENT = os.path.abspath(_argv[_i + 1])
    del _argv[_i:_i + 2]
    if not os.path.isdir(CONTENT):
        raise SystemExit('内容目录不存在: %s' % CONTENT)
_pos = [a for a in _argv if not a.startswith('--')]
STRICT = '--strict' in _argv
if not _pos:
    raise SystemExit('用法: python3 -I build.py <repo> [outdir] [--strict] [--content <内容目录>]')
REPO = _pos[0]
OUT = os.path.abspath(_pos[1]) if len(_pos) > 1 else CONTENT
SCENE_ONLY = not os.path.exists(os.path.join(CONTENT, 'content_a.py'))
if SCENE_ONLY and not os.path.exists(os.path.join(CONTENT, 'content_scene.py')):
    raise SystemExit('内容目录里既没有 content_a.py 也没有 content_scene.py: %s' % CONTENT)
GIT = ['git', '-C', REPO, '-c', 'safe.directory=*', '-c', 'core.hooksPath=/dev/null',
       '-c', 'core.fsmonitor=false']

import cv  # noqa: E402

if CONTENT != HERE:         # 别的项目：内容模块只从它自己的目录找（cv 已经载入；本目录那几份 content_*.py 不能混进去）
    sys.path.remove(HERE)
    sys.path.insert(0, CONTENT)
SLOTS = {}      # 槽位的默认标题。舞台自己不带任何项目的表：每次构建从项目的顶层模块读（没写就是空的）

# ------------------------------------------------------------------ 仓库读取（只读 HEAD）
_files, _cinfo = {}, {}
PATH_RE = re.compile(r'^[\w][\w./-]*$')
HASH_RE = re.compile(r'^[0-9a-f]{7,40}$')


def git(*args):
    return subprocess.run(GIT + list(args), capture_output=True)


def flines(path):
    if path not in _files:
        if not isinstance(path, str) or not PATH_RE.match(path):
            raise BuildError('文件路径格式不对: %r' % (path,))
        out = git('show', 'HEAD:' + path)
        if out.returncode != 0:
            raise BuildError('仓库 HEAD 里取不到文件: %s' % path)
        _files[path] = out.stdout.decode('utf-8', 'replace').split('\n')
    return _files[path]


def getlines(path, a, b):
    L = flines(path)
    if not isinstance(a, int) or not isinstance(b, int) or a < 1 or b > len(L) or a > b:
        raise BuildError('行号越界: %s:%s-%s (文件 %d 行)' % (path, a, b, len(L)))
    return L[a - 1:b]


def commit(hx):
    if not HASH_RE.match(hx):
        raise BuildError('提交哈希格式不对（要 7–40 位小写十六进制）: %s' % hx)
    if hx not in _cinfo:
        out = git('log', '-1', '--format=%H%x00%ad%x00%s%x00%B', '--date=short', hx, '--')
        if out.returncode != 0 or not out.stdout:
            raise BuildError('仓库里解析不了这个提交: %s' % hx)
        full, date, subj, body = out.stdout.decode('utf-8', 'replace').split('\x00', 3)
        _cinfo[hx] = dict(full=full, date=date, subject=subj, body=body)
    c = _cinfo[hx]
    COMMITS[hx] = dict(full=c['full'], date=c['date'], subject=c['subject'])
    return c


# ------------------------------------------------------------------ 构建状态（可回滚）
SNIPS, COMMITS, NAV, OWNER, PLACED = {}, {}, {}, {}, {}
LINKS, WARN, SLOT_ERR = [], [], []     # WARN: (模块, 节点id, 文字)
PROVIDERS = {}                          # 槽位id -> (模块名, 原始节点)
META = {}
LEVELS, LVCLS, TAGS, WHO = [], {}, [], []
SNIP_MAX, NAV_MAX, NOTE_MAX, CODE_MAX = 30, 12, 160, 120
CLS_OK = ('e1', 'e2', 'e3', 'e4', 'fact', 'infer', 'view', 'unk')
EV_CLS = ('e1', 'e2', 'e3', 'e4')


def reset():
    for d in (SNIPS, COMMITS, NAV, OWNER, PLACED):
        d.clear()
    del LINKS[:], WARN[:], SLOT_ERR[:]


def snapshot():
    return (dict(SNIPS), dict(COMMITS), dict(NAV), dict(OWNER), dict(PLACED),
            len(LINKS), len(WARN), len(SLOT_ERR))


def restore(s):
    for d, old in ((SNIPS, s[0]), (COMMITS, s[1]), (NAV, s[2]), (OWNER, s[3]), (PLACED, s[4])):
        d.clear()
        d.update(old)
    del LINKS[s[5]:], WARN[s[6]:], SLOT_ERR[s[7]:]


REF_RE = re.compile(r'^([\w./-]+\.(?:c|h|conf|tcl|md|json|py|sh|txt|lua|yml|yaml)):(\d+)(?:-(\d+))?$')
REFISH_RE = re.compile(r'^[\w./-]+:\d+(?:-\d+)?$')
LINK_RE = re.compile(r'\[\[([\w.]+)(?:\|([^\]]+))?\]\]')
ID_RE = re.compile(r'^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$')


class Ctx:
    def __init__(self, nid, mod):
        self.nid, self.mod, self.refs = nid, mod, []

    def ref(self, f, a, b):
        k = '%s:%d-%d' % (f, a, b)
        getlines(f, a, b)
        if k not in self.refs:
            self.refs.append(k)
        if b - a + 1 <= SNIP_MAX and k not in SNIPS:
            SNIPS[k] = dict(file=f, a=a, b=b, lines=getlines(f, a, b))
        return k


def warn(cx, msg):
    WARN.append((cx.mod, cx.nid, msg))


def width(s):
    return sum(1 if ord(c) > 0x2e7f else 0.5 for c in s)


def chip(tok, cx):
    m = REF_RE.match(tok)
    if m:
        f, a = m.group(1), int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        k = cx.ref(f, a, b)
        lab = '%s:%d' % (os.path.basename(f), a) + ('–%d' % b if b > a else '')
        return '<a class="ref" data-k="%s" title="%s">%s</a>' % (k, html.escape(tok), lab)
    if tok.startswith('c:'):
        hx = tok[2:]
        commit(hx)
        return '<a class="cm" data-h="%s">%s</a>' % (hx, hx)
    if REFISH_RE.match(tok):
        warn(cx, '`%s` 像锚点但扩展名不在识别范围内，被当成了行内代码' % tok)
    return '<code>%s</code>' % html.escape(tok, quote=False)


def fmt(s, cx):
    if s is None:
        return None
    if not isinstance(s, str):
        raise BuildError('这里要字符串，拿到的是 %s: %r' % (type(s).__name__, s))
    parts = s.split('`')
    if len(parts) % 2 == 0:
        warn(cx, '反引号不成对: %s' % s[:40])
    out = []
    for i, seg in enumerate(parts):
        if i % 2:
            out.append(chip(seg, cx))
        else:
            t = html.escape(seg, quote=False)
            t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)

            def lk(m):
                LINKS.append((cx.nid, m.group(1)))
                return '<a class="lk" href="#/%s">%s</a>' % (m.group(1), m.group(2) or '\x00NAV:%s\x00' % m.group(1))
            out.append(LINK_RE.sub(lk, t))
    return ''.join(out)


# ------------------------------------------------------------------ 引文核对
def _norm(s):
    s = re.sub(r'/\*+|\*+/', ' ', s)
    s = re.sub(r'(?m)^[ \t]*(?:\*+|//+|#+)(?=\s|$)', ' ', s)     # 行首注释符
    return re.sub(r'\s+', ' ', s).strip().lower()


def quote_ok(q, hays):
    """q 里可用 … 或 ... 省略中间；各片段须按顺序出现在锚点范围（或提交说明）里。忽略空白和注释符。"""
    hay, pos = _norm('\n'.join(hays)), 0
    for frag in re.split(r'…+|\.{3,}', q):
        f = _norm(frag)
        if not f:
            continue
        i = hay.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def chk_lv(lv, cx):
    if lv is not None and lv not in LEVELS:
        warn(cx, '证据等级 %r 不在 META.levels 里（可用：%s；纯说明用 None）' % (lv, '/'.join(LEVELS)))


def do_note(n, cx, counter, codefile=None):
    counter[0] += 1
    lv = n.get('lv')
    chk_lv(lv, cx)
    if n.get('tag') is not None and n['tag'] not in TAGS:
        warn(cx, '标签 %r 不在 META.tags 里（可用：%s）' % (n['tag'], '/'.join(TAGS)))
    if n.get('who') is not None and n['who'] not in WHO:
        warn(cx, 'who=%r 不在 META.who 里（可用：%s）' % (n['who'], '/'.join(WHO)))
    r = dict(t='note', k='n%d' % counter[0], lv=lv, html=fmt(n['text'], cx),
             tag=n.get('tag'), who=n.get('who'), more=fmt(n.get('more'), cx), ev=[])
    plain = re.sub(r'\[\[[\w.]+\||\]\]|\[\[|\*\*|`', '', n['text'])
    if len(plain) > NOTE_MAX:
        warn(cx, '解释偏长（%d 字 > %d）：把细节挪进 more= 折叠：%s…' % (len(plain), NOTE_MAX, plain[:20]))
    if 'a' in n:
        r['a'], r['b'] = n['a'], n['b']
    for e in n['ev']:
        chk_lv(e['lv'], cx)
        refs = list(e['refs'])
        if not refs and e['q'] and codefile:
            refs = ['%s:%d-%d' % (codefile, n['a'], n['b'])]
        raw, hay = [], []
        for x in refs:
            if x.startswith('c:'):
                hay.append(commit(x[2:])['body'])
                raw.append(x)
            else:
                m = REF_RE.match(x)
                if not m:
                    raise BuildError('证据锚点格式不对: %s' % x)
                a = int(m.group(2))
                b = int(m.group(3) or a)
                hay.append('\n'.join(getlines(m.group(1), a, b)))
                raw.append('%s:%d-%d' % (m.group(1), a, b))
        if e['q']:
            if not refs:
                warn(cx, '有引文但没给 ref，没法核对：“%s”' % e['q'][:40])
            elif not quote_ok(e['q'], hay):
                warn(cx, '引文在锚点范围里对不上 [%s]：“%s”' % (' '.join(raw), e['q'][:60]))
        r['ev'].append(dict(lv=e['lv'], q=e['q'], raw=raw,
                            rh=' '.join(chip(x, cx) for x in refs)))
    if LVCLS.get(lv) in EV_CLS and not r['ev'] and not codefile:
        warn(cx, '标了「%s」却没给 q/ref 依据：%s…' % (lv, plain[:20]))
    return r


def chk_rect(r, W, H, what, cx, need):
    if r is None:
        if need:
            raise BuildError('%s 必须给 [x, y, w, h]' % what)
        return
    if not isinstance(r, (list, tuple)) or len(r) != 4 or not all(isinstance(v, (int, float)) for v in r):
        raise BuildError('%s 要写成 [x, y, w, h] 四个数，拿到 %r' % (what, r))
    if r[0] < 0 or r[1] < 0 or r[0] + r[2] > W or r[1] + r[3] > H:
        warn(cx, '%s=%r 超出画布 %dx%d，超出部分会被裁掉' % (what, list(r), W, H))


# ------------------------------------------------------------------ 机器图（scene）
SC_KINDS = ('act', 'store', 'ext', 'bg', 'gate', 'out')
SC_SIDES = ('l', 'r', 't', 'b')
SC_GLYPHS = ('cells', 'gauge', 'list', 'kv', 'checks', 'text', 'raw')
CARD_KEYS = (('does', '这里是干什么的'), ('how', '怎么设计的'), ('why', '为什么这么设计'))
ITEM_MAX, WLABEL_MAX, WONE_MAX = 220, 12, 18
SC_STAT = dict(parts=0, wires=0, steps=0, cards=0)


def plain_w(s):
    """去掉迷你语法记号后的显示宽度（汉字算 1，其余算 0.5）。"""
    s = re.sub(r'\[\[[^\]|]*\|([^\]]*)\]\]', r'\1', s)
    return width(s.replace('`', '').replace('**', ''))


def do_glyph(g, cx, what):
    if g is None:
        return None
    if not isinstance(g, dict) or g.get('g') not in SC_GLYPHS:
        raise BuildError('%s 的 glyph 要用 cv.g_cells / g_gauge / g_list / g_kv / g_checks / g_text / g_raw 来造' % what)
    k = g['g']
    if k == 'cells':
        cells = []
        for c in g['cells']:
            if not isinstance(c, dict) or 'v' not in c:
                raise BuildError('%s 的 g_cells 里每个格子要用 cv.cell(k, v, s)' % what)
            cells.append(dict(k=c.get('k'), v=str(c.get('v', '')), s=c.get('s')))
        return dict(g=k, cells=cells, left=g.get('left'), right=g.get('right'))
    if k == 'gauge':
        for f in ('used', 'limit'):
            if not isinstance(g.get(f), (int, float)):
                raise BuildError('%s 的 g_gauge 的 %s 要是数字' % (what, f))
        tot = g.get('total') or max(g['used'], g['limit']) * 1.15 or 1
        return dict(g=k, used=g['used'], limit=g['limit'], total=tot, label=fmt(g.get('label'), cx), unit=g.get('unit') or '')
    if k == 'list':
        return dict(g=k, rows=[fmt(r, cx) for r in g['rows']], on=[i for i in g.get('on') or [] if isinstance(i, int)])
    if k == 'kv':
        return dict(g=k, rows=[[fmt(str(r[0]), cx), fmt(str(r[1]), cx)] for r in g['rows']])
    if k == 'checks':
        rows = []
        for r in g['rows']:
            if r[1] not in ('ok', 'no', 'skip', None):
                raise BuildError("%s 的 g_checks 状态只能是 'ok' / 'no' / 'skip' / None" % what)
            rows.append([fmt(str(r[0]), cx), r[1]])
        return dict(g=k, rows=rows)
    if k == 'text':
        return dict(g=k, big=html.escape(str(g['big'])), small=fmt(g.get('small'), cx), tone=g.get('tone'))
    return dict(g=k, html=str(g.get('html') or ''))


def do_items(lst, cx, counter, what):
    out = []
    for it in lst or []:
        if isinstance(it, str):
            if len(it) > ITEM_MAX:
                warn(cx, '%s 有一段 %d 字（上限 %d），拆成两条' % (what, len(it), ITEM_MAX))
            out.append(dict(t='p', html=fmt(it, cx)))
        elif isinstance(it, dict) and it.get('t') == 'note':
            out.append(do_note(it, cx, counter))
        else:
            raise BuildError('%s 的条目只能是一段字或 cv.note(...)' % what)
    return out


def do_card(c, cx, counter, what):
    if not isinstance(c, dict):
        warn(cx, '%s 没有说明卡（每个部件、每条连线都要 card(干什么, 怎么设计, 为什么这么设计)）' % what)
        return None
    r = dict((k, do_items(c.get(k), cx, counter, '%s 的说明卡' % what)) for k in ('does', 'how', 'why', 'traps'))
    for k, nm in CARD_KEYS:
        if not r[k]:
            warn(cx, '%s 的说明卡缺「%s」一栏' % (what, nm))
    for it in r['why']:
        if it['t'] != 'note':
            warn(cx, '%s 的说明卡「为什么这么设计」里有一条没标依据等级（要用 note(等级, …)）' % what)
    SC_STAT['cards'] += 1
    return r


def do_scene(b, cx, counter):
    W, H, mw, mh = b['w'], b['h'], b.get('mw'), b.get('mh')
    for v in (W, H):
        if not isinstance(v, (int, float)) or v <= 0:
            raise BuildError('机器图的 w / h 要是正数')
    if (mw is None) != (mh is None):
        raise BuildError('机器图的 mw / mh 要么都给要么都不给')
    if not mw:
        warn(cx, '机器图没给手机画幅（mw / mh + 每个部件的 m）：手机上会把桌面版整体缩小，字会很小')
    if H > 520:
        warn(cx, '机器图桌面画幅高 %d（建议 ≤ 520）：1440×900 的屏上全景会缩到约 %.2f 倍，框里的字偏小；'
                 '把部件排得更横一些，或者把次要部件挪进说明卡' % (H, min(1.0, 641.0 / (H + 186))))
    ids, parts, wires = {}, [], []

    def one(s, what, budget):
        if s is None:
            return None
        if not isinstance(s, str):
            raise BuildError('%s 要是一句字' % what)
        w_ = plain_w(s)
        if w_ > budget:
            warn(cx, '%s 太长（约 %.0f 字宽，这里放得下约 %d）：会被截断，缩成一句短话，长的放说明卡' % (what, w_, budget))
        return fmt(s, cx)

    def tos(x, what):
        r = []
        for i in x.get('to') or []:
            if not isinstance(i, str) or not ID_RE.match(i):
                raise BuildError('%s 的 to 要是节点 id' % what)
            LINKS.append((cx.nid, i))
            r.append(i)
        return r

    for x in b['parts']:
        pid = x.get('id')
        if not isinstance(pid, str) or not re.match(r'^[A-Za-z0-9_]+$', pid):
            raise BuildError('部件 id 只能用字母数字下划线: %r' % (pid,))
        if pid in ids:
            raise BuildError('机器图里 id 重复: %s' % pid)
        what = '部件 %s' % pid
        if x.get('kind') not in SC_KINDS:
            raise BuildError("%s 的 kind 只能是 %s" % (what, ' / '.join(SC_KINDS)))
        chk_rect(x.get('d'), W, H, what + ' 的 d', cx, True)
        if mw:
            chk_rect(x.get('m'), mw, mh, what + ' 的 m', cx, True)
        bw = min([r[2] for r in (x.get('d'), x.get('m') if mw else None) if r])
        cap_ = max(4, int((bw - 22) // 13))
        ids[pid] = 'p'
        parts.append(dict(
            id=pid, name=html.escape(str(x['name'])), code=x.get('code'), kind=x['kind'],
            n=None if x.get('n') is None else str(x['n']), d=x['d'], m=x.get('m') if mw else None,
            glyph=do_glyph(x.get('glyph'), cx, what),
            say=one(x.get('say'), what + ' 的 say', 2 * cap_),
            why=one(x.get('why'), what + ' 的 why', 2 * cap_ - 3),
            trap=one(x.get('trap'), what + ' 的 trap', 2 * cap_ - 2),
            card=do_card(x.get('card'), cx, counter, what), to=tos(x, what)))
        if not x.get('say'):
            warn(cx, '%s 没有 say（主流程图层下框底那句「它干什么」）' % what)

    for key, lim_w, lim_h in (('d', W, H), ('m', mw, mh)):
        rs = [(p_['id'], p_[key]) for p_ in parts if p_.get(key)]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a_, b_ = rs[i][1], rs[j][1]
                if a_[0] < b_[0] + b_[2] and b_[0] < a_[0] + a_[2] and a_[1] < b_[1] + b_[3] and b_[1] < a_[1] + a_[3]:
                    warn(cx, '部件 %s 和 %s 的 %s 矩形叠在一起了' % (rs[i][0], rs[j][0], key))

    for x in b['wires']:
        wid = x.get('id')
        if not isinstance(wid, str) or not re.match(r'^[A-Za-z0-9_]+$', wid):
            raise BuildError('连线 id 只能用字母数字下划线: %r' % (wid,))
        if wid in ids:
            raise BuildError('机器图里 id 重复: %s' % wid)
        what = '连线 %s' % wid
        for end in ('f', 't'):
            if ids.get(x.get(end)) != 'p':
                raise BuildError('%s 的 %s=%r 不是图里的部件' % (what, end, x.get(end)))
        for sd in ('fs', 'ts', 'mfs', 'mts'):
            if x.get(sd) not in (None,) + SC_SIDES:
                raise BuildError("%s 的 %s 只能是 'l' 'r' 't' 'b'" % (what, sd))
        if x.get('cls') not in (None, 'dash'):
            raise BuildError("%s 的 cls 只能是 'dash'" % what)
        for key, lw, lh in (('lp', W, H), ('mlp', mw, mh)):
            v = x.get(key)
            if v is not None and not (isinstance(v, (list, tuple)) and len(v) == 2):
                raise BuildError('%s 的 %s 要是 [x, y]' % (what, key))
        if not x.get('label'):
            raise BuildError('%s 没有 label' % what)
        for key in ('label', 'ml'):
            if x.get(key) and plain_w(x[key]) > WLABEL_MAX:
                warn(cx, '%s 的 %s 太长（约 %.0f 字宽，上限 %d）：线上只放几个字，长的放说明卡' % (what, key, plain_w(x[key]), WLABEL_MAX))
        wires.append(dict(
            id=wid, f=x['f'], t=x['t'], label=html.escape(x['label']),
            ml=None if x.get('ml') is None else html.escape(x['ml']),
            n=None if x.get('n') is None else str(x['n']),
            why=one(x.get('why'), what + ' 的 why', WONE_MAX), trap=one(x.get('trap'), what + ' 的 trap', WONE_MAX),
            card=do_card(x.get('card'), cx, counter, what), to=tos(x, what),
            fs=x.get('fs'), ts=x.get('ts'), mfs=x.get('mfs'), mts=x.get('mts'),
            lp=x.get('lp'), mlp=x.get('mlp'), cls=x.get('cls')))
        ids[wid] = 'w'

    groups = []
    for g in b.get('groups') or []:
        chk_rect(g.get('d'), W, H, '分组框「%s」的 d' % g.get('label'), cx, True)
        if mw and g.get('m') is not None:
            chk_rect(g['m'], mw, mh, '分组框「%s」的 m' % g.get('label'), cx, True)
        groups.append(dict(label=g['label'], d=g['d'], m=g.get('m') if mw else None))

    steps = []
    for i, s in enumerate(b.get('steps') or []):
        what = '推演第 %d 步' % (i + 1)
        if not isinstance(s.get('title'), str) or not isinstance(s.get('what'), str):
            raise BuildError('%s 要有 title 和 what' % what)
        if len(s['what']) > ITEM_MAX:
            warn(cx, '%s 的 what 有 %d 字（上限 %d）' % (what, len(s['what']), ITEM_MAX))
        for f in s.get('focus') or []:
            if f not in ids:
                raise BuildError('%s 的 focus 里 %r 不是图里的部件 / 连线' % (what, f))
        wy = s.get('why')
        if wy is None:
            warn(cx, '%s 没写「为什么这样」' % what)
        elif isinstance(wy, str):
            warn(cx, '%s 的「为什么这样」没标依据等级（要用 note(等级, …)）' % what)
            wy = dict(t='p', html=fmt(wy, cx))
        elif isinstance(wy, dict) and wy.get('t') == 'note':
            wy = do_note(wy, cx, counter)
        else:
            raise BuildError('%s 的 why 只能是一段字或 cv.note(...)' % what)
        state = {}
        for pid, g in (s.get('state') or {}).items():
            if ids.get(pid) != 'p':
                raise BuildError('%s 的 state 里 %r 不是图里的部件' % (what, pid))
            state[pid] = do_glyph(g, cx, '%s 的 state[%s]' % (what, pid))
        steps.append(dict(title=html.escape(s['title']), what=fmt(s['what'], cx), why=wy,
                          focus=list(s.get('focus') or []), state=state))

    SC_STAT['parts'] += len(parts)
    SC_STAT['wires'] += len(wires)
    SC_STAT['steps'] += len(steps)
    return dict(t='scene', w=W, h=H, mw=mw, mh=mh, parts=parts, wires=wires, groups=groups,
                steps=steps, cap=fmt(b.get('cap'), cx))


def do_block(b, cx, counter):
    t = b['t']
    if t == 'p':
        return dict(t='p', html=fmt(b['text'], cx))
    if t == 'h':
        return dict(t='h', text=b['text'])
    if t == 'note':
        return do_note(b, cx, counter)
    if t == 'code':
        parts, total = [], 0
        for a, bb in b['parts']:
            parts.append(dict(a=a, b=bb, lines=getlines(b['file'], a, bb)))
            total += bb - a + 1
        if total > CODE_MAX:
            warn(cx, '一个代码块 %d 行（> %d），考虑拆成子节点或用多段省略' % (total, CODE_MAX))
        notes = []
        for n in b['notes']:
            if 'a' not in n:
                raise BuildError('code 的 notes 里要用 at(...)，不是 note(...)')
            if not any(pt['a'] <= n['a'] and n['b'] <= pt['b'] for pt in parts):
                warn(cx, '批注不在所示代码范围内 %s:%d-%d' % (b['file'], n['a'], n['b']))
            txt = '\n'.join(getlines(b['file'], n['a'], n['b']))
            if n.get('has') and n['has'].lower() not in txt.lower():
                warn(cx, 'has 不中 %s:%d-%d 期望含 %r，实际：%s' % (
                    b['file'], n['a'], n['b'], n['has'], ' ⏎ '.join(x.strip() for x in txt.split('\n'))[:200]))
            notes.append(do_note(n, cx, counter, b['file']))
        return dict(t='code', file=b['file'], parts=parts, notes=notes, cap=fmt(b.get('cap'), cx))
    if t == 'table':
        for r in b['rows']:
            if len(r) != len(b['head']):
                warn(cx, '表格有一行 %d 格，表头 %d 格' % (len(r), len(b['head'])))
        return dict(t='table', head=b['head'], rows=[[fmt(c, cx) for c in r] for r in b['rows']],
                    cap=fmt(b.get('cap'), cx))
    if t == 'flow':
        st = []
        for s in b['steps']:
            if s.get('to'):
                LINKS.append((cx.nid, s['to']))
            for o in s.get('out') or []:
                if o.get('lv') not in ('ok', 'run', 'fail'):
                    warn(cx, 'flow 出口的 lv 只能是 ok/run/fail，拿到 %r' % (o.get('lv'),))
            st.append(dict(k=s['k'], title=fmt(s['title'], cx), sub=fmt(s.get('sub'), cx), to=s.get('to'),
                           out=s.get('out') or [], loop=s.get('loop'), ref=fmt(s.get('ref'), cx)))
        return dict(t='flow', steps=st, cap=fmt(b.get('cap'), cx))
    if t == 'diagram':
        W, H, MW, MH = b['w'], b['h'], b.get('mw'), b.get('mh')
        if bool(MW) != bool(MH):
            raise BuildError('diagram 的 mw / mh 要么都给，要么都不给')
        boxes, ids = [], set()
        for x in b['boxes']:
            if x['id'] in ids:
                raise BuildError('图里框 id 重复: %s' % x['id'])
            ids.add(x['id'])
            chk_rect(x.get('d'), W, H, '框 %s 的 d' % x['id'], cx, True)
            if MW:
                chk_rect(x.get('m'), MW, MH, '框 %s 的 m' % x['id'], cx, False)
                if x.get('m') is None:
                    warn(cx, '框 %s 没给 m：手机布局里它（和连着它的线）不会出现' % x['id'])
            elif x.get('m') is not None:
                warn(cx, '框 %s 给了 m，但 diagram 没给 mw/mh，手机布局不会启用' % x['id'])
            if x.get('kind') not in ('act', 'store', 'ext', 'bg'):
                warn(cx, '框 %s 的 kind=%r 不认识（act/store/ext/bg）' % (x['id'], x.get('kind')))
            if x.get('to'):
                LINKS.append((cx.nid, x['to']))
            boxes.append(dict(x))
        for e in b['edges']:
            if e['f'] not in ids or e['t'] not in ids:
                raise BuildError('图里的边指向不存在的框: %s->%s' % (e['f'], e['t']))
        for g in b.get('frames') or []:
            chk_rect(g.get('d'), W, H, '分组框 %s 的 d' % g.get('label'), cx, True)
            if MW:
                chk_rect(g.get('m'), MW, MH, '分组框 %s 的 m' % g.get('label'), cx, False)
        r = dict(b)
        r['boxes'] = boxes
        r['cap'] = fmt(b.get('cap'), cx)
        return r
    if t == 'frames':
        if not b['steps']:
            raise BuildError('frames 至少要一步')
        nc = set()
        for s in b['steps']:
            if not isinstance(s.get('cells'), list):
                raise BuildError('frames 的每一步要有 cells 列表（用 cv.frame / cv.cell 构造）')
            nc.add(len(s['cells']))
        if len(nc) > 1:
            warn(cx, 'frames 各步格子数不一致 %s，翻页时会跳动' % sorted(nc))
        r = dict(b)
        r['steps'] = [dict(s, say=fmt(s.get('say'), cx)) for s in b['steps']]
        r['cap'] = fmt(b.get('cap'), cx)
        return r
    if t == 'bar':
        for s in b['segs']:
            if not isinstance(s.get('w'), (int, float)) or s['w'] <= 0 or s.get('kind') not in ('in', 'out') or not s.get('label'):
                raise BuildError('bar 的每一段要有 w>0、kind in/out、label（用 cv.seg 构造）: %r' % (s,))
        r = dict(b)
        r['cap'] = fmt(b.get('cap'), cx)
        return r
    if t == 'collect':
        if b['by'] not in ('tag', 'lv'):
            raise BuildError("collect 的 by 只能是 'tag' 或 'lv'")
        for k in b['keys']:
            if k not in (TAGS if b['by'] == 'tag' else LEVELS):
                warn(cx, 'collect 的键 %r 不在 META 的%s里' % (k, '标签' if b['by'] == 'tag' else '等级'))
        return dict(b)
    if t == 'commits':
        return dict(b)
    if t == 'scene':
        return do_scene(b, cx, counter)
    raise BuildError('未知块类型 %s' % t)


# ------------------------------------------------------------------ 树的装配
def stub_node(sid, title, why):
    title = title or SLOTS.get(sid) or sid
    nav = re.split('[：:]', title)[0]
    NAV[sid], OWNER[sid] = nav, '-'
    return dict(id=sid, nav=nav, title=title, short='这一节还没有内容。', lead=None, src=None,
                blocks=[], refs=[], children=[], stub=why)


def do_node(n, mod, prefix):
    nid = n.get('id')
    if not isinstance(nid, str) or not ID_RE.match(nid):
        raise BuildError('节点 id 只能用英文字母/数字/下划线，用点分层: %r' % (nid,))
    if nid in NAV:
        raise BuildError('节点 id 重复: %s（已被 %s 用了）' % (nid, OWNER.get(nid)))
    if prefix and nid != prefix and not nid.startswith(prefix + '.'):
        raise BuildError('节点 id %s 必须以 "%s." 开头（槽位id.子名）' % (nid, prefix))
    for k in ('nav', 'title', 'short'):
        if not isinstance(n.get(k), str) or not n[k].strip():
            raise BuildError('节点 %s 缺 %s（要非空字符串）' % (nid, k))
    cx, counter = Ctx(nid, mod), [0]
    NAV[nid], OWNER[nid] = n['nav'], mod
    if width(n['nav']) > NAV_MAX:
        warn(cx, 'nav 偏长（%s）：面包屑和地图里会被截断，建议 ≤ %d 个汉字宽' % (n['nav'], NAV_MAX))
    try:
        short, lead = fmt(n['short'], cx), fmt(n.get('lead'), cx)
    except BuildError as e:
        raise BuildError('节点 %s 的 short/lead: %s' % (nid, e))
    blocks = []
    for i, b in enumerate(n.get('blocks') or []):
        bt = b.get('t') if isinstance(b, dict) else None
        try:
            if bt is None:
                raise BuildError('blocks 里要放 cv 的块（p/note/code/…），拿到的是 %s' % type(b).__name__)
            blocks.append(do_block(b, cx, counter))
        except BuildError as e:
            raise BuildError('节点 %s 第 %d 个块(%s): %s' % (nid, i + 1, bt, e))
        except Exception as e:
            raise BuildError('节点 %s 第 %d 个块(%s) 数据结构不对: %s: %s' % (nid, i + 1, bt, type(e).__name__, e))
    r = dict(id=nid, nav=n['nav'], title=n['title'], short=short, lead=lead, src=n.get('src'),
             blocks=blocks, refs=cx.refs)
    r['children'] = [build_tree(c, mod, prefix) for c in n.get('children') or []]
    return r


def build_tree(raw, mod, prefix):
    if not isinstance(raw, dict):
        raise BuildError('children 里要放 node(...) 或 slot(...)，拿到的是 %s' % type(raw).__name__)
    if raw.get('t') == 'slot':
        return fill_slot(raw)
    return do_node(raw, mod, prefix)


def fill_slot(raw):
    sid = raw.get('id')
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise BuildError('槽位 id 不合法: %r' % (sid,))
    if sid in PLACED or sid in NAV:
        raise BuildError('槽位 %s 在树里放了两次（或和已有节点 id 撞了）' % sid)
    PLACED[sid] = None
    if sid not in PROVIDERS:
        return stub_node(sid, raw.get('title'), '还没有模块提供这个槽位')
    pmod, pnode = PROVIDERS[sid]
    snap = snapshot()
    try:
        if not isinstance(pnode, dict) or 'id' not in pnode:
            raise BuildError('nodes()[%r] 不是 node(...)' % sid)
        if pnode['id'] != sid:
            raise BuildError('nodes() 的键是 %r，节点自己的 id 却是 %r，两者必须相同' % (sid, pnode['id']))
        r = do_node(pnode, pmod, sid)
        PLACED[sid] = pmod
        return r
    except Exception as e:          # 一个槽位坏了不连累别的槽位
        restore(snap)
        PLACED[sid] = None
        msg = str(e) if isinstance(e, BuildError) else '%s: %s' % (type(e).__name__, e)
        SLOT_ERR.append((sid, pmod, msg))
        return stub_node(sid, raw.get('title'), '模块 %s 的内容构建失败，见构建输出' % pmod)


def walk(n, f):
    f(n)
    for c in n['children']:
        walk(c, f)


def brief(e):
    loc = ''
    for fr in reversed(traceback.extract_tb(e.__traceback__)):
        fn = os.path.abspath(fr.filename)
        if os.path.dirname(fn) in (HERE, CONTENT) and os.path.basename(fn) != 'build.py':
            loc = ' (%s:%d)' % (os.path.basename(fn), fr.lineno)
            break
    if isinstance(e, SyntaxError):
        loc = ' (%s:%s)' % (os.path.basename(e.filename or '?'), e.lineno)
    return '%s: %s%s' % (type(e).__name__, e, loc)


def load_modules():
    mods, errs = {}, []
    for path in sorted(glob.glob(os.path.join(CONTENT, 'content_*.py'))):
        name = os.path.basename(path)[:-3]
        if name in ('content_a', 'content_scene'):
            continue
        try:
            d = importlib.import_module(name).nodes()
            if not isinstance(d, dict):
                raise BuildError('nodes() 应返回 {槽位id: node(...)} 字典')
            mods[name] = d
        except KeyboardInterrupt:
            raise
        except BaseException as e:      # 包括 SyntaxError / SystemExit：一个模块坏了不连累别人
            errs.append((name, brief(e)))
    return mods, errs


FALLBACK_META = dict(title='（content_a 出错，临时骨架）', levels=[
    dict(name='注释', cls='e1', desc='代码注释里写了原因'), dict(name='提交', cls='e2', desc='提交说明里写了原因'),
    dict(name='文档', cls='e3', desc='文档/配置说明里写了原因'), dict(name='测试', cls='e4', desc='有测试固定了这个行为'),
    dict(name='推断', cls='infer', mark='≈', desc='没有直接证据，从代码行为倒推'),
    dict(name='观点', cls='view', mark='※', desc='评价或建议，不是事实')])


def set_meta(meta):
    if not isinstance(meta, dict) or not isinstance(meta.get('levels'), list) or not meta['levels']:
        raise BuildError('META 要是字典，且有非空的 levels 列表')
    for l in meta['levels']:
        if not isinstance(l, dict) or not l.get('name') or l.get('cls') not in CLS_OK:
            raise BuildError('META.levels 每项要有 name 和 cls（%s）: %r' % ('/'.join(CLS_OK), l))
        l.setdefault('desc', '')
    META.clear()
    META.update(meta)
    META.setdefault('title', '画布')
    META.setdefault('who', {})
    META.setdefault('whoDesc', {})
    META.setdefault('tags', [])
    META.setdefault('srcLabel', '')
    LEVELS[:] = [l['name'] for l in META['levels']]
    LVCLS.clear()
    LVCLS.update((l['name'], l['cls']) for l in META['levels'])
    TAGS[:] = [x['name'] for x in META['tags']]
    WHO[:] = list(META['who'])


def set_slots(mod):
    """项目自己的槽位表（可选）：顶层模块里的 SLOTS = {槽位id: 默认标题}。"""
    t = getattr(mod, 'SLOTS', None) or {}
    if not isinstance(t, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in t.items()):
        raise BuildError('SLOTS 要是 {槽位id: 默认标题} 的字典')
    SLOTS.clear()
    SLOTS.update(t)


def fallback_root():
    # 顶层模块坏了：项目的表读到了就按表列，没读到就列各模块实际提供的那几节
    ids = list(SLOTS) or sorted(PROVIDERS)
    return cv.node('root', '全景', '（顶层模块出错，这是临时骨架）', '顶层模块没能加载，下面只把各模块提供的节列出来。',
                   children=[cv.slot(k) for k in ids])


def scene_only_root():
    # 顶层是机器图的项目：META 和顶层那一页都由 content_scene.py 给。
    # 下层各页（可以又是机器图，也可以是源码页）放在 root(base) 返回的 children 里，或用 slot 交给别的模块填。
    cs = importlib.import_module('content_scene')
    set_slots(cs)
    meta = dict(getattr(cs, 'META', None) or {})
    if not isinstance(meta.get('title'), str) or not meta['title'].strip():
        raise BuildError("content_scene.py 里要有 META = dict(title='这一页的标题')")
    meta.setdefault('levels', json.loads(json.dumps(FALLBACK_META['levels'])))
    set_meta(meta)
    return cs.root(cv.node('root', '全景', META['title'], META['title']))


def main(scene=False):
    hd = git('rev-parse', '--short', 'HEAD')
    if hd.returncode != 0:
        raise SystemExit('不是可用的 git 仓库: %s' % REPO)
    head = hd.stdout.decode().strip()

    PROVIDERS.clear()
    for k in SC_STAT:
        SC_STAT[k] = 0
    amod = 'content_scene' if scene else 'content_a'
    mods, moderr = load_modules()
    dups = []
    for name in sorted(mods):
        for sid, nd in mods[name].items():
            if sid in PROVIDERS:
                dups.append((name, sid, PROVIDERS[sid][0]))
            else:
                PROVIDERS[sid] = (name, nd)

    a_err, root, meta_ok = None, None, False
    try:
        if SCENE_ONLY:
            root_raw = scene_only_root()
            meta_ok = True
        else:
            ca = importlib.import_module('content_a')
            set_slots(ca)
            set_meta(dict(ca.META))
            meta_ok = True
            root_raw = ca.root()
            if scene:           # 机器图版：顶层那一页换成 content_scene.root(原来的顶层节点) 给的内容
                root_raw = importlib.import_module('content_scene').root(root_raw)
        reset()
        root = build_tree(root_raw, amod, None)
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        a_err = str(e) if isinstance(e, BuildError) else brief(e)
        root = None
    if root is None:                    # 总架构坏了：退回骨架，别的作者照样能看自己的槽位
        if not meta_ok or SCENE_ONLY:
            set_meta(json.loads(json.dumps(FALLBACK_META)))
        reset()
        root = build_tree(fallback_root(), 'content_a', None)

    for sid, (name, _) in PROVIDERS.items():
        if sid not in PLACED and not sid.startswith('zz_'):
            WARN.append((name, sid, '提供了节点 %s，但总架构的树里没有 slot(%r)，这部分内容没出现在画布上' % (sid, sid)))
    for name, sid, first in dups:
        WARN.append((name, sid, '槽位 %s 已由 %s 提供，本模块的同名节点被忽略' % (sid, first)))

    back, dead, seen = {}, set(), set()
    for f, t in LINKS:
        if t in NAV:
            if f != t and f not in back.setdefault(t, []):
                back[t].append(f)
            continue
        dead.add(t)
        if (f, t) in seen:
            continue
        seen.add((f, t))
        if t in SLOTS:
            WARN.append((OWNER.get(f, '?'), f, '内链指向槽位 %s，但总架构的树里还没放它；链接暂时点不动' % t))
        else:
            WARN.append((OWNER.get(f, '?'), f, '内链指向不存在的节点 %s（拼错了？或那一页别人还没写）；正文里的链接会显示成红色断链，说明卡上的「钻进去」按钮不会出现' % t))

    def navname(i):
        return NAV.get(i) or re.split('[：:]', SLOTS.get(i, i))[0]
    dead_a = re.compile(r'<a class="lk" href="#/([\w.]+)">(.*?)</a>')

    def undead(m):
        if m.group(1) not in dead:
            return m.group(0)
        return '<span class="lk dead" title="目标页 %s 还不存在">%s</span>' % (m.group(1), m.group(2))

    def fix(o):
        if isinstance(o, str):
            if '\x00NAV:' in o:
                o = re.sub('\x00NAV:([\\w.]+)\x00', lambda m: html.escape(navname(m.group(1))), o)
            if dead and 'class="lk"' in o:
                o = dead_a.sub(undead, o)
            return o
        if isinstance(o, list):
            return [fix(x) for x in o]
        if isinstance(o, dict):
            return {k: fix(v) for k, v in o.items()}
        return o
    root = fix(root)

    st = dict(nodes=0, stubs=0, depth=0, blocks=0, segs=0, notes=0)
    anchors = []
    tree = []

    def visit(n, d):
        st['nodes'] += 1
        tree.append('%s%s · %s%s' % ('  ' * d, n['id'], NAV.get(n['id'], ''), '（未展开，空页）' if n.get('stub') else ''))
        st['stubs'] += 1 if n.get('stub') else 0
        st['depth'] = max(st['depth'], d)
        n['back'] = back.get(n['id'], [])
        for b in n['blocks']:
            if b['t'] == 'note':
                st['notes'] += 1
            elif b['t'] == 'code':
                st['blocks'] += 1
                st['notes'] += len(b['notes'])
                for pt in b['parts']:
                    st['segs'] += 1
                    anchors.append(dict(node=n['id'], file=b['file'], a=pt['a'], b=pt['b'], kind='code', text=pt['lines']))
            elif b['t'] == 'flow':
                for s in b['steps']:
                    if s.get('to') and s['to'] not in NAV:
                        s['to'] = None
            elif b['t'] == 'diagram':
                for x in b['boxes']:
                    if x.get('to') and x['to'] not in NAV:
                        x['to'] = None
            elif b['t'] == 'scene':
                for x in b['parts'] + b['wires']:
                    x['to'] = [i for i in x['to'] if i in NAV]
        for k in n['refs']:
            f, rg = k.rsplit(':', 1)
            a, bb = rg.split('-')
            anchors.append(dict(node=n['id'], file=f, a=int(a), b=int(bb), kind='ref'))
        for c in n['children']:
            visit(c, d + 1)
    visit(root, 1)
    for k in sorted(SNIPS):
        s = SNIPS[k]
        anchors.append(dict(node='*', file=s['file'], a=s['a'], b=s['b'], kind='code', via='popover', text=s['lines']))

    data = dict(meta=META, root=root, snips=SNIPS, commits=COMMITS)
    blob = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    os.makedirs(OUT, exist_ok=True)
    ztp, d3p = os.path.join(HERE, 'template_zoom.html'), os.path.join(HERE, 'vendor', 'd3.v7.9.0.min.js')

    def zoom_page(fname):
        with open(ztp, encoding='utf-8') as fh:
            ztpl = fh.read()
        with open(d3p, encoding='utf-8') as fh:
            d3src = fh.read().replace('</script', '<\\/script')
        # 顺序不能换：先塞 D3，再标题，数据最后（数据里出现什么字样都不会再被替换）
        zpage = ztpl.replace('__D3__', d3src).replace('__TITLE__', html.escape(META['title'])).replace('__DATA__', blob)
        with open(os.path.join(OUT, fname), 'w', encoding='utf-8') as fh:
            fh.write(zpage)
        return len(zpage.encode('utf-8')) / 1024

    if scene:
        # 机器图版只出 scene.html（同一个可缩放画布模板；canvas.html / zoom.html / anchors.json 不动）
        ssize = zoom_page('scene.html')
        print('')
        print('机器图 → %s（%.0f KB）' % (os.path.join(OUT, 'scene.html'), ssize))
        print('部件 %d  连线 %d  说明卡 %d  推演 %d 步；锚点条目 %d  提交 %d' % (
            SC_STAT['parts'], SC_STAT['wires'], SC_STAT['cards'], SC_STAT['steps'], len(anchors), len(COMMITS)))
        no_top = not any(b['t'] == 'scene' for b in root['blocks'])
        if no_top:
            print('!! content_scene.root() 给的顶层内容里没有 scene(...) 块')
        if a_err:
            print('!! content_scene 出错，这次顶层用的是临时骨架：%s' % a_err)
        if len(tree) > 1:
            print('下面这些节点可以钻进去（part / wire 的 to= 用这些 id；标「未展开」的是空页，别指过去）：')
        else:
            print('目前只有顶层这一屏，没有下层页面：part / wire 的 to= 没有可指的地方（要加下层见 SCENE.md 第 8 节）。')
        for ln in tree[1:]:
            print('  ' + ln)
        if not SCENE_ONLY:
            # 有 content_a 的项目：别的模块的问题在上一遍（canvas.html）已经报过，这里只列机器图自己的
            mine = [w for w in WARN if w[0] == amod]
            print('WARN %d 条（只列机器图自己的）' % len(mine))
            for mod, nid, msg in mine:
                print('WARN [%s] [%s] %s' % (mod, nid, msg))
            return bool(a_err or mine or no_top)
        # 顶层是机器图的项目：这是唯一的一遍，所有模块的问题都在这里报
        unfilled = [s for s, m in PLACED.items() if not m]
        if PLACED:
            print('已填槽位 %d：%s' % (len(PLACED) - len(unfilled), ' '.join('%s←%s' % (s, m) for s, m in PLACED.items() if m) or '（无）'))
            print('未填槽位 %d：%s' % (len(unfilled), ' '.join(unfilled) or '（无）'))
        if moderr:
            print('!! 模块出错 %d 个（它们的槽位按未填处理）：' % len(moderr))
            for name, msg in moderr:
                print('   ERROR [%s] %s' % (name, msg))
        if SLOT_ERR:
            print('!! 槽位内容构建失败 %d 个（按未填处理）：' % len(SLOT_ERR))
            for sid, name, msg in SLOT_ERR:
                print('   ERROR [%s] [%s] %s' % (name, sid, msg))
        print('WARN %d 条%s' % (len(WARN), '（格式：WARN [模块] [节点id] 说明）' if WARN else ''))
        for mod, nid, msg in WARN:
            print('WARN [%s] [%s] %s' % (mod, nid, msg))
        return bool(a_err or no_top or unfilled or moderr or SLOT_ERR or WARN)

    with open(os.path.join(HERE, 'template.html'), encoding='utf-8') as fh:
        tpl = fh.read()
    page = tpl.replace('__TITLE__', html.escape(META['title'])).replace('__DATA__', blob)
    with open(os.path.join(OUT, 'canvas.html'), 'w', encoding='utf-8') as fh:
        fh.write(page)
    with open(os.path.join(OUT, 'anchors.json'), 'w', encoding='utf-8') as fh:
        json.dump(anchors, fh, ensure_ascii=False, indent=0)

    # v2：同一份数据再出一张「可缩放画布」zoom.html（相机用 vendor 里的 D3，内嵌进页面，仍是单文件）
    zsize = 0
    if os.path.exists(ztp) and os.path.exists(d3p):
        zsize = zoom_page('zoom.html')

    filled = ['%s←%s' % (s, m) for s, m in PLACED.items() if m]
    unfilled = [s for s, m in PLACED.items() if not m]
    unplaced = [s for s in SLOTS if s not in PLACED]
    print('构建完成 → %s（%.0f KB）+ anchors.json；仓库 HEAD %s' % (
        os.path.join(OUT, 'canvas.html'), len(page.encode('utf-8')) / 1024, head))
    if zsize:
        print('可缩放画布 → %s（%.0f KB，其中 D3 约 %.0f KB）' % (os.path.join(OUT, 'zoom.html'), zsize, os.path.getsize(d3p) / 1024))
    print('节点 %d（其中占位 %d）  最大深度 %d  代码块 %d（共 %d 段）  批注 %d  锚点条目 %d  提交 %d' % (
        st['nodes'], st['stubs'], st['depth'], st['blocks'], st['segs'], st['notes'], len(anchors), len(COMMITS)))
    print('已填槽位 %d：%s' % (len(filled), ' '.join(filled) or '（无）'))
    print('未填槽位 %d：%s' % (len(unfilled), ' '.join(unfilled) or '（无）'))
    if unplaced:
        print('槽位表里有、但树里没放的 %d 个：%s' % (len(unplaced), ' '.join(unplaced)))
    if a_err:
        print('!! 总架构 content_a 出错，本次用的是临时骨架：%s' % a_err)
    if moderr:
        print('!! 模块出错 %d 个（它们的槽位按未填处理）：' % len(moderr))
        for name, msg in moderr:
            print('   ERROR [%s] %s' % (name, msg))
    if SLOT_ERR:
        print('!! 槽位内容构建失败 %d 个（按未填处理）：' % len(SLOT_ERR))
        for sid, name, msg in SLOT_ERR:
            print('   ERROR [%s] [%s] %s' % (name, sid, msg))
    print('WARN %d 条%s' % (len(WARN), '（格式：WARN [模块] [节点id] 说明；只看自己的可以 grep 模块名）' if WARN else ''))
    for mod, nid, msg in WARN:
        print('WARN [%s] [%s] %s' % (mod, nid, msg))
    return bool(unfilled or unplaced or a_err or moderr or SLOT_ERR or WARN)


if SCENE_ONLY:
    _bad = main(scene=True)
else:
    _bad = main()
    if os.path.exists(os.path.join(CONTENT, 'content_scene.py')):
        _bad = main(scene=True) or _bad
if STRICT and _bad:
    sys.exit(1)
