# lmcache-l1data-1：只重做「L1 存储」一个框（2026-11-28）

这不是一整单。它是 `runs/lmcache-skill-1/`（2026-11-24 那单）的对照版：交代里加了两句话
（源码页要同时给出逻辑和它读写的数据），然后**只重跑** `research-l1` 和 `present-l1` 两步，其余原样沿用。
来龙去脉和验收结论在 `demo/duo/README.md`「源码页补上『数据』」一节。

## 怎么做出来的

1. 把旧运行目录整个复制成新目录。
2. 拿走 L1 的步骤记录和产物（`logs/research-l1.*`、`logs/present-l1.*`、`research/manuscript-l1.md`、`content/content_l1.py`），
   `run.py` 看到这两步没有成功记录就会重做，别的步骤有记录就跳过。
3. 同一个问题、同一个仓库提交（`b27f6bd9e2`）再跑一次 `run.py`。

## 这个目录里有什么

| 文件 | 是不是这一轮新产生的 |
|---|---|
| `research/manuscript-l1.md` | 新（补充研究员，sonnet） |
| `content/content_l1.py`、`content/framework-issues-l1.md`、`content/top-fixes-l1.md` | 新（讲解员，opus） |
| `logs/research-l1.*`、`logs/present-l1.*` | 新（交代原文、回复原文、花费） |
| `logs/final-build.txt`、`logs/final-shots.txt` | 新（整张画布重新装配和自检） |
| `content/content_scene.py`、`content_xfer.py`、`content_sched.py`、`dives.json` | **沿用**，和 `runs/lmcache-skill-1/content/` 逐字节相同；放在这里只为了这个目录能独立重建 |
| `report.md`、`report.json`、`progress.txt` | 新写的，但见下面的提醒 |

顶层和另外两个框的底稿、交代、回复没有复制过来，看 `runs/lmcache-skill-1/`。

## 读 report.md 时的提醒

- 表里 9 步的花费合计 **$39.34**，其中 7 步是沿用旧记录，这一轮**没有重跑、没有花钱**。
  这一轮真正花的只有两步：`research-l1` $3.36 + `present-l1` $4.26 = **$7.62**。
- 「用时 37 分钟」是这一轮的真实墙钟（两步 + 装配 + 自检）。
- `present-top.fix` 那一行是旧记录。这一轮 L1 讲解员新写的 `top-fixes-l1.md`（1 条）**没有落实到顶层**：
  顶层那一步有成功记录，被跳过了。这是这种「只重做一个框」做法的已知缺口。
