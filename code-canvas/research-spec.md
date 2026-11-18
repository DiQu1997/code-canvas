# 调研（research.json）——读懂代码，把事实交给呈现方

生成一张画布分两段：**调研方**读懂代码，写出 `research.json`；**呈现方**只拿这份文件去设计画布。
你是调研方。你不设计画布，不考虑怎么画、分几层、动画怎么动——那是呈现方的事。
你要保证的是：呈现方**不重读仓库**，只靠这份文件就能把读者的问题讲对。

所以这份文件是事实库，不是文章：每条结论都带依据 `[文件, 行号, 符号]`，由 `check_research.py` 机械核对。

## 1. 你要回答的

1. **读者到底问的是哪块代码**。问题太宽就收窄，并写明收窄到哪、为什么。
2. **线上默认实际运行的是什么**。读者问的类 / 函数是不是默认路径？有没有同名的精简版、旧版、
   实验版？去找实例化它的地方（工厂、注册表、配置默认值）核实。**这是最容易讲错、代价最大的一条**，
   写进 `scope.default_path`，带依据。
3. **有哪些部件**。按职责和执行归属（哪个线程 / 进程、谁拥有这份状态）切，不按目录切。
4. **每个机制是怎么做的**。用第 3 节的模式词表说它是什么，填上那个模式要求的每一项，
   再写它**和教科书不一样的地方**——那通常是这段代码最值得讲的东西。
5. **主路径**。一个具体的例子（写出具体的值）从头走到尾，每一步落到真实的行。
6. **读者缺什么背景**。读者是 `audience` 描述的人；他不认识的词列出来，各用一两句讲清。
7. **你没查清的**。写进 `open_questions`，不要用猜测填空。

能跑的就跑（测试、几行复现脚本）：跑出来的证据比推演可靠。

## 2. 格式

```jsonc
{
 "question": "读者的原话",
 "audience": "读者是谁、已经会什么、不会什么",
 "scope": {
   "covers": "这份调研讲的是哪份代码（文件 / 类）",
   "not_covered": ["没讲的相邻部分，各一句为什么"],
   "default_path": "线上默认实际运行的是什么；和这里讲的是不是同一份，差在哪",
   "refs": [["python/pkg/registry.py", 152, "create_unified"]]
 },
 "shape": {"kind": "structure", "why": "读者问的是一个数据结构怎么工作"},
 "protagonist": {"what": "谁的状态变化是这段代码存在的理由", "instance": "例子里的具体实例，如 请求 B = [11,12,13]"},
 "background": [{"term": "KV cache", "explain": "一两句，新手能懂"}],
 "modules": [
   {"id": "tree", "name": "RadixCache", "role": "负责什么", "not": "它不是什么（和谁容易混）",
    "owns": ["它拥有的状态"], "runs_on": "哪个线程 / 进程", "files": ["python/pkg/radix_cache.py"],
    "refs": [["python/pkg/radix_cache.py", 300, "class RadixCache"]]}
 ],
 "mechanisms": [
   {"id": "split", "pattern": "tree", "module": "tree", "name": "匹配停在节点中间就分裂",
    "summary": "三四句讲清它怎么运转",
    "slots": {"node": "TreeNode：key 是一段 token，value 是槽号", "key": "子节点字典的键是第一个 token",
              "ops": "match / split / insert / delete_leaf"},
    "deviation": "查询会改树：match 命中停在节点中间时先把节点劈开",
    "invariants": ["lock_ref > 0 的节点不会被淘汰"],
    "refs": [["python/pkg/radix_cache.py", 684, "_split_node"]]}
 ],
 "functions": [
   {"id": "match_helper", "name": "RadixCache._match_prefix_helper", "file": "python/pkg/radix_cache.py",
    "lines": [658, 682], "module": "tree", "does": "一句话：做了什么决定 / 改了什么状态",
    "calls": ["split"], "on_main_path": true}
 ],
 "handoffs": [
   {"from": "sched", "to": "tree", "how": "调用 / 入队 / 回调 / 跨进程", "what": "交出去的是什么",
    "refs": [["python/pkg/common.py", 197, "match_prefix"]]}
 ],
 "walkthrough": {
   "setup": "例子的设定；哪些值是为演示假设的，写明",
   "steps": [
     {"text": "这一步发生了什么，用例子的具体值", "values": {"prefix_len": 5},
      "function": "match_helper", "ref": ["python/pkg/radix_cache.py", 669, "prefix_len < len(child.key)"]}
   ]
 },
 "predictions": [
   {"if": "prompt 全部命中", "then": "仍要算最后一个 token", "refs": [["python/pkg/batch.py", 1223, "input_len - 1"]]}
 ],
 "open_questions": [{"q": "没查清的问题", "why": "为什么没查清"}]
}
```

- `shape.kind`：`flow` 一个东西穿过系统被加工 · `lifecycle` 一个东西被创建、改变状态、销毁 ·
  `loop` 系统自己一拍一拍地转 · `operation` 把一次调用剖开 · `transform` 一个值被逐步加工 ·
  `structure` 一个数据结构和对它做的操作。常见两种叠加，写主的那种，`why` 里说另一种在哪交汇。
- `functions`：主角经过的、做决定或改状态的函数。`lines` 必须对准真实函数（第一行是签名）。
  纯转发、getter、日志不列。10–40 个。
- `walkthrough.steps`：8–30 步，一步一个变化；除了发生在仓库之外的步，每步都带 `ref`。
  数值必须能从代码和 `setup` 算出来。
- `predictions`：换一组输入会怎样。**只写你在代码里核实过的**，没核实的放 `open_questions`。

## 3. 模式词表（mechanisms[].pattern）

机制是有限的。从下表里选最贴近的一个，把它的每一项填进 `slots`。

| pattern | 是什么 | slots 必填 |
|---|---|---|
| `queue` | 先进先出的缓冲 | `producer` 谁放 · `consumer` 谁取 · `capacity` 多大、满了怎样 |
| `stack` | 后进先出 | `push` · `pop` |
| `map` | 键 → 值的表 | `key` · `value` · `lifetime` 条目何时进、何时出 |
| `tree` | 树形结构 | `node` 节点里有什么 · `key` 怎么找子节点 · `ops` 有哪些操作 |
| `linked` | 链表 / 指针图 | `node` · `links` 指向谁 · `ops` |
| `ring` | 环形缓冲 / 时间轮 | `slots` 几格 · `cursor` 游标怎么走 |
| `pool` | 一池可复用的资源（槽、块、对象） | `unit` 一份是什么 · `alloc` · `free` · `exhausted` 用光了怎样 |
| `counter` | 计数 / 预算 / 水位 | `what` 数的是什么 · `inc` · `dec` · `limit` |
| `log` | 只追加的序列 + 游标 | `append` · `cursors` 有哪些游标 |
| `thread` | 一个执行者（线程 / 进程） | `runs` 它跑什么 · `blocks_on` 会阻塞在哪 |
| `coroutine` | 可挂起、可恢复的执行 | `suspend` 在哪挂起 · `resume` 谁恢复它 · `state` 挂起时状态存哪 |
| `switch` | 上下文切换 / 栈切换：保存现场、换栈、恢复 | `saved` 现场里存了什么、存在哪 · `swap` 怎么换过去 · `fresh` 从没跑过的上下文怎么造出"像刚被切出"的现场 |
| `loop` | 主循环，每圈固定几站 | `stations` 每圈做哪几件事 · `exit` 何时退出 |
| `pipeline` | 一串有先后的阶段 | `stages` |
| `dispatch` | 按类型 / 条件分派 | `on` 按什么分 · `branches` |
| `statemachine` | 显式状态 + 转换 | `states` · `transitions` 每条：从哪到哪、什么触发 |
| `call` | 同步调用再返回 | `caller` · `callee` |
| `enqueue` | 交接：放进队列，另一个执行者取走 | `from` · `to` · `via` 哪条队列 |
| `callback` | 闭包存起来，之后由别人调用 | `registered` 在哪登记 · `invoked` 谁、何时调用 |
| `lock` | 互斥 / 等待 | `protects` 保护什么 · `holders` 谁会拿 |
| `refcount` | 引用计数，决定能不能回收 | `counted` 数的是谁 · `inc` · `dec` · `zero` 归零时怎样 |
| `evict` | 资源不够时按策略回收 | `candidates` 谁能被回收 · `order` 按什么顺序 · `trigger` 何时触发 |
| `retry` | 失败 / 超时后重试 | `condition` · `backoff` |
| `other` | 上面都不贴切 | `missing` 缺一个什么样的模式、它有哪些要素 |

`other` 是正经的出口：硬套一个不贴切的模式，比说"没有合适的"更糟。

## 4. 核对

```bash
python3 check_research.py research.json --repo <仓库根>     # 0 ERROR 才算过；WARN 逐条看
```

它检查：必填项齐全；每条依据的文件存在、行号不越界、符号在该行 ±3 行内；`functions` 的行段对准
真实函数；`mechanisms` 的 pattern 在词表里、slots 齐全；`walkthrough` 的步有依据、指向的函数存在。
通过后它把仓库当时的提交写进 `repo.commit`——呈现方读的必须是同一份代码。

**不可商量**：0 ERROR；`scope.default_path` 有依据；没核实的不写成事实。
