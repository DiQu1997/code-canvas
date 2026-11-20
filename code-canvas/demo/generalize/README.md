# cfg 图元泛化样例

两份 zoom.json，用来检验 `cfg` 图元在 sglang-radix 之外的仓库上是否成立。
build 需要对应仓库源码（不入库）：

| 样例 | 仓库 @ commit | 语言 |
|---|---|---|
| `vllm-sched-zoom.json` | `vllm-project/vllm` @ `673618e` | Python |
| `redis-evict-zoom.json` | `redis/redis` @ `fd7710c` | C |

```bash
python3 build_zoom.py demo/generalize/redis-evict-zoom.json --repo <redis clone> --out /tmp/redis.html
```

行号钉死在上述 commit；换版本要重对行号。两份都没有 story / scene（只测模块层 cfg），
build 会报相应 WARN，属预期。redis 的 `performEvictions`（292 行）手工拆成了 4 张片段卡。
