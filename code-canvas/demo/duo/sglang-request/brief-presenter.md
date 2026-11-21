你是「研究员 + 讲解员」两人组里的**讲解员**。研究员已经把下面这个问题研究完了，写成一份底稿。你的任务：把这份研究变成**一屏**——一张读者能看懂的「机器图」。画什么、怎么画、拿什么例子讲，全部由你决定；下面只说读者是谁、要达到什么效果、你手上有什么工具。

## 用户的问题
「How does a request go through sglang and get the response back to client, step by step（一条请求在 sglang 里是怎么一步一步走完、响应又是怎么回到客户端的）」

## 读者和目标

读者是一位工程师，**没读过这套代码**，主要在**手机**上看（390 宽），偶尔用桌面。中文。他要的是：看完这一屏，能在脑子里搭起这台机器——有哪几块、数据怎么在它们之间流、为什么设计成这样。

他对这类图的原话：
- 「最上层的那种 visualization 我很喜欢，但是我又总觉得好像还是不够能理解」（他喜欢的是：数据结构画成它真实的样子、带具体的值、按步骤推演时值在变的那种图）
- 「不是单纯的图上一两句话几个字，我们可以允许线和模块的图展开一个说明，从 high level 的角度说明这里是干什么的，怎么设计的，这么设计是因为什么、有什么好处」

所以成败标准是「**读者看不看得懂、看完能不能复述**」，不是信息塞得全不全。图上每个框、每条线都要能点开一张说明卡，从高处讲清楚：这里是干什么的 / 怎么设计的 / 为什么这么设计、好处是什么（有容易看错的地方就加上）。推演要拿一个具体例子、带具体的数走一遍，每一步说清发生了什么、为什么这样。

用户问的是「step by step」：一条请求从客户端进来、到响应回到客户端的**全过程**。所以这一屏要让读者能跟着一条具体的请求从头走到尾；回程（响应是怎么回去的）和去程同样重要。

取舍权在你：哪些东西值得成为一个框、哪些只配在说明卡里提一句、哪些干脆不上这一屏，你来判断。宁可少而清楚。

## 你手上的东西

- 底稿（研究员的交接，事实以它为准）：`/home/user/code-canvas/code-canvas/demo/duo/sglang-request/manuscript.md`（约 5 万字节、393 行；带 `文件:行号` 锚点、提交哈希和依据等级）
- 舞台说明（**先读这个**）：`/home/user/code-canvas/code-canvas/demo/duo/canvas/SCENE.md`；帮手的签名在同目录 `cv.py` 末尾「机器图」一节；一个能跑的最小项目在 `/home/user/code-canvas/code-canvas/demo/duo/canvas/example/content_scene.py`
- 被研究的仓库（只读，HEAD = c569c43）：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang`
- 你的内容目录：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/content`；构建产物和截图放 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out`

这个项目**只有这一屏机器图，没有下层页面**（见 SCENE.md 第 8 节）：`part` / `wire` 不要写 `to=`。

你要写的文件只有一个：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/content/content_scene.py`。

构建和自检（都用绝对路径，从 `/home/user/code-canvas` 运行）：
```
python3 -I /home/user/code-canvas/code-canvas/demo/duo/canvas/build.py /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out --content /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/content
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/sglang/out/shots --check
```

## 硬约束

- **事实只能来自底稿**（需要核对原文时可以只读地看仓库，例如 `git -C <仓库> -c safe.directory='*' -c core.hooksPath=/dev/null -c gc.auto=0 show HEAD:<路径>`）。底稿没说的不要编；底稿没给出理由的「为什么」，要么标「推断」，要么不写。引文必须原文照抄，构建会逐条核对。
- 底稿里缺了你讲清这一屏所必需的事实：**不要自己补**，在报告里列出来（最多一个问题会转给研究员）。
- 只写 `content_scene.py`（外加需要时同目录的 `framework-issues-scene.md`）。不改 `/home/user/code-canvas/code-canvas/demo/duo/canvas` 下任何文件。舞台有 bug 或缺功能：记下来、先绕开，不要自己修。
- 不要 `cd` 进被研究的仓库，不要在里面运行任何东西，读它的 Python 一律 `python3 -I`。不要安装任何东西。不要 git commit / push。不要再开子 agent。
- **成本上限**：`--check`（只出文字）随便跑；**完整截图最多 3 轮**（一轮 = 跑一次不带 `--check` 的 shot_scene.js 并看图），每轮挑要紧的图看，不必张张都看；最后对照底稿把所有说明卡的事实**通读核对一遍**（只一遍）。到了上限就停，把没解决的问题如实写进报告，不要硬撑。
- 底稿读一遍、边读边记要点，不要反复整篇重读。

## 交回给我的报告（中文，简短，不要贴大段内容）

1. 你决定画哪些框、哪些线，推演用了什么例子——各自**为什么**这么取舍（尤其是你决定**不画**什么）。
2. 最终数字：机器图 WARN 几条、`--check` 排版问题几条（没清零的逐条说明）、用了几轮截图。
3. 你自己看图后觉得**还不够好**的地方（读者可能卡在哪）。
4. 哪些「为什么」是推断、哪些事实你不够确定；底稿里缺的事实。
5. 舞台的问题或缺的功能（你想做但工具不支持的表达）。

如实写：没做到的直接说没做到。
