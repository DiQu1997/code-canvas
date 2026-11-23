# 下一层：顶层「读者浏览器」那个框里面——像素是怎么排出来的。
# 事实来自研究员底稿 research/manuscript-dive_render.md（仓库 HEAD 79153e7）。
# 例子沿用顶层：nano-vllm 画布，打开 /c/nano-vllm/；标「设」的像素值是底稿为演示机制设的。
from cv import *

H = 'code-canvas/template/canvas.html'
ID = 'dive_render'


def _k(x):
    return ID + '.' + x


def root_scene():
    parts = [
        # ---------------------------------------------------------------- 第一排：加载时只跑一次（layout）
        part('data', '页面里的 DATA', [20, 26, 200, 138], m=[8, 8, 230, 124], kind='store', code='DATA',
             glyph=g_kv([('cards', '10 张'), ('step', 'col 0 · band 0'), ('run', 'col 0 · band 2')]),
             say='整份画布 JSON，只有粗位置', why='粗位置交给浏览器精排', trap='线里的行号是卡内行号',
             card=card(
                 'html 里那行 `const DATA = /*CANVAS_DATA*/null;` 已被服务端换成整份画布 JSON。页面脚本先照着它把每张卡、每条 note、每个故事线色块建成 DOM，之后所有部件都只读它。',
                 ['agent 交的位置只有 `layout: {col, band}` 两个整数：col 是第几列，band 是第几层。例子里 `step` 是 `{col: 0, band: 0}`，`schedule` 是 `{col: 1, band: 0}`，`run` 是 `{col: 0, band: 2}`。',
                  '每张卡的 DOM id 是 `card-<卡id>`，每一行代码是 `<卡id>-L<行号>`；后面的连线、点亮、镜头都靠这两种 id 找元素。',
                  'DOM 建好后，下一帧依次跑 `layout()` → `setStep(0)` → `syncStreamMode()`；之后一切靠事件（▶ / ◀、点卡头、窗口 resize）。'],
                 [note('文档', '排版引擎放在浏览器的 JS 里，是因为只有浏览器能量出真实的文字宽度。',
                       q='浏览器能真实测文本宽度，Python 只能估——这是架构决定', ref='HANDOFF.md:177'),
                  note('文档', 'agent 被明确要求不给像素坐标。', q='**不要给像素坐标**', ref='HANDOFF.md:166')],
                 ['线和步里写的 `line: 4` 是**卡内**第 4 行（从 1 数起），不是源文件行号；卡的 `file` 字段（如 step 卡的 engine/llm_engine.py 第 49 行）才是源文件起点。',
                  '页面加载时还会探一下 hub 在不在（决定问答走在线还是复制到剪贴板），和排版无关，只在 [[dive_render.mobile|初始化源码页]] 里批注。'])),

        part('measure', '量宽', [340, 26, 210, 138], m=[8, 196, 230, 124], kind='act', n='1', code='_w',
             glyph=g_kv([('step', '720（设）'), ('sampler', '800 ✂'), ('col 0 列宽', '800')]),
             say='全摊开量一次，之后宽度钉死', why='宽度不再变，列 x 才稳', trap='超 800 的行被裁掉，不换行',
             card=card(
                 '给每张卡定一个固定的像素宽度。只在页面加载时做这一次。',
                 ['第一遍：把卡展开、把所有块摊开，宽度设成 `max-content`，读 `offsetWidth`，取 `min(offsetWidth + 2, 800)`。',
                  '代码行不换行（`white-space: pre`），所以自然宽度 = **最长那一行** + 行号和内边距。',
                  '第二遍：把宽度写死成像素，再套回默认折叠：数据里 `collapsed` 的卡折上、所有块折上、`folded:false` 的块例外打开。'],
                 [note('推断', '量的是「全展开」时的宽度，之后展开一张卡只会变高、不会变宽；代码里也没有别处再重算宽度，所以列的 x 永远不用重排。'),
                  note('文档', '上限 800 是拿 nano-vllm 真实代码试出来的：真实代码行常见 90+ 字符，原来的上限太窄。',
                       q='卡片宽度上限放宽到 800px（真实行…常见 90+ 字符）', ref='code-canvas/DESIGN.md:147-148')],
                 ['碰到 800 不会换行：卡片 `overflow: hidden`，超出的那截在桌面上被裁掉，没有横向滚动条（底稿从 CSS 推出，没在浏览器里点过）。validate 对超过 100 字符的行只报 warn。' + ' `code-canvas/validate.py:202-203`',
                  '两遍不能合并：块一折，里面的长行就不参与量宽，量出来会偏窄（推断）。',
                  '`+ 2` 没有注释，原因没查到。']),
             to=_k('layout')),

        part('place', 'col/band → 像素', [670, 26, 210, 138], m=[8, 384, 230, 130], kind='act', n='2', code='colX · _baseY',
             glyph=g_kv([('col 0 → x', '370'), ('col 1 → x', '1720'), ('band 2 → y', '720 起')]),
             say='x 按列累加，y = 160 + band×280', why='只给粗位置，像素归渲染器', trap='有 note 车道，col 0 不在 40',
             card=card(
                 '把每张卡的 `{col, band}` 变成像素：x 由列决定，y 先定一个「基准」。',
                 ['x 从左往右累加：起点 40；画布里有角落 note、或有左置 note 指向 col 0 的卡，就先让出一块 note 区（250 + 80）。',
                  '对每一列：若有左置 note 指向这一列（col > 0），先让出一条 250 + 70 = 320 的车道；记下这一列的 x；再加上**本列最宽那张卡**的宽度 + 230 的列间距。',
                  '基准 y = 160 + band × 280：band 0 → 160，band 1 → 440，band 2 → 720，band 3 → 1000。',
                  '算完立刻跑一遍 [[dive_render.push|同列下推]]，再摆 note、包故事线色块、算世界尺寸。'],
                 [note('文档', '布局哲学：agent 只给粗位置，量行宽定列宽、同列下推都由渲染器做。',
                       q='agent 只给粗位置 {col, band}，渲染器精排（测行宽定列宽、', ref='HANDOFF.md:164'),
                  note('文档', 'note 车道这件事 SKILL 也告诉了 agent，好让它排列时心里有数。',
                       q='左置 note 指向非 0 列的卡时会加一条 ~320px 的 note 车道', ref='code-canvas/SKILL.md:191')],
                 ['顶层图写的「col 0 → x = 40」只在没有 note 车道的画布成立；nano-vllm 有一张角落 note，col 0 在 **370**。',
                  'col 1 的 x 只跟 **col 0 最宽的卡**和 note 车道有关，跟 col 1 自己的卡宽无关（那决定 col 2 的 x）。',
                  'band 算出的只是**基准** y：实际 y 每次重画都可能被同列下推改掉。']),
             to=_k('layout')),

        # ---------------------------------------------------------------- 右列：每次重画都跑（drawWires）
        part('push', '同列下推', [1000, 26, 200, 138], m=[8, 578, 230, 124], kind='act', n='3', code='relayoutColumns',
             glyph=g_kv([('schedule', 'y 160'), ('preempt', 'y 720'), ('postprocess', 'y 1000')]),
             say='按基准 y 排队，压在上一张下 60', why='卡会展开变高，每次重画都推', trap='间距是写死的 60，vGap 没用',
             card=card(
                 '保证同一列的卡不重叠：一张卡变高了，把它下面整列往下推；变矮了，下面的卡又回到原位。',
                 ['把一列的卡按**基准 y** 排序，从上往下走：`y = max(基准 y, 上一张的底边 + 60)`，写进 `style.top`。',
                  '上一张的底边用 `offsetHeight` 读——读的是此刻展开 / 折叠之后的真实高度。',
                  '它不只在 `layout()` 末尾跑一次：`drawWires()` 每次开头都先跑它，再跑 note 跟随、色块包边、世界尺寸，最后才画线。'],
                 [note('文档', '起因：在 nano-vllm 真实样例上，展开的卡撞到了下方的卡，于是下推改成每次重绘都做。',
                       q='同列冲突下推改为每次重绘动态…执行（展开的卡会撞到下方卡）', ref='code-canvas/DESIGN.md:148-149'),
                  note('推断', '按**基准 y** 排序而不是按当前 y：展开又折回时，下面的卡会回到原位，不会「推了就回不去」。')],
                 ['只往下推，不会往上拉过基准 y；也只管同一列，不同列之间不比较。',
                  '常量表里有个 `vGap: 90`，但全文件没有地方用它；真正的间距是写死的 60。']),
             to=_k('push')),

        part('anchor', '连线锚点', [1000, 220, 200, 142], m=[8, 766, 230, 150], kind='gate', n='4', code='resolveAnchor',
             glyph=g_checks([('卡折着 → 锚卡头', None), ('行看得见 → 锚行', None), ('块折着 → 锚块条', None)]),
             say='把「某卡某行」变成一个点', why='行藏了就降级，线不指空', trap='锚的是卡内行号',
             card=card(
                 '每根线写的是 `from: {card, line}` → `to: {card, line?}`。这里把两端变成世界坐标里的两个点，中间画一条贝塞尔曲线。每次 `drawWires()` 都把所有线整体重建。',
                 ['有 `line`：按 id `卡id-L行号` 找到那一行，再过一道 `visibleProxy`：卡整个折着 → 锚到卡；行当前可见 → 锚到行的中线；否则沿祖先找**最外层**折着的块，锚到它的标题条。',
                  '没写 `line` 的整卡端点：调用线落在卡顶 + 16（折着时中线 − 5），数据线落在卡高 62% 处（折着时中线 + 7），两种线进同一张卡不打架。',
                  '左右：起点默认出右边、终点默认进左边。同列两张卡之间的线（如 `w-run`）数据里两端都写 `side: left`，曲线从左边绕出去，不穿过中间的卡。',
                  '坐标用 `wpos()`：沿 `offsetParent` 一路累加，再减掉沿途滚动容器的滚动量，得到世界坐标，跟相机缩放无关（推断）。'],
                 [note('推断', '行会被折起，也会被滚出窗口；线总要指向读者看得见的东西，所以降级到包住它的块条或卡头。'),
                  note('提交', '滚动时线锚要跟着重钉：减掉滚动位移，滚出窗口就夹在窗缘。',
                       q='线锚随滚动重钉：wpos 减滚动容器位移；行滚出窗口时锚点夹在窗缘', ref='c:398eb5c')],
                 ['`line: 12` 是**卡内**第 12 行，不是源文件第 12 行。',
                  '−16、+7、−5、62% 这些偏移的理由代码里没写。']),
             to=_k('anchor')),

        # ---------------------------------------------------------------- 第二排：读者一操作就动
        part('setstep', '按步走', [20, 220, 200, 142], m=[8, 1194, 230, 150], kind='act', n='5', code='setStep(i)',
             glyph=g_kv([('当前步', '— 未开始'), ('亮的线', '—'), ('变暗', '—')]),
             say='▶ 时改 class、定亮暗、定镜头', why='暗不暗只看这一步的线', trap='focus 不决定谁亮',
             card=card(
                 '读者按 ▶ / ◀ / 方向键时跑。把这一步要的东西摆出来：展开卡、摊开块、点亮行、暗掉无关的卡、定镜头，最后整体重画一次。',
                 ['① `expand` 里的卡去掉 `collapsed`；② `unfold` 按**块名**找块、去掉 `folded`（找不到就静默跳过）；③ 清掉所有蓝条，再给 `lines` 里的行加 `hot`。',
                  '④ 收集这一步 `wires` 里每根线的两端卡，**不在其中的卡**加 `dim`（透明度 .32）；没有 `wires` 字段的步一张都不暗。⑤ note 只亮本步的。',
                  '⑥ 定镜头（手机流 / 带读 / 总览 / 读点取景）；⑦ 最后 `drawWires()`：同列下推 + 重建所有线。'],
                 [note('推断', '先改 class、最后画：下推要读改完之后的真实高度，所以 `drawWires()` 必须排在最后。')],
                 ['亮暗由这一步**点亮的线的两端**决定，不是由 `focus` 决定；`focus` 用来给镜头兜底、标读序徽标 ①②、决定手机上列哪些卡。为什么这么分，底稿没查到。',
                  '亮暗和蓝条每步重算；展开状态却带着历史（见「折叠状态」）。']),
             to=_k('step')),

        part('fold', '折叠状态', [340, 220, 210, 142], m=[8, 980, 230, 150], kind='store', code='.collapsed',
             glyph=g_list(['step ▾ 展开', 'schedule ▾ 3 块全折', 'run · sampler ▸ 折着', 'can_allocate ▸ 折着']),
             say='存在 DOM class 里，没有变量', why='改 class 就改了高度', trap='只会被打开，不会被折回',
             card=card(
                 '记着每张卡是展开还是折着、每个块是摊开还是折着。它决定卡有多高，也决定哪一行看得见。',
                 ['没有 JS 变量：整卡折着是 `.collapsed`，块折着是 `.blk.folded`（CSS 让块里的代码不显示）。',
                  '初值在 `layout()` 里设一次（见「量宽」第二遍）。',
                  '两种人改它：`setStep` 按 `expand` / `unfold` 打开；读者点卡头 / 块条时直接切换 class，然后调 `drawWires()`——下面的卡立刻跟着上移或下移。'],
                 [note('推断', '状态直接放在 DOM 上，浏览器排版自然按新高度来；下推、连线只要读 `offsetHeight` / `offsetParent`，不用另记一份。')],
                 ['`setStep` 只 remove、从不 add：往回按 ◀ 不会把后面步骤展开的东西收回去；只有读者自己点卡头才会折回。',
                  '这是底稿读代码得出的；是有意设计还是副作用，没查到。']),
             to=_k('step')),

        part('cam', '镜头', [20, 418, 200, 100], m=[8, 1408, 150, 142], kind='act', n='6', code='cam',
             glyph=g_text('—', '还没定'),
             say='对准这一步要读的那几行', why='整卡取景会缩到看不清', trap='缩不下就怼住第一个读点',
             card=card(
                 '决定读者屏幕上看到世界的哪一块、放多大。相机就是三个数 `{x, y, s}`，变成整个世界层的 CSS transform；步进时 0.55 秒「飞」过去。',
                 ['总览步：`fitAll`，整个内容包围盒缩到刚好装下（最大 1.15，最小 0.1），从不裁切。',
                  '其它步：`focusRead`。读点 = `lines` 里的行 ∪ `unfold` 的块 ∪ 本步 note 和它指的那行；对读点求包围盒。',
                  '比例 `sc = min(宽 / (w+140), (高−170) / (h+140), 1.15)`；`sc ≥` 可读下限（桌面 .85、手机 .5）就居中到包围盒，否则**以第一个读点为中心、按下限缩放**，其余让读者自己拖。'],
                 [note('文档', '作者反馈：进一步时画面太大，要直接放大到这一节该读的那几行。改完后各步比例从 .4–.6 变成 .85–1.15。',
                       q='作者反馈：进一步时画面太大，要直接 zoom 到这…一节该读的那几行', ref='ROADMAP.md:236-237'),
                  note('提交', '缩不下时宁可只露出阅读起点，也不缩到看不清。', q='缩不下就怼阅读起点', ref='c:4b9fa78')],
                 ['镜头框的是**行**，不是 `focus` 里的那几张卡。',
                  '它在 `drawWires()` 之前算，用的是下推之前的位置；这一步若把别的卡推下去，构图可能略偏（推断）。',
                  '`+140`、`−170` 这些留白的含义没找到注释。']),
             to=_k('cam')),

        part('mobile', '手机卡片流', [340, 418, 210, 100], m=[182, 1408, 150, 142], kind='bg', code='mstream',
             glyph=g_text('≥700px', '2D 画布'),
             say='窄屏换一种投影', why='缩小的全景读不了', trap='量宽仍按桌面做',
             card=card(
                 '屏幕窄于 700px（且没按「2D 画布」逃生钮）时，不再摆 x / y、不再画线，而是把这一步要读的卡按阅读序竖着排。',
                 ['同一批卡片 DOM 被 `appendChild` **搬进**卡片流容器（不是复制）；CSS 把它们改成 `position: static`、`width: auto`，桌面量出的宽度被覆盖。',
                  '点亮的线变成可点的「关系条」；总览步变成按故事线分节的目录。',
                  '`drawWires()` 在卡片流里一进来就返回。窗口 resize 越过 700 会重新判定；切回 2D 时卡被搬回，重跑 `setStep` 和 `drawWires()`。'],
                 [note('提交', '之前试过缩小 + HUD 收纳，作者裁决手机上不可读，才换成另一种投影。',
                       q='作者裁决：0.12 缩放的 2D 全景在手机上不可读，resize 不是答案。', ref='c:51b075e'),
                  note('文档', 'ROADMAP 里同一个裁决的原话。', q='作者裁决"大图手机看不了，resize 救不了"', ref='ROADMAP.md:36')],
                 ['初次加载仍是先 `layout()`（按桌面量宽、排 2D）→ `setStep` → 最后才切到流。',
                  'resize 不会重新 `layout()`、不重新量宽，只重画线（以及在 700px 处切换投影）。']),
             to=_k('mobile')),
    ]

    wires = [
        wire('w_dm', 'data', 'measure', '建好 DOM 再量', ml='建 DOM 后量',
             card=card('脚本先按 DATA 把卡片 DOM 一次性建好，下一帧（`requestAnimationFrame`）里才调 `layout()` 去量。',
                       '量宽要读 `offsetWidth`，元素必须已经在页面里排过一次。',
                       [note('推断', 'DOM 建好、浏览器排过一帧，读到的宽度才是真实字体排出来的宽度。')])),
        wire('w_mp', 'measure', 'place', '列宽 = 最宽卡', ml='列宽取最宽', why='一张宽卡推远后面所有列',
             card=card('每列的宽度取本列最宽那张卡，决定下一列从哪开始。',
                       '下一列 x = 本列 x + 本列最宽 `_w` + 230。',
                       [note('文档', '所以 SKILL 劝 agent 把代码行收窄：行窄，列就窄，总览就不至于太小。',
                             q='行宽收窄可救列宽', ref='code-canvas/SKILL.md:190')],
                       '例子里一张最长行 104 字符的 `sampler` 就让 col 0 宽到 800，col 1 跟着往右挪。')),
        wire('w_pp', 'place', 'push', '只是基准 y', ml='基准 y', why='实际 y 由下推定',
             card=card('`layout()` 只给出「想放的位置」（基准 y）；真正的 y 由同列下推决定。',
                       '`layout()` 末尾调一次下推；之后每次 `drawWires()` 开头都再调一次。',
                       [note('文档', 'y 拆成「基准 + 动态下推」，因为卡会随展开折叠变高。',
                             q='同列冲突每次重绘动态下推', ref='HANDOFF.md:165')])),
        wire('w_pa', 'push', 'anchor', '卡就位再连线', ml='就位再连线', why='线的端点依赖卡的位置',
             card=card('`drawWires()` 里先下推、摆 note、包色块、算世界尺寸，最后才清空 SVG、逐根算锚点画线。',
                       '每次都**整体重建**所有线，不做增量；有 `wires` 的步里，本步的线亮、其余的暗。',
                       [note('推断', '线的两端读的是卡此刻的位置，所以必须排在所有挪卡的动作之后。')])),
        wire('w_sf', 'setstep', 'fold', '只打开', trap='◀ 不会折回',
             card=card('`setStep` 按这一步的 `expand`、`unfold` 把卡和块打开。',
                       '只 `classList.remove`（`collapsed` / `folded`）；块按名字找，同名取第一个，找不到就跳过。',
                       [note('推断', '「只开不关」的理由底稿没查到；后果是同一步从不同路径进来，看到的展开程度可能不一样。')],
                       '亮暗、蓝条每步重算；只有展开状态带着历史。')),
        wire('w_fp', 'fold', 'push', '高度变了，重推', ml='高度变了', why='读此刻的 offsetHeight', fs='r', ts='b', mfs='r', mts='r',
             card=card('class 一变，卡的高度就变；`drawWires()` 开头的下推按新高度重新摆整列。',
                       '读者点卡头、`setStep` 换步、窗口 resize，最后都走到 `drawWires()` → 下推。',
                       [note('推断', '没有单独的「重新排版」：同一个下推函数每次重跑一遍就够了。')])),
        wire('w_fa', 'fold', 'anchor', '行藏了就降级', ml='藏了就降级', why='线永远指向看得见的东西',
             card=card('连线锚点要看折叠状态：要锚的那一行在折着的块里，就改锚到块条或卡头。',
                       '`visibleProxy` 先看卡是不是整个折着，再看这一行当前可不可见（`offsetParent !== null`），都不行就往外找最外层折着的块。',
                       [note('推断', '降级到「包住它的、看得见的东西」，读者顺着线总能找到入口，点开就是那一行。')])),
        wire('w_sc', 'setstep', 'cam', '先定镜头', trap='在下推之前算',
             card=card('`setStep` 在画线之前决定镜头往哪飞。',
                       '总览或 `fit` → `fitAll`；有带读分段 → 交给带读；否则 `focusRead`（读点取景）。',
                       [note('推断', '镜头排在 `drawWires()` 前面是不是有意，底稿没查到；后果是它用的是下推之前的位置。')])),
        wire('w_sm', 'setstep', 'mobile', '<700px 走卡片流', ml='<700 走流', cls='dash',
             card=card('在手机卡片流里，`setStep` 不算镜头，改成重建卡片流，把这一步的卡按阅读序竖排。',
                       '`setStep` 看页面是不是处在卡片流：是就 `buildStream()`；随后的 `drawWires()` 直接返回。',
                       [note('推断', '卡片流里没有 x / y，线无处可画；线表达的关系改成可点的关系条。')])),
    ]

    steps = [
        beat('html 到了：只有粗位置',
             '顶层最后一步里，`/c/nano-vllm/` 原样返回了 html。`DATA` 里是 10 张卡、5 条 note、11 根线、6 步；'
             '每张卡只有 `{col, band}`：`step` `{0, 0}`、`run` `{0, 2}`、`schedule` `{1, 0}`。脚本先把卡建成 DOM（`card-step`，每行 `step-L1`…），**一个像素数都还没有**。',
             note('文档', 'agent 只给粗位置，渲染器精排。', q='agent 只给粗位置 {col, band}，渲染器精排', ref='HANDOFF.md:164'),
             focus=['data', 'w_dm', 'measure']),
        beat('量宽：全摊开量，钉死，再折回',
             '每张卡去掉折叠、`width: max-content`、读宽度。`sampler` 最长一行 104 字符 → 碰到上限，钉在 **800**，多出的那截被裁掉；'
             '`step` 设为 720。于是 col 0 列宽 800（`sampler`）、col 1 列宽 800（`schedule`，101 字符）、col 2 设为 640。宽度写死后再折回：`run`、`sampler` 等默认折着。',
             note('推断', '按全展开时的宽度定死，之后展开只变高不变宽，列 x 不用重排（像素数是底稿设的，没在真实浏览器里量过）。'),
             focus=['measure', 'w_mp']),
        beat('col → x：col 0 在 370，不是 40',
             '画布有一张角落 note `bg0`（`corner: nw`）→ 起点 40 + 250 + 80 = **370**。'
             '`n-chunk`、`n-preempt` 是左置 note、指向 col 1 → 让出 320：x = 370 + 800 + 230 + 320 = **1720**。'
             '同理 col 2 = 2750、col 3 = 3620。顶层的「x = 40」只适用于没有 note 车道的画布。',
             note('文档', 'note 车道是渲染器的已知行为。', q='左置 note 指向非 0 列的卡时会加一条 ~320px 的 note 车道', ref='code-canvas/SKILL.md:191'),
             focus=['place', 'w_mp']),
        beat('band → 基准 y，第一次下推：没撞',
             '基准 y = 160 + band × 280：`step`、`schedule` 160，`kv-pool`（band 1）440，`run`、`preempt` **720**，`sampler`、`postprocess` 1000。'
             '`layout()` 末尾第一次下推 col 1：`schedule` 折着块时设约 300 高 → 底边 460；`preempt` 取 max(720, 460 + 60) = 720，不动。',
             note('文档', '下推每次重绘都做，因为卡会展开变高。', q='同列冲突下推改为每次重绘动态…执行', ref='code-canvas/DESIGN.md:148-149'),
             focus=['push', 'w_pp', 'place'],
             state={'push': g_kv([('schedule', 'y 160'), ('preempt', '720 不动'), ('postprocess', '1000 不动')])}),
        beat('总览：setStep(0)',
             '第 0 步没有 `wires` 字段 → 一张卡都不暗；`fit: true` → `fitAll`。包围盒设约 3900 × 1200，视口设 1400 × 900 → 缩放 ≈ **0.35**，读者先看到一张宏观的全景。',
             note('推断', '总览是先让读者看到整张图的形状，所以宁可缩小也不裁切（源码注释原话见[[dive_render.cam|镜头源码页]]）。'),
             focus=['setstep', 'w_sc', 'cam'],
             state={'setstep': g_kv([('当前步', '0 总览'), ('亮的线', '全部默认'), ('变暗', '0 张')]),
                    'cam': g_text('×0.35', '总览 · fitAll')}),
        beat('▶ 第①步：亮暗看线，不看 focus',
             '`STEPS[1].wires` = `w-sched`、`w-run`、`w-post`、`d-token` → 两端卡 `step`、`schedule`、`run`、`postprocess` 亮，其余 **6 张变暗**。'
             '`step` 第 2、4、5 行加蓝条。这一步没有 `expand`：`run`、`postprocess` 仍折着。',
             note('推断', '亮暗每步重算；这一步的 focus 恰好也是这 4 张，但决定亮暗的是线（为什么这样分，底稿没查到）。'),
             focus=['setstep', 'w_sf', 'fold'],
             state={'setstep': g_kv([('当前步', '①'), ('亮的线', '4 根'), ('变暗', '6 张')])}),
        beat('①：每根线锚到哪',
             '`w-sched` 起点：`step` 第 2 行看得见 → 锚在这一行中线。`w-run` 终点：`run` 整卡折着 → 卡中线 − 5。'
             '`w-canalloc`（这一步不亮，也照画）起点是 `schedule` 卡内第 12 行，它在折着的「Prefill 批次」块（行 5–31）里 → **降级锚到这个块的标题条**。',
             note('推断', '线总要指向看得见的东西；行藏起来了，就落到包住它的块条或卡头。'),
             focus=['anchor', 'w_fa', 'w_pa'],
             state={'anchor': g_checks([('w-run → run 卡头', 'ok'), ('w-sched ← step 行 2', 'ok'), ('w-canalloc ← 块条', 'ok')])}),
        beat('①：镜头 ×1.15，只框 3 行',
             '读点只有 `step` 的第 2、4、5 行。设 `step` 卡宽 720、这几行约 84 高 → sc = min(1400 / 860, 730 / 224, 1.15) = **1.15** ≥ 0.85 → 居中到这几行。'
             '`schedule` 在 x ≈ 1720，屏幕外，只看得到线探出去的方向。',
             note('文档', '作者要的是直接放大到该读的那几行。', q='要直接 zoom 到这…一节该读的那几行', ref='ROADMAP.md:236-237'),
             focus=['cam', 'w_sc'],
             state={'cam': g_text('×1.15', 'step 行 2–5', tone='ok')}),
        beat('▶▶ 第③步：展开一张，整列被推下去',
             '展开 `can_allocate`、`allocate`；摊开三个块（`schedule` 的「Prefill 批次」就有 27 行 × 21px）。`schedule` 设长到约 900 高 → 底边 1060 → '
             '`preempt` = max(720, 1060 + 60) = **1120**；它折着设 34 高 → `postprocess` = max(1000, 1154 + 60) = **1214**。列 x 没动。',
             note('文档', '这正是当初改成每次重绘都下推的原因。', q='展开的卡会撞到下方卡', ref='code-canvas/DESIGN.md:149'),
             focus=['fold', 'w_fp', 'push', 'w_sf'],
             state={'fold': g_list(['Prefill 批次 ▾ 摊开', '前缀缓存探测 ▾ 摊开', 'can_allocate ▾ 展开', 'allocate ▾ 展开'], on=[0, 1, 2, 3]),
                    'push': g_kv([('schedule', 'y 160'), ('preempt', '720 → 1120'), ('postprocess', '1000 → 1214')]),
                    'setstep': g_kv([('当前步', '③'), ('亮的线', '4 根'), ('变暗', '6 张')])}),
        beat('③：线锚升回行，镜头缩不下',
             '块摊开了，`w-canalloc` 的起点从块条**回到第 12 行本身**。镜头：读点 = `schedule` 行 12、21 + 三个摊开的块（一个在 col 2 的 `allocate` 里），'
             '包围盒设宽约 1670 → 1400 / 1810 ≈ 0.77 < 0.85 → 改为以第一个读点（`schedule` 行 12）为中心、缩放 **0.85**；`allocate` 只露一部分，要读者自己拖。',
             note('提交', '缩不下就怼住阅读起点。', q='缩不下就怼阅读起点', ref='c:4b9fa78'),
             focus=['cam', 'anchor'],
             state={'anchor': g_checks([('w-canalloc ← 行 12', 'ok'), ('w-alloc ← 行 21', 'ok'), ('块已摊开', 'ok')]),
                    'cam': g_text('×0.85', '怼住行 12', tone='run')}),
        beat('◀ 回到第②步：展开的不收回',
             '`setStep(2)`：②里没有 `expand` / `unfold`，而 `setStep` 只会打开、从不折回 → `can_allocate`、`allocate`、「Prefill 批次」**仍然开着**。'
             '亮暗重算：`wires` 只有 `w-sched` → 只亮 `step`、`schedule`，暗 8 张。同一个第②步，直接走到和从③退回来，看到的不一样。',
             note('推断', '这是读代码得出的结论（底稿没在浏览器里点过）；是有意设计还是副作用，没查到。'),
             focus=['setstep', 'w_sf', 'fold'],
             state={'setstep': g_kv([('当前步', '②'), ('亮的线', '1 根'), ('变暗', '8 张')])}),
        beat('换到手机：同一批卡变成卡片流',
             '如果读者像你一样在 390 宽的手机上打开：初始化照样先 `layout()` 按桌面量宽、排 2D → `setStep` → `syncStreamMode()` 发现宽度 < 700，'
             '把这一步的卡 DOM **搬进**卡片流竖排、宽度改成 auto；`drawWires()` 直接返回——没有 x / y，也没有线。',
             note('提交', '0.12 倍的全景在手机上读不了，缩放救不了。', q='作者裁决：0.12 缩放的 2D 全景在手机上不可读，resize 不是答案。', ref='c:51b075e'),
             focus=['mobile', 'w_sm', 'setstep'],
             state={'mobile': g_text('390px', '卡片流 · 无线', tone='run')}),
    ]

    return scene(1220, 520, parts, wires, steps, mw=340, mh=1562,
                 groups=[group('layout() · 加载时只跑一次', [8, 2, 884, 170], m=None),
                         group('drawWires() · 每次变化都重跑', [988, 2, 224, 368], m=None)],
                 cap='①–④ 打开页面时跑一遍；之后每按一步 ⑤ 改折叠状态，③④ 重推、重连线，⑥ 定镜头')


# ---------------------------------------------------------------- 源码页
def _src_layout():
    return node(
        _k('layout'), 'layout() 源码', 'layout()：量宽 → 定列 x → 定基准 y',
        '「量宽」和「col/band → 像素」两个框的源码：页面加载时只跑这一次。',
        lead='你在「读者浏览器」→「量宽」/「col/band → 像素」里面。整段只在页面加载时跑一次；例子里的数接着上一层的推演。',
        src=H + ':1031-1081',
        blocks=[code(H, [(1031, 1081)], notes=[
            at(1031, '推断', '常量表：边距 40、顶 160、列间距 230、band 高 280、note 宽 250。`vGap: 90` 全文件没人用——真正的同列间距是下推里写死的 60。', has='vGap: 90'),
            at(1035, '注释', '先展开、摊开再量：量的是「全展开」时的自然宽度，之后展开只会变高。', has='measure natural card widths (expanded, unfolded)'),
            at((1040, 1041), '推断', '最长那一行决定宽度（代码行不换行）。上限 800 来自 DESIGN.md：真实代码行常见 90+ 字符。`+ 2` 的原因没查到。例子：`sampler` 最长行 104 字符，碰到 800。', has='Math.min(el.offsetWidth + 2, 800)'),
            at(1046, '推断', '宽度写死成像素：之后展开只变高、不变宽，列的 x 不必重排。这两个循环不能合并——块先折上的话，里面的长行就量不到了。', has="el.style.width = c._w + 'px'"),
            at((1047, 1051), '推断', '套回默认折叠：数据里 `collapsed` 的卡折上；所有块折上；`folded:false` 的块例外打开。nano-vllm 里 `run`、`sampler` 等默认折着。', has='applyFoldDefaults'),
            at((1054, 1055), '推断', '任何角落 note 都算「有左侧 note」：nano-vllm 的 `bg0` 是 `corner: nw`，所以起点是 40 + 250 + 80 = 370，不是 40。', has='n.place.corner'),
            at((1057, 1060), '推断', '左置 note 指向 col > 0 的卡，那一列记进 `leftNoteCols`：nano-vllm 的 `n-chunk`、`n-preempt` 指向 `schedule`（col 1）。', has="n.place.side === 'left'"),
            at(1066, '推断', '这一列前面让出 250 + 70 = 320 的 note 车道：col 1 的 x = 370 + 800 + 230 + 320 = 1720（800 是设的 col 0 宽）。', has='L.noteW + 70'),
            at(1068, '推断', '下一列起点 = 本列最宽那张卡 + 230：一张超宽卡会推远它后面所有的列。', has='Math.max(...cols[k].map(c => c._w))'),
            at(1070, '注释', 'x 和基准 y 只来自粗位置；动态下推在每次重画时才发生。', has='dynamic push-down happens on every draw'),
            at(1075, '推断', '基准 y = 160 + band × 280：band 0 → 160，band 2 → 720，band 3 → 1000。这只是「想放的位置」。', has='L.top + c.layout.band * L.bandH'),
            at((1077, 1080), '推断', '定完立刻跑这四个：下推、note 跟随、色块包边、世界尺寸——它们也正是 `drawWires()` 每次开头跑的四个。', has='relayoutColumns();'),
        ], cap='layout() 全文（含常量表）')])


def _src_push():
    return node(
        _k('push'), '下推源码', 'relayoutColumns()：每次重画都把同列重推一遍',
        '「同列下推」那个框的源码，加上 drawWires() 的开头：每次重画先推卡、最后画线。',
        lead='你在「读者浏览器」→「同列下推」里面。上半段是下推本身，下半段是谁在每次重画时调用它。',
        src=H + ':1136-1151',
        blocks=[
            code(H, [(1136, 1151)], notes=[
                at(1137, '注释', '卡会随展开折叠变高变矮，所以要一直保证同列不重叠。', has='cards grow and shrink as they expand/fold'),
                at(1141, '推断', '按基准 y 排序，不按当前 y：展开又折回之后，下面的卡会回到原位。', has='a._baseY - b._baseY'),
                at(1145, '推断', '只往下推：y = max(基准 y, 上一张底边 + 60)。例子第③步：`preempt` = max(720, 1060 + 60) = 1120。60 是写死的，不是常量表里的 vGap。', has='prevBottom + 60'),
                at(1148, '推断', '底边用 `offsetHeight`：必须在 class 改完之后读，所以 `setStep` 先改 class、最后才调 `drawWires()`。', has='el.offsetHeight'),
            ], cap='relayoutColumns() 全文'),
            code(H, [(1287, 1291)], notes=[
                at((1288, 1290), '注释', '手机卡片流和精读模式里直接返回：卡片流没有空间布线，精读时等卡归位再布线。', has='卡片流没有空间布线'),
                at(1291, '推断', '每次重画的四连：下推 → note 跟随 → 色块包边 → 世界尺寸；画线排在这之后，因为线的端点依赖卡已经到位。', has='relayoutColumns(); placeNotes(); fitRegions(); sizeWorld();'),
            ], cap='drawWires() 的开头'),
        ])


def _src_anchor():
    return node(
        _k('anchor'), '锚点源码', 'wpos / visibleProxy / resolveAnchor：把「某卡某行」变成一个点',
        '「连线锚点」那个框的源码：取世界坐标、看不见就降级、按端点种类定 y。',
        lead='你在「读者浏览器」→「连线锚点」里面。三个函数从下往上用：resolveAnchor 调 visibleProxy 找元素，再调 wpos 取坐标。',
        src=H + ':1186-1234',
        blocks=[code(H, [(1186, 1234)], notes=[
            at(1188, '推断', '沿 offsetParent 累加到世界层：得到世界坐标，不受相机缩放 / 平移影响（offset 系列不含 CSS transform，推断）。', has='e.offsetParent'),
            at((1189, 1195), '提交', '减掉沿途滚动容器的滚动量，线锚才钉得住；这一段是后来加的。',
               q='线锚随滚动重钉：wpos 减滚动容器位移', ref='c:398eb5c', has='y -= s.scrollTop'),
            at((1199, 1206), '注释', '三级降级：卡折着 → 卡；行可见 → 行；否则最外层折着的块的标题条；都不是 → 卡。例：`schedule` 卡内第 12 行在折着的「Prefill 批次」里，线就从这个块条出发。', has='outermost folded block bar'),
            at(1204, '推断', '`unshift` 让外层的块排在前面，所以下一行取到的是**最外层**折着的块。', has='chain.unshift(e)'),
            at(1212, '推断', '按 `卡id-L行号` 找那一行：行号是卡内行号，从 1 数起，不是源文件行号。', has="spec.card + '-L' + spec.line"),
            at(1216, '推断', '默认 side：起点右、终点左。同列连线（如 `w-run`）靠数据里两端都写 left，从左侧绕出去。', has="spec.side || (slot ? 'left' : 'right')"),
            at((1221, 1226), '注释', '行在一体滚动窗里被滚出视野时，锚点夹在窗口边缘 ±6px，线不会飘出卡外。', has='wp.y + 6'),
            at((1229, 1231), '推断', '整卡端点：调用线贴卡头（+16，折着时中线 −5），数据线落在 62% 处（折着时中线 +7），两种线进同一张卡不重叠。这些偏移的理由代码没写。', has='0.62'),
        ], cap='wpos → visibleProxy → resolveAnchor')])


def _src_step():
    return node(
        _k('step'), 'setStep 源码', 'setStep(i)：改 class、定亮暗、定镜头，最后重画',
        '「按步走」和「折叠状态」两个框的源码：按 ▶ 时跑的前半段。',
        lead='你在「读者浏览器」→「按步走」里面。注意前 4 段只改 DOM 的 class，真正挪卡、画线在最后一行。',
        src=H + ':1689-1726',
        blocks=[code(H, [(1689, 1726)], notes=[
            at((1695, 1699), '推断', '`expand`：只去掉 collapsed，从不加回——展开是单向的，往回按 ◀ 不会收回。例子③步展开 `can_allocate`、`allocate`。', has="remove('collapsed')"),
            at((1700, 1703), '推断', '`unfold` 按块名找（只比块标题文字），同名取第一个；找不到就静默跳过。', has=".bname').textContent === name"),
            at((1705, 1709), '推断', '蓝条每步重算：先清空所有 hot，再给本步 `lines` 加。①步：`step` 第 2、4、5 行。', has="classList.add('hot')"),
            at((1710, 1715), '推断', '亮暗看线不看 focus：本步 `wires` 两端卡之外全部 dim；没有 `wires` 字段的步一张都不暗。①步亮 4 张、暗 6 张；②步只亮 2 张。', has='activeCards'),
            at((1716, 1720), '推断', 'note：只亮本步的；总览步（step 0）一张都不暗。', has='step > 0 && !mine'),
            at((1723, 1725), '注释', '镜头三选一：手机流 → 重建卡片流；带读步 → 交给 showSeg（放在 HUD 更新之后）；总览或 fit → fitAll，否则 focusRead。', has='放在 HUD 更新之后'),
            at(1726, '推断', '最后才 `drawWires()`：下推 + 重建所有线。镜头在它之前算，用的是下推之前的位置。', has='drawWires();'),
        ], cap='setStep() 的前半')])


def _src_cam():
    return node(
        _k('cam'), '镜头源码', 'fitAll / focusRead：总览全装下，其余对准要读的行',
        '「镜头」那个框的源码：相机三个数怎么定。',
        lead='你在「读者浏览器」→「镜头」里面。上半段是放到屏幕中心和总览，下半段是读点取景。',
        src=H + ':1408-1458',
        blocks=[
            code(H, [(1381, 1392)], notes=[
                at((1383, 1384), '推断', '把世界点 (wx, wy) 放到屏幕中心；`innerHeight − 70` 的 70 大概是底栏高度（推断）。', has='innerHeight - 70'),
                at((1388, 1391), '注释', '总览从不裁切：宽画布缩到多小都行（最大 1.15，至少 0.1）。nano-vllm 设约 0.35。', has='never crop the overview'),
            ], cap='centerOn 和 fitAll'),
            code(H, [(1408, 1458)], notes=[
                at((1408, 1410), '注释', '读点取景的理由：长函数整卡取景会缩到看不清（作者反馈）；缩不下就怼住阅读起点，其余靠平移。', has='长函数整卡取景会缩成'),
                at(1411, '推断', '可读下限：桌面 .85、手机 .5。写成函数，每次调用时看当前宽度（resize 后会变，推断）。', has='READ_FLOOR'),
                at((1414, 1417), '推断', '读点一：`lines` 里的行（经 visibleProxy）；折着的卡里的行不算。', has="!c.classList.contains('collapsed')"),
                at((1418, 1422), '推断', '读点二：`unfold` 的块。③步里 `allocate` 的「认领命中块」在 col 2，把包围盒一下拉宽。', has='s.unfold'),
                at((1446, 1453), '注释', '没有读点的老数据：不整卡缩小，对准第一张 focus 卡的顶部。', has='对准首个 focus 卡的顶部'),
                at(1455, '推断', '能装下的比例。①步：min(1400/860, 730/224, 1.15) = 1.15；③步：1400/1810 ≈ 0.77（视口和尺寸是设的）。+140 / −170 的含义没找到注释。', has='r.x1 - r.x0 + 140'),
                at((1456, 1457), '推断', '两条分支：够大就居中到包围盒；否则以第一个读点为中心、按下限缩放——③步走这条，×0.85 对准 `schedule` 行 12。', has='r.first.cx'),
            ], cap='readRect 和 focusRead'),
        ])


def _src_mobile():
    return node(
        _k('mobile'), '手机与初始化', '卡片流开关、resize、初始化顺序、问答探测',
        '「手机卡片流」那个框的源码，加上页面启动的顺序和 hub 在线探测。',
        lead='你在「读者浏览器」→「手机卡片流」里面。三小段：切投影、resize 与初始化、问答探测。',
        src=H + ':2173-2184',
        blocks=[
            code(H, [(2173, 2184)], notes=[
                at(2174, '推断', '窄于 700px 且没按「2D 画布」逃生钮，就要卡片流。', has='innerWidth < 700 && !forceCanvas'),
                at(2179, '推断', '已在目标模式就只重建流，不来回搬 DOM。', has='want === streamOn()'),
                at(2183, '推断', '切回 2D：卡片搬回世界层，重跑 `setStep` 和 `drawWires()`。', has='restoreFromStream(); setStep(step, false); drawWires();'),
            ], cap='syncStreamMode()'),
            code(H, [(2884, 2906)], notes=[
                at((2884, 2888), '推断', 'resize 只切投影、重画线、总览步重新 fitAll——不会重新 `layout()`，不重新量宽。', has="addEventListener('resize'"),
                at(2891, '注释', 'URL 末尾带 `#s2` 就直接跳到第 2 步。', has='#s2 jumps to step 2'),
                at(2896, '推断', '初始化第一件事：`layout()`，按桌面 2D 量宽、排位。', has='layout();'),
                at((2905, 2906), '推断', '再 `setStep`，最后才 `syncStreamMode()` 切到手机流。先量再切，才量得到没被 `width: auto` 覆盖的自然宽度（推断）。', has='syncStreamMode();'),
            ], cap='resize 与初始化'),
            code(H, [(1798, 1801)], notes=[
                at((1799, 1800), '推断', '问答历史只存在读者自己浏览器的 localStorage 里，键是 `canvas-qa:<标题>`。', has='localStorage'),
                at(1801, '推断', '探测 hub 在不在：请求成功且是合法 JSON 就算在线，内容不看；离线打开就保持 false，问答退化成「复制上下文到剪贴板」。', has="fetch('__alive')"),
            ], cap='问答开关'),
        ])


def nodes():
    return {ID: node(
        ID, '浏览器排版', '浏览器怎么把 JSON 排成画布',
        '顶层「读者浏览器」那个框里面：量宽 → 定列 → 定基准 y → 同列下推 → 连线锚到行 → 按步走镜头。',
        lead=('你现在在顶层图「读者浏览器」那个框里面。agent 交的 JSON 只有 `{col, band}`，'
              '这里看浏览器怎么把它变成像素：①–④ 在页面打开时跑一遍；之后读者每按一步，⑤ 改状态，③④ 再重来一次。'
              '推演接着用 nano-vllm 这张画布（像素数是研究员为演示设的）。'),
        src=H + ':1031-1081',
        blocks=[root_scene()],
        children=[_src_layout(), _src_push(), _src_anchor(), _src_step(), _src_cam(), _src_mobile()])}
