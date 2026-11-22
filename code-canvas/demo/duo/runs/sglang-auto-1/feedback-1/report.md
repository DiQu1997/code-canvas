# 改版报告

- 反馈：来进行微调，这个图里的各种推断神什么的最好是隐藏的，然后对应的行高亮，可以点开，旁边弹出一个文字框，就像pdf里的note那种可以点开显示解释内容，这样可以不影响主段落的阅读流畅性
- 截图：`/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/revise/1/feedback-1.png`
- 基线提交：`69d4c03`；运行目录：`/home/user/code-canvas/code-canvas/demo/duo/runs/sglang-auto-1`
- 花费：**$3.26**；用时 43 分钟

| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |
|---|---|---|---|---|---|
| reviser | claude-opus-5-5 | $3.26 | 29 分 | 61 | 成功 |

## 改动（git status，改版员交卷时）

     M code-canvas/demo/duo/canvas/SCENE.md
     M code-canvas/demo/duo/canvas/shot_scene.js
     M code-canvas/demo/duo/canvas/shot_zoom.js
     M code-canvas/demo/duo/canvas/template_zoom.html

## 把关

**通过，重建的页面已拷回 demo 目录**

```
redis-eviction-v1.html         没变       结论：没有控制台错误，没有横向溢出  [shot.js root main pool.demo loop.take]
redis-eviction-v2-zoom.html    有变化      36 PASS / 0 FAIL  [shot_zoom.js]
redis-eviction-v3-scene.html   有变化      41 PASS / 0 FAIL  [shot_scene.js]
sglang-request-v3-scene.html   有变化      31 PASS / 0 FAIL  [shot_scene.js]
sglang-request-v4-dive.html    有变化      308 PASS / 0 FAIL  [shot_scene.js --all]
sglang-request-v5-auto.html    有变化      832 PASS / 0 FAIL  [shot_scene.js --all]

已把 6 个重建页面拷回 demo 目录

回归（全档）：6 个页面，全部通过
```

## 改版员的报告（原文）

## 改版报告

**1. 判断：这是呈现的事，内容不用动**
作者嫌的是批注的显示方式：代码行之间夹着一张张批注卡，打断读代码。批注本身的文字他没说有问题，所以只改舞台。内容里也没有「同上」「见上一条」这类单独点开会看不懂的写法，内容目录一个字没动。

**2. 改了哪些文件**
- **`canvas/template_zoom.html`**（`scene.html` 和 `zoom.html` 共用的模板），规则对所有代码块的批注一样，没有针对某个仓库或节点写特例：
  - 钉在代码行上的批注默认收起。读者只看到那几行染成黄色，行尾有个小「注」标记，鼠标悬停时整行可点。
  - 点那几行，批注像 PDF 注释一样浮在这几行下方：桌面靠右，宽度最多 460px；手机占满代码宽度。它盖在下面的代码上，不把代码挤开，那几行同时加深、左边出一道色条。
  - 关掉的方式：点 ✕、按 Esc、点页面别处，或再点一次那几行。便签要是超出屏幕底部，镜头会轻轻往上移一点。
  - 代码块标题栏原来的「只看代码」改成「展开全部批注 / 收起批注」，想把批注全摊开的人还能用以前的读法。
  - 从「去看：…」链接直达一条代码批注时，会自动弹出它的便签并闪一下。
  - 代码之外的独立批注、推演条、说明卡里的批注都没变。
- **`canvas/shot_scene.js`**：新增「代码批注便签」这一组检查，见第 3 节。
- **`canvas/shot_zoom.js`**：新增一条检查，直达代码批注时它要以便签弹出、同页其余代码批注仍收着。
- **`canvas/SCENE.md`**：源码页那张表里加了一行，告诉讲解员批注现在默认收起、点开看，所以每条要单独点开也读得懂，行号范围要钉准。

**3. 新加的自检和反向验证**
- `shot_scene.js` 的检查在桌面和手机各跑 7 条：
  - 批注默认收起，不夹在代码中间；
  - 有批注的行染色、可点、有「注」标记；
  - 点了以后便签弹出、那几行加深；
  - 便签浮在代码上，不挤开下面的代码；
  - 便签贴着那几行的最后一行；
  - 便签整个在屏幕里；
  - 点 ✕ 能收起，并且没有 JS 报错。
- 反向验证（旧舞台用 `git show HEAD:` 取出，结果文件在工作目录 `reverse-*.txt`）：

| 页面 | 旧舞台 | 新舞台 |
|---|---|---|
| sglang `src_tm`（`shot_scene.js`） | 新加的 16 条中 14 条 FAIL，没过的就是收起、点开、浮动、关闭这几项；只有两条「没有 JS 报错」通过 | 24 PASS / 0 FAIL（含新加的 16 条） |
| redis `zoom.html`（`shot_zoom.js`） | 新加的 1 条 FAIL，批注是普通段落、同页另有 6 条批注露着 | 36 PASS / 0 FAIL |

**4. 回归全档**：6 个页面全部通过，没有 FAIL。
- `redis-eviction-v1.html` 没变，没有控制台错误
- `redis-eviction-v2-zoom.html`：36 PASS
- `redis-eviction-v3-scene.html`：41 PASS
- `sglang-request-v3-scene.html`：31 PASS
- `sglang-request-v4-dive.html`：308 PASS
- `sglang-request-v5-auto.html`：832 PASS

快档前后跑过多次。中途有一次失败：被点的是多行批注的第一行，便签按设计挂在这段的最后一行下面，是检查写错了，已改成按整段的最后一行来量。

**5. 看过的截图和对照结论**
截图做了 2 轮：
- **第 1 轮**：作者反馈的那页 `src_tm`，桌面和手机，收起状态和点开状态各一张。
- **第 2 轮**：sg4 选批主干页的 5 行批注（桌面和手机），以及 redis 页直达代码批注。

对照作者的话：「推断」卡片不再夹在代码里，代码从头到尾连着读；对应的行高亮，点开在旁边浮出文字框；手机上也正常。另外查了「展开全部批注」能全展开（这次只看了数量，没截图），Esc 和点别处都能关掉，Esc 不会误退到上一层页面。

**6. 没做到的、有风险的地方**
- 便签不是放在代码块外面的页边，而是浮在那几行下方、盖住右侧几行代码。原因是叶子页两侧的空白太窄，放在页边会出屏。作者如果想要的是真正「写在页边」的样子，可能还会不满意。
- 便签总是挂在这段批注范围的最后一行下面，所以点一段长范围的第一行时，便签会出现在几行之后。
- 批注落在代码块最后一行时，便签会盖住下一个代码块的标题栏。
- 只改了可缩放模板。老的 v1 页面（`template.html` 生成的 `canvas.html`）还是原来的批注夹在代码中间、带「只看代码」按钮，因为作者看的不是那一版。
- 自检只在无头 Chromium 里跑，iOS Safari 上的点按手感没有验证。
- 工作中我误建了一个空文件 `/tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/6e7ecd7f-x.js`，在允许写的三个目录之外，已经删掉。
