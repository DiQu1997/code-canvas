改了 3 处，删了 0 处。

- **改了 3 处**：BAD 的 3 个歧义锚点（`base.py:259`、`unified_radix_cache.py:1669`、`schedule_batch.py:1215`）已补成完整路径。我自己的核对脚本重跑后，锚点全部通过。
- **没动的 ???**：这些条目是我自己写的代码缩写或例子，不是引文，所以没改也没删。
  - 其中有两类是真引文，但出自 HEAD 之外：`FIXME…` 和 `Need at least one token…` 出自 `1e495e0847` 当时的历史文件（`git show` 可核到），`so LRU eviction can discard…` 等出自 `0a57403468` 的 diff。这几条我已逐字核对过，所以保留。
