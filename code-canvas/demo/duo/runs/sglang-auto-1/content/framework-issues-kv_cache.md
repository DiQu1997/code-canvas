# 舞台问题（kv_cache 讲解员）

1. **`.mdx` 文件不能当证据锚点。**
   现象：`note('文档', ..., q=..., ref='docs/docs/advanced_features/radix_eviction_policy.mdx:13')` → 构建 ERROR「证据锚点格式不对」。
   sglang 的官方文档全是 `.mdx`（`docs/docs/**/*.mdx`），底稿里有 3 条关键的【文档】依据（淘汰只看叶子 / 从枝头往根 / 策略只打分、锁才是硬保护）都在这里。
   绕法：改引 `mem_cache/unified_cache/components/README.md` 和代码注释里意思相近的原文；「父节点重新进候选」那条只能降成【推断】。
   建议：锚点扩展名白名单加上 `mdx`（按 `md` 处理即可）。

2. （小）一张图里想表达「同一条线在两个时刻各走一次」（写树：prefill 后一次、结束时一次；分槽：prefill 15 个、decode 每步 1 个），
   只能把角标写成 `n='4/6'`、`n='3/5'`。能用，但角标变宽，手机上右侧走线的标签容易出画幅。
