from cv import *


def nodes():
    N = {}

    N['delete'] = node(
        'delete', '删 key 并通知', '选中之后：删掉、通知、传播、记账',
        '包一层执行单元，删前后各量一次内存，再把这一个 key 的 DEL/UNLINK 发出去。',
        lead='`bestkey` 选出来以后，主循环对这一个 key 做四件事。影响到：内存账、订阅者、客户端缓存、AOF 和副本、INFO 计数。',
        src='src/evict.c performEvictions() 737-751；src/db.c deleteKeyAndPropagate()',
        blocks=[
            flow([
                step('1', '包一层执行单元', '淘汰不在任何命令里，要自己包', to='delete.unit',
                     ref='`src/evict.c:742`'),
                step('2', '量内存、删、通知、记账', '淘汰和过期共用同一个函数', to='delete.core',
                     ref='`src/db.c:2980-3028`'),
                step('3', '传播：发 DEL 或 UNLINK', '强制发往 AOF 和副本', to='delete.prop',
                     ref='`src/db.c:3062`'),
                step('4', '从哈希表摘掉并释放', '同步还是后台释放，有门槛', to='delete.unlink',
                     ref='`src/db.c:929-955`'),
            ], cap='每一步都能点进去'),
            p('后台释放开启时，这里量出的释放量会偏小，由 [[tick16]] 的第二件事补上；主循环条件见 [[loop]]。'),
        ],
        children=[])

    N['delete.unit'] = node(
        'delete.unit', '执行单元', '为什么每删一个 key 就包一次执行单元',
        '删除会触发通知，通知里模块可能排写操作，必须等这次删除完整结束再跑。',
        src='src/evict.c:737-751',
        blocks=[
            code('src/evict.c', (737, 751), notes=[
                at(742, '提交', '淘汰不在任何命令里，所以自己包一层执行单元。删除会触发通知，模块可能在通知里注册稍后的写操作，要等这次删除结束后再跑。',
                   q='wrapping each expiration/eviction of a key with an execution unit',
                   ref='c:0ffb9d2ea',
                   also=[('注释', 'Execution unit is a code that should be done atomically.', 'src/server.c:4117-4119')],
                   has='enterExecutionUnit'),
                at(743, '推断', '把 key 名复制成新对象：下游要对象；且通知和传播发生在删除之后，那时还要用 key 名，而 bestkey 属于将被删的 key 或候选池格子。',
                   tag='隐含前提', has='createStringObject',
                   more='依据是下面「先删、后通知、再传播」的顺序，没有注释直接这么说。'),
                at(748, '注释', '每删一个就收尾一次，队列里只有一条 DEL，不会被包进 MULTI/EXEC。',
                   q='preferably just after a single deletion batch, so that DEL/UNLINK will NOT be wrapped in MULTI/EXEC',
                   ref='src/db.c:3054-3058',
                   also=[('测试', 'propagation with eviction', 'tests/unit/maxmemory.tcl:531')],
                   tag='反直觉', has='postExecutionUnitOperations',
                   more='机制：只有 `server.also_propagate.numops > 1` 才包事务（`src/server.c:4076-4077`）。测试核对副本收到的是一条条独立的删除。'),
            ]),
        ])

    N['delete.core'] = node(
        'delete.core', '删除主函数', 'deleteKeyAndPropagate：量、删、通知、计数',
        '淘汰和过期共用，只差几个名字；内部分「量释放量」和「通知顺序」两层。',
        src='src/db.c:2980-3028',
        blocks=[
            p('`src/db.c:2983` 的 `lazy_flag` 淘汰时取 `server.lazyfree_lazy_eviction`；`src/db.c:2984-2985` 定下事件名 `evict-del` 和通知名 `evicted`。入口 `src/db.c:3036-3038` 的 `deleteEvictedKeyAndPropagate` 只是带 `NOTIFY_EVICTED` 转调。'),
            p('计数：`src/db.c:3024` 的 `server.stat_evictedkeys++`，对应 INFO 的 `evicted_keys`（`src/server.c:7165`）。'),
        ],
        children=[
            node('delete.core.measure', '量释放量', '只量「删」这一步的内存差',
                 '删前后各算一次，夹在中间的只有 dbGenericDelete。',
                 src='src/db.c:3010-3015',
                 blocks=[
                     code('src/db.c', [(3010, 3015)], notes=[
                         at(3010, '注释', '只量 db 删除本身。通知、传播的内存不算：否则传播 DEL 的开销可能比释放的还大，循环永远退不出。',
                            q='but we can\'t account for that otherwise we would never exit the loop',
                            ref='src/db.c:2995-3004',
                            also=[('提交', 'excluding keyspace notification, client tracking, and propagation to AOF and replicas', 'c:c4fdf09c0')],
                            tag='隐含前提', has='zmalloc_used_memory',
                            more='同段注释：AOF 和输出缓冲迟早会释放，所以只关心键空间的内存。'),
                     ]),
                 ],
                 children=[
                     node('delete.core.negative', '释放量为负', '释放量可能是负数，所以有个强制转换',
                          '懒释放追加列表、别的线程分配，都会让差值为负。',
                          src='src/evict.c:550, 579',
                          blocks=[
                              code('src/evict.c', [(550, 550), (579, 579)], notes=[
                                  at(550, '提交', '`mem_freed` 可能为负：懒释放会往列表追加；两次测量之间别的线程可能在分配。',
                                     q='mem_freed can be negative when: 1. We use lazy free (consuming memory by appending to a list) 2. Thread doing an allocation between the two calls to zmalloc_used_memory.',
                                     ref='c:37fd3d40a', tag='后果', has='May be negative'),
                                  at(579, '提交', '转成 `long long` 比较：否则负数下溢，循环提前退出，没淘汰够。',
                                     q="If 'delta' is negative 'mem_freed' may underflow and cause the while loop to exit prematurely",
                                     ref='c:37fd3d40a', has='long long)mem_tofree'),
                              ]),
                          ]),
                 ]),
            node('delete.core.order', '通知顺序', '先通知，再客户端缓存失效，最后传播',
                 '这三行的顺序跟所有命令、主动过期一致。',
                 src='src/db.c:3017-3019',
                 blocks=[
                     code('src/db.c', (3017, 3019), notes=[
                         at(3017, '提交', '淘汰里也是先通知、再传播，与主动过期和所有命令一致。',
                            q='in eviction, call propagateDeletion after notification, like active-expire and all commands',
                            ref='c:7ac213079', has='notifyKeyspaceEvent'),
                         at(3019, None, '这一行只是把命令放进待传播队列。真正写进 AOF 和复制缓冲，是回到 `src/evict.c:747-748` 的 `postExecutionUnitOperations()` 时；副本真正收到，还要等缓冲写到网络上（见 [[tick16]]）。',
                            tag='易混', has='propagateDeletion'),
                     ]),
                 ]),
        ])

    N['delete.prop'] = node(
        'delete.prop', '传播', '淘汰被当作一条普通 DEL/UNLINK 发出去',
        '开了后台释放发 UNLINK，否则发 DEL；强制发往 AOF 和副本。',
        src='src/db.c:3045-3070',
        blocks=[
            code('src/db.c', (3060, 3070), notes=[
                at(3062, None, '开了后台释放就传 UNLINK，否则传 DEL。', has='shared.unlink'),
                at(3069, '注释', '强制传播：主库删了就一定要告诉副本。',
                   q='If the master decided to delete a key we must propagate it to replicas no matter what.',
                   ref='src/db.c:3067-3068', has='alsoPropagateForced'),
            ]),
            note('注释', '不让副本自己判断，把淘汰当普通删除发出去：删除集中在一处，AOF 和复制链路都保证顺序，所以一致。和「副本默认不自己淘汰」是同一设计的两面：删不删主库一家说了算。',
                 q='This way the key deletion is centralized in one place, and since both AOF and the replication link guarantee operation ordering, everything will be consistent',
                 ref='src/db.c:3045-3048'),
        ])

    N['delete.unlink'] = node(
        'delete.unlink', '摘表与释放', 'dbGenericDelete：从哈希表摘掉，同步或后台释放',
        '过期表一起删；同步还是异步只差一个分支。',
        src='src/db.c:929-955',
        blocks=[
            code('src/db.c', (929, 955), notes=[
                at(930, None, '`blessIsNoEvict` 是给普通删除清保护位的，与淘汰无关：被保护的 key 本来就不会被淘汰选中（见 [[bless]]）。',
                   tag='易混', has='blessIsNoEvict'),
                at((945, 946), None, '过期表里的条目一起删。', has='kvstoreDictDelete'),
                at(952, '推断', '把哈希表条目里的指针置空，大概是为了让最后摘除条目时，不再释放已交给后台的对象。',
                   q='Set the key to NULL in the main dictionary.',
                   more='注释只说了做什么，没讲目的。'),
            ]),
        ],
        children=[
            node('delete.unlink.lazy', '后台释放门槛', '开了开关也不一定走后台',
                 '要「释放成本够大」且「没被别处共享」才交给后台线程。',
                 src='src/lazyfree.c:213-225',
                 blocks=[
                     code('src/lazyfree.c', (213, 225), notes=[
                         at(219, '注释', '成本不高的小对象在后台释放反而更慢，低于门槛就在主线程当场释放；被共享的对象（refcount 不为 1）也不能现在回收。',
                            q='to free in a lazy way is actually just slower',
                            ref='src/lazyfree.c:126-130', tag='反直觉', has='LAZYFREE_THRESHOLD',
                            more='门槛值在 `src/lazyfree.c:131`；为什么是 64 没查到。'),
                         at(223, None, '不满足条件就走这里，主线程直接释放。', has='decrRefCount'),
                     ]),
                     note('文档', '默认是关的：`lazyfree-lazy-eviction no`。',
                          q='In all the above cases the default is to delete objects in a blocking way, like if DEL was called.',
                          ref='redis.conf:1397-1400',
                          also=[(None, 'lazyfree-lazy-eviction no', 'redis.conf:1402')],
                          more='代码默认值在 `src/config.c:4209`。为什么默认关，没查到。'),
                     p('开了以后，`dbGenericDelete` 返回时大对象内存还没还给分配器，释放量会偏小，主循环只信这个数会多删。补这个洞的是 [[tick16]]。'),
                 ]),
        ])

    N['delete']['children'] = [N['delete.unit'], N['delete.core'], N['delete.prop'], N['delete.unlink']]
    return {'delete': N['delete']}
