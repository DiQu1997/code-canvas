# 顶层需要改的地方（来自「传输模块」下一层，依据 research/manuscript-xfer.md）

## 1. 部件 `xfer` 说明卡「怎么设计的」：RETRIEVE 的先后顺序写反了
- 原话：「RETRIEVE ①：算 key → 直接读 LOOKUP 已经锁住的对象（`unsafe_read`，不再加锁）。」「RETRIEVE ②：等 event → 入队 CPU→GPU 拷贝 → 挂回调 `finish_read_by_owner` 放锁。」
- 为什么不对：代码里**等 event 在前**（`lmcache_driven_transfer.py:1269-1272`），`unsafe_read` 在后（`:1330-1332`）；中间还有一步「先打包要放的锁」（`:1314-1318`）。底稿 §4 末尾「更正上一趟」、§4 R3–R5。
- 建议：「RETRIEVE ①：算 key → 让自己的流等 vLLM 的 event → 先定好拷完要放的锁。」「RETRIEVE ②：`unsafe_read` 读 LOOKUP 已锁住的对象（不再加锁）→ 入队 CPU→GPU 拷贝 → 挂回调 `finish_read_by_owner` 放锁。」

## 2. 推演第 11 步「B：拷回 GPU，拷完放锁」：同样的顺序问题
- 原话：「服务端重算出同样的 3 个 key，**不再加锁**，直接读已锁住的对象；流先等 vLLM 的 event，再入队 CPU→GPU 拷贝……」
- 为什么不对：同上，等 event 在读对象之前（底稿 §4 R3、R5）。
- 建议：「服务端重算出同样的 3 个 key，先让流等 vLLM 的 event，再**不加锁**地读已锁住的对象，入队 CPU→GPU 拷贝……」

## 3. 部件 `xfer` 说明卡「为什么」第 1 条：「要么全存，要么全不存」说得太满（说法不准，不算错）
- 原话：「要么全存，要么全不存：每块都拷成功才提交，否则整单放弃。」
- 为什么不准：只对「入队时抛异常 / block id 不够」成立；某些 key 已在 L1 或 L1 没空间时**不算失败**，那几块跳过、其余照存（底稿 §5 Q4 的表，`lmcache_driven_transfer.py:970-975`）。
- 建议：在「容易看错的地方」加一句：「『全不存』只指入队出错或 block id 不够；key 已存在 / L1 满时只跳过那几块，其余照存。」
