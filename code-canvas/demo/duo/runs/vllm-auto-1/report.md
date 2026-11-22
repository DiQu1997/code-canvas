# 自动运行报告

- 问题：How does a request go through vllm and get the response back to client, step by step
- 仓库：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus3/vllm` @ `0e3c8dbdae`
- 模型配置：present_dive=opus，present_top=opus，research_dive=sonnet，research_top=opus
- 花费：**$32.64**（上限 $55）；用时 87 分钟

## 每一步

| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |
|---|---|---|---|---|---|
| research-top | claude-opus-5-5 | $7.52 | 14 分 | 118 | 成功 |
| present-top | claude-opus-5-5 | $2.76 | 11 分 | 29 | 成功 |
| research-sched | claude-sonnet-5-5 | $3.82 | 14 分 | 87 | 成功 |
| present-sched | claude-opus-5-5 | $5.63 | 25 分 | 37 | 成功 |
| research-gpu | claude-sonnet-5-5 | $3.47 | 12 分 | 94 | 成功 |
| research-gpu.fix | claude-sonnet-5-5 | $0.12 | 0 分 | 2 | 成功 |
| present-gpu | claude-opus-5-5 | $3.39 | 14 分 | 35 | 成功 |
| research-outproc | claude-sonnet-5-5 | $2.13 | 9 分 | 55 | 成功 |
| present-outproc | claude-opus-5-5 | $3.39 | 16 分 | 34 | 成功 |
| present-top.fix | claude-opus-5-5 | $0.40 | 1 分 | 5 | 成功 |

## 底稿核对（机械）

- 顶层：核对通过
- sched：核对通过
- gpu：核对通过
- outproc：核对通过

## 往下钻

- **调度器**（`sched`）：整台机器「这一步算谁、算几个 token、KV 放哪」都在这里决定；顶层只能画出三个数（本步排 / 已算 / 占位），里面是 token 预算、running/waiting 两个队列、KV block 分配、前缀缓存、抢占一整套在变的状态。
- **GPU 执行**（`gpu`）：async scheduling 能成立全靠这里：常驻请求状态、GPU 上的 last_sampled_tokens、主 stream 与 copy_stream、AsyncOutput 的 event。顶层只能说「前向 + 采样，异步拷回」，读者会想看一步之内 GPU 上到底排了哪些活、数据在哪几块显存之间流。
- **输出处理**（`outproc`）：token id 变成客户端看到的文字发生在这里：增量去分词、stop 串扣字、单槽信箱合并、反向 abort。里面有每请求状态在一步步变，顶层一张卡讲不出「文字为什么会晚几个字」「信箱怎么合并」。
- `sched` 下层内容文件：有
- `gpu` 下层内容文件：有
- `outproc` 下层内容文件：有

## 装配

- build --strict 退出码 0；WARN 0 条；未填槽位：无
- shot_scene --all：交互自检 644 PASS / 0 FAIL；排版问题 0 条；FAIL 0 条

## 各 agent 的最终回复

在 `logs/<步骤>.reply.md`（原文，未改）。
