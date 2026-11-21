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


# ---------------------------------------------------------------- 机器图（scene）—— 用法见 SCENE.md
# 一张「机器图」= 部件 part + 连线 wire + 可选的逐步推演 beat。
# 每个部件、每条连线都必须带一张说明卡 card(干什么, 怎么设计, 为什么这么设计)。
def _ls(x):
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def card(does, how, why, traps=None):
    """说明卡，四栏各是一条或一组条目。条目 = 一段字（迷你语法） 或 note(等级, 文字, q, ref)。
    does 这里是干什么的 / how 怎么设计的 / why 为什么这么设计·好处（必须用 note 标等级） / traps 容易看错的地方。"""
    return dict(does=_ls(does), how=_ls(how), why=_ls(why), traps=_ls(traps))


def part(id, name, d, m=None, code=None, kind='act', n=None, glyph=None,
         say=None, why=None, trap=None, card=None, to=None):
    """机器图里的一个部件。d / m = 桌面 / 手机两套 [x, y, w, h]。
    name 人话名字；code 代码里的名字（等宽小字）；n 角标（'1' 'A'…）；
    kind：'act' 动作 / 'store' 存着的数据 / 'ext' 外部 / 'bg' 后台 / 'gate' 判断 / 'out' 结果。
    say / why / trap = 三个图层下，框底那一行字（主流程 / 为什么 / 易混点），各一句短话。
    glyph = 框中间的小图（g_cells / g_gauge / g_list / g_kv / g_checks / g_text / g_raw）。
    card = card(...)；to = 可以钻进去的节点 id（或 id 列表）。"""
    return dict(id=id, name=name, d=d, m=m, code=code, kind=kind, n=n, glyph=glyph,
                say=say, why=why, trap=trap, card=card, to=_ls(to))


def wire(id, f, t, label, n=None, why=None, trap=None, card=None, to=None,
         ml=None, fs=None, ts=None, mfs=None, mts=None, lp=None, mlp=None, cls=None):
    """从部件 f 到部件 t 的一条连线（f == t 画成回环）。label 线上的短标签（ml 手机版）。
    fs / ts：从哪条边出、进哪条边（'l' 'r' 't' 'b'；不给就自动挑），mfs / mts 是手机版。
    lp / mlp：标签位置 [x, y]（不给就放在线的中点）。cls='dash' 画虚线（旁路 / 异步 / 偶尔发生）。
    why / trap / card / to 同 part。"""
    return dict(id=id, f=f, t=t, label=label, ml=ml, n=n, why=why, trap=trap, card=card,
                to=_ls(to), fs=fs, ts=ts, mfs=mfs, mts=mts, lp=lp, mlp=mlp, cls=cls)


def beat(title, what, why=None, focus=None, state=None):
    """推演的一步。what 发生了什么（带具体的值）；why 为什么这样：一段字或 note(等级, …)。
    focus 这一步点亮哪些部件 / 连线的 id；state = {部件id: glyph} 从这一步起把那个部件的小图换掉。"""
    return dict(title=title, what=what, why=why, focus=_ls(focus), state=state or {})


def scene(w, h, parts, wires, steps=None, mw=None, mh=None, groups=None, cap=None):
    """机器图。w / h 桌面画幅，mw / mh 手机画幅（宽建议 340）。groups 用 group(label, d, m)。"""
    return dict(t='scene', w=w, h=h, mw=mw, mh=mh, parts=parts, wires=wires,
                steps=steps or [], groups=groups or [], cap=cap)


# 小图（glyph）：部件框中间那块，用来把「里面装着什么 / 现在是什么值」画出来
def g_cells(cells, left=None, right=None):
    """一排格子，cells 用 cell(k, v, s)。left / right 是两头的小字（比如「最不该删」「最该删」）。"""
    return dict(g='cells', cells=cells, left=left, right=right)


def g_gauge(used, limit, total=None, label=None, unit=''):
    """一根量条：used 当前值、limit 红线、total 满刻度（默认取两者较大的 1.15 倍）。超过红线的部分画红。"""
    return dict(g='gauge', used=used, limit=limit, total=total, label=label, unit=unit)


def g_list(rows, on=None):
    """几行短字。on = 要高亮的那一行（下标，或下标列表）。"""
    return dict(g='list', rows=rows, on=_ls(on))


def g_kv(rows):
    """几行「名字 → 值」：rows = [(名字, 值), …]。"""
    return dict(g='kv', rows=[list(r) for r in rows])


def g_checks(rows):
    """几行判断：rows = [(文字, 状态), …]，状态 'ok' 过 / 'no' 不过 / 'skip' 没走到 / None 还没判。"""
    return dict(g='checks', rows=[list(r) for r in rows])


def g_text(big, small=None, tone=None):
    """一个大字的值 + 一行小字。tone：None / 'ok' 绿 / 'run' 黄 / 'fail' 红。"""
    return dict(g='text', big=big, small=small, tone=tone)


def g_raw(html):
    """万不得已的口子：自己给一段 HTML / SVG（不过校验，自己保证在框里放得下）。"""
    return dict(g='raw', html=html)
