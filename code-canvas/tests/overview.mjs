// Algorithm overview board: auto-open once, flow links jump into code, vars marked
// in card code, validate/merge gates. Usage: node tests/overview.mjs
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync, spawnSync } from 'child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'canvas-ov-'));

const canvas = {
  meta: { title: 'ov fixture' },
  cards: [
    { id: 'sched', name: 'schedule()', file: 's.py:1', lang: 'py',
      code: 'def schedule(self):\n    token_budget = self.max\n    for req in self.running:\n        n = min(req.need, token_budget)\n        token_budget -= n\n    return token_budget',
      blocks: [{ name: '预算', lines: [2, 2], summary: 's' }, { name: '循环', lines: [3, 5], summary: 'l' }],
      layout: { col: 0, band: 0 } },
    { id: 'pre', name: 'preempt()', file: 'p.py:1', lang: 'py', code: 'def preempt(self):\n    pass',
      layout: { col: 1, band: 0 } },
  ],
  wires: [{ id: 'w1', kind: 'call', from: { card: 'sched', line: 4 }, to: { card: 'pre' } }],
  regions: [], notes: [],
  steps: [{ title: '总览', fit: true }, { title: '① 预算', focus: ['sched'], lines: [['sched', 2]] }],
  overview: {
    problem: '一步调度分配 token 预算。', idea: '贪心：按 running 顺序扣预算。',
    flow: [
      { text: '设预算', card: 'sched', block: '预算' },
      { text: '逐条扣', card: 'sched', block: '循环' },
      { text: '不够就抢占', card: 'pre' },
      { text: '看第一步', step: 1 },
      { text: '返回剩余', card: 'sched' },
    ],
    vars: [
      { name: 'token_budget', meaning: '本步还能排的 token 数', rw: 'schedule 写，循环读' },
      { name: 'n', meaning: '本条分到的 token', rw: '循环写' },
      { name: 'req', meaning: '当前请求', rw: '循环读' },
      { name: 'self.running', meaning: '在跑队列', rw: '外部写' },
    ],
    example: '| 轮 | budget |\n|---|---|\n| 0 | 10 |\n| 1 | 7 |',
    pitfalls: ['预算为 0 时循环仍会跑一遍', '抢占后不重算'],
  },
};
const cj = join(tmp, 'ov.json');
writeFileSync(cj, JSON.stringify(canvas));
const validate = p => spawnSync('python3', [resolve(root, 'validate.py'), p, '--no-exec'], { encoding: 'utf8' });
const v0 = validate(cj);
check('fixture overview passes validate', v0.status === 0 && !v0.stdout.includes('ERROR'));

// validate gates: fabricated var name / dangling flow link
const bad1 = JSON.parse(JSON.stringify(canvas)); bad1.overview.vars[0].name = 'ghost_var';
writeFileSync(join(tmp, 'bad1.json'), JSON.stringify(bad1));
check('validate rejects var absent from code', validate(join(tmp, 'bad1.json')).stdout.includes('ghost_var'));
const bad2 = JSON.parse(JSON.stringify(canvas)); bad2.overview.flow[1].block = '不存在的块';
writeFileSync(join(tmp, 'bad2.json'), JSON.stringify(bad2));
check('validate rejects flow link to missing block', validate(join(tmp, 'bad2.json')).stdout.includes('不存在的块'));
// a var that lives only in the embedded file context (not on a card) is still real
const inFiles = JSON.parse(JSON.stringify(canvas));
inFiles.files = { 's.py': 'def schedule(self):\n    is_prefill_chunk = True\n' };
inFiles.overview.vars.push({ name: 'is_prefill_chunk', meaning: '还在 prefill 中', rw: 'update 写' });
writeFileSync(join(tmp, 'infiles.json'), JSON.stringify(inFiles));
check('validate accepts var present only in files context', !validate(join(tmp, 'infiles.json')).stdout.includes('ERROR'));
const noOv = JSON.parse(JSON.stringify(canvas)); delete noOv.overview;
writeFileSync(join(tmp, 'noov.json'), JSON.stringify(noOv));
check('validate warns deep canvas without overview', validate(join(tmp, 'noov.json')).stdout.includes('缺 overview'));

// merge_overview.py: good merge lands, bad one leaves the canvas untouched
writeFileSync(join(tmp, 'good-ov.json'), JSON.stringify(canvas.overview));
const m1 = spawnSync('python3', [resolve(root, 'merge_overview.py'), join(tmp, 'noov.json'), join(tmp, 'good-ov.json')], { encoding: 'utf8' });
check('merge_overview merges a valid overview', m1.status === 0
  && JSON.parse(readFileSync(join(tmp, 'noov.json'), 'utf8')).overview.flow.length === 5);
writeFileSync(join(tmp, 'noov2.json'), JSON.stringify(noOv));
writeFileSync(join(tmp, 'bad-ov.json'), JSON.stringify(bad1.overview));
const m2 = spawnSync('python3', [resolve(root, 'merge_overview.py'), join(tmp, 'noov2.json'), join(tmp, 'bad-ov.json')], { encoding: 'utf8' });
check('merge_overview prunes fabricated vars and merges the rest', m2.status === 0
  && m2.stdout.includes('ghost_var')
  && JSON.parse(readFileSync(join(tmp, 'noov2.json'), 'utf8')).overview.vars.length === 3);
writeFileSync(join(tmp, 'noov3.json'), JSON.stringify(noOv));
writeFileSync(join(tmp, 'bad-link.json'), JSON.stringify(bad2.overview));
const m3 = spawnSync('python3', [resolve(root, 'merge_overview.py'), join(tmp, 'noov3.json'), join(tmp, 'bad-link.json')], { encoding: 'utf8' });
check('merge_overview refuses dangling flow links', m3.status === 2
  && !('overview' in JSON.parse(readFileSync(join(tmp, 'noov3.json'), 'utf8'))));

// render + browser behaviour
const html = join(tmp, 'ov.html');
execFileSync('python3', [resolve(root, 'render.py'), cj, html]);
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto('file://' + html);
await page.waitForTimeout(700);
check('overview board auto-opens on first visit', await page.isVisible('#overview'));
check('board lists flow with links and var table',
  (await page.$$('.ov-flow li')).length === 5 && (await page.$$('.ov-link')).length === 5
  && (await page.$$('.ov-vars tbody tr')).length === 4
  && (await page.textContent('.ov-ex')).includes('budget'));
await page.$$eval('.ov-link', els => els[1].click());   // → sched · 循环
await page.waitForTimeout(500);
check('flow link closes board, unfolds the block, zooms legibly',
  await page.isHidden('#overview')
  && await page.evaluate(() => ![...document.querySelectorAll('#card-sched .blk')]
       .find(b => b.querySelector('.bname').textContent === '循环').classList.contains('folded'))
  && await page.evaluate(() => cam.s) >= .85);
check('HUD button offers the board afterwards', await page.isVisible('#ov-btn')
  && (await page.textContent('#ov-btn')) === '算法总览');
const vrefs = await page.$$eval('.vref', els => els.map(e => [e.dataset.v, e.title]));
check('vars are marked in card code with meaning tooltips',
  vrefs.filter(v => v[0] === 'token_budget').length === 4
  && vrefs.some(v => v[0] === 'self.running')
  && vrefs.every(v => v[1].includes(' — ')));
check('identifier inside another word is not marked',   // n in min()/need must not match
  vrefs.filter(v => v[0] === 'n').length === 2);
await page.$eval('.vref[data-v="token_budget"]', el => el.click());
await page.waitForTimeout(200);
check('clicking a var opens the board at its row',
  await page.isVisible('#overview') && await page.$eval('#ov-var-token_budget', el => el.classList.contains('hl')));
await page.keyboard.press('Escape');
check('Esc closes the board', await page.isHidden('#overview'));
await page.reload(); await page.waitForTimeout(600);
check('board does not auto-open again once seen', await page.isHidden('#overview'));
await page.$$eval('.ov-link', () => 0);
await page.click('#ov-btn'); await page.waitForTimeout(150);
await page.$$eval('.ov-link', els => els[3].click());   // → step 1
await page.waitForTimeout(400);
check('flow link to a step walks the storyline', await page.evaluate(() => step) === 1);
await browser.close();
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
