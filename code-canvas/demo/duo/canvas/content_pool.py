# content_pool.py —— 槽位 pool（候选池）与 loop（主循环怎么用池）。
# 依据：研究底稿 5.3.3 / 5.3.4。who='me' 的条目是呈现者自己加的，底稿里没有。
from cv import (node, p, h, note, at, code, step, flow, box, edge, diagram, frames, cell, frame, out)

EV = 'src/evict.c'
SCALE = '缩小示意：6 格代替 16 格；key 名和分数是编的，规则照源码。数字 = 淘汰分，越大越该删。'


def _row(s):
    """'a:40 c:60:new _' → 一排格子；'_' 是空格，第三段是状态。"""
    r = []
    for t in s.split():
        a = t.split(':')
        r.append(cell() if t == '_' else cell(a[0], a[1], a[2] if len(a) > 2 else None))
    return r


def _f(title, row, say, inc=None, incsay=None):
    return frame(title, _row(row), say=say, inc=inc, incsay=incsay)


# ------------------------------------------------------------------ pool
def _demo():
    return node(
        'pool.demo', '逐帧演示', '逐帧：候选怎么进池、谁被挤掉、从哪头取',
        '9 步看完池的全部行为：落在哪、何时右移、何时丢弃、满了挤谁、从哪头取。',
        lead='真实的池是 16 格，这里缩成 6 格。每一步的规则照源码，行号附在解说里。',
        src='src/evict.c 第 184–216 行（插入）、第 636–653 行（取）',
        blocks=[frames([
            _f('空池', '_ _ _ _ _ _', '启动时分配，所有格子的 `key` 都是 NULL：`src/evict.c:122-127`。'),
            _f('第一个候选：落在最左', 'a:40:new _ _ _ _ _',
               '从左往右找「第一个空格，或第一个分数不小于自己的格子」，记为 `k`：`src/evict.c:184-187`。'
               '这里 `k` = 0 是空格，直接放：`src/evict.c:192-193`。', cell('a', '40', 'new'), '分数 40'),
            _f('比池里都高：接在右边', 'a:40 b:95:new _ _ _ _',
               '越过分数更低的 a，停在下一个空格，还是直接放：`src/evict.c:192-193`。', cell('b', '95', 'new'), '分数 95'),
            _f('夹在中间：从 k 起右移', 'a:40 c:60:new b:95:mv _ _ _',
               '`k` 停在 b（95 不小于 60）。最右格还空着，就把 `k` 起的格子整体右移一格，c 放进 `k`：`src/evict.c:197-205`。',
               cell('c', '60', 'new'), '分数 60'),
            _f('再进三个，池满了', 'd:20:new a:40:mv c:60:mv e:70:new f:85:new b:95:mv',
               '同样的规则再插入 d、e、f（过程略）。始终升序：左边分低，右边分高。'),
            _f('池满，来个更差的：丢弃', 'd:20 a:40 c:60 e:70 f:85 b:95',
               '`k` 停在 0：最低的 d（20）也不比它低。最右格非空说明池满，不进池：`src/evict.c:188-191`。',
               cell('g', '10', 'out'), '分数 10，进不来'),
            _f('池满，来个更好的：先挤掉最左', 'd:20:out a:40 c:60 e:70 f:85 b:95',
               '`k` 停在 f（85 不小于 75）。右边没空位，只能丢掉最左边、分数最低的 d：`src/evict.c:206-212`。',
               cell('h', '75', 'new'), '分数 75'),
            _f('左半段左移，新候选落在 k-1', 'a:40:mv c:60:mv e:70:mv h:75:new f:85 b:95',
               '`k--` 之后，a、c、e 各左移一格，h 放进腾出的位置；f、b 不动：`src/evict.c:208-214`。'),
            _f('取：从最右边拿', 'a:40 c:60 e:70 h:75 f:85 b:95:pick',
               '主循环从右往左扫，最右的非空格分数最高：`src/evict.c:636-638`。取走即清格（`src/evict.c:649-653`），'
               '其余留给下一次调用。细节见 [[loop.take]]。'),
        ], left='分数低', right='分数高 · 先删', cap=SCALE)])


def _entry():
    return node(
        'pool.entry', '格子与分配', '一个格子里有什么，池在哪里分配',
        '5 个字段、两个宏；`idle` 名不副实，`cached` 是性能补丁。启动时分配一次。',
        src='src/evict.c 第 36–51、116–129 行',
        blocks=[
            code(EV, [(36, 37), (43, 51)], notes=[
                at(44, '注释', '名不副实：`idle` 存的是「淘汰分」，越大越该删，不一定是空闲时间。读代码时一律读成分数。',
                   q='This is called idle just because the code initially handled LRU, but is in fact just a score '
                     'where a higher score means better candidate.',
                   ref='src/evict.c:160-162', tag='易混', has='idle'),
                at((45, 46), '提交', '为什么一格有两个 sds：`cached` 是每格自带的缓冲，key 名不超过 255 字节就抄进去复用，省掉反复分配释放。',
                   q='To destroy and recreate the pool[].key element is slow, so we allocate in pool[].cached SDS '
                     'strings that can account up to 255 chars keys and try to reuse them. This provides a solid 20% '
                     'performance improvement in real world workload alike benchmarks.',
                   ref='c:e64bf05f4', tag='历史', has='cached'),
                at(47, None, '池是所有库共用的一个，所以每格要记自己来自哪个库。为什么共用见 [[loop.fill]]。', has='dbid'),
                at(51, None, '`static` 全局指针：池不属于某一次调用，上次没删的候选下次还在。', has='EvictionPoolLRU'),
            ]),
            code(EV, (116, 129), notes=[
                at(125, None, '16 块缓冲在这里一次分好，每格一块。', has='sdsnewlen'),
                at(128, None, '只在启动时调用一次（`src/server.c:3199`），之后永不释放。', has='EvictionPoolLRU'),
            ]),
        ])


def _sample():
    return node(
        'pool.populate.sample', '抽样', '抽样：挑一个 slot，抓一批 key',
        '按 key 数加权挑 slot；抓 key 不保证抓满、不保证不重复；受保护的不算候选。',
        src='src/evict.c 第 139–158、90–94 行',
        blocks=[
            code(EV, (139, 158), notes=[
                at((145, 146), '注释', '先挑一个 slot（一张哈希表）：按 key 数加权随机，key 多的表更容易被挑中。',
                   q='probability of each dict being returned is proportional to the number of elements that dictionary holds.',
                   ref='src/kvstore.c:400-417', has='kvstoreGetFairRandomDictIndex',
                   more='非集群模式只有一张表，这一步直接返回 0：`src/kvstore.c:421-422`。'),
                at(148, '注释', '抓至多 `maxmemory_samples` 个 key。为了快，不保证抓满，也不保证不重复。',
                   q="It does not guarantee to return all the keys specified in 'count', nor it does guarantee to "
                     "return non-duplicated elements",
                   ref='src/dict.c:1325-1346', has='kvstoreDictGetSomeKeys',
                   more='最终落到 `src/dict.c:1347` 的 `dictGetSomeKeys`。底稿的判断：这对淘汰够用，抽样本来就是近似。'),
                at((156, 158), None, '受保护（[[bless|BLESS 过]]）的 key 跳过，也不计入 `*candidates`。'
                   '这是入池这一道；出池还有一道，见 [[loop.ghost]]。', has='blessIsNoEvict'),
            ]),
            code(EV, (90, 94), cap='上面第 146 行传进去的回调。', notes=[
                at((90, 91), '注释', '正在导入的 slot 整个跳过：里面的 key 处于中间状态，不能淘汰。',
                   q='keys that are being imported are in an intermediate state. We cannot evict them and therefore skip them.',
                   has='imported'),
            ]),
        ])


def _score():
    return node(
        'pool.populate.score', '打分', '打分：三种算法，统一成「越大越该删」',
        '空闲时间、访问频率、过期时间都换算成同一个分数，池只管排序。',
        src='src/evict.c 第 160–179 行',
        blocks=[
            code(EV, (160, 179), notes=[
                at((160, 162), '注释', '变量还叫 `idle`，其实是统一的淘汰分：越大越该删。',
                   q='just a score where a higher score means better candidate', has='score'),
                at((163, 164), None, 'LRU、LRM：分数 = 空闲毫秒数。时钟从哪来见 [[clock|对象头里的 24 位]]。',
                   has='estimateObjectIdleTime'),
                at(173, None, 'LFU：访问越少分越高，所以用 255 减去频率。', has='LFUDecrAndReturn'),
                at((174, 176), None, 'volatile-ttl：过期越早分越高，所以用最大值减去过期时间。', has='kvobjGetExpire'),
            ]),
            note('提交', '为什么要统一成一个分数：把 volatile-ttl 也接进池以后，淘汰更准，而且大多数策略共用一条代码路径。',
                 q='Precision of the eviction improved sensibly. Also this allows us to have a single code path for '
                   'most eviction types.', ref='c:2d5eb1f1a'),
            p('这几个策略位怎么判断，见 [[policy|策略位标志]]。'),
        ])


def _place():
    return node(
        'pool.populate.place', '找位置', '找位置：直接放、右移、挤掉最左，或者丢弃',
        '四种情况，两次 memmove；memmove 前后存取 `cached` 的那两行最容易看不懂。',
        lead='先看 [[pool.demo]] 的动画，再对着代码看每种情况落在哪几行。',
        src='src/evict.c 第 181–216 行',
        blocks=[
            code(EV, (181, 193), notes=[
                at((181, 183), '推断', '注释说找「比自己小的」格子；代码实际停在第一个空格，或第一个分数「不小于」自己的格子。以代码为准。',
                   who='me', tag='易混', has='smaller'),
                at(188, '推断', '这里拿「最右格非空」当「池满」用。前提是已占的格子总从最左连续排起，空格都在右边。',
                   who='me', tag='隐含前提', has='EVPOOL_SIZE-1',
                   more='连续性靠两处保证：插入只往已占的左段里放或紧接其后（本页）；取的时候从右往左扫、扫过即清（[[loop.take]]）。'
                        '全仓库只有这两处会改池。'),
                at((189, 191), None, '池满且 `k == 0`：新候选比池里最低分还低，丢弃。', has='continue'),
                at((192, 193), None, '`k` 指着空格：直接放，不用挪。', has='empty position'),
            ]),
            code(EV, (194, 216), notes=[
                at((197, 199), None, '右边还有空位：从 `k` 起整体右移一格，新候选放在 `k`。', has='Free space on the right'),
                at(202, '推断', '为什么 memmove 前先存 `cached`、之后再放回：memmove 把整个结构体连 `cached` 指针一起搬走，'
                   '不放回就会有两格指着同一块缓冲，末尾那块没人指。', tag='隐含前提', has='cached',
                   more='存了再放回，是为了保持「16 块缓冲，每块恰好属于一个格子」。依据是这几行代码本身；'
                        '注释只说了复用缓冲是为了性能（`src/evict.c:218-221`）。左移那一支的第 211、214 行是同样的写法。'),
                at((207, 208), None, '池满：`k--`，丢掉最左边（分数最低）的一格，它右边直到 `k` 的格子左移一格。', has='k--'),
                at(212, None, '被挤掉的若是长 key（名字是另外分配的），在这里释放。', has='sdsfree'),
            ]),
        ])


def _key():
    return node(
        'pool.populate.key', '存 key 名', '把 key 名抄进格子',
        '池里存的是名字的副本，不是指针：原 key 被删了也不会悬空，只会变成「幽灵」。',
        src='src/evict.c 第 218–235 行',
        blocks=[
            code(EV, (218, 235), notes=[
                at((218, 221), '注释', '复用格子自带的缓冲，因为反复分配、释放这个对象很贵。',
                   q='because allocating and deallocating this object is costly', has='reuse',
                   more='带来多少提升，见 [[pool.entry]] 里那条提交。'),
                at((223, 228), None, '长 key（超过 255 字节）另外复制一份；否则抄进本格缓冲，`key` 直接指向 `cached`。'
                   '所以别处的 `key != cached` 意思是「这个名字是另外分配的，要单独释放」。', has='sdsdup'),
                at(235, None, '返回抓到的个数（含受保护的）；可淘汰的个数走 `*candidates`。', has='return count'),
            ]),
            note(None, '存的是名字的副本，不是指向数据库的指针。所以原 key 被删了池也不会悬空，只留下一个「幽灵」，'
                       '取的时候才发现：见 [[loop.ghost]]。'),
        ])


def _populate():
    return node(
        'pool.populate', '灌一批', '灌一批：一次调用做四件事',
        '挑一个 slot 抓一批 key，逐个打分，按分数插进池。四步各一页真实代码。',
        lead='`evictionPoolPopulate()` 每调用一次：抓至多 `maxmemory_samples` 个 key，受保护的跳过，其余逐个打分、找位置、抄名字。',
        src='src/evict.c evictionPoolPopulate() 第 139–235 行',
        blocks=[
            flow([
                step('1', '挑一个 slot，抓一批 key', sub='受保护的 key 当场跳过', to='pool.populate.sample',
                     ref='`src/evict.c:146-158`'),
                step('2', '打分', sub='三种算法统一成「越大越该删」', to='pool.populate.score', ref='`src/evict.c:163-176`'),
                step('3', '找位置', sub='直接放 / 右移 / 挤掉最左 / 丢弃', to='pool.populate.place',
                     ref='`src/evict.c:184-216`'),
                step('4', '把 key 名抄进格子', sub='存的是名字的副本', to='pool.populate.key', ref='`src/evict.c:222-232`'),
            ], cap='第 2–4 步对抓到的每个 key 各做一遍。'),
            p('两个输出：返回值是抓到的个数，`*candidates` 是其中可淘汰的个数。主循环两个都用，见 [[loop.fill]]。'),
        ],
        children=[_sample(), _score(), _place(), _key()])


def _pool():
    return node(
        'pool', '候选池', '候选池：16 个格子，跨调用保留',
        '抽样抽到的好候选不用完就扔：存进一个全局的 16 格数组，按分数排好，下次接着用。',
        lead='它是什么、谁往里写、谁从里面取。行为先看逐帧演示，代码在后面几页。',
        src='src/evict.c 第 36–51、116–235 行',
        blocks=[
            p('一个全局数组，`EVPOOL_SIZE` = 16 格（`src/evict.c:36`），按「淘汰分」升序：左边分低，右边分高、最该删'
              '（`src/evict.c:136-138`）。启动时分配一次，之后永不释放。'),
            diagram(
                860, 200,
                boxes=[
                    box('alloc', '启动时分配一次', [20, 14, 210, 58], m=[4, 12, 162, 54], kind='ext',
                        sub='evictionPoolAlloc()'),
                    box('pop', '写：按分数插入', [20, 128, 210, 58], m=[174, 12, 162, 54], to='pool.populate',
                        sub='evictionPoolPopulate()'),
                    box('pool', '候选池', [335, 70, 190, 60], m=[75, 126, 190, 56], kind='store', to='pool.entry',
                        sub='16 格 · 分数升序'),
                    box('take', '读：从最右取', [650, 70, 190, 60], m=[75, 240, 190, 56], to='loop.take',
                        sub='主循环，取完清格'),
                ],
                edges=[edge('alloc', 'pool', '分配'), edge('pop', 'pool', '插入'), edge('pool', 'take', '取最右', ml='取')],
                mw=340, mh=306, cap='带 › 的框可以点进去。'),
            note('提交', '为什么不是「抽 5 个，删其中最差的」：上一轮抽到但没删的好候选留在池里不浪费，等于用同样的抽样成本看了更多的 key。',
                 q='we use an eviction pool that is persistent across evictions of keys, and gets populated with the '
                   'best candidates for evictions found so far.', ref='c:22c9cfaf5',
                 also=[('提交', 'It allows to approximate LRU eviction at a given number of samples better than the '
                              'previous algorithm used.', 'c:22c9cfaf5'),
                       ('提交', 'Verified to have better real world performances with power-law access patterns '
                              'because of the data accumulated across calls.', 'c:6416ab19d')]),
            p('只有 LRU / LFU / LRM / volatile-ttl 用池；随机策略不经过它，见 [[random|随机策略]]。'),
        ],
        children=[_demo(), _entry(), _populate()])


# ------------------------------------------------------------------ loop
def _fill():
    return node(
        'loop.fill', '先灌池', '先灌池：每个库都抽，每个库抽几次',
        '所有 DB 都抽一遍；每库最多抽「非空哈希表个数」次；预算只算可淘汰的 key。',
        src='src/evict.c 第 597–633 行',
        blocks=[
            code(EV, (597, 612), notes=[
                at((597, 600), '注释', '每个库都抽，不只看当前库。原来的问题：访问很频繁的库照样被 LRU 选中，而别的库里有更该删的候选。',
                   q="We don't want to make local-db choices when expiring keys",
                   also=[('提交', 'However this means that there could be DBs with very frequently accessed keys that '
                                'are targeted by the LRU algorithm while there were other DBs with many better '
                                'candidates to expire.', 'c:e423f76e7')],
                   has='server.dbnum', more='所以池是全局一个，格子里要记 `dbid`，见 [[pool.entry]]。'),
                at((603, 607), None, 'allkeys 策略抽全部 key（`keys`）；其余策略只抽带过期时间的（`expires`）。',
                   has='MAXMEMORY_FLAG_ALLKEYS'),
            ]),
            code(EV, (613, 633), notes=[
                at((613, 615), '提交', '每个库最多抽「非空哈希表个数」次，每次一个随机 slot。非集群模式最多 1 次。',
                   q="We'll never try to sample the db dicts more times than the number of non-empty dicts in the db "
                     "(max 1 in non-cluster mode).", ref='c:9ee1cc33a', has='kvstoreNumNonEmptyDicts'),
                at((618, 621), '注释', '预算加的是 `candidates`（可淘汰的），不是 `sampled`（抓到的）。'
                   '否则全是受保护 key 的 slot 会把预算耗光，轮不到还有受害者的 slot。',
                   q="so slots full of NO-EVICT keys don't starve the sampling and stop us from reaching slots that "
                     "still have victims.", tag='易混', has='sampled_keys += candidates'),
                at((624, 626), None, '提前结束之一：这个库已经抽够 `maxmemory_samples` 个可淘汰的 key。', has='maxmemory_samples'),
                at((627, 631), '注释', '提前结束之二：库里的 key 不到抽样数的 10 倍，表很稀疏，再抽也抽不满，别空转。',
                   q='dict/s may be very sparsely populated', has='maxmemory_samples*10'),
            ]),
        ])


def _take():
    return node(
        'loop.take', '从池里取', '再从池里取：从右往左，回库查一次，扫过即清',
        '最右边分最高。拿名字回库里再查一次；不管查没查到，扫过的格子一律清空。',
        src='src/evict.c 第 636–653 行',
        blocks=[
            frames([
                _f('从最右的非空格开始', 'a:40 c:60 e:70 h:75 f:85:pick _',
                   '假设灌完池是这样。`k` 从最右往左走，空格跳过，先碰到 f，拿它的名字回库里查：`src/evict.c:637-647`。'),
                _f('查不到：幽灵', 'a:40 c:60 e:70 h:75 f:85:ghost _',
                   'f 入池之后已经被删掉了。格子清空，继续往左：`src/evict.c:649-656`。'),
                _f('查到了，但已受保护', 'a:40 c:60 e:70 h:75:out _ _',
                   'h 还在库里，可入池之后被 BLESS 了：不选它。它的格子同样已经清掉：`src/evict.c:659-663`。'),
                _f('查到了，可以删', 'a:40 c:60 e:70:pick _ _ _',
                   'e 在库里、没受保护：`bestkey` 就是它，跳出：`src/evict.c:664-665`。'),
                _f('收场', 'a:40 c:60 _ _ _ _',
                   'e 的格子在判断之前就清了：`src/evict.c:649-653`。a、c 留在池里，下一轮灌池时和新候选一起排。'),
            ], left='分数低', right='分数高 · 先看', cap=SCALE + '后两种跳过的代码见 [[loop.ghost]]。'),
            code(EV, (636, 653), notes=[
                at((636, 637), '注释', '从右往左：池是升序的，最右边最该删。',
                   q='Go backward from best to worst element to evict.', has='EVPOOL_SIZE-1'),
                at((642, 646), '推断', 'volatile 策略回查的是 `expires` 表，顺带复核了资格：入池后被去掉过期时间的 key 已不在这张表里，'
                   '会被当成幽灵跳过。', has='expires'),
                at(647, None, '池里只有名字，所以要拿名字回数据库再查一次。', has='kvstoreDictFind'),
                at((649, 653), None, '不管查没查到，这一格都清掉。', has='pool[k].key = NULL'),
            ]),
        ])


def _ghost():
    return node(
        'loop.ghost', '幽灵与受保护', '查到了才算数：幽灵跳过，入池后被保护的也跳过',
        '池里的名字可能已经不存在，也可能后来被 BLESS 了。两种都换下一个。',
        src='src/evict.c 第 655–670 行',
        blocks=[
            code(EV, (655, 670), notes=[
                at((655, 656), '注释', '查不到就是「幽灵」，换下一个。key 被删时没人来清池，所以池里会留着已不存在的名字。',
                   q='Otherwise it is a ghost and we need to try the next element.',
                   also=[('注释', "we don't remove keys from the pool when they are deleted so the pool may contain "
                                "keys that no longer exist.", 'src/evict.c:108-109')], has='ghost'),
                at((659, 663), '注释', '和入池时那次检查不重复：池跨调用保留，入池之后才被 BLESS 的 key 要在出池时拦下。一次管入池，一次管出池。',
                   q='The pool persists across performEvictions() calls, so a key pooled while unblessed may since '
                     'have been marked NO-EVICT.',
                   also=[('测试', 'Blessing a key already in the LRU eviction pool still protects it',
                          'tests/unit/bless.tcl:383')], tag='易混', has='blessIsNoEvict'),
                at(667, '推断', '看起来像漏了：key 被 DEL 时为什么不顺手清池？那样每次删 key 都要扫一遍池，成本摊到所有写路径上；'
                   '现在推迟到真要用它时，一次哈希查找就解决。', tag='反直觉', has='Ghost',
                   more='依据：`src/evict.c:108-109` 的注释只陈述了「不清」这个事实，没有给理由。'),
            ]),
        ])


def _end():
    return node(
        'loop.end', '取不到时', '池里一个能用的都没有：三种结束，否则再灌一轮',
        '没 key、没抽到、连续 8 轮全是受保护的 key：这三种情况放弃；其余重新灌池再取。',
        src='src/evict.c 第 634、670–691 行',
        blocks=[
            code(EV, [(634, 634), (670, 691)], notes=[
                at(634, None, '结束方式 1：所有库加起来一个 key 都没有。', has='total_keys'),
                at((672, 680), '注释', '结束方式 2：这一轮一个 key 都没抽到。这个检查是特意挪到扫池之后的，'
                   '为了把「什么都没抽到」和「抽到了但全受保护」分开。',
                   q='This check moved below the pool scan (it used to sit right after sampling) so we can tell two '
                     'cases apart', who='me', tag='历史', has='total_sampled_keys'),
                at((682, 689), None, '结束方式 3：抽到了但全是受保护的 key，连续 8 轮，置 `blessed_blocked = 1` 后退出。'
                   '8 来自 `src/evict.c:38`。另见 [[bless|BLESS]]。', has='EVICTION_MAX_BLESSED_ONLY_ROUNDS'),
                at(690, None, '这一轮抽到过可淘汰的候选，计数清零：所以是「连续」8 轮。然后回到 `while (bestkey == NULL)` 再灌一轮。',
                   has='blessed_only_rounds = 0'),
            ]),
            p('三种结束方式下 `bestkey` 都是 NULL，接下来走到 `goto cant_free`。'),
            note('推断', '会不会死循环：不会。某一轮抽到了可淘汰的候选却没选出 key，说明池里扫到的全是幽灵或受保护的 key；'
                 '扫过的格子都清空了，下一轮新候选一定能插进去并被选中。',
                 more='依据是 `src/evict.c:649-653` 无条件清格子。底稿没有找到注释或测试专门论证终止性。'),
            note('注释', '文件头注释里有一句相近的断言：池里的 key 都不存在时就重新灌池，这一次保证至少有一个能删的（只要整个库里还有能删的）。',
                 q="This time we'll be sure that the pool has at least one key that can be evicted, if there is at "
                   "least one key that can be evicted in the whole database.", ref='src/evict.c:111-114', who='me'),
        ])


def _loop():
    return node(
        'loop', '主循环怎么用池', '主循环怎么用池：灌、取、取不到再灌',
        '每删一个 key 之前：先给池补一批候选，再从最右边往左找第一个还能删的；找不到就重来。',
        lead='这一段只做一件事：定下这一次删谁（`bestkey`）。',
        src='src/evict.c performEvictions() 第 587–692 行',
        blocks=[
            flow([
                step('1', '灌池：每个库都抽一遍', sub='每库最多抽「非空哈希表个数」次', to='loop.fill',
                     ref='`src/evict.c:600-633`'),
                step('2', '取：从右往左，回库查一次', sub='扫过的格子一律清空', to='loop.take', ref='`src/evict.c:636-653`'),
                step('3', '验：幽灵和已受保护的跳过', sub='查到、且没受保护，才算数', to='loop.ghost',
                     out=[out('ok', '找到 → bestkey')], ref='`src/evict.c:655-670`'),
                step('4', '没找到：放弃，还是再灌一轮', sub='三种情况放弃', to='loop.end',
                     out=[out('run', '回到第 1 步'), out('fail', 'bestkey 仍是 NULL → cant_free')],
                     ref='`src/evict.c:672-690`'),
            ]),
            code(EV, (587, 592), notes=[
                at((587, 588), None, '四类策略走池；其余是随机策略，不经过池，见 [[random|随机策略]]。', has='MAXMEMORY_VOLATILE_TTL'),
                at(590, '注释', '看着像局部变量，其实指向全局的池：上一次调用剩下的候选还在里面。',
                   q='The pool persists across performEvictions() calls', ref='src/evict.c:659-660',
                   tag='隐含前提', has='EvictionPoolLRU'),
                at(592, None, '一直转到选出 `bestkey`，或者确认没有可删的。', has='bestkey == NULL'),
            ]),
            p('选出 `bestkey` 之后怎么删，见 [[delete|删 key 并通知]]。'),
        ],
        children=[_fill(), _take(), _ghost(), _end()])


def nodes():
    return {'pool': _pool(), 'loop': _loop()}
