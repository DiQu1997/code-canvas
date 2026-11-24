交付后的独立机械核对（`/root/.claude/skills/canvas-duo/check/spot_manuscript.py`，对 HEAD）发现底稿 `/root/canvas-duo-runs/LMCache-20261123-2359/research/manuscript-l1.md` 里有核不过的锚点或提交哈希（BAD），以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：

  BAD 底稿第 155 行  config.py:495  —— 路径有歧义（9 个同名）
  BAD 底稿第 183 行  memory_management.py:1289  —— 路径有歧义（2 个同名）
  BAD 底稿第 297 行  config.py:495  —— 路径有歧义（9 个同名）
  ??? 底稿第 6 行  路径缩写：`L1B` = `lmcache/v1/distributed/l1_manager/base.py`；`SM` = `lmcache/v1/distributed/storage_manager.py`；`PC` = `lmcache/v1/distributed/storage_con
  ??? 底稿第 46 行  allocate(layout, n)
  ??? 底稿第 57 行  int(456 × 0.2) = 91
  ??? 底稿第 71 行  reserve_write(..., tag="storage_manager")
  ??? 底稿第 79 行  _staging = {h0:{"storage_manager":…}, h1:…, h2:…}
  ??? 底稿第 79 行  _staging_bytes = 108 MiB
  ??? 底稿第 114 行  reserve_read(row.keys, row.num_kv_readers)
  ??? 底稿第 155 行  int(len(self._order) × ratio)
  ??? 底稿第 164 行  will check again and safely reject a key that became locked between the check and the actual deletion.” 淘汰线程调
  ??? 底稿第 177 行  int(456×0.2) = 91
  ??? 底稿第 184 行  (OUT_OF_MEMORY, [])
  ??? 底稿第 239 行  counter > 0 && now < expiration

请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；??? 里真是引文却对不上原文的，照原文改。不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。