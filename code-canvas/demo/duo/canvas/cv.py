# cv.py —— 内容构造辅助函数（纯数据，不碰仓库、不含渲染逻辑）
# 文本迷你语法（由 build.py 转成 HTML）：
#   `src/x.c:12-34`  锚点（自动登记，可点开看原文）   `c:哈希`  提交
#   `别的`  行内代码      [[节点id|文字]] / [[节点id]]  内链      **粗体**
#
# 签名已冻结：下面「原有函数」的参数不再改动；「增量」一节是后加的小帮手。
# 用法和页面效果见 WRITERS.md。


# ---------------------------------------------------------------- 原有函数
def node(id, nav, title, short, lead=None, src=None, blocks=None, children=None):
    return dict(id=id, nav=nav, title=title, short=short, lead=lead, src=src,
                blocks=blocks or [], children=children or [])


def p(text):
    return dict(t='p', text=text)


def h(text):
    return dict(t='h', text=text)


def _ev(lv, q, ref):
    refs = [] if ref is None else ([ref] if isinstance(ref, str) else list(ref))
    return dict(lv=lv, q=q, refs=refs)


def note(lv, text, q=None, ref=None, also=None, tag=None, more=None, who=None):
    ev = []
    if q or ref:
        ev.append(_ev(lv, q, ref))
    for x in (also or []):
        ev.append(_ev(*x))
    return dict(t='note', lv=lv, text=text, ev=ev, tag=tag, more=more, who=who)


def at(line, lv, text, q=None, ref=None, also=None, tag=None, more=None, who=None, has=None):
    """代码内批注：line 是行号或 (a, b)；has 是该范围里应出现的子串（build 时核对）。"""
    n = note(lv, text, q, ref, also, tag, more, who)
    a, b = (line, line) if isinstance(line, int) else line
    n['a'], n['b'], n['has'] = a, b, has
    return n


def code(file, parts, notes=None, cap=None):
    if isinstance(parts, tuple):
        parts = [parts]
    return dict(t='code', file=file, parts=[list(x) for x in parts], notes=notes or [], cap=cap)


def table(head, rows, cap=None):
    return dict(t='table', head=head, rows=rows, cap=cap)


def step(k, title, sub=None, to=None, out=None, loop=False, ref=None):
    return dict(k=k, title=title, sub=sub, to=to, out=out or [], loop=loop, ref=ref)


def flow(steps, cap=None):
    return dict(t='flow', steps=steps, cap=cap)


def box(id, label, d, m=None, sub=None, msub=None, kind='act', to=None, flag=None):
    return dict(id=id, label=label, sub=sub, msub=msub, kind=kind, d=d, m=m, to=to, flag=flag)


def edge(f, t, label=None, ml=None, cls=None, d=None, m=None, lp=None, mlp=None):
    return dict(f=f, t=t, label=label, ml=ml, cls=cls, d=d, m=m, lp=lp, mlp=mlp)


def diagram(w, h, boxes, edges, frames=None, mw=None, mh=None, cap=None):
    return dict(t='diagram', w=w, h=h, mw=mw, mh=mh, boxes=boxes, edges=edges,
                frames=frames or [], cap=cap)


def frames(steps, cap=None, left=None, right=None):
    return dict(t='frames', steps=steps, cap=cap, left=left, right=right)


def bar(segs, cap=None):
    return dict(t='bar', segs=segs, cap=cap)


def collect(by, keys):
    return dict(t='collect', by=by, keys=keys)


def commits():
    return dict(t='commits')


# ---------------------------------------------------------------- 增量（多人装配 + 小帮手）
# 槽位表：id -> 默认标题。总架构（content_a.py）用 slot(id) 把它们挂进树里，
# 各内容模块 content_<名字>.py 的 nodes() 返回 {槽位id: node(...)} 来填。
SLOTS = {
    'gate': '安全闸：什么时候干脆不淘汰',
    'acct': '算账：到底超了多少',
    'resize': '旁路：哈希表扩容之前先问一句',
    'policy': '策略是一组位标志',
    'clock': '打分的原料：对象头里的 24 位',
    'pool': '候选池：16 个格子，跨调用保留',
    'loop': '主循环怎么用池',
    'random': '随机策略：不经过池',
    'bless': 'BLESS：永不淘汰的 key',
    'delete': '删：一个 key 怎么被删掉并通知出去',
    'tick16': '每删 16 个 key 停下来做三件事',
    'budget': '时间预算怎么算',
    'exit': '出口：等一等、宽限、记指标',
    'fail': 'EVICT_FAIL 之后：谁被拒，脚本怎么办',
    'why': '宏观：为什么设计成这样',
    'improve': '可改进处',
    'unknown': '没查到为什么',
}


def slot(id, title=None):
    """树里的占位（放在 children 里）。构建时被某个 content_*.py 的 nodes()[id] 替换；
    没人提供就变成「未展开」占位页，标题取 title 或 SLOTS[id]。"""
    return dict(t='slot', id=id, title=title)


def cell(k=None, v='', s=None):
    """frames 里的一个格子。k 上行（等宽粗体）、v 下行；k 为空 = 空格子。
    s：None 普通 / 'new' 新进 / 'out' 被挤掉 / 'pick' 被选中 / 'mv' 挪动过 / 'ghost' 残影 / 'empty' 空。"""
    if k is None and s is None:
        s = 'empty'
    return dict(k=k, v=v, s=s)


def frame(title, cells, say=None, inc=None, incsay=None):
    """frames 的一步。cells 是这一步的整排格子；inc 是「新来的」那个 cell；say 是一句解说（支持迷你语法）。"""
    return dict(title=title, cells=cells, say=say, inc=inc, incsay=incsay)


def seg(w, label, kind='in', tag=None):
    """bar 的一段。w 是相对宽度；kind：'in' 实心 / 'out' 斜纹灰；tag 是图例里的小标签。"""
    return dict(w=w, label=label, kind=kind, tag=tag)


def out(lv, text):
    """flow 步骤的出口小药丸。lv：'ok' 绿 / 'run' 黄 / 'fail' 红。"""
    return dict(lv=lv, text=text)


def group(label, d, m=None):
    """diagram 里的虚线分组框（传给 diagram(frames=[...])）。d/m 同 box：[x, y, w, h]。"""
    return dict(label=label, d=d, m=m)
