# 下一层：顶层「生成 agent」那个框里面。
# 事实全部来自研究员底稿 research/manuscript-dive_agent.md（仓库 HEAD 79153e7），例子接顶层那一单。
from cv import *

SV = 'code-canvas/serve.py'
SK = 'code-canvas/SKILL.md'
RC = 'code-canvas/reader_check.py'
CR = 'code-canvas/check_refs.py'
VA = 'code-canvas/validate.py'
EM = 'code-canvas/embed_context.py'
EX = 'code-canvas/extract.py'
JOB = 'j20261123-101530-482'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def R(f, a, b=None):
    return '`%s`' % A(f, a, b)


def nodes():
    return {'dive_agent': _agent()}


# ---------------------------------------------------------------- 机器图
def _agent():
    parts = [
        part('prompt', '提示词', [20, 8, 200, 130], m=[8, 8, 170, 140], kind='ext', n='1', code='<id>.prompt',
             glyph=g_list(['head：读 SKILL.md', '任务：仓库 + 关注点', 'tail：交付 + 契约'], on=None),
             say='三段：读 SKILL、任务、契约', why='只给标准和契约，不给步骤', trap='只有契约是写死的',
             card=card(
                 'agent 启动时拿到的**全部**输入：一段文本，没有对话、没有别的通道。它决定了这个框里哪些事是死的、哪些交给 agent。',
                 ['三段拼成：`head + task_line + tail`。' + R(SV, 736),
                  'head：读 SKILL.md，并说明它「不是一条要逐步执行的管线」。' + R(SV, 668, 669),
                  'task：仓库在哪（缓存路径 + 原始 URL）、关注点是什么。例子里是「讲讲调度器怎么工作」。' + R(SV, 713),
                  'tail：交付文件的**绝对路径**、校验标准、完成标准（SKILL 第 1 节六条）、自查建议、「不要写入画布库」、「完成后打印 DONE」。' + R(SV, 659, 667)],
                 [note('注释', '提示词只给三样硬的（交付在哪、校验标准、不许入库），其余明说交给 agent。',
                       q='其余怎么做由你判断。', ref=A(SV, 663)),
                  note('提交', '改版前提示词是编号步骤（「严格按其管线执行」），提交 f4bbe29 换成了现在的「判断标准」写法；提交只有标题，没写为什么换。',
                       q='目标+判断标准+决策清单取代编号管线', ref='c:f4bbe29'),
                  note('推断', '改版的动机大概是 agent 按清单填空：SKILL 第 10 节把「为了消掉 warn 而写出空洞的内容」列为失败模式。')],
                 ['自查（渲染、截图、reader_check）在提示词里是**建议**口吻，没有任何东西检查它做没做。',
                  '粘贴代码的任务没有仓库，tail 里 check_refs / embed 两项是有条件拼进去的。' + R(SV, 658) + ' ' + R(SV, 661)]),
             to='dive_agent.prompt'),

        part('extract', '结构提取', [330, 8, 200, 130], m=[212, 8, 120, 140], kind='bg', code='extract.py',
             glyph=g_text('可选', '跑不跑由 agent 定'),
             say='可选：机械抽卡和线', why='材料，不是答案', trap='不跑也能交',
             card=card(
                 '一个零 LLM 的提取器：扫仓库缓存里的 `.py`，毫秒级产出「结构层」——每个函数一张卡（原文逐字）、调用线、粗布局，写成工作目录里的 `structure.json`。',
                 ['只认顶层函数和类的直接方法；只扫 `.py`。' + R(EX, 8),
                  '调用线只按名字最后一段匹配，且这个名字在全仓只对应一张卡时才画。' + R(EX, 111, 120),
                  '列 = 从入度 0 的卡开始的调用深度（最多到 col 4）；同列按文件和起始行排 band。每张卡都是折叠的。' + R(EX, 181, 214),
                  '叙事层（故事线、注释、推演）一概不产出。' + R(EX, 4, 6)],
                 [note('文档', 'SKILL 把它定位成研究材料：事实字段（代码、文件、行号）不许手改，但删哪些、留哪些、怎么讲是 agent 的判断。',
                       q='它是研究材料，…不是答案', ref=A(SK, 75, 76)),
                  note('注释', '调用线宁缺毋滥：同名方法有多个时一律不画。',
                       q='线是断言不是猜测', ref=A(EX, 105)),
                  note('推断', '不强制跑：非 Python 仓库它根本用不上；hub 的监视器也只是看工作目录里有没有 `structure*.json` 来点亮「结构层」阶段。')],
                 ['它不做类型解析：`self.scheduler.schedule()` 能连上，是因为 `schedule` 这个名字全仓唯一；重名方法一律没线。',
                  'extract 的列是纯调用深度；agent 交的画布可以按故事线重排（例子里样板就是按 A/B/C 三条线排列的）。']),
             to='dive_agent.extract'),

        part('skill', '规程 SKILL.md', [20, 204, 200, 150], m=[8, 206, 170, 160], kind='store', n='2', code='SKILL.md',
             glyph=g_list(['§1 读者能做到的 6 条', '§2 必须答的 8 问', '§3 推荐的循环', '§6 交付契约'], on=None),
             say='目标 + 8 个判断 + 契约', why='给标准，不给步骤', trap='循环是推荐，不是规定',
             card=card(
                 '这个框真正的「程序」：一份自然语言文件。没有任何 Python 代码在驱动 agent 走流程——代码只管拼提示词、摆好工具、退出后收尾。',
                 ['§1 判断标准：读者只看画布就能做到的六条（说出主角的一生、一句话说清每个部件……）。' + R(SK, 18, 33),
                  '§2 八个判断：动笔之前必须先回答，答案和理由写进 `meta.judgment`。' + R(SK, 35, 67),
                  '§3 推荐循环：探索 → 假设 → 验证 → 画 → 让新读者挑错。' + R(SK, 69, 90),
                  '§6 交付契约：标题就叫「不可商量」。' + R(SK, 133, 146)],
                 [note('文档', '顺序和轮数明说交给 agent。', q='推荐的循环（顺序和轮数由你决定）', ref=A(SK, 71)),
                  note('文档', '规程必须全在这一个文件里：评测考的就是它，提示词里不再复述步骤。',
                       q='方法论必须完全由 skill 文件承载', ref='code-canvas/eval/README.md:9')],
                 ['「agent 按 SKILL 走 5 步」不对：那是推荐循环，可以跳、可以反复。',
                  'SKILL 里的命令写的是相对路径（`python3 validate.py canvas.json`），而 agent 的 cwd 是仓库缓存：工具在哪要 agent 从 SKILL.md 的位置自己推。' + R(SK, 118)]),
             to='dive_agent.judge'),

        part('brain', 'agent 的判断', [330, 204, 200, 150], m=[8, 426, 324, 160], kind='act', n='3', code='meta.judgment',
             glyph=g_kv([('scope', '—'), ('shape', '—'), ('protagonist', '—')]),
             say='先读代码答 8 问，记进 judgment', why='每条写理由，给作者复查', trap='judgment 没人显示',
             card=card(
                 '这个框里唯一「动脑子」的地方：模型自己的上下文。读哪些文件、先后顺序、跑不跑 extract、返工几轮、什么时候停，全是它的判断。',
                 ['八个判断对应 `judgment` 的 **11 个键**：`scope` `shape` `protagonist` `parts` `handoffs` `cut` `tools` `scenarios` `skipped` `revisions` `reader_rounds`。「和谁对比」没有自己的键，落在 `parts[].not` 里。' + R(SK, 150, 164),
                  '除了最后落进 `canvas.json` 的那份 judgment，它的草稿、假设、读过的文件都只在模型上下文里，没有别处存。',
                  '它从工具拿到的反馈只有终端输出和退出码；从新读者拿到的只有一个报告文件——都要它自己去读。'],
                 [note('文档', '先答后画；没有标准答案，但给了反例。',
                       q='"照目录结构切""每张卡都画"这类不经思考', ref=A(SK, 39)),
                  note('文档', '零上下文的新会话也能做出接近手作的质量，靠的是机械检查和自检清单托底。',
                       q='质量地板由 validate + 自检清单托底', ref='code-canvas/DESIGN.md:136')],
                 ['judgment **不显示在画布上**，新读者也看不到；validate 只在它整个缺失或五个键为空时 warn。写多写少都没有后果。' + R(VA, 306, 313),
                  'SKILL 一处说它「也是给读者的导读」（第 38 行），另一处说渲染器不显示（第 166 行），文档自相矛盾。']),
             to='dive_agent.judge'),

        part('canvas', 'canvas.json', [640, 204, 200, 150], m=[8, 866, 324, 150], kind='store', n='4', code='.jobs/<id>/',
             glyph=g_text('还没写', '工作目录，不是缓存'),
             say='工作目录里唯一的交付物', why='只交数据，入库归服务端', trap='不在仓库缓存里',
             card=card(
                 'agent 的交付：一份只有数据的 JSON（卡、线、步、粗位置、judgment）。它在工作目录 `<hub>/.jobs/<id>/` 里，旁边还有 agent 自取名的 `structure.json`、截图、`canvas.html`、`reader_report.md`。',
                 ['路径是提示词给的绝对路径，例子里是 `/home/ubuntu/hub/.jobs/' + JOB + '/canvas.json`。' + R(SV, 659),
                  '契约要求：validate 无 ERROR、check_refs 全过、卡内代码是原文、行号用脚本算、布局只给 `{col, band}`、`meta.judgment` 必须存在。' + R(SK, 133, 146),
                  '它会被别人改：`check_refs.py` 和 `embed_context.py` 都**就地改写**这个文件。'],
                 [note('提交', 'agent 只产这个文件，入库由服务端做：全过才入库。',
                       q='agent 只产工作目录 canvas.json', ref='c:4b43d42'),
                  note('推断', 'cwd 是缓存，交付却在工作目录：缓存下次刷新会被 `git clean -fdx` 清掉，写在缓存里的东西会丢。' + R(SV, 727))],
                 ['「不可商量」不等于「机器会拦」：契约里只有 validate 的 ERROR 会让画布不入库；`meta.judgment` 缺了只是 warn，「卡内代码是原文」只有 embed 的弱核对。见 [[dive_agent.contract|谁在执法]]。']),
             to='dive_agent.contract'),

        part('tools', '自查工具', [640, 8, 200, 140], m=[8, 646, 150, 150], kind='gate', n='5', code='4 个脚本',
             glyph=g_checks([('validate', None), ('check_refs', None), ('embed', None)]),
             say='退出码和输出回到 agent', why='同一批脚本服务端再跑', trap='check_refs 永远退出 0',
             card=card(
                 'agent 在自己的循环里反复跑的几把尺子：`validate.py`、`check_refs.py`、`embed_context.py`、`render.py`。服务端收尾用的也是这同一批。',
                 ['**validate**：只读；有 ERROR 退出码 1，warn 不影响退出码；末行 `N errors, M warnings`。' + R(VA, 657, 662),
                  '**check_refs**：核每条依据 [文件, 行号, 符号]，核不过的就地删掉；退出码**永远是 0**，只能读输出里的「通过 / 剔除」。' + R(CR, 11) + ' ' + R(CR, 76, 77),
                  '**embed_context**：把卡引用的源文件全文写进 `files`，对不上时改卡的行号；一张都嵌不进才退出码 2。' + R(EM, 85, 95),
                  '**render**：渲染到工作目录的 `canvas.html`，agent 截图自己看。'],
                 [note('注释', 'ERROR 只管「渲染器撑不住」的结构错误；预算超限只是 warn。',
                       q='Errors are structural breakages the renderer cannot survive gracefully', ref=A(VA, 4)),
                  note('注释', 'check_refs 的设计是删掉假出处而不是拒收：agent 跑是自查，服务端再跑是兜底。',
                       q='读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。', ref=A(CR, 8)),
                  note('提交', 'embed 从可选升成必选：可选项 agent 不会自己做。', q='生成 agent 从不执行', ref='c:40781a8')],
                 ['tail 写的是「check_refs 全过」，但没有退出码能看：agent 要自己读出「剔除 0」。',
                  'warn 不是命令，是「你考虑过了吗」的提醒；用不上的要在 `judgment.skipped` 里写一句为什么。' + R(SK, 112, 113)]),
             to='dive_agent.tools'),

        part('reader', '新读者', [640, 410, 200, 110], m=[190, 646, 142, 150], kind='bg', n='6', code='reader_check.py',
             glyph=g_text('没跑', '建议至少一轮'),
             say='另起 sonnet，只看画布', why='切断上下文才测得出缺口', trap='报告不会自动回来',
             card=card(
                 '`reader_check.py` 再起一个 claude（默认 sonnet）当「第一次接触这个代码库的工程师」，只读这张画布，写一份挑错报告。',
                 ['把 `canvas.json` 读进内存，去掉 `files`（源码全文）和 `meta.judgment`，写进一个临时目录。' + R(RC, 58, 63),
                  '`claude -p <提示词> --model sonnet --allowedTools Read --output-format text`，cwd 是临时目录，超时 900 秒。' + R(RC, 69, 70),
                  '要它写四节：主角的一生、每个部件一句话（含「不是什么」）、3–8 个读不懂的问题、可疑或矛盾处；末行自评理解度 X/10。' + R(RC, 18, 34),
                  'stdout 写进报告文件（默认 `reader_report.md`），删掉临时目录。' + R(RC, 71, 73)],
                 [note('注释', '去掉 judgment：作者的理由会替画布补全信息，测试就失真了。',
                       q='读者不看作者的自述，只看画布本身', ref=A(RC, 61)),
                  note('文档', '读者读不懂，问题在画布。', q='读者说错的地方往往不是他笨，而是画布没讲清', ref=A(SK, 90)),
                  note('推断', '为什么要另一个 claude：作者读自己写的东西会脑补缺掉的上下文，只有切断上下文的读者测得出缺口。')],
                 ['隔离靠 cwd + 提示词，不靠沙盒：`--allowedTools Read` 没限制路径，「不看仓库」靠读者遵守提示词（推断）。',
                  '为什么默认 sonnet 而生成默认 opus，底稿没找到理由。']),
             to='dive_agent.reader'),

        part('exit', '退出', [950, 204, 200, 150], m=[8, 1076, 200, 124], kind='out', n='7', code='DONE',
             glyph=g_kv([('DONE', '—'), ('rc', '—'), ('canvas.json', '—')]),
             say='自己决定停，打印 DONE', why='退出码 + 文件在不在', trap='没有代码读 DONE',
             card=card(
                 'agent 自己判断「做完了」，打印 DONE、进程退出。从这里回到顶层：后台脚本看退出码和 `canvas.json` 在不在，进入服务端收尾。',
                 ['「做完了」有三层判据：机械层（validate 0 ERROR、check_refs 无剔除）、判断层（SKILL §1 六条）、看图层（截图自己看）。只有第一层是机器的。' + R(SK, 18, 33) + ' ' + R(SK, 131),
                  '真正被读的信号是进程退出：退出码 0 → 看 `canvas.json` 在不在，不在就 rc=5；在就 embed → check_refs → validate，validate 不过 rc=4。' + R(SV, 633, 636)],
                 [note('注释', '服务端不信任 agent 守规程：退出后能机械做的再做一遍。',
                       q='服务端入库（不信任 agent 守规程）', ref=A(SV, 609)),
                  note('推断', 'DONE 没有代码读，也许是留给人看日志的；底稿没找到说明。')],
                 ['生成 agent 没有 `--max-turns`，也没有超时：什么时候停完全由它自己定。' + R(SV, 648, 651),
                  '提示词里的「完成后打印 DONE」在 serve.py 里只出现在提示词文本中。' + R(SV, 667)]),
             to='dive_agent.contract'),
    ]

    wires = [
        wire('w_read', 'prompt', 'skill', '先读 SKILL.md', ml='先读 SKILL',
             card=card('提示词的第一句：去读 SKILL.md。',
                       'SKILL.md 和工具脚本在 serve.py 所在目录；提示词给的是 `{skill}/SKILL.md` 这个绝对路径。' + R(SV, 668),
                       [note('注释', '提前说清这份文件的性质：给标准，不给管线。', q='它不是一条要逐步执行的管线', ref=A(SV, 668))])),
        wire('w_rules', 'skill', 'brain', '标准 + 8 问', ml='标准 + 8 问', why='先读代码再动笔',
             card=card('SKILL 交给 agent 的是要达到什么、必须回答什么，而不是先做什么后做什么。',
                       '§2 要求动笔写 JSON 之前先读代码、回答八个问题，每条写理由。' + R(SK, 37, 38),
                       [note('文档', '先读代码、先回答，再写 JSON。', q='动笔写 JSON 之前，先读代码', ref=A(SK, 37))])),
        wire('w_struct', 'extract', 'brain', 'structure.json', ml='材料', cls='dash', why='只当研究材料',
             card=card('extract 的产出交给 agent 当材料：卡的原文、行号、调用线都是机械算出来的。',
                       '写在工作目录里；agent 自己读、自己挑。',
                       [note('文档', '事实字段不许手改，但删哪些、留哪些、怎么讲全是 agent 的判断。',
                             q='删哪些、…留哪些、怎么讲，全是你的判断', ref=A(SK, 76, 77))],
                       '虚线：可选。不跑 extract 也能交出合格的画布。')),
        wire('w_write', 'brain', 'canvas', '写 JSON', ml='写入', mlp=[176, 831], why='先答后写',
             card=card('agent 自己写 `canvas.json`：卡、线、步、粗位置和 judgment。',
                       '卡里的代码要从仓库原文取，行号锚点用脚本在最终卡文上算。' + R(SK, 136, 138),
                       [note('文档', '行号锚点不许手数：数错了渲染时锚点就落到别的行上。',
                             q='一律用脚本在最终卡文上计算，不要手数', ref=A(SK, 138))])),
        wire('w_check', 'tools', 'canvas', '查，可能就地改写', ml='查 / 改写', mlp=[72, 831], trap='改完要重读',
             card=card('工具读 `canvas.json`；其中两个还会把它改掉再写回去。',
                       ['check_refs 删掉核不过的依据，依据被剔光的「已确认」步卡降级成推断。' + R(CR, 72, 76),
                        'embed 写入 `files`，对不上时改卡的 `file` 行号。' + R(EM, 79) + ' ' + R(EM, 88, 89)],
                       [note('注释', '核不过就删、不拒收：读者永远看不到假出处。',
                             q='读者永远看不到假出处', ref=A(CR, 8))],
                       'agent 上下文里那份 JSON 已经过时了：跑完 check_refs / embed，要重新读文件再接着改。')),
        wire('w_out', 'tools', 'brain', '输出 + 退出码', ml='输出 + 退出码', trap='check_refs 看输出不看码',
             card=card('工具回给 agent 的唯一通道：终端输出和退出码。',
                       ['validate：`ERROR …` / `warn …` 每行一条，末行计数，有 ERROR 退出 1。' + R(VA, 657, 662),
                        'check_refs：「依据核对：N 条，通过 K，剔除 M」，退出码永远 0。' + R(CR, 77, 82),
                        'embed：「嵌入 N 文件 / M 卡」，跳过的卡逐条列出（走 stderr）。' + R(EM, 90, 95)],
                       [note('注释', 'check_refs 把「剔除」当正常完成，所以退出码不报错。',
                             q='exit 0（剔除也算正常完成）', ref=A(CR, 11))],
                       '三个工具报错的方式各不一样：只看退出码会以为 check_refs 永远全过。')),
        wire('w_copy', 'canvas', 'reader', '去掉答案的副本', ml='去答案副本', mlp=[272, 831], why='只看画布本身',
             card=card('reader_check 读 `canvas.json`，在内存里去掉源码全文和 judgment，写到临时目录给新读者。',
                       '只改内存里的副本，原文件不动。' + R(RC, 58, 63),
                       [note('注释', '读者拿到的和真实读者在画布上看到的一致。',
                             q='和真实读者在画布上看到的内容一致', ref=A(RC, 6))])),
        wire('w_report', 'reader', 'brain', '报告，自己读', ml='报告文件', cls='dash', trap='没有自动返工',
             card=card('新读者的报告只是工作目录里的一个文件；agent 要自己读、自己决定改不改。',
                       'SKILL 让它对照 §1 的六条逐条看读者哪里做不到，回去补，再记进 `judgment.reader_rounds`。' + R(SK, 87, 89),
                       [note('文档', '可以补完再测一轮；轮数没有上限，也没有东西强制至少一轮。',
                             q='补完可以再测一轮', ref=A(SK, 89))],
                       ['报告不会触发任何东西：没有代码读它，`reader_rounds` 也不被校验。',
                        '读者超时（900 秒）时没有 try：agent 看到的是一段 traceback 而不是报告，临时目录残留（推断）。' + R(RC, 70, 73)])),
        wire('w_exit', 'canvas', 'exit', '退出后被取走', ml='退出后取走',
             card=card('agent 退出后，后台脚本只认两样：退出码，和工作目录里的这个文件。',
                       '退出码 0 且文件在 → 进入顶层的「服务端收尾」。' + R(SV, 633),
                       [note('提交', '入库只由服务端做，done 就等于已入库。',
                             q='agent 只产工作目录 canvas.json', ref='c:4b43d42')])),
    ]

    J0 = g_kv([('scope', '—'), ('shape', '—'), ('protagonist', '—')])
    steps = [
        beat('读提示词',
             '`cd` 进 `/home/ubuntu/hub/.repos/nano-vllm-155c1586` 后 claude 启动。提示词三段：读 SKILL.md；'
             '「任务：为仓库 … 生成一张 Code Canvas，主题/关注点：讲讲调度器怎么工作」；tail 给交付路径 `…/.jobs/' + JOB + '/canvas.json`、契约、「不要写入画布库」、「完成后打印 DONE」。',
             note('注释', '只有交付位置、校验标准、不许入库是死的；其余明说交给 agent。', q='其余怎么做由你判断。', ref=A(SV, 663)),
             focus=['prompt', 'w_read', 'skill'],
             state={'prompt': g_list(['head：读 SKILL.md', '任务：讲讲调度器…', 'tail：交付 + 契约'], on=[0, 1, 2])}),
        beat('读 SKILL，先答判断 1',
             '（以下是一种设想的走法，每次都不同。）nano-vllm 是「~1200 行的 vLLM 教学重写」，不到约 2000 行 → **深潜**；'
             '问题是调度器 → 收窄到 `step` / `Scheduler` / `BlockManager`。写进 judgment 的 `scope`。',
             note('文档', '动笔之前先读代码、先回答判断。', q='动笔写 JSON 之前，先读代码', ref=A(SK, 37)),
             focus=['brain', 'w_rules', 'skill'],
             state={'skill': g_list(['§1 读者能做到的 6 条', '§2 必须答的 8 问', '§3 推荐的循环', '§6 交付契约'], on=1),
                    'brain': g_kv([('scope', '深潜，收窄到调度'), ('shape', '—'), ('protagonist', '—')])}),
        beat('跑 extract（可选）',
             '`python3 extract.py <源码目录> --repo-root <缓存> --out <工作目录>/structure.json`，stderr 回一行 `wrote …  (N cards, M wires)`。'
             '拿到一堆折叠卡 + 按名字匹配的调用线：`self.scheduler.schedule()` 能连上，重名方法没线。',
             note('文档', '这只是材料，不是答案。', q='它是研究材料，…不是答案', ref=A(SK, 75, 76)),
             focus=['extract', 'w_struct', 'brain'],
             state={'extract': g_text('已产出', '折叠卡 + 调用线', tone='ok')}),
        beat('读代码，答完 8 问',
             '形态：**循环**（引擎一拍一拍）叠加**一生**（seq 在 waiting / running 间进出）；主角：**`Sequence`**；'
             '切部件：Scheduler 拥有两个队列，BlockManager 拥有块账本；剪掉：ModelRunner、Sampler 折叠；'
             '演示：预算够 / 被挡 / 切片 / 抢占——从 `schedule` 的分支里找。',
             note('文档', '按「读者会卡在哪」选表达，不按字段有没有。', q='不要因为某个字段存在就填它', ref=A(SK, 64)),
             focus=['brain', 'skill'],
             state={'brain': g_kv([('scope', '深潜，收窄到调度'), ('shape', '循环 + 一生'), ('protagonist', 'Sequence')])}),
        beat('写 canvas.json',
             '10 张卡：`step / run / sampler` 放 col 0，`schedule / preempt / postprocess` 放 col 1，`can_allocate / allocate / hash_blocks` 放 col 2，一张块池快照卡放 col 3。'
             '只给 `{col, band}`，不给像素；judgment 一起写进 `meta`。',
             note('推断', '交付写在工作目录的绝对路径，不写在 cwd（缓存）：缓存下次刷新会被 `git clean -fdx` 清掉。'),
             focus=['canvas', 'w_write', 'brain'],
             state={'canvas': g_kv([('cards', '10（col 0–3）'), ('refs', '6 条'), ('files', '—')])}),
        beat('第一次 validate：1 个 ERROR',
             '输出：`ERROR  线 w-hash: to.card hash_blocks 不存在`、`warn   第 1 列有 4 张卡（>3）: …`，末行 `1 errors, 1 warnings`，退出码 1。'
             'agent 改对卡 id、把一张卡挪到别的列，再跑一遍，直到 **0 errors**。',
             note('注释', 'ERROR 是渲染器撑不住的结构错误，必须清零；warn 是预算超限，只是提醒。',
                  q='Errors are structural breakages the renderer cannot survive gracefully', ref=A(VA, 4)),
             focus=['tools', 'w_out', 'brain'],
             state={'tools': g_checks([('validate 1 ERROR', 'no'), ('check_refs', None), ('embed', None)])}),
        beat('check_refs：剔除 1 条，文件被改了',
             '输出「依据核对：6 条，通过 5，剔除 1」，下面列出被剔的那条；退出码照样是 **0**。canvas.json 已被就地改写：'
             '那条假依据没了。agent 要**重新读文件**再继续，不然会把旧版本写回去。',
             note('注释', '删掉假出处、不拒收；agent 自己跑是自查，服务端还会再跑。',
                  q='读者永远看不到假出处。agent 自查时看报告修正；服务端入库前再跑一遍兜底。', ref=A(CR, 8)),
             focus=['tools', 'w_check', 'canvas'],
             state={'tools': g_checks([('validate 0 ERROR', 'ok'), ('check_refs 剔除 1', 'no'), ('embed', None)]),
                    'canvas': g_kv([('cards', '10（col 0–3）'), ('refs', '6 → 5 条'), ('files', '—')])}),
        beat('embed + render，截图自己看',
             'embed 把卡引用的源文件全文写进 `files`，输出「嵌入 N 文件 / M 卡」；render 渲染到工作目录的 `canvas.html`。'
             'agent 截图，看每步的镜头有没有框住 caption 说到的东西。',
             note('提交', 'embed 原本是可选项，agent 从来不做，于是升成必选。', q='生成 agent 从不执行', ref='c:40781a8'),
             focus=['tools', 'w_check', 'canvas'],
             state={'tools': g_checks([('validate 0 ERROR', 'ok'), ('check_refs 修好', 'ok'), ('embed 已嵌', 'ok')]),
                    'canvas': g_kv([('cards', '10（col 0–3）'), ('refs', '5 条，全过'), ('files', '已嵌')])}),
        beat('新读者挑错一轮',
             'reader_check 去掉 `files` 和 judgment，起一个 sonnet 只读这份副本。报告里写：「第③步 `can_allocate` 返回 -1 没解释」。'
             'agent **自己读**这份报告，在那张卡补一条 term 注释，把这一轮记进 `judgment.reader_rounds`；要不要再测一轮，也是它定。',
             note('文档', '读者读不懂，问题在画布，不在读者。', q='读者说错的地方往往不是他笨，而是画布没讲清', ref=A(SK, 90)),
             focus=['reader', 'w_copy', 'w_report', 'brain'],
             state={'reader': g_text('第 1 轮', '③ 的 -1 没解释', tone='run'),
                    'brain': g_kv([('protagonist', 'Sequence'), ('revisions', '1 条'), ('reader_rounds', '1 轮')])}),
        beat('打印 DONE，退出',
             'agent 自己觉得够了：打印 `DONE`，进程以 **0** 退出。没有代码读 DONE；后台脚本看的是退出码 0 和 `canvas.json` 在不在，'
             '然后回到顶层，进入「服务端收尾」再查一遍。',
             note('注释', '服务端不信任 agent：退出后能机械做的再做一遍。', q='服务端入库（不信任 agent 守规程）', ref=A(SV, 609)),
             focus=['exit', 'w_exit', 'canvas'],
             state={'exit': g_kv([('DONE', '打印了，没人读'), ('rc', '0'), ('canvas.json', '在 ✓')])}),
    ]

    return node(
        'dive_agent', '生成 agent 里面', '生成 agent 里面：JSON 是怎么被想出来的',
        '顶层「生成 agent」那个框里面：没有驱动流程的代码，只有一份规程、八个判断、一圈自查工具和一个挑错的新读者。',
        lead='你现在在顶层图的「生成 agent」框里面（那一单的 `claude -p`）。这里没有一段 Python 在驱动 agent 走流程：'
             '提示词和 SKILL.md 规定了交什么、怎么验，中间读什么、怎么切、改几轮全由模型自己判断。图跟着同一单 nano-vllm +「讲讲调度器怎么工作」走一遍（走法是设想的，每次都不同；工具输出的格式来自代码）。',
        src=A(SK, 11, 14),
        blocks=[scene(1220, 520, parts, wires, steps, mw=340, mh=1208,
                      cap='生成 agent 里面：提示词 → 规程 → 判断 → canvas.json ⇄ 自查工具 / 新读者 → 退出')],
        children=[_src_prompt(), _src_judge(), _src_extract(), _src_tools(), _src_reader(), _src_contract()])


# ---------------------------------------------------------------- 源码页
def _src_prompt():
    return node(
        'dive_agent.prompt', '提示词源码', '提示词：三样死的，其余交给 agent',
        '「提示词」框的原件：`spawn_generate` 里拼 tail 和 head 的 12 行。',
        lead='「生成 agent」里面「提示词」那个框的源码：agent 拿到的全部指令就是这 12 行拼出来的文本，加上一行任务。',
        src=A(SV, 658, 669),
        blocks=[code(SV, [(658, 669)], notes=[
            at(658, None, '粘贴代码的任务没有仓库：下面 check_refs / embed 两项只在有仓库时拼进去。', has='has_repo'),
            at(659, None, '交付文件是工作目录里的绝对路径；例子里是 `…/.jobs/' + JOB + '/canvas.json`。agent 的 cwd 却是仓库缓存。', has='交付：{w}'),
            at((660, 662), None, '列出五样「不可商量」：validate 无 ERROR、check_refs 全过、embed 嵌入、卡内代码是原文、judgment 写实话。前三样服务端会再跑；后两样没有机械手段强制。', has='不可商量'),
            at(663, '注释', '其余明说交给 agent；「做完了」定义成 SKILL §1 的六条——一个人判断的标准，不是机器判据。', q='其余怎么做由你判断。'),
            at((664, 665), None, '渲染、截图、reader_check 都是建议口吻，没有任何东西检查 agent 做没做。', has='reader_check.py'),
            at(666, '提交', '不许 agent 自己入库：服务端 embed + check_refs + validate 全过才入库。', q='agent 只产工作目录 canvas.json', ref='c:4b43d42'),
            at(667, '推断', '`DONE` 没有任何代码读；真正被读的是进程的退出码。', has='DONE'),
            at((668, 669), '提交', 'head：改版前写的是「严格按其管线执行」，提交 f4bbe29 换成了判断标准。提交只有标题，没写为什么。', q='目标+判断标准+决策清单取代编号管线', ref='c:f4bbe29'),
        ], cap='serve.py：提示词的 tail 和 head')])


def _src_judge():
    return node(
        'dive_agent.judge', '8 个判断原文', 'SKILL §2：八个判断，和它落进 judgment 的样子',
        'SKILL.md 第 2 节的八问原文，批注里是 nano-vllm +「调度器」的一种可能答案；以及 judgment 的 11 个键。',
        lead='「生成 agent」里面「规程」和「agent 的判断」两个框的原文。批注里的答案是设想的一种（每次都不同），材料取自仓库自带的 nano-vllm 样板。',
        src=A(SK, 35, 166),
        blocks=[
            code(SK, [(35, 67)], notes=[
                at((37, 38), '文档', '先读代码、先回答，再写 JSON；每条都要写理由。', q='动笔写 JSON 之前，先读代码'),
                at((39, 40), '文档', '没给正确答案，只给了反例。', q='"照目录结构切""每张卡都画"这类不经思考'),
                at((42, 46), None, '判断 1 → `scope`。例子：样板副标题「~1200 行的 vLLM 教学重写」，不到 2000 行 → 深潜，收窄到 step / Scheduler / BlockManager。行数是 agent 自己估的，没有代码在量。', has='2000 行'),
                at((47, 53), None, '判断 2 → `shape`。例子：循环（引擎一拍一拍）叠加一生（seq 在 waiting / running 间进出），两条线在 `schedule()` 交汇。', has='故事是什么形态'),
                at((54, 55), None, '判断 3 → `protagonist`。例子：`Sequence`——被调度器改状态最多、被到处传递。', has='主角是谁'),
                at((56, 58), None, '判断 4 → `parts` + `handoffs` 两个键。例子：Scheduler 拥有 waiting / running 队列，BlockManager 拥有块账本，交接在 `schedule()` 里。', has='职责和执行归属'),
                at((59, 61), '文档', '判断 5 → `cut`。例子：ModelRunner、Sampler 折叠成上下文卡。为什么要剪：', q='全量的卡和线等于毛线球'),
                at((62, 64), '文档', '判断 6 → `tools`。例子：块池变化 → 快照卡；抢占 → 步卡。选表达看读者卡在哪：', q='不要因为某个字段存在就填它'),
                at(65, None, '判断 7「和谁对比」**没有自己的键**，落在 `parts[].not`。例子：「Scheduler 不是 BlockManager：它决定谁跑，不管块怎么记账」。', has='和谁对比'),
                at((66, 67), '文档', '判断 8 → `scenarios`。例子：预算够 / 被挡 / 切片 / 抢占；样板沙盘里的预算数字就是从配置里来的。', q='不要凭空编'),
            ], cap='SKILL.md §2：你必须自己做出的判断'),
            code(SK, [(148, 166)], notes=[
                at((152, 162), None, '八个判断对应 **11 个键**：判断 4 拆成 `parts` 和 `handoffs`，判断 7 没有键，另外多了 `skipped`、`revisions`、`reader_rounds` 三个过程记录。', has='"scope"'),
                at(161, None, '例子：「我原以为一批里 prefill 与 decode 可混排，代码表明 is_prefill 是整批标志」（设想）。', has='revisions'),
                at(162, None, '例子：新读者没看懂 `can_allocate` 返回 -1 → 补一条 term 注释（设想）。这个键不被校验。', has='reader_rounds'),
                at(166, '文档', 'judgment 不显示在画布上；validate 也只在它整个缺失或 scope/shape/protagonist/parts/cut 为空时 warn。', q='渲染器目前不显示 judgment；它是给作者复查的'),
            ], cap='SKILL.md §7：meta.judgment 的格式'),
        ])


def _src_extract():
    return node(
        'dive_agent.extract', 'extract 源码', 'extract：调用线只画唯一的名字，列 = 调用深度',
        '「结构提取」框的两段核心：怎么画调用线、怎么定 col / band。',
        lead='「生成 agent」里面「结构提取」那个框的源码：它机械产出的线和粗布局是怎么算的。',
        src=A(EX, 104, 214),
        blocks=[
            code(EX, [(104, 131)], notes=[
                at(105, '注释', '名字有歧义时宁可不画：', q='线是断言不是猜测'),
                at((114, 116), None, '只取调用名的最后一段：`self.scheduler.schedule()` 取到的是 `schedule`。不做类型解析。', has='callee.attr'),
                at(120, None, '这个名字在全仓对应的卡不是恰好一张 → 不画；自己调自己也不画。', has='len(targets) != 1'),
                at((123, 125), None, '同一对卡只画一根线。', has='seen_pair'),
                at(128, None, '线的起点钉在调用发生的那一行（卡内行号）。', has='node.lineno - f["start"] + 1'),
            ], cap='call_wires：调用线'),
            code(EX, [(181, 214)], notes=[
                at(190, None, '入度 0 的卡当根放 col 0；全是环、没有根时就全部当根。', has='not callers[i]'),
                at(194, None, '最多走 4 层：col 0–4，「≤5 列」在这里是写死的。', has='depth < 4'),
                at((203, 204), None, '没被连上的卡全丢到 col 4。', has='col.setdefault(i, 4)'),
                at((207, 213), None, '同一列按 (文件, 起始行) 排序，依次是 band 0、1、2……', has='enumerate(members)'),
            ], cap='layout：粗布局 {col, band}'),
        ])


def _src_tools():
    return node(
        'dive_agent.tools', '自查工具源码', '三把尺子回话的方式各不一样',
        '「自查工具」框：validate 的退出码、check_refs 的就地改写和永远为 0 的退出码、embed 的输出。',
        lead='「生成 agent」里面「自查工具」那个框的源码：agent 跑完这几个脚本，看到的是什么、文件被改成了什么。',
        src=A(CR, 1, 82),
        blocks=[
            code(VA, [(2, 8), (657, 662)], notes=[
                at((4, 5), '注释', 'ERROR 只管渲染器撑不住的结构错误。', q='Errors are structural breakages the renderer cannot survive gracefully'),
                at(6, '注释', 'warn 是预算超限：只是提醒，不影响退出码。', q='Warnings are curation-budget violations'),
                at((657, 661), None, '例子里第一次跑：`ERROR  线 w-hash: to.card hash_blocks 不存在`，末行 `1 errors, 1 warnings`（设想）。', has='errors,'),
                at(662, None, '有 ERROR 退出码 1；warn 只有加 `--strict` 才算失败。', has='sys.exit(1 if E'),
            ], cap='validate.py：开头的自述和最后的输出'),
            code(CR, [(18, 36), (59, 82)], notes=[
                at((22, 26), None, '先拦路径逃逸：解析后必须还在仓库里。', has='relative_to(repo)'),
                at((32, 35), None, '符号要在行号前后 ±3 行的窗口里出现：容忍 agent 数偏几行。', has='ref[1] - 4'),
                at(72, None, '只留核得过的依据，直接改掉原对象。', has='h["refs"] = good'),
                at((73, 75), None, '一条步卡的依据被剔光、又写着「已确认」→ 降级成推断。', has='h["ev"] = "infer"'),
                at(76, None, '不管有没有剔除都把文件写回去：agent 手里的旧版本从此过时。', has='cj.write_text'),
                at(77, None, '例子：「依据核对：6 条，通过 5，剔除 1」（设想）。tail 要求的「全过」只能从这一行读出来。', has='依据核对'),
                at(82, '注释', '剔除也返回 0。', q='exit 0（剔除也算正常完成）', ref=A(CR, 11)),
            ], cap='check_refs.py：核对和就地改写'),
            code(EM, [(85, 95)], notes=[
                at((85, 87), None, '一张卡都嵌不进才退出 2；部分跳过不算失败。', has='sys.exit(2)'),
                at((88, 89), None, '就地改写：把源文件全文写进 `files`。', has='d["files"] = files'),
                at((90, 95), '注释', '结果走 stderr；对不上的卡只是列出来，画布照常可用。', q='对不上的卡如实跳过并列出', ref=A(EM, 9)),
            ], cap='embed_context.py：结尾的输出'),
        ])


def _src_reader():
    return node(
        'dive_agent.reader', 'reader_check 源码', '新读者：去掉答案，另起一个只能读的 claude',
        '「新读者」框：18 行主流程，从去掉 files / judgment 到写报告。',
        lead='「生成 agent」里面「新读者」那个框的源码：值得从头读，没几行。',
        src=A(RC, 50, 75),
        blocks=[
            code(RC, [(12, 16)], notes=[
                at(12, None, '给读者的人设：有经验的工程师，第一次接触这个代码库。', has='第一次接触这个代码库'),
                at((15, 16), '注释', '测的就是「只看这张画布能不能读懂」。', q='你要检验的正是"只看这张画布能不能读懂"'),
            ], cap='给读者的提示词（开头）'),
            code(RC, [(50, 75)], notes=[
                at(54, None, '默认 sonnet（可用环境变量 `READER_MODEL` 改）；生成 agent 默认是 opus，为什么不同底稿没找到。', has='"sonnet"'),
                at((59, 60), None, '去掉 `files`（源码全文），只在内存里改，原文件不动。', has='data.pop("files"'),
                at(61, '注释', '去掉 judgment：', q='读者不看作者的自述，只看画布本身'),
                at((62, 63), None, '写到一个新的临时目录：读者的 cwd 里只有这一份 canvas.json。', has='mkdtemp'),
                at(69, None, '只自动放行 Read，不限路径；提示词紧跟 `-p`——同仓库另一处注释说过，放在 `--allowedTools` 后面会被吞成工具名。', has='"--allowedTools", "Read"'),
                at(70, None, '超时 900 秒，没有 try：超时时抛异常，下面删临时目录那行也走不到（推断）。', has='timeout=900'),
                at(71, None, '读者没出声也要写一份报告，让 agent 知道。', has='(读者没有输出)'),
                at(72, None, '报告就是一个文件：没有任何代码会把它送回 agent，agent 要自己读。', has='a.out.write_text'),
                at(75, None, 'claude 成功退出 0，否则退出 1。', has='sys.exit(0 if r.returncode == 0 else 1)'),
            ], cap='main()：起第二个 claude'),
        ])


def _src_contract():
    return node(
        'dive_agent.contract', '契约和执法', '「不可商量」的契约，谁在执法',
        'SKILL §6 契约原文，逐条对照：哪条有机器闸门、哪条只是 warn、哪条没人查。',
        lead='「生成 agent」里面「canvas.json」和「退出」两个框落到的原文：交付契约逐条，以及服务端真正拦人的那一行。',
        src=A(SK, 133, 146),
        blocks=[
            code(SK, [(133, 146)], notes=[
                at(135, None, 'validate 无 ERROR：**唯一的硬闸门**，服务端不过就 rc=4、不入库。check_refs 全过：服务端再跑一遍只为剔除，退出码被忽略，不拒收。', has='validate.py'),
                at((136, 137), '注释', '卡内代码是原文：只有 embed 弱核对，对不上的卡跳过并列出，画布照常入库。', q='对不上的卡如实跳过并列出', ref=A(EM, 9)),
                at(138, None, '行号用脚本算：validate 只查越界（ERROR），算得对不对不查。', has='不要手数'),
                at(140, None, '推断标 ev / unknown 写 need：只查格式（枚举不对、need 没写是 ERROR），该不该标查不了。', has='ev:"infer"'),
                at((142, 143), None, '预算数字：每步线 >5、高亮行 >6、每卡 intent note >3、单行 >100 字符都只是 warn；「focus 卡 ≤4」没找到检查。', has='线 ≤ 5'),
                at((144, 145), None, '布局：validate 只查每张卡有没有 `col` / `band`（ERROR）和一列超过 3 张（warn）；「≤5 列」没有检查。', has='layout: {col, band}'),
                at(146, None, '`meta.judgment` 必须存在：validate 里**只是 warn**。为什么写成 warn，底稿没找到说明。', has='meta.judgment'),
            ], cap='SKILL.md §6：交付契约'),
            code(SV, [(608, 610), (633, 636)], notes=[
                at(609, '注释', '服务端不信 agent 守规程，退出后再做一遍。', q='服务端入库（不信任 agent 守规程）'),
                at(633, None, 'agent 退出码 0 才往下走；`canvas.json` 不在 → rc=5。embed、check_refs 后面接的是 `;`，失败不拦。', has='if [ "$rc" -eq 0 ]'),
                at((634, 635), None, '只有 validate 是 `if`：过了才 cp 进库、render；不过 rc=4。', has='else rc=4'),
            ], cap='serve.py post_pass：真正拦人的只有 validate'),
        ])
