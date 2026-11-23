你是「研究员 + 讲解员」两人组里的**讲解员**。研究员已经把下面这个问题研究完了，写成底稿。你的任务：把这份研究变成读者能看懂、能一层一层点进去的「机器图」。画什么、怎么画、拿什么例子讲、哪里值得再往下一层，全部由你决定；下面只说读者是谁、要达到什么效果、这一趟做到哪、你手上有什么工具。

## 用户的问题
「我想要理解给定一个repo link和用户query，画板是如何一步步生成的」

## 读者和目标

读者是一位工程师，**没读过这套代码**，主要在**手机**上看（390 宽），偶尔用桌面。中文。他要的是：看完一张图，能在脑子里搭起这台机器——有哪几块、数据怎么在它们之间流、为什么设计成这样；想细看哪一块，能顺着点下去，一层一层直到真实源码。

他对这类图的原话：
- 「最上层的那种 visualization 我很喜欢，但是我又总觉得好像还是不够能理解」（他喜欢的是：数据结构画成它真实的样子、带具体的值、按步骤推演时值在变的那种图）
- 「不是单纯的图上一两句话几个字，我们可以允许线和模块的图展开一个说明，从 high level 的角度说明这里是干什么的，怎么设计的，这么设计是因为什么、有什么好处」
- 「这个 top layer 非常好，但是怎么没有 dive in 了，不能向下降一层或多层直到源代码吗」

所以成败标准是「**读者看不看得懂、看完能不能复述**」，不是信息塞得全不全。每一张图（顶层的、下层的都一样）上每个框、每条线都要能点开一张说明卡，从高处讲清楚：这里是干什么的 / 怎么设计的 / 为什么这么设计、好处是什么（有容易看错的地方就加上）。推演要拿一个具体例子、带具体的数走一遍，每一步说清发生了什么、为什么这样。

用户的问题就是这一屏要回答的：让读者能跟着一个具体的例子，把这个问题从头走到尾。

取舍权在你：哪些东西值得成为一个框、哪些只配在说明卡里提一句、哪些干脆不画；哪个框里面真有一套机制、值得有它自己的下一层图，哪个框直接落到源码就够，哪个框一张说明卡就讲完了——你来判断（舞台说明第 8.2 节）。宁可少而清楚。

## 这一趟做到哪

这一趟做**顶层那一屏**，并决定哪些框值得有自己的下一层。

- 顶层写在内容目录的 `content_scene.py`（舞台说明第 8 节）：一张机器图 + 每个框 / 每条线的说明卡 + 拿底稿里那个例子走的逐步推演。
- 某个框里面就是**一段从头读到尾的代码**、底稿给了锚点：这一趟就可以直接给它挂源码页（`children` 里放 `node(...)`，`blocks` 里放 `code(...)`），部件上写 `to=`。
- 某个框里面**真有一套机制**（几个部件、数据在里面流、有自己的状态在变），但底稿只讲到高层、不够撑起它自己的一张图：**不要硬做**。给它留一个槽位 `slot('<槽位id>', '<标题>')`（放在 `root(base)` 的 `children` 里），在那个部件上写 `to='<槽位id>'`，然后在内容目录的 `dives.json` 里给它写一条**申请**。你交卷后，系统会为每条申请派一位研究员专门研究这个框，再派一位讲解员做它的下一层（机器图 → 源码）。
  - 最多 3 条，按重要性排。宁缺毋滥：只申请读者最想点进去、而且里面确实有一套机制的框。一条都不值得就写 `[]`。
  - 格式（JSON 数组，每条一个对象）：
    ```
    [{"id": "槽位id（字母 / 数字 / 下划线）",
      "title": "这个框的标题（和图上一致）",
      "part": "顶层图上那个部件的 id",
      "priority": 1,
      "why": "为什么这个框值得有下一层（一两句）",
      "ask": ["读者点进去会问的问题，用读者的口吻，3–8 个；只写问题，不要替研究员写答案"],
      "anchors": ["底稿里和这个框相关的锚点，形如 文件:行"],
      "sections": "顶层底稿里和这个框相关的小节，例如 §2.2、§4 第 3 步"}]
    ```
  - 自检时这些槽位还是「未展开」，构建会报「未填槽位」，这是正常的。
- `to=` 只指向你这一趟挂上的源码页，或者 `dives.json` 里申请了的槽位。

## 你手上的东西

- 底稿（研究员的交接，事实以它为准）：
  - `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/research/manuscript.md`
- 舞台说明（**先读这个**）：`/home/user/code-canvas/code-canvas/demo/duo/canvas/SCENE.md`；帮手的签名在同目录 `cv.py` 末尾「机器图」一节；一个能跑的最小多层项目（机器图 → 机器图 → 源码）在 `/home/user/code-canvas/code-canvas/demo/duo/canvas/example/`
- 被研究的仓库（只读，HEAD = 79153e7017）：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus4/code-canvas`
- 你的内容目录：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/content`；构建产物和截图放 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top`

你写的文件都在内容目录里：`content_scene.py`，下层内容多时再拆出 `content_<名字>.py`（舞台说明第 8.3 节）。其中哪些这一趟能动、哪些不能动，以上面「这一趟做到哪」为准。

构建和自检（都用绝对路径，在哪个目录下运行都行）：
```
python3 -I /home/user/code-canvas/code-canvas/demo/duo/canvas/build.py /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus4/code-canvas /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top --content /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/content
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/shots --check                    # 顶层那张图，只查排版
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/shots --node <节点id> --check    # 某个下层节点，只查排版
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/shots --node <节点id>            # 完整：检查 + 交互 + 截图（一张图约一分多钟）
node /home/user/code-canvas/code-canvas/demo/duo/canvas/shot_scene.js /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/scene.html /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/runs/canvas-hub-1/out/present-top/shots --all                     # 顶层 + 每个下层都完整查一遍，并真点一次「钻进去」（几分钟：放后台跑或把超时设长）
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