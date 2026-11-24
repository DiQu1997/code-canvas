改了 4 处，删了 0 处。

- **BAD 锚点（3 处）**：把 `config.py:495`（两处）和 `memory_management.py:1289` 改成完整路径，指向的行不变。
- **疑似引文（1 处）**：第 164 行的引文原先以反引号开头。我把反引号去掉，引文改成 “will check again and safely reject a key that became locked between the check and the actual deletion.”，并在后面注明句首主语是 `delete()`。
- **其余 ???（没动）**：第 6、46、57、71、79、114、155、177、184、239 行这几条是代码表达式、缩写表或我自己举的例子，不是对源码的引文。

我自己的核对脚本重跑通过，锚点、提交哈希、引文都没有新增问题。