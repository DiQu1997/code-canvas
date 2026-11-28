# 自动运行报告

- 问题：LMCache 是怎么把 KV cache 存下来、下一个请求又怎么取回来复用的，一步步讲
- 仓库：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus5/LMCache` @ `b27f6bd9e2`
- 模型配置：present_dive=opus，present_top=opus，research_dive=sonnet，research_top=opus
- 花费：**$39.34**（上限 $55）；用时 37 分钟

## 每一步

| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |
|---|---|---|---|---|---|
| research-top | claude-opus-5-5 | $10.30 | 20 分 | 128 | 成功 |
| present-top | claude-opus-5-5 | $3.54 | 14 分 | 35 | 成功 |
| research-xfer | claude-sonnet-5-5 | $3.71 | 15 分 | 89 | 成功 |
| present-xfer | claude-opus-5-5 | $4.24 | 17 分 | 45 | 成功 |
| research-l1 | claude-sonnet-5-5 | $3.36 | 11 分 | 80 | 成功 |
| present-l1 | claude-opus-5-5 | $4.26 | 19 分 | 41 | 成功 |
| research-sched | claude-sonnet-5-5 | $3.30 | 12 分 | 71 | 成功 |
| present-sched | claude-opus-5-5 | $4.79 | 20 分 | 41 | 成功 |
| present-top.fix | claude-opus-5-5 | $1.84 | 1 分 | 8 | 成功 |

## 底稿核对（机械）

- 顶层：核对通过
- xfer：核对通过
- l1：核对通过
- sched：核对通过

## 往下钻

- **传输模块**（`xfer`）：这是 KV 真正被搬动的地方：一次 STORE / RETRIEVE 里有自己的 CUDA 流、跨进程 event、staging 对象、流回调和亲和线程，状态随时间在变，顶层只能讲到顺序。
- **L1 存储**（`l1`）：里面有一套对象状态机（staging → ready → read_locked）、带 TTL 的读写锁、读者计数和后台淘汰，正是「存下来、锁住、不被挤掉」的机制所在，顶层只画了结果。
- **调度侧连接器**（`sched`）：每个请求的 tracker 状态机、「查多少 / 存多少」的算术、LOOKUP 的异步轮询状态和放锁时机都在这里，跨多个调度步才走完，读者最容易在这里迷路。
- `xfer` 下层内容文件：有
- `l1` 下层内容文件：有
- `sched` 下层内容文件：有

## 装配

- build --strict 退出码 0；WARN 0 条；未填槽位：无
- shot_scene --all：交互自检 566 PASS / 0 FAIL；排版问题 0 条；FAIL 0 条

## 各 agent 的最终回复

在 `logs/<步骤>.reply.md`（原文，未改）。
