#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py —— 「研究员 + 讲解员」两人组的自动调度。py3.8 兼容。

只做调度和机械核对，不做任何内容判断：画什么、哪些框往下钻、钻进去问什么，全由 agent 决定。
每个 agent 是一次无人值守的 `claude -p`（走本机 claude 的登录），模型按角色分开配。

用法:
  python3 -I run.py --repo <仓库路径> --question "<问题>" --out <运行目录> [选项]

选项:
  --models k=v,...  各角色用哪档模型；默认 research_top=opus,present_top=opus,research_dive=sonnet,present_dive=opus
  --max-dives N     顶层讲解员最多申请几个框往下钻（默认 3）
  --budget USD      全程 agent 花费上限（默认 50）。每启动一个 agent 前检查「已花 + 在跑的预估 + 这一步的预估」
  --parallel N      下钻并行数（默认 3）
  --cli-bin PATH    claude 命令（默认 claude）
  --name NAME       成品文件名（默认取运行目录名）

再跑同一个 --out = 接着上次：已成功的步骤直接跳过。

流程:
  1 研究员（顶层）→ research/manuscript.md → 机械核对（核不过：让同一位研究员接着改一次）
  2 讲解员（顶层）→ content/content_scene.py + content/dives.json（它申请往下钻的框）
  3 每条申请（按它排的优先级；受 --max-dives / --budget 限制；并行）：
      研究员（补充）→ research/manuscript-<槽位>.md → 机械核对（同上）
      讲解员（下层）→ content/content_<槽位>.py
  4 装配：build --strict + shot_scene --all → <运行目录>/<name>.html、report.md、report.json
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.join(HERE, 'agents')
STAGE = os.path.join(HERE, 'canvas')
SPOT = os.path.join(HERE, 'check', 'spot_manuscript.py')
TOOLROOT = os.path.abspath(os.path.join(HERE, '..', '..'))      # code-canvas/（工具和舞台）
sys.path.insert(0, os.path.join(HERE, 'check'))
import agent_report  # noqa: E402  从 stream-json 里取 agent 的报告（result 只是最后一条文字，可能不是报告）

DEFAULT_MODELS = dict(research_top='opus', present_top='opus', research_dive='sonnet', present_dive='opus')
# 预估（美元，按以往实测往高取）：只用于「这一步还启不启动」的预算判断
ESTIMATE = {'research': dict(fable=20, opus=10, sonnet=5, haiku=1),
            'present': dict(fable=14, opus=7, sonnet=4, haiku=1)}
CAP_FACTOR = 2.5            # 单个 agent 的硬上限 = 预估 × 这个数（claude --max-budget-usd）
AGENT_TIMEOUT = 60 * 60     # 单个 agent 最长跑多久（秒）
TOOLS_OK = 'Bash,Read,Write,Edit,Glob,Grep,TodoWrite'
TOOLS_NO = 'Agent,Task,WebFetch,WebSearch'

A = None                    # 命令行参数
OUT = REPO = CONTENT = HEAD = ''
LOCK = threading.Lock()
SPENT = [0.0]               # 已花（含上次运行留下的记录）
RESERVED = [0.0]            # 在跑的 agent 的预估
T0 = time.time()


# ---------------------------------------------------------------- 小工具
def read(p):
    with open(p, encoding='utf-8') as fh:
        return fh.read()


def write(p, s):
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(s)


def log(msg):
    line = '%s  %s' % (time.strftime('%H:%M:%S'), msg)
    with LOCK:
        print(line, flush=True)
        with open(os.path.join(OUT, 'progress.txt'), 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')


def fill(text, **vals):
    """同 agents/fill.py：{{名字}} 全部要填上，多给的名字也报错。"""
    need = set(re.findall(r'\{\{([A-Z_]+)\}\}', text))
    miss, extra = sorted(need - set(vals)), sorted(set(vals) - need)
    if miss or extra:
        raise SystemExit('模板填充出错 —— 没填: %s；多给: %s' % (' '.join(miss) or '无', ' '.join(extra) or '无'))
    return re.sub(r'\{\{([A-Z_]+)\}\}', lambda m: vals[m.group(1)], text)


def tpl(name, **vals):
    return fill(read(os.path.join(AGENTS, name)), **vals).rstrip('\n')


def git(*a):
    return subprocess.run(['git', '-C', REPO, '-c', 'safe.directory=*', '-c', 'core.hooksPath=/dev/null'] + list(a),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def fam(model):
    m = (model or '').lower()
    for k in ('fable', 'opus', 'sonnet', 'haiku'):
        if k in m:
            return k
    return 'opus'


# ---------------------------------------------------------------- 预算
def reserve(step, role, model):
    est = ESTIMATE[role][fam(model)]
    with LOCK:
        if SPENT[0] + RESERVED[0] + est > A.budget:
            return None
        RESERVED[0] += est
    return est


def release(est, cost):
    with LOCK:
        RESERVED[0] -= est
        SPENT[0] += cost


# ---------------------------------------------------------------- 跑一个 agent
def result_path(step):
    return os.path.join(OUT, 'logs', step + '.result.json')


def done_before(step):
    p = result_path(step)
    if os.path.exists(p):
        r = json.loads(read(p))
        if r.get('ok'):
            return r
    return None


def run_agent(step, prompt, role, model, resume=None, base_cost=0.0, held=None):
    """跑一次 claude -p；返回记录 dict（ok / cost / secs / turns / session / models / reply）。
    resume：接着某个会话说（claude 报的 total_cost_usd 是整段会话的累计，花费要减去 base_cost）。
    held：调用方已经替这一步预留好的预估额度（整个框一起预留时用）。"""
    old = done_before(step)
    if old:
        if held:
            release(held, 0.0)
        log('跳过 %s（上次已成功，$%.2f）' % (step, old.get('cost', 0)))
        return old
    est = held if held else reserve(step, role, model)
    if est is None:
        log('不启动 %s：预算不够（已花 $%.2f，在跑预估 $%.2f，上限 $%.0f）' % (step, SPENT[0], RESERVED[0], A.budget))
        return dict(step=step, role=role, model_req=model, ok=False, skipped='budget', cost=0.0)
    cap = est * CAP_FACTOR
    write(os.path.join(OUT, 'logs', step + '.prompt.md'), prompt)
    # prompt 必须紧跟 -p（--allowedTools 是可变参数，放在它前面的位置参数会被吞成工具名）
    cmd = [A.cli_bin, '-p', prompt, '--model', model, '--output-format', 'stream-json', '--verbose',
           '--allowedTools', TOOLS_OK, '--disallowedTools', TOOLS_NO, '--max-budget-usd', '%.2f' % cap]
    if resume:
        cmd += ['--resume', resume]
    log('启动 %s（%s，单步上限 $%.0f）' % (step, model, cap))
    t = time.time()
    res, ntool = None, 0
    errf = open(os.path.join(OUT, 'logs', step + '.stderr.txt'), 'w', encoding='utf-8')
    proc = subprocess.Popen(cmd, cwd=OUT, stdout=subprocess.PIPE, stderr=errf, universal_newlines=True,
                            encoding='utf-8', errors='replace')
    killer = threading.Timer(AGENT_TIMEOUT, proc.kill)
    killer.start()
    try:
        with open(os.path.join(OUT, 'logs', step + '.jsonl'), 'w', encoding='utf-8') as lf:
            for line in proc.stdout:
                lf.write(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('type') == 'result':
                    res = ev
                elif ev.get('type') == 'assistant':
                    for c in (ev.get('message') or {}).get('content') or []:
                        if isinstance(c, dict) and c.get('type') == 'tool_use':
                            ntool += 1
        proc.wait()
    finally:
        killer.cancel()
        errf.close()
    secs = int(time.time() - t)
    res = res or {}
    cost_total = float(res.get('total_cost_usd') or 0.0)
    cost = max(0.0, cost_total - base_cost)
    release(est, cost)
    rec = dict(step=step, role=role, model_req=model, ok=bool(res) and not res.get('is_error') and proc.returncode == 0,
               subtype=res.get('subtype'), rc=proc.returncode, cost=round(cost, 4),
               cost_session_total=round(cost_total, 4), secs=secs,
               turns=res.get('num_turns'), tools=ntool, session=res.get('session_id'),
               models=sorted((res.get('modelUsage') or {}).keys()))
    reply = agent_report.report(os.path.join(OUT, 'logs', step + '.jsonl')) or res.get('result') or '（没有最终回复）'
    write(os.path.join(OUT, 'logs', step + '.reply.md'), reply)
    write(result_path(step), json.dumps(rec, ensure_ascii=False, indent=1))
    log('%s %s：$%.2f，%d 分 %d 秒，%s 次工具调用，模型 %s' % (
        '完成' if rec['ok'] else '失败', step, cost, secs // 60, secs % 60, ntool, ','.join(rec['models']) or '?'))
    return rec


# ---------------------------------------------------------------- 机械核对
def spot(step, ms):
    p = subprocess.run([sys.executable, '-I', SPOT, ms, REPO, '--sample', '0'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    write(os.path.join(OUT, 'logs', step + '.spot.txt'), p.stdout)
    return p.returncode == 0, p.stdout


def build(out_dir, strict=False):
    cmd = [sys.executable, '-I', os.path.join(STAGE, 'build.py'), REPO, out_dir, '--content', CONTENT]
    if strict:
        cmd.append('--strict')
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    m = re.search(r'未填槽位 \d+：(.*)', p.stdout)
    unfilled = [] if not m or m.group(1).strip() == '（无）' else m.group(1).split()
    mw = re.findall(r'^WARN (\d+) 条', p.stdout, re.M)
    nwarn = int(mw[-1]) if mw else len(re.findall(r'^WARN \[', p.stdout, re.M))
    return dict(rc=p.returncode, out=p.stdout, unfilled=unfilled, warn=nwarn)


# ---------------------------------------------------------------- 两种角色
def research(step, ms, scope, notes, hint, model, held=None):
    rec = run_agent(step, tpl('researcher.md', QUESTION=A.question, SCOPE=scope, HEAD=HEAD, REPO=REPO,
                              REPO_NOTES=notes, ENTRY_HINT=hint, MANUSCRIPT=ms,
                              WORKDIR=os.path.join(OUT, 'research', 'work-' + step), TOOLROOT=TOOLROOT),
                    'research', model, held=held)
    recs = [rec]
    if not rec.get('ok') or not os.path.exists(ms):
        return False, recs, '研究员没交出底稿'
    ok, out = spot(step, ms)
    prev_fix = done_before(step + '.fix')
    if prev_fix:                    # 接着上次跑：返工那一步上次已经做过，记录照样算进报告
        recs.append(prev_fix)
    if not ok and rec.get('session') and not prev_fix:
        bad = '\n'.join(l for l in out.split('\n') if l.startswith('  BAD ') or l.startswith('  ??? '))[:6000]
        fix = run_agent(step + '.fix', (
            '交付后的独立机械核对（`%s`，对 HEAD）发现底稿 `%s` 里有核不过的锚点或提交哈希（BAD），'
            '以及逐字找不到的疑似引文（???；找不到不一定是编的，可能是跨行原话或你自己举的例子）：\n\n%s\n\n'
            '请逐条核实：BAD 改成正确的锚点 / 哈希，核实不了的就删掉那条断言（或改标【推断】并说明依据）；'
            '??? 里真是引文却对不上原文的，照原文改。'
            '不要动别的地方。改完用你自己的核对脚本再跑一遍。最终回复只写：改了几处、删了几处。'
            % (SPOT, ms, bad or out[-3000:])), 'research', model, resume=rec['session'],
            base_cost=float(rec.get('cost_session_total', rec.get('cost')) or 0.0))
        recs.append(fix)
        ok, out = spot(step + '.after-fix', ms)
    return True, recs, ('核对通过' if ok else '核对仍有不过的条目（见 logs/%s*.spot.txt）' % step)


def present(step, task, focus, manuscripts, model, held=None):
    out_dir = os.path.join(OUT, 'out', step)
    return run_agent(step, tpl('presenter.md', QUESTION=A.question, FOCUS=focus, TASK=task, MANUSCRIPTS=manuscripts,
                               STAGE=STAGE, HEAD=HEAD, REPO=REPO, CONTENT=CONTENT, OUT=out_dir),
                     'present', model, held=held)


def repo_notes():
    if git('config', '--get', 'remote.origin.promisor').stdout.strip() == 'true':
        return ('- 这是部分克隆：工作区已检出，可以直接读文件；但较大文件的历史内容没有下载，'
                '`git show` / `git log -S` 碰到它们可能失败或很慢，跳过即可。')
    return ''


# ---------------------------------------------------------------- 往下钻
def read_dives(unfilled):
    """读顶层讲解员的申请；只做格式检查（id 必须是它真留了的槽位），不做内容判断。"""
    p = os.path.join(CONTENT, 'dives.json')
    if not os.path.exists(p):
        return [], ['没有 dives.json（当作一条都没申请）']
    try:
        items = json.loads(read(p))
    except ValueError as e:
        return [], ['dives.json 不是合法 JSON：%s' % e]
    if not isinstance(items, list):
        return [], ['dives.json 不是数组']
    ok, notes, seen = [], [], set()
    for it in items:
        sid = str((it or {}).get('id', '')) if isinstance(it, dict) else ''
        if not re.match(r'^[A-Za-z0-9_]+$', sid):
            notes.append('丢弃一条：id 不合格 %r' % sid)
        elif sid in seen:
            notes.append('丢弃一条：id 重复 %s' % sid)
        elif sid not in unfilled and not (done_before('research-' + sid) or done_before('present-' + sid)):
            notes.append('丢弃 %s：顶层里没有这个未填槽位' % sid)   # 接着上次跑时，已经做过的框槽位是填上的，照样算
        else:
            seen.add(sid)
            ok.append(it)

    def prio(it):
        try:
            return int(it.get('priority', 99))
        except (TypeError, ValueError):
            return 99
    ok.sort(key=prio)
    if len(ok) > A.max_dives:
        notes.append('超出 --max-dives %d，不做：%s' % (A.max_dives, ' '.join(i['id'] for i in ok[A.max_dives:])))
        ok = ok[:A.max_dives]
    return ok, notes


def as_text(v):
    if isinstance(v, list):
        return '\n'.join('- %s' % x for x in v)
    return str(v or '')


def dive_research(it, ms_top, prev):
    """一个框的补充研究。prev = 前面已经研究完的框 [(条目, 底稿路径)]：它们补设的值要接着用（并行时各设各的，实测对不上）。
    返回 (row, 底稿路径或 None, 给讲解留的预算额度)。"""
    sid, title = it['id'], str(it.get('title') or it['id'])
    why = str(it.get('why') or '（没写）')
    er = 0 if done_before('research-' + sid) else ESTIMATE['research'][fam(A.models['research_dive'])]
    ep = 0 if done_before('present-' + sid) else ESTIMATE['present'][fam(A.models['present_dive'])]
    with LOCK:
        room = SPENT[0] + RESERVED[0] + er + ep <= A.budget
        if room:
            RESERVED[0] += er + ep
    if not room:
        log('不做 %s 这个框：研究 + 讲解的预估 $%d 放不进预算（已花 $%.2f，在跑预估 $%.2f，上限 $%.0f）' % (
            sid, er + ep, SPENT[0], RESERVED[0], A.budget))
        return dict(id=sid, title=title, why=why, research='没做（预算）', recs=[]), None, 0
    ms = os.path.join(OUT, 'research', 'manuscript-%s.md' % sid)
    sib = ''.join('\n  - `%s`（「%s」）' % (m, str(i.get('title') or i['id'])) for i, m in prev)
    notes = '\n'.join(x for x in [
        repo_notes(),
        '- **上一趟（顶层）底稿**（只读；不要改它，也不要重写它）：`%s`。先读讲解员指出的相关部分（%s）和贯穿全程的那个例子，'
        '其余不必读。' % (ms_top, it.get('sections') or '讲解员没指出'),
        '- 读者在顶层图上已经看到的这个框：`%s` 里 id 为 `%s` 的部件和它的说明卡（只为让你知道下一层从哪里接着讲；只看这一段即可）。'
        % (os.path.join(CONTENT, 'content_scene.py'), it.get('part') or sid),
        ('- **别的框的补充底稿**（前面几位研究员刚做完，只读）：' + sib + '\n  读者会在这几个框之间来回看，所以例子里**同一个量**'
         '（同一条请求的编号、行号、槽位号、批里有谁……）凡是它们已经补设过的，你都照用、不另起一套；只需要读它们里标「设」的值'
         '和与你这个框相交的部分。你新补设的值照样标「设」。') if prev else ''] if x)
    anchors = it.get('anchors') or []
    hint = '、'.join('`%s`' % a for a in anchors) if isinstance(anchors, list) and anchors else '讲解员没给；从上一趟底稿里这个框的锚点出发'
    ok, recs, chk = research('research-' + sid, ms,
                             tpl('scope-dive.md', TITLE=title, WHY=why, ASK=as_text(it.get('ask'))),
                             notes, hint, A.models['research_dive'], held=er or None)
    row = dict(id=sid, title=title, why=why, research=chk, recs=recs)
    if not ok:
        if ep:
            release(ep, 0.0)
        return row, None, 0
    return row, ms, ep


def dive_present(row, it, ms_top, ms, ep):
    sid, title = it['id'], str(it.get('title') or it['id'])
    why = str(it.get('why') or '（没写）')
    mss = ('  - 第一份（顶层研究；读者已经看过据此做的顶层图）：`%s`——只为接上例子和读者已经看到的东西，按需查\n'
           '  - 第二份（这一趟：「%s」这个框的下一层，主材料）：`%s`' % (ms_top, title, ms))
    rec = present('present-' + sid,
                  tpl('task-dive.md', TITLE=title, SLOT=sid, PART=str(it.get('part') or sid), WHY=why),
                  tpl('focus-dive.md', TITLE=title), mss, A.models['present_dive'], held=ep or None)
    row['recs'].append(rec)
    row['content'] = os.path.exists(os.path.join(CONTENT, 'content_%s.py' % sid))
    return row


# ---------------------------------------------------------------- 主流程
def main():
    global A, OUT, REPO, CONTENT, HEAD
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--question', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--models', default='', dest='models_raw')
    ap.add_argument('--max-dives', type=int, default=3)
    ap.add_argument('--budget', type=float, default=50.0)
    ap.add_argument('--parallel', type=int, default=3)
    ap.add_argument('--cli-bin', default='claude')
    ap.add_argument('--name', default='')
    A = ap.parse_args()
    A.models = dict(DEFAULT_MODELS)
    for kv in filter(None, A.models_raw.split(',')):
        k, v = kv.split('=', 1)
        if k not in DEFAULT_MODELS:
            raise SystemExit('--models 里不认识的角色：%s（可用：%s）' % (k, ' '.join(DEFAULT_MODELS)))
        A.models[k] = v
    OUT, REPO = os.path.abspath(A.out), os.path.abspath(A.repo)
    groot = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
    if OUT.startswith(groot + os.sep):
        raise SystemExit('--out 要放在代码仓库外面（agent 的工作目录在仓库里会读到给开发者看的 AGENTS.md）')
    CONTENT = os.path.join(OUT, 'content')
    for d in (OUT, CONTENT, os.path.join(OUT, 'logs'), os.path.join(OUT, 'research'), os.path.join(OUT, 'out')):
        if not os.path.isdir(d):
            os.makedirs(d)
    HEAD = git('rev-parse', '--short=10', 'HEAD').stdout.strip()
    if not HEAD:
        raise SystemExit('读不到仓库 HEAD：%s' % REPO)
    name = A.name or os.path.basename(OUT)
    for p in sorted(os.listdir(os.path.join(OUT, 'logs'))):       # 接着上次：把已花的钱算进来
        if p.endswith('.result.json'):
            SPENT[0] += float(json.loads(read(os.path.join(OUT, 'logs', p))).get('cost') or 0)
    log('开始：仓库 %s @ %s；模型 %s；最多下钻 %d 个；预算 $%.0f（之前已花 $%.2f）' % (
        REPO, HEAD, ','.join('%s=%s' % kv for kv in sorted(A.models.items())), A.max_dives, A.budget, SPENT[0]))
    rep = dict(question=A.question, repo=REPO, head=HEAD, models=A.models, budget=A.budget, steps=[], dives=[])

    # 1 顶层研究
    ms_top = os.path.join(OUT, 'research', 'manuscript.md')
    ok, recs, chk = research('research-top', ms_top, tpl('scope-top.md'), repo_notes(),
                             '没有；入口由你从仓库里找（README、目录结构、启动入口）', A.models['research_top'])
    rep['steps'] += recs
    rep['research_top'] = chk
    if not ok:
        return finish(rep, name, fatal='顶层研究失败：' + chk)

    # 2 顶层讲解
    rec = present('present-top', tpl('task-top.md', MAX_DIVES=str(A.max_dives)), tpl('focus-top.md'),
                  '  - `%s`' % ms_top, A.models['present_top'])
    rep['steps'].append(rec)
    if not rec.get('ok') or not os.path.exists(os.path.join(CONTENT, 'content_scene.py')):
        return finish(rep, name, fatal='顶层讲解员没交出 content_scene.py')
    b = build(os.path.join(OUT, 'out', 'check-top'))
    items, notes = read_dives(b['unfilled'])
    rep['dive_requests'] = [dict(id=i['id'], title=i.get('title'), why=i.get('why')) for i in items]
    rep['dive_notes'] = notes
    log('顶层讲解员申请往下钻 %d 个：%s%s' % (len(items), ' '.join(i['id'] for i in items) or '（无）',
                                     ('；' + '；'.join(notes)) if notes else ''))

    # 3 往下钻：补充研究按讲解员排的优先级依次跑（后一位接着用前面补设的值），每做完一份就并行派它的讲解员
    if items:
        prev, futs = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, A.parallel)) as ex:
            for it in items:
                try:
                    row, ms, ep = dive_research(it, ms_top, prev)
                except Exception as e:          # 一个框出事不拖垮别的框
                    row, ms, ep = dict(id=it.get('id', '?'), error=repr(e), recs=[]), None, 0
                if ms:
                    prev.append((it, ms))
                    futs.append(ex.submit(dive_present, row, it, ms_top, ms, ep))
                else:
                    rep['steps'] += row.pop('recs', [])
                    rep['dives'].append(row)
            for f in futs:
                try:
                    row = f.result()
                except Exception as e:
                    row = dict(id='?', error=repr(e), recs=[])
                rep['steps'] += row.pop('recs', [])
                rep['dives'].append(row)
        top_fixes(rep, rec)
    return finish(rep, name)


def top_fixes(rep, top_rec):
    """下层讲解员发现顶层有事实问题时写进 top-fixes-<槽位>.md（它们不许改顶层）；这里接着顶层讲解员的会话让它统一改一次。"""
    fs = sorted(f for f in os.listdir(CONTENT) if f.startswith('top-fixes-') and f.endswith('.md'))
    body = '\n\n'.join('### 来自 %s\n\n%s' % (f[len('top-fixes-'):-3], read(os.path.join(CONTENT, f)).strip()) for f in fs)
    rep['top_fixes'] = fs
    if not body.strip():
        return
    if not top_rec.get('session'):
        rep['top_fixes_note'] = '顶层讲解员的会话找不到，没交回'
        return
    log('下层讲解员报了顶层的问题（%s），交回顶层讲解员' % ' '.join(fs))
    r = run_agent('present-top.fix', (
        '你做的顶层图之后，系统按你的申请派了几位讲解员做下层。他们对照各自那份补充底稿，认为你的顶层有下面这些事实问题'
        '（他们不许改你的文件，所以交回给你）：\n\n%s\n\n'
        '请逐条对照底稿核实（补充底稿在 `%s` 下的 manuscript-*.md）：确实不准的，在 `content_scene.py` 里改准'
        '（只改说法和数，不重做图、不动坐标和结构）；你认为没错的，不改，并说明理由。改完 `--strict` 构建一次、'
        '`--check` 自检一次，确认 WARN 0、排版问题 0。不要改别的 content_*.py。最终回复只写：每条改了没有、为什么。'
        % (body, os.path.join(OUT, 'research'))),
        'present', A.models['present_top'], resume=top_rec['session'],
        base_cost=float(top_rec.get('cost_session_total', top_rec.get('cost')) or 0.0))
    rep['steps'].append(r)


def finish(rep, name, fatal=None):
    rep['fatal'] = fatal
    if not fatal:
        fdir = os.path.join(OUT, 'out', 'final')
        b = build(fdir, strict=True)
        rep['build'] = dict(rc=b['rc'], warn=b['warn'], unfilled=b['unfilled'], tail=b['out'][-2500:])
        write(os.path.join(OUT, 'logs', 'final-build.txt'), b['out'])
        page = os.path.join(fdir, 'scene.html')
        if os.path.exists(page):
            log('装配完成（strict 退出码 %d，WARN %d，未填槽位 %s）；跑 --all 自检' % (
                b['rc'], b['warn'], ' '.join(b['unfilled']) or '无'))
            try:
                p = subprocess.run(['node', os.path.join(STAGE, 'shot_scene.js'), page, os.path.join(fdir, 'shots'), '--all'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=40 * 60)
                out, rc = p.stdout, p.returncode
            except subprocess.TimeoutExpired as e:
                out, rc = (e.output or '') + '\n（超时）', -1
            write(os.path.join(OUT, 'logs', 'final-shots.txt'), out)
            fails = [l for l in out.split('\n') if l.startswith('FAIL')]
            summ = [l for l in out.split('\n') if '交互自检' in l]
            rep['shots'] = dict(rc=rc, fail=len(fails), fail_lines=fails[:30], summary=summ)
            shutil.copyfile(page, os.path.join(OUT, name + '.html'))
            rep['page'] = os.path.join(OUT, name + '.html')
    rep['spent'] = round(SPENT[0], 2)
    rep['wall_min'] = round((time.time() - T0) / 60, 1)
    write(os.path.join(OUT, 'report.json'), json.dumps(rep, ensure_ascii=False, indent=1))
    write(os.path.join(OUT, 'report.md'), report_md(rep))
    log('结束：花费 $%.2f，用时 %.0f 分钟%s' % (rep['spent'], rep['wall_min'], ('；致命：' + fatal) if fatal else ''))
    return 1 if fatal else 0


def report_md(rep):
    L = ['# 自动运行报告', '',
         '- 问题：%s' % rep['question'],
         '- 仓库：`%s` @ `%s`' % (rep['repo'], rep['head']),
         '- 模型配置：%s' % '，'.join('%s=%s' % kv for kv in sorted(rep['models'].items())),
         '- 花费：**$%.2f**（上限 $%.0f）；用时 %.0f 分钟' % (rep['spent'], rep['budget'], rep['wall_min'])]
    if rep.get('fatal'):
        L += ['', '**中止：%s**' % rep['fatal']]
    L += ['', '## 每一步', '', '| 步骤 | 模型（实际） | 花费 | 用时 | 工具调用 | 结果 |', '|---|---|---|---|---|---|']
    for r in rep['steps']:
        res = '成功' if r.get('ok') else ('没启动（预算）' if r.get('skipped') else '失败 %s' % (r.get('subtype') or ''))
        L.append('| %s | %s | $%.2f | %s | %s | %s |' % (
            r['step'], ','.join(r.get('models') or []) or r.get('model_req'), r.get('cost') or 0,
            '%d 分' % ((r.get('secs') or 0) // 60), r.get('tools', '-'), res))
    L += ['', '## 底稿核对（机械）', '', '- 顶层：%s' % rep.get('research_top', '-')]
    for d in rep.get('dives', []):
        L.append('- %s：%s' % (d.get('id'), d.get('research') or d.get('error') or '-'))
    L += ['', '## 往下钻', '']
    for d in rep.get('dive_requests', []):
        L.append('- **%s**（`%s`）：%s' % (d.get('title'), d.get('id'), d.get('why')))
    for n in rep.get('dive_notes', []):
        L.append('- 注：%s' % n)
    if not rep.get('dive_requests'):
        L.append('- 顶层讲解员没有申请往下钻')
    for d in rep.get('dives', []):
        L.append('- `%s` 下层内容文件：%s' % (d.get('id'), '有' if d.get('content') else '**没有**'))
    if 'build' in rep:
        b = rep['build']
        L += ['', '## 装配', '', '- build --strict 退出码 %d；WARN %d 条；未填槽位：%s' % (
            b['rc'], b['warn'], ' '.join(b['unfilled']) or '无')]
    if 'shots' in rep:
        s = rep['shots']
        L += ['- shot_scene --all：%s；FAIL %d 条' % ('；'.join(s['summary']) or '没有汇总行（退出码 %d）' % s['rc'], s['fail'])]
        L += ['  - %s' % l for l in s['fail_lines']]
    L += ['', '## 各 agent 的最终回复', '', '在 `logs/<步骤>.reply.md`（原文，未改）。']
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    sys.exit(main())
