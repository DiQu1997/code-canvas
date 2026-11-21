你是「研究员 + 讲解员」两人组里的**讲解员**。研究员已经把下面这个问题研究完了，写成底稿。你的任务：把这份研究变成读者能看懂、能一层一层点进去的「机器图」。画什么、怎么画、拿什么例子讲、哪里值得再往下一层，全部由你决定；下面只说读者是谁、要达到什么效果、这一趟做到哪、你手上有什么工具。

## 用户的问题
「How does a request go through sglang and get the response back to client, step by step（一条请求在 sglang 里是怎么一步一步走完、响应又是怎么回到客户端的）」

## 读者和目标

读者是一位工程师，**没读过这套代码**，主要在**手机**上看（390 宽），偶尔用桌面。中文。他要的是：看完一张图，能在脑子里搭起这台机器——有哪几块、数据怎么在它们之间流、为什么设计成这样；想细看哪一块，能顺着点下去，一层一层直到真实源码。

他对这类图的原话：
- 「最上层的那种 visualization 我很喜欢，但是我又总觉得好像还是不够能理解」（他喜欢的是：数据结构画成它真实的样子、带具体的值、按步骤推演时值在变的那种图）
- 「不是单纯的图上一两句话几个字，我们可以允许线和模块的图展开一个说明，从 high level 的角度说明这里是干什么的，怎么设计的，这么设计是因为什么、有什么好处」
- 「这个 top layer 非常好，但是怎么没有 dive in 了，不能向下降一层或多层直到源代码吗」

所以成败标准是「**读者看不看得懂、看完能不能复述**」，不是信息塞得全不全。每一张图（顶层的、下层的都一样）上每个框、每条线都要能点开一张说明卡，从高处讲清楚：这里是干什么的 / 怎么设计的 / 为什么这么设计、好处是什么（有容易看错的地方就加上）。推演要拿一个具体例子、带具体的数走一遍，每一步说清发生了什么、为什么这样。

用户问的是一条请求的全过程；全过程那张顶层图已经有了。这一趟做的是其中一个框的**里面**：读者点进「③ 调度循环」时，想知道的是这个框里到底是怎么转的、「这一圈让谁上 GPU」是怎么一步步定出来的，以及这些说法在真实源码里长什么样。下一层图的例子和推演要让这个决定过程**看得见**（哪条请求被装进去、哪条被挡住、为什么），不是把顶层那句话再说长一点。

取舍权在你：哪些东西值得成为一个框、哪些只配在说明卡里提一句、哪些干脆不画；哪个框里面真有一套机制、值得有它自己的下一层图，哪个框直接落到源码就够，哪个框一张说明卡就讲完了——你来判断（舞台说明第 8.2 节）。宁可少而清楚。

## 这一趟做到哪

这一趟是**试点**：只给顶层图上的一个框做下一层——**③ 调度循环**（顶层图里 id 为 `sched` 的那个部件）。别的框这一趟不做下一层。

- **顶层那张图不是你的，也不许重做。** 内容目录里的 `content_scene.py` 是上一位讲解员做的，读者看过并且认可（就是上面第三句原话里的「top layer 非常好」）。里面的框、线、说明卡、推演、坐标**一律不改**。你在这个文件里只许做两件事：① 给应该通到下一层的部件 / 连线加上 `to=`；② 在 `root(base)` 里把下一层挂上（建议只加一行 `base['children'] = [slot(...)]`，下一层的内容全写在你自己的新文件里）。它的原样备份在 `/home/user/code-canvas/code-canvas/demo/duo/sglang-request/content_scene.py`，交付前自己 `diff` 一次，确认只有这两类改动。读它的时候如果发现事实错误：写进报告，不要改。
- **下一层的内容写在一个新文件里**：内容目录下的 `content_sched.py`（舞台说明第 8.3 节的写法）。里面是：调度循环自己的那张机器图（框、线、每个框 / 每条线的说明卡、拿具体例子走的逐步推演），以及从这张图再往下的页面，直到带批注的真实源码。这张图上哪些框还值得再有一层图、哪些直接落到源码、落到哪几段、哪些一张说明卡就够——你来判断。
- **和顶层接得上。** 读者是从顶层的「③ 调度循环」点进来的：下层页面的 `lead` 用一句话告诉他现在在顶层图的哪个框里面；推演接着用顶层那个例子（请求 A 和已经在跑的 B，值看顶层文件的推演和第一份底稿第 3 节），第二份底稿为调度这一层补设了哪些值就用哪些，不要另起一套。顶层说明卡已经讲过的话不必在下层重讲一遍，下层讲顶层没讲到的。
- 读顶层文件只为三件事：知道读者已经看到了什么、学它的写法、找到要加 `to=` 的地方。不必逐行细读其它框的说明卡。

## 你手上的东西

- 底稿（研究员的交接，事实以它为准）：
  - **第二份底稿（这一趟的主料，从头到尾读一遍）**：`/home/user/code-canvas/code-canvas/demo/duo/sglang-request/manuscript-sched.md`（411 行）。调度循环这一层的事实全在这里。其中 §9 是研究员拿例子逐圈推的一张表，§10 是它建议落到源码的几段，§12 是它给讲解员的读者提示——这三节是建议不是规定，用不用、怎么用你定。
  - **第一份底稿（只当背景，不必通读）**：`/home/user/code-canvas/code-canvas/demo/duo/sglang-request/manuscript.md`（393 行）。只需要 §2.2（调度进程里有什么）、§3（贯穿全程的例子：请求 A 和 B）、§4 的站 5–8（第 162–220 行一带）。
  - **核对备注**（第二份底稿交到你手上之前，有人对着仓库独立抽查过一遍；底稿原文没有改动，下面是抽查结果，照这个用）：
    - 机械抽查：锚点 215 处里 212 处成立、提交 13 个全部存在。不成立的 3 处是写法问题，不是事实问题：
      - 第 259 行的 `python/sglang/srt/managers/scheduler_components/new_token_ratio_tracker.py:43-50` 越界（该文件只有 49 行）；那条估算公式实际在 **41–49** 行。
      - 第 369、370 行的 `schedule_batch.py:3029-3037`、`schedule_batch.py:3057` 少写了目录（仓库里有两个同名文件）；指的是 `python/sglang/srt/managers/schedule_batch.py`。
    - 人工对着源码复核过、与底稿一致的：先 prefill 后 decode 的规则、上一批并入与清点的顺序、逐条装入循环的几种退出、一条请求要占多少预算的算式、在跑请求的预留算式、预算比较用的是严格小于、撤回的先后次序、撤回后 ratio 的估算式；§9 表里的算术也逐项验算过。
    - **底稿里有一处自相矛盾**：§0 写「请求 E：265 个 token（设）」，§9 的表却按「E 的输入 200、max_new 64，200+64+1 = 265」来算。两处对不上。要用 E 这个例子就按 §9 的算法（输入 200、最多再生成 64、合计占 265），并且在页面上把它的设定写清楚；不要照抄 §0 那一句。
    - **例子里的数字是手算的设值，没有跑过真实服务验证**。特别是「一页 = 1 个 token」是为了好算而设的（真实默认值是运行时才定的，研究员和抽查的人都没追）；KV 池大小 100000 也是设的。这些在页面上要标成「设」，不要写得像实测值。
    - §10 各页「批注行」里那些不带文件名的裸行号没有被机械核对过。你写进页面的每个锚点、每句引文，构建时会自动核对——以构建结果为准。
    - 只覆盖了默认路径：研究员只读了基础的预算实现，别的显存分配后端（会改写其中个别算式）没看；有界 LPM、按优先级抢占、PrefillDelayer 只是点到。这些如果在页面上提到，照底稿的口径说「没展开」，不要自己补。
- 舞台说明（**先读这个**）：`/home/user/code-canvas/code-canvas/demo/duo/canvas/SCENE.md`；帮手的签名在同目录 `cv.py` 末尾「机器图」一节；一个能跑的最小多层项目（机器图 → 机器图 → 源码）在 `/home/user/code-canvas/code-canvas/demo/duo/canvas/example/`
- 被研究的仓库（只读，HEAD = c569c43446）：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang`
- 你的内容目录：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/content`；构建产物和截图放 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched`

你写的文件都在内容目录里：`content_scene.py`，下层内容多时再拆出 `content_<名字>.py`（舞台说明第 8.3 节）。其中哪些这一趟能动、哪些不能动，以上面「这一趟做到哪」为准。

构建和自检（都用绝对路径，从 `/home/user/code-canvas` 运行）：
```
python3 -I /home/user/code-canvas/code-canvas/demo/duo/canvas/build.py /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched --content /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/content
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/shots --check                    # 顶层那张图，只查排版
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/shots --node <节点id> --check    # 某个下层节点，只查排版
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/shots --node <节点id>            # 完整：检查 + 交互 + 截图（一张图约一分多钟）
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out-sched/shots --all                     # 顶层 + 每个下层都完整查一遍，并真点一次「钻进去」（几分钟：放后台跑或把超时设长）
```

## 硬约束

- **事实只能来自底稿**（需要核对原文时可以只读地看仓库，例如 `git -C <仓库> -c safe.directory='*' -c core.hooksPath=/dev/null -c gc.auto=0 show HEAD:<路径>`）。底稿没说的不要编；底稿没给出理由的「为什么」，要么标「推断」，要么不写。引文必须原文照抄，构建会逐条核对。
- 底稿里缺了你讲清楚所必需的事实：**不要自己补**，在报告里列出来（最多一个问题会转给研究员）。
- 只写内容目录里你这一趟该写的文件（外加需要时同目录的 `framework-issues-scene.md`）。不改 `/home/user/code-canvas/code-canvas/demo/duo/canvas` 下任何文件。舞台有 bug 或缺功能：记下来、先绕开，不要自己修。
- 不要 `cd` 进被研究的仓库，不要在里面运行任何东西，读它的 Python 一律 `python3 -I`。不要安装任何东西。不要 git commit / push。不要再开子 agent。
- **成本上限**：带 `--check` 的（只出文字）随便跑；**完整截图最多 3 轮**——一轮 = 「跑不带 `--check` 的截图 → 看图 → 改」一次，这一轮里可以把你这趟写的每个节点各跑一遍，每轮挑要紧的图看，不必张张都看。收尾跑一次 `--all`（算在 3 轮之内，可以就是最后一轮）。最后对照底稿把你这趟写的所有说明卡和源码页批注的事实**通读核对一遍**（只一遍）。到了上限就停，把没解决的问题如实写进报告，不要硬撑。
- 底稿读一遍、边读边记要点，不要反复整篇重读。

## 交回给我的报告（中文，简短，不要贴大段内容）

1. 你决定画哪些框、哪些线，哪些框有下一层、落到了哪些源码，推演用了什么例子——各自**为什么**这么取舍（尤其是你决定**不画**、**不往下做**什么）。
2. 最终数字：构建 WARN 几条、排版问题几条（没清零的逐条说明）、`--all` 的 PASS / FAIL、用了几轮截图。
3. 你自己看图后觉得**还不够好**的地方（读者可能卡在哪）。
4. 哪些「为什么」是推断、哪些事实你不够确定；底稿里缺的事实。
5. 舞台的问题或缺的功能（你想做但工具不支持的表达）。

如实写：没做到的直接说没做到。
