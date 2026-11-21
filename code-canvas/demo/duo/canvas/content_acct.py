# content_acct.py —— 槽位 gate（安全闸）与 acct（算账）。只有内容数据，代码正文由 build.py 从仓库取。
from cv import *

E = 'src/evict.c'


def _gate():
    five = node(
        'gate.five', '五个条件', '五个条件，任一成立就不淘汰',
        '`isSafeToPerformEvictions()` 只有 21 行，每个 `return 0` 各有各的理由。',
        lead='返回 0 = 不安全。逐个看每个 `return 0` 凭什么。',
        src='src/evict.c isSafeToPerformEvictions() 479–499',
        blocks=[
            code(E, (479, 486), notes=[
                at(482, '注释', '正在一个超时的长命令里重入事件循环时不删。命令入口其实已经判过一次 → [[gate.twice]]',
                   q='There must be no script in timeout condition.', ref='src/evict.c:480-481',
                   also=[('提交', 'Avoid eviction during yield command processing.', 'c:b2061de2e')],
                   has='isInsideYieldingLongCommand'),
                at(482, '提交', '正在加载数据时不删：加载 AOF 途中淘汰会破坏数据一致性。',
                   q="if we are loading AOF it's dangerous, because processCommand would call "
                     "freeMemoryIfNeeded to evict, and that will break data consistency, see issue #5686.",
                   ref='c:b9cd89d10', has='server.loading'),
                at(486, '注释', '副本默认不自己删，只做主库的精确副本。谁来删、为什么 → [[gate.replica]]',
                   q='By default replicas should ignore maxmemory and just be masters exact copies.',
                   ref='src/evict.c:484-485', has='repl_slave_ignore_maxmemory'),
            ]),
            code(E, (488, 499), notes=[
                at((491, 493), '注释', '当前客户端是 slot 迁移的导入通道时不删：避免淘汰失败带来的延迟和错误。',
                   q='Disable eviction during slot migration import to avoid delays and errors caused by failed evictions.',
                   ref='src/evict.c:488-490', has='CLIENT_ASM_IMPORTING',
                   more='导入通道是一个专用客户端，同时带 `CLIENT_MASTER` 和 `CLIENT_ASM_IMPORTING` 两个标志。'),
                at(496, '提交', '淘汰动作被暂停时不删（例如 CLIENT PAUSE、故障切换期间）。提交只有这句标题，理由没查到 → [[unknown]]',
                   q='Prevent expirations and evictions while paused', ref='c:a3e53cf9b',
                   has='PAUSE_ACTION_EVICT', more='提交正文只指向一个底稿作者读不到的 issue。'),
            ]),
        ])

    replica = node(
        'gate.replica', '副本不自己删', '副本为什么不自己淘汰',
        '淘汰只在主库做，副本等主库发来的 DEL。Redis 5 起的默认行为，一次有意的不兼容变更。',
        lead='第三个条件（`src/evict.c:486`）背后是配置项 `replica-ignore-maxmemory`，默认开（`src/config.c:4228`）。',
        src='redis.conf 1321–1330',
        blocks=[
            code('redis.conf', (1321, 1330), notes=[
                at((1321, 1324), '文档', '淘汰只由主库做：主库删了 key，把 DEL 发给副本。',
                   q='It means that the eviction of keys will be just handled by the master, sending the '
                     'DEL commands to the replica as keys evict in the master side.'),
                at((1326, 1330), '文档', '这样主从保持一致，通常正是你想要的。',
                   q='This behavior ensures that masters and replicas stay consistent, and is usually what you want'),
            ]),
            note('提交', '有意的不兼容变更：从此副本默认照抄主库，不再自己淘汰。', tag='历史', ref='c:067647a78',
                 q='Note: this breaks backward compatibility with Redis 4, since now slaves by default are '
                   'exact copies of masters and do not try to evict keys independently.'),
        ])

    twice = node(
        'gate.twice', '长命令判两次', '长命令这个条件为什么判两次',
        '命令入口判过 `isInsideYieldingLongCommand()`，闸里又判一次。看着重复，其实有第二个调用者。',
        lead='命令入口已经判过一次，闸里（`src/evict.c:482`）又判一次。重复吗？',
        src='src/server.c 4948 · src/evict.c 459',
        blocks=[
            code('src/server.c', (4942, 4949), cap='调用者一：命令入口', notes=[
                at((4944, 4947), '注释', '入口处的注释给了理由：避免脚本的传播和淘汰产生的 DEL 的传播混在一起。', who='me',
                   q='to avoid mixing the propagation of scripts with the propagation of DELs due to eviction.'),
                at(4948, None, '第一次：长命令重入时，入口根本不调 `performEvictions()`。',
                   has='!isInsideYieldingLongCommand()'),
            ]),
            code(E, (448, 459), cap='调用者二：定时器回调', notes=[
                at(459, '推断', '不是冗余：定时器回调这条路不经过命令入口，所以闸必须装在函数内部；入口那次只是提前短路。',
                   tag='隐含前提', has='performEvictions()',
                   more='依据是两个调用点的位置，没有注释直接这么说。'),
            ]),
        ])

    fakeok = node(
        'gate.fakeok', '假的 OK', '不安全时回 OK，而且不记指标',
        '超限了却不删，为什么对外说「没事」而不是「失败」？为什么连指标都不记？',
        src='src/evict.c performEvictions() 543–546',
        blocks=[
            code(E, (543, 546), notes=[
                at((544, 545), '注释', '注释自己叫它「假的 OK」：当作淘汰根本没触发，所以直接 return，不去 `update_metrics`，指标也不记。',
                   q="we don't goto update_metrics here because this check skips eviction as if it wasn't "
                     "triggered. it's a fake EVICT_OK.", tag='易混'),
                at(546, '提交', '回 OK 而不是 FAIL：回 FAIL 可能挡住写命令。提交自己也说实践中可能没区别，只是概念上更对。',
                   q="It looks safer to return C_OK from freeMemoryIfNeeded() when clients are paused because "
                     "returning C_ERR may prevent success of writes. It is possible that there is no difference "
                     "in practice since clients cannot execute writes while clients are paused, but it looks "
                     "more correct this way, at least conceptually.",
                   ref='c:c9097393b', has='return EVICT_OK', more='这条提交说的只是「客户端被暂停」那一种情况。'),
                at(546, '推断', '其余几种同理：回 FAIL 的唯一后果是拒绝写命令（[[fail]]）；而在加载 AOF、或副本回放主库命令时拒绝写入，会直接破坏数据一致性。'),
            ]),
        ])

    return node(
        'gate', '安全闸', '安全闸：什么时候干脆不淘汰',
        '五种情况下即使超限也不删 key，而且对外假装没事。',
        lead='`performEvictions()` 的第一行：先问「现在删 key 安全吗」。输入是服务器当前状态，结果只有两种。',
        src='src/evict.c performEvictions() 546 → isSafeToPerformEvictions() 479',
        blocks=[
            table(['结果', '发生什么'], [
                ['不安全', '立刻返回 `EVICT_OK`：不删 key、不算账、不记指标 → [[gate.fakeok]]'],
                ['安全', '往下走 → [[acct]]'],
            ]),
            p('不安全的五种情况（任一成立）：超时长命令重入 · 正在加载数据 · 副本忽略 maxmemory · '
              'slot 迁移导入通道 · 淘汰被暂停。逐条看 [[gate.five]]。'),
        ],
        children=[five, replica, twice, fakeok])


def _acct():
    state = node(
        'acct.state', '比两次', '先比原始数字，扣完再比一次',
        '`getMaxmemoryState()`：三个提前返回、一个防下溢的三目运算、一行看着重复的比较。',
        lead='37 行。上半段想尽早返回；下半段扣掉缓冲后再比一次，算出 `mem_tofree`。',
        src='src/evict.c getMaxmemoryState() 395–431',
        blocks=[
            code(E, (395, 408), cap='上半：能早走就早走', notes=[
                at(400, '推断', '这是分配器统计的已分配字节数，不是操作系统看到的进程占用（RSS），所以内存碎片不在这笔账里。',
                   tag='易混', has='zmalloc_used_memory',
                   more='依据只是函数名和它定义在分配器封装层（`src/zmalloc.c:658`）；底稿作者没有展开读它的实现。'),
                at((404, 407), '提交', '没设上限就直接走。这个判断是后来特意提到「算缓冲」之前的。',
                   q="when server.maxmemory is not set, don't count AOF and replicas buffers.",
                   ref='c:f0fab99d6', tag='历史', has='!server.maxmemory'),
                at(408, '注释', '快路径：连原始数字都没超，就不必再算要扣多少。',
                   q='If we are not, no need to subtract the slaves output buffers. We can just return ASAP.',
                   ref='src/evict.c:398-399', has='!level',
                   more='底稿作者补充（不是注释原话）：这是每条命令都要走的路径，所以值得省。'),
            ]),
            code(E, (410, 431), cap='下半：扣掉、再比、算差额', notes=[
                at(414, '推断', '三目运算是防无符号下溢：两个量都是 `size_t`，缓冲比总量还大的瞬间直接相减会变成天文数字。',
                   has='mem_used-overhead'),
                at(419, '推断', '看着和 408 行重复：调用者要 `level` 时 408 行不会提前返回，算完比例后在这里补上同一个判断。',
                   who='me', tag='易混', has='mem_reported <= server.maxmemory'),
                at(422, None, '再比一次：扣完以后不超，也算没事。', has='mem_used <= server.maxmemory'),
                at(425, None, '目标就是刚好回到线上：没有「多删一点留余量」的低水位。讨论见 [[improve]]。',
                   has='mem_used - server.maxmemory'),
            ]),
        ])

    repl = node(
        'acct.deduct.repl', '复制缓冲', '复制缓冲：backlog 以内照算，超出才扣',
        '同一块缓冲为什么劈成两半算？没有副本时为什么也扣？那个近似值是什么？',
        src='src/evict.c freeMemoryGetNotCountedMemory() 332–358',
        blocks=[
            code(E, (332, 347), cap='三段注释，一个条件', notes=[
                at((332, 334), '注释', '副本和 backlog 共用一块全局复制缓冲；只有超出 backlog 大小的部分才算副本额外占的。',
                   q='we think only the part of exceeding backlog size is the extra separate consumption of replicas.',
                   also=[('提交', 'Since all replicas and replication backlog share global replication buffer, we think '
                          'only the part of exceeding backlog size the extra separate consumption of replicas.',
                          'c:c1718f9d8')],
                   more='`c:c1718f9d8` 就是把复制缓冲改成全局共享的那次提交。'),
                at((336, 339), '注释', 'backlog 以内可以照算：它长到设定大小就不再长，引发的淘汰有上限，不会共振。',
                   q="it'll eventually stop growing and remain constant in size, so even if its creation will "
                     "cause some eviction, it's capped, and also here to stay (no resonance effect)"),
                at((341, 346), '注释', '没有副本时也扣：backlog 是后台增量修剪的，慢副本断开后它会短暂超出设定大小。', tag='反直觉',
                   q="To avoid massive eviction loop, we don't count the delayed freed replication backlog into "
                     "used memory even if there are no replicas"),
            ]),
            code(E, (348, 358), cap='该算多少、扣多少', notes=[
                at((351, 353), '注释', '还要加一个近似值：把链表节点和块头的开销也估进「该算」的那一侧。',
                   q="we can't know exact blocks numbers, we only get approximate size according to per block size.",
                   ref='src/evict.c:348-350', has='extra_approx_size'),
                at((355, 356), None, '`counted_mem` 以内照算；超出的部分才加进 `overhead`。', has='counted_mem'),
            ]),
        ])

    rest = node(
        'acct.deduct.rest', '迁移与 AOF 缓冲', '另外两项：迁移输出缓冲、AOF 缓冲',
        '迁移客户端被当成副本对待；AOF 缓冲取的是已分配大小，不是长度。',
        src='src/evict.c freeMemoryGetNotCountedMemory() 360–369',
        blocks=[
            code(E, (360, 369), notes=[
                at((360, 363), '注释', '迁移客户端像副本：淘汰迁移中 slot 的 key 时也会往它那里写 DEL，所以它的输出缓冲同样不算。',
                   q="The migrate client is like a replica, we also push DELs into it when evicting keys belonging "
                     "to the migrating slot, so we don't count its output buffer to avoid eviction loop.",
                   has='asmGetMigrateOutputMemoryUsage'),
                at(366, '提交', '取已分配大小，而不是字符串长度。提交只有这一句标题。',
                   q='Overhead is the allocated size of the AOF buffer, not its length', ref='c:29e488cbb',
                   has='sdsAllocSize',
                   more='底稿作者的解读：sds 字符串会预留空间，真正占内存的是分配出去的那一整块。'),
            ]),
        ])

    deduct = node(
        'acct.deduct', '扣掉的三项', '扣掉哪三项，为什么要扣',
        '复制缓冲超出 backlog 的部分、slot 迁移输出缓冲、AOF 缓冲。不扣的话，淘汰会自己喂大自己。',
        lead='`freeMemoryGetNotCountedMemory()` 返回三项之和。先看函数头上那段注释：为什么要扣。',
        src='src/evict.c freeMemoryGetNotCountedMemory() 321–369',
        blocks=[
            table(['扣掉的', '条件', '代码'], [
                ['复制缓冲里超出 backlog 的部分', '`repl_buffer_mem > repl_backlog_size`', '`src/evict.c:347-358`'],
                ['slot 迁移的输出缓冲', '无条件', '`src/evict.c:360-363`'],
                ['AOF 缓冲', '`aof_state != AOF_OFF`', '`src/evict.c:365-367`'],
            ]),
            code(E, (321, 330), notes=[
                at((323, 326), '注释', '防正反馈把库删空：删 key 要往这些缓冲里写 DEL → 缓冲变大 → 算进去就得删更多 key → 更多 DEL……',
                   q="it can cause feedback-loop when we push DELs into them, putting more and more DELs will make "
                     "them bigger, if we count them, we need to evict more keys, and then generate more DELs, "
                     "maybe cause massive eviction loop, even all keys are evicted.",
                   ref='src/evict.c:321-328',
                   also=[('文档', 'network problems / resyncs will not trigger a loop where keys are evicted, and in '
                          'turn the output buffer of replicas is full with DELs of keys evicted triggering the '
                          'deletion of more keys, and so forth until the database is completely emptied.',
                          'redis.conf:1250-1255'),
                         ('测试', "replica buffer don't induce eviction", 'tests/unit/maxmemory.tcl:422')]),
            ]),
            note('提交', '这条规则很老（2012 年），而且作者坦白是试出来的。', tag='历史', ref='c:f6b32c14f',
                 q="This three changes are the result of many tests, I found (partially empirically) that is the "
                   "best way to address the problem, but maybe we'll find better solutions in the future."),
            note('文档', '被扣掉的缓冲是真实占着内存的：挂了副本，就该把 `maxmemory` 设低一点，给副本输出缓冲留出 RAM。', tag='后果',
                 q='if you have replicas attached it is suggested that you set a lower limit for maxmemory so that '
                   'there is some free RAM on the system for replica output buffers',
                 ref='redis.conf:1257-1259',
                 more='被扣掉的量可以在 INFO 里看到：`mem_not_counted_for_evict`（`src/server.c:6965`）。'),
        ],
        children=[repl, rest])

    return node(
        'acct', '算账', '算账：到底超了多少',
        '「已用内存」不是直接拿分配器的数字：先扣掉一笔缓冲，再和上限比。',
        lead='输入：分配器的数字和 `maxmemory`。输出：没超（C_OK），或超了（C_ERR）外加要腾出的字节数 `mem_tofree`。',
        src='src/evict.c performEvictions() 556 → getMaxmemoryState() 395',
        blocks=[
            bar([
                seg(10, '其余已分配内存（主要是数据）', 'in', '计入'),
                seg(3, '复制缓冲：backlog 以内', 'in', '计入'),
                seg(2, '复制缓冲：超出 backlog 的部分', 'out', '扣掉'),
                seg(1, 'slot 迁移输出缓冲', 'out', '扣掉'),
                seg(1, 'AOF 缓冲', 'out', '扣掉'),
            ], cap='整条 = 分配器报的 `mem_reported`；实心部分 = 拿去和 `maxmemory` 比的 `mem_used`。宽度只是示意，不是真实比例。'),
            flow([
                step('1', '取分配器的数字', sub='`mem_reported`', ref='`src/evict.c:400`'),
                step('2', '没设 `maxmemory`？', out=[out('ok', '是 → C_OK')], ref='`src/evict.c:404-407`'),
                step('3', '原始数字就没超？', sub='快路径', out=[out('ok', '是 → C_OK')], ref='`src/evict.c:408`'),
                step('4', '扣掉不计入的缓冲', sub='得到 `mem_used`', to='acct.deduct', ref='`src/evict.c:413-414`'),
                step('5', '扣完还超吗？', sub='`mem_tofree = mem_used - maxmemory`', to='acct.state',
                     out=[out('ok', '不超 → C_OK'), out('run', '超 → C_ERR，带回 mem_tofree')],
                     ref='`src/evict.c:422-425`'),
            ]),
            p('C_OK：结果记为 `EVICT_OK`，直接去 [[exit]]（`src/evict.c:556-559`）。C_ERR：带着 `mem_tofree` 往下走，开始找 key 删。'),
        ],
        children=[state, deduct])


def nodes():
    return {'gate': _gate(), 'acct': _acct()}
