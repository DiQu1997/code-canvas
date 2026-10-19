// Scenario walkthroughs (scenarios) + black boxes (boxes): validate gates, check_refs on box refs,
// desktop tick-by-tick stepping, call-site box marks, overview entry points, phone stream.
// Usage: node tests/scenario.mjs
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync, spawnSync } from 'child_process';
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'canvas-scn-'));

const sched = [
  'def schedule(budget):',
  '    for req in running:',
  '        n = need(req)',
  '        if n == 0:',
  '            continue',
  '        blocks = allocate(req, n)',
  '        if blocks is None:',
  '            preempt(req)',
  '        budget -= n',
  '    return budget',
].join('\n');
const preempt = ['def preempt(req):', '    free(req.blocks)', '    req.computed = 0', '    waiting.appendleft(req)'].join('\n');
const canvas = {
  meta: { title: 'scenario fixture' },
  cards: [
    { id: 'sched', name: 'schedule()', file: 's.py:1', lang: 'py', code: sched, layout: { col: 0, band: 0 } },
    { id: 'pre', name: 'preempt()', file: 's.py:20', lang: 'py', code: preempt, layout: { col: 1, band: 0 } },
  ],
  wires: [{ id: 'w1', kind: 'call', from: { card: 'sched', line: 8 }, to: { card: 'pre' } }],
  regions: [], notes: [],
  steps: [{ title: '总览', fit: true }, { title: '① 排', focus: ['sched'], caption: 'c1' }],
  boxes: [
    { name: 'need', in: 'req', out: '还差几个 token', effect: '只读', why: '算术', refs: [['s.py', 3, 'need']] },
    { name: 'allocate', in: 'req、n', out: '块或 None', effect: '占块', why: '块池另讲', refs: [['s.py', 6, 'allocate'], ['s.py', 99, 'nope']] },
    { name: 'free', in: 'blocks', out: '无', effect: '还块', why: '块池另讲' },
  ],
  scenarios: [
    { id: 'pre', title: '块不够：抢占', goal: '走 allocate 失败 → preempt 的分支', setup: '预算 **16**；空闲 0 块；running=[A]',
      init: { budget: '16', running: '[A]' },
      ticks: [
        { card: 'sched', lines: [2, 3], text: 'A 差 1 个 token', vals: { n: '1' } },
        { card: 'sched', lines: [4, 5], text: 'n 不为 0', branch: 'n == 0 不成立，不进 continue' },
        { card: 'sched', lines: [6, 7], text: '没有空闲块', vals: { blocks: 'None' }, branch: 'blocks is None 成立' },
        { card: 'pre', lines: [2, 3], text: '还块、清进度', vals: { 'req.computed': '22 → 0' } },
        { card: 'sched', lines: [9, 9], text: '扣预算', vals: { budget: '16 → 15' } },
      ],
      result: 'A 回到 waiting 队首，预算剩 15。' },
    { id: 'ok', title: '顺利', goal: '主路径', setup: '空闲 4 块', ticks: [
      { card: 'sched', lines: [2, 3], text: 't1', vals: { n: '1' } },
      { card: 'sched', lines: [6, 7], text: 't2', vals: { blocks: '[b7]' } },
      { card: 'sched', lines: [9, 10], text: 't3', vals: { budget: '16 → 15' } }], result: 'r' },
  ],
};
const cj = join(tmp, 'scn.json');
writeFileSync(cj, JSON.stringify(canvas));
const validate = p => spawnSync('python3', [resolve(root, 'validate.py'), p, '--no-exec'], { encoding: 'utf8' }).stdout;
check('fixture with boxes + scenarios passes validate', !validate(cj).includes('ERROR'));
const variant = (name, mut) => {
  const c = JSON.parse(JSON.stringify(canvas)); mut(c);
  const p = join(tmp, name + '.json'); writeFileSync(p, JSON.stringify(c)); return validate(p);
};
check('fabricated variable in vals → ERROR',
  variant('v1', c => { c.scenarios[0].ticks[0].vals = { kv_watermark: '3' }; }).includes('推演不许编造变量'));
check('tick lines out of range → ERROR',
  variant('v2', c => { c.scenarios[0].ticks[1].lines = [9, 30]; }).includes('应为卡内 [起, 止]'));
check('jump between unlinked cards warns',
  variant('v3', c => { c.wires = []; }).includes('两卡之间没有连线'));
check('scenario needs ≥3 ticks', variant('v4', c => { c.scenarios[1].ticks.pop(); }).includes('ticks 应有 3-40 跳'));
check('box that is never called → ERROR', variant('v5', c => { c.boxes[0].name = 'reserve'; }).includes('都没被调用'));
check('box already opened as a card warns', variant('v6', c => { c.boxes.push({ name: 'preempt', in: 'r', out: '-', effect: 'e', why: 'w' }); }).includes('已在画布上展开'));
check('deep canvas without boxes / scenarios warns',
  (o => o.includes('缺 boxes') && o.includes('缺 scenarios'))(variant('v7', c => { delete c.boxes; delete c.scenarios; })));
// check_refs verifies box refs against the repo and drops the bad one
const repo = join(tmp, 'repo'); mkdirSync(repo);
writeFileSync(join(repo, 's.py'), sched + '\n');
const cr = join(tmp, 'cr.json'); writeFileSync(cr, JSON.stringify(canvas));
execFileSync('python3', [resolve(root, 'check_refs.py'), cr, repo]);
const crd = JSON.parse(readFileSync(cr, 'utf8'));
check('check_refs drops a box ref that does not hold, keeps the good one',
  crd.boxes[1].refs.length === 1 && crd.boxes[1].refs[0][1] === 6 && crd.boxes[0].refs.length === 1);

const html = join(tmp, 'scn.html');
execFileSync('python3', [resolve(root, 'render.py'), cj, html]);
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto('file://' + html);
await page.waitForTimeout(500);
await page.keyboard.press('Escape'); await page.waitForTimeout(200);
const cur = () => page.$$eval('.ln.sc-cur', els => els.map(e => e.id));
const walkTxt = () => page.textContent('#walk');
check('scenario tabs list 导读 + each scenario', (await page.$$eval('#scn-tabs .scn-tab', e => e.map(x => x.textContent))).join('|')
  === '导读|情境 · 块不够：抢占|情境 · 顺利');
await page.click('#scn-tabs .scn-tab[data-i="0"]'); await page.waitForTimeout(400);
check('entering a scenario: first tick lines lit, pointer on first line, setup + init values + 推演 label',
  JSON.stringify(await cur()) === '["sched-L2","sched-L3"]'
  && await page.$eval('#sched-L2', e => e.classList.contains('sc-ptr'))
  && (await walkTxt()).includes('初始条件') && (await walkTxt()).includes('推演')
  && (await page.$$eval('#walk .sc-vars td', t => t.map(x => x.textContent))).includes('16')
  && (await page.textContent('#sbadges')).includes('非实测'));
check('tick shows inline value at the end of its last line', (await page.textContent('#sched-L3 .sc-val')) === 'n = 1');
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(300);
check('→ moves to the next tick and shows the branch taken',
  JSON.stringify(await cur()) === '["sched-L4","sched-L5"]' && (await walkTxt()).includes('不进 continue')
  && (await page.$$('.sc-val')).length === 0);
await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight'); await page.waitForTimeout(400);
check('cross-card tick: other card dims, walk names the card, changed value highlighted',
  JSON.stringify(await cur()) === '["pre-L2","pre-L3"]'
  && await page.$eval('#card-sched', e => e.classList.contains('dim'))
  && (await walkTxt()).includes('preempt()')
  && (await page.$eval('#walk .sc-vars tr.hot', e => e.textContent)).includes('22 → 0'));
check('values carry over across ticks (blocks still in the panel)',
  (await page.$$eval('#walk .sc-vars td', t => t.map(x => x.textContent))).includes('None'));
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(300);
check('last tick shows the result; ▶ disabled; stays in scenario',
  (await walkTxt()).includes('A 回到 waiting 队首') && await page.$eval('#next', b => b.disabled));
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(200);
check('→ past the last tick does not leave the scenario', JSON.stringify(await cur()) === '["sched-L9"]');
await page.keyboard.press('ArrowLeft'); await page.waitForTimeout(300);
check('← steps back one tick', JSON.stringify(await cur()) === '["pre-L2","pre-L3"]');
await page.click('#scn-tabs .scn-tab[data-i="-1"]'); await page.waitForTimeout(300);
check('导读 tab returns to the story step and clears the pointer',
  (await cur()).length === 0 && !(await page.textContent('#stitle')).includes('情境推演'));
// black boxes at call sites
check('black box marked at its call site only, with the contract in the tooltip',
  (await page.$$('#card-sched .vref.box[data-b="allocate"]')).length === 1
  && (await page.$eval('#card-sched .vref.box[data-b="allocate"]', e => e.title)).includes('块或 None')
  && (await page.$$('.vref.box[data-b="free"]')).length === 1);
await page.click('#card-sched .vref.box[data-b="allocate"]'); await page.waitForTimeout(300);
check('clicking a black box opens the overview on its row',
  !(await page.isHidden('#overview')) && await page.$eval('#ov-box-allocate', r => r.classList.contains('hl')));
check('overview lists the scenarios as entry points', (await page.$$('#overview .ov-link[data-scn]')).length === 2);
await page.click('#overview .ov-link[data-scn="1"]'); await page.waitForTimeout(400);
check('overview entry starts that scenario', await page.isHidden('#overview')
  && (await page.textContent('#stitle')).includes('顺利') && JSON.stringify(await cur()) === '["sched-L2","sched-L3"]');
await page.evaluate(() => setStep(1)); await page.waitForTimeout(300);
check('jumping to a step leaves scenario mode', (await cur()).length === 0 && await page.evaluate(() => scn) === null);
// phone stream
await page.setViewportSize({ width: 390, height: 844 }); await page.reload(); await page.waitForTimeout(600);
await page.keyboard.press('Escape'); await page.waitForTimeout(200);
await page.click('#scn-tabs .scn-tab[data-i="0"]'); await page.waitForTimeout(400);
check('phone: scenario renders as one stream of ticks (code + value chips + text)',
  (await page.$$('#mstream .ms-tick')).length === 5 && (await page.$$('#mstream .ms-tick .ms-code')).length === 5
  && (await page.textContent('#mstream .ms-vals')).includes('n = 1') && (await page.textContent('#mstream')).includes('A 回到 waiting 队首'));
await page.click('#next'); await page.click('#next'); await page.waitForTimeout(400);
check('phone: ▶ scrolls to the next tick and marks it',
  await page.$eval('#mst-2', e => e.classList.contains('cur'))
  && await page.evaluate(() => Math.abs(mstreamEl.scrollTop - Math.min(document.getElementById('mst-2').offsetTop - 8,
    mstreamEl.scrollHeight - mstreamEl.clientHeight)) < 4 && mstreamEl.scrollTop > 0));
await browser.close();
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
