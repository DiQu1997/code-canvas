# 舞台问题（讲解员·顶层）

1. 证据锚点不认 `.mdx`：`build.py` 的 `REF_RE` 只收 `c|h|conf|tcl|md|json|py|sh|txt|lua|yml|yaml`，
   `note('文档', ..., ref='docs/docs/advanced_features/unified_memory_design.mdx:121')` 直接 BuildError，整页退回临时骨架。
   最小复现：任意 note 的 ref 写成 `x.mdx:1`。绕开：改引 `python/sglang/srt/arg_groups/fields/schedule.py:215` 的配置说明。
