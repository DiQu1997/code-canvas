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

const PORT = 8352;
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
            code: 'def f():\n    pass', layout: { col: 0, band: 0 } }],
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
  '--port', String(PORT), '--cli-bin', '/bin/echo'], { stdio: 'ignore' });
const base = `http://127.0.0.1:${PORT}`;
const alive = async () => {
  for (let i = 0; i < 30; i++) {
    try { const r = await fetch(`${base}/__alive`); if (r.ok) return true; } catch (e) {}
    await new Promise(r => setTimeout(r, 200));
  }
  return false;
};
if (!await alive()) { server.kill(); console.error('FAIL server did not start'); process.exit(1); }

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);

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
page.once('dialog', d => d.accept());
await page.click('#dive-btn');
await page.waitForTimeout(900);
const jobsAfterDive = await (await fetch(`${base}/jobs`)).json();
const dive = jobsAfterDive.jobs.find(j => j.name === 'pv-fix-dive1');
check('dive click spawns generate job with step ask',
  !!dive && dive.ask === '讲讲调度器怎么工作');
check('dive job reuses canvas source', !!dive && (dive.source || '').includes(root));

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

// 5b. stale canvas auto-rerender: html older than template gets refreshed on serve
const { utimesSync, statSync } = await import('fs');
const old = new Date(Date.now() - 86400e3);
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

await browser.close();
server.kill();
rmSync(hub, { recursive: true, force: true });
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
