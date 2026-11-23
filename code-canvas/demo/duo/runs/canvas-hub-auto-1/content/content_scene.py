# 顶层：给一个 repo 链接 + 一句关注点，hub 怎么一步步把画布生成出来。
# 事实全部来自研究员底稿 research/manuscript.md（仓库 HEAD 79153e7）。
from cv import *

META = dict(title='一张画布是怎么生成的')
SV = 'code-canvas/serve.py'


def A(f, a, b=None):
    return '%s:%s' % (f, a if b is None else '%d-%d' % (a, b))


def S(a, b=None):
    """serve.py 的 ref=（不带反引号）。"""
    return A(SV, a, b)


def R(f, a, b=None):
    """文字里的锚点（迷你语法，带反引号）。"""
    return '`%s`' % A(f, a, b)


def RS(a, b=None):
    return R(SV, a, b)


URL = 'github.com/…/nano-vllm'
JOB = 'j20261123-101530-482'


def root(base):
    base['lead'] = ('你在 hub 首页填一个 git 地址和一句「想看什么」，20–40 分钟后得到一张画布。'
                    '这张图跟着一个例子（nano-vllm 仓库 +「讲讲调度器怎么工作」）走完全程：'
                    '谁接单、谁研究代码、谁把关入库、像素最后在哪里排出来。每个框、每条线都能点开。')

    parts = [
        # ---------------------------------------------------------------- 第一排：服务进程里的同步部分
        part('form', '首页表单', [20, 8, 200, 124], m=[8, 8, 200, 124], kind='ext', n='1',
             glyph=g_kv([('git_url', '…/nano-vllm'), ('ask', '讲讲调度器怎么工作'), ('name', '留空')]),
             say='填地址和关注点，点生成', why='ask 必填：画布为谁讲什么',
             trap='提交后不等生成，只拿回单号',
             card=card(
                 '用户唯一的入口：填 git 地址和「主题 / 关注点」，点「生成画布」。之后这一页每 5 秒拉一次任务列表，看自己那一单走到哪了。',
                 ['前端拼一个 JSON body：`{ask, name?, git_url}`；只有选了别的类型 / 引擎才多带 `preview / zoom / engine / model`。' + RS(874, 884),
                  'ask 为空，前端当场拦下，不发请求。' + RS(885),
                  '表单默认勾的是 git 地址、深潜画布、Claude 引擎，本图走的就是这条默认路。' + RS(830) + ' ' + RS(842)],
                 [note('注释', 'ask 不能空：画布好不好，是按「这位读者能做到什么」判的；没有 ask，就没有判断标准。',
                       q='主题/关注点是必填的——这张图为谁、讲什么', ref=S(885)),
                  note('文档', '首页是生成表单而不是画布列表，是作者的明确裁决：画布库只是沉淀。',
                       q='首页必须是生成入口', ref='HANDOFF.md:180')],
                 ['点完按钮拿回来的只是任务单（job meta），不是画布；1.2 秒后页面自己刷新，之后靠轮询看进度。' + RS(891, 892),
                  '名字留空时，`JSON.stringify` 会把 `undefined` 的 name 丢掉，服务端再用 URL 尾段起名。'])),

        part('gate', '/generate 把关', [330, 8, 200, 124], m=[8, 196, 200, 124], kind='gate', n='2', code='do_POST',
             glyph=g_checks([('ask 非空', None), ('http(s) 地址', None), ('name=nano-vllm', None)]),
             say='几条硬规矩，全过才起任务', why='名字会拼进路径和 URL',
             trap='不查仓库能否 clone',
             card=card(
                 'hub 服务（常驻的 `serve.py --hub`，每个请求一个线程）收到 `POST /generate`，先做几条机械检查，再把活交给 `spawn_generate`。',
                 ['ask 后端再查一遍；git_url 只收 `http(s)://`。' + RS(1118, 1124),
                  '画布名默认取 URL 最后一段、去掉 `.git`；只允许 `[A-Za-z0-9._-]`。' + RS(1126) + ' ' + RS(1152, 1153),
                  '引擎只收 claude / codex，模型 id 也只许几种字符。' + RS(1154, 1159),
                  '最后在 `SPAWN_LOCK` 里调 `spawn_generate`，把返回的任务单原样回给浏览器。' + RS(1160, 1162)],
                 [note('推断', '名字直接拼进文件路径 `<hub>/<name>.html` 和 URL `/c/<name>/`，所以只放行安全字符（测试 code-canvas/tests/hub.mjs 第 473 行专门测了路径穿越的名字返回 404）。'),
                  note('注释', '调用包在锁里：「判定缓存怎么取用」和「写 meta」必须一气呵成，否则两单同仓任务会都以为自己是第一个。',
                       q='缓存取用模式的判定+meta 落盘要原子，防同仓并发单互踩', ref=S(1160))],
                 ['这里不检查仓库存不存在、能不能 clone——那是后台脚本的事，失败了只会体现在任务状态里。',
                  'hub 没有任何鉴权；访问控制全靠只绑内网（tailscale）地址。' + RS(41, 42)]),
             to='src_generate'),

        part('spawn', '起任务', [640, 8, 200, 124], m=[8, 384, 200, 124], kind='act', n='3', code='spawn_generate',
             glyph=g_kv([('job', '…101530-482'), ('prompt', 'head+任务+tail'), ('script', 'prep;gen;post')]),
             say='拼好提示词和脚本，起进程就走', why='断开：服务重启不连累任务',
             trap='服务不等它，也不设超时',
             card=card(
                 '给这一单起编号、定好所有文件路径，拼出两样东西——一段给 agent 的**提示词**、一段 **bash 脚本**——然后起一个后台进程跑脚本，自己立刻返回。',
                 ['编号：`j年月日-时分秒-毫秒后三位`，账本文件和工作目录随之确定。' + RS(592, 599),
                  '提示词 = head（「读 SKILL.md，它不是管线」）+ 任务行（仓库路径 + 关注点）+ tail（交付路径、不可商量的契约、「不要写入画布库」）。' + RS(659, 669) + ' ' + RS(736),
                  '脚本三段：prep 准备仓库 → gen 跑 claude 并计时 → post 服务端收尾，最后把退出码写进 `.status`。' + RS(652, 657) + ' ' + RS(740, 745),
                  '提示词写进 `.prompt` 文件，`Popen(["bash","-c",script], start_new_session=True)`，写 meta（含 pid），返回。' + RS(762, 771)],
                 [note('注释', '毫秒后缀是被真实事故逼出来的：同一秒两单会共用 id，文件互相覆盖。',
                       q='毫秒后缀去重：同一秒下多单会共享 id，prompt/status/workdir 互相踩（实案）', ref=S(591)),
                  note('注释', '起的是 `claude -p` 子进程而不是在服务里调 API：走订阅登录。',
                       q='引擎二选一，都走各自的订阅登录，不走 API', ref=S(639)),
                  note('推断', '新会话 + 不 wait：hub 进程只管接单，生成要跑几十分钟，不该占着请求线程，也不该随服务重启一起死。')],
                 ['`start_new_session=True` 逃不出 systemd 的 cgroup；「服务重启不杀任务」另一半靠仓库外的 `KillMode=process` 配置，仓库里看不到。',
                  '生成任务**没有超时**：`TIMEOUT_S = 300` 只管问答，不管这里。' + RS(59)]),
             to='src_spawn'),

        part('cache', '共享仓库缓存', [950, 8, 200, 124], m=[8, 572, 200, 124], kind='store', n='4', code='.repos/',
             glyph=g_list(['nano-vllm-155c1586/', '.ready ✗ 不存在', '同仓任务：无']),
             say='同一个地址只 clone 一份', why='每单各 clone 曾写爆磁盘',
             trap='有人在读时不刷新',
             card=card(
                 '后台脚本的第一段（prep）：把仓库准备好。同一个 git 地址的所有任务共用一份浅克隆，agent 就在这份缓存里读代码。',
                 ['目录名 = URL 尾名 + URL 的 md5 前 8 位；例子里是 `nano-vllm-155c1586`。' + RS(352, 355),
                  '看两件事选四种姿势：**`.ready` 在不在**、**有没有同仓任务在跑**。在 + 有人在跑 → 照用；在 + 没人 → fetch 刷新；不在 + 有人 → 每 2 秒等 `.ready`，最多 30 分钟；不在 + 没人 → `clone --depth 1` 后 `touch .ready`。' + RS(722, 734),
                  'hub 启动时清掉 30 天没碰过的缓存。' + RS(425, 436),
                  '四种姿势都有测试（code-canvas/tests/hub.mjs 第 114–166 行）：首单 clone、再下单走 fetch、同仓在跑时零 git 调用、等 `.ready` 后继续。'],
                 [note('注释', '以前每单各 clone 一份，攒到 21G 把磁盘写爆，才改成共用。',
                       q='每单各自 clone 曾攒 21G 写爆磁盘', ref=S(717)),
                  note('注释', '别的 agent 正在读这份缓存时不 fetch，免得代码在它脚下变了。',
                       q='刷新只在无同仓任务在跑时做，防止把文件从读者脚下抽走', ref=S(718)),
                  ],
                 ['「任务全程只读缓存」只是约定：agent 带着跳过权限的参数在这个目录里跑，代码并不强制只读。' + RS(717, 718),
                  '`.ready` 是「就绪」信号文件，不是锁；真正防两单同时 clone 的，是上一格说的 `SPAWN_LOCK`。']),
             to='src_cache'),

        # ---------------------------------------------------------------- 第二排：后台 bash 里的两段 + 账本
        part('ledger', '任务账本', [330, 188, 200, 140], m=[236, 590, 100, 170], kind='store', code='.jobs/',
             glyph=g_list(['.prompt', '.meta', '.status —'], on=[0, 1]),
             say='交接全靠文件', why='没有队列',
             trap='不是数据库',
             card=card(
                 '服务进程和后台任务之间唯一的「通信方式」：每单在 `<hub>/.jobs/` 下 6 个文件 + 1 个工作目录。谁想知道这单怎样了，就去读文件。',
                 ['`<id>.prompt / .log / .status / .meta.json / .result.json / .timing.json`，以及工作目录 `<id>/`（agent 的 `canvas.json` 交在这里）。' + RS(594, 604),
                  '起任务时写 `.prompt` 和 `.meta.json`（含 pid）；claude 的事件流落在 `.result.json`；脚本最后把退出码写进 `.status`。' + RS(652, 654),
                  '首页问 `/jobs` 时，服务当场扫这些文件：有 `.status` → 0 是 done，否则 `failed(码)`；没 `.status` 且 pid 已死 → `failed(中断)`；其余 running。' + RS(446, 453)],
                 [note('注释', '「中断」这一档是为了不让被杀掉的任务永远显示 running。',
                       q='进程没了又没留 status：被杀/宕机', ref=S(451)),
                  note('推断', '用文件交接，服务进程可以随时重启：重启后读一遍文件就恢复了全部任务的状态，不需要在内存里记任何东西。')],
                 ['这里没有队列、没有回调、没有推送：任务做完只是多了一个 `.status` 文件，等下次有人来问。',
                  '监视器显示的阶段进度（克隆 → 画布 JSON → 渲染 → 入库）也是看工作目录里**有哪些文件**猜出来的。' + RS(510, 521)]),
             to='src_jobs'),

        part('post', '服务端收尾', [640, 188, 200, 140], m=[8, 964, 200, 150], kind='gate', n='6', code='post_pass',
             glyph=g_checks([('canvas.json 在', None), ('embed 嵌全文', None), ('check_refs', None), ('validate', None)]),
             say='不信 agent，机械再查一遍', why='能机械做的就在服务端做',
             trap='只有 validate 能拦下画布',
             card=card(
                 '后台脚本的第三段：claude 退出码为 0 后，服务端自己把 agent 交的 `canvas.json` 再过一遍，全过才拷进画布库。',
                 ['顺序：文件不在 → rc=5；`embed_context.py` 把卡片引用的源文件全文塞进 `files`；`check_refs.py` 核依据、剔掉假的；`validate.py` 不过 → rc=4；`cp` 进库 + `render.py` 出 html + 写 `.src.json`。' + RS(627, 637),
                  'cp 或 render 失败 → rc=6；写来源记录那步末尾是 `:`，永远算成功。' + RS(634),
                  'embed 和 check_refs 后面接的是 `;`，退出码被忽略。' + RS(620, 621) + ' ' + RS(627, 628),
                  '最后外层 `echo $rc > .status`。' + RS(654)],
                 [note('提交', '作者的裁决：保证质量不能靠 agent 自觉守规程。',
                       q='保障不该压在 agent 守 SKILL 规程上', ref='c:690022d'),
                  note('提交', '起因：嵌入上下文原本只是 SKILL 里的可选项，agent 从来不做，新画布没有上下文。',
                       q='生成 agent 从不执行', ref='c:40781a8'),
                  note('注释', 'done 和「已入库」是一回事：没全过就不入库。',
                       q='全过才写进画布库（任务 done ⇔ 画布已入库）', ref=S(588)),
                  note('推断', 'embed 放在 validate 前面：validate 要拿源文件全文去核「推演里的变量名是不是编的」。')],
                 ['embed / check_refs 自己从不拦下画布（退出码被 `;` 吞掉）；check_refs 是「删掉假依据」而不是拒收。但 embed 一张都没嵌成时，validate 核名字只剩卡片代码可查，原本靠源文件全文通过的名字可能被判「编造」→ rc=4。',
                  'validate 服务端调用时没带 `--no-exec`，画布里的沙盘脚本会在 hub 机器上真跑一遍。' + R('code-canvas/validate.py', 88, 101)]),
             to=['dive_post', 'src_post']),

        part('agent', '生成 agent', [950, 188, 200, 140], m=[8, 760, 200, 140], kind='act', n='5', code='claude -p',
             glyph=g_list(['读 SKILL.md', '探索→假设→验证', '写 canvas.json', '让新读者挑错']),
             say='自己读代码，交一份 JSON', why='给判断标准，不给步骤',
             trap='它不画像素，只给粗位置',
             card=card(
                 '整条路上唯一「动脑子」的一步：一个无人值守的 `claude -p`，cwd 是仓库缓存，读 SKILL.md、自己研究代码，在工作目录里交出 `canvas.json`——一份只有数据、没有画面的 JSON。',
                 ['命令：`claude -p --model <默认 opus> "$(cat <id>.prompt)" --output-format stream-json --verbose --dangerously-skip-permissions`，stdout 落到 `.result.json`。' + RS(648, 652),
                  'SKILL.md 给的是目标（读者看完能做到的六条）+ 8 个必须自己做的判断 + 推荐循环 + 交付契约。' + R('code-canvas/SKILL.md', 11, 14),
                  '布局只给每张卡一个粗位置 `{col, band}`，不给像素。',
                  '它通常会自己跑 validate / check_refs / embed / render 自查；提示词还**建议**用 `reader_check.py` 再起一个 claude（默认 sonnet，只给去掉答案的画布）来挑错——但没有任何东西检查它做没做、做几轮。' + R('code-canvas/reader_check.py', 54)],
                 [note('注释', 'prompt 明说 SKILL 不是逐步执行的管线：怎么研究由 agent 自己判断，契约不可商量。',
                       q='它不是一条要逐步执行的管线', ref=S(668)),
                  note('文档', '只给粗位置：精确排版要量真实文字宽度，那只有浏览器做得到。',
                       q='浏览器能真实测文本宽度，Python 只能估——这是架构决定', ref='HANDOFF.md:177'),
                  note('提交', '用 stream-json 落盘，监视器可以边跑边读 agent 在干什么。',
                       q='生成任务 claude 改 --output-format stream-json 落盘', ref='c:d58259b'),
                  note('推断', '跳过权限：后台进程没人能点「允许」，不跳过就会卡死（底稿没找到原文理由）。')],
                 ['agent 在工作目录里渲染的 `canvas.html` 只给它自己截图看；入库的 html 是服务端从 json 重新 render 的。',
                  '一单可能有两层 claude 进程；`reader_check` 那一层的花费不在首页显示的指标里（推断）。',
                  '这一步耗时最长，行为每次都不同，没法从代码推出它具体做了什么。']),
             to='dive_agent'),

        # ---------------------------------------------------------------- 第三排：库和读者
        part('lib', '画布库', [640, 384, 200, 130], m=[8, 1178, 200, 130], kind='store', n='7', code='<hub>/',
             glyph=g_list(['nano-vllm.json', 'nano-vllm.html', 'nano-vllm.src.json']),
             say='json + 单文件 html + 来源', why='留 json：模板升级可重渲染',
             trap='入库的 html 不是 agent 那份',
             card=card(
                 '收尾的终点：每张画布三份文件。首页的画布列表、`/c/<name>/` 和下载都从这里读。',
                 ['`<name>.json` 是数据本体；`<name>.html` = 模板 + 这份数据（render 把 JSON 塞进占位符）；`<name>.src.json` 记来源 `{"git_url": …}`。' + R('code-canvas/render.py', 19, 25),
                  '来源记录决定首页按仓库分组，也决定问答时去哪找仓库。' + RS(285, 299)],
                 [note('提交', '来源记录只在入库时写：失败的任务不留痕。',
                       q='来源记录入库时才写', ref='c:4b43d42'),
                  note('推断', 'html 是自包含单文件，下载下来离线也能打开；留着 json 是为了模板升级时重渲染、问答时打补丁。')],
                 ['render 失败时 json 已经拷进来了：首次生成会剩一个只有 json 的半成品（列表只认 html，所以看不见）；同名画布已存在时，json 换成新的、html 还是旧的。']),
             ),

        part('view', '读者浏览器', [330, 384, 200, 124], m=[8, 1372, 200, 124], kind='out', n='8', code='layout()',
             glyph=g_kv([('col 0 → x', '370'), ('band 0 → y', '160'), ('band 2 → y', '720')]),
             say='量宽、排卡、连线都在这', why='只有浏览器量得准字宽',
             trap='排版不是生成时做的',
             card=card(
                 '读者打开 `/c/nano-vllm/`：服务把 html 原样发过来，**像素级的排版在读者浏览器里才发生**。',
                 ['服务端先 `maybe_rerender`：html 比模板旧就用旁边的 json 重新 render，然后原样返回。' + RS(1042, 1044) + ' ' + RS(236, 262),
                  '页面里 `const DATA = /*CANVAS_DATA*/null;` 已被换成整份数据；下一帧 `layout()`：量每张卡的自然宽度 → 按 col 排列 → 卡的基准 y = 160 + band × 280 → 同列下推。（code-canvas/template/canvas.html 第 1031–1080 行）',
                  '按 ▶ 时 `setStep(i)` 展开指定的卡、点亮行和连线。（code-canvas/template/canvas.html 第 1689–1721 行）'],
                 [note('文档', '模板改了不用重跑生成：打开时发现 html 比模板旧就自动重渲染。',
                       q='模板变更**无需**重渲染存量画布（serve 时自动重渲染）', ref='AGENTS.md:17'),
                  note('文档', 'agent 给粗位置，渲染器精排。',
                       q='agent 只给粗位置 {col, band}，渲染器精排', ref='HANDOFF.md:164')],
                 ['「agent 画了画布」是最常见的误会：agent 交的是 JSON，同一份 JSON 在不同屏幕上会排出不同的像素。']),
             to='dive_render'),
    ]

    wires = [
        wire('w_req', 'form', 'gate', 'POST /generate', ml='POST', n='1', why='立即返回任务单',
             card=card('浏览器把 `{ask, git_url}` 发给 hub。',
                       'HTTP 请求，JSON body；响应**立即**返回，只含任务单，不等生成。' + RS(888, 889),
                       [note('推断', '生成要几十分钟，HTTP 请求不能挂那么久；所以只回一个单号，进度另外查。')])),
        wire('w_lock', 'gate', 'spawn', '持锁调用', n='2', why='判定+写 meta 要原子',
             card=card('校验通过后，同一个请求线程里直接调 `spawn_generate`。',
                       '包在 `with SPAWN_LOCK:` 里，同步调用。' + RS(1160, 1161),
                       [note('注释', '锁住的是「看有没有同仓任务」和「把自己的 meta 写下去」这一对动作。',
                             q='缓存取用模式的判定+meta 落盘要原子，防同仓并发单互踩', ref=S(1160))],
                       '`_running_git_job` 靠扫 meta 文件判断同仓任务；两单交错就会都去 `rm -rf` 再 clone。')),
        wire('w_pop', 'spawn', 'cache', 'Popen 后放手', ml='Popen 放手', n='3', cls='dash', why='谁也不等谁',
             trap='没有队列也没有回调',
             card=card('起一个脱离服务的后台 bash，它的第一段就是准备仓库缓存。',
                       '`Popen(["bash","-c",script], start_new_session=True)`；之后服务端不 wait，只把 pid 记进 meta。' + RS(763, 770),
                       [note('推断', '这条线是「断开」的：从这以后的状态、进度、产物，全靠写文件、下次被问时再读。')],
                       ['git 源时整段包成 `{ prep; } && cd 缓存 && { gen; }`；不包 `{}` 的话 `&&` 只管第一条命令，取缓存失败 agent 也会照跑。' + RS(738, 740)])),
        wire('w_book', 'spawn', 'ledger', '写 prompt + meta', ml='写账本', mfs='r', mts='t', mlp=[286, 520],
             card=card('起任务时落两份文件：提示词和任务单。',
                       '`.prompt` 存提示词，脚本里用 `$(cat .prompt)` 喂给 claude；`.meta.json` 存 id、名字、ask、pid、mode、缓存路径。' + RS(762) + ' ' + RS(764, 770),
                       [note('推断', '提示词走文件而不是命令行字面量：内容长、含中文和引号，放文件里免去一层转义。')])),
        wire('w_cd', 'cache', 'agent', 'cd 缓存，跑 claude', ml='cd 缓存跑', n='4',
             card=card('仓库就绪后，脚本 `cd` 进缓存目录，同步跑 claude，等它退出拿 `rc`。',
                       '`{prep} && cd {缓存} && { gen; }`；gen 里先记时间 T1，跑 claude，记 `rc=$?` 和 T2，写 `.timing.json`。' + RS(652, 653) + ' ' + RS(740),
                       [note('推断', 'cwd 就是仓库，agent 读代码用相对路径最自然；它的产物另写到工作目录，不碰缓存。')])),
        wire('w_self', 'agent', 'agent', '自查、返工', fs='b', mfs='r', cls='dash', why='第一层校验：agent 自己',
             card=card('agent 在自己的循环里反复跑工具：validate / check_refs / embed / render 截图，提示词还建议跑 reader_check（不强制）。',
                       'SKILL 第 3 节推荐的循环：探索 → 假设 → 验证 → 画 → 让新读者挑错，读者做不到的地方回去改。' + R('code-canvas/SKILL.md', 69, 90),
                       [note('注释', 'prompt 把完成标准定成 SKILL 第 1 节的六条：一位第一次接触代码库的读者只看画布就能做到。reader_check 的「新读者」只是建议的自查手段，不是标准本身。',
                             q='完成的标准是 SKILL 第 1 节', ref=S(663))],
                       '这些脚本和服务端收尾用的是**同一批**；agent 通常自查若干次，服务端收尾一定再跑一次。')),
        wire('w_json', 'agent', 'post', 'canvas.json，rc=0', ml='canvas.json', n='5',
             card=card('agent 退出后，脚本看退出码和工作目录里有没有 `canvas.json`。',
                       '`rc` 为 0 才进收尾；文件不在就 `rc=5`。交接物就是这一个文件。' + RS(633),
                       [note('注释', 'agent 被明确要求不要自己入库。',
                             q='**不要写入画布库**——入库由服务端完成', ref=S(666))])),
        wire('w_st', 'post', 'ledger', '写 .status', mfs='r', mts='b', mlp=[286, 960], why='退出码就是结果',
             card=card('脚本的最后一个动作：把 rc 写进 `.status`。',
                       '0 = 已入库；3 / 4 / 5 / 6 分别是升级被拒、validate 未过、没产出、渲染失败。' + RS(609, 610) + ' ' + RS(654),
                       [note('推断', '一个数字就够首页区分成功和失败原因；细节在 `.log` 里。')])),
        wire('w_lib', 'post', 'lib', '全过才入库', ml='全过入库', n='6', trap='入库的是服务端重渲染的 html',
             card=card('`cp canvas.json <hub>/nano-vllm.json`，再 `render.py` 出 `nano-vllm.html`，最后写 `nano-vllm.src.json`。',
                       '三步用 `&&` 串起来；cp 或 render 失败就 `rc=6`，写来源记录那步末尾带 `:`，永远算成功。' + RS(634),
                       [note('推断', '用 `&&` 串：前一步失败后一步就不做，也就不会留下指向坏 html 的来源记录。')],
                       '测试（code-canvas/tests/hub.mjs 第 630–632 行）：假 agent 产出 validate 不过的画布 → 不入库，status 为 4，也不写来源记录。')),
        wire('w_poll', 'ledger', 'form', '每 5 秒轮询', ml='5 秒轮询', cls='dash', mfs='l', mts='r', why='拉取，没有推送',
             card=card('首页发现「做完了」的唯一方式：有 running 的任务时，每 5 秒 `GET /jobs` 一次。',
                       '服务端 `list_jobs` 当场扫 `.jobs/*.meta.json`，读旁边的 `.status`、`.timing.json` 和 `.result.json` 的最后一条 result 事件（token、成本）。' + RS(953) + ' ' + RS(439, 465),
                       [note('推断', '服务和任务之间本来就没有通道，轮询文件是最简单、重启也不丢的办法。')],
                       '这条线在代码里其实是「浏览器 → hub 服务 → 读账本」；图上画成从账本指回首页，表示信息的流向。')),
        wire('w_open', 'lib', 'view', 'GET /c/nano-vllm/', ml='GET /c/…', n='7',
             card=card('读者打开画布：服务从库里找 html（找不到再去 examples/），必要时重渲染，原样返回。',
                       '`_canvas_path` 先校验名字白名单、再找文件；`maybe_rerender` 只比较 html 和模板的修改时间。' + RS(1011, 1020) + ' ' + RS(256, 260),
                       [note('注释', 'html 只是模板的一张快照，所以模板一更新，旧画布下次打开就自动换新皮。',
                             q='画布 html 是模板快照：若旁边有 json 且 html 比模板旧，自动重渲染。', ref=S(237))])),
    ]

    J = 'code-canvas/demo/nano-vllm.json'
    steps = [
        beat('填表，点「生成画布」',
             '地址填 `https://github.com/GeeeekExplorer/nano-vllm`，关注点填「讲讲调度器怎么工作」（这是输入框的占位示例），名字留空。'
             '浏览器发出的 body 只有两项：`{"ask": "讲讲调度器怎么工作", "git_url": "https://github.com/GeeeekExplorer/nano-vllm"}`。',
             note('推断', 'name 是 undefined，`JSON.stringify` 直接丢掉；默认选项（git / 深潜 / Claude）不发字段，服务端按默认处理。'),
             focus=['form', 'w_req', 'gate']),
        beat('hub 把关',
             'ask 非空 ✓；地址是 https ✓；名字取 URL 尾段 → **`nano-vllm`**，字符合法 ✓；engine 默认 **claude** ✓。'
             '得到 `src = {"kind": "git", …}`、`preview = False`、`zoom = False`、`model = ""`。',
             note('推断', '名字只放行安全字符，因为它会拼进文件路径和 URL。'),
             focus=['gate', 'w_lock', 'spawn'],
             state={'gate': g_checks([('ask 非空', 'ok'), ('http(s) 地址', 'ok'), ('name=nano-vllm', 'ok')])}),
        beat('起编号、写账本、放手',
             '下单时刻设为 10:15:30.482 → 编号 **`' + JOB + '`**。写下 `.prompt` 和 `.meta.json`'
             '（mode `"deep"`、pid、缓存路径），`Popen` 起后台 bash 后立刻返回；浏览器拿到 `{"ok": true, "job": {…}}`，1.2 秒后自己刷新。',
             note('注释', '毫秒后缀：同一秒两单会共用 id、互相覆盖文件（真实踩过）。',
                  q='毫秒后缀去重：同一秒下多单会共享 id，prompt/status/workdir 互相踩（实案）', ref=S(591)),
             focus=['spawn', 'w_book', 'ledger', 'w_pop'],
             state={'ledger': g_list(['.prompt ✓', '.meta ✓', '.status —'], on=[0, 1])}),
        beat('首单：clone 仓库',
             '缓存目录 `.repos/nano-vllm-155c1586`（尾数是这个 URL 的 md5 前 8 位）。`.ready` 不存在、也没有同仓任务在跑 → '
             '`rm -rf` 后 `git clone --depth 1`，成功才 `touch .ready`。下一单同地址的任务会走 fetch 刷新，而不是再 clone。',
             note('注释', '共用一份克隆：以前每单各 clone，攒到 21G 写爆了磁盘。', q='每单各自 clone 曾攒 21G 写爆磁盘', ref=S(717)),
             focus=['cache', 'w_pop'],
             state={'cache': g_list(['nano-vllm-155c1586/', '.ready ✓ clone 完成', '同仓任务：无'], on=1)}),
        beat('agent 开工',
             '`cd` 进缓存，跑 `claude -p`（没传模型，用默认 `claude-opus-5-5`）。它拿到的只有这段提示词：'
             '「阅读 SKILL.md…任务：为仓库 …/nano-vllm-155c1586（共享缓存，clone 自 …）生成一张 Code Canvas，主题/关注点：讲讲调度器怎么工作…交付：…/' + JOB + '/canvas.json…不要写入画布库」。',
             note('注释', '提示词只给标准和契约，不给步骤。', q='它不是一条要逐步执行的管线', ref=S(668)),
             focus=['agent', 'w_cd', 'cache']),
        beat('agent 交出 canvas.json',
             'agent 每次行为都不同，这里拿仓库里现成的 `' + J + '` 当「一份合格的交付」：**10 张卡**分在 col 0–3，'
             '**11 根线**（8 call、2 data、1 struct），**6 步**推演，3 条故事线。每张卡只有粗位置，比如 `step` 卡是 `{col: 0, band: 0}`。'
             '中间它会反复自查（虚线回环），也可能再起一个 sonnet 读者来挑错。',
             note('文档', '只给粗位置、不给像素：排版要量真实字宽，只有浏览器做得到。',
                  q='浏览器能真实测文本宽度，Python 只能估——这是架构决定', ref='HANDOFF.md:177'),
             focus=['agent', 'w_self', 'w_json'],
             state={'agent': g_kv([('cards', '10（col 0–3）'), ('wires', '11'), ('steps', '6'), ('layout', '{col, band}')])}),
        beat('服务端收尾：再查一遍',
             'claude 退出码 0，`canvas.json` 在 ✓。embed：把卡片引用的源文件全文塞进 `files`（demo 里嵌的是 5 个）✓；check_refs：设 agent 写了 6 条依据，6 条全过 ✓（仓库里的旧格式样板其实没有 refs）；'
             'validate：**0 ERROR** ✓（缺新字段只报 warn）。',
             note('提交', '不信任 agent：能机械做的在服务端做。', q='保障不该压在 agent 守 SKILL 规程上', ref='c:690022d'),
             focus=['post', 'w_json'],
             state={'post': g_checks([('canvas.json 在', 'ok'), ('embed 嵌全文', 'ok'), ('check_refs', 'ok'), ('validate', 'ok')])}),
        beat('入库，写 .status = 0',
             '`cp` 得到 `<hub>/nano-vllm.json`，`render.py` 生成 `nano-vllm.html`（模板 + 约 4.7 万字符的数据），再写 `nano-vllm.src.json`。'
             '最后 `echo 0 > ' + JOB + '.status`。',
             note('注释', 'done 就等于已入库。', q='全过才写进画布库（任务 done ⇔ 画布已入库）', ref=S(588)),
             focus=['lib', 'w_lib', 'post', 'w_st', 'ledger'],
             state={'lib': g_list(['nano-vllm.json ✓', 'nano-vllm.html ✓', 'nano-vllm.src.json ✓'], on=[0, 1, 2]),
                    'ledger': g_list(['.prompt ✓', '.meta ✓', '.status 0'], on=[2])}),
        beat('首页发现「done」',
             '首页还在每 5 秒问一次 `/jobs`。这一次 `list_jobs` 读到 `.status` 内容是 `0` → 显示 **done**，并附上 clone / claude 分段耗时和 token、成本。'
             '整单通常 20–40 分钟，大头在 agent 那一步。',
             note('推断', '服务从不主动通知；任务做完只是多了一个文件，等下一次轮询发现。'),
             focus=['form', 'w_poll', 'ledger'],
             state={'form': g_text('done', JOB, tone='ok')}),
        beat('读者打开，浏览器排版',
             '`GET /c/nano-vllm/`，html 比模板新，原样返回。`layout()`：col 0 的 x = **370**（左上有角落 note，先让出 330）；'
             '`step` 卡 y = 160；`run` 卡 band 2 → y = 160 + 2 × 280 = **720**（会再下推）。按 ▶ 进入第 ① 步：4 根线点亮，两端 4 张卡亮、其余 6 张变暗；镜头放大到 `step` 卡的第 2–5 行。',
             note('文档', 'agent 只给粗位置，渲染器精排。', q='agent 只给粗位置 {col, band}，渲染器精排', ref='HANDOFF.md:164'),
             focus=['view', 'w_open', 'lib']),
    ]

    base['blocks'] = [scene(1220, 518, parts, wires, steps, mw=340, mh=1510,
                            cap='主路：hub 首页 → /generate → 后台脚本（缓存 → agent → 收尾）→ 画布库 → 读者浏览器')]
    base['children'] = [
        _src_generate(), _src_spawn(), _src_cache(), _src_jobs(), _src_post(),
        slot('dive_agent', '生成 agent 里面'),
        slot('dive_render', '浏览器怎么把 JSON 排成画布'),
        slot('dive_post', '收尾的三道机械核对'),
    ]
    return base


# ---------------------------------------------------------------- 源码页
def _src_generate():
    return node(
        'src_generate', '/generate 源码', '/generate：几条硬规矩，全过才起任务',
        '顶层「/generate 把关」那个框的源码：从读 body 到调用 spawn_generate。',
        src=SV + ':1112-1162',
        blocks=[code(SV, [(1112, 1162)], notes=[
            at((1118, 1119), '推断', 'ask 在后端再查一次：前端拦过，但接口也可能被直接调用。', has='if not ask:'),
            at((1123, 1124), '推断', 'git 地址只收 http(s)：例子里 `https://github.com/GeeeekExplorer/nano-vllm` 过。', has='^https?://'),
            at(1126, '推断', '默认名 = URL 最后一段去掉 `.git`；例子里得到 `nano-vllm`。', has='rsplit("/", 1)[-1]'),
            at((1152, 1153), '推断', '名字会拼进文件路径和 URL，只放行安全字符（测试 code-canvas/tests/hub.mjs 第 473 行：路径穿越的名字返回 404）。', has='NAME_RE.match(name)'),
            at((1160, 1161), '注释', '判定缓存取用方式和写 meta 要一气呵成，所以整个调用包在锁里。',
               q='缓存取用模式的判定+meta 落盘要原子，防同仓并发单互踩', has='with SPAWN_LOCK'),
            at(1162, '推断', '立即把任务单回给浏览器，不等生成。', has='"ok": True'),
        ], cap='do_POST 的 /generate 分支')])


def _src_spawn():
    return node(
        'src_spawn', 'spawn_generate 源码', 'spawn_generate：拼提示词、拼脚本、起进程就走',
        '顶层「起任务」那个框的源码，分五段：编号和账本 → claude 命令 → 提示词 → 脚本组装 → Popen。',
        src=SV + ':584-771',
        blocks=[
            code(SV, [(591, 604)], notes=[
                at((591, 592), '注释', '编号带毫秒后三位，例子里是 `' + JOB + '`；同一秒两单不会再撞。',
                   q='毫秒后缀去重：同一秒下多单会共享 id，prompt/status/workdir 互相踩（实案）', has='-{:03d}'),
                at((594, 596), '推断', '账本：每单一组 `<id>.*` 文件 + 一个工作目录，服务和后台任务靠它们交接。', has='.prompt'),
                at(598, '推断', 'agent 要交的文件：工作目录里的 `canvas.json`。', has='canvas.json'),
                at(599, '推断', '入库目标 `<hub>/<name>.json` 和 `.html`：只有收尾全过才会写到这里。', has='ARGS.hub / (name + ".json")'),
            ], cap='编号和账本'),
            code(SV, [(639, 657)], notes=[
                at(639, '注释', '起子进程而不是调 API：走订阅登录。', q='引擎二选一，都走各自的订阅登录，不走 API'),
                at((648, 651), '推断', 'claude 命令：提示词从 `.prompt` 文件读，输出 stream-json 方便监视器边跑边读；跳过权限因为没人能点「允许」。', has='--dangerously-skip-permissions'),
                at(652, '推断', 'claude 同步跑完才拿 `rc=$?`；stdout 落到 `.result.json`。', has='rc=$?'),
                at(654, '推断', '`{post}` 是占位：后面换成 post_pass 拼出的收尾命令，最后 `echo $rc` 写进 `.status`。', has='echo $rc > {st}'),
            ], cap='claude 命令和 gen 段'),
            code(SV, [(659, 669)], notes=[
                at(666, '注释', '明确告诉 agent 不要自己入库：入库归服务端。', q='**不要写入画布库**——入库由服务端完成'),
                at(668, '注释', 'SKILL 不是管线：给判断标准、要做的判断、工具和契约。', q='它不是一条要逐步执行的管线'),
            ], cap='提示词的 head 和 tail'),
            code(SV, [(735, 745)], notes=[
                at(736, '推断', '提示词 = head + 任务行（缓存路径 + 原始 URL + 关注点）+ tail。', has='prompt = head + task_line'),
                at((738, 740), '注释', '整段必须包成 `{}` 命令组，否则取缓存失败时 agent 照样开跑。',
                   q='gen 是分号串，必须包成 {} 命令组，否则 && 只约束它第一条命令', has="T0=$(date +%s)"),
                at((741, 743), '推断', '取缓存失败的分支：只记 clone 耗时，把非零 rc 写进 `.status`，不跑 claude。', has='rc=$?'),
            ], cap='git 源：组装整段脚本'),
            code(SV, [(762, 771)], notes=[
                at(762, '推断', '提示词落到 `.prompt` 文件，脚本里用 `$(cat …)` 读。', has='prompt_f.write_text'),
                at(763, '推断', '新会话起后台 bash；之后不 wait，这条线从此断开。', has='start_new_session=True'),
                at((764, 770), '推断', 'meta 记下 pid 和 mode `deep`、缓存路径；首页靠 pid 判断「中断」。', has='"pid": proc.pid'),
            ], cap='放手：写 prompt、Popen、写 meta'),
        ])


def _src_cache():
    return node(
        'src_cache', '仓库缓存源码', '共享缓存：四种取用姿势',
        '顶层「共享仓库缓存」那个框的源码：按「.ready 在不在 × 有没有同仓任务在跑」选 prep 命令。',
        src=SV + ':715-734',
        blocks=[code(SV, [(715, 734)], notes=[
            at((716, 718), '注释', '共用一份浅克隆，起因是每单各 clone 写爆过磁盘。', q='每单各自 clone 曾攒 21G 写爆磁盘'),
            at((719, 720), '推断', '缓存目录 + 旁边的 `.ready` 标记；例子里是 `nano-vllm-155c1586` 和 `nano-vllm-155c1586.ready`。', has='.ready'),
            at((723, 724), '推断', '已就绪 + 有同仓任务在跑：只 touch，不动文件。', has='touch {rd}'),
            at((726, 728), '注释', '已就绪 + 没人在读：fetch + reset + clean 刷新到最新；失败就用旧缓存。',
               q='刷新只在无同仓任务在跑时做，防止把文件从读者脚下抽走', ref=S(718), has='fetch --depth 1'),
            at(731, '推断', '别人正在 clone：每 2 秒看一次 `.ready`，最多 900 次 ≈ 30 分钟。', has='seq 1 900'),
            at((733, 734), '推断', '首单（例子走这条）：清空后 `clone --depth 1`，成功才 touch `.ready`。', has='clone --depth 1'),
        ], cap='prep 的四个分支')])


def _src_jobs():
    return node(
        'src_jobs', 'list_jobs 源码', 'list_jobs：每次被问都现扫账本',
        '顶层「任务账本」那个框怎么被读：首页每 5 秒问一次 /jobs，服务端当场扫文件。',
        src=SV + ':439-465',
        blocks=[code(SV, [(439, 465)], notes=[
            at(441, '推断', '没有内存里的任务表：每次都 glob 一遍 meta 文件。', has='glob("*.meta.json")'),
            at((446, 449), '推断', '有 `.status`：内容 0 是 done，否则 `failed(码)`。例子最后读到 `0`。', has='"done" if code == "0"'),
            at((450, 451), '注释', '没 status、pid 也死了：如实标中断，不再永远 running。', q='进程没了又没留 status：被杀/宕机'),
            at((455, 461), '推断', '顺带附上分段耗时和 claude 的指标信封（token、成本）。', has='read_result_envelope'),
        ], cap='list_jobs 全文')])


def _src_post():
    return node(
        'src_post', 'post_pass 源码', 'post_pass：一串 shell，全过才入库',
        '顶层「服务端收尾」那个框的源码：拼出一串 shell，插在 claude 之后、写 .status 之前。本图走的是默认分支（不是 zoom、不是升级）。',
        src=SV + ':608-637',
        blocks=[code(SV, [(608, 637)], notes=[
            at((609, 610), '注释', '退出码表：3 升级被拒 / 4 validate 未过 / 5 没产出 / 6 渲染失败。', q='服务端入库（不信任 agent 守规程）'),
            at((620, 621), '推断', 'check_refs 后面接 `;`：它的退出码不影响 rc，核不过的依据被就地剔除。', has='check_refs.py'),
            at((627, 628), '推断', 'embed 同样接 `;`：嵌入失败画布照样往下走；但没了 `files`，后面 validate 的名字核对只剩卡片代码可查，会更严。', has='embed_context.py'),
            at((633, 635), '推断', '真正的闸门：rc 为 0 → 文件在（否则 5）→ embed → check_refs → validate（否则 4）→ cp + render + 来源记录（否则 6）。', has='rc=6'),
        ], cap='post_pass（默认分支在 627-637）')])
