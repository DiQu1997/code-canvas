# 顶层修正建议（来自「L1 存储」下一层讲解员）

1. 顶层连线 `w_rw`（传输模块 → L1 存储）说明卡「怎么设计的」第 2 条，原话：「转正时还会通知监听者（淘汰控制器等）。」
   - 不准处：`finish_write` 通知的监听者是淘汰**策略**的桥接器 `L1EvictionPolicy`（它再调 LRU 的 `on_keys_created`）和（有 L2 时的）StoreController，
     不是淘汰控制器（`L1EvictionController`，那是每秒轮询的线程，不听事件）。依据：底稿 manuscript-l1.md §1.1「L1EvictionPolicy（监听桥）」一行、§3 Q1 第 6 步。
   - 建议改成：「转正时还会通知监听者（LRU 名单、有 L2 时的写入控制器）。」
