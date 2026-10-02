// Trust layer: evidence levels (ev), gaps, objects, step trace — validate gates,
// check_refs pruning/downgrade, and how the renderer surfaces them.
// Usage: node tests/evidence.mjs
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync, spawnSync } from 'child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'canvas-ev-'));
const repo = join(tmp, 'repo');
mkdirSync(join(repo, 'pkg'), { recursive: true });
writeFileSync(join(repo, 'pkg', 'svc.py'), 'import os\n\ndef submit(job):\n    q.put(job)\n    return job.id\n');

const canvas = {
  meta: { title: 'ev fixture', mode: 'preview' },
  cards: [
    { id: 'api', kind: 'district', name: '接入层', role: '服务入口', oneliner: 'o', stat: 'pkg · 约 1 千行',
      highlights: ['submit'], file: 'pkg', layout: { col: 0, band: 0 } },
    { id: 'worker', kind: 'district', name: '后台执行', role: '核心机制', oneliner: 'o', stat: 'pkg · 约 1 千行',
      highlights: ['run'], file: 'pkg', layout: { col: 1, band: 0 },
      ev: 'unknown', need: '要看部署里 worker 的进程数配置' },
  ],
  wires: [{ id: 'r1', kind: 'route', route: 0, from: { card: 'api' }, to: { card: 'worker' }, ev: 'infer' }],
  regions: [], notes: [],
  gaps: [
    { kind: 'behavior', title: '提交即返回', text: '入队后立刻返回 id，不等执行', refs: [['pkg/svc.py', 4, 'q.put']] },
    { kind: 'unknown', title: '队列容量', text: '代码里没设上限', need: '看 broker 配置' },
    { kind: 'drift', title: 'README 说同步', text: 'README 写同步执行，实现是入队' },
  ],
  objects: [{ name: 'Job', where: '内存队列', created: 'submit()', rw: 'api 写 / worker 读', states: 'queued→done',
              refs: [['pkg/svc.py', 3, 'def submit']] }],
  steps: [
    { title: '总览', fit: true, wires: ['r1'] },
    { title: '① 提交一条任务', focus: ['api', 'worker'], wires: ['r1'], caption: 'c', ask: '追一次提交',
      trace: { trigger: '客户端调 submit', mode: 'async',
               data: [{ op: 'create', what: 'Job' }, { op: 'update', what: '队列' }],
               fail: '入队失败直接抛异常，无重试',
               refs: [['pkg/svc.py', 4, 'q.put'], ['pkg/svc.py', 2, 'no_such_thing']] } },
  ],
};
const cj = join(tmp, 'ev.json');
writeFileSync(cj, JSON.stringify(canvas));
const validate = p => spawnSync('python3', [resolve(root, 'validate.py'), p, '--no-exec'], { encoding: 'utf8' });
const v0 = validate(cj);
check('fixture with trust layers passes validate', v0.status === 0 && !v0.stdout.includes('ERROR'));

const variant = (name, mut) => {
  const c = JSON.parse(JSON.stringify(canvas)); mut(c);
  const p = join(tmp, name + '.json'); writeFileSync(p, JSON.stringify(c)); return validate(p).stdout;
};
check('ev=unknown without need is an ERROR', variant('v1', c => { delete c.cards[1].need; }).includes('ev=unknown 必须写 need'));
check('gap kind=unknown without need is an ERROR', variant('v2', c => { delete c.gaps[1].need; }).includes('kind=unknown 必须写 need'));
check('bad trace mode / data op are ERRORs', (() => {
  const out = variant('v3', c => { c.steps[1].trace.mode = 'later'; c.steps[1].trace.data[0].op = 'touch'; });
  return out.includes('trace.mode') && out.includes('trace.data');
})());
check('preview without gaps/objects/trace warns', (() => {
  const out = variant('v4', c => { delete c.gaps; delete c.objects; delete c.steps[1].trace; });
  return out.includes('缺 gaps') && out.includes('缺 objects') && out.includes('路线步缺 trace');
})());

// check_refs: prune what does not verify, keep what does, downgrade emptied facts
const cr = spawnSync('python3', [resolve(root, 'check_refs.py'), cj, repo], { encoding: 'utf8' });
const after = JSON.parse(readFileSync(cj, 'utf8'));
check('check_refs keeps verified refs and prunes the rest', cr.status === 0
  && cr.stdout.includes('通过 3，剔除 1') && cr.stdout.includes('no_such_thing')
  && after.steps[1].trace.refs.length === 1 && after.gaps[0].refs.length === 1);
const allBad = JSON.parse(JSON.stringify(after));
allBad.steps[1].trace.refs = [['pkg/missing.py', 1, 'x'], ['pkg/svc.py', 99, 'q.put']];
writeFileSync(join(tmp, 'allbad.json'), JSON.stringify(allBad));
const cr2 = spawnSync('python3', [resolve(root, 'check_refs.py'), join(tmp, 'allbad.json'), repo], { encoding: 'utf8' });
const ab = JSON.parse(readFileSync(join(tmp, 'allbad.json'), 'utf8'));
check('fact trace with no surviving refs is downgraded to infer', ab.steps[1].trace.ev === 'infer'
  && cr2.stdout.includes('文件不存在') && cr2.stdout.includes('行号越界'));

// renderer
const html = join(tmp, 'ev.html');
execFileSync('python3', [resolve(root, 'render.py'), cj, html]);
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto('file://' + html);
await page.waitForTimeout(700);
check('unknown district carries 待核实 badge with need tooltip',
  await page.$eval('#card-worker .evb.unknown', el => el.title.includes('进程数配置'))
  && await page.$eval('#card-worker', el => el.classList.contains('ev-unknown')));
check('inferred route drawn dashed', await page.$('svg path.wire.route.ev-infer') !== null);
check('legend explains evidence styles', (await page.textContent('.legend')).includes('推断')
  && (await page.textContent('.legend')).includes('待核实'));
check('preview map gets an 架构总览 board button with unknown count',
  (await page.textContent('#ov-btn')).includes('架构总览') && (await page.textContent('#ov-btn')).includes('待核实 1'));
check('board does not auto-open for preview maps', await page.isHidden('#overview'));
await page.click('#ov-btn'); await page.waitForTimeout(200);
const board = await page.textContent('#ov-body');
check('board shows objects table and three gap groups',
  (await page.$$('.ov-objs tbody tr')).length === 1 && board.includes('值得注意的实际行为')
  && board.includes('尚未确认') && board.includes('文档与实现不一致') && board.includes('需要：看 broker 配置')
  && board.includes('pkg/svc.py:4 q.put'));
await page.keyboard.press('Escape');
await page.click('#next'); await page.waitForTimeout(400);
check('route step HUD shows async / evidence / ref-count badges',
  (await page.textContent('#sbadges')).includes('异步') && (await page.textContent('#sbadges')).includes('已确认')
  && (await page.textContent('#sbadges')).includes('依据 1 处'));
await page.click('#sdetail-btn'); await page.waitForTimeout(200);
const det = await page.textContent('#sdetail');
check('step trace card lists trigger, CRUD, failure branch and refs',
  det.includes('客户端调 submit') && det.includes('创建Job') && det.includes('入队失败直接抛异常')
  && det.includes('pkg/svc.py:4 q.put'));
await browser.close();
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
