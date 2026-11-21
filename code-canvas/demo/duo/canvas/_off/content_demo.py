# content_demo.py —— 冒烟测试：只填 zz_demo 一个槽位，把每种块各用一次。
# 不是正式内容；总架构去掉 slot('zz_demo') 后这个文件就不再出现在画布上（也可以直接删）。
from cv import (node, p, h, note, at, code, table, step, flow, box, edge, diagram, frames, bar,
                collect, commits, cell, frame, seg, out, group)

EV = 'src/evict.c'


def _deep():
    return node(
        'zz_demo.code.deep', '第四层', '第四层：只为检查面包屑在深处的样子',
        '面包屑到第四层时，手机上中间会折叠成「…」。',
        src='src/evict.c performEvictions() 开头',
        blocks=[
            code(EV, (548, 554), notes=[
                at(550, None, '`mem_freed` 可能是负数（注释原话）。', q='May be negative', has='mem_freed'),
            ]),
        ])


def _code():
    return node(
        'zz_demo.code', '代码/图/逐帧', '代码块、图、逐帧演示',
        '一段带三条批注的真实代码，一张有桌面/手机两套布局的图，一个逐帧演示。',
        lead='这一页检查三种「重」块：code、diagram、frames。',
        src='src/evict.c 第 538–559 行',
        blocks=[
            code(EV, [(538, 546), (552, 559)], cap='两段之间省略了几行局部变量声明。', notes=[
                at((544, 546), '注释', '闸门不放行时返回的是「假的 OK」：既没淘汰，也不记指标。',
                   q="it's a fake EVICT_OK", has='isSafeToPerformEvictions', tag='易混'),
                at(553, '推断', '`result` 先设成最坏结果，后面每条成功路径再改写它。',
                   has='EVICT_FAIL', who='me', tag='隐含前提',
                   more='这是演示用的折叠长说明：推断没有原文依据，所以是虚线框、黄底，并且带「呈现者加的」小标签。'),
                at((556, 559), '提交', '没超限就直接跳去记指标。',
                   ref='c:0cb1ee0dc', has='getMaxmemoryState',
                   also=[('文档', 'maxmemory-samples 5', 'redis.conf:1311')]),
            ]),
            h('图：桌面与手机各一套坐标'),
            diagram(
                860, 250,
                boxes=[
                    box('cmd', 'processCommand', [20, 95, 150, 60], m=[95, 14, 150, 50], kind='ext', sub='每条命令'),
                    box('pe', 'performEvictions', [230, 95, 170, 60], m=[85, 110, 170, 54],
                        to='zz_demo.code.deep', flag='543 行', sub='入口', msub='入口'),
                    box('pool', '候选池', [480, 30, 150, 60], m=[10, 214, 150, 54], kind='store', sub='16 格'),
                    box('del', '删 key', [480, 165, 150, 60], m=[180, 214, 150, 54]),
                    box('bg', '后台释放', [690, 165, 150, 60], m=[180, 312, 150, 50], kind='bg', sub='大对象'),
                ],
                edges=[
                    edge('cmd', 'pe', '命令执行前', ml='执行前'),
                    edge('pe', 'pool', '取候选'),
                    edge('pe', 'del', '删'),
                    edge('del', 'bg', '惰性', cls='dash'),
                ],
                frames=[group('一次调用之内', [215, 14, 430, 224], m=[4, 96, 332, 182])],
                mw=340, mh=372,
                cap='带 › 的框可以点进去；虚线 = 不一定发生。演示数据，不是正式结论。'),
            h('逐帧：一排格子怎么变'),
            frames([
                frame('池是空的', [cell() for _ in range(6)], say='开始时 6 个格子全空。'),
                frame('采样进来 3 个', [cell(), cell(), cell(), cell('k7', '12s', 'new'), cell('k2', '40s', 'new'),
                                   cell('k9', '95s', 'new')],
                      inc=cell('k9', '95s', 'new'), incsay='空闲最久的排最右', say='按空闲时间升序插入，**最右边最该被淘汰**。'),
                frame('取走最右边', [cell(), cell(), cell(), cell('k7', '12s'), cell('k2', '40s', 'mv'),
                                cell('k9', '95s', 'pick')],
                      say='从右往左找第一个还存在的 key，见 `src/evict.c:543`。'),
            ], left='空闲时间短', right='空闲时间长', cap='演示数据。'),
        ],
        children=[_deep()])


def _idx():
    return node(
        'zz_demo.idx', '汇总/提交', '按标签、按等级自动汇总，以及提交表',
        'collect 把散在各页的批注按标签或等级收拢成索引；commits 列出全画布引用过的提交。',
        blocks=[
            h('按标签（易困惑点索引就是这样做的）'),
            collect('tag', ['反直觉', '易混', '隐含前提', '历史', '后果']),
            h('按等级：所有推断和观点'),
            collect('lv', ['推断', '观点']),
            h('引用过的提交'),
            commits(),
        ])


def nodes():
    return {'zz_demo': node(
        'zz_demo', '演示', '临时演示：每种块各用一次',
        '冒烟测试页：段落、六种证据等级、流程、表格、条形；子页里是代码、图、逐帧、汇总。',
        lead='这不是正式内容，只为确认渲染器的每种块都能在桌面和手机上正常显示。',
        blocks=[
            p('迷你语法：锚点 `src/evict.c:546`、提交 `c:b9800e06b`、行内代码 `maxmemory`、'
              '内链 [[zz_demo.code]] 和 [[gate|带自定义文字的内链]]、**粗体**。'),
            h('证据等级各一条'),
            note('注释', '这个函数的目标是改善内存状况，不是立刻解决。',
                 q='The goal of this function is to improve the memory situation - not to immediately resolve it.',
                 ref='src/evict.c:528-529'),
            note('提交', 'BLESS 过的 key 永远不会被选为淘汰对象。',
                 q='A blessed key is never chosen as an eviction victim.', ref='c:b9800e06b', tag='后果'),
            note('文档', '默认每次采样 5 个 key。', q='maxmemory-samples 5', ref='redis.conf:1311'),
            note('测试', '测试里直接把策略切成 allkeys-lru 来验证淘汰行为。',
                 q='r config set maxmemory-policy allkeys-lru', ref='tests/unit/maxmemory.tcl:16', tag='历史'),
            note('推断', '这是一条推断：没有原文依据，从代码行为倒推出来的。', who='me', tag='反直觉',
                 more='折叠的长说明放在 more 里：读者点「展开」才看到，正文保持短。可以写 `行内代码` 和 `src/evict.c:543` 这样的锚点。'),
            note('观点', '这是一条观点：评价或建议，不是事实。', tag='反直觉'),
            note(None, '这是一条没有等级的纯说明（lv=None）：只讲是什么。'),
            h('流程'),
            flow([
                step('1', '先过安全闸', sub='不安全就什么都不做', to='gate', out=[out('ok', '假的 EVICT_OK')],
                     ref='`src/evict.c:546`'),
                step('2', '算账：超了多少', to='acct', out=[out('ok', '没超 → EVICT_OK')]),
                step('3', '挑一个 key 删掉', sub='看代码页 [[zz_demo.code]]', to='zz_demo.code', loop=True),
                step('4', '出口', out=[out('ok', 'EVICT_OK'), out('run', 'EVICT_RUNNING'), out('fail', 'EVICT_FAIL')]),
            ], cap='带「下钻 ›」的步骤整块可点。'),
            h('表格'),
            table(['返回值', '含义', '依据'], [
                ['`EVICT_OK`', '内存没问题，或者现在不适合淘汰', '`src/evict.c:539`'],
                ['`EVICT_RUNNING`', '还超着，但淘汰还在进行', '`src/evict.c:540`'],
                ['`EVICT_FAIL`', '还超着，而且没东西可淘汰了', '`src/evict.c:541`'],
            ], cap='手机上每行变成一张卡片，表头变成每格上方的小字。'),
            h('条形'),
            bar([seg(5, '算进 maxmemory 的部分', 'in', '计入'), seg(1, '演示：某块缓冲', 'out', '不计入'),
                 seg(1, '演示：另一块缓冲', 'out', '不计入'), seg(3, '剩余额度', 'in')],
                cap='演示数据：宽度只表示相对比例。'),
        ],
        children=[_code(), _idx()])}
