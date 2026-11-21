# Redis 内存超过 maxmemory 时怎么腾内存、为什么这么做（研究底稿）

> 写给第一次读这份代码的工程师。先给结论和全景，再一层层下钻到真实代码；每个「为什么」都标了证据等级。

## 0. 读法说明

**代码版本。** 仓库 HEAD 是 fd7710ca2，工作区干净。这是比较新的开发分支：数据库已经是按 slot 分片的 kvstore，淘汰策略里多了 LRM，还有 BLESS（受保护、不可淘汰的 key）和 ASM（原子 slot 迁移）。如果你手边是老版本，函数名和行号会不一样。

**锚点。** 形如 `src/evict.c:543` 的是「文件:行号」，例如这一行上就是 `int performEvictions(void) {`。所有锚点都对 HEAD 成立，并用脚本机械核对过（见第 10 节）。代码摘录块的第一行注释写明它来自哪个文件的哪些行，内容逐行原样复制；块里单独一行的 `...` 表示中间跳过了若干行。

**引文。** 英文直引号里的英文，是源码注释、配置文件注释或提交说明的原话（逐字；中间用 … 表示省略）。直角引号「」里的是我的转述或强调，不是原话。

<!-- legend-start -->
**「为什么」的证据等级（每条必居其一）：**

- 【注释】源码注释明说，带锚点。
- 【提交】提交说明里写了，带 9 位短哈希并引用原话。
- 【文档】仓库内文档或配置文件注释（主要是 redis.conf），带锚点。
- 【测试】测试用例佐证，带锚点。
- 【推断】我自己的推理，没有直接证据；会写明依据。
- 另有【观点】：只用于「可改进处」，是我的看法，不是事实。
<!-- legend-end -->

**这份底稿的边界。** 我没有外网，PR 和 issue 的正文读不到；凡是来自历史的「为什么」，依据都只是本地 git 里的提交说明。提交说明里写「见某号 issue」的地方，我没法跟过去看。查了但没找到理由的地方集中列在第 9 节，没有用听起来合理的说法补上。

**老名字。** 老提交里这个函数叫 freeMemoryIfNeeded()，例如 `cd90389b3` 的标题就是 "freeMemoryIfNeeded(): improve code and lazyfree handling."。现在它叫 `performEvictions()`，读历史时把两个名字当成同一个东西即可。

**章节地图。** 第 1 节给答案；第 2 节是全景（第 0 层）；第 3 节讲什么时候触发（第 1 层）；第 4 节是主函数骨架（第 2 层）；第 5 节逐段下钻到真实代码（第 3 层）；第 6 节讲宏观上为什么这样设计；第 7 节是读者最容易困惑的点；第 8 节可改进处；第 9 节没查到为什么；第 10 节附录。


## 1. 先给答案

**一句话。** 每条命令执行之前，主线程先算一笔账；如果超了，就按配置的策略「随机抽几个 key → 打分 → 放进候选池 → 删掉池里最该删的那个」，一个一个地删，直到账平了、或这一轮的时间预算用完、或确实没有可删的 key。只有最后一种情况，才会让要占内存的写命令收到 OOM 错误。

**三个最关键的设计（后文逐个展开）：**

1. **近似，而不是精确。** Redis 不维护全局的 LRU 链表，而是每次只抽 `server.maxmemory_samples` 个 key 打分（`src/evict.c:148`），再配一个跨调用保留的 16 格候选池 `EvictionPoolLRU`（`src/evict.c:51`）。目的是省内存。
2. **限时，而不是一次删够。** 主循环每删 16 个 key 看一次表（`src/evict.c:753` 的 `keys_freed % 16 == 0`），超过预算就调用 `startEvictionTimeProc()` 挂一个定时器接着干（`src/evict.c:778`），自己先返回。目的是不让服务器卡住。
3. **记账时扣掉复制缓冲和 AOF 缓冲。** `freeMemoryGetNotCountedMemory()` 算出这部分（`src/evict.c:329`），`getMaxmemoryState()` 再把它从已用内存里减掉（`src/evict.c:413-414` 的 `overhead`）。目的是防止「删 key 产生 DEL → 缓冲变大 → 超得更多 → 删更多」的正反馈把库删空。

**一个要先纠正的直觉。** maxmemory 不是硬上限。检查发生在命令执行之前，命令执行过程中涨多少都不会当场拦；而且一轮淘汰没做完时，写命令照样放行。细节见 3.1 和 4.2。

## 2. 第 0 层：全景——谁参与，怎么配合

### 2.1 最粗的一张流程（文字示意，不是代码）

```text
客户端命令到达
  └─ 主线程 processCommand()
       ├─ evictClients()              先处理「客户端缓冲太大」（另一套机制，踢客户端）
       ├─ performEvictions()          本稿主角
       │    ├─ 现在能做吗？   isSafeToPerformEvictions()
       │    ├─ 超了多少？     getMaxmemoryState() → mem_tofree
       │    ├─ 循环：选一个 key → 删掉 → 记账（每 16 个 key 检查一次时间）
       │    │     选：LRU/LFU/LRM/TTL 策略 → evictionPoolPopulate() 抽样打分进池，取池里最优
       │    │         random 策略         → 直接随机取一个
       │    │     删：deleteEvictedKeyAndPropagate() → dbGenericDelete()
       │    │           ├─ 默认：主线程当场释放
       │    │           └─ 开了 lazyfree 且对象够大：交给 bio_lazy_free 线程
       │    │         然后：发 evicted 事件 → 让客户端缓存失效 → 把 DEL/UNLINK 传给 AOF 和副本
       │    └─ 返回 EVICT_OK / EVICT_RUNNING / EVICT_FAIL
       ├─ EVICT_FAIL 且命令带 DENYOOM 标志 → 回 -OOM，命令不执行
       └─ 否则照常执行命令

事件循环的定时器（只在「上一轮没做完」或「maxmemory 被调低」时才存在）
  └─ evictionTimeProc() → 再调 performEvictions()，直到不再返回 EVICT_RUNNING
```

### 2.2 线程：只有两个角色

- **主线程**：算账、选 key、删 key、发通知、传播 DEL，全在这里。`performEvictions()` 全仓库只有两个调用点，命令入口 `src/server.c:4949` 和定时器回调 `src/evict.c:459`，两处都跑在主线程的事件循环里。
- **bio_lazy_free 后台线程**：只在打开 `lazyfree-lazy-eviction` 且对象够大时，替主线程做真正的内存释放（`src/lazyfree.c:219-221` 的 `bioCreateLazyFreeJob`）。线程名见 `src/bio.c:54` 的 `bio_lazy_free`。它不参与「选谁」。
- **没有专门的淘汰线程。**【推断】依据是上面两个调用点都在主线程，选 key 和删 key 的代码里也没有任何加锁；这符合 Redis「键空间只由主线程读写」的通用执行模型，但我没有找到哪条注释或提交专门解释「为什么淘汰不放到后台线程」。

### 2.3 存储单元与数据结构

| 东西 | 锚点 | 在淘汰里的角色 |
|---|---|---|
| 上限 `maxmemory` | `src/server.h:2657` | 为 0 表示不限，此时直接返回（`src/evict.c:404` 的 `!server.maxmemory`） |
| 策略 `maxmemory_policy` | `src/server.h:2659` | 决定从哪个集合抽样、按什么打分，默认值是 `MAXMEMORY_NO_EVICTION`（`src/config.c:4294`） |
| 每个对象头里的 24 位字段 `lru:LRU_BITS` | `src/object.h:112` | 存「最近访问时间」或「访问频率」，是打分的原料 |
| 候选池 `EvictionPoolLRU` | `src/evict.c:51` | 全局唯一、16 格（`src/evict.c:36` 的 `EVPOOL_SIZE`），跨调用保留 |
| 每个 DB 的两个 kvstore：`db->keys` 与 `db->expires` | `src/evict.c:603-607` | allkeys 类策略从前者抽样，volatile 类策略从后者抽样 |
| 待传播队列 `server.also_propagate` | `src/evict.c:576` | 删除产生的 DEL/UNLINK 先进这里，再发给 AOF 和副本 |
| 后台任务计数 `bio_jobs_counter` | `src/bio.c:73` | 没东西可删时，用它判断后台线程是否还在释放内存 |
| 保护位 `no_evict` | `src/object.h:130` | BLESS 过的 key，永不被选中 |
| 定时器标志 `isEvictionProcRunning` | `src/evict.c:452` | 表示「还有一轮没做完」，同时决定返回值是不是 EVICT_RUNNING |

### 2.4 会被淘汰波及的外部对象

- **副本与 AOF。** 淘汰被当成一次普通删除传播出去：`src/db.c:3069` 的 `alsoPropagateForced`，目标是 `PROPAGATE_AOF|PROPAGATE_REPL`。
- **开了客户端缓存（client tracking）的客户端。** `src/db.c:3018` 的 `keyModified` 会让它们缓存的这个 key 失效。
- **订阅 keyspace 事件的客户端和模块。** `src/db.c:3017` 的 `notifyKeyspaceEvent` 发出事件，事件名是 `evicted`（`src/db.c:2985`）。
- **普通客户端的缓冲。** `evictClients()`（`src/server.c:4936`）是另一套「踢客户端」的机制，排在 key 淘汰之前先跑，本稿只在入口处提一句。

### 2.5 文件分工

| 文件 | 职责 | 从哪里读起 |
|---|---|---|
| `src/evict.c` | 记账、选 key、主循环、时间预算、定时器 | `src/evict.c:543` 的 `performEvictions` |
| `src/server.c` | 命令入口、执行单元与传播、dict 扩容许可、INFO 指标 | `src/server.c:4949`（调用 `performEvictions()` 的那一行） |
| `src/db.c` | 真正的删除、通知、传播，以及访问 key 时更新时钟 | `src/db.c:2980` 的 `deleteKeyAndPropagate` |
| `src/lazyfree.c`、`src/bio.c` | 后台释放 | `src/lazyfree.c:213` 的 `freeObjAsync` |
| `src/kvstore.c`、`src/dict.c` | 公平地随机挑一个 slot、从一个 dict 里抓一批 key | `src/kvstore.c:418-419` 的 `kvstoreGetFairRandomDictIndex` 与 `src/dict.c:1347` 的 `dictGetSomeKeys` |
| `src/object.h`、`src/object.c` | 对象头里的 24 位字段及其初始化 | `src/object.c:124-135` 的 `initObjectLRUOrLFU` |
| `src/bless.c` | NO-EVICT 保护位的读写 | `src/bless.c:99-102` 的 `blessIsNoEvict` |
| `src/script.c` | 脚本内部的 OOM 判定 | `src/script.c:456` 的 `scriptVerifyOOM` |
| `src/config.c`、`redis.conf` | 可调参数与默认值 | `src/config.c:4326` 的 `maxmemory-samples` |


## 3. 第 1 层：什么时候触发

**结论。** 只有两个触发点，都在主线程：（1）每条命令执行之前；（2）一个按需存在的定时器。没有「分配内存时触发」这条路。

### 3.1 触发点一：每条命令执行之前

下面是 `processCommand()` 里和内存有关的一段，位置在命令真正执行之前。【注释】`src/evict.c:534-536` 对调用时机的要求是 "This should be called before execution of commands."

```c
/* src/server.c:4933-4972 */
    /* Disconnect some clients if total clients memory is too high. We do this
     * before key eviction, after the last command was executed and consumed
     * some client output buffer memory. */
    evictClients();
    if (server.current_client == NULL) {
        /* If we evicted ourself then abort processing the command */
        return C_ERR;
    }

    /* Handle the maxmemory directive.
     *
     * Note that we do not want to reclaim memory if we are here re-entering
     * the event loop since there is a busy Lua script running in timeout
     * condition, to avoid mixing the propagation of scripts with the
     * propagation of DELs due to eviction. */
    if (server.maxmemory && !isInsideYieldingLongCommand()) {
        int out_of_memory = (performEvictions() == EVICT_FAIL);

        /* performEvictions may evict keys, so we need flush pending tracking
         * invalidation keys. If we don't do this, we may get an invalidation
         * message after we perform operation on the key, where in fact this
         * message belongs to the old value of the key before it gets evicted.*/
        trackingHandlePendingKeyInvalidations();

        /* performEvictions may flush slave output buffers. This may result
         * in a slave, that may be the active client, to be freed. */
        if (server.current_client == NULL) return C_ERR;

        if (out_of_memory && is_denyoom_command) {
            rejectCommand(c, shared.oomerr);
            return C_OK;
        }

        /* Save out_of_memory result at command start, otherwise if we check OOM
         * in the first write within script, memory used by lua stack and
         * arguments might interfere. We need to save it for EXEC and module
         * calls too, since these can call EVAL, but avoid saving it during an
         * interrupted / yielding busy script / module. */
        server.pre_command_oom_state = out_of_memory;
    }
```

逐段看：

- **先踢客户端，再淘汰 key。** `src/server.c:4936` 先调 `evictClients()`。【注释】`src/server.c:4933-4935`："We do this before key eviction, after the last command was executed and consumed some client output buffer memory."【提交 `2753429c9`】说这套机制的目的是 "This prevents eviction or OOM caused by accumulated used memory between all clients."，也就是先把「客户端缓冲吃掉的内存」这个原因排除，别让数据替客户端背锅。
- **两个前提。** `src/server.c:4948` 要求 `server.maxmemory` 非 0，并且 `!isInsideYieldingLongCommand()`，也就是当前不是在一个超时的长脚本里重入事件循环。【注释】`src/server.c:4942-4947`："to avoid mixing the propagation of scripts with the propagation of DELs due to eviction"。
- **只有 EVICT_FAIL 才算内存不足。** `src/server.c:4949` 把 `performEvictions() == EVICT_FAIL` 记成 `out_of_memory`。返回 EVICT_RUNNING（还超着，但还在删）时这里是 0，命令照常执行。这一点最反直觉，4.2 节专门讲。
- **马上把淘汰产生的缓存失效消息发掉。** `src/server.c:4955` 调 `trackingHandlePendingKeyInvalidations()`。【注释】`src/server.c:4951-4954`："If we don't do this, we may get an invalidation message after we perform operation on the key, where in fact this message belongs to the old value of the key before it gets evicted."【提交 `fd135f3e2`】交代了背景："Tracking invalidation messages were sometimes sent in inconsistent order, before the command's reply rather than after."，所以失效消息平时是攒着的，这里必须手动冲一次。
- **当前客户端可能已经没了。** `src/server.c:4959` 检查 `server.current_client == NULL` 就直接返回。【注释】`src/server.c:4957-4958`："performEvictions may flush slave output buffers. This may result in a slave, that may be the active client, to be freed."。这是「效果在别处」的典型：淘汰循环里会强制冲刷副本缓冲（见 5.5），冲刷过程中副本连接可能被释放，而发来当前命令的恰好可能就是那个副本。具体是哪条出错路径释放了连接，我没有逐行跟，这里只以注释原话为准。
- **只拒绝会涨内存的命令。** `src/server.c:4961-4964` 在 `out_of_memory && is_denyoom_command` 时用 `shared.oomerr` 拒绝。`is_denyoom_command` 的定义在 `src/server.c:4854-4855`：命令自己带 `CMD_DENYOOM`，或者它是 EXEC 且事务里排队的命令带这个标志。【文档】`redis.conf:1242-1245`："Redis will start to reply with errors to commands that would use more memory, like SET, LPUSH, and so on, and will continue to reply to read-only commands like GET."。
- **把结论存起来给脚本用。** `src/server.c:4971` 写 `server.pre_command_oom_state`。【注释】`src/server.c:4966-4970`："Save out_of_memory result at command start, otherwise if we check OOM in the first write within script, memory used by lua stack and arguments might interfere."。脚本里怎么用它见 5.9。

**这里读者最容易困惑：检查在命令之前，所以上限是软的。** 一条命令执行过程中内存涨多少都不会被拦，要等下一条命令进来才会淘汰。【注释】`src/evict.c:522-526` 承认 "It's possible for Redis to suddenly be significantly over the … setting"。【推断】由此可知 maxmemory 的语义是「超了以后尽快回到线下」，不是「永不超过」；依据是全仓库 `performEvictions()` 只有上面说的两个调用点，没有任何一处在分配内存时调用它。

### 3.2 触发点二：按需存在的定时器

```c
/* src/evict.c:448-473 */
/* The evictionTimeProc is started when "maxmemory" has been breached and
 * could not immediately be resolved.  This will spin the event loop with short
 * eviction cycles until the "maxmemory" condition has resolved or there are no
 * more evictable items.  */
static int isEvictionProcRunning = 0;
static int evictionTimeProc(
        struct aeEventLoop *eventLoop, long long id, void *clientData) {
    UNUSED(eventLoop);
    UNUSED(id);
    UNUSED(clientData);

    if (performEvictions() == EVICT_RUNNING) return 0;  /* keep evicting */

    /* For EVICT_OK - things are good, no need to keep evicting.
     * For EVICT_FAIL - there is nothing left to evict.  */
    isEvictionProcRunning = 0;
    return AE_NOMORE;
}

void startEvictionTimeProc(void) {
    if (!isEvictionProcRunning) {
        isEvictionProcRunning = 1;
        aeCreateTimeEvent(server.el, 0,
                evictionTimeProc, NULL, NULL);
    }
}
```

- **它是什么。** 一个挂在事件循环上的时间事件，回调里就一件事：再调一次 `performEvictions()`（`src/evict.c:459`）。【注释】`src/evict.c:448-451`："This will spin the event loop with short eviction cycles until the … condition has resolved or there are no more evictable items."
- **什么时候挂上。** 两处：主循环用完时间预算时（`src/evict.c:778` 的 `startEvictionTimeProc()`），以及 CONFIG SET 把 maxmemory 调到比当前用量还低时（`src/config.c:3431` 的 `startEvictionTimeProc()`）。
- **为什么不会挂两个。** `src/evict.c:468-469` 用静态变量 `isEvictionProcRunning` 当开关，已经在跑就不再创建。
- **多久触发一次。** `src/evict.c:470-471` 的 `aeCreateTimeEvent(server.el, 0, evictionTimeProc, NULL, NULL)` 延迟是 0 毫秒。回调返回 0 时，事件循环按 `src/ae.c:339-340` 的 `te->when = now + (monotime)retval * 1000` 重新排到「现在」，也就是每转一圈事件循环就跑一小轮淘汰，中间穿插着处理客户端命令。
- **什么时候停。** 只要返回值不是 EVICT_RUNNING 就把开关清零并返回 `AE_NOMORE`（`src/evict.c:463-464`）。【注释】`src/evict.c:461-462`："For EVICT_OK - things are good, no need to keep evicting. For EVICT_FAIL - there is nothing left to evict."

**为什么 CONFIG SET maxmemory 不当场淘汰，而只是挂定时器。**【提交 `2e1979a21`】说得很直白："This would mean that the effects of … may not be visible once the command returns. That could anyway happen since incremental eviction was added in redis 6.2 (see #7653)"，以及动机 "We do this to fix one of the propagation bugs about eviction see #9890 and #10014."。也就是：在一条命令（CONFIG SET）执行的中途去删 key 并传播 DEL，会把传播顺序搞乱；反正增量淘汰之后「返回时不一定删完」本来就成立，干脆统一交给定时器。

**这两件事最好对照着看：** 定时器是否在跑（`isEvictionProcRunning`）不只是调度状态，它还直接决定 `performEvictions()` 的返回值（`src/evict.c:787`）。所以「定时器在跑」就等于「对外宣称还在努力，写命令先放行」。


## 4. 第 2 层：performEvictions() 的骨架

**结论。** 这个函数是「闸 → 算账 → 循环（选、删、看表）→ 三种出口」。先看作者自己写的函数头注释，它把设计目标说得很清楚：

```c
/* src/evict.c:519-546 */
/* Check that memory usage is within the current "maxmemory" limit.  If over
 * "maxmemory", attempt to free memory by evicting data (if it's safe to do so).
 *
 * It's possible for Redis to suddenly be significantly over the "maxmemory"
 * setting.  This can happen if there is a large allocation (like a hash table
 * resize) or even if the "maxmemory" setting is manually adjusted.  Because of
 * this, it's important to evict for a managed period of time - otherwise Redis
 * would become unresponsive while evicting.
 *
 * The goal of this function is to improve the memory situation - not to
 * immediately resolve it.  In the case that some items have been evicted but
 * the "maxmemory" limit has not been achieved, an aeTimeProc will be started
 * which will continue to evict items until memory limits are achieved or
 * nothing more is evictable.
 *
 * This should be called before execution of commands.  If EVICT_FAIL is
 * returned, commands which will result in increased memory usage should be
 * rejected.
 *
 * Returns:
 *   EVICT_OK       - memory is OK or it's not possible to perform evictions now
 *   EVICT_RUNNING  - memory is over the limit, but eviction is still processing
 *   EVICT_FAIL     - memory is over the limit, and there's nothing to evict
 * */
int performEvictions(void) {
    /* Note, we don't goto update_metrics here because this check skips eviction
     * as if it wasn't triggered. it's a fake EVICT_OK. */
    if (!isSafeToPerformEvictions()) return EVICT_OK;
```

### 4.1 九个阶段

| 阶段 | 锚点 | 做什么 | 之后去哪 |
|---|---|---|---|
| A 安全闸 | `src/evict.c:546` | `isSafeToPerformEvictions()` 说不行就直接返回 EVICT_OK | 函数结束，连指标都不记 |
| B 算账 | `src/evict.c:556-559` | `getMaxmemoryState` 返回 C_OK 表示没超 | 跳到 `update_metrics`，返回 EVICT_OK |
| C 策略禁止 | `src/evict.c:561-564` | 策略是 `MAXMEMORY_NO_EVICTION` 就不删 | 跳到 `update_metrics`，返回 EVICT_FAIL |
| D 起表 | `src/evict.c:566-576` | 用 `evictionTimeLimitUs()` 算出本轮时间预算，启动计时，并断言 `server.also_propagate.numops == 0` | 进主循环 |
| E 主循环 | `src/evict.c:579` | 条件是 `mem_freed < (long long)mem_tofree`，每圈选一个 key、删掉、记账 | 见下面三种离开方式 |
| F 正常出口 | `src/evict.c:787` | 按 `isEvictionProcRunning` 决定返回 EVICT_RUNNING 还是 EVICT_OK | 落入 G |
| G 无物可删 | `src/evict.c:789-806` | 标签 `cant_free`，只有 result 仍是 EVICT_FAIL 才真正做事：等一等后台释放线程 | 落入 H |
| H BLESS 宽限 | `src/evict.c:813-818` | 如果是被保护的 key 挡住了（`blessed_blocked`），在 1.25 倍以内放行 | 落入 I |
| I 记指标 | `src/evict.c:823-833` | 标签 `update_metrics`，累计「超限持续了多久」 | 返回 result |

**主循环有三种离开方式，决定了返回值：**

1. **删够了。** 循环条件不再成立，落到 `src/evict.c:787`，由 `isEvictionProcRunning` 决定返回值。
2. **中途 break。** 两处：后台释放已经让内存达标（`src/evict.c:767-771` 的 `server.lazyfree_lazy_eviction` 分支），或时间预算用完（`src/evict.c:776-779` 的 `eviction_time_limit_us`）。同样落到第 787 行。
3. **选不出 key。** `src/evict.c:782-784` 的 `goto cant_free` 直接跳过第 787 行，所以 result 保持初值。

**这里读者最容易困惑：result 的初值就是失败。** `src/evict.c:553` 写的是 `int result = EVICT_FAIL;`。也就是说「失败」不是某处显式赋的，而是「没有任何一条成功路径改写过它」的默认结果。跳到 `cant_free` 的那条路正是靠这个初值表达「没东西可删」。

**这里读者也容易困惑：cant_free 这个标签，正常路径也会流过。** 标签不是函数，第 787 行执行完会自然往下走进 `src/evict.c:789-790`，只是 `if (result == EVICT_FAIL)` 这个条件不成立，里面的等待不会执行。名字像是「失败专用」，其实是所有进过主循环的路径的公共收尾。

**为什么进循环前要断言传播队列为空。**【注释】`src/evict.c:575-576`："Try to smoke-out bugs (server.also_propagate should be empty here)"。【提交 `8ad0cfa56`】解释了为什么是断言而不是一个 if："Keep that condition may hide bugs (i.e. performEvictions should always expect to have an empty server.also_propagate)"。也就是：淘汰只应该发生在「两条命令之间」，如果此时队列里还有上一条命令没发完的东西，说明别处有 bug，宁可崩也不要悄悄绕过。

### 4.2 三个返回值，以及「还在删」时为什么放行写命令

| 返回值 | 定义 | 注释原话 | 命令入口怎么处理 | 定时器怎么处理 |
|---|---|---|---|---|
| `EVICT_OK` | `src/server.h:4757` | 【注释】`src/evict.c:539`："memory is OK or it's not possible to perform evictions now" | 照常执行命令 | 停表 |
| `EVICT_RUNNING` | `src/server.h:4758` | 【注释】`src/evict.c:540`："memory is over the limit, but eviction is still processing" | 照常执行命令 | 返回 0，下一圈事件循环再来 |
| `EVICT_FAIL` | `src/server.h:4759` | 【注释】`src/evict.c:541`："memory is over the limit, and there's nothing to evict" | 带 DENYOOM 标志的命令回 OOM 错误 | 停表 |

**三点要对照着看：**

- **EVICT_OK 有两种完全不同的含义。** 一种是真的没超；另一种是「现在不允许淘汰」（阶段 A）。【注释】`src/evict.c:544-545` 自己就说第二种是 "it's a fake EVICT_OK."。为什么不返回失败，见 5.1。
- **EVICT_RUNNING 时写命令是放行的。** 代码事实：命令入口只判断 `performEvictions() == EVICT_FAIL`（`src/server.c:4949`）。契约写在【注释】`src/evict.c:534-536`："If EVICT_FAIL is returned, commands which will result in increased memory usage should be rejected."，没有提 RUNNING。
- **为什么敢放行。**【注释】`src/evict.c:528-532`："The goal of this function is to improve the memory situation - not to immediately resolve it."。【提交 `810e28a39`】（引入增量淘汰的那次提交）承认了代价："There is a risk that some other edge cases (like massive pipelined use of MGET) could cause Redis memory usage to keep growing despite the eviction attempts, so a new maxmemory-eviction-tenacity config is introduced to let users mitigate that."。【推断】放行而不是拒绝，是因为「时间到了但其实还有 key 可删」不等于「没内存了」，这时拒绝写入会把一次延迟优化变成一批业务报错；依据是上面两段原话都把目标定为「改善而不是立刻解决」，但没有哪句话直接说「RUNNING 时必须放行」。

**为什么由 performEvictions() 的返回值说了算，而不是命令入口自己比一下内存。**【提交 `f6b32c14f`】（2012 年，那时函数还叫 freeMemoryIfNeeded）："To say if we can accept or not write queries is up to the return value of freeMemoryIfNeeded(), that has full control about that."。原因是记账规则（扣掉哪些缓冲）只有这个函数知道，入口自己比会得出不一样的结论。


## 5. 第 3 层：逐段下钻

### 5.1 安全闸：什么时候干脆不淘汰

**结论。** 有五种情况即使超限也不删 key，而且对外假装没事（返回 EVICT_OK）。

```c
/* src/evict.c:479-499 */
static int isSafeToPerformEvictions(void) {
    /* - There must be no script in timeout condition.
     * - Nor we are loading data right now.  */
    if (isInsideYieldingLongCommand() || server.loading) return 0;

    /* By default replicas should ignore maxmemory
     * and just be masters exact copies. */
    if (server.masterhost && server.repl_slave_ignore_maxmemory) return 0;

    /* Disable eviction during slot migration import to avoid delays and errors
     * caused by failed evictions. A special client is created for data import,
     * identified by the CLIENT_MASTER and CLIENT_ASM_IMPORTING flags. */
    if (server.current_client && server.current_client->flags & CLIENT_MASTER &&
        server.current_client->flags & CLIENT_ASM_IMPORTING)
        return 0;

    /* If 'evict' action is paused, for whatever reason, then return false */
    if (isPausedActionsWithUpdate(PAUSE_ACTION_EVICT)) return 0;

    return 1;
}
```

| 条件 | 锚点 | 为什么 |
|---|---|---|
| 正在一个超时的长命令里重入事件循环 | `src/evict.c:482` 的 `isInsideYieldingLongCommand()` | 【注释】`src/evict.c:480-481`："There must be no script in timeout condition."【提交 `b2061de2e`】列的改动项里有 "2. Avoid eviction during yield command processing." |
| 正在加载数据 | `src/evict.c:482` 的 `server.loading` | 【提交 `b9cd89d10`】："if we are loading AOF it's dangerous, because processCommand would call freeMemoryIfNeeded to evict, and that will break data consistency, see issue #5686." |
| 本机是副本，且配置为忽略 maxmemory | `src/evict.c:486` 的 `server.repl_slave_ignore_maxmemory` | 【注释】`src/evict.c:484-485`："By default replicas should ignore maxmemory and just be masters exact copies." |
| 当前客户端是 slot 迁移的导入通道 | `src/evict.c:491-493` 的 `CLIENT_ASM_IMPORTING` | 【注释】`src/evict.c:488-490`："Disable eviction during slot migration import to avoid delays and errors caused by failed evictions." |
| 淘汰动作被暂停（例如 CLIENT PAUSE、故障切换期间） | `src/evict.c:495-496` 的 `PAUSE_ACTION_EVICT` | 【提交 `a3e53cf9b`】的标题是 "Prevent expirations and evictions while paused"，正文只指向一个我读不到的 issue，理由见第 9 节 |

**逐条补充：**

- **副本为什么不自己淘汰。**【文档】`redis.conf:1321-1324`："It means that the eviction of keys will be just handled by the master, sending the DEL commands to the replica as keys evict in the master side."；【文档】`redis.conf:1326-1330`："This behavior ensures that masters and replicas stay consistent, and is usually what you want"。默认值是开（`src/config.c:4228` 的 `replica-ignore-maxmemory`，默认 1）。【提交 `067647a78`】说明这是一次有意的不兼容变更："Note: this breaks backward compatibility with Redis 4, since now slaves by default are exact copies of masters and do not try to evict keys independently."
- **长命令这个条件看起来重复。** 命令入口 `src/server.c:4948` 已经判过 `!isInsideYieldingLongCommand()`，这里（`src/evict.c:482`）又判一次 `isInsideYieldingLongCommand()`。【推断】不是冗余：`performEvictions()` 还有第二个调用者（定时器回调，`src/evict.c:459`），那条路不经过命令入口，所以闸必须装在函数内部；入口处那一次只是提前短路。依据是两个调用点的位置，没有注释直接这么说。
- **为什么不安全时返回 OK 而不是 FAIL。**【提交 `c9097393b`】："It looks safer to return C_OK from freeMemoryIfNeeded() when clients are paused because returning C_ERR may prevent success of writes. It is possible that there is no difference in practice since clients cannot execute writes while clients are paused, but it looks more correct this way, at least conceptually."。这条说的是暂停那一种；【推断】其余几种同理：返回 FAIL 的唯一后果是拒绝写命令，而在加载 AOF、或副本回放主库命令时拒绝写入会直接破坏数据一致性。
- **为什么这条路连指标都不记。**【注释】`src/evict.c:544-545`："we don't goto update_metrics here because this check skips eviction as if it wasn't triggered."

### 5.2 算账：到底超了多少

**结论。** 「已用内存」不是直接拿分配器的数字，而是先减掉一笔「不该算在数据头上」的缓冲，再和上限比。差额 `mem_tofree` 就是这一轮要腾出的字节数。

```c
/* src/evict.c:395-431 */
int getMaxmemoryState(size_t *total, size_t *logical, size_t *tofree, float *level) {
    size_t mem_reported, mem_used, mem_tofree;

    /* Check if we are over the memory usage limit. If we are not, no need
     * to subtract the slaves output buffers. We can just return ASAP. */
    mem_reported = zmalloc_used_memory();
    if (total) *total = mem_reported;

    /* We may return ASAP if there is no need to compute the level. */
    if (!server.maxmemory) {
        if (level) *level = 0;
        return C_OK;
    }
    if (mem_reported <= server.maxmemory && !level) return C_OK;

    /* Remove the size of slaves output buffers and AOF buffer from the
     * count of used memory. */
    mem_used = mem_reported;
    size_t overhead = freeMemoryGetNotCountedMemory();
    mem_used = (mem_used > overhead) ? mem_used-overhead : 0;

    /* Compute the ratio of memory usage. */
    if (level) *level = (float)mem_used / (float)server.maxmemory;

    if (mem_reported <= server.maxmemory) return C_OK;

    /* Check if we are still over the memory limit. */
    if (mem_used <= server.maxmemory) return C_OK;

    /* Compute how much memory we need to free. */
    mem_tofree = mem_used - server.maxmemory;

    if (logical) *logical = mem_used;
    if (tofree) *tofree = mem_tofree;

    return C_ERR;
}
```

- **原始数字。** `src/evict.c:400` 取 `zmalloc_used_memory()`，这是分配器统计的已分配字节数。【推断】它不是操作系统看到的进程占用（RSS），所以内存碎片不在这笔账里；依据是这个函数名和它定义在分配器封装层（`src/zmalloc.c:658` 的 `zmalloc_used_memory`），我没有展开读它的实现。
- **没设上限就直接走。** `src/evict.c:404-407` 在 `!server.maxmemory` 时返回 C_OK。【提交 `f0fab99d6`】："when server.maxmemory is not set, don't count AOF and replicas buffers."，也就是把这个判断提到了「算缓冲」之前。
- **快路径。** `src/evict.c:408` 的 `mem_reported <= server.maxmemory && !level`：连原始数字都没超，就不用再算要扣多少。【注释】`src/evict.c:398-399`："If we are not, no need to subtract the slaves output buffers. We can just return ASAP."。这是每条命令都要走的路径，所以值得省。
- **扣掉不计入的部分。** `src/evict.c:413` 调 `freeMemoryGetNotCountedMemory()`，`src/evict.c:414` 写的是 `mem_used = (mem_used > overhead) ? mem_used-overhead : 0;`。【推断】三目运算是防无符号下溢：两个量都是 `size_t`，直接相减在缓冲比总量还大的瞬间会变成一个天文数字。
- **再比一次。** `src/evict.c:422` 的 `mem_used <= server.maxmemory`：扣完以后不超也算没事。
- **目标就是刚好回到线上。** `src/evict.c:425` 写的是 `mem_tofree = mem_used - server.maxmemory;`。没有「多删一点留余量」的低水位，这一点在第 8 节有讨论。

**那笔「不计入」的内存是什么：**

```c
/* src/evict.c:321-369 */
/* We don't want to count AOF buffers and slaves output buffers as
 * used memory: the eviction should use mostly data size, because
 * it can cause feedback-loop when we push DELs into them, putting
 * more and more DELs will make them bigger, if we count them, we
 * need to evict more keys, and then generate more DELs, maybe cause
 * massive eviction loop, even all keys are evicted.
 *
 * This function returns the sum of AOF and replication buffer. */
size_t freeMemoryGetNotCountedMemory(void) {
    size_t overhead = 0;

    /* Since all replicas and replication backlog share global replication
     * buffer, we think only the part of exceeding backlog size is the extra
     * separate consumption of replicas.
     *
     * Note that although the backlog is also initially incrementally grown
     * (pushing DELs consumes memory), it'll eventually stop growing and
     * remain constant in size, so even if its creation will cause some
     * eviction, it's capped, and also here to stay (no resonance effect)
     *
     * Note that, because we trim backlog incrementally in the background,
     * backlog size may exceeds our setting if slow replicas that reference
     * vast replication buffer blocks disconnect. To avoid massive eviction
     * loop, we don't count the delayed freed replication backlog into used
     * memory even if there are no replicas, i.e. we still regard this memory
     * as replicas'. */
    if ((long long)server.repl_buffer_mem > server.repl_backlog_size) {
        /* We use list structure to manage replication buffer blocks, so backlog
         * also occupies some extra memory, we can't know exact blocks numbers,
         * we only get approximate size according to per block size. */
        size_t extra_approx_size =
            (server.repl_backlog_size/PROTO_REPLY_CHUNK_BYTES + 1) *
            (sizeof(replBufBlock)+sizeof(listNode));
        size_t counted_mem = server.repl_backlog_size + extra_approx_size;
        if (server.repl_buffer_mem > counted_mem) {
            overhead += (server.repl_buffer_mem - counted_mem);
        }
    }

    /* The migrate client is like a replica, we also push DELs into it when
     * evicting keys belonging to the migrating slot, so we don't count its
     * output buffer to avoid eviction loop. */
    overhead += asmGetMigrateOutputMemoryUsage();

    if (server.aof_state != AOF_OFF) {
        overhead += sdsAllocSize(server.aof_buf);
    }
    return overhead;
}
```

一共三项：

1. **复制缓冲里超出 backlog 大小的部分。** `src/evict.c:347` 的条件是 `server.repl_buffer_mem > server.repl_backlog_size`，`src/evict.c:355-356` 把超出 `counted_mem` 的部分加进 `overhead`。
2. **slot 迁移的输出缓冲。** `src/evict.c:360-363` 的 `asmGetMigrateOutputMemoryUsage()`。
3. **AOF 缓冲。** `src/evict.c:365-367`，条件是 `server.aof_state != AOF_OFF`，取的是 `sdsAllocSize(server.aof_buf)`。

**为什么要扣：防止正反馈把库删空。** 这是整套机制里最重要的一个「为什么」，三处证据互相印证：

- 【注释】`src/evict.c:321-328`："it can cause feedback-loop when we push DELs into them, putting more and more DELs will make them bigger, if we count them, we need to evict more keys, and then generate more DELs, maybe cause massive eviction loop, even all keys are evicted."
- 【文档】`redis.conf:1250-1255`："network problems / resyncs will not trigger a loop where keys are evicted, and in turn the output buffer of replicas is full with DELs of keys evicted triggering the deletion of more keys, and so forth until the database is completely emptied."
- 【测试】`tests/unit/maxmemory.tcl:422` 有一条用例就叫 "replica buffer don't induce eviction"。

这条规则很老。【提交 `f6b32c14f`】（2012 年）就已经这样做，并且坦白是试出来的："This three changes are the result of many tests, I found (partially empirically) that is the best way to address the problem, but maybe we'll find better solutions in the future."

**为什么 backlog 以内的部分照算、以外的才扣。**【注释】`src/evict.c:332-334`："we think only the part of exceeding backlog size is the extra separate consumption of replicas."；【注释】`src/evict.c:336-339` 解释 backlog 本身为什么可以算进去："it'll eventually stop growing and remain constant in size, so even if its creation will cause some eviction, it's capped, and also here to stay (no resonance effect)"。【提交 `c1718f9d8`】是把复制缓冲改成全局共享的那次提交，原话："Since all replicas and replication backlog share global replication buffer, we think only the part of exceeding backlog size the extra separate consumption of replicas."

**细节一：为什么还要加一个近似值。** `src/evict.c:351-353` 的 `extra_approx_size` 把链表节点和块头的开销也估进「该算」的那一侧。【注释】`src/evict.c:348-350`："we can't know exact blocks numbers, we only get approximate size according to per block size."

**细节二：没有副本时也扣。**【注释】`src/evict.c:341-346`："To avoid massive eviction loop, we don't count the delayed freed replication backlog into used memory even if there are no replicas"。背景是 backlog 是后台增量修剪的，慢副本断开后它会短暂超出设定大小。

**细节三：AOF 缓冲为什么取已分配大小而不是长度。**【提交 `29e488cbb`】只有一句标题："Overhead is the allocated size of the AOF buffer, not its length"。意思是 sds 字符串会预留空间，真正占内存的是分配出去的那一整块。

**运维上要知道的副作用。** 被扣掉的缓冲是真实占着内存的。【文档】`redis.conf:1257-1259`："if you have replicas attached it is suggested that you set a lower limit for maxmemory so that there is some free RAM on the system for replica output buffers"。这笔被扣掉的量可以在 INFO 里看到：`src/server.c:6965` 的 `mem_not_counted_for_evict`。


### 5.3 选谁：策略、打分原料、候选池

这是最长的一节，分五步：策略怎么编码 → 打分的原料存在哪 → 候选池 → 主循环怎么用池 → 随机策略。

#### 5.3.1 策略是一组位标志

```c
/* src/server.h:738-757 */
/* Redis maxmemory strategies. Instead of using just incremental number
 * for this defines, we use a set of flags so that testing for certain
 * properties common to multiple policies is faster. */
#define MAXMEMORY_FLAG_LRU (1<<0)
#define MAXMEMORY_FLAG_LFU (1<<1)
#define MAXMEMORY_FLAG_ALLKEYS (1<<2)
#define MAXMEMORY_FLAG_LRM (1<<3)
#define MAXMEMORY_FLAG_NO_SHARED_INTEGERS \
    (MAXMEMORY_FLAG_LRU|MAXMEMORY_FLAG_LFU|MAXMEMORY_FLAG_LRM)

#define MAXMEMORY_VOLATILE_LRU ((0<<8)|MAXMEMORY_FLAG_LRU)
#define MAXMEMORY_VOLATILE_LFU ((1<<8)|MAXMEMORY_FLAG_LFU)
#define MAXMEMORY_VOLATILE_TTL (2<<8)
#define MAXMEMORY_VOLATILE_RANDOM (3<<8)
#define MAXMEMORY_ALLKEYS_LRU ((4<<8)|MAXMEMORY_FLAG_LRU|MAXMEMORY_FLAG_ALLKEYS)
#define MAXMEMORY_ALLKEYS_LFU ((5<<8)|MAXMEMORY_FLAG_LFU|MAXMEMORY_FLAG_ALLKEYS)
#define MAXMEMORY_ALLKEYS_RANDOM ((6<<8)|MAXMEMORY_FLAG_ALLKEYS)
#define MAXMEMORY_NO_EVICTION (7<<8)
#define MAXMEMORY_VOLATILE_LRM ((8<<8)|MAXMEMORY_FLAG_LRM)
#define MAXMEMORY_ALLKEYS_LRM ((9<<8)|MAXMEMORY_FLAG_LRM|MAXMEMORY_FLAG_ALLKEYS)
```

**怎么读这十个策略。** 两个维度相乘，再加一个「不淘汰」：

| 维度 | 取值 | 代码里怎么判断 |
|---|---|---|
| 从哪个集合里选 | allkeys：所有 key，volatile：只在设了过期时间的 key 里选 | `src/evict.c:603-607`，带 `MAXMEMORY_FLAG_ALLKEYS` 就用 `db->keys`，否则用 `db->expires` |
| 按什么打分 | LRU 最久没访问，LFU 访问最少，LRM 最久没修改，TTL 最快过期（只有 volatile），random 不打分 | `src/evict.c:587-588`，带 `MAXMEMORY_FLAG_LRU`、LFU、LRM 标志或等于 `MAXMEMORY_VOLATILE_TTL` 的走候选池，其余走随机 |
| 不淘汰 | noeviction，也是默认值 | `src/evict.c:561-564` 的 `MAXMEMORY_NO_EVICTION` |

**为什么不用 0、1、2 这样的连续编号。**【注释】`src/server.h:738-740`："we use a set of flags so that testing for certain properties common to multiple policies is faster."。主循环里每删一个 key 都要问「这个策略是不是 allkeys」，一次按位与就够了。

#### 5.3.2 打分的原料：每个对象头里的 24 位

```c
/* src/object.h:104-116 */
struct redisObject {
    unsigned type:4;
    unsigned encoding:5;
    unsigned refcount : OBJ_REFCOUNT_BITS;
    unsigned iskvobj : 1;   /* 1 if this struct serves as a kvobj base */

    /* metabits and lru are Relevant only when iskvobj is set: */
    unsigned metabits :8;  /* Bitmap of metadata (+expiry) attached to this kvobj */
    unsigned lru:LRU_BITS; /* LRU time (relative to global lru_clock) or
                            * LFU data (least significant 8 bits frequency
                            * and most significant 16 bits access time). */
    void *ptr;
};
```

**结论。** 每个 key 的对象头里有一个 24 位的位域 `lru:LRU_BITS`（`src/object.h:112`，宽度定义在 `src/object.h:94` 的 `LRU_BITS`）。LRU、LRM 策略下它存一个时间戳，LFU 策略下它被拆成「16 位时间 + 8 位计数」。Redis 不为淘汰维护任何额外的全局结构（没有 LRU 链表、没有堆），打分的全部原料就是这 24 位。

**为什么 LFU 要挤在同一个字段里。**【注释】`src/evict.c:241-243`："We have 24 total bits of space in each object in order to implement an LFU (Least Frequently Used) eviction policy, since we re-use the LRU field for this purpose."

**这 24 位在三个时机被写入：**

1. **key 写入数据库时。** `src/object.c:124-135` 的 `initObjectLRUOrLFU`：LFU 策略写「当前分钟 + 初始计数 `LFU_INIT_VAL`」，其他策略写 `LRU_CLOCK()`。
2. **key 被查到时。** 见下面这段。
3. **key 被修改时（只对 LRM 有意义）。** `src/db.c:1248` 的 `updateLRM` 由 `keyModified` 调用。

```c
/* src/db.c:332-347 */
    if (val) {
        /* Update the access time for the ageing algorithm.
         * Don't do it if we have a saving child, as this will trigger
         * a copy on write madness. */
        if (((flags & LOOKUP_NOTOUCH) == 0) &&
            (server.current_client && server.current_client->flags & CLIENT_NO_TOUCH) &&
            (server.executing_client && server.executing_client->cmd->proc != touchCommand))
            flags |= LOOKUP_NOTOUCH;
        if (!hasActiveChildProcess() && !(flags & LOOKUP_NOTOUCH)){
            if (server.maxmemory_policy & MAXMEMORY_FLAG_LFU) {
                updateLFU(val);
            } else if (!(server.maxmemory_policy & MAXMEMORY_FLAG_LRM)) {
                /* LRM policy should NOT update timestamp on reads. */
                val->lru = LRU_CLOCK();
            }
        }
```

- **有子进程在做快照时不更新。** `src/db.c:340` 的条件里有 `!hasActiveChildProcess()`。【注释】`src/db.c:333-335`："Don't do it if we have a saving child, as this will trigger a copy on write madness."。意思是：fork 之后父子进程共享内存页，只要改一个对象头里的 24 位，整页都要被复制一份。副作用是快照期间访问热度不被记录。
- **LRM 策略下读不算数。** `src/db.c:343-345` 的 else 分支要求 `!(server.maxmemory_policy & MAXMEMORY_FLAG_LRM)` 才写时间戳。【提交 `0cb1ee0dc`】："LRM (Least Recently Modified) is similar to LRU but only updates the timestamp on write operations, not read operations."【测试】`tests/unit/maxmemory.tcl:656` 的用例名："LRM: Keys with only read operations should be removed first"。

```c
/* src/db.c:52-68 */
/* Update LFU when an object is accessed.
 * Firstly, decrement the counter if the decrement time is reached.
 * Then logarithmically increment the counter, and update the access time. */
void updateLFU(robj *val) {
    unsigned long counter = LFUDecrAndReturn(val);
    counter = LFULogIncr(counter);
    val->lru = (LFUGetTimeInMinutes()<<8) | counter;
}

/* Update LRM when an object is modified. */
void updateLRM(robj *o) {
    if (o->refcount == OBJ_SHARED_REFCOUNT)
        return;
    if (server.maxmemory_policy & MAXMEMORY_FLAG_LRM) {
        o->lru = LRU_CLOCK();
    }
}
```

**LRU 时钟：秒级、24 位、会回绕。**

```c
/* src/evict.c:60-88 */
unsigned int getLRUClock(void) {
    return (mstime()/LRU_CLOCK_RESOLUTION) & LRU_CLOCK_MAX;
}

/* This function is used to obtain the current LRU clock.
 * If the current resolution is lower than the frequency we refresh the
 * LRU clock (as it should be in production servers) we return the
 * precomputed value, otherwise we need to resort to a system call. */
unsigned int LRU_CLOCK(void) {
    unsigned int lruclock;
    if (1000/server.hz <= LRU_CLOCK_RESOLUTION) {
        lruclock = server.lruclock;
    } else {
        lruclock = getLRUClock();
    }
    return lruclock;
}

/* Given an object returns the min number of milliseconds the object was never
 * requested, using an approximated LRU algorithm. */
unsigned long long estimateObjectIdleTime(robj *o) {
    unsigned long long lruclock = LRU_CLOCK();
    if (lruclock >= o->lru) {
        return (lruclock - o->lru) * LRU_CLOCK_RESOLUTION;
    } else {
        return (lruclock + (LRU_CLOCK_MAX - o->lru)) *
                    LRU_CLOCK_RESOLUTION;
    }
}
```

- **单位和位宽。** `src/evict.c:61` 是 `(mstime()/LRU_CLOCK_RESOLUTION) & LRU_CLOCK_MAX`：毫秒时间除以 1000（`src/object.h:96` 的 `LRU_CLOCK_RESOLUTION`）再截到 24 位（`src/object.h:95` 的 `LRU_CLOCK_MAX`）。我按这个公式算：2 的 24 次方秒约等于 194 天，时钟就回绕一圈。
- **为什么访问 key 时不取系统时间。** `src/evict.c:70-71` 在 `1000/server.hz <= LRU_CLOCK_RESOLUTION` 时直接用缓存值 `server.lruclock`，它由周期任务刷新（`src/server.c:1674` 的 `server.lruclock = getLRUClock();`）。【注释】`src/evict.c:64-67`："we return the precomputed value, otherwise we need to resort to a system call."。这是每次读 key 都要走的热路径，省一次系统调用。
- **回绕怎么处理。** `src/evict.c:78-88` 的 `estimateObjectIdleTime`：当前时钟比对象里存的小，就按「绕了一圈」算。【注释】`src/server.c:1663-1674` 承认这不精确："Note that even if the counter wraps it's not a big problem, everything will still work but some object will appear younger to Redis."

**LFU：16 位「上次衰减时间」+ 8 位对数计数器。**

```c
/* src/evict.c:276-319 */
unsigned long LFUGetTimeInMinutes(void) {
    return (server.unixtime/60) & 65535;
}

/* Given an object ldt (last access time), compute the minimum number of minutes
 * that elapsed since the last access. Handle overflow (ldt greater than
 * the current 16 bits minutes time) considering the time as wrapping
 * exactly once. */
unsigned long LFUTimeElapsed(unsigned long ldt) {
    unsigned long now = LFUGetTimeInMinutes();
    if (now >= ldt) return now-ldt;
    return 65535-ldt+now;
}

/* Logarithmically increment a counter. The greater is the current counter value
 * the less likely is that it gets really incremented. Saturate it at 255. */
uint8_t LFULogIncr(uint8_t counter) {
    if (counter == 255) return 255;
    double r = (double)rand()/RAND_MAX;
    double baseval = counter - LFU_INIT_VAL;
    if (baseval < 0) baseval = 0;
    double p = 1.0/(baseval*server.lfu_log_factor+1);
    if (r < p) counter++;
    return counter;
}

/* If the object's ldt (last access time) is reached, decrement the LFU counter but
 * do not update LFU fields of the object, we update the access time
 * and counter in an explicit way when the object is really accessed.
 * And we will decrement the counter according to the times of
 * elapsed time than server.lfu_decay_time.
 * Return the object frequency counter.
 *
 * This function is used in order to scan the dataset for the best object
 * to fit: as we check for the candidate, we incrementally decrement the
 * counter of the scanned objects if needed. */
unsigned long LFUDecrAndReturn(robj *o) {
    unsigned long ldt = o->lru >> 8;
    unsigned long counter = o->lru & 255;
    unsigned long num_periods = server.lfu_decay_time ? LFUTimeElapsed(ldt) / server.lfu_decay_time : 0;
    if (num_periods)
        counter = (num_periods > counter) ? 0 : counter - num_periods;
    return counter;
}
```

- **8 位怎么数到上百万次访问。** `src/evict.c:292-300` 的 `LFULogIncr`：计数越大，加一的概率越小（`src/evict.c:297` 的 `1.0/(baseval*server.lfu_log_factor+1)`）。【文档】`redis.conf:2732-2739`："The LFU counter is just 8 bits per key, it's maximum value is 255, so Redis uses a probabilistic increment with logarithmic behavior."。配置文件里给了一张表（`redis.conf:2741-2755`，其中有 `factor` 一列），默认因子 10 时大约一百万次访问才数到 255。
- **新 key 为什么从 5 起步。**【注释】`src/evict.c:262-263`："New keys don't start at zero, in order to have the ability to collect some accesses before being trashed away, so they start at LFU_INIT_VAL."。数值定义在 `src/server.h:4753` 的 `LFU_INIT_VAL`。
- **为什么要衰减。**【注释】`src/evict.c:252-255`："what used to be a frequently accessed key in the past, will remain ranked like that forever, while we want the algorithm to adapt to access pattern changes."。做法在 `src/evict.c:315-317`：每过 `server.lfu_decay_time` 分钟，计数减 1。
- **为什么衰减是减 1 而不是减半。**【提交 `06ca9d683`】："Splitting the popularity in half actually just needs decrementing the counter because the counter is logarithmic."
- **16 位分钟也会回绕。** `src/evict.c:286-287` 的 `LFUTimeElapsed` 处理了 `now >= ldt` 不成立的情况。【注释】`src/evict.c:257-260`："we take 16 bits of the time converted in minutes since we don't care about wrapping around"。我按位宽算：65536 分钟约 45.5 天。
- **这里读者最容易困惑：`LFUDecrAndReturn` 只算不写。** 名字像是「衰减并返回」，但 `src/evict.c:312-319` 里没有任何对 `o->lru` 的赋值，它只是返回「如果现在衰减，计数应该是多少」。真正写回在访问路径上（`src/db.c:58` 的 `val->lru = (LFUGetTimeInMinutes()<<8) | counter;`）。【提交 `583c31472`】："it should only try to get decremented counter, not update LFU fields, we will update it in an explicit way."。所以淘汰打分时调用它是没有副作用的。

#### 5.3.3 候选池：16 个格子，跨调用保留

```c
/* src/evict.c:36-51 */
#define EVPOOL_SIZE 16
#define EVPOOL_CACHED_SDS_SIZE 255
#define EVICTION_MAX_BLESSED_ONLY_ROUNDS 8
/* When eviction can't free memory because blessed (NO-EVICT) keys hold it,
 * tolerate used memory up to 1.25x maxmemory. There is no separate hard
 * ceiling, so this is the ceiling. */
#define EVICTION_OVERUSE_DIVISOR 4
struct evictionPoolEntry {
    unsigned long long idle;    /* Object idle time (inverse frequency for LFU) */
    sds key;                    /* Key name. */
    sds cached;                 /* Cached SDS object for key name. */
    int dbid;                   /* Key DB number. */
    int slot;                   /* Slot. */
};

static struct evictionPoolEntry *EvictionPoolLRU;
```

**结论。** 池是一个全局的、按分数升序排列的 16 元素数组（`src/evict.c:36` 的 `EVPOOL_SIZE`），启动时分配一次（`src/server.c:3199` 的 `evictionPoolAlloc()`），之后永不释放。每次抽样把新候选按分数插进去，取的时候从最右边（分数最高，最该删）拿。

**为什么要有池，而不是「抽 5 个，删其中最差的」。**【提交 `22c9cfaf5`】（2014 年引入池的提交）："we use an eviction pool that is persistent across evictions of keys, and gets populated with the best candidates for evictions found so far."，以及效果："It allows to approximate LRU eviction at a given number of samples better than the previous algorithm used."。也就是：上一轮抽到但没删的好候选不浪费，等价于用同样的抽样成本看了更多的 key。【提交 `6416ab19d`】后来把 LFU 也接进池里时说："Verified to have better real world performances with power-law access patterns because of the data accumulated across calls."

**字段名 idle 是历史遗留。**【注释】`src/evict.c:160-162`："This is called idle just because the code initially handled LRU, but is in fact just a score where a higher score means better candidate."。所以读代码时把 `idle` 一律读成「淘汰分」。

**每个格子为什么带一个 cached 缓冲。**【提交 `e64bf05f4`】："To destroy and recreate the pool[].key element is slow, so we allocate in pool[].cached SDS strings that can account up to 255 chars keys and try to reuse them. This provides a solid 20% performance improvement in real world workload alike benchmarks."。对应 `src/evict.c:37` 的 `EVPOOL_CACHED_SDS_SIZE`。

**抽样与打分：**

```c
/* src/evict.c:139-179 */
int evictionPoolPopulate(redisDb *db, kvstore *samplekvs, struct evictionPoolEntry *pool, int *candidates) {
    int j, k, count;
    dictEntry *samples[server.maxmemory_samples];

    *candidates = 0;

    /* Don't retry, since we will call evictionPoolPopulate multiple times if needed. */
    int slot = kvstoreGetFairRandomDictIndex(samplekvs, randomEvictionShouldSkipDictIndex, 1, 0);
    if (slot == -1) return 0;
    count = kvstoreDictGetSomeKeys(samplekvs,slot,samples,server.maxmemory_samples);
    for (j = 0; j < count; j++) {
        unsigned long long idle;

        dictEntry *de = samples[j];
        kvobj *kv = dictGetKV(de);
        sds key = kvobjGetKey(kv);

        /* Blessed NO-EVICT keys are never eviction candidates. */
        if (blessIsNoEvict(kv)) continue;
        (*candidates)++;

        /* Calculate the idle time according to the policy. This is called
         * idle just because the code initially handled LRU, but is in fact
         * just a score where a higher score means better candidate. */
        if (server.maxmemory_policy & (MAXMEMORY_FLAG_LRU|MAXMEMORY_FLAG_LRM)) {
            idle = estimateObjectIdleTime(kv);
        } else if (server.maxmemory_policy & MAXMEMORY_FLAG_LFU) {
            /* When we use an LRU policy, we sort the keys by idle time
             * so that we expire keys starting from greater idle time.
             * However when the policy is an LFU one, we have a frequency
             * estimation, and we want to evict keys with lower frequency
             * first. So inside the pool we put objects using the inverted
             * frequency subtracting the actual frequency to the maximum
             * frequency of 255. */
            idle = 255-LFUDecrAndReturn(kv);
        } else if (server.maxmemory_policy == MAXMEMORY_VOLATILE_TTL) {
            /* In this case the sooner the expire the better. */
            idle = ULLONG_MAX - kvobjGetExpire(kv);
        } else {
            serverPanic("Unknown eviction policy in evictionPoolPopulate()");
        }
```

- **先公平地挑一个 slot。** `src/evict.c:146` 调 `kvstoreGetFairRandomDictIndex`。数据库是按 slot 分片的一组哈希表，「公平」指按 key 数加权。【注释】`src/kvstore.c:400-417`："probability of each dict being returned is proportional to the number of elements that dictionary holds."。非集群模式只有一个哈希表，这一步直接返回 0（`src/kvstore.c:421-422` 的 `kvs->num_dicts == 1`）。
- **跳过正在导入的 slot。** 回调 `randomEvictionShouldSkipDictIndex` 定义在 `src/evict.c:90-94`。【注释】`src/evict.c:90-91`："keys that are being imported are in an intermediate state. We cannot evict them and therefore skip them."
- **再抓一批 key。** `src/evict.c:148` 的 `kvstoreDictGetSomeKeys` 最终落到 `src/dict.c:1347` 的 `dictGetSomeKeys`。【注释】`src/dict.c:1325-1346` 说明它为了快而放弃了质量保证："It does not guarantee to return all the keys specified in 'count', nor it does guarantee to return non-duplicated elements"。这对淘汰够用：抽样本来就是近似。
- **受保护的 key 不算候选。** `src/evict.c:156-158`：`blessIsNoEvict(kv)` 为真就跳过，不计入 `*candidates`。
- **打分。** 三种算法，统一成「越大越该删」：

| 策略 | 分数 | 锚点 |
|---|---|---|
| LRU、LRM | 空闲毫秒数 `estimateObjectIdleTime(kv)` | `src/evict.c:163-164` |
| LFU | `255-LFUDecrAndReturn(kv)`，访问越少分越高 | `src/evict.c:173` |
| volatile-ttl | `ULLONG_MAX - kvobjGetExpire(kv)`，过期越早分越高 | `src/evict.c:174-176` |

**为什么要统一成一个分数。**【提交 `2d5eb1f1a`】（把 volatile-ttl 也接进池的提交）："Precision of the eviction improved sensibly. Also this allows us to have a single code path for most eviction types."

**插入池：**

```c
/* src/evict.c:181-235 */
        /* Insert the element inside the pool.
         * First, find the first empty bucket or the first populated
         * bucket that has an idle time smaller than our idle time. */
        k = 0;
        while (k < EVPOOL_SIZE &&
               pool[k].key &&
               pool[k].idle < idle) k++;
        if (k == 0 && pool[EVPOOL_SIZE-1].key != NULL) {
            /* Can't insert if the element is < the worst element we have
             * and there are no empty buckets. */
            continue;
        } else if (k < EVPOOL_SIZE && pool[k].key == NULL) {
            /* Inserting into empty position. No setup needed before insert. */
        } else {
            /* Inserting in the middle. Now k points to the first element
             * greater than the element to insert.  */
            if (pool[EVPOOL_SIZE-1].key == NULL) {
                /* Free space on the right? Insert at k shifting
                 * all the elements from k to end to the right. */

                /* Save SDS before overwriting. */
                sds cached = pool[EVPOOL_SIZE-1].cached;
                memmove(pool+k+1,pool+k,
                    sizeof(pool[0])*(EVPOOL_SIZE-k-1));
                pool[k].cached = cached;
            } else {
                /* No free space on right? Insert at k-1 */
                k--;
                /* Shift all elements on the left of k (included) to the
                 * left, so we discard the element with smaller idle time. */
                sds cached = pool[0].cached; /* Save SDS before overwriting. */
                if (pool[0].key != pool[0].cached) sdsfree(pool[0].key);
                memmove(pool,pool+1,sizeof(pool[0])*k);
                pool[k].cached = cached;
            }
        }

        /* Try to reuse the cached SDS string allocated in the pool entry,
         * because allocating and deallocating this object is costly
         * (according to the profiler, not my fantasy. Remember:
         * premature optimization bla bla bla. */
        int klen = sdslen(key);
        if (klen > EVPOOL_CACHED_SDS_SIZE) {
            pool[k].key = sdsdup(key);
        } else {
            memcpy(pool[k].cached,key,klen+1);
            sdssetlen(pool[k].cached,klen);
            pool[k].key = pool[k].cached;
        }
        pool[k].idle = idle;
        pool[k].dbid = db->id;
        pool[k].slot = slot;
    }

    return count;
```

- **找位置。** `src/evict.c:184-187` 从左往右找第一个分数不小于自己的格子 `k`（条件 `pool[k].idle < idle`）。
- **比池里所有人都差，且池满了：丢弃。** `src/evict.c:188-191` 的 `k == 0 && pool[EVPOOL_SIZE-1].key != NULL`。
- **位置是空的：直接放。** `src/evict.c:192-193`，条件里有 `pool[k].key == NULL`。
- **右边还有空位：从 k 起整体右移一格。** `src/evict.c:197-205` 的 `memmove`。
- **池满了：丢掉最左边（分数最低）的，左半段左移一格。** `src/evict.c:206-215`，其中 `src/evict.c:212` 的 `if (pool[0].key != pool[0].cached) sdsfree(pool[0].key);` 释放被挤出去的长 key。
- **这里读者最容易困惑：memmove 前后为什么要把 cached 指针存起来再放回去。** `src/evict.c:202` 先存 `pool[EVPOOL_SIZE-1].cached`，`src/evict.c:205` 再写回 `pool[k].cached`。【推断】memmove 是把整个结构体连同 `cached` 指针一起搬走的，搬完以后末尾那个格子的缓冲没人指着，而第 k 格和第 k+1 格指着同一块缓冲；存一下再放回，是为了保持「16 块缓冲，每块恰好属于一个格子」。依据是这几行代码本身，注释只说了复用缓冲是为了性能（`src/evict.c:218-221`，原话 "because allocating and deallocating this object is costly"）。
- **key 怎么存。** `src/evict.c:222-229`：长度超过 `EVPOOL_CACHED_SDS_SIZE` 的用 `sdsdup` 复制一份，否则拷进自带的缓冲。池里存的是 key 名字的副本，不是指向数据库的指针，所以原 key 被删了池也不会悬空，只会变成「幽灵」（见下一小节）。

#### 5.3.4 主循环怎么用池

```c
/* src/evict.c:579-634 */
    while (mem_freed < (long long)mem_tofree) {
        int j, k, i;
        static unsigned int next_db = 0;
        sds bestkey = NULL;
        int bestdbid;
        redisDb *db;
        dictEntry *de;

        if (server.maxmemory_policy & (MAXMEMORY_FLAG_LRU|MAXMEMORY_FLAG_LFU|MAXMEMORY_FLAG_LRM) ||
            server.maxmemory_policy == MAXMEMORY_VOLATILE_TTL)
        {
            struct evictionPoolEntry *pool = EvictionPoolLRU;
            int blessed_only_rounds = 0;
            while (bestkey == NULL) {
                unsigned long total_keys = 0;
                unsigned long total_sampled_keys = 0;
                unsigned long total_candidates = 0;

                /* We don't want to make local-db choices when expiring keys,
                 * so to start populate the eviction pool sampling keys from
                 * every DB. */
                for (i = 0; i < server.dbnum; i++) {
                    db = server.db+i;
                    kvstore *kvs;
                    if (server.maxmemory_policy & MAXMEMORY_FLAG_ALLKEYS) {
                        kvs = db->keys;
                    } else {
                        kvs = db->expires;
                    }
                    unsigned long sampled_keys = 0;
                    unsigned long current_db_keys = kvstoreSize(kvs);
                    if (current_db_keys == 0) continue;

                    total_keys += current_db_keys;
                    int l = kvstoreNumNonEmptyDicts(kvs);
                    /* Do not exceed the number of non-empty slots when looping. */
                    while (l--) {
                        int candidates = 0;
                        int sampled = evictionPoolPopulate(db, kvs, pool, &candidates);
                        /* Count only evictable (non-blessed) keys toward the budget,
                         * so slots full of NO-EVICT keys don't starve the sampling
                         * and stop us from reaching slots that still have victims. */
                        sampled_keys += candidates;
                        total_sampled_keys += sampled;
                        total_candidates += candidates;
                        /* We have sampled enough evictable keys in the current db. */
                        if (sampled_keys >= (unsigned long) server.maxmemory_samples)
                            break;
                        /* If there are not a lot of keys in the current db, dict/s may be very
                         * sparsely populated, exit the loop without meeting the sampling
                         * requirement. */
                        if (current_db_keys < (unsigned long) server.maxmemory_samples*10)
                            break;
                    }
                }
                if (!total_keys) break; /* No keys to evict. */
```

**先灌池。**

- **每个 DB 都抽。** `src/evict.c:600` 的 `for (i = 0; i < server.dbnum; i++)` 遍历所有库。【注释】`src/evict.c:597-599`："We don't want to make local-db choices when expiring keys"。【提交 `e423f76e7`】讲了原来的问题："However this means that there could be DBs with very frequently accessed keys that are targeted by the LRU algorithm while there were other DBs with many better candidates to expire."。所以池是全局一个，格子里要记 `dbid`。
- **每个 DB 抽几次。** `src/evict.c:613-615`：最多循环「非空哈希表个数」次（`kvstoreNumNonEmptyDicts`），每次抽一个随机 slot。【提交 `9ee1cc33a`】："We'll never try to sample the db dicts more times than the number of non-empty dicts in the db (max 1 in non-cluster mode)."
- **两个提前结束条件。** 抽够了（`src/evict.c:625-626` 的 `sampled_keys >= (unsigned long) server.maxmemory_samples`）；或者这个库本来就没多少 key（`src/evict.c:627-631` 的 `current_db_keys < (unsigned long) server.maxmemory_samples*10`）。后者的理由见【注释】`src/evict.c:627-629`："dict/s may be very sparsely populated"，也就是再抽也抽不满，别空转。
- **预算只算可淘汰的 key。** `src/evict.c:621` 的 `sampled_keys += candidates;` 加的是候选数而不是抽样数。【注释】`src/evict.c:618-620`："so slots full of NO-EVICT keys don't starve the sampling and stop us from reaching slots that still have victims."

```c
/* src/evict.c:636-692 */
                /* Go backward from best to worst element to evict. */
                for (k = EVPOOL_SIZE-1; k >= 0; k--) {
                    if (pool[k].key == NULL) continue;
                    bestdbid = pool[k].dbid;

                    kvstore *kvs;
                    if (server.maxmemory_policy & MAXMEMORY_FLAG_ALLKEYS) {
                        kvs = server.db[bestdbid].keys;
                    } else {
                        kvs = server.db[bestdbid].expires;
                    }
                    de = kvstoreDictFind(kvs, pool[k].slot, pool[k].key);

                    /* Remove the entry from the pool. */
                    if (pool[k].key != pool[k].cached)
                        sdsfree(pool[k].key);
                    pool[k].key = NULL;
                    pool[k].idle = 0;

                    /* If the key exists, is our pick. Otherwise it is
                     * a ghost and we need to try the next element. */
                    if (de) {
                        kvobj *kv = dictGetKV(de);
                        /* The pool persists across performEvictions() calls, so a
                         * key pooled while unblessed may since have been marked
                         * NO-EVICT. The entry is already removed from the pool
                         * above; skip it as a target and try the next one. */
                        if (blessIsNoEvict(kv)) continue;
                        bestkey = kvobjGetKey(kv);
                        break;
                    } else {
                        /* Ghost... Iterate again. */
                    }
                }
                if (bestkey) break;

                /* No victim was pooled. This check moved below the pool scan (it
                 * used to sit right after sampling) so we can tell two cases apart,
                 * in order:
                 *   1. sampled nothing at all -> no keys to evict, stop here.
                 *   2. sampled keys but all were blessed -> retry (handled by the
                 *      total_candidates branch below), don't stop.
                 * The old early break sat before that split and would stop on an
                 * all-blessed sample even when unblessed keys still remain. */
                if (!total_sampled_keys) break;

                if (!total_candidates) {
                    /* Sampled keys, but every one was blessed (NO-EVICT). */
                    if (++blessed_only_rounds >= EVICTION_MAX_BLESSED_ONLY_ROUNDS) {
                        blessed_blocked = 1;
                        break;
                    }
                    continue;
                }
                blessed_only_rounds = 0;
            }
        }
```

**再从池里取。**

- **从右往左。** `src/evict.c:636-637`，【注释】原话 "Go backward from best to worst element to evict."
- **拿名字回数据库里再查一次。** `src/evict.c:647` 的 `kvstoreDictFind(kvs, pool[k].slot, pool[k].key)`。
- **不管查没查到，这个格子都清掉。** `src/evict.c:649-653`，把 `pool[k].key` 置 NULL。
- **查不到就是「幽灵」，换下一个。**【注释】`src/evict.c:655-656`："If the key exists, is our pick. Otherwise it is a ghost and we need to try the next element."。幽灵的来源见【注释】`src/evict.c:108-109`："we don't remove keys from the pool when they are deleted so the pool may contain keys that no longer exist."
- **看起来像漏了：key 被正常 DEL 时为什么不去清池。**【推断】因为那样每次删 key 都要扫一遍池，把成本摊到了所有写路径上；现在的做法是把校验推迟到「真要用它」的那一刻，一次哈希查找就解决。依据是上面那句注释只陈述了「不清」这个事实，没有给理由。
- **volatile 策略下，回查用的是 expires 表。** `src/evict.c:641-646` 按 `MAXMEMORY_FLAG_ALLKEYS` 选表。【推断】这顺带完成了资格复核：一个 key 入池以后如果被去掉了过期时间，它就不在 `expires` 里了，会被当成幽灵跳过。
- **入池以后才被保护的 key。** `src/evict.c:663` 再查一次 `blessIsNoEvict(kv)`。【注释】`src/evict.c:659-662`："The pool persists across performEvictions() calls, so a key pooled while unblessed may since have been marked NO-EVICT."【测试】`tests/unit/bless.tcl:383`："Blessing a key already in the LRU eviction pool still protects it"。所以它和抽样时那一次检查（`src/evict.c:156-158` 的 `blessIsNoEvict`）并不重复：一次管入池，一次管出池。

**池里一个能用的都没有怎么办。** 外层是 `while (bestkey == NULL)`（`src/evict.c:592`），会重新灌池再取。三种结束方式：

1. 所有库加起来一个 key 都没有：`src/evict.c:634` 的 `if (!total_keys) break;`。
2. 一个都没抽到：`src/evict.c:680` 的 `if (!total_sampled_keys) break;`。
3. 抽到了但全是受保护的 key，连续 8 轮：`src/evict.c:682-689`，置 `blessed_blocked = 1` 后退出。8 这个数来自 `src/evict.c:38` 的 `EVICTION_MAX_BLESSED_ONLY_ROUNDS`。

三种情况 `bestkey` 都是 NULL，接下来会走到 `goto cant_free`。

**会不会死循环。**【推断】不会。只要某一轮抽到了可淘汰的候选却没选出 key，说明池里扫到的全是幽灵或受保护的 key，而扫过的格子都被清空了，下一轮新候选一定能插进去并被选中。依据是 `src/evict.c:649-653` 无条件清格子（`pool[k].key = NULL`）；我没有找到注释或测试专门论证终止性。

#### 5.3.5 随机策略：不经过池

```c
/* src/evict.c:694-735 */
        /* volatile-random and allkeys-random policy */
        else if (server.maxmemory_policy == MAXMEMORY_ALLKEYS_RANDOM ||
                 server.maxmemory_policy == MAXMEMORY_VOLATILE_RANDOM)
        {
            /* When evicting a random key, we try to evict a key for
             * each DB, so we use the static 'next_db' variable to
             * incrementally visit all DBs. */
            int blessed_only_rounds = 0;
            while (bestkey == NULL) {
                unsigned long total_sampled_keys = 0;
                for (i = 0; i < server.dbnum; i++) {
                    j = (++next_db) % server.dbnum;
                    db = server.db+j;
                    kvstore *kvs;
                    if (server.maxmemory_policy == MAXMEMORY_ALLKEYS_RANDOM) {
                        kvs = db->keys;
                    } else {
                        kvs = db->expires;
                    }
                    int slot = kvstoreGetFairRandomDictIndex(kvs, randomEvictionShouldSkipDictIndex, 16, 0);
                    if (slot == -1) continue;
                    /* Try a few random keys so blessed NOEVICT keys are skipped. */
                    for (int attempts = 0; attempts < server.maxmemory_samples; attempts++) {
                        de = kvstoreDictGetRandomKey(kvs, slot);
                        if (de == NULL) break;
                        total_sampled_keys++;
                        kvobj *kv = dictGetKV(de);
                        if (blessIsNoEvict(kv)) continue;
                        bestkey = kvobjGetKey(kv);
                        bestdbid = j;
                        break;
                    }
                    if (bestkey) break;
                }
                if (bestkey || !total_sampled_keys) break;
                /* Sampled keys, but every one was blessed (NO-EVICT). */
                if (++blessed_only_rounds >= EVICTION_MAX_BLESSED_ONLY_ROUNDS) {
                    blessed_blocked = 1;
                    break;
                }
            }
        }
```

- **轮转访问各个库。** `src/evict.c:705` 的 `j = (++next_db) % server.dbnum;`，其中 `next_db` 是函数内的静态变量（`src/evict.c:581`）。【注释】`src/evict.c:698-700`："we try to evict a key for each DB, so we use the static 'next_db' variable to incrementally visit all DBs."
- **选表方式和池路径不同。** 这里比较的是 `server.maxmemory_policy == MAXMEMORY_ALLKEYS_RANDOM`（`src/evict.c:708-712`），因为走到这个分支的只剩两个随机策略。
- **随机取一个，碰到受保护的就再试。** `src/evict.c:716-717`：最多试 `server.maxmemory_samples` 次 `kvstoreDictGetRandomKey`。【注释】`src/evict.c:715`："Try a few random keys so blessed NOEVICT keys are skipped."
- **结束条件和池路径对称。** `src/evict.c:728` 的 `if (bestkey || !total_sampled_keys) break;`，以及 `src/evict.c:730-733` 的 8 轮上限（`EVICTION_MAX_BLESSED_ONLY_ROUNDS`）。

**这两件事最好对照着看：** 池路径在 `src/evict.c:146` 调 `kvstoreGetFairRandomDictIndex` 时倒数第二个参数是 1，随机路径在 `src/evict.c:713` 传的是 16。前者有【注释】`src/evict.c:145` 解释："Don't retry, since we will call evictionPoolPopulate multiple times if needed."；后者为什么是 16，我没查到（见第 9 节）。

#### 5.3.6 BLESS：永不淘汰的 key

这是很新的功能（【提交 `b9800e06b`】，标题里叫 "protect keys from eviction"），对淘汰路径的影响有五处，前面已经分别碰到，这里汇总：

| 位置 | 锚点 | 作用 |
|---|---|---|
| 保护位 | `src/object.h:130` 的 `no_evict` | 紧跟在对象头后面的一个位 |
| 读保护位 | `src/bless.c:99-102` 的 `blessIsNoEvict` | 淘汰路径只读这个位，不查任何索引 |
| 抽样时跳过 | `src/evict.c:156-158` 的 `blessIsNoEvict` | 不进池 |
| 出池时复核 | `src/evict.c:663` 的 `blessIsNoEvict` | 入池后才被保护的也不删 |
| 全是受保护 key 时的宽限 | `src/evict.c:813-818` 的 `blessed_blocked` | 见 5.7 |

【提交 `b9800e06b`】对语义的一句话概括："A blessed key is never chosen as an eviction victim."【注释】`src/bless.c:16-17` 说明为什么淘汰路径这么轻："The eviction path never consults it - blessIsNoEvict() reads the bit directly from kvBits."


### 5.4 删：一个 key 是怎么被删掉并通知出去的

**结论。** 选出 `bestkey` 以后，主循环用一个「执行单元」把「删除、通知、传播」包起来，删除前后各量一次内存算出释放量，然后马上把这一个 key 的 DEL（或 UNLINK）发出去。

```c
/* src/evict.c:737-751 */
        /* Finally remove the selected key. */
        if (bestkey) {
            long long key_mem_freed;
            db = server.db+bestdbid;

            enterExecutionUnit(1, 0);
            robj *keyobj = createStringObject(bestkey,sdslen(bestkey));
            deleteEvictedKeyAndPropagate(db, keyobj, &key_mem_freed);
            decrRefCount(keyobj);
            exitExecutionUnit();
            /* Propagate the DEL command */
            postExecutionUnitOperations();

            mem_freed += key_mem_freed;
            keys_freed++;
```

- **执行单元。** `src/evict.c:742` 调 `enterExecutionUnit(1, 0)`，`src/evict.c:746` 调 `exitExecutionUnit()`，`src/evict.c:747-748` 调 `postExecutionUnitOperations()`。【注释】`src/server.c:4117-4119`："Execution unit is a code that should be done atomically."。平时一条命令就是一个执行单元；淘汰不在任何命令里，所以要自己包一层。
- **为什么要包。**【提交 `0ffb9d2ea`】："This PR fixes the issue by wrapping each expiration/eviction of a key with an execution unit. This makes sure the entire operation will run atomically and all the post notification jobs will be executed at the end where it is safe to write."。也就是：删除会触发通知，模块可能在通知里注册「稍后要做的写操作」，这些必须等这一次删除完整结束后再跑。
- **为什么每删一个 key 就收尾一次，而不是攒一批。**【注释】`src/db.c:3054-3058`："the caller must remember to call postExecutionUnitOperations, preferably just after a single deletion batch, so that DEL/UNLINK will NOT be wrapped in MULTI/EXEC"。机制在 `src/server.c:4076-4077`：只有 `server.also_propagate.numops > 1` 时才会把待传播的命令包成事务。一个 key 一次收尾，队列里就只有一条 DEL，不会被包。【测试】`tests/unit/maxmemory.tcl:531` 的用例 "propagation with eviction" 核对的正是副本收到的是一条条独立的删除命令。
- **为什么要把 key 名复制成一个新对象。** `src/evict.c:743` 的 `createStringObject(bestkey,sdslen(bestkey))`。【推断】两个原因：下游函数的参数类型是对象而不是裸字符串；更重要的是，`bestkey` 指向的字符串属于即将被删的那个 key（或属于候选池的格子），而通知和传播发生在删除之后（见下面的顺序），那时还要用到 key 名。依据是下面 `deleteKeyAndPropagate` 里「先删、后通知、再传播」的顺序，没有注释直接这么说。

**真正干活的函数（淘汰和过期共用）：**

```c
/* src/db.c:2980-3028 */
static void deleteKeyAndPropagate(redisDb *db, robj *keyobj, int notify_type, long long *key_mem_freed) {
    mstime_t latency;
    int del_flag = notify_type == NOTIFY_EXPIRED ? DB_FLAG_KEY_EXPIRED : DB_FLAG_KEY_EVICTED;
    int lazy_flag = notify_type == NOTIFY_EXPIRED ? server.lazyfree_lazy_expire : server.lazyfree_lazy_eviction;
    char *latency_name = notify_type == NOTIFY_EXPIRED ? "expire-del" : "evict-del";
    char *notify_name = notify_type == NOTIFY_EXPIRED ? "expired" : "evicted";

    /* The key needs to be converted from static to heap before deleted */
    int static_key = keyobj->refcount == OBJ_STATIC_REFCOUNT;
    if (static_key) {
        keyobj = createStringObject(keyobj->ptr, sdslen(keyobj->ptr));
    }

    serverLog(LL_DEBUG,"key %s %s: deleting it", redactLogCstr((char*)keyobj->ptr), notify_type == NOTIFY_EXPIRED ? "expired" : "evicted");

    /* We compute the amount of memory freed by db*Delete() alone.
     * It is possible that actually the memory needed to propagate
     * the DEL in AOF and replication link is greater than the one
     * we are freeing removing the key, but we can't account for
     * that otherwise we would never exit the loop.
     *
     * Same for CSC invalidation messages generated by keyModified.
     *
     * AOF and Output buffer memory will be freed eventually so
     * we only care about memory used by the key space.
     *
     * The code here used to first propagate and then record delta
     * using only zmalloc_used_memory but in CRDT we can't do that
     * so we use freeMemoryGetNotCountedMemory to avoid counting
     * AOF and slave buffers */
    if (key_mem_freed) *key_mem_freed = (long long) zmalloc_used_memory() - freeMemoryGetNotCountedMemory();
    latencyStartMonitor(latency);
    dbGenericDelete(db, keyobj, lazy_flag, del_flag);
    latencyEndMonitor(latency);
    latencyAddSampleIfNeeded(latency_name, latency);
    if (key_mem_freed) *key_mem_freed -= (long long) zmalloc_used_memory() - freeMemoryGetNotCountedMemory();

    notifyKeyspaceEvent(notify_type, notify_name,keyobj, db->id);
    keyModified(NULL, db, keyobj, NULL, 1);
    propagateDeletion(db, keyobj, lazy_flag);

    if (notify_type == NOTIFY_EXPIRED)
        server.stat_expiredkeys++;
    else
        server.stat_evictedkeys++;

    if (static_key)
        decrRefCount(keyobj);
}
```

- **淘汰和过期只差几个名字。** `src/db.c:2983` 的 `lazy_flag` 在淘汰时取 `server.lazyfree_lazy_eviction`；`src/db.c:2984-2985` 定下延迟事件名 `evict-del` 和通知名 `evicted`。入口 `src/db.c:3036-3038` 的 `deleteEvictedKeyAndPropagate` 只是带上 `NOTIFY_EVICTED` 转调。
- **释放量怎么量。** `src/db.c:3010` 在删之前记下 `zmalloc_used_memory() - freeMemoryGetNotCountedMemory()`，`src/db.c:3015` 在删之后再算一次相减（`*key_mem_freed -=`）。夹在中间的只有 `src/db.c:3012` 的 `dbGenericDelete`。
- **为什么只量「删」这一步，不把通知和传播也算进去。**【注释】`src/db.c:2995-3004`："It is possible that actually the memory needed to propagate the DEL in AOF and replication link is greater than the one we are freeing removing the key, but we can't account for that otherwise we would never exit the loop."。同一段注释还说 "AOF and Output buffer memory will be freed eventually so we only care about memory used by the key space."。【提交 `c4fdf09c0`】是把客户端缓存失效也挪出测量区间的那次修复："The section that measures the memory impact of the eviction should be just on dbDelete, excluding keyspace notification, client tracking, and propagation to AOF and replicas."
- **释放量可能是负数。** `src/evict.c:550` 的声明是 `long long mem_freed; /* May be negative */`。【提交 `37fd3d40a`】给了两个来源："mem_freed can be negative when: 1. We use lazy free (consuming memory by appending to a list) 2. Thread doing an allocation between the two calls to zmalloc_used_memory."。后果也写了："If 'delta' is negative 'mem_freed' may underflow and cause the while loop to exit prematurely (And not evicting enough memory)"。所以主循环条件里才有那个到 `long long` 的强制转换（`src/evict.c:579` 的 `mem_freed < (long long)mem_tofree`）。
- **顺序：先通知，再让客户端缓存失效，最后传播。** `src/db.c:3017` 的 `notifyKeyspaceEvent`，`src/db.c:3018` 的 `keyModified`，`src/db.c:3019` 的 `propagateDeletion`。【提交 `7ac213079`】："3. minor: in eviction, call propagateDeletion after notification, like active-expire and all commands (we always send a notification before propagating the command)"。
- **计数。** `src/db.c:3024` 的 `server.stat_evictedkeys++`，对应 INFO 里的 `evicted_keys`（`src/server.c:7165`）。

**传播出去的是什么。** `src/db.c:3062` 的 `argv[0] = lazy ? shared.unlink : shared.del;`：开了后台释放就传 UNLINK，否则传 DEL。`src/db.c:3069` 用 `alsoPropagateForced` 同时发往 AOF 和副本。

- **为什么是「强制」传播。**【注释】`src/db.c:3067-3068`："If the master decided to delete a key we must propagate it to replicas no matter what."
- **为什么把淘汰当成一条普通删除命令发出去，而不是让副本自己判断。**【注释】`src/db.c:3045-3048`："This way the key deletion is centralized in one place, and since both AOF and the replication link guarantee operation ordering, everything will be consistent even if we allow write operations against deleted keys."。这和 5.1 里「副本默认不自己淘汰」是同一个设计的两面：删不删由主库一家说了算。

**这里读者最容易困惑：效果不在这一行。** 第 3019 行只是把命令放进待传播队列，真正写进 AOF 缓冲和复制缓冲是在回到 `src/evict.c:747-748` 的 `postExecutionUnitOperations()` 时。而副本真正收到，要等缓冲被写到网络上（见 5.5 的第一件事）。

**往下一层：从哈希表里摘掉。**

```c
/* src/db.c:907-911,929-955 */
int dbGenericDelete(redisDb *db, robj *key, int async, int flags) {
    dictEntryLink link;
    int table;
    int slot = getKeySlot(key->ptr);
    link = kvstoreDictTwoPhaseUnlinkFind(db->keys, slot, key->ptr, &table);
...
        incrRefCount(kv); /* refcnt=1->2 */
        if (blessIsNoEvict(kv)) blessSetNoEvict(db, kv, 0);
        /* Metadata hook: notify unlink for key metadata cleanup. */
        if (getModuleMetaBits(kv->metabits)) keyMetaOnUnlink(db, key, kv);
        /* Tells the module that the key has been unlinked from the database. */
        moduleNotifyKeyUnlink(key, kv, db->id, flags);
        /* We want to try to unblock any module clients or clients using a blocking XREADGROUP */
        signalDeletedKeyAsReady(db,key,type);
        /* We should call decr before freeObjAsync. If not, the refcount may be
         * greater than 1, so freeObjAsync doesn't work */
        decrRefCount(kv);

        /* Because of dbUnshareStringValue, the val in db may change. */
        kv = dictGetKV(*link);

        /* if expirable, delete an entry from the expires dict is not decrRefCount of kvobj */
        if (kvobjGetExpire(kv) != -1)
            kvstoreDictDelete(db->expires, slot, key->ptr);

        if (async) {
            if (server.memory_tracking_enabled)
                updateSlotAllocSize(db, slot, kv, kvobjAllocSize(kv), -1);
            freeObjAsync(key, kv, db->id);
            /* Set the key to NULL in the main dictionary. */
            kvstoreDictSetAtLink(db->keys, slot, NULL, &link, 0);
        }
        kvstoreDictTwoPhaseUnlinkFree(db->keys, slot, link, table);
```

- **过期表里的条目一起删。** `src/db.c:945-946` 的 `kvstoreDictDelete(db->expires, slot, key->ptr)`。
- **同步还是异步，只差一个分支。** `src/db.c:948-954`：`async` 为真时调 `freeObjAsync(key, kv, db->id)`，再用 `kvstoreDictSetAtLink` 把哈希表条目里的指针置空；最后 `src/db.c:955` 的 `kvstoreDictTwoPhaseUnlinkFree` 统一摘除条目。【推断】置空是为了让最后这一步不要把已经交给后台的对象再释放一次；注释只写了 "Set the key to NULL in the main dictionary."（`src/db.c:952`），没有讲目的。
- **第 930 行与淘汰无关。** `src/db.c:930` 的 `blessIsNoEvict(kv)` 是给普通删除清保护位用的；被保护的 key 根本不会被淘汰路径选中（见 5.3.6）。

**异步释放的门槛：**

```c
/* src/lazyfree.c:213-225 */
void freeObjAsync(robj *key, robj *obj, int dbid) {
    size_t free_effort = lazyfreeGetFreeEffort(key,obj,dbid);
    /* Note that if the object is shared, to reclaim it now it is not
     * possible. This rarely happens, however sometimes the implementation
     * of parts of the Redis core may call incrRefCount() to protect
     * objects, and then call dbDelete(). */
    if (free_effort > LAZYFREE_THRESHOLD && obj->refcount == 1) {
        atomicIncr(lazyfree_objects,1);
        bioCreateLazyFreeJob(lazyfreeFreeObject,1,obj);
    } else {
        decrRefCount(obj);
    }
}
```

- **不是开了开关就一定走后台。** `src/lazyfree.c:219` 的条件是 `free_effort > LAZYFREE_THRESHOLD && obj->refcount == 1`，满足才 `bioCreateLazyFreeJob`（`src/lazyfree.c:221`）；否则 `src/lazyfree.c:223` 的 `decrRefCount(obj)` 就在主线程当场释放。
- **为什么要有门槛。**【注释】`src/lazyfree.c:126-130`："If the value is composed of a few allocations, to free in a lazy way is actually just slower... So under a certain limit we just free the object synchronously."。门槛值定义在 `src/lazyfree.c:131` 的 `LAZYFREE_THRESHOLD`，为什么是 64 没查到（见第 9 节）。
- **默认是关的。** `redis.conf:1402` 写的是 `lazyfree-lazy-eviction no`，代码默认值在 `src/config.c:4209` 的 `lazyfree-lazy-eviction`。【文档】`redis.conf:1397-1400`："In all the above cases the default is to delete objects in a blocking way, like if DEL was called."。为什么默认关，没查到（见第 9 节）。

**这两件事最好对照着看：** 开了后台释放以后，`dbGenericDelete` 返回时大对象的内存还没还给分配器，所以上面量出来的释放量会偏小。主循环如果只信这个数，就会多删。5.5 的第二件事就是为了补这个洞。

### 5.5 每删 16 个 key，停下来做三件事

```c
/* src/evict.c:753-785 */
            if (keys_freed % 16 == 0) {
                /* When the memory to free starts to be big enough, we may
                 * start spending so much time here that is impossible to
                 * deliver data to the replicas fast enough, so we force the
                 * transmission here inside the loop. */
                if (slaves) flushSlavesOutputBuffers();

                /* Normally our stop condition is the ability to release
                 * a fixed, pre-computed amount of memory. However when we
                 * are deleting objects in another thread, it's better to
                 * check, from time to time, if we already reached our target
                 * memory, since the "mem_freed" amount is computed only
                 * across the dbAsyncDelete() call, while the thread can
                 * release the memory all the time. */
                if (server.lazyfree_lazy_eviction) {
                    if (getMaxmemoryState(NULL,NULL,NULL,NULL) == C_OK) {
                        break;
                    }
                }

                /* After some time, exit the loop early - even if memory limit
                 * hasn't been reached.  If we suddenly need to free a lot of
                 * memory, don't want to spend too much time here.  */
                if (elapsedUs(evictionTimer) > eviction_time_limit_us) {
                    // We still need to free memory - start eviction timer proc
                    startEvictionTimeProc();
                    break;
                }
            }
        } else {
            goto cant_free; /* nothing to free... */
        }
    }
```

**结论。** 主循环不是每删一个 key 都看表，而是每 16 个（`src/evict.c:753` 的 `keys_freed % 16 == 0`）集中做三件「比较贵」的事。

1. **把复制缓冲强行写到网络上。** `src/evict.c:758` 的 `if (slaves) flushSlavesOutputBuffers();`，其中 `slaves` 是进函数时记下的副本数（`src/evict.c:552`）。【注释】`src/evict.c:754-757`："we may start spending so much time here that is impossible to deliver data to the replicas fast enough, so we force the transmission here inside the loop."。背景是：平时往副本的套接字写数据靠事件循环，而淘汰循环正占着主线程不回事件循环。【注释】`src/networking.c:5641-5644` 说这个函数就是为此而生："Helper function used by performEvictions() in order to flush slaves output buffers without returning control to the event loop."
2. **开了后台释放时，重新算一次总账。** `src/evict.c:767-771`：`server.lazyfree_lazy_eviction` 为真时调 `getMaxmemoryState`，已经达标就 `break`。【注释】`src/evict.c:760-766`："when we are deleting objects in another thread, it's better to check, from time to time, if we already reached our target memory, since the … amount is computed only across the dbAsyncDelete() call, while the thread can release the memory all the time."【提交 `cd90389b3`】："2. Every 10 keys evicted, check if memory usage already reached the target value directly, since we otherwise don't count all the memory reclaimed by the background thread right now."
3. **看表。** `src/evict.c:776-779`：`elapsedUs(evictionTimer) > eviction_time_limit_us` 就调 `startEvictionTimeProc()` 挂上定时器，然后 `break`。【注释】`src/evict.c:773-775`："After some time, exit the loop early - even if memory limit hasn't been reached."

**一个要如实说明的出入。** 上面 `cd90389b3` 的提交说明写的是每 10 个 key，而 HEAD 的代码是每 16 个。是哪次提交改的、为什么改，我没有追到（见第 9 节）。

**为什么是攒一批再做，而不是每个 key 都做。**【推断】这三件事各有成本：一次是往套接字写的系统调用，一次要重新统计缓冲大小，一次要读时钟；每个 key 都做会让「删一个小 key」的固定开销翻倍。依据只是代码结构，没有注释解释批量的理由。

**由此产生的一个边界行为。**【推断】看表只发生在「删满 16 个」的那一刻，所以哪怕时间预算是 0，一轮也会删 16 个 key 才停；反过来，如果某一个 key 是同步释放的巨型对象，这一轮会超出预算很多才有机会看表。依据是 `src/evict.c:776` 的 `elapsedUs(evictionTimer)` 判断整个嵌在 `src/evict.c:753` 的 `if (keys_freed % 16 == 0)` 里面。

### 5.6 时间预算怎么算

```c
/* src/evict.c:502-517 */
static unsigned long evictionTimeLimitUs(void) {
    serverAssert(server.maxmemory_eviction_tenacity >= 0);
    serverAssert(server.maxmemory_eviction_tenacity <= 100);

    if (server.maxmemory_eviction_tenacity <= 10) {
        /* A linear progression from 0..500us */
        return 50uL * server.maxmemory_eviction_tenacity;
    }

    if (server.maxmemory_eviction_tenacity < 100) {
        /* A 15% geometric progression, resulting in a limit of ~2 min at tenacity==99  */
        return (unsigned long)(500.0 * pow(1.15, server.maxmemory_eviction_tenacity - 10.0));
    }

    return ULONG_MAX;   /* No limit to eviction time */
}
```

**结论。** 预算由配置项 `maxmemory-eviction-tenacity`（0 到 100，默认 10，见 `src/config.c:4327`）换算成微秒，分三段：

| tenacity | 公式 | 锚点 |
|---|---|---|
| 0 到 10 | 线性，每档 50 微秒 | `src/evict.c:506-508` 的 `50uL * server.maxmemory_eviction_tenacity` |
| 11 到 99 | 每档乘 1.15 的几何增长 | `src/evict.c:511-513` 的 `pow(1.15, server.maxmemory_eviction_tenacity - 10.0)` |
| 100 | 不限时 | `src/evict.c:516` 的 `ULONG_MAX` |

下面这张表是**我按公式算的**，不是源码里的数字：

| tenacity | 每轮预算（约） |
|---|---|
| 0 | 0 微秒（但仍会删 16 个 key，见 5.5） |
| 5 | 250 微秒 |
| 10（默认） | 500 微秒 |
| 20 | 2 毫秒 |
| 30 | 8 毫秒 |
| 50 | 134 毫秒 |
| 75 | 4.4 秒 |
| 99 | 126 秒 |
| 100 | 无限 |

99 那一档和【注释】`src/evict.c:512` 的说法对得上："A 15% geometric progression, resulting in a limit of ~2 min at tenacity==99"。

**怎么理解这个旋钮。**【文档】`redis.conf:1313-1317`："If there is an unusually large amount of write traffic, this value may need to be increased. Decreasing this value may reduce latency at the risk of eviction processing effectiveness"，以及 "0 = minimum latency, 10 = default, 100 = process without regard to latency"。也就是在「单次卡顿」和「淘汰跟不跟得上写入」之间做取舍。

50 微秒的步长和 1.15 的底数为什么这么选，没查到（见第 9 节）。

### 5.7 出口：等一等、宽限、记指标

```c
/* src/evict.c:786-834 */
    /* at this point, the memory is OK, or we have reached the time limit */
    result = (isEvictionProcRunning) ? EVICT_RUNNING : EVICT_OK;

cant_free:
    if (result == EVICT_FAIL) {
        /* At this point, we have run out of evictable items.  It's possible
         * that some items are being freed in the lazyfree thread.  Perform a
         * short wait here if such jobs exist, but don't wait long.  */
        mstime_t lazyfree_latency;
        latencyStartMonitor(lazyfree_latency);
        while (bioPendingJobsOfType(BIO_LAZY_FREE) &&
              elapsedUs(evictionTimer) < eviction_time_limit_us) {
            if (getMaxmemoryState(NULL,NULL,NULL,NULL) == C_OK) {
                result = EVICT_OK;
                break;
            }
            usleep(eviction_time_limit_us < 1000 ? eviction_time_limit_us : 1000);
        }
        latencyEndMonitor(lazyfree_latency);
        latencyAddSampleIfNeeded("eviction-lazyfree",lazyfree_latency);
    }

    /* If we still couldn't free enough and blessed (NO-EVICT) keys exist, the
     * blessing is likely why - failing the write with OOM would punish the user
     * for a limit they deliberately raised. Tolerate a bounded overshoot: let the
     * command run (EVICT_OK) while used memory stays within maxmemory*factor; we
     * reclaim on later commands. Past the factor we still OOM (the hard ceiling). */
    if (result == EVICT_FAIL && blessed_blocked) {
        size_t mem_tofree;
        if (getMaxmemoryState(NULL, NULL, &mem_tofree, NULL) == C_OK ||
            mem_tofree <= server.maxmemory / EVICTION_OVERUSE_DIVISOR)
            result = EVICT_OK;
    }

    latencyEndMonitor(latency);
    latencyAddSampleIfNeeded("eviction-cycle",latency);

update_metrics:
    if (result == EVICT_RUNNING || result == EVICT_FAIL) {
        if (server.stat_last_eviction_exceeded_time == 0)
            elapsedStart(&server.stat_last_eviction_exceeded_time);
    } else if (result == EVICT_OK) {
        if (server.stat_last_eviction_exceeded_time != 0) {
            server.stat_total_eviction_exceeded_time += elapsedUs(server.stat_last_eviction_exceeded_time);
            server.stat_last_eviction_exceeded_time = 0;
        }
    }
    return result;
}
```

**出口一：正常收尾。** `src/evict.c:787` 的 `result = (isEvictionProcRunning) ? EVICT_RUNNING : EVICT_OK;`。定时器挂着就报「还在删」，否则报「好了」。

**出口二：没东西可删时，先等一等后台线程。**

- **做什么。** `src/evict.c:796-797` 的循环条件是 `bioPendingJobsOfType(BIO_LAZY_FREE)` 非 0 且还没超出时间预算；循环体里 `src/evict.c:798-800` 重算总账，达标就把 `result` 改成 `EVICT_OK`；否则 `src/evict.c:802` 用 `usleep` 睡至多 1000 微秒再看。
- **为什么等。**【注释】`src/evict.c:791-793`："It's possible that some items are being freed in the lazyfree thread. Perform a short wait here if such jobs exist, but don't wait long."。也就是：「没有可删的 key」不等于「没有正在归还的内存」，别急着报错。
- **后台任务数怎么读。** `src/bio.c:376-384` 的 `bioPendingJobsOfType` 在互斥锁保护下读 `bio_jobs_counter[type]`。这是主线程为了淘汰而主动去读的后台线程状态，读的时候加了锁。
- **这段循环是修过 bug 的。**【提交 `464aa0418`】："This bug is introduced in #7653. (Redis 6.2.0)"，症状是 "redis will sleep forever if some items are being freed in the lazyfree thread"。我对比了这次提交的改动：旧代码是只睡一次、时长等于整个时间预算；新代码改成每次最多睡 1000 微秒并反复检查。【推断】「永远睡下去」对应的是 tenacity 等于 100 的情况，那时预算是 `ULONG_MAX`（`src/evict.c:516`）；依据是旧代码把预算直接当睡眠时长，这句因果是我自己连起来的。

**出口三：被保护的 key 挡住时的宽限。**

- **做什么。** `src/evict.c:813-818`：结果是 EVICT_FAIL 且 `blessed_blocked` 为真时，只要超出量不大于 `server.maxmemory / EVICTION_OVERUSE_DIVISOR`，就把结果改成 EVICT_OK。除数定义在 `src/evict.c:39-42` 的 `EVICTION_OVERUSE_DIVISOR`，值是 4，所以上限是 1.25 倍。
- **为什么。**【注释】`src/evict.c:808-812`："failing the write with OOM would punish the user for a limit they deliberately raised. Tolerate a bounded overshoot"，以及 "Past the factor we still OOM (the hard ceiling)."
- **佐证。**【测试】`tests/unit/bless.tcl:492`："Blessed overshoot: tolerated just under the 1.25x factor, OOM just over it"。【提交 `b9800e06b`】："that factor is the effective ceiling for a heavily-blessed keyspace"。
- `blessed_blocked` 在哪里被置 1，见 5.3.4 和 5.3.5 的「连续 8 轮」。

**最后：两类观测数据。**

- **延迟事件。** `src/evict.c:820-821` 以 `eviction-cycle` 为名记录整轮耗时，`src/evict.c:804-805` 以 `eviction-lazyfree` 为名记录等后台线程的耗时。【提交 `528ea98bd`】："1. add eviction-lazyfree monitor"。
- **超限持续时间。** `src/evict.c:824-826`：结果是 RUNNING 或 FAIL 且 `server.stat_last_eviction_exceeded_time == 0` 时开始计时；`src/evict.c:827-831`：结果是 OK 时把这一段累加进 `server.stat_total_eviction_exceeded_time` 并清零。INFO 输出在 `src/server.c:7169-7170` 的 `total_eviction_exceeded_time` 和 `current_eviction_exceeded_time`。【提交 `17511df59`】："The units of these two metrics are ms."

### 5.8 旁路：哈希表扩容之前先问一句

这一段不在淘汰循环里，但它是同一个问题的「预防」那一半，放在这里对照着看。

```c
/* src/server.c:560-574 */
/* Return 1 if currently we allow dict to expand. Dict may allocate huge
 * memory to contain hash buckets when dict expands, that may lead redis
 * rejects user's requests or evicts some keys, we can stop dict to expand
 * provisionally if used memory will be over maxmemory after dict expands,
 * but to guarantee the performance of redis, we still allow dict to expand
 * if dict load factor exceeds HASHTABLE_MAX_LOAD_FACTOR. */
int dictResizeAllowed(size_t moreMem, double usedRatio) {
    /* for debug purposes: dict is not allowed to be resized. */
    if (!server.dict_resizing) return 0;

    if (usedRatio <= HASHTABLE_MAX_LOAD_FACTOR) {
        return !overMaxmemoryAfterAlloc(moreMem);
    } else {
        return 1;
    }
```

```c
/* src/evict.c:433-446 */
/* Return 1 if used memory is more than maxmemory after allocating more memory,
 * return 0 if not. Redis may reject user's requests or evict some keys if used
 * memory exceeds maxmemory, especially, when we allocate huge memory at once. */
int overMaxmemoryAfterAlloc(size_t moremem) {
    if (!server.maxmemory) return  0; /* No limit. */

    /* Check quickly. */
    size_t mem_used = zmalloc_used_memory();
    if (mem_used + moremem <= server.maxmemory) return 0;

    size_t overhead = freeMemoryGetNotCountedMemory();
    mem_used = (mem_used > overhead) ? mem_used - overhead : 0;
    return mem_used + moremem > server.maxmemory;
}
```

- **做什么。** 哈希表想扩容时先调 `dictResizeAllowed`。负载因子不超过 `HASHTABLE_MAX_LOAD_FACTOR` 时（`src/server.c:570-571`），只有 `!overMaxmemoryAfterAlloc(moreMem)` 才允许；超过了就无条件允许（`src/server.c:573` 的 `return 1;`）。阈值定义在 `src/server.h:241` 的 `HASHTABLE_MAX_LOAD_FACTOR`。
- **它挂在哪。** `src/server.c:640` 和 `src/server.c:656` 两处把 `dictResizeAllowed` 填进哈希表类型定义的回调位。这两个类型分别叫什么、被哪些表使用，我没有展开核对。
- **记账口径和淘汰一致。** `src/evict.c:443-444` 同样先扣掉 `freeMemoryGetNotCountedMemory()`。
- **为什么。**【注释】`src/server.c:560-565`："Dict may allocate huge memory to contain hash buckets when dict expands, that may lead redis rejects user's requests or evicts some keys"。【提交 `75f9dec64`】举了量级："For db dict, if we have more than 64 million keys, we need to cost 1GB for ht[1] when dict expands."。也就是：扩容那一下申请的新桶数组本身就可能把内存顶过线，引发一大波淘汰。
- **为什么又不是绝对禁止。**【提交 `75f9dec64`】："to guarantee the performance of redis, we still allow dict to expand if dict load factor exceeds the safe load factor."。负载因子太高，哈希冲突会拖慢所有命令。
- **佐证。**【测试】`tests/unit/maxmemory.tcl:425`："Don't rehash if used memory exceeds maxmemory after rehash"。

增量淘汰的提交也点名了同一个场景。【提交 `810e28a39`】："This will reduce the latency impact on many cases, especially pathological cases like massive used memory increase during dict rehashing."

### 5.9 EVICT_FAIL 之后：谁被拒，脚本怎么办

**普通命令。** 3.1 已经讲过：只有带 DENYOOM 标志的命令被拒。【文档】`redis.conf:1290-1295` 举了例子："A few examples are: SET, INCR, HSET, LPUSH, SUNIONSTORE, SORT (due to the STORE argument), and EXEC (if the transaction includes any command that requires memory)."

**脚本是特例：不能执行到一半再拒绝。**

```c
/* src/script.c:456-475 */
static int scriptVerifyOOM(scriptRunCtx *run_ctx, char **err) {
    if (run_ctx->flags & SCRIPT_ALLOW_OOM) {
        /* Allow running any command even if OOM reached */
        return C_OK;
    }

    /* If we reached the memory limit configured via maxmemory, commands that
     * could enlarge the memory usage are not allowed, but only if this is the
     * first write in the context of this script, otherwise we can't stop
     * in the middle. */

    if (server.maxmemory &&                            /* Maxmemory is actually enabled. */
        !mustObeyClient(run_ctx->original_client) &&   /* Don't care about mem for replicas or AOF. */
        !(run_ctx->flags & SCRIPT_WRITE_DIRTY) &&      /* Script had no side effects so far. */
        server.pre_command_oom_state &&                /* Detected OOM when script start. */
        (run_ctx->c->cmd->flags & CMD_DENYOOM))
    {
        *err = sdsdup(shared.oomerr->ptr);
        return C_ERR;
    }
```

- **脚本开始前拦一次。** `src/script.c:243-244` 的条件里有 `server.pre_command_oom_state`：进脚本之前已经判定内存不足，且脚本既没有声明允许超限、也没有声明自己不写，就整个不跑。
- **脚本里每条命令再判一次，但只拦「第一条写」。** `src/script.c:467-471` 的几个条件里，`!(run_ctx->flags & SCRIPT_WRITE_DIRTY)` 表示脚本到目前为止还没写过任何东西，`server.pre_command_oom_state` 用的是进脚本之前存下的结论。
- **为什么只拦第一条写。**【注释】`src/script.c:462-465`："only if this is the first write in the context of this script, otherwise we can't stop in the middle."。脚本没有回滚，写了一半再报错会留下半成品。
- **为什么用进脚本之前存下的结论，而不是现场再算。** 3.1 引过【注释】`src/server.c:4966-4970`，原话是 "memory used by lua stack and arguments might interfere"。
- **副本回放和 AOF 加载不受限。** `src/script.c:468` 的 `!mustObeyClient(run_ctx->original_client)`，行内注释写的是 "Don't care about mem for replicas or AOF."


## 6. 宏观：为什么设计成这样

前面每一节都带着局部的「为什么」。这一节把它们收拢成几个设计决定，并补上并发、失败、参数、观测这几个横切面。已经在前面给过证据的，这里只指回去，不重复引用。

### 6.1 它解决什么问题、给谁用

- **两种用法。**【文档】`redis.conf:1247-1248`："This option is usually useful when using Redis as an LRU or LFU cache, or to set a hard memory limit for an instance (using the 'noeviction' policy)."。也就是：当缓存用（满了自动腾），或者当硬上限用（满了拒写、绝不丢数据）。
- **默认是后一种。** `redis.conf:1299` 写的是 `maxmemory-policy noeviction`，代码默认值在 `src/config.c:4294` 的 `MAXMEMORY_NO_EVICTION`。而 `maxmemory` 本身默认是 0，也就是不限（`src/config.c:4393` 的 `maxmemory`）。所以一个什么都没配的 Redis 既不淘汰也不拒写。为什么默认策略选 noeviction，没查到（见第 9 节）。
- **32 位是例外。** `src/server.c:3312-3315`：32 位实例如果没配上限，启动时自动设成 3GB 加 `MAXMEMORY_NO_EVICTION`。【注释】`src/server.c:3308-3311`："This avoids useless crashes of the Redis instance for out of memory."

### 6.2 七个设计决定

**决定一：近似，而不是精确。**

- 证据：【文档】`redis.conf:1301-1305`："LRU, LFU and minimal TTL algorithms are not precise algorithms but approximated algorithms (in order to save memory), so you can tune it for speed or accuracy."；【注释】`src/evict.c:98-101`："Redis uses an approximation of the LRU algorithm that runs in constant memory."
- 精度旋钮：【文档】`redis.conf:1307-1309`："The default of 5 produces good enough results. 10 Approximates very closely true LRU but costs more CPU. 3 is faster but not very accurate."
- 【推断】省下的具体是什么：精确 LRU 需要把所有 key 串成一条按访问时间排序的链，每个 key 至少多两个指针，每次访问还要挪动链表节点；近似做法只用对象头里本来就有的 24 位（`src/object.h:112` 的 `lru:LRU_BITS`），访问时改一个字段。依据是这个字段的位置和大小；仓库里的文档只说了「为了省内存」，没有做这个对比。
- 代价：删掉的只是「抽到过的 key 里最该删的」，不是全局最该删的。候选池（决定二）就是用来缩小这个差距的。

**决定二：候选池跨调用保留。** 证据和原话见 5.3.3（提交 `22c9cfaf5`、`6416ab19d`）。代价是池里会有过时条目，所以取用时必须回查（5.3.4）。

**决定三：跨库统一选，而不是每个库各选各的。** 证据见 5.3.4（提交 `e423f76e7`）。

**决定四：限时、增量，而不是一次删够。**

- 证据：【提交 `810e28a39`】："Rather than blindly evicting until maxmemory limit is achieved, this update adds a time limit to eviction. While over the maxmemory limit, eviction will process before each command AND as a timeProc when no commands are running."
- 代价：上限变软，「还在删」的时候写命令照样放行（4.2），极端情况下内存会继续涨。提交说明自己承认了这一点（原话见 4.2）。

**决定五：记账时扣掉复制缓冲和 AOF 缓冲。** 证据见 5.2。代价是这部分内存不受 maxmemory 约束，要靠运维预留。

**决定六：删不删由主库一家说了算。** 副本默认不淘汰（5.1），主库把每次淘汰当成一条删除命令传下去（5.4）。代价是副本的内存占用完全跟着主库走，副本自己没有保护。

**决定七：删除默认在主线程同步完成，后台释放是可选项。**

- 同步删除的风险，配置文件写得很直白。【文档】`redis.conf:1360-1367`："the server can block for a long time (even seconds) in order to complete the operation."
- 后台释放是什么。【文档】`redis.conf:1369-1373`："Another thread will incrementally free the object in the background as fast as possible."。淘汰是可以打开它的场景之一（`redis.conf:1382-1384` 的 "On eviction, because of the maxmemory and maxmemory policy configurations"）。
- 为什么默认不开，没查到（见第 9 节）。
- 一条历史线索：提交 `7af4eeb74` 的标题是 "Lazyfree: incremental removed, only threaded survived."，说明早期还试过一种不用线程的增量释放，后来删掉了。这次提交没有正文，原因没查到。

### 6.3 并发模型

- **键空间只有主线程碰。** 算账、抽样、打分、从哈希表摘除、通知、传播，全在主线程（2.2）。
- **后台线程只做一件事：释放已经摘下来的对象。**【推断】所以这里不需要给键空间加锁：对象交给后台之前已经从哈希表里摘掉了（`src/db.c:948-955` 的 `freeObjAsync` 之后紧跟 `kvstoreDictTwoPhaseUnlinkFree`），而且只有引用计数为 1 的才交（`src/lazyfree.c:219` 的 `obj->refcount == 1`），也就是除了后台线程没人再拿着它。依据是这两处代码，没有注释把这层道理连起来说。
- **两个线程之间看得见的耦合有两处。** 一是分配器的已用内存统计：后台线程在还内存，主线程在读这个数。【提交 `37fd3d40a`】把它列为释放量可能为负的原因之一："Thread doing an allocation between the two calls to zmalloc_used_memory."。二是后台任务计数，主线程读的时候加了互斥锁（`src/bio.c:379-381` 的 `pthread_mutex_lock`）。
- **不允许重入。** 两道防线：安全闸里的 `isInsideYieldingLongCommand()`（5.1），以及进循环前对待传播队列为空的断言（4.1）。

### 6.4 失败模型

- **没有可淘汰的 key，或者策略是 noeviction。** 返回 EVICT_FAIL，会涨内存的命令收到 OOM 错误，读命令照常。【文档】`redis.conf:1242-1245`："will continue to reply to read-only commands like GET."
- **淘汰跟不上写入。** 没有任何报错，内存继续涨，直到某一轮追上或者真的没东西可删。【提交 `810e28a39`】："There is a risk that some other edge cases (like massive pipelined use of MGET) could cause Redis memory usage to keep growing despite the eviction attempts"。能观察到的信号是 6.6 里的「超限持续时间」。
- **被扣掉的缓冲把机器内存吃光。** 淘汰机制看不见这部分（5.2），不会为它腾地方。
- **进程中途崩溃。**【推断】不需要专门的恢复逻辑：每删一个 key，它的删除命令就立刻进入传播流程（5.4 的执行单元），没有「攒了一批还没发」的中间状态；候选池只是内存里的提示信息，重启后重新抽样即可。依据是 5.4 的代码结构；我没有找到专门讨论淘汰与崩溃恢复的注释或测试。

### 6.5 可调参数

| 配置项 | 默认 | 锚点 | 管什么 |
|---|---|---|---|
| `maxmemory` | 0（不限） | `src/config.c:4393` | 上限，改它会触发 `updateMaxmemory` |
| `maxmemory-policy` | noeviction | `src/config.c:4294` | 选哪个集合、按什么打分（5.3.1） |
| `maxmemory-samples` | 5，范围 1 到 64 | `src/config.c:4326` | 每次抽样抓多少个 key（5.3.3、5.3.4） |
| `maxmemory-eviction-tenacity` | 10，范围 0 到 100 | `src/config.c:4327` | 每轮时间预算（5.6） |
| `lazyfree-lazy-eviction` | no | `src/config.c:4209` | 大对象是否交给后台线程释放（5.4） |
| `replica-ignore-maxmemory` | yes | `src/config.c:4228` | 副本是否不自己淘汰（5.1） |
| `lfu-log-factor` | 10 | `src/config.c:4322` | LFU 计数器增长有多慢（5.3.2） |
| `lfu-decay-time` | 1 | `src/config.c:4323` | LFU 计数器多少分钟衰减一次（5.3.2） |

### 6.6 可观测性

| 想知道什么 | 看哪里 | 锚点 |
|---|---|---|
| 一共淘汰了多少 key | INFO 的 `evicted_keys` | `src/server.c:7165` |
| 有多少内存被扣掉没算 | INFO 的 `mem_not_counted_for_evict` | `src/server.c:6965` |
| 处于超限状态多久了 | INFO 的 `total_eviction_exceeded_time` 和 `current_eviction_exceeded_time` | `src/server.c:7169-7170` |
| 有多少受保护的 key | INFO 的 `blessed_keys` | `src/server.c:7166` |
| 当前 LRU 时钟 | INFO 的 `lru_clock` | `src/server.c:6844` |
| 哪个 key 被淘汰了 | keyspace 事件，事件名 `evicted` | `src/db.c:2985` |
| 一轮淘汰卡了多久 | 延迟事件 `eviction-cycle` | `src/evict.c:820-821` |
| 等后台线程等了多久 | 延迟事件 `eviction-lazyfree` | `src/evict.c:804-805` |
| 单次删除卡了多久 | 延迟事件 `evict-del` | `src/db.c:2984` |

keyspace 事件要订阅才有。【文档】`redis.conf:2248` 对应的事件类别说明是 "Evicted events (events generated when a key is evicted for maxmemory)"。

### 6.7 这段代码是怎么长成现在这样的

下面的日期是 git 记录的作者日期，完整信息见第 10 节的提交表。

1. 2012 年，`f6b32c14f`：记账时不计复制缓冲；由淘汰函数的返回值决定要不要拒写（5.2、4.2）。
2. 2014 年，`22c9cfaf5`：引入候选池（5.3.3）。
3. 2016 年，`4a140d320` 把淘汰代码拆成独立文件，提交说明就一句 "Add expire.c and evict.c."；同月 `e423f76e7` 改成跨库选择（5.3.4）。更早的历史因此不在 `src/evict.c` 名下。
4. 2017 年，`cd90389b3`：配合后台释放，循环里定期重算总账（5.5）。
5. 2018 年，`067647a78`：副本默认不再自己淘汰（5.1）。
6. 2020 年，`810e28a39`：限时增量淘汰（决定四）。同年 `37fd3d40a`、`c4fdf09c0` 修正「释放量」的记账，`75f9dec64` 加上扩容前的检查（5.4、5.8）。
7. 2021 到 2023 年，`7ac213079`、`2e1979a21`、`0ffb9d2ea`：理顺传播顺序和执行单元（5.4、3.2）；`464aa0418` 修掉等待后台线程时的无限睡眠（5.7）。
8. 2026 年，`0cb1ee0dc` 加入 LRM 策略（5.3.2），`b9800e06b` 加入 BLESS（5.3.6）。

【推断】从这条线看，「抽样、删除、把删除传播出去」的骨架很早就定了；后来的提交大多在修两类问题：一类是「什么算释放了的内存」（记账），一类是「删除命令以什么顺序、什么边界传出去」（传播）。这也是今天读这段代码最需要小心的两处。这是我读完这些提交说明后的归纳，没有哪条提交这样总结过。


## 7. 读者最容易困惑的点（集中索引）

这一节不引入新事实，只把前面散落的「这里容易看错」收在一起，每条指回证据所在的小节。

**名字和实际含义对不上的：**

1. **候选池的字段叫 `idle`，但它不一定是空闲时间。** 它是一个通用的「越大越该删」的分数，LFU 下是反过来的频率，TTL 下是反过来的过期时间。见 5.3.3。
2. **EVICT_OK 不一定表示内存没问题。** 安全闸不放行时返回的也是它，源码自己管这叫假的 OK。见 4.2、5.1。
3. **`cant_free` 标签不是失败专用。** 正常删够的路径也会流过它，靠 `result` 的值区分。见 4.1。
4. **maxmemory 不是进程内存上限。** 它比的是分配器统计值减去几类缓冲，不含碎片，不含被扣掉的复制缓冲和 AOF 缓冲；而且在「还在删」的状态下可以被越过。见 5.2、4.2。

**看起来多余、其实不是的：**

5. **长命令检查做了两次**（命令入口一次，安全闸里一次）。因为定时器那条路不经过命令入口。见 5.1。
6. **受保护 key 检查做了两次**（入池打分时一次，从池里取出后一次）。因为 key 入池以后还可能被标成受保护。见 5.3.4。
7. **从池里取出 key 后还要回哈希表查一遍。** 因为池跨调用保留，里面的 key 可能已经被删、被改、过期。见 5.3.4。

**看起来漏了、其实是有意（或至少无害）的：**

8. **key 被删除时没有人去清候选池。** 靠取用时的回查兜底。见 5.3.4。
9. **`result` 没有任何地方显式赋成失败。** 失败是初值。见 4.1。
10. **读 LFU 计数时做了衰减计算，但没有写回对象。** 见 5.3.2。
11. **`mem_freed` 是有符号数，可以是负的。** 见 5.4。

**效果不在这一行、要到别处才看得见的：**

12. **删除命令的传播。** 淘汰循环里只是把命令放进待传播队列，真正写 AOF、发给副本是在执行单元结束时。见 5.4。
13. **内存真正还给分配器。** 开了后台释放时，大对象是在另一个线程里释放的，主循环当时看不到内存下降，所以才有 5.5 的重算总账和 5.7 的等待。
14. **发给客户端的缓存失效通知。** 淘汰会产生失效消息，它们在命令入口处、淘汰返回之后才统一发出。见 3.1。
15. **淘汰过程中当前客户端可能被释放。** 命令入口在淘汰之后要重新检查。见 3.1。

**时间上不连续的：**

16. **一轮没删完，下一轮在哪。** 不在当前调用栈里，而是下一条命令到来时，或者事件循环里的零延时定时器。见 3.2。

**这两件事最好对照着看：**

- 「超限但还在删」（放行写命令）对「超限且删不动」（拒绝写命令）：4.2。
- 池化策略（抽样、打分、入池、取最优）对随机策略（抽一个就删）：5.3.4 对 5.3.5。
- 同步删除对后台释放：5.4，以及它引出的 5.5、5.7。
- 命令入口触发对定时器触发：3.1 对 3.2。

## 8. 可改进处

这一节全部是我的看法，不是仓库里的结论。每条都给出我依据的代码事实，但「值不值得改」需要压测和线上数据，我没有。

1. 【观点】**切换策略时候选池不清空。** 池里的分数是按旧策略算的，切到新策略后旧条目会和新条目混排一段时间。依据：配置表里 `src/config.c:4294` 的 `maxmemory-policy` 这一项我没有看到挂清池的回调，`src/evict.c` 里也没有清池的函数。同理，在 LRU 和 LFU 之间切换时，对象头里那 24 位不会被重新解释。影响应该是短暂的（旧条目会被逐步取走或挤掉），但没有注释说明这是有意接受的。
2. 【观点】**时间预算的最小粒度是 16 个 key。** `src/evict.c:776` 的 `elapsedUs(evictionTimer)` 只在每 16 个 key 的检查点才读。如果这 16 个 key 都是同步删除的大对象，一轮可以远远超出预算（5.5 的边界分析）。可以考虑按「已释放字节数」而不是「key 个数」决定何时看表。
3. 【观点】**等待后台线程时在主线程里睡眠。** `src/evict.c:802` 的 `usleep` 会让主线程停下，期间不处理任何客户端。上限受时间预算约束，默认配置下很短；但把预算调大的用户会直接感受到。
4. 【观点】**「还在删」时写命令无条件放行，没有折中档。** 提交说明自己承认内存可能持续上涨（4.2）。可以考虑在超出上限一定比例后改为拒绝，类似 BLESS 那条 1.25 倍的线（5.7），但目前这条线只对受保护 key 的场景生效。
5. 【观点】**客户端缓存失效通知造成的反馈没有被扣掉。** 淘汰 key 会给开了客户端缓存的连接发失效消息，这些消息占的输出缓冲算在已用内存里。【测试】`tests/unit/maxmemory.tcl:522-524` 的注释承认了这一点："eviction continues (known problem described in #8069)"。那个 issue 的内容我读不到。
6. 【观点】**没有低水位。** `src/evict.c:425` 写的是 `mem_tofree = mem_used - server.maxmemory;`，目标是刚好回到线上。写入持续的话，几乎每条写命令都会重新进入淘汰。多删一点可以把淘汰摊成更少的批次，代价是缓存命中率略降。快路径（5.2）已经让「没超」的情况很便宜，所以这一条的实际收益我不确定。
7. 【观点】**两个时钟都会回绕。** LRU 时钟宽度是 `src/object.h:94` 的 `LRU_BITS`（24 位），精度是 `src/object.h:96` 的 `LRU_CLOCK_RESOLUTION`（1000 毫秒），我算下来大约 194 天回绕一次；LFU 的 16 位分钟时间戳大约 45.5 天回绕一次。回绕后估算会偏小，一个极久没访问的 key 会被误判成较新。对缓存场景影响很小，但读代码时要知道这个估算不是单调的。
8. 【观点】**池条目的 `slot` 字段没有初始化。** `src/evict.c:122-127` 的初始化循环设置了 `ep[j].dbid = 0;` 等字段，没有设 `slot`（字段定义在 `src/evict.c:48` 的 `int slot;`）。它是无害的：读 `pool[k].slot` 的地方（`src/evict.c:647`）之前一定先判过 `pool[k].key == NULL`（`src/evict.c:638`），而 key 非空时 `slot` 已经在入池时写过（`src/evict.c:232` 的 `pool[k].slot = slot;`）。只是补一行初始化会让读者少想一步。
9. 【观点】**有子进程时不更新访问信息。** 5.3.2 讲过这是为了避免写时复制。副作用是长时间的快照或重写期间，新的访问热度不会被记下来，淘汰依据的是之前的旧信息。

## 9. 没查到为什么

下面这些地方我查过源码注释、提交说明、配置文件注释和测试，没有找到理由。我没有用听起来合理的说法去填。

**一批数字，只有值、没有出处：**

- 候选池为什么是 16 格（`src/evict.c:36` 的 `EVPOOL_SIZE`）。
- 循环里为什么每 16 个 key 检查一次。提交 `cd90389b3` 的说明写的是每 10 个 key，今天的代码是 16；是哪次提交、出于什么原因改的，我没有追到。
- 连续多少轮只抽到受保护 key 就放弃，为什么是 8（`src/evict.c:38` 的 `EVICTION_MAX_BLESSED_ONLY_ROUNDS`）。
- 受保护 key 场景下的容忍上限为什么是 1.25 倍（`src/evict.c:42` 的 `EVICTION_OVERUSE_DIVISOR`）。
- 时间预算公式里的常数：每档 50 微秒、超过 10 档后按 1.15 倍增长（5.6）。
- 默认抽样数为什么是 5。配置文件只说够用（`redis.conf:1307-1309` 的 "The default of 5 produces good enough results."），没有给数据。
- 随机策略挑分片时最多试 16 次，而池化路径同一个函数只试 1 次（`src/evict.c:713` 对 `src/evict.c:146`，都是 `kvstoreGetFairRandomDictIndex`）。
- 池化路径里「抽样数乘以 10」这个尝试上限里的 10（5.3.4）。
- 对象头里为什么是 24 位、精度为什么是 1 秒（`src/object.h:94` 的 `LRU_BITS`，`src/object.h:96` 的 `LRU_CLOCK_RESOLUTION`）。
- 后台释放的门槛为什么是 64 个元素（`src/lazyfree.c:131` 的 `LAZYFREE_THRESHOLD`）。
- 哈希表扩容检查里的负载因子上限为什么是 1.618（`src/server.h:241` 的 `HASHTABLE_MAX_LOAD_FACTOR`）。

**一批设计选择，只有结果、没有论证：**

- 为什么默认策略是 noeviction。
- 为什么后台释放对淘汰默认关闭（`redis.conf:1402` 的 `lazyfree-lazy-eviction no`）。
- 为什么没有专门的淘汰线程，而是在主线程里做。2.2 里我给了一个推断，但没有文字证据。
- 早期的非线程增量释放为什么被删。提交 `7af4eeb74` 只有标题。
- 暂停期间为什么不淘汰。提交 `a3e53cf9b` 只指向一个 issue 编号，正文没有理由。

**读不到的材料：**

- 我不能访问外网，所以所有 PR 和 issue 的正文都没读，只读了提交说明。提交说明里引用过、而我没读到的编号包括 #327、#2647、#4027、#5686、#7653、#8069、#9890、#15649。其中一些很可能包含上面那些问题的答案。

**我没有展开去追的代码路径：**

- 分配器的已用内存统计是怎么在多线程下累加的（`src/zmalloc.c:658` 的 `zmalloc_used_memory` 我只看了名字和位置）。
- 向副本刷输出缓冲时，具体哪条错误路径会把客户端释放掉（3.1、5.5 只引用了注释的说法）。
- 哪些类型的哈希表挂了扩容检查。我只确认了 `src/server.c:640` 和 `src/server.c:656` 两处登记了 `dictResizeAllowed`，没有逐个确认它们分别是哪种表。
- 集群 slot 迁移（ASM）那一侧的细节。本稿只在它和淘汰交界的地方引用了注释。

## 10. 附录

### 10.1 引用过的提交

下表由脚本从 git 读出日期、作者和标题；最后一列是本稿引用这条提交时涉及的文件，核对脚本会确认该提交确实改动了这些文件。早期提交里文件名还是 `src/redis.c`、`src/redis.h`。

| 哈希 | 日期 | 作者 | 标题 | 本稿引用它时涉及的文件 |
|---|---|---|---|---|
| `cd90389b3` | 2017-02-21 | antirez | freeMemoryIfNeeded(): improve code and lazyfree handling. | `src/evict.c` |
| `2753429c9` | 2021-09-23 | yoav-steinberg | Client eviction (#8687) | `src/server.c` `src/networking.c` |
| `fd135f3e2` | 2021-10-07 | Huang Zhw | Make tracking invalidation messages always after command's reply (#9422) | `src/server.c` `src/tracking.c` |
| `2e1979a21` | 2022-01-04 | zhaozhao.zz | use startEvictionTimeProc() in config set maxmemory (#10019) | `src/config.c` `src/evict.c` |
| `8ad0cfa56` | 2022-04-24 | guybe7 | isSafeToPerformEvictions: Remove redundant condition (#10610) | `src/evict.c` |
| `810e28a39` | 2020-09-15 | Jim Brunner | Incremental eviction processing (#7653) | `src/evict.c` `src/server.c` `src/config.c` `redis.conf` |
| `f6b32c14f` | 2012-02-04 | antirez | This fixes issue #327, is a very complex fix (unfortunately), details: | `src/redis.c` `src/networking.c` |
| `b2061de2e` | 2022-06-01 | Oran Agra | Fix broken protocol in MISCONF error, RM_Yield bugs, RM_Call(EVAL) OOM check bug, and new RM_Call checks. (#10786) | `src/evict.c` `src/server.c` `src/script.c` |
| `b9cd89d10` | 2018-12-12 | zhaozhao.zz | evict: don't care about mem if loading | `src/evict.c` |
| `a3e53cf9b` | 2017-06-01 | Zachary Marquez | Prevent expirations and evictions while paused | `src/evict.c` `src/expire.c` |
| `067647a78` | 2018-08-27 | antirez | Introduce repl_slave_ignore_maxmemory flag internally. | `src/evict.c` `src/server.c` |
| `c9097393b` | 2017-06-23 | antirez | Issue #4027: unify comment and modify return value in freeMemoryIfNeeded(). | `src/evict.c` |
| `f0fab99d6` | 2021-09-23 | Huang Zhw | Minor optimize getMaxmemoryState, when server.maxmemory is not set (#9533) | `src/evict.c` |
| `c1718f9d8` | 2021-10-25 | Wang Yuan | Replication backlog and replicas use one global shared replication buffer (#9166) | `src/evict.c` `src/replication.c` |
| `29e488cbb` | 2018-10-16 | Damien Tournoud | Overhead is the allocated size of the AOF buffer, not its length | `src/evict.c` |
| `0cb1ee0dc` | 2026-01-06 | debing.sun | New eviction policies - least recently modified (#14624) | `src/evict.c` `src/db.c` `redis.conf` |
| `06ca9d683` | 2017-11-27 | antirez | LFU: Fix LFUDecrAndReturn() to just decrement. | `src/evict.c` |
| `583c31472` | 2017-10-15 | zhaozhao.zz | LFU: do some changes about LFU to find hotkeys | `src/evict.c` `src/db.c` |
| `22c9cfaf5` | 2014-03-20 | antirez | LRU eviction pool implementation. | `src/redis.c` `src/redis.h` |
| `6416ab19d` | 2016-07-18 | antirez | LFU: Use the LRU pool for the LFU algorithm. | `src/evict.c` |
| `e64bf05f4` | 2016-07-12 | antirez | LRU: cache SDS strings in the eviction pool. | `src/evict.c` |
| `2d5eb1f1a` | 2016-07-20 | antirez | Volatile-ttl eviction policy implemented in terms of the pool. | `src/evict.c` |
| `e423f76e7` | 2016-07-13 | antirez | LRU: Make cross-database choices for eviction. | `src/evict.c` |
| `9ee1cc33a` | 2023-12-06 | zhaozhao.zz | Make the sampling logic in eviction clearer (#12781) | `src/evict.c` |
| `b9800e06b` | 2026-09-15 | Tal Bar Yakar | RED-211767 - Add BLESS — protect keys from eviction (#15649) | `src/evict.c` `src/bless.c` `src/db.c` `tests/unit/bless.tcl` |
| `464aa0418` | 2022-09-18 | zhaozhao.zz | fix infinite sleep in performEvictions when have lazyfree jobs (#11237) | `src/evict.c` |
| `37fd3d40a` | 2020-10-13 | guybe7 | performEvictions: mem_freed may be negative (#7908) | `src/evict.c` |
| `c4fdf09c0` | 2020-12-06 | Oran Agra | prevent client tracking from causing feedback loop in performEvictions (#8100) | `src/evict.c` `tests/unit/maxmemory.tcl` |
| `7ac213079` | 2021-12-22 | guybe7 | Sort out mess around propagation and MULTI/EXEC (#9890) | `src/evict.c` `src/db.c` `tests/unit/maxmemory.tcl` |
| `0ffb9d2ea` | 2023-11-08 | Meir Shpilraien (Spielrein) | Before evicted and before expired server events are not executed inside an execution unit. (#12733) | `src/evict.c` `src/expire.c` |
| `75f9dec64` | 2020-12-06 | Wang Yuan | Limit the main db and expires dictionaries to expand (#7954) | `src/evict.c` `src/server.c` `src/dict.c` `tests/unit/maxmemory.tcl` |
| `17511df59` | 2021-07-26 | Huang Zhw | Add INFO stat total_eviction_exceeded_time and current_eviction_exceeded_time (#9031) | `src/evict.c` `src/server.c` |
| `528ea98bd` | 2019-02-27 | zhaozhao.zz | lazyfree & eviction: record latency generated by lazyfree eviction | `src/evict.c` |
| `7af4eeb74` | 2015-09-25 | antirez | Lazyfree: incremental removed, only threaded survived. | `src/lazyfree.c` |
| `4a140d320` | 2016-07-06 | antirez | Add expire.c and evict.c. | `src/evict.c` `src/expire.c` |

### 10.2 研究范围

- **通读的文件：** `src/evict.c` 全文（834 行，本稿绝大多数锚点在这里）。
- **读了相关段落的文件：** `src/server.c`（命令入口、执行单元收尾、扩容检查、INFO 输出、32 位默认值）、`src/db.c`（删除与传播）、`src/lazyfree.c`、`src/bio.c`、`src/script.c`、`src/config.c`（配置表）、`src/object.h`、`src/object.c`、`src/server.h`、`src/bless.c`、`src/kvstore.c`、`src/dict.c`、`src/networking.c`、`src/ae.c`，以及 `redis.conf` 里 maxmemory 和 lazyfree 两段、`tests/unit/maxmemory.tcl`、`tests/unit/bless.tcl` 的相关用例。
- **只确认了符号位置、没有读实现的：** `src/zmalloc.c`。
- **git 历史：** 改动过 `src/evict.c` 的提交一共 78 条，我全部看了标题和改动文件统计，其中大约 35 条读了完整的提交说明；另外沿着函数改名之前的历史追了几条更早的提交（上表里文件名是 `src/redis.c` 的那些）。上表列出的是最终被本稿引用的。
- **没有做的：** 没有编译、没有运行 Redis、没有跑测试。本稿里所有「会发生什么」都来自读代码，不是观察到的行为。

### 10.3 机械核对做了什么、没做什么

交付前用脚本核对过本稿，脚本在仓库之外，只把仓库内容当数据读。

- **代码块：** 每个带文件头的代码块，逐行和 HEAD 上对应行号的内容比对。
- **锚点：** 正文里每个「文件:行号」锚点，它所在的那句话里必须有一个反引号标出的符号或一段引号里的原话，能在锚点指向的那些行上找到。
- **原话：** 每段直引号里的英文原话，必须能在同一行所标锚点的源码行里、或同一行所标提交的说明里找到（省略号表示中间有删节）。
- **提交：** 每个哈希必须存在，表里的日期、作者、标题必须和 git 一致，表里列的文件必须确实被该提交改动。

**它保证不了的：** 脚本只能证明「我说的符号确实在那一行」「我引的话确实有人写过」，证明不了我对这行代码的解释是对的，也证明不了标成推断的那些说法成立。解释是否正确，仍然要靠读者对照代码块自己判断。
