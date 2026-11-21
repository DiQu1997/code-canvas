# content_a.py —— 总架构：全景 → 何时触发 → 主函数骨架 → 三个返回值 → 索引。其余内容由各槽位模块填（slot）。
from cv import *

EV, SV = 'src/evict.c', 'src/server.c'
TAGS = ['反直觉', '易混', '隐含前提', '历史', '后果']

META = dict(
    title='Redis 内存超限时怎么腾内存',
    srcLabel='代码位置：',
    # 证据等级（cls 决定样式：e1–e4 实线白底 = 有证据；infer / view 虚线 = 没证据）。各模块以这里为准。
    levels=[
        dict(name='注释', cls='e1', desc='代码注释里作者自己写了原因'),
        dict(name='提交', cls='e2', desc='提交说明里写了原因'),
        dict(name='文档', cls='e3', desc='redis.conf 或仓库内文档里写了原因'),
        dict(name='测试', cls='e4', desc='有测试用例把这个行为固定了下来'),
        dict(name='推断', cls='infer', mark='≈', desc='没有直接证据，是从代码行为倒推的（虚线框、黄底）'),
        dict(name='观点', cls='view', mark='※', desc='评价或建议，不是事实（虚线框、粉底）'),
    ],
    who={'me': '呈现者加的'},
    whoDesc={'me': '底稿里没有，是做这张画布的人自己补的推断'},
    tags=[
        dict(name='反直觉', desc='多数人第一反应会猜错'),
        dict(name='易混', desc='名字或数字长得像，其实不是一回事'),
        dict(name='隐含前提', desc='代码没明说，但少了它就不成立'),
        dict(name='历史', desc='现在看着怪，是演变留下来的'),
        dict(name='后果', desc='对使用者有实际影响，容易踩到'),
    ],
)


# 这个项目的槽位表：id -> 默认标题。下面的树用 slot(id) 把它们挂进去，
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


def _panorama():
    return diagram(
        860, 396,
        boxes=[
            box('cmd', '每条命令之前', [14, 96, 170, 60], m=[6, 8, 158, 54], sub='processCommand()', to='trig.cmd'),
            box('timer', '定时器', [14, 236, 170, 60], m=[176, 8, 158, 54], sub='上轮没删完才存在', to='trig.timer'),
            box('pe', 'performEvictions()', [270, 166, 200, 60], m=[6, 112, 176, 56], sub='算账 → 选 → 删，限时', to='main'),
            box('pool', '候选池', [500, 14, 180, 58], m=[6, 222, 130, 54], kind='store', sub='16 格，跨调用保留', to='pool'),
            box('db', '键空间', [660, 166, 186, 60], m=[204, 222, 130, 54], kind='store', sub='db->keys / db->expires',
                msub='keys / expires'),
            box('bio', '后台线程', [660, 320, 186, 58], m=[204, 340, 130, 54], kind='bg', sub='bio_lazy_free，只管释放',
                msub='bio_lazy_free'),
            box('rep', '副本与 AOF', [270, 320, 200, 58], m=[232, 112, 102, 56], kind='ext', sub='收到 DEL / UNLINK',
                msub='收到 DEL'),
        ],
        edges=[
            edge('cmd', 'pe'), edge('timer', 'pe', cls='dash'),
            edge('db', 'pool', '抽样打分', ml='抽样'),
            edge('pool', 'pe', '给出人选', ml='人选'),
            edge('pe', 'db', '删最该删的', ml='删'),
            edge('db', 'bio', '大对象可交后台', ml='大对象', cls='dash'),
            edge('pe', 'rep', '删除被传播', ml='传播'),
        ],
        mw=340, mh=402,
        cap='带 › 的框可以点进去。紫色是唯一的后台线程，其余动作都在主线程。虚线 = 不一定发生：定时器按需存在；'
            '开了 `lazyfree-lazy-eviction` 且对象够大才交后台。')


def _trig():
    cmd = node(
        'trig.cmd', '命令之前', '触发点一：每条命令执行之前',
        '`processCommand()` 里的一段：先踢客户端，再淘汰；只有 FAIL 才拒写。',
        src='src/server.c processCommand() 第 4933–4972 行',
        blocks=[
            code(SV, (4933, 4949), notes=[
                at(4936, '注释', '先踢「缓冲太大」的客户端，再淘汰 key：先把客户端缓冲吃掉的内存这个原因排除。',
                   q='We do this before key eviction, after the last command was executed and consumed some client output buffer memory.',
                   ref='src/server.c:4933-4935', has='evictClients',
                   also=[('提交', 'This prevents eviction or OOM caused by accumulated used memory between all clients.', 'c:2753429c9')]),
                at(4948, '注释', '两个前提：设了上限；并且不是在超时的长脚本里重入事件循环，免得脚本的传播和淘汰的 DEL 混在一起。',
                   q='to avoid mixing the propagation of scripts with the propagation of DELs due to eviction',
                   ref='src/server.c:4944-4947', has='isInsideYieldingLongCommand'),
                at(4949, None, '只有 `EVICT_FAIL` 才记成内存不足。返回 `EVICT_RUNNING`（还超着、还在删）时这里是 0，命令照常执行。为什么见 [[ret]]。',
                   tag='反直觉', has='EVICT_FAIL'),
            ]),
            code(SV, (4951, 4972), notes=[
                at(4955, '注释', '马上冲掉淘汰产生的缓存失效消息；不然它会在本条命令操作这个 key 之后才到，而它其实属于被淘汰前的旧值。',
                   q="If we don't do this, we may get an invalidation message after we perform operation on the key, where in fact this message belongs to the old value of the key before it gets evicted.",
                   ref='src/server.c:4951-4954', has='trackingHandlePendingKeyInvalidations',
                   also=[('提交', "Tracking invalidation messages were sometimes sent in inconsistent order, before the command's reply rather than after.", 'c:fd135f3e2')],
                   more='提交交代了背景：失效消息平时是攒着的，所以这里必须手动冲一次。'),
                at(4959, '注释', '当前客户端可能已经没了：淘汰循环会冲刷副本缓冲，发来这条命令的可能恰好就是被释放的那个副本。',
                   q='performEvictions may flush slave output buffers. This may result in a slave, that may be the active client, to be freed.',
                   ref='src/server.c:4957-4958', has='current_client', tag='反直觉',
                   more='具体是哪条出错路径释放了连接，底稿没有逐行跟，只以注释原话为准。冲刷在 [[tick16]]。'),
                at((4961, 4964), '文档', '只拒绝会涨内存的命令：自己带 `CMD_DENYOOM`，或是 EXEC 且事务里排了这种命令。读命令照常。',
                   q='Redis will start to reply with errors to commands that would use more memory, like SET, LPUSH, and so on, and will continue to reply to read-only commands like GET.',
                   ref='redis.conf:1242-1245', has='is_denyoom_command',
                   more='`is_denyoom_command` 的定义在 `src/server.c:4854-4855`。谁会被拒见 [[fail]]。'),
                at(4971, '注释', '把结论存起来给脚本用：等脚本里第一次写时再查，Lua 栈和参数占的内存会干扰判断。脚本怎么用见 [[fail]]。',
                   q='Save out_of_memory result at command start, otherwise if we check OOM in the first write within script, memory used by lua stack and arguments might interfere.',
                   ref='src/server.c:4966-4970', has='pre_command_oom_state'),
            ]),
        ])
    timer = node(
        'trig.timer', '定时器', '触发点二：按需存在的定时器',
        '上一轮没删完时挂在事件循环上，每圈再删一小轮；它的开关还决定返回值。',
        src='src/evict.c 第 448–473 行',
        blocks=[
            code(EV, (448, 473), notes=[
                at(452, None, '这个开关不只是调度状态，它还直接决定 `performEvictions()` 的返回值（`src/evict.c:787`）：'
                   '「定时器在跑」=「对外宣称还在努力，写命令先放行」。', tag='易混', has='isEvictionProcRunning'),
                at(459, '注释', '回调只做一件事：再调一次 `performEvictions()`。还是 RUNNING 就返回 0，下一圈事件循环再来。',
                   q='This will spin the event loop with short eviction cycles until the … condition has resolved or there are no more evictable items.',
                   ref='src/evict.c:448-451', has='EVICT_RUNNING',
                   more='延迟是 0 毫秒（`src/evict.c:470-471`）；返回 0 时事件循环按 `src/ae.c:339-340` 把它重新排到「现在」。'
                        '所以每转一圈事件循环跑一小轮淘汰，中间穿插处理客户端命令。'),
                at((463, 464), '注释', '只要不是 RUNNING 就停表：OK 是没事了，FAIL 是没东西可删了。',
                   q='For EVICT_OK - things are good, no need to keep evicting. For EVICT_FAIL - there is nothing left to evict.',
                   ref='src/evict.c:461-462', has='AE_NOMORE'),
                at((468, 469), None, '已经在跑就不再创建，所以不会挂两个。', has='isEvictionProcRunning'),
            ]),
            note(None, '挂上它的只有两处：主循环用完时间预算（`src/evict.c:778`），和 `CONFIG SET` 把 maxmemory 调到比当前用量还低（`src/config.c:3431`）。'),
            note('提交', '`CONFIG SET maxmemory` 不当场淘汰，只挂定时器：在一条命令执行中途删 key 并传播 DEL，会把传播顺序搞乱。',
                 q='We do this to fix one of the propagation bugs about eviction see #9890 and #10014.', ref='c:2e1979a21', tag='历史',
                 also=[('提交', 'That could anyway happen since incremental eviction was added in redis 6.2 (see #7653)', 'c:2e1979a21')],
                 more='提交也承认：命令返回时效果不一定可见；但增量淘汰之后这本来就成立，干脆统一交给定时器。'),
        ])
    return node(
        'trig', '何时触发', '什么时候触发：只有两个地方',
        '每条命令执行之前，和一个按需存在的定时器；都在主线程。',
        lead='只有两个触发点，都在主线程。没有「分配内存时触发」这条路。',
        blocks=[
            table(['触发点', '什么时候', '调用处'], [
                ['[[trig.cmd|每条命令之前]]', '命令真正执行前；先踢客户端，再淘汰', '`src/server.c:4949`'],
                ['[[trig.timer|定时器]]', '上一轮没做完，或 maxmemory 被调低时才存在', '`src/evict.c:459`'],
            ]),
            note('注释', '「在命令执行之前调用」是作者写明的要求。',
                 q='This should be called before execution of commands.', ref='src/evict.c:534-536'),
            note('推断', '所以 `maxmemory` 的语义是「超了以后尽快回到线下」，不是「永不超过」。',
                 more='依据：全仓库 `performEvictions()` 只有上面两个调用点，没有任何一处在分配内存时调用它。'),
        ],
        children=[cmd, timer])


def _pick():
    return node(
        'pick', '选谁', '选谁：按策略分两条路',
        '要打分的策略走候选池，random 类直接随机取；BLESS 过的 key 永不入选。',
        lead='要打分的策略（LRU / LFU / LRM / TTL）走候选池；random 策略直接随机取一个。',
        src='src/evict.c performEvictions() 主循环前半',
        blocks=[
            diagram(
                860, 324,
                boxes=[
                    box('policy', '看策略', [14, 130, 150, 60], m=[6, 8, 150, 54], kind='store', sub='maxmemory_policy', to='policy'),
                    box('clock', '对象头 24 位', [310, 14, 170, 56], m=[198, 8, 136, 54], kind='store', sub='打分的原料', to='clock'),
                    box('pool', '候选池', [310, 130, 170, 60], m=[198, 120, 136, 54], kind='store', sub='抽样、打分、进池', to='pool'),
                    box('loop', '取池里最优', [540, 130, 170, 60], m=[198, 232, 136, 54], sub='主循环怎么用池', to='loop'),
                    box('rnd', '随机取一个', [310, 250, 170, 60], m=[6, 120, 150, 54], sub='不经过池', to='random'),
                    box('got', '删掉它', [740, 190, 106, 60], m=[6, 232, 150, 54], sub='下一步', to='pace'),
                ],
                edges=[
                    edge('policy', 'pool', 'LRU/LFU/LRM/TTL', ml='LRU 等'),
                    edge('policy', 'rnd', 'random', ml='random'),
                    edge('clock', 'pool', '打分', ml='打分'),
                    edge('pool', 'loop'), edge('loop', 'got'), edge('rnd', 'got'),
                ],
                mw=340, mh=296, cap='每个框都能点进去。'),
            p('从哪个集合抽也由策略定：allkeys 类从 `db->keys` 抽，volatile 类从 `db->expires` 抽（`src/evict.c:603-607`）；'
              '每次只抽 `server.maxmemory_samples` 个（`src/evict.c:148`）。'),
            p('例外：BLESS 过的 key 带保护位 `no_evict`（`src/object.h:130`），永不被选中，见 [[bless]]。为什么不做精确 LRU，见 [[why]]。'),
        ],
        children=[slot('policy'), slot('clock'), slot('pool'), slot('loop'), slot('random'), slot('bless')])


def _pace():
    return node(
        'pace', '删与看表', '删一个，记账；每 16 个看一次表',
        '选中之后怎么删、通知谁；多久抬头看一次时间，预算从哪来。',
        src='src/evict.c performEvictions() 主循环后半',
        blocks=[
            flow([
                step('1', '删掉这个 key，并通知出去', to='delete',
                     sub='默认主线程当场释放；开了 lazyfree 且对象够大才交给 `bio_lazy_free`。然后发 `evicted` 事件、让客户端缓存失效、把 DEL/UNLINK 传给 AOF 和副本'),
                step('2', '每删 16 个 key 看一次表', sub='`keys_freed % 16 == 0`', to='tick16', ref='`src/evict.c:753`',
                     out=[out('ok', '后台释放已让内存达标 → break'), out('run', '预算用完 → 挂定时器，break')]),
                step('3', '表上的时间从哪来', sub='进循环前由 `evictionTimeLimitUs()` 算出本轮预算', to='budget', ref='`src/evict.c:566-576`'),
            ]),
        ],
        children=[slot('delete'), slot('tick16'), slot('budget')])


def _main():
    skel = node(
        'main.skel', '骨架代码', '骨架代码：几处容易看错',
        '函数头注释写明了目标；`result` 初值就是失败；`cant_free` 不是失败专用。',
        src='src/evict.c performEvictions() 第 519–790 行（节选）',
        blocks=[
            code(EV, (519, 546), cap='函数头注释：作者自己写的设计目标。', notes=[
                at((522, 526), '注释', '内存可能突然超出很多（大块分配如哈希表扩容，或上限被手动调低），所以淘汰必须限时，否则服务器会卡住。',
                   q="it's important to evict for a managed period of time - otherwise Redis would become unresponsive while evicting.",
                   has='managed period of time'),
                at((528, 532), '注释', '目标是「改善」内存状况，不是立刻解决：删了一些但还没达标，就挂定时器接着删。',
                   q='The goal of this function is to improve the memory situation - not to immediately resolve it.', has='aeTimeProc'),
                at((534, 536), '注释', '契约：返回 `EVICT_FAIL` 才该拒绝会涨内存的命令。这里没有提 RUNNING。',
                   q='If EVICT_FAIL is returned, commands which will result in increased memory usage should be rejected.', has='EVICT_FAIL'),
            ]),
            code(EV, [(553, 553), (575, 579), (782, 790)], cap='三段节选：初值、进循环前的断言、循环之后。', notes=[
                at(553, None, '`result` 的初值就是失败。「失败」不是某处显式赋的，而是没有任何成功路径改写过它；'
                   '`goto cant_free`（第 782–784 行）正是靠这个初值表达「没东西可删」。', tag='隐含前提', has='EVICT_FAIL'),
                at((575, 576), '注释', '进循环前断言传播队列为空：淘汰只该发生在两条命令之间，队列里还有东西说明别处有 bug，宁可崩也不悄悄绕过。',
                   q='Try to smoke-out bugs (server.also_propagate should be empty here)', has='also_propagate',
                   also=[('提交', 'Keep that condition may hide bugs (i.e. performEvictions should always expect to have an empty server.also_propagate)', 'c:8ad0cfa56')]),
                at(787, None, '正常出口：定时器在跑就返回 RUNNING，否则 OK。删够了和中途 break 都落到这一行。', has='isEvictionProcRunning'),
                at((789, 790), None, '`cant_free` 名字像「失败专用」，其实是所有进过主循环的路径的公共收尾：标签不是函数，第 787 行执行完会自然流进来，'
                   '只是 `result == EVICT_FAIL` 不成立，里面的等待不执行。', tag='易混', has='cant_free'),
            ]),
        ])
    return node(
        'main', '主函数骨架', 'performEvictions() 的九个阶段',
        '闸 → 算账 → 循环（选、删、看表）→ 三种出口。每一步都能点下去。',
        src='src/evict.c performEvictions()，第 543 行起',
        blocks=[
            flow([
                step('A', '安全闸', sub='`isSafeToPerformEvictions()` 说不行就直接返回', to='gate', ref='`src/evict.c:546`',
                     out=[out('ok', '假的 EVICT_OK，连指标都不记')]),
                step('B', '算账', sub='`getMaxmemoryState` 说没超就结束', to='acct', ref='`src/evict.c:556-559`', out=[out('ok', 'EVICT_OK')]),
                step('C', '策略禁止', sub='策略是 `MAXMEMORY_NO_EVICTION` 就不删', to='policy', ref='`src/evict.c:561-564`',
                     out=[out('fail', 'EVICT_FAIL')]),
                step('D', '起表', sub='算出本轮时间预算、开始计时；断言传播队列为空', to='budget', ref='`src/evict.c:566-576`'),
                step('E', '主循环：选一个 key', sub='循环条件 `mem_freed < mem_tofree`', to='pick', loop=True, ref='`src/evict.c:579`'),
                step('E′', '删掉、记账；每 16 个看一次表', to='pace', loop=True,
                     out=[out('run', '预算用完 → 挂定时器'), out('fail', '选不出 key → cant_free')]),
                step('F', '正常出口', sub='按 `isEvictionProcRunning` 决定返回值', to='ret', ref='`src/evict.c:787`',
                     out=[out('run', 'EVICT_RUNNING'), out('ok', 'EVICT_OK')]),
                step('G', '无物可删', sub='标签 `cant_free`：result 仍是 FAIL 才等一等后台释放线程', to='exit', ref='`src/evict.c:789-806`'),
                step('H', 'BLESS 宽限', sub='被保护的 key 挡住时，1.25 倍以内放行', to='exit', ref='`src/evict.c:813-818`'),
                step('I', '记指标', sub='标签 `update_metrics`：累计「超限持续了多久」，然后返回 result', to='exit', ref='`src/evict.c:823-833`'),
            ], cap='九个阶段 A–I；E 拆成两行只为各自下钻。'),
            h('主循环的三种离开方式'),
            table(['怎么离开', '在哪', '结果'], [
                ['删够了', '循环条件不再成立', '落到第 787 行，由 `isEvictionProcRunning` 定返回值'],
                ['中途 break', '后台释放已让内存达标 `src/evict.c:767-771`，或时间预算用完 `src/evict.c:776-779`', '同样落到第 787 行'],
                ['选不出 key', '`goto cant_free` `src/evict.c:782-784`', '跳过第 787 行，result 保持初值 FAIL'],
            ], cap='代码见 [[main.skel]]。'),
        ],
        children=[skel, slot('gate'), slot('acct'), slot('resize'), _pick(), _pace()])


def _ret():
    return node(
        'ret', '三个返回值', '三个返回值，以及「还在删」时为什么放行',
        'OK 有真假两种；RUNNING 时写命令照样放行；只有 FAIL 才回 OOM。',
        src='src/evict.c 第 538–541 行；src/server.c 第 4949 行',
        blocks=[
            code(EV, (538, 541), notes=[
                at(539, '注释', 'OK 有两种完全不同的含义：真的没超；或「现在不允许淘汰」，作者自己叫它假的 OK（见 [[gate]]）。',
                   q="memory is OK or it's not possible to perform evictions now", tag='易混', has='EVICT_OK',
                   also=[('注释', "it's a fake EVICT_OK.", 'src/evict.c:544-545')]),
                at(540, '注释', 'RUNNING = 还超着，但还在删。命令入口只判断 `== EVICT_FAIL`（`src/server.c:4949`），所以此时写命令照样放行。',
                   q='memory is over the limit, but eviction is still processing', tag='反直觉', has='EVICT_RUNNING'),
                at(541, '注释', 'FAIL = 还超着，而且没东西可删。只有它会让带 DENYOOM 标志的命令收到 OOM。',
                   q="memory is over the limit, and there's nothing to evict", has='EVICT_FAIL'),
            ]),
            table(['返回值', '命令入口', '定时器'], [
                ['`EVICT_OK` `src/server.h:4757`', '照常执行命令', '停表'],
                ['`EVICT_RUNNING` `src/server.h:4758`', '照常执行命令', '返回 0，下一圈事件循环再来'],
                ['`EVICT_FAIL` `src/server.h:4759`', '带 DENYOOM 标志的命令回 OOM 错误', '停表'],
            ]),
            h('还在删的时候，为什么敢放行'),
            note('提交', '因为目标是「改善」而不是立刻解决（注释见 [[main.skel]]）；代价是承认的：极端情况下内存会继续涨。',
                 q='There is a risk that some other edge cases (like massive pipelined use of MGET) could cause Redis memory usage to keep growing despite the eviction attempts, so a new maxmemory-eviction-tenacity config is introduced to let users mitigate that.',
                 ref='c:810e28a39', tag='后果', more='这是引入增量淘汰的那次提交；`maxmemory-eviction-tenacity` 就是留给用户的缓解手段。'),
            note('推断', '放行而不拒绝：「时间到了但还有 key 可删」不等于「没内存了」，这时拒写会把一次延迟优化变成一批业务报错。',
                 more='依据：注释和提交都把目标定为「改善而不是立刻解决」；但没有哪句话直接说「RUNNING 时必须放行」。'),
            note('提交', '能不能接受写命令，由这个函数的返回值说了算，命令入口不自己比内存。',
                 q='To say if we can accept or not write queries is up to the return value of freeMemoryIfNeeded(), that has full control about that.',
                 ref='c:f6b32c14f', tag='历史',
                 more='2012 年的提交，那时函数还叫 `freeMemoryIfNeeded()`。底稿的解释：记账规则（扣掉哪些缓冲）只有这个函数知道，入口自己比会得出不一样的结论。'),
        ],
        children=[slot('exit'), slot('fail')])


def _idx():
    return node(
        'idx', '索引', '索引：易困惑点、全部推断、引用的提交',
        '按标签收拢全画布的易困惑点，列出所有推断和引用过的提交；可改进处和没查到的也挂在这里。',
        blocks=[h('易困惑点（按标签）'), collect('tag', TAGS), h('全部推断'), collect('lv', ['推断']), h('引用过的提交'), commits()],
        children=[slot('improve'), slot('unknown')])


def root():
    return node(
        'root', '全景', 'Redis 内存超过 maxmemory 时怎么腾内存',
        '每条命令执行前算一笔账，超了就抽样、打分、删最该删的 key。',
        lead='每条命令执行前，主线程先算一笔账；超了就「随机抽几个 key → 打分 → 放进候选池 → 删掉池里最该删的」，一个一个删，'
             '直到账平了、这一轮时间预算用完、或确实没有可删的 key。只有最后一种才让写命令收到 OOM。',
        blocks=[
            _panorama(),
            note('注释', '`maxmemory` 不是硬上限：检查在命令执行之前，命令执行中涨多少都不当场拦。',
                 q="It's possible for Redis to suddenly be significantly over the … setting", ref='src/evict.c:522-526', tag='反直觉',
                 more='而且一轮淘汰没做完时，写命令照样放行，见 [[ret]]。'),
            note('推断', '没有专门的淘汰线程：算账、选、删、通知、传播全在主线程；后台线程只替它释放内存，不参与「选谁」。',
                 more='依据：`performEvictions()` 全仓库只有两个调用点（`src/server.c:4949`、`src/evict.c:459`），都在主线程；'
                      '选和删的代码里没有加锁。没找到哪条注释或提交专门解释「为什么淘汰不放到后台线程」。'),
            h('图上没画的'),
            table(['东西', '在淘汰里的角色'], [
                ['键空间的两个 kvstore', 'allkeys 类策略从 `db->keys` 抽样，volatile 类从 `db->expires`（`src/evict.c:603-607`）'],
                ['策略 `maxmemory_policy`', '决定从哪抽、按什么打分；默认不淘汰（`src/config.c:4294`）→ [[policy]]'],
                ['对象头 24 位 `lru`', '存「最近访问时间」或「访问频率」，打分的原料（`src/object.h:112`）→ [[clock]]'],
                ['保护位 `no_evict`', 'BLESS 过的 key，永不被选中（`src/object.h:130`）→ [[bless]]'],
                ['待传播队列 `also_propagate`', '删除产生的 DEL/UNLINK 先进这里，再发给 AOF 和副本（`src/evict.c:576`）→ [[delete]]'],
                ['订阅者与缓存客户端', '收到 `evicted` 事件（`src/db.c:3017`）；缓存的这个 key 被置失效（`src/db.c:3018`）'],
            ]),
            p('读法：[[trig]] → [[main]] → [[ret]] → [[why]]；易困惑点汇总在 [[idx]]。'),
        ],
        children=[_trig(), _main(), _ret(), slot('why'), _idx()])
