# 精读带读视频（video.json）规格 v0.1

把一张深潜画布讲成一支 3Blue1Brown 风格的带读视频。**你只写脚本**：每一幕
一种画面 + 若干拍旁白；渲染器 `video.py` 负责动画、配音、字幕与音画同步。
代码一律由渲染器从画布 JSON 里**原文提取**——你只给卡 id 与行号，不许自己敲代码。

## 讲法（3Blue1Brown 的几条铁律）

1. **先具体后抽象**：开头 1 分钟内给出一个小到一眼看全的例子（3 条请求、
   预算 20），让读者先看见现象，再讲规则
2. **一幕一个想法**：一幕只回答一个问题；画面变化跟着旁白走，每一拍都要
   有东西在动（高亮移动、方块移位、数字变化、表格加行）
3. **先问后答**：幕首旁白抛出问题（"预算只有 20，谁先上？"），幕内回答
4. **代码是证据不是主角**：先用模拟画面讲清机制，再切代码幕"这就是刚才
   那一步在代码里的样子"，逐段高亮对照
5. **收尾回顾**：最后一幕用 3-5 条把全片串起来

## 结构

```jsonc
{
  "title": "vLLM 调度器：没有阶段，只有追赶",
  "canvas": "vllm-sched",            // 对应画布名
  "voice": "Tingting",               // 可省
  "scenes": [ { "kind": "...", ... , "beats": [ { "say": "旁白", ...本拍画面动作 } ] } ]
}
```

- 全片 8-14 幕，3-6 分钟。每拍 `say` 是**口语中文**，≤60 字（约 10 秒）；
  一拍一个意思，读出来顺口（别写括号、别写"如下表"）
- 每幕 2-6 拍；每拍都应改变画面（`title`/`idea` 的首拍除外）

## 幕的种类

### `title` 片头
`{"kind":"title","text":"主标题","sub":"副标题","beats":[{"say":"…"}]}`

### `idea` 一个大想法
`{"kind":"idea","text":"一句核心论断（≤40 字）","emph":["要标黄的短语"],"beats":[…]}`

### `flow` 流程骨架（逐行点亮）
`{"kind":"flow","items":["① …","② …"],"beats":[{"say":"…","item":0}, …]}`
`item` = 本拍点亮第几行（从 0 起）。items 每行 ≤26 字，3-7 行。

### `sim` 模拟（核心幕：让读者"看见"机制）
```jsonc
{"kind": "sim",
 "lanes": ["running", "waiting"],                 // 自上而下的队列道，1-3 条
 "budget": {"name": "token_budget", "total": 20}, // 顶部预算条（可省）
 "items": [ {"id": "R1", "lane": "running", "label": "39/40"},   // 初始方块，每道 ≤6 个
            {"id": "W1", "lane": "waiting", "label": "0/10"} ],
 "beats": [
   {"say": "R1 只差 1 个 token，排它 1 个。",
    "ops": [ {"op": "focus", "item": "R1"},
             {"op": "consume", "n": 1},                     // 预算条减 n
             {"op": "label", "item": "R1", "text": "40/40"} ]},
   {"say": "R2 要第三块却没有空闲块——抢占队尾的 R3。",
    "ops": [ {"op": "mark", "item": "R3", "state": "bad"},  // good | bad | done | none
             {"op": "move", "item": "R3", "lane": "waiting", "at": 0} ]}   // at: 插到第几位，省略=队尾
 ]}
```
op：`focus`（放大闪一下）、`consume`（扣预算）、`refund`（退预算）、`label`（改方块上的字）、
`mark`（上色）、`move`（换道/换位）、`add`（新方块 `{"op":"add","item":"W2","lane":"waiting","label":"…"}`）、
`remove`（移出画面）。**数字必须按代码规则算得出**——拿画布 overview.example 的数据最稳。

### `table` 账本（逐行出现）
`{"kind":"table","cols":["请求","已算/总长","本步排","token_budget"],"beats":[{"say":"…","row":["R1","39/40","1","20→19"],"hl":[3]}]}`
每拍加一行；`hl` = 本行要标黄的列下标。≤6 列、≤7 行。

### `code` 代码对照（原文，逐段高亮）
`{"kind":"code","card":"runb","lines":[1,14],"beats":[{"say":"…","hl":[3,6]}, …]}`
`card` 是画布里的卡 id，`lines` 是卡内行号区间（≤16 行）；每拍 `hl` 高亮 `lines`
范围内的一段。渲染器按卡的 `file` 行号显示真实文件行号。

### `recap` 回顾（逐条出现）
`{"kind":"recap","items":["…","…"],"beats":[{"say":"…","item":0}, …]}`

## 校验

`python3 video.py --check video.json canvas.json`：卡 id / 行号区间 / 高亮范围 /
sim 里的方块 id 与道名 / 表格列数 / 旁白长度——ERROR 必须清零。
