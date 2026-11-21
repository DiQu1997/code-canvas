# content_why.py —— 槽位 why：宏观为什么（精简版）。七个设计决定各一条；长理由在 more 里。
# 决定二、三、五、六的证据原话在别的槽位（底稿第 5 节），这里只指过去，不重复引用、不另定等级。
from cv import node, h, note


def nodes():
    return {'why': node(
        'why', '为什么这样', '宏观：为什么设计成这样',
        '七个设计决定，各一两句：换来什么、代价是什么、证据在哪。',
        lead='把各页的局部「为什么」收拢成七个设计决定。每条先说决定和代价；长理由点「展开」。',
        blocks=[
            h('它解决什么问题'),
            note('文档', '两种用法：当缓存（满了自动腾），或当硬上限（noeviction：满了拒写、绝不丢数据）。',
                 q="This option is usually useful when using Redis as an LRU or LFU cache, or to set a hard memory limit for an instance (using the 'noeviction' policy).",
                 ref='redis.conf:1247-1248',
                 more='默认策略是后一种：`redis.conf:1299` 的 `maxmemory-policy noeviction`，代码默认值在 `src/config.c:4294`。'
                      '而 `maxmemory` 本身默认是 0，也就是不限（`src/config.c:4393`）。所以什么都没配的 Redis 既不淘汰也不拒写。'
                      '为什么默认策略选 noeviction，没查到，见 [[unknown]]。'),
            note('注释', '32 位实例是例外：没配上限时，启动自动设成 3GB 加 noeviction。',
                 q='This avoids useless crashes of the Redis instance for out of memory.', ref='src/server.c:3308-3311',
                 more='代码在 `src/server.c:3312-3315`。'),
            h('七个设计决定'),
            note('文档', '一 · 近似，而不是精确。不维护全局 LRU 链表，每次只抽几个 key 打分；为了省内存。',
                 q='LRU, LFU and minimal TTL algorithms are not precise algorithms but approximated algorithms (in order to save memory), so you can tune it for speed or accuracy.',
                 ref='redis.conf:1301-1305',
                 also=[('注释', 'Redis uses an approximation of the LRU algorithm that runs in constant memory.', 'src/evict.c:98-101'),
                       ('文档', 'The default of 5 produces good enough results. 10 Approximates very closely true LRU but costs more CPU. 3 is faster but not very accurate.', 'redis.conf:1307-1309')],
                 more='代价：删掉的只是「抽到过的 key 里最该删的」，不是全局最该删的；候选池（决定二）用来缩小这个差距。怎么选见 [[pick]]。'),
            note('推断', '省下的具体是什么：精确 LRU 要把所有 key 串成按访问时间排序的链，每个 key 至少多两个指针，每次访问还要挪节点；'
                 '近似做法只用对象头里本来就有的 24 位。',
                 more='依据是这个字段的位置和大小（`src/object.h:112`）。仓库里的文档只说了「为了省内存」，没有做这个对比。'),
            note('提交', '二 · 候选池跨调用保留。代价：池里会有过时条目，所以取用时必须回查。',
                 ref=['c:22c9cfaf5', 'c:6416ab19d'], more='提交原话在 [[pool]]；回查在 [[loop]]。'),
            note('提交', '三 · 跨库统一选，而不是每个库各选各的。', ref='c:e423f76e7', more='证据原话在 [[loop]]。'),
            note('提交', '四 · 限时、增量，而不是一次删够。代价：上限变软，「还在删」时写命令照样放行，极端情况下内存会继续涨。',
                 q='Rather than blindly evicting until maxmemory limit is achieved, this update adds a time limit to eviction. While over the maxmemory limit, eviction will process before each command AND as a timeProc when no commands are running.',
                 ref='c:810e28a39', more='提交说明自己承认了这个代价，原话见 [[ret]]。节奏怎么控见 [[pace]]。'),
            note(None, '五 · 记账时扣掉复制缓冲和 AOF 缓冲（`src/evict.c:329`、`src/evict.c:413-414`）。为什么、证据都在 [[acct]]。'
                 '代价：这部分内存不受 maxmemory 约束，要靠运维预留。'),
            note(None, '六 · 删不删由主库一家说了算：副本默认不淘汰（[[gate]]），主库把每次淘汰当成一条删除命令传下去（[[delete]]）。'
                 '代价：副本的内存占用完全跟着主库走，自己没有保护。'),
            note('文档', '七 · 删除默认在主线程同步完成，后台释放是可选项。同步删除的风险，配置文件写得很直白：可能阻塞很久，甚至数秒。',
                 q='the server can block for a long time (even seconds) in order to complete the operation.', ref='redis.conf:1360-1367',
                 also=[('文档', 'Another thread will incrementally free the object in the background as fast as possible.', 'redis.conf:1369-1373')],
                 more='淘汰是可以打开后台释放的场景之一（`redis.conf:1382-1384`）。为什么默认不开，没查到，见 [[unknown]]。'
                      '一条历史线索：提交 `c:7af4eeb74` 的标题是 "Lazyfree: incremental removed, only threaded survived."，'
                      '说明早期还试过不用线程的增量释放，后来删掉了；这次提交没有正文，原因没查到。'),
        ])}
