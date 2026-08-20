# Code Canvas — JSON schema v0.2

一份 canvas JSON 渲染成一个自包含 HTML。渲染器负责精确布局（测量代码行宽、
排列列与行带、包住区域、计算镜头）；agent 只给**粗位置**和内容。

三种画布模式（`meta.mode`）：省略 = 阅读画布；`"diff"` = 复盘实际改动；
`"plan"` = **计划画布**——agent 动手改代码之前先画"打算怎么改"，作者在
画布上审批。计划与 diff 用**同一张底图**（相同卡 id 与 layout），
`compare.py` 机械对照两者，"说好动三张卡、实际动了四张"一眼可见。

```jsonc
{
  "meta": {
    "title": "Code Canvas",          // 左上角标题
    "subtitle": "orders 服务 · 写路径", // 标题旁的小字
    "audience": "新接手的后端；中文；重点显存" // 可选：这张图为谁生成。
                                     // 生成时所有文字（caption/说明/note/term）
                                     // 的深度、关注点、语言风格按它来写
  },

  "regions": [                        // 故事线区域（着色框）
    {
      "id": "A",
      "title": "故事线 A · 请求主流程", // 标签胶囊
      "blurb": "请求的进与出",          // 标签下一句话，≤20 字，可省略
      "hue": "blue",                  // blue | green | violet | amber
      "cards": ["handler", "fetch"]   // 成员卡片；区域矩形自动包住它们
    }
  ],

  "cards": [
    {
      "id": "handler",
      "name": "handle_request(req)",  // 头部签名
      "file": "server/handler.py",
      "lang": "py",                   // py | js | 其他(退化为普通高亮)
      "layout": { "col": 0, "band": 0 }, // 粗位置：列(0..n 左→右)、行带(0..n 上→下)
      "collapsed": false,             // 默认折叠成签名条？重点卡 false
      "code": "def handle_request(req):\n    ...", // 原文，行号从 1 起
      "blocks": [                     // 可选：长函数的逻辑块（行段树，非 AST）
        {
          "name": "水位检查",          // 块名（色条上）
          "summary": "低于高水位就什么都不做", // 一句话功能，≤20 字
          "lines": [2, 4],            // 闭区间行号
          "folded": true,             // 默认折叠成大纲条
          "explain": "为什么这么写……",  // 可选：块的展开解释（≤120 字），
                                      // 色条出现「说明」按钮，点击展开
          "children": [ /* 嵌套子块，行段必须落在父块内 */ ]
        }
      ],
      "diff": {                       // 可选：diff 画布的变更标记
        "added": [7, 8],              // head 行号（现行行号），渲染绿条 + 底色
        "removed": [                  // 被删原文：红色删除线展示，
          { "after": 6,               // 显示在 head 第 6 行之后（0 = 卡首）
            "code": "    resp = fetch_upstream(req)" }
        ]                             // removed 行不参与行号与任何锚点
      },                              // 卡头自动出 +n −m 徽标；图例自动补色样
      "plan": "modify",               // 仅 mode:"plan"：这张卡的变更意图
                                      // "add"    = 幽灵卡：将新建的函数。虚线框 +「新建」徽标。
                                      //            code 是计划中的签名/骨架草图（此函数尚不存在，
                                      //            "代码必须是原文"对幽灵卡自然不适用，渲染上
                                      //            用虚线明示这一点）
                                      // "modify" = 将改这张卡（「将改」琥珀徽标）
                                      // "remove" = 将删这张卡（「将删」红徽标）
                                      // 没有 plan 字段 = 计划不动它（上下文卡）。
                                      // 为什么这么改：写进 note / step caption，不塞进卡头
      "plan_delta": "offplan",        // 仅 mode:"diff"，通常由 compare.py --annotate 注入：
                                      // "offplan" = 计划没说要动、实际动了（⚠ 计划外 红徽标）
                                      // "missed"  = 计划说要动、实际没动（○ 计划未动 灰徽标）
      "terms": [                      // 可选：变元注释——不好懂的标识符
        { "line": 3,                  // 卡片内行号
          "token": "num_batched_tokens", // 该行中的标识符（整词匹配首次出现）
          "note": "本批已占用的 token 预算……" } // ≤60 字；渲染为虚线下划线，点击行下展开
      ]
    },
    { "id": "kv-pool",                // ---- 实例快照卡：数据结构的具象例子 ----
      "kind": "state",                // 教科书画法：画一个真实感的实例，随步进演化。
      "name": "KV 块池 · 实例",       // 渲染器对相邻状态做 cell 级 diff：变更琥珀、新增绿
      "file": "engine/block_manager.py",
      "lang": "py", "layout": { "col": 3, "band": 2 },
      "nodes": [                      // 三种节点：array（格子行）| map | record（k→v 行）
        { "id": "fq",  "kind": "array",  "label": "free_block_queue" },
        { "id": "b7",  "kind": "record", "label": "Block 7（共享前缀）" }
      ],
      "states": [                     // 每个状态 = 某一步之后的全量值（按 step 递增）。
        { "step": 1,                  // 首个状态是基线（不 diff）；步进走到 state.step
          "nodes": {                  // 时切换并高亮与上一状态的差异，note 一句话说变化
            "fq": { "cells": [2, 5, 8, 9] },
            "b7": { "rows": [["ref_count", 1], ["hash", "h(sys)"]] } } },
        { "step": 3, "note": "前缀命中：7 号块复用，ref+1；新块 2 出队",
          "nodes": {
            "fq": { "cells": [5, 8, 9] },
            "b7": { "rows": [["ref_count", 2], ["hash", "h(sys)"]] } } }
      ]                               // 快照与代码行高亮 + caption 构成同屏三角：
    }                                 // 谁干的（行）/ 干了什么（格子变色）/ 为什么（caption）
  ],

  "wires": [                          // 策展的线：只画对故事重要的
    { "id": "w-key",  "kind": "call", // call=调用/执行(灰) | data=数据流(琥珀)
      "from": { "card": "handler", "line": 2 },        // line 可省 → 卡片边缘
      "to":   { "card": "cachekey" } },                // side 可选 left|right
    { "id": "d-key",  "kind": "data", "label": "key",  // data 线建议带值名
      "from": { "card": "cachekey", "line": 5 },
      "to":   { "card": "get" } },
    { "id": "p-jit",  "kind": "call", "plan": "add",   // 仅 mode:"plan"：计划加线（虚绿）；
      "from": { "card": "handler" },                   // "remove" = 计划删线（虚红）
      "to":   { "card": "jittered" } },
    { "id": "s-own",  "kind": "struct",                // 结构关系线（紫虚点）：结构间的
      "label": "含 N 个",                              // 含/引用/索引关系，label 必带。
      "from": { "card": "scheduler", "line": 3 },      // 一样是策展的——不画全量 ER
      "to":   { "card": "request" } }
  ],

  "notes": [
    { "id": "bg0", "flavor": "bg",      // bg=背景(青) | intent=意图/不变量(琥珀)
      "tag": "背景 · 这是什么",
      "text": "……",                     // bg 画布级 ≤80 字；概念 ≤60 字；intent ≤60 字
      "place": { "corner": "nw" },      // 画布级背景卡：西北角
      "step": 0 },                      // 在哪一步点亮（0=总览）
    { "id": "n1", "flavor": "intent",
      "tag": "NOTE · handler.py:6",
      "text": "……",
      "anchor": { "card": "handler", "line": 6 },  // 引线锚到具体行
      "place": { "side": "left", "of": "handler" },// 摆放：卡片左/上 (left|above)
      "step": 2 }
  ],

  "files": {                          // 可选：卡片引用文件的全文（路径 → 原文）。
    "server/handler.py": "…全文…"      // 有此映射时，对应卡片出「▲ 上文 / ▼ 下文」
  },                                  // 展开条（±20 行/次，可收起）：摘选保持策展
                                      // 密度，整个文件在卡里待命。上下文行淡化无
                                      // 行号，锚点全部钉在摘选行上不受影响。
                                      // extract.py --embed-context 可机械生成
  "steps": [                          // 镜头路径；第 0 步通常是总览
    // 每步可标 "storyline": "<region id>" —— 步进点按故事线着色、标题带色签、
    // 点画布上的区域标签跳到该故事线第一步。叙事顺序不必按故事线分组，
    // 一条故事线可以分几段出现（先讲 A 的骨架，最后回到 A 收尾）。
    { "title": "总览", "caption": "……", "fit": true },
    { "title": "① 请求如何变成 key", "storyline": "B",
      "caption": "……",                 // ≤80 字："这步看哪、注意什么"的一眼镜头
      "detail": "……",                  // 可选，≤300 字：底栏「详解」按钮点开才显示。
                                      // 讲这步代码在干什么/为什么这么设计/容易误解
                                      // 什么；不复述 caption，不贴代码
      "wires": ["w-key", "d-key"],    // 本步点亮的线；其余淡出
      "lines": [["handler", 2]],      // 本步高亮的行
      "expand": ["cachekey"],         // 本步自动展开的卡
      "unfold": [["evict", "全表扫描打分"]], // 本步自动展开的块（[卡 id, 块名]）
      "focus": ["handler", "cachekey", "n3"], // 取景元素（卡/note id）；镜头自动计算。
                                      // focus 的书写顺序 = 本步的阅读顺序：焦点元素
                                      // ≥2 个时渲染器在各元素左上角钉 ①②③ 序号标，
                                      // 随步进更换——所以按"先读哪个"排 focus
      "order": ["cachekey", "handler"] } // 可选：阅读顺序与取景顺序不一致时显式覆盖
  ]
}
```

## 渲染器行为（约定）

- **布局**：列 x 由该列最宽卡片决定（列间距固定）；带 y = 带号 × 带高，
  同列冲突自动下推。区域矩形 = 成员卡片 bbox + padding，实时跟随展开/折叠。
- **锚点级联**：线/引线锚到行；行被折叠时退到所在块的大纲条；卡片折叠时退到卡片边缘。
- **块颜色**：渲染器按层深自动从两套色板分配，agent 不指定颜色。
- **镜头**：`focus` 元素的 bbox 决定中心与缩放；`fit` 取全图。
- **交互**：拖拽平移、滚轮缩放、点卡头折叠/展开、点块条折叠/展开、
  块条「说明」展开解释、虚线标识符点击展开变元注释、
  ◀▶/←→ 步进、点区域标签跳故事线。
  `#s2` 直达步骤；调试尾缀：`x` 全展开、`e` 开全部说明、`t` 开全部变元注释、
  `q` 打开问答抽屉。
- **块级问答**：每根块条有「问」按钮，打开侧边抽屉就该块提问。页面通过
  `/__alive` 探测 `serve.py`（`python3 serve.py 画布.html`，桥接 `claude -p`
  或 `codex exec`）；无服务时降级为把「问题 + 块代码 + 上下文」复制到剪贴板。
  问答历史存 localStorage，在线模式另存 sidecar `<html>.qa.json`。
- **阅读画像与个性化重讲**：右上角「阅读画像」让读者写下自己是谁、想懂什么、
  偏好什么讲法（自由文本，存本机 localStorage）。设置后：① 块问答的提示词
  自动携带画像，答案按人调整；② 每条「说明」与变元注释出现「按我的画像
  重讲」——把原文 + 代码上下文 + 画像发给 agent 现场改写，结果带
  「已按画像重讲」徽标、可一键还原原文，并按 (画像, 元素) 缓存。
  生成时的默认文字始终保留——个性化是叠加视图，不是覆盖。

## 硬约束（agent 侧，渲染器不校验但 review 时执行）

- 每步点亮的线 ≤ 5，邻域卡片 ≤ 4，行高亮 ≤ 6
- 每卡 intent note ≤ 3；文字上限见字段注释，写不下说明还没想清楚
- 代码行 ≤ 60 字符（更长的行考虑换行重排或截选）
- 块必须是"一句话说得清功能"的行段；不做 AST 粒度
- data 线只画故事需要的"值的旅程"；call 线是骨架但同样只画在场的
