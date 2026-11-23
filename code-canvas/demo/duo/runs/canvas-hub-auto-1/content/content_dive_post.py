# 下一层：顶层「服务端收尾」那个框里面。
# 事实全部来自研究员底稿 research/manuscript-dive_post.md（仓库 HEAD 79153e7），例子接顶层那一单。
from cv import *

SV = 'code-canvas/serve.py'
EM = 'code-canvas/embed_context.py'
CR = 'code-canvas/check_refs.py'
VA = 'code-canvas/validate.py'
RD = 'code-canvas/render.py'
SK = 'code-canvas/SKILL.md'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


def nodes():
    return {'dive_post': _post()}


# ---------------------------------------------------------------- 小图（同一套数）
def G_W(files='—', refs='6 条', ev='fact', extra=None):
    rows = [('卡', '10 张'), ('files', files), ('refs', refs), ('步4 ev', ev)]
    return g_kv(rows + ([extra] if extra else []))


def G_EMB(a=None, b=None, c=None, t=('猜仓库根', '逐卡比对', '写回 files')):
    return g_checks([(t[0], a), (t[1], b), (t[2], c)])


def G_REF(a=None, b=None, c=None, t=('文件 / 行号', '符号 ±3 行', '剔光→infer')):
    return g_checks([(t[0], a), (t[1], b), (t[2], c)])


def G_VAL(a=None, b=None, c=None, t=('结构 / 引用', '名字不许编造', '沙盘真跑')):
    return g_checks([(t[0], a), (t[1], b), (t[2], c)])


def G_RC(rc='0', st='—'):
    return g_kv([('rc', rc), ('.status', st)])


G_LIB0 = g_list(['cp → .json', 'render → .html', '写 .src.json'])
G_LIB_OK = g_list(['nano-vllm.json ✓', 'nano-vllm.html ✓', '.src.json ✓'], on=[0, 1, 2])
G_LIB_NO = g_list(['cp 没执行', '库里不动'], on=[0])


# ---------------------------------------------------------------- 机器图
def _post():
    parts = [
        part('embed', '嵌上下文', [10, 8, 210, 150], m=[8, 8, 140, 170], kind='act', n='1', code='embed_context',
             glyph=G_EMB(),
             say='代码对到源文件，嵌全文', why='给 validate 和读者全文',
             trap='失败不拦，但让 validate 更严',
             card=card(
                 '收尾第 ① 步：把每张代码卡对回仓库里的源文件；对上了，就把**整个源文件**塞进 canvas.json 的 `files`——validate 拿它核名字，读者拿它就地展开上下文。行号不对还会顺手改掉。',
                 ['先猜根：数仓库根能命中几条卡路径；没全中才扫一级子目录（跳过 `.` 开头），严格更多才换，换了只影响读文件。' + R(EM, 35, 51),
                  '比对规则：卡代码和文件「从起始行起 n+40 行」两边都删掉空白、`//`、`#`、`\\` 后，看卡代码是不是前缀。' + R(EM, 22, 23) + ' ' + R(EM, 69, 70),
                  '比不上：拿卡代码首行（规范化后）从文件第 1 行往下找，第一个「首行相同、整段也对上」的位置当新行号，改写卡的 `file`。' + R(EM, 72, 81),
                  '还找不到：这张卡跳过；同一文件只要有一张卡对上就嵌整个文件。一张都没嵌成：打印「没有任何卡可嵌入」、`exit 2`，**不写文件**。' + R(EM, 82, 87)],
                 [note('注释', '规范化和评分器用同一套：SKILL 允许超宽行「保持 token 顺序的换行重排」，重排会插进续行符，所以这些符号不算差异。',
                       q='与评分器同款', ref=A(EM, 6)),
                  note('注释', '行漂移修正是给「仓库比画布新」准备的。', q='（仓库比画布新时的常见情形）', ref=A(EM, 8)),
                  note('提交', '猜根是为 monorepo：卡路径写成相对 codex-rs 这类子目录。', q='仓库根自动定位（monorepo 子目录如 codex-rs，', ref='c:690022d'),
                  note('提交', 'exit 2 被忽略是有意的：预览地图本来就没有代码卡，必然一张都嵌不成。', q='预览图等无代码卡时自动跳过不碰状态码', ref='c:690022d')],
                 ['文件头注释说找「唯一匹配点」，代码取的却是**第一个**命中：不查有没有第二个，也不从原行号附近找；文件里有两段相同代码时会改到前一段（测试只测了唯一匹配，' + R('code-canvas/tests/embed_test.py', 33, 41) + '）。',
                  '「有代码卡但全对不上」的 exit 2 也一起被吞，这是同一写法的副作用（推断）。它不拦画布，却会让 validate 更严——见推演最后一步。',
                  '在 hub 上漂移多半不是仓库变了（缓存只在没有同仓任务时才刷新），更可能是 agent 报的起始行本身有偏差（推断，没有频率数据）。']),
             to='dive_post.embed'),

        part('refs', '核依据', [330, 8, 210, 150], m=[216, 230, 116, 170], kind='act', n='2', code='check_refs',
             glyph=G_REF(),
             say='核依据，假的就地删', why='删假出处，不拒收', trap='不猜子目录根',
             card=card(
                 '收尾第 ② 步：核画布里每条「依据」`[文件, 行号, 符号]` 在仓库里成不成立；核不过的**就地删掉**；依据被删光的「已确认」步卡降级成「推断」。',
                 ['依据可能出现在 `steps[].trace`、`gaps`、`objects`、`boxes` 的 `refs` 里。' + R(CR, 49, 56),
                  '`ref_ok` 依次问：格式对不对 → 路径越没越出仓库 → 文件在不在 → 行号越没越界 → 有符号时，这个**子串**在不在该行前 3 行到后 3 行里。' + R(CR, 18, 36),
                  '只有步卡 trace 会降级：`ev` 缺省或是 fact、剩下的依据为空 → 改成 `infer`。' + R(CR, 73, 74),
                  '剔除了也返回 0；服务端后面又接 `;`，连参数错的 1 也不看。' + R(SV, 620, 621)],
                 [note('注释', '目标是读者看不到假出处；服务端这一遍是兜底。',
                       q='读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。', ref=A(CR, 8)),
                  note('注释', '剔除算正常完成，不算失败。', q='exit 0（剔除也算正常完成）；1 参数/格式错。', ref=A(CR, 11)),
                  note('推断', '降级而不拒收：证据本来就分 fact / infer / unknown 三档；依据全假只说明「没证据」，不说明结论错。提交没有单独讨论这一点。',
                       also=[('注释', '证据分级：fact（缺省）/ infer 有依据的推断 / unknown 尚未确认（必须写 need）', A(VA, 378))])],
                 ['它只证明「附近出现过这个词」：符号是子串匹配，`allocate` 会被 `deallocate` 命中。',
                  '**没写 refs** 的 fact 步卡根本不进检查，也就不会降级。' + R(CR, 61, 62),
                  '它**不猜子目录根**，直接用传进来的仓库根；embed 却会猜。卡路径相对子目录时：上下文完好，依据全灭（推演变体 B）。' + R(CR, 43),
                  '`lstrip("./")` 去的是字符集合不是前缀：`.github/x.yml` 会变成 `github/x.yml`，点目录里的依据会被误判不存在（推断，没有测试覆盖）。' + R(CR, 22)]),
             to='dive_post.refs'),

        part('val', '校验', [650, 8, 230, 170], m=[8, 500, 140, 200], kind='gate', n='3', code='validate',
             glyph=G_VAL(),
             say='唯一能拦下画布的', why='结构坏了渲染器扛不住', trap='不读仓库，查不出改代码',
             card=card(
                 '收尾第 ③ 步，也是**唯一的闸门**：对 canvas.json 做结构检查，再把里面的沙盘脚本真跑一遍。有 ERROR（或脚本自己崩了）→ 退出码 1 → rc=4，入库一步都不做。',
                 ['ERROR 主要几类：画布空 / id 撞；引用悬空（线端、note、步的 focus 指向不存在的卡）；行号越界 / 块重叠；缺必填（`layout.col/band`、overview 的几项）；步卡结构；**编造**（变量、黑盒函数名在「卡代码 ∪ files 全文」里找不到）；**沙盘**（缺 expected、超时 / 报错 / 输出不符）。',
                  'warn 只是提醒：字数预算、缺层（judgment、overview、gaps、boxes、scenarios）、某列超 3 张卡、有 file:line 却没有 `files`。服务端没传 `--strict`，warn 不影响退出码。' + R(VA, 662) + ' ' + R(SV, 636),
                  '它只打开 canvas.json 这一个文件，**从不读仓库**。' + R(VA, 116, 118)],
                 [note('注释', 'ERROR 的定义：渲染器扛不住的结构损坏。',
                       q='Errors are structural breakages the renderer cannot survive gracefully', ref=A(VA, 4)),
                  note('注释', 'warn 的定义：超了策展预算，不是坏了。',
                       q="Warnings are curation-budget violations from schema.md's hard limits.", ref=A(VA, 6)),
                  note('注释', '名字核对的语料包括 files 全文，所以「卡片没截到的那几行」里的名字也算真的。',
                       q='卡片没截到的行里的变量也算真', ref=A(VA, 356))],
                 ['「卡里的代码是不是源码原文」**没有闸门**：一张卡的 code 整段编造，embed 只在日志里记一行「跳过」，validate 不知道，画布照样入库；只有某个文件一张卡都没对上时才多一条 warn。' + R(VA, 135, 138),
                  'rc=4 不一定是 ERROR：字段类型意外时 validate 直接抛异常（比如对没有 `code` 的状态卡取 code），退出码同样是 1（推断，没构造用例跑过）。原因要去 `.log` 里找。' + R(VA, 260)]),
             to='dive_post.val'),

        part('lib', '入库', [990, 8, 220, 150], m=[8, 780, 140, 150], kind='out', n='4', code='cp + render',
             glyph=G_LIB0,
             say='cp + render + 来源记录', why='done 就等于已入库', trap='render 败了留半成品',
             card=card(
                 '收尾第 ④ 步：validate 过了，才把 canvas.json 拷进画布库、渲染出 html、写来源记录。',
                 ['一行 shell：`cp w 库/nano-vllm.json && render 库/nano-vllm.json 库/nano-vllm.html && { 写 .src.json; :; } || rc=6`。' + R(SV, 634),
                  'render 读的是**库里刚拷进去的 json**，只做一件事：把模板里唯一的占位符 `/*CANVAS_DATA*/null` 换成这份 JSON，并把 `</` 换成 `<\\/`。' + R(RD, 10, 25),
                  '来源记录那段后面接了 `:`，永远算成功；所以 rc=6 只可能来自 cp 或 render。'],
                 [note('提交', 'done 和「已入库」是一回事；来源记录只在入库时写，失败的任务不留记录。',
                       q='全过才入库（done ⇔ 已入库；rc 4/5/6 如实失败；来源记录入库时才写）', ref='c:4b43d42'),
                  note('注释', '`</` 要转义：数据里的字符串不能把 script 标签提前结束。',
                       q='</script> inside string data must not terminate the script tag', ref=A(RD, 23))],
                 ['render 失败时（按代码推）：首次生成 → 库里有 `nano-vllm.json`、**没有** html、没有来源记录，`.status` 是 6；同名画布已存在 → json 已是新的，html 还是旧的。首页只列 `*.html`，半成品看不见，也没人清理。没找到 rc=6 的测试。' + R(SV, 282),
                  '被 validate 拦下（rc=4）时这里一步都没走：库里什么都没动，同名旧画布原样保留（测试 code-canvas/tests/hub.mjs 第 630–632 行：被拦的任务既无 json 也无来源记录）。']),
             to='dive_post.shell'),

        part('repo', '仓库根 R', [10, 300, 190, 130], m=[216, 8, 116, 130], kind='ext', code='repo_dir',
             glyph=g_list(['nano-vllm-155c1586/', '只读']),
             say='共享缓存，只读', why='同一份代码', trap='两个脚本用法不同',
             card=card(
                 'embed 和 check_refs 拿来对照的源码目录。git 源就是这单 agent 读过的共享缓存 `.repos/nano-vllm-155c1586`；路径源是 `--repo` 指定的目录。',
                 ['post_pass 拼命令时把它当第二个参数传给两个脚本。' + R(SV, 735) + ' ' + R(SV, 749),
                  '粘贴代码的任务没有仓库：embed 和 check_refs 两段直接是空串，只剩 validate + 入库。' + R(SV, 621) + ' ' + R(SV, 628)],
                 [note('推断', '用 agent 读过的同一份缓存来核：核的是「agent 看到的代码」；缓存只在没有同仓任务时才刷新，任务进行中它不会变。')],
                 ['同一个 R，两个脚本用法不同：embed 会自己挪到子目录（猜根），check_refs 原样使用。',
                  'validate 根本不拿 R。']),
             ),

        part('w', 'canvas.json', [330, 290, 250, 170], m=[8, 250, 140, 170], kind='store', code='w',
             glyph=G_W(),
             say='两个修补工原地改它', why='数据只经这一个文件交接', trap='被拦下时它已被改过',
             card=card(
                 'agent 交在工作目录里的 `canvas.json`（代码里记作 `w`），也是这个框里**唯一**的交接物：四个零件不互相传参数，全围着它转。',
                 ['embed 原地改写：加顶层 `files`（`{卡里写的路径: 源文件全文}`），必要时改卡的 `file` 行号。' + R(EM, 79) + ' ' + R(EM, 88, 89),
                  'check_refs 原地改写：剔掉假依据、把依据剔光的步卡 ev 改成 infer；每次都整份重写。' + R(CR, 72, 76),
                  'validate 只读；过了才被 `cp` 进画布库。' + R(SV, 634)],
                 [note('推断', '只靠一个文件交接，每个脚本都能单独拿来跑——agent 自查用的就是同一批脚本，服务端只是再跑一遍。')],
                 ['被拦下的画布：库里什么都没动，但工作目录这份**已经被 embed / check_refs 改过**。',
                  '`files` 的键是卡里写的路径，validate 查「缺上下文」也用同样的路径比，所以 embed 换了子目录根也对得上。' + R(EM, 82) + ' ' + R(VA, 134)]),
             ),

        part('box', '沙盘子进程', [650, 300, 230, 130], m=[216, 520, 116, 140], kind='bg', code='python -I -c',
             glyph=g_kv([('次数', '4 + 3 = 7'), ('上限', '8 秒/次')]),
             say='agent 写的脚本真跑', why='防手写 expected', trap='-I 不是沙箱',
             card=card(
                 '画布里每个块沙盘（`block.run`）是 agent 蒸馏的一段自包含 Python 脚本 + 1–5 组输入，每组带一份预录输出 `expected`——静态页上读者看到的就是这份预录。validate 把每组输入真跑一遍，对不上就 ERROR。',
                 ['把输入值原样替换进脚本里唯一的 `__INPUT__`（输入值本身就是代码，比如 `tokens = [1, 2, 3, 4, 5, 6, 7, 8]`），起 `python -I -c <脚本>`，最多 8 秒；一个接一个跑，不并发。' + R(VA, 92, 95),
                  '三种 ERROR：超时、退出码非 0、输出（去掉首尾空白）≠ expected。' + R(VA, 96, 102),
                  '例子里两个沙盘：schedule 卡的「Prefill 批次」4 组输入、allocate 卡的「认领命中块」3 组；`lang` 缺省是 py，所以收尾**真跑 7 次**，最坏 7 × 8 秒。' + R(VA, 68)],
                 [note('注释', 'expected 必须是真跑出来的，不许想象。',
                       q='块沙盘：结构 + 真跑核验（expected 必须是实际运行的产物，不许想象）。', ref=A(VA, 65)),
                  note('提交', '真跑就是为了掐死编造的输出；`--no-exec` 只是逃生口。', q='掐死编造输出（--no-exec 逃生口）', ref='c:1c9fabe'),
                  note('注释', '8 秒上限：沙盘必须小而确定。', q='沙盘必须小而确定', ref=A(VA, 97))],
                 ['`-I` 只是 Python 的隔离模式（不读 `PYTHON*` 环境变量、不把当前目录放进 `sys.path`），**不是**文件系统 / 网络沙箱：脚本以 hub 用户的权限跑。',
                  '这次 `subprocess.run` 没设 `cwd`，继承任务脚本的目录——git 源就是共享缓存。读者在页面上点「试」走的 `/run` 却显式用 `/tmp`。为什么不同，底稿没查到。' + R(SV, 740) + ' ' + R(SV, 1212),
                  '`--no-exec` 只有问答补丁那条路在用，生成收尾不用；为什么分开也没查到。' + R(SV, 138)]),
             to='dive_post.run'),

        part('rc', '退出码 rc', [990, 300, 220, 130], m=[216, 790, 116, 130], kind='store', code='$rc',
             glyph=G_RC(),
             say='一个数，最后进 .status', why='原因只在 .log', trap='修补工碰不到它',
             card=card(
                 '整串命令共用的一个 shell 变量。进来时是 claude 的退出码，收尾可能把它改成 4 / 5 / 6，最后外层 `echo $rc > .status`。首页看到的 done / failed(4) 就是它。',
                 ['守卫：rc 不是 0 → 整个收尾跳过；canvas.json 不在 → rc=5。' + R(SV, 633),
                  '只有两处会改它：validate 不过 → 4；cp / render 失败 → 6。embed、check_refs 后面接的是 `;`，碰不到它。' + R(SV, 627, 635),
                  '退出码表写在 post_pass 的文档串里：3 升级被拒 / 4 validate 未过 / 5 没产出 / 6 渲染失败。' + R(SV, 609, 610)],
                 [note('注释', '收尾的立场：不信任 agent 守规程。', q='服务端入库（不信任 agent 守规程）', ref=A(SV, 609)),
                  note('推断', '一个数字就够首页分清成败；细节交给 `.log`。')],
                 ['done 看不出 embed 跳过了几张卡、check_refs 剔了几条依据；rc=4 的原因也只在 `.log` 里（三个零件的输出都追加进 `.log`）。' + R(SV, 627)]),
             ),
    ]

    wires = [
        wire('q_er', 'embed', 'refs', '；不看退出码', ml='；照跑', why='无代码卡不该失败', trap='embed 败了也不停',
             card=card('embed 跑完，不管成败，接着跑 check_refs。',
                       'shell 里两段之间是 `;` 而不是 `&&`：embed 的 exit 2 不会让后面停下，也不会改 rc。' + R(SV, 627, 628),
                       [note('提交', '有意为之：预览图没有代码卡，embed 必然 exit 2，不能因此失败。',
                             q='预览图等无代码卡时自动跳过不碰状态码', ref='c:690022d')],
                       '最容易以为「embed 失败会停」。它不停——但它的失败会让 validate 更严（推演最后一步）。')),
        wire('q_rv', 'refs', 'val', '；不看退出码', ml='；照跑', why='两者互不依赖',
             card=card('check_refs 跑完，同样不看退出码，进 validate。',
                       'check_refs 那段也以 `;` 结尾。' + R(SV, 620, 621),
                       [note('推断', 'check_refs 和 validate 互不依赖（validate 只验 refs 的形状，' + R(VA, 396, 404) + '），先后没有说明。')])),
        wire('q_vl', 'val', 'lib', '退出码 0 才 cp', ml='过了才 cp', why='全过才入库',
             card=card('validate 退出码 0 才进入库。',
                       '`if validate w >> .log 2>&1; then cp … ; else rc=4; fi`。' + R(SV, 634, 635),
                       [note('注释', 'done 和「已入库」是一回事。', q='全过才写进画布库（任务 done ⇔ 画布已入库）', ref=A(SV, 588))])),
        wire('d_ew', 'embed', 'w', '写 files、改行号', ml='写 files', why='validate 的语料从这来',
             card=card('embed 把结果写回 canvas.json：`files` + 修正后的行号。',
                       '整份 JSON 重写；`files` 只含至少有一张卡对上的文件。一张都没对上时**什么都不写**。' + R(EM, 82, 89),
                       [note('注释', '这是 embed → validate 唯一的数据耦合：validate 核「名字是不是编的」，语料 = 卡代码 ∪ files 全文。所以 embed 要排在 validate 前面（依赖是明文的；「必须先于」是从这推出来的）。',
                             q='只对"卡片代码 ∪ 引用文件全文"核对名字，不许编造', ref=A(VA, 563))])),
        wire('d_rw', 'refs', 'w', '剔假依据、降级', ml='剔依据', why='只删依据，不动结论',
             card=card('check_refs 把剔过的 refs 和降级后的 ev 写回 canvas.json。',
                       '核不过的从 refs 里拿掉；trace 的 refs 剔光且 ev 是 fact → 改成 infer；最后整份重写。' + R(CR, 72, 76),
                       [note('提交', '依据必须在仓库里成立，核不过就剔。', q='须在仓库里成立，核不过剔除，依据剔光', ref='c:4b43d42')])),
        wire('d_wv', 'w', 'val', '只读这一个文件', ml='只读 w', why='不碰仓库', trap='不核卡代码真假',
             card=card('validate 读 canvas.json：它唯一的输入。',
                       'main 只读命令行给的那一个 JSON，不碰仓库。' + R(VA, 116, 118),
                       [note('推断', '不需要仓库，agent 自查、问答补丁、服务端收尾都能用同一个脚本；代价是核不了「卡代码是不是原文」。')],
                       '「卡内代码是原文」是 SKILL 的要求，但没有机械闸门。' + R(SK, 136))),
        wire('r_e', 'repo', 'embed', '读源码（会猜根）', ml='读源码', why='路径写错也兜得住',
             card=card('embed 从 R 读源文件。',
                       'R 命中的卡路径不足全部时，扫 R 的一级子目录，挑命中最多的当根。' + R(EM, 43, 51),
                       [note('文档', 'agent 违反「路径相对仓库根」时，用子目录根兜住。', q='考生违规但可用子目录根兜住', ref='ROADMAP.md:144')])),
        wire('r_r', 'repo', 'refs', '读源码（不猜根）', ml='读源码', trap='子目录时全灭', mlp=[280, 184],
             card=card('check_refs 从 R 读源文件。',
                       '直接用传进来的 R（只做一次 `resolve()`），不猜子目录。' + R(CR, 43),
                       [note('推断', '没查到为什么不跟 embed 一样猜：两者是不同提交加的（4b43d42 与 690022d），没看到合并考虑的文字。')],
                       '卡路径相对子目录时，embed 嵌得上，check_refs 全判「文件不存在」。')),
        wire('x_vs', 'val', 'box', '真跑 7 次，≤8 秒', ml='真跑 ×7', why='expected 不许手写',
             card=card('validate 对每组输入起一个子进程。',
                       '一个接一个，不并发；每个最多 8 秒。例子里 7 个。' + R(VA, 94, 95),
                       [note('文档', 'SKILL 把「沙盘的 expected 是手写的」列为编造。', q='沙盘的 expected 是手写的', ref=A(SK, 203))])),
        wire('c_v', 'val', 'rc', '有 ERROR → rc=4', ml='rc=4', why='只看退出码',
             card=card('validate 退出码非 0 → rc=4，入库整段不执行。',
                       '`else rc=4; fi`。ERROR 和脚本抛异常，退出码都是 1。' + R(SV, 635) + ' ' + R(VA, 662),
                       [note('推断', '服务端不解析 validate 的输出，只看退出码；原因留在 `.log` 给人看。')])),
        wire('c_l', 'lib', 'rc', '失败 → rc=6', ml='rc=6', trap='json 拷进去就不回滚',
             card=card('cp 或 render 失败 → rc=6。',
                       '`cp && render && {来源记录; :;} || rc=6`：`||` 管整条 `&&` 链。' + R(SV, 634),
                       [note('推断', '用 `&&` 串：前一步失败后一步不做，不会留下指向坏 html 的来源记录。')],
                       'cp 成功、render 失败时，库里已经有新 json 了，不回滚。')),
    ]

    steps = [
        beat('进场：rc=0，canvas.json 在',
             '顶层推演「服务端收尾：再查一遍」那一步，从这里展开。claude 退出码 0，工作目录里有 canvas.json → 进收尾；R = 共享缓存 `.repos/nano-vllm-155c1586`。'
             '此刻 canvas.json：9 张代码卡 + 1 张状态卡，**还没有 `files`**；6 条依据（设：沿用 agent 那一层的设定，agent 已自查过；仓库里那份旧格式样板其实没有依据）。',
             note('注释', '收尾的立场：不信任 agent 守规程，agent 自查过也再来一遍。', q='服务端入库（不信任 agent 守规程）', ref=A(SV, 609)),
             focus=['w', 'rc', 'repo'],
             state={'rc': G_RC('0（claude）', '—')}),
        beat('embed：9 张卡全对上，嵌 5 个文件',
             '逐卡把 `code` 和 R 里同一文件「从起始行起 n+40 行」比（两边删掉空白和 `//` `#` `\\` 后看前缀）。9 张全对上，没有漂移。'
             '日志一行「嵌入 5 文件 / 9 卡，共 23 KB」；canvas.json 多了 `files`：5 个源文件的**全文**。',
             note('注释', 'agent 自查时已经跑过一次；重跑只是把 files 重算一遍。', q='幂等：重跑只是重算 files。', ref=A(EM, 11)),
             focus=['embed', 'r_e', 'd_ew', 'w'],
             state={'embed': G_EMB('ok', 'ok', 'ok', t=('根：R 本身 5/5', '9/9 卡对上', '写回 files')),
                    'w': G_W(files='5 文件')}),
        beat('check_refs：6 条依据全过',
             '`;` 之后接着跑。每条 `[文件, 行号, 符号]` 依次问：格式 → 越没越出仓库 → 文件在不在 → 行号越没越界 → 符号在不在该行上下 3 行里。'
             '6 条全过，日志「依据核对：6 条，通过 6，剔除 0」；canvas.json 被整份重写一遍，内容没变。',
             note('注释', '服务端这一遍是兜底：读者永远看不到假出处。',
                  q='读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。', ref=A(CR, 8)),
             focus=['refs', 'q_er', 'r_r', 'd_rw'],
             state={'refs': G_REF('ok', 'ok', 'skip', t=('6 条文件都在', '符号都在', '无需降级'))}),
        beat('validate：0 ERROR，沙盘真跑 7 次',
             '只打开 canvas.json。结构、引用全对；名字核对的语料 = 卡代码 ∪ `files` 全文。两个沙盘——schedule 的 4 组输入、allocate 的 3 组——'
             '各起一个 `python -I -c`，输出和 expected 一致 → **0 ERROR**、若干 warn，退出码 0。',
             note('提交', '真跑就是为了掐死编造的输出。', q='掐死编造输出（--no-exec 逃生口）', ref='c:1c9fabe'),
             focus=['val', 'd_wv', 'x_vs', 'box'],
             state={'val': G_VAL('ok', 'ok', 'ok', t=('结构 / 引用', '名字不许编造', '沙盘 7/7')),
                    'box': g_kv([('跑了', '7 次'), ('不符', '0')])}),
        beat('入库，.status = 0',
             '`cp` → `<hub>/nano-vllm.json`；render 把这份 json 塞进模板的占位符 → `nano-vllm.html`；写 `nano-vllm.src.json`。'
             '三步 `&&` 串着全成功，rc 保持 0，外层 `echo $rc` 写进 `.status`。这就是顶层推演「入库，写 .status = 0」那一步。',
             note('提交', 'done 就等于已入库；来源记录只在入库时写。',
                  q='全过才入库（done ⇔ 已入库；rc 4/5/6 如实失败；来源记录入库时才写）', ref='c:4b43d42'),
             focus=['lib', 'q_vl', 'c_l', 'rc'],
             state={'lib': G_LIB_OK, 'rc': G_RC('0', '0 → done')}),
        beat('变体 A：卡的行号写成了 72',
             '（设）preempt 卡的 `file` 写成 scheduler.py 第 72 行，真在 75。从 72 起比不上 → 取卡代码首行，规范化成 `defpreempt(self,seq:Sequence):`，'
             '从文件第 1 行往下扫，第一个「首行相同、整段也对上」的是 75 → 改写成第 75 行，日志「行漂移修正: preempt 72→75」。画布照常往下走。',
             note('注释', '行漂移修正：对不上时搜全文，机械修正行号。', q='行漂移修正：对不上时在全文里搜索唯一匹配点，机械修正 file 行号', ref=A(EM, 7)),
             focus=['embed', 'd_ew', 'w'],
             state={'embed': G_EMB('no', 'ok', 'ok', t=('从 72 行比', '扫全文：75', '改成第 75 行')),
                    'w': G_W(files='5 文件', extra=('preempt', '72→75 行')),
                    'refs': G_REF(), 'val': G_VAL(), 'box': g_kv([('次数', '4 + 3 = 7'), ('上限', '8 秒/次')]),
                    'lib': G_LIB0, 'rc': G_RC('0（claude）', '—')}),
        beat('变体 B ①：embed 自己找到子目录根',
             '（设）真实代码在 `nanovllm/` 包下，卡路径却写 `engine/…`。embed 先数 R 能命中几条卡路径：0 < 5 → 扫一级子目录，nanovllm 命中 5 条 → '
             '日志「仓库根自动定位到子目录: nanovllm」，照常嵌入 5 个文件。',
             note('提交', '猜根是为了 monorepo 这类卡路径相对子目录的情况。', q='仓库根自动定位（monorepo 子目录如 codex-rs，', ref='c:690022d'),
             focus=['embed', 'r_e', 'repo'],
             state={'embed': G_EMB('no', 'ok', 'ok', t=('R 命中 0/5', 'nanovllm 5/5', '嵌 5 文件')),
                    'repo': g_list(['nano-vllm-155c1586/', '└ nanovllm/ ← embed 用'], on=1),
                    'w': G_W(files='5 文件')}),
        beat('变体 B ②：check_refs 不猜根，依据全灭',
             'check_refs 直接用 R：R 下面没有 engine/scheduler.py → 6 条依据都是「文件不存在」，全剔；写着 fact、依据剔光的步卡全降成 infer。'
             '日志形如「依据核对：6 条，通过 0，剔除 6」。结果：**上下文完好，依据全灭**，画布照样入库。',
             note('推断', '两个脚本是不同提交加的，猜根只加在了 embed 里；没看到为什么不统一。'),
             focus=['refs', 'r_r', 'd_rw', 'w'],
             state={'refs': G_REF('no', 'skip', 'ok', t=('文件不存在 ×6', '剔除 6/6', 'fact → infer')),
                    'w': G_W(files='5 文件', refs='0（剔 6）', ev='infer')}),
        beat('变体 D：依据是编的，agent 没自查',
             '（设）steps[4] 的两条依据：scheduler.py 第 94 行（全文才 93 行）、第 88 行 + 符号 preempt（85–91 行里没有；它在 62、64、75 行）。'
             '日志：剔除「行号越界（全文 93 行）」、剔除「符号「preempt」不在该行 ±3 行内」、降级「依据全部核不过，ev → infer」。画布**照常入库**，这一步成了推断。',
             note('提交', '核不过就剔，剔光就降级，不拒收。', q='须在仓库里成立，核不过剔除，依据剔光', ref='c:4b43d42'),
             focus=['refs', 'd_rw', 'w'],
             state={'refs': G_REF('no', 'no', 'ok', t=('94 > 93 行', '88±3 无 preempt', '降级 infer')),
                    'repo': g_list(['nano-vllm-155c1586/', '只读']),
                    'w': G_W(files='5 文件', refs='剔 2 条', ev='infer')}),
        beat('变体 C：沙盘 expected 被手改 → rc=4',
             '（设）allocate 沙盘第 1 组「完全命中」真跑出「命中前缀块数: 2 → 免算 token 数: 8」，expected 里却把 2 改成了 3。'
             'validate：ERROR「expected 与实际输出不符——预录输出必须是真跑的产物」→ 退出码 1 → **rc=4**。cp 根本没执行：库里没有新文件，同名旧画布原样保留；原因只在 `.log`。',
             note('注释', 'expected 必须是真跑的产物。', q='块沙盘：结构 + 真跑核验（expected 必须是实际运行的产物，不许想象）。', ref=A(VA, 65)),
             focus=['box', 'val', 'c_v', 'rc', 'lib'],
             state={'refs': G_REF(), 'w': G_W(files='5 文件'),
                    'val': G_VAL('ok', 'ok', 'no', t=('结构 / 引用', '名字不许编造', '沙盘 6/7')),
                    'box': g_kv([('跑了', '7 次'), ('不符', '1 组')]),
                    'lib': G_LIB_NO, 'rc': G_RC('4', '4 → failed')}),
        beat('变体 E：embed 没嵌成，validate 反而更严',
             '（设）agent 在 boxes 里写了黑盒 `is_finished`。卡代码里没有 `is_finished(` 的调用，它只在 llm_engine.py 全文第 58、73 行。有 files 时 → 过。'
             '若 embed 一张都没对上：exit 2 被 `;` 吞掉、不写 files → 语料只剩卡代码 → ERROR「is_finished( 在卡片代码与引用文件里都没被调用——不许编造」→ rc=4。',
             note('推断', 'embed 自己不拦，但它的产物就是 validate 的语料：embed 失败 = 语料变小 = 更容易被判「编造」。'),
             focus=['embed', 'q_er', 'w', 'd_wv', 'val', 'rc'],
             state={'embed': G_EMB('skip', 'no', 'no', t=('猜仓库根', '0/9 对上', 'exit 2 不写')),
                    'w': G_W(), 'box': g_kv([('次数', '4 + 3 = 7'), ('上限', '8 秒/次')]),
                    'val': G_VAL('ok', 'no', None, t=('结构 / 引用', 'is_finished( ✗', '沙盘真跑')),
                    'rc': G_RC('4', '4 → failed')}),
    ]

    return node(
        'dive_post', '服务端收尾里面', '服务端收尾：两个修补工和一道闸门',
        '顶层「服务端收尾」框里面：embed 和 check_refs 原地修补 canvas.json、从不拦；只有 validate（含沙盘真跑）能让入库作废。',
        lead='你现在在顶层图第 ⑥ 个框「服务端收尾」里面：claude 退出码 0 之后、写 `.status` 之前，同一个后台 bash 顺序拉起的四个短命 `python3`。'
             '它们不互相传参数，全围着工作目录里那一份 canvas.json 转。推演接着顶层那一单（nano-vllm +「讲讲调度器怎么工作」）：'
             '先走正常的一遍，再拿五个变体看什么会被悄悄改掉、什么会被拦下。',
        src=SV + ':605-637',
        blocks=[scene(1220, 460, parts, wires, steps, mw=340, mh=950,
                      cap='收尾里面：① embed →（;）② check_refs →（;）③ validate → ④ 入库；只有 ③ 和 ④ 能改 rc')],
        children=[_src_shell(), _src_embed(), _src_refs(), _src_val(), _src_run()])


# ---------------------------------------------------------------- 源码页
def _src_shell():
    return node(
        'dive_post.shell', '收尾那串 shell', 'post_pass：分号和 if 决定谁能拦',
        '整个框的骨架：post_pass 拼出的那串 shell。`;` 接的两段不看结果，`if` 接的 validate 决定入不入库。',
        lead='你在「服务端收尾」框里面的「入库」那一格往下：这是把四个零件串起来的那串 shell，以及入库时调用的 render.py。',
        src=SV + ':605-637',
        blocks=[
            code(SV, [(605, 637)], notes=[
                at(605, '推断', '每个零件都是 `python3 <脚本>`：同一个 bash 顺序拉起几个短命子进程，没有并发。', has='tool = lambda f'),
                at((609, 610), '注释', '退出码表：3 升级被拒 / 4 validate 未过 / 5 没产出 / 6 渲染失败。立场是不信任 agent 守规程。',
                   q='服务端入库（不信任 agent 守规程）'),
                at(612, '推断', '缩放画布走另一条路（build_zoom）；本图走的是默认分支。', has='if zoom:'),
                at((620, 621), '推断', 'check_refs 那段：以 `;` 结尾，退出码不影响 rc；粘贴代码（没有仓库）时是空串。', has='check_refs.py'),
                at(622, '推断', '升级任务（补层）也走另一条路：合并失败 rc=3。', has='if upgrade:'),
                at((627, 628), '提交', 'embed 那段同样以 `;` 结尾：exit 2（没有卡可嵌）不会让后面停下，也不改 rc。为的是预览图这种本来就没有代码卡的画布。',
                   q='预览图等无代码卡时自动跳过不碰状态码', ref='c:690022d', has='embed_context.py'),
                at((629, 632), '推断', '来源记录 `{"git_url": …}`：只是一句 printf，排在入库链最后。', has='sidecar'),
                at(633, '推断', '两层守卫：claude 的 rc 是 0 → canvas.json 在；然后 embed、check_refs 依次跑，都不看结果。', has='if [ "$rc" -eq 0 ]'),
                at(634, '提交', '唯一的闸门：validate 过了才 cp → render → 来源记录（`:` 让它永远成功），cp 或 render 失败 rc=6。',
                   q='全过才入库（done ⇔ 已入库；rc 4/5/6 如实失败；来源记录入库时才写）', ref='c:4b43d42', has='rc=6'),
                at(635, '推断', 'validate 不过 → rc=4；canvas.json 不在 → rc=5。', has='else rc=4'),
                at((636, 637), '推断', 'validate 只带一个参数：没有 `--strict`（warn 不拦），也没有 `--no-exec`（沙盘真跑）。', has='val=tool("validate.py")'),
            ], cap='post_pass（默认分支在 627-637）'),
            code(RD, [(10, 26)], notes=[
                at(10, '推断', '模板里唯一的占位符。', has='PLACEHOLDER = '),
                at(17, '推断', '读的是库里刚拷进去的那份 json（服务端传的第一个参数）。', has='data_path.read_text'),
                at(23, '注释', '`</` 转义成 `<\\/`：数据里的字符串不能把 script 标签提前结束。',
                   q='</script> inside string data must not terminate the script tag'),
                at(25, '推断', '写出单文件 html。这一步失败 → rc=6，但 json 已经在库里，不回滚。', has='template.replace'),
            ], cap='render.py：把 JSON 塞进模板'),
        ])


def _src_embed():
    return node(
        'dive_post.embed', 'embed 源码', 'embed_context：比对、行漂移、猜根',
        '卡代码怎么和源文件比、行号不对怎么改、卡路径相对子目录时怎么猜根、什么时候 exit 2。',
        lead='你在「服务端收尾」框里面的「嵌上下文」那一格往下：embed_context.py 的 main 从头读到尾，就是推演里主线、变体 A、B、E 的出处。',
        src=EM,
        blocks=[
            code(EM, [(4, 13), (22, 23)], notes=[
                at(6, '注释', '规范化和评分器同一套：容忍换行重排插进来的 `//` `#` `\\` 续行符。', q='与评分器同款'),
                at(7, '注释', '注释说「唯一匹配点」，代码其实取第一个命中（见第 74–75 行）。',
                   q='行漂移修正：对不上时在全文里搜索唯一匹配点，机械修正 file 行号'),
                at(13, '推断', '退出码 2 = 一张都没嵌成；服务端用 `;` 接，这个 2 被吞掉。', has='2 没有任何卡可嵌入'),
                at((22, 23), '推断', '规范化：删空白、`//`、`#`、`\\`。例子里 preempt 首行变成 `defpreempt(self,seq:Sequence):`。', has='def norm'),
            ], cap='文件头 + 比较规则'),
            code(EM, [(35, 51)], notes=[
                at((37, 38), '推断', '候选路径 = 所有卡 `file` 冒号前的部分；例子里 5 个。', has='paths = '),
                at((40, 42), '推断', 'score = 这个根下面实际存在几条卡路径。变体 B：R 得 0。', has='def score'),
                at(43, '推断', '只有 R 没全中才去找子目录。', has='if bs < len(paths)'),
                at((44, 48), '推断', '只看一级子目录，跳过 `.` 开头，按名字排序；严格更多才换，平手先到先得。变体 B：nanovllm 得 5。', has='startswith(".")'),
                at((49, 51), '提交', '换了根只影响读文件；`files` 的键仍是卡里写的路径。', q='仓库根自动定位（monorepo 子目录如 codex-rs，',
                   ref='c:690022d', has='repo = best'),
            ], cap='猜仓库根'),
            code(EM, [(53, 95)], notes=[
                at((56, 57), '推断', '没有 code、或 file 不是「路径:行号」的卡静默略过，不算跳过。', has='if not m or not c.get("code")'),
                at((59, 65), '推断', '文件不存在、超过 400KB：记为跳过。', has='文件不存在'),
                at((69, 70), '推断', '比对：从起始行取 n+40 行，规范化后看卡代码是不是前缀。40 这个余量没有出处。', has='n + 40'),
                at((72, 75), '推断', '对不上：拿首行从第 1 行往下扫，`next(...)` 取**第一个**「首行相同、整段对上」的行——不查第二个，也不看离原行号远近。变体 A：72 对不上，扫到 75。', has='hit = next('),
                at((76, 78), '推断', '全文都找不到：这张卡跳过，`file` 不动。', has='与仓库对不上，跳过'),
                at((79, 81), '推断', '找到了：改写卡的 file 行号，记一条修正。', has='c["file"] = '),
                at(82, '推断', '这张卡对上，就把**整个文件**放进 files，键是卡里写的路径。', has='files[m.group(1)] = text'),
                at((85, 87), '提交', '一张都没嵌成：exit 2，而且在写文件**之前**退出——canvas.json 不变。',
                   q='预览图等无代码卡时自动跳过不碰状态码', ref='c:690022d', has='sys.exit(2)'),
                at((90, 93), '推断', '日志：主线「嵌入 5 文件 / 9 卡，共 23 KB」；变体 A 多一行「行漂移修正: preempt 72→75」。', has='行漂移修正'),
            ], cap='逐卡比对、行漂移修正、写回'),
        ])


def _src_refs():
    return node(
        'dive_post.refs', 'check_refs 源码', 'check_refs：五问一条依据，剔光就降级',
        '一条依据怎么被判真假、窗口为什么是「前 3 后 3」、什么时候降级、为什么永远返回 0。',
        lead='你在「服务端收尾」框里面的「核依据」那一格往下：check_refs.py 全部逻辑就两个函数，推演里主线、变体 B ②、D 都出自这里。',
        src=CR,
        blocks=[
            code(CR, [(5, 11)], notes=[
                at((5, 7), '推断', '规则一句话：文件在、行号不越界、符号在 ±3 行内；剔光的「已确认」步卡降级。', has='±3'),
                at(8, '注释', '读者看不到假出处；服务端这一遍是兜底。', q='读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。'),
                at(11, '注释', '剔除也算正常完成。', q='exit 0（剔除也算正常完成）；1 参数/格式错。'),
            ], cap='文件头'),
            code(CR, [(18, 36)], notes=[
                at((19, 21), '推断', '格式：[文件, 正整数行号, 可选符号]。', has='格式应为'),
                at(22, '推断', '`lstrip("./")` 去的是字符集合：`.github/x.yml` 会变成 `github/x.yml`（推断，没有测试）。', has='lstrip'),
                at((23, 26), '推断', 'resolve 之后必须还在仓库里：挡住 `../` 越出。', has='路径越出仓库'),
                at((27, 28), '推断', '变体 B ②：R 下面没有 engine/scheduler.py → 文件不存在。', has='文件不存在'),
                at((30, 31), '推断', '变体 D：94 行 > 全文 93 行 → 「行号越界（全文 93 行）」。', has='行号越界'),
                at((32, 35), '推断', '窗口 = 下标 [行-4, 行+3)，即该行前 3 行到后 3 行；符号是子串匹配。变体 D：88 行看 85–91，没有 preempt。为什么是 ±3 没有出处。', has='ref[1] - 4'),
            ], cap='ref_ok：一条依据的五问'),
            code(CR, [(57, 82)], notes=[
                at((60, 62), '推断', '没有 refs 的直接跳过——没写依据的 fact 步卡不会被降级。', has='if not refs:'),
                at(72, '推断', '只留下核过的依据。', has='h["refs"] = good'),
                at((73, 75), '提交', '降级条件：是步卡 trace、依据剔光、ev 缺省或 fact。', q='须在仓库里成立，核不过剔除，依据剔光',
                   ref='c:4b43d42', has='h["ev"] = "infer"'),
                at(76, '推断', '不管剔没剔，都整份重写 canvas.json。', has='cj.write_text'),
                at((77, 81), '推断', '日志：主线「依据核对：6 条，通过 6，剔除 0」；变体 D 还有两行「剔除」和一行「降级」。', has='依据核对'),
                at(82, '推断', '永远返回 0：服务端也不看它。', has='return 0'),
            ], cap='main：剔除、降级、写回'),
        ])


def _src_val():
    return node(
        'dive_post.val', 'validate 源码', 'validate：只读一个 JSON，ERROR 才拦',
        'ERROR 和 warn 的分界、它为什么不读仓库、名字核对的语料怎么拼（embed 在这里起作用）。',
        lead='你在「服务端收尾」框里面的「校验」那一格往下：这里挑出三段——入口（只读一个文件）、语料（embed 的 files 在这里被用上）、退出码。沙盘真跑在隔壁一页。',
        src=VA,
        blocks=[
            code(VA, [(1, 8)], notes=[
                at((4, 5), '注释', 'ERROR：渲染器扛不住的结构损坏。', q='Errors are structural breakages the renderer cannot survive gracefully'),
                at(6, '注释', 'warn：超了策展预算。', q="Warnings are curation-budget violations from schema.md's hard limits."),
            ], cap='文件头：ERROR 与 warn 的定义'),
            code(VA, [(111, 138)], notes=[
                at(115, '注释', '`--no-exec` 跳过真跑；服务端收尾没带。', q='跳过沙盘真跑核验（无解释器的环境用）'),
                at(118, '推断', '只读 args[0] 这一个 JSON，不碰仓库。', has='json.loads(Path(args[0])'),
                at(128, '注释', '有 file:line 却没 files：只是 warn。', q='上下文全文：有 file:line 的代码卡应有 files 映射兜底（embed_context.py）'),
                at((129, 134), '推断', '比的是卡里写的路径（冒号前）和 files 的键；状态卡等豁免。', has='have_files'),
                at((135, 138), '推断', '只是 warn：一张卡的代码编造了，只要同文件别的卡对上，连这条 warn 都没有。', has='warn('),
            ], cap='入口 + 缺上下文的 warn'),
            code(VA, [(563, 593)], notes=[
                at(563, '注释', '名字只对「卡代码 ∪ 引用文件全文」核对。', q='只对"卡片代码 ∪ 引用文件全文"核对名字，不许编造'),
                at((564, 565), '推断', '语料 = 所有卡的 code + files 的全部值。embed 没写 files 时，这里只剩卡代码。', has='corpus = '),
                at((581, 582), '推断', '黑盒名后面要跟 `(` 才算被调用。变体 E：`is_finished(` 只在 llm_engine.py 全文第 58、73 行，卡代码里没有——没有 files 就 ERROR。', has='不许编造'),
                at((590, 592), '推断', '已经作为卡展开的函数不算黑盒（只 warn）。', has='不算黑盒'),
            ], cap='黑盒核对：embed → validate 唯一的数据耦合'),
            code(VA, [(657, 662)], notes=[
                at(662, '推断', '有 ERROR → 1；warn 只在 `--strict` 时才算失败，服务端没传。', has='sys.exit(1 if E'),
            ], cap='退出码'),
        ])


def _src_run():
    return node(
        'dive_post.run', '沙盘真跑源码', 'check_run：hub 上真跑 agent 写的脚本',
        '沙盘的结构规则、输入怎么替换进脚本、子进程怎么起、三种 ERROR。',
        lead='你在「服务端收尾」框里面的「沙盘子进程」那一格往下：validate 里的 check_run，例子里它在 hub 上真跑了 7 次。',
        src=A(VA, 61, 102),
        blocks=[
            code(VA, [(61, 102)], notes=[
                at(61, '推断', '默认真跑。', has='NO_EXEC = False'),
                at(65, '注释', 'expected 必须是真跑的产物。', q='块沙盘：结构 + 真跑核验（expected 必须是实际运行的产物，不许想象）。'),
                at(68, '推断', 'lang 缺省是 py：例子两个沙盘都会真跑。', has='run.get("lang", "py")'),
                at((69, 74), '推断', '结构：脚本里恰好一处 `__INPUT__`；输入 1–5 组。', has='__INPUT__'),
                at((86, 88), '推断', '缺 expected 直接 ERROR。', has='缺 expected'),
                at((89, 90), '推断', '非 Python 或 `--no-exec`：只验结构，不跑。', has='NO_EXEC'),
                at(92, '推断', '输入值原样替换进脚本——输入本身就是代码（如 `tokens = [1, 2, 3, 4, 5, 6, 7, 8]`）。', has='harness.replace'),
                at((94, 95), '推断', '`-I` 隔离模式、8 秒上限、**没有 cwd**：继承任务脚本的目录（git 源 = 共享缓存）。不是文件系统或网络沙箱。', has='timeout=8'),
                at((96, 97), '注释', '超时就是 ERROR：沙盘必须小而确定。', q='沙盘必须小而确定'),
                at((99, 100), '推断', '退出码非 0：带 stderr 末尾 200 字的 ERROR。', has='沙盘运行报错'),
                at((101, 102), '提交', '输出去掉首尾空白后和 expected 比，不符 = ERROR。变体 C 就停在这里。',
                   q='掐死编造输出（--no-exec 逃生口）', ref='c:1c9fabe', has='expected 与实际输出不符'),
            ], cap='check_run 全文'),
        ])
