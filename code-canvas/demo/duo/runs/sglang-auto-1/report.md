# 自动运行报告

- 问题：How does a request go through sglang and get the response back to client, step by step（一条请求在 sglang 里是怎么一步一步走完、响应又是怎么回到客户端的）
- 仓库：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang` @ `c569c43446`
- 模型配置：present_dive=opus，present_top=opus，research_dive=sonnet，research_top=opus
- 花费：**$45.69**（上限 $50）；用时 8 分钟

## 每一步

| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |
|---|---|---|---|---|---|
| research-top | claude-opus-5-5 | $11.57 | 23 分 | 139 | 成功 |
| present-top | claude-opus-5-5 | $3.65 | 15 分 | 40 | 成功 |
| research-sched_loop | claude-sonnet-5-5 | $4.40 | 14 分 | 111 | 成功 |
| present-sched_loop | claude-opus-5-5 | $5.41 | 26 分 | 46 | 成功 |
| research-kv_cache | claude-sonnet-5-5 | $3.93 | 16 分 | 93 | 成功 |
| present-kv_cache | claude-opus-5-5 | $4.31 | 23 分 | 43 | 成功 |
| research-model_fwd | claude-sonnet-5-5 | $6.76 | 21 分 | 159 | 成功 |
| present-model_fwd | claude-opus-5-5 | $5.54 | 27 分 | 47 | 成功 |

## 底稿核对（机械）

- 顶层：核对通过
- sched_loop：核对通过
- kv_cache：核对通过
- model_fwd：核对通过

## 往下钻

- **调度循环**（`sched_loop`）：整台机器的心脏：一条请求在这里被反复调度，overlap 让「发射」和「处理结果」差一圈，读者最容易在这里卡住；顶层只画成一个框，里面有等待队列、running batch、result_queue、future_map 几样状态在每圈变化。
- **KV 缓存 + 前缀树**（`kv_cache`）：「命中 5 个、只算 15 个」「结束时顺手插进树」是这条请求省掉算力的地方；里面有三层存储（req_to_token、分配器、前缀树）和匹配 / 分配 / 插入 / 淘汰一整套机制，顶层只讲到高层。
- **模型前向 + 采样**（`model_fwd`）：顶层只说了「ScheduleBatch → ForwardBatch → logits → 采样」；里面有 CUDA graph 回放 / prefill 分段图 / eager 的分叉、attention backend 和采样器，读者想知道 GPU 那一步到底怎么跑。
- `sched_loop` 下层内容文件：有
- `kv_cache` 下层内容文件：有
- `model_fwd` 下层内容文件：有

## 装配

- build --strict 退出码 0；WARN 0 条；未填槽位：无
- shot_scene --all：交互自检 456 PASS / 0 FAIL；排版问题 0 条；FAIL 0 条

## 各 agent 的最终回复

在 `logs/<步骤>.reply.md`（原文，未改）。
