# 舞台问题（讲解员，L1 存储下一层）

1. 锚点扩展名白名单（build.py `REF_RE`）不含 `.cpp` / `.pyi`（顶层已报过 `.rst`）。
   本层的 `TTLLock` 实现在 `csrc/lmcache_native/ttl_lock.cpp`，说明卡里没法写 `` `csrc/lmcache_native/ttl_lock.cpp:74` `` 这种锚点（会被当成行内代码并 WARN）；
   `code()` 源码页能显示 .cpp，但 .cpp 代码块里的 `at(..., '注释', q=...)` 不给 ref 时会自动拼成 `ttl_lock.cpp:a-b`，直接 BuildError「证据锚点格式不对」。
   最小复现：`code('csrc/lmcache_native/ttl_lock.cpp', [(13, 20)], notes=[at(14, '注释', 'x', q='now_ms')])`。
   绕开：卡片里改引 `ttl_lock.h`（在白名单里）并用 `[[l1.ttl]]` 式的下钻；.cpp 页的批注只用无等级 / 推断。
2. 手机上线标签在「为什么 / 易混点」图层比主流程长得多（上限 18 字），两个并排框之间的空隙放不下；只能把线的 why / trap 压到 8 字左右，
   或把框拉开很远。若线的 why / trap 能有手机短版（像 `ml` 那样）会好很多。
