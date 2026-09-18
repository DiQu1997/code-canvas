// End-to-end test for hub mode: library page, per-canvas QA routing with
// separate sidecars, generation job lifecycle, and path sanitization.
// Run against a live serve.py --hub backed by a stub CLI (/bin/echo).
// Usage: node tests/hub.mjs
// Env: CANVAS_TEST_PW (playwright pkg path if not resolvable), CANVAS_TEST_CHROMIUM.
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { spawn, execFileSync } from 'child_process';
import { mkdtempSync, mkdirSync, copyFileSync, existsSync, readFileSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

// 端口随机化：崩溃泄漏的旧 server 若还占着固定端口，新 run 会静默绑定失败、
// 对着挂旧目录的僵尸服务器测试（实案）。随 pid 挑端口根治这一类。
const PORT = 18400 + (process.pid % 1000) * 2;
const procs = [];                          // 本 run 拉起的所有 server，崩溃也要收尸
const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
process.on('uncaughtException', err => {   // 崩溃也要吐出已收集的结果，别哑死
  console.log(results.join('\n'));
  console.error('CRASH:', err.message.split('\n')[0]);
  for (const pr of procs) { try { pr.kill(); } catch (e) {} }
  process.exit(1);
});
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const hub = mkdtempSync(join(tmpdir(), 'canvas-hub-'));
for (const f of ['nano-vllm.html', 'nano-vllm.json', 'cache-diff.html', 'cache-diff.json'])
  copyFileSync(join(root, 'demo', f), join(hub, f));
mkdirSync(join(hub, 'examples'));
for (const f of ['cache-demo.html', 'cache-demo.json'])
  copyFileSync(join(root, 'demo', f), join(hub, 'examples', f));
// dive-order fixture: a mini preview canvas whose step 1 carries an ask,
// plus a src sidecar pointing at a real dir so /generate accepts the reuse
writeFileSync(join(hub, 'pv-fix.json'), JSON.stringify({
  meta: { title: 'pv fixture', mode: 'preview' },
  cards: [{ id: 'a', name: 'A', file: 'x.py', lang: 'py',
            code: 'def f():\n    pass', layout: { col: 0, band: 0 } },
          { id: 'sched', kind: 'district', name: '调度与批组装', role: '核心机制',
            oneliner: '每步挑谁进批', stat: 'core/sched · 约 4 千行',
            file: 'core/sched', layout: { col: 1, band: 0 } }],
  wires: [], regions: [], notes: [],
  steps: [{ title: '总览', fit: true },
          { title: '① 调度线', caption: 'p', focus: ['a'], ask: '讲讲调度器怎么工作' }],
}));
execFileSync('python3', [resolve(root, 'render.py'), join(hub, 'pv-fix.json'), join(hub, 'pv-fix.html')]);
writeFileSync(join(hub, 'pv-fix.src.json'), JSON.stringify({ repo: root }));
writeFileSync(join(hub, 'cache-diff.src.json'), JSON.stringify({ repo: root }));
// interrupted job fixture: pid long dead, no status file → must NOT show "running";
// its result.json is a stream-json NDJSON: monitor reads turns/actions, metrics read tail result event
mkdirSync(join(hub, '.jobs'));
mkdirSync(join(hub, '.jobs', 'j00000000-000000'));
writeFileSync(join(hub, '.jobs', 'j00000000-000000.meta.json'), JSON.stringify({
  id: 'j00000000-000000', name: 'zombie', ask: 'x', source: 'https://example.com/x.git',
  pid: 999999999, started: '2026-01-01T00:00:00',
}));
writeFileSync(join(hub, '.jobs', 'j00000000-000000.result.json'), [
  JSON.stringify({ type: 'system', subtype: 'init' }),
  JSON.stringify({ type: 'assistant', message: { content: [{ type: 'text', text: '开始' }] } }),
  JSON.stringify({ type: 'assistant', message: { content: [
    { type: 'tool_use', name: 'Bash', input: { command: 'python3 validate.py canvas.json' } }] } }),
  JSON.stringify({ type: 'result', total_cost_usd: 0.5, duration_ms: 60000, num_turns: 2,
    usage: { input_tokens: 100, output_tokens: 200, cache_read_input_tokens: 300 } }),
].join('\n'));

const server = spawn('python3', [resolve(root, 'serve.py'), '--hub', hub,
  '--port', String(PORT), '--cli-bin', '/bin/echo', '--codex-bin', '/bin/echo'],
  { stdio: 'ignore' });
procs.push(server);
const base = `http://127.0.0.1:${PORT}`;
const alive = async () => {
  for (let i = 0; i < 30; i++) {
    try { const r = await fetch(`${base}/__alive`); if (r.ok) return true; } catch (e) {}
    await new Promise(r => setTimeout(r, 200));
  }
  return false;
};
if (!await alive()) { server.kill(); console.error('FAIL server did not start'); process.exit(1); }

// 1. front page: generation form first, then library, then examples section
const listHtml = await (await fetch(`${base}/`)).text();
check('front page has generation form',
  listHtml.includes('生成画布') && listHtml.includes('git 地址') && listHtml.includes('粘贴代码'));
check('front page offers preview canvas type', listHtml.includes('预览地图'));
check('library lists canvases',
  listHtml.includes('/c/nano-vllm/') && listHtml.includes('/c/cache-diff/'));
check('examples section separated',
  listHtml.includes('示例') && listHtml.includes('/c/cache-demo/'));
check('example canvas served via /c/', (await fetch(`${base}/c/cache-demo/`)).ok);

// 2. canvas page loads and its QA goes live via relative __alive
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.goto(`${base}/c/nano-vllm/`);
await page.waitForTimeout(900);
check('canvas page renders cards', await page.$$eval('.card', c => c.length > 3));
check('home link visible under hub', await page.isVisible('#home-link'));
check('download button visible under hub', await page.isVisible('#dl-btn'));
const dl = await fetch(`${base}/c/nano-vllm/download`);
check('download serves attachment',
  dl.status === 200 &&
  (dl.headers.get('content-disposition') || '').includes('attachment') &&
  (dl.headers.get('content-disposition') || '').includes('nano-vllm.html') &&
  (await dl.text()).includes('CANVAS_DATA') === false);  // 已注入数据的成品，非空模板
check('relative __alive resolves per-canvas', await page.evaluate(
  () => fetch('__alive').then(r => r.json()).then(j => j.ok === true && j.html === 'nano-vllm.html').catch(() => false)));

// 2b. block sandbox live run: endpoint + in-page ▶ round-trip
const runRes = await (await fetch(`${base}/c/nano-vllm/run`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code: 'print(6*7)' }),
})).json();
check('run endpoint executes snippet', runRes.ok === true && runRes.out.trim() === '42');
const runBad = await (await fetch(`${base}/c/nano-vllm/run`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code: 'x', lang: 'rust' }),
})).json();
check('run endpoint rejects non-python', runBad.ok === false);
await page.$eval('#card-allocate', el => el.classList.remove('collapsed'));
await page.click('#card-allocate .bbtn[data-act="run"]');
await page.waitForTimeout(300);
check('live run button visible under hub', await page.isVisible('#card-allocate .rn-go'));
await page.$eval('#card-allocate .rn-input', el => {
  el.value = 'tokens = [1, 2, 3, 4]';
  el.dispatchEvent(new Event('input'));
});
await page.click('#card-allocate .rn-go');
await page.waitForTimeout(1500);
check('edited input runs live and reports timing',
  (await page.textContent('#card-allocate .rn-badge')).includes('实跑')
  && (await page.textContent('#card-allocate .rn-out')).includes('命中前缀块数: 1'));

// 3. /c/<name> without slash redirects so relative paths resolve
const r301 = await fetch(`${base}/c/nano-vllm`, { redirect: 'manual' });
check('bare canvas path redirects to slash', r301.status === 301);

// 4. per-canvas ask lands in per-canvas sidecar
const askRes = await (await fetch(`${base}/c/cache-diff/ask`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ prompt: 'hub-test-prompt', card: 'x', block: 'y', question: 'q' }),
})).json();
check('ask round-trips via stub', askRes.ok === true && askRes.answer.includes('hub-test-prompt'));
check('sidecar written next to right canvas',
  existsSync(join(hub, 'cache-diff.html.qa.json')) && !existsSync(join(hub, 'nano-vllm.html.qa.json')));

// 4b. canvas-level QA on a BLOCKLESS canvas (cache-diff has no blocks —
// previously it had no ask entry at all)
await page.goto(`${base}/c/cache-diff/`);
await page.waitForTimeout(900);
check('canvas-ask button present', await page.isVisible('#canvas-ask-btn'));
await page.click('#canvas-ask-btn');
check('canvas-ask opens drawer in canvas mode',
  await page.isVisible('#qa.open') &&
  (await page.textContent('#qa-name')).includes('整张画布'));
await page.fill('#qa-input', '这个 PR 治什么病？');
await page.click('#qa-send');
await page.waitForTimeout(1200);
const drawerText = await page.textContent('#qa-log');
check('canvas ask answers via stub', drawerText.includes('这个 PR 治什么病'));
const sidecar2 = JSON.parse(readFileSync(join(hub, 'cache-diff.html.qa.json'), 'utf8'));
const canvasRec = sidecar2.find(r => r.card === '__canvas__');
check('canvas ask persisted with __canvas__ marker', !!canvasRec);
check('canvas prompt carries curated context',
  canvasRec.question === '这个 PR 治什么病？');

// 4c. route step「→ 深潜这条」one-click dive order
await page.goto(`${base}/c/pv-fix/`);
await page.waitForTimeout(900);
check('dive btn hidden on step without ask', await page.isHidden('#dive-btn'));
await page.click('#next');
await page.waitForTimeout(400);
check('dive btn shows on ask step under hub', await page.isVisible('#dive-btn'));
await page.click('#dive-btn');
await page.waitForTimeout(300);
if (!(await page.isVisible('#ordbox'))) throw new Error('ordbox not shown for dive');
await page.click('#ob-go');
await page.waitForTimeout(900);
const jobsAfterDive = await (await fetch(`${base}/jobs`)).json();
const dive = jobsAfterDive.jobs.find(j => j.name === 'pv-fix-dive1');
check('dive click spawns generate job with step ask',
  !!dive && dive.ask === '讲讲调度器怎么工作');
check('dive job reuses canvas source', !!dive && (dive.source || '').includes(root));

// 4d. district-card ordering: per-module buttons → sub-preview or code deep dive
check('district action buttons visible under hub', await page.isVisible('.dact .dact-pv'));
await page.click('.dact .dact-pv');
await page.waitForTimeout(300);
check('order dialog offers engine choice', await page.isVisible('#ordbox')
  && (await page.textContent('#ordbox')).includes('Codex'));
await page.click('#ob-go');
await page.waitForTimeout(900);
const jobsPv = await (await fetch(`${base}/jobs`)).json();
const subPv = jobsPv.jobs.find(j => j.name === 'pv-fix-sched-map');
check('module sub-preview ordered with scope in ask', !!subPv && subPv.mode === 'preview'
  && subPv.ask.includes('调度与批组装') && subPv.ask.includes('core/sched'));
await page.click('.dact .dact-dive');
await page.waitForTimeout(300);
await page.check('#ordbox input[value="codex"]');
await page.fill('#ob-model', 'gpt-6-astra');
await page.click('#ob-go');
await page.waitForTimeout(900);
const jobsDv = await (await fetch(`${base}/jobs`)).json();
const subDv = jobsDv.jobs.find(j => j.name === 'pv-fix-sched');
check('module code-dive ordered as deep canvas', !!subDv && subDv.mode === 'deep'
  && subDv.ask.includes('深潜细讲'));
check('order dialog engine+model reach the job', !!subDv
  && subDv.engine === 'codex' && subDv.model === 'gpt-6-astra');
// 引擎选择被记住：再开一次面板应默认 codex
await page.click('.dact .dact-pv');
await page.waitForTimeout(300);
check('engine choice remembered across orders', await page.$eval(
  '#ordbox input[value="codex"]', el => el.checked));
await page.click('#ob-cancel');

// 5. generate: three sources. code mode runs to done (stub exits instantly)
const gen = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: '测试生成', name: 'gen-test', code: 'def f():\n    return 1' }),
})).json();
check('generate accepts pasted code', gen.ok === true && gen.job.name === 'gen-test');
let done = false;
for (let i = 0; i < 25; i++) {
  const js = await (await fetch(`${base}/jobs`)).json();
  const j = js.jobs.find(j => j.id === gen.job.id);
  if (j && j.status !== 'running') { done = j.status === 'done'; break; }
  await new Promise(r => setTimeout(r, 200));
}
check('job completes with status done', done);
const zombie = (await (await fetch(`${base}/jobs`)).json()).jobs.find(j => j.name === 'zombie');
check('dead-pid job reported interrupted, not running', !!zombie && zombie.status === 'failed(中断)');
check('metrics parsed from NDJSON tail result event',
  !!zombie && zombie.metrics && zombie.metrics.cost_usd === 0.5 && zombie.metrics.in_tok === 100);

// 5a. job monitor: stages from workdir files, agent feed from stream-json
const mon = await (await fetch(`${base}/jobs/j00000000-000000/monitor`)).json();
check('monitor reports stages', mon.ok === true &&
  mon.stages.some(s => s.t === '克隆仓库') && mon.stages.every(s => s.done === false));
check('monitor reads agent feed from stream',
  mon.turns === 2 && mon.actions.some(a => a.includes('validate.py')));
const monGen = await (await fetch(`${base}/jobs/${gen.job.id}/monitor`)).json();
check('monitor works for stub job (no clone stage)', monGen.ok === true &&
  !monGen.stages.some(s => s.t === '克隆仓库'));
check('monitor 404s unknown job', (await fetch(`${base}/jobs/j99999999-999999/monitor`)).status === 404);
// UI: clicking a job row expands the live detail panel
await page.goto(`${base}/`);
await page.waitForTimeout(600);
await page.click('.job[data-jid="j00000000-000000"]');
await page.waitForTimeout(600);
check('job row click opens monitor panel', await page.isVisible('#jd-j00000000-000000') &&
  (await page.textContent('#jd-j00000000-000000')).includes('克隆仓库') &&
  (await page.textContent('#jd-j00000000-000000')).includes('第 2 轮'));
check('pasted code landed in workdir',
  readFileSync(join(hub, '.jobs', gen.job.id, 'src', 'pasted.txt'), 'utf8').includes('def f()'));
check('prompt follows SKILL pipeline',
  readFileSync(join(hub, '.jobs', `${gen.job.id}.prompt`), 'utf8').includes('SKILL.md'));
check('job timing recorded', existsSync(join(hub, '.jobs', `${gen.job.id}.timing.json`)) &&
  JSON.parse(readFileSync(join(hub, '.jobs', `${gen.job.id}.timing.json`), 'utf8')).claude_s >= 0);

// source validation
const noSrc = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x' }),
})).json();
check('generate rejects missing source', noSrc.ok === false);
const badGit = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', git_url: 'file:///etc/passwd' }),
})).json();
check('generate rejects non-http git_url', badGit.ok === false);
const badRepo = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', repo: '/no/such/dir' }),
})).json();
check('generate rejects missing box path', badRepo.ok === false);
const gitName = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', git_url: 'https://127.0.0.1:1/none/myrepo.git' }),
})).json();
check('git_url derives canvas name', gitName.ok === true && gitName.job.name === 'myrepo');

// preview jobs: -map naming, preview-spec prompt, src sidecar; pasted code rejected
const pv = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: '第一次接触', git_url: 'https://127.0.0.1:1/none/prevrepo.git', preview: true }),
})).json();
check('preview job named -map with mode', pv.ok === true &&
  pv.job.name === 'prevrepo-map' && pv.job.mode === 'preview');
check('preview prompt follows preview-spec',
  readFileSync(join(hub, '.jobs', `${pv.job.id}.prompt`), 'utf8').includes('preview-spec.md'));
check('generate records src sidecar',
  JSON.parse(readFileSync(join(hub, 'prevrepo-map.src.json'), 'utf8')).git_url.includes('prevrepo'));
const pvCode = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', code: 'y = 1', preview: true }),
})).json();
check('preview rejects pasted code', pvCode.ok === false);

// codex engine: alternate generator CLI, per-job model, subscription-side auth
check('front page offers codex engine', listHtml.includes('Codex') && listHtml.includes('g-model'));
const cx = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'codex 引擎测试', repo: root, name: 'cxjob',
                         engine: 'codex', model: 'gpt-5.6-sol' }),
})).json();
check('codex job accepted with engine+model recorded', cx.ok === true
  && cx.job.engine === 'codex' && cx.job.model === 'gpt-5.6-sol');
let cxDone = false;
for (let i = 0; i < 40; i++) {
  const js = await (await fetch(`${base}/jobs`)).json();
  const j = js.jobs.find(x => x.id === cx.job.id);
  if (j && j.status !== 'running') { cxDone = j.status === 'done'; break; }
  await new Promise(r => setTimeout(r, 250));
}
check('codex job completes via codex binary', cxDone
  && readFileSync(join(hub, '.jobs', `${cx.job.id}.result.json`), 'utf8').includes('exec'));
const badEng = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', repo: root, engine: 'gemini' }),
})).json();
check('unknown engine rejected', badEng.ok === false);
const badModel = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', repo: root, engine: 'codex', model: 'a b;rm' }),
})).json();
check('model id sanitized', badModel.ok === false);

// 5b. stale canvas auto-rerender: html older than template gets refreshed on serve
const { utimesSync, statSync } = await import('fs');
const old = new Date('2000-01-01');  // 必须早于模板 mtime，回拨一天不够（模板不是每天都改）
utimesSync(join(hub, 'nano-vllm.html'), old, old);
await fetch(`${base}/c/nano-vllm/`);
check('stale html re-rendered against current template',
  statSync(join(hub, 'nano-vllm.html')).mtimeMs > old.getTime() + 1000);

// 5c. delete: library canvas removable (html+json+qa), examples protected
const delRes = await (await fetch(`${base}/c/cache-diff`, { method: 'DELETE' })).json();
check('delete removes library canvas', delRes.ok === true &&
  !existsSync(join(hub, 'cache-diff.html')) && !existsSync(join(hub, 'cache-diff.json')) &&
  !existsSync(join(hub, 'cache-diff.src.json')) && !existsSync(join(hub, 'cache-diff.html.qa.json')));
check('deleted canvas 404s', (await fetch(`${base}/c/cache-diff/`)).status === 404);
const delEx = await (await fetch(`${base}/c/cache-demo`, { method: 'DELETE' })).json();
check('examples protected from delete', delEx.ok === false &&
  existsSync(join(hub, 'examples', 'cache-demo.html')));
const listAfter = await (await fetch(`${base}/`)).text();
check('delete button only on library cards',
  listAfter.includes('data-name="nano-vllm"') && !listAfter.includes('data-name="cache-demo"'));

// 6. sanitization: traversal names rejected
const evil = await fetch(`${base}/c/..%2F..%2Fetc/`);
check('traversal name 404s', evil.status === 404);
const evilGen = await (await fetch(`${base}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', name: '../evil' }),
})).json();
check('generate rejects bad name', evilGen.ok === false);

// 7. QA canvas-patch: agent answer carries ops → show passthrough, validate-gated
// persist, rejection on bogus refs, undo restores. Second server with a fake CLI.
const shq = s => "'" + s.replace(/'/g, "'\\''") + "'";
const goodEnv = JSON.stringify({
  result: '看图。\n```canvas-patch\n' + JSON.stringify({ ops: [
    { op: 'show', focus: ['a'] },
    { op: 'add_note', note: { tag: '问答', text: 'f 恒返回 1', anchor: { card: 'a', line: 2 } } },
    { op: 'set_layout', card: 'a', layout: { col: 1, band: 0 } },
  ] }) + '\n```',
  total_cost_usd: 0.01, duration_ms: 100, usage: { input_tokens: 1, output_tokens: 2 },
});
const badEnv = JSON.stringify({
  result: '答。\n```canvas-patch\n' + JSON.stringify({ ops: [
    { op: 'add_note', note: { tag: 'x', text: 'y', anchor: { card: 'ghost', line: 999 } } },
  ] }) + '\n```',
  total_cost_usd: 0, duration_ms: 1, usage: {},
});
const ppCanvas = JSON.stringify({
  meta: { title: 'pp' },
  cards: [{ id: 'a', name: 'f', file: 'x.py:2', lang: 'py',
            code: 'def f():\n    return 1', layout: { col: 0, band: 0 } }],
  wires: [], notes: [], steps: [{ title: 't', fit: true }],
});
writeFileSync(join(hub, 'fakepatch.sh'),
  `#!/bin/bash
case "$*" in
  *pp-test*) printf '%s' ${shq(ppCanvas)} > ${shq(join(hub, 'pp-test.json'))}; echo dummy > ${shq(join(hub, 'pp-test.html'))}; echo done;;
  *BADPATCH*) printf '%s' ${shq(badEnv)};;
  *) printf '%s' ${shq(goodEnv)};;
esac\n`,
  { mode: 0o755 });
const PORT2 = PORT + 1;
const server2 = spawn('python3', [resolve(root, 'serve.py'), '--hub', hub,
  '--port', String(PORT2), '--cli-bin', join(hub, 'fakepatch.sh')], { stdio: 'ignore' });
procs.push(server2);
const base2 = `http://127.0.0.1:${PORT2}`;
for (let i = 0; i < 30; i++) {
  try { if ((await fetch(`${base2}/__alive`)).ok) break; } catch (e) {}
  await new Promise(r => setTimeout(r, 200));
}
const pj = () => JSON.parse(readFileSync(join(hub, 'pv-fix.json'), 'utf8'));
const pa = await (await fetch(`${base2}/c/pv-fix/ask`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ prompt: 'x', card: 'a', block: '-', question: '画出来' }),
})).json();
check('patch answer stripped of fence, show passed through',
  pa.ok === true && !pa.answer.includes('canvas-patch') && pa.show && pa.show.focus[0] === 'a');
check('persist ops applied behind validate gate', pa.patched && pa.patched.n === 2);
const dj = pj();
check('qa note landed tagged in canvas JSON',
  dj.notes.some(n => n.qa === pa.patched.patch_id && n.text === 'f 恒返回 1'));
check('set_layout moved the card', dj.cards.find(c => c.id === 'a').layout.col === 1);
const pb = await (await fetch(`${base2}/c/pv-fix/ask`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ prompt: 'BADPATCH', card: 'a', block: '-', question: 'q' }),
})).json();
check('bogus patch rejected, answer still delivered',
  pb.ok === true && !!pb.patch_error && !pj().notes.some(n => n.text === 'y'));
const un = await (await fetch(`${base2}/c/pv-fix/unpatch`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ patch_id: pa.patched.patch_id }),
})).json();
const dj2 = pj();
check('undo removes qa elements and restores layout', un.ok === true
  && !dj2.notes.some(n => n.qa === pa.patched.patch_id)
  && dj2.cards.find(c => c.id === 'a').layout.col === 0);
// 8. service post-pass: agent "forgets" to embed context → server does it
// mechanically after the job (embed_context + re-render), before status lands.
mkdirSync(join(hub, 'pp-repo'));
writeFileSync(join(hub, 'pp-repo', 'x.py'), 'x=0\ndef f():\n    return 1\n');
const ppGen = await (await fetch(`${base2}/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ask: 'x', repo: join(hub, 'pp-repo'), name: 'pp-test' }),
})).json();
check('post-pass job accepted', ppGen.ok === true);
let ppDone = false;
for (let i = 0; i < 40; i++) {
  const js = await (await fetch(`${base2}/jobs`)).json();
  const j = js.jobs.find(x => x.id === ppGen.job.id);
  if (j && j.status !== 'running') { ppDone = j.status === 'done'; break; }
  await new Promise(r => setTimeout(r, 250));
}
check('post-pass job done', ppDone);
const ppd = JSON.parse(readFileSync(join(hub, 'pp-test.json'), 'utf8'));
check('server embedded context mechanically (agent skipped it)',
  ppd.files && (ppd.files['x.py'] || '').includes('def f()'));
check('server re-rendered html after embed',
  readFileSync(join(hub, 'pp-test.html'), 'utf8').includes('ctx-bar'));
server2.kill();

await browser.close();
server.kill();
rmSync(hub, { recursive: true, force: true });
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
