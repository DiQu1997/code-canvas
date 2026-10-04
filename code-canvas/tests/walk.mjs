// Segment-by-segment guided reading (step.read): validate gates, in-step navigation,
// dimming/highlight, explanation card, and the phone stream rendering.
// Usage: node tests/walk.mjs
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync, spawnSync } from 'child_process';
import { mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'canvas-walk-'));

const code = Array.from({ length: 30 }, (_, i) => `    x${i + 1} = step_${i + 1}(budget)`).join('\n');
const longText = t => t + '：这几行把预算按条件分出去，并记下本步排了多少，后面的循环会接着用。';
const canvas = {
  meta: { title: 'walk fixture' },
  cards: [
    { id: 'sched', name: 'schedule()', file: 's.py:100', lang: 'py', code, layout: { col: 0, band: 0 } },
    { id: 'other', name: 'other()', file: 'o.py:1', lang: 'py', code: 'def other():\n    return 1', layout: { col: 1, band: 0 } },
  ],
  wires: [], regions: [], notes: [],
  steps: [
    { title: '总览', fit: true },
    { title: '① 开账', focus: ['sched'], caption: 'c1',
      read: { card: 'sched', ranges: [[3, 10], [20, 24]], gaps: ['省略：统计字段'], problem: '每一步要把有限的预算分给排队的请求。', crux: '预算不够时谁先谁后。',
              walk: [{ lines: [3, 6], text: longText('第一段') }, { lines: [7, 10], text: longText('第二段') },
                     { lines: [20, 24], text: longText('第三段') }],
              solution: '按顺序贪心扣预算。', takeaway: '贪心分配。' } },
    { title: '② 另一步', focus: ['sched'], caption: 'c2',
      read: { card: 'sched', ranges: [[11, 14]], problem: 'q2', walk: [{ lines: [11, 14], text: longText('唯一一段') }], solution: 'a2' } },
  ],
};
const cj = join(tmp, 'walk.json');
writeFileSync(cj, JSON.stringify(canvas));
const validate = p => spawnSync('python3', [resolve(root, 'validate.py'), p, '--no-exec'], { encoding: 'utf8' }).stdout;
check('fixture with read passes validate', !validate(cj).includes('ERROR'));
const variant = (name, mut) => {
  const c = JSON.parse(JSON.stringify(canvas)); mut(c);
  const p = join(tmp, name + '.json'); writeFileSync(p, JSON.stringify(c)); return validate(p);
};
check('walk must cover every line (gap → ERROR)',
  variant('v1', c => { c.steps[1].read.walk[1].lines = [8, 10]; }).includes('应从第 7 行开始'));
check('walk must not leave lines unexplained',
  variant('v2', c => { c.steps[1].read.walk.pop(); }).includes('漏讲了第 20-24 行'));
check('read capped at 60 lines',
  variant('v3', c => { c.cards[0].code = Array.from({ length: 80 }, (_, i) => 'y' + i).join('\n');
                        c.steps[2].read = { card: 'sched', ranges: [[1, 70]], walk: [{ lines: [1, 70], text: 'x'.repeat(600) }] }; })
    .includes('70 行代码（≤60）'));
check('gaps count must match ranges', variant('v4', c => { c.steps[1].read.gaps = []; }).includes('read.gaps 应有 1 条'));
check('thin explanation warns', variant('v5', c => { c.steps[2].read.walk[0].text = '取预算。'; }).includes('偏薄'));
check('story step without read warns on deep canvas', variant('v6', c => { delete c.steps[2].read; }).includes('故事步缺 read'));

const html = join(tmp, 'walk.html');
execFileSync('python3', [resolve(root, 'render.py'), cj, html]);
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto('file://' + html);
await page.waitForTimeout(600);
const cur = () => page.$$eval('.ln.rd-cur', els => els.map(e => +e.id.split('-L').pop()));
const walkTxt = () => page.textContent('#walk');
await page.click('#next'); await page.waitForTimeout(500);
check('entering a read step dims lines outside the ranges',
  await page.$eval('#sched-L15', e => e.classList.contains('rd-out'))
  && !(await page.$eval('#sched-L4', e => e.classList.contains('rd-out'))));
check('first segment shows the problem, the crux and its explanation',
  JSON.stringify(await cur()) === '[3,4,5,6]' && (await walkTxt()).includes('要解决的问题') && (await walkTxt()).includes('难点')
  && (await walkTxt()).includes('有限的预算') && (await walkTxt()).includes('第 1 / 3 段'));
check('explanation card sits right of the card, near the segment', await page.evaluate(() => {
  const w = document.getElementById('walk'), c = document.getElementById('card-sched');
  return w.offsetLeft > c.offsetLeft + c.offsetWidth && Math.abs(w.offsetTop - (c.offsetTop + document.getElementById('sched-L3').offsetTop)) < 120;
}));
check('explanation card stays clear of the floating HUD buttons', await page.evaluate(() => {
  const w = document.getElementById('walk').getBoundingClientRect();
  return ['hint', 'canvas-ask-btn', 'profile-btn', 'dl-btn', 'ov-btn', 'up-btn'].map(id => document.getElementById(id))
    .filter(e => e && e.offsetParent).every(e => { const r = e.getBoundingClientRect();
      return r.right < w.left || r.left > w.right || r.bottom < w.top || r.top > w.bottom; });
}));
check('camera frames the segment legibly', await page.evaluate(() => cam.s) >= 0.85);
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(400);
check('→ advances to the next segment inside the same step',
  await page.evaluate(() => step) === 1 && JSON.stringify(await cur()) === '[7,8,9,10]'
  && !(await walkTxt()).includes('要解决的问题'));
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(400);
check('last segment shows the solution and the takeaway',
  JSON.stringify(await cur()) === '[20,21,22,23,24]' && (await walkTxt()).includes('怎么解决的')
  && (await walkTxt()).includes('设计点') && (await page.textContent('#sbadges')).includes('带读 3/3'));
await page.keyboard.press('ArrowRight'); await page.waitForTimeout(400);
check('→ after the last segment moves to the next step, first segment',
  await page.evaluate(() => step) === 2 && JSON.stringify(await cur()) === '[11,12,13,14]'
  && await page.$eval('#sched-L4', e => e.classList.contains('rd-out')));
await page.keyboard.press('ArrowLeft'); await page.waitForTimeout(400);
check('← back into the previous step lands on its last segment',
  await page.evaluate(() => step) === 1 && JSON.stringify(await cur()) === '[20,21,22,23,24]');
await page.evaluate(() => setStep(0)); await page.waitForTimeout(300);
check('leaving read steps clears dimming and the card', await page.isHidden('#walk')
  && (await page.$$('.ln.rd-out')).length === 0);
// phone: stream mode renders code/explanation pairs
await page.setViewportSize({ width: 390, height: 844 }); await page.reload(); await page.waitForTimeout(600);
await page.evaluate(() => setStep(1)); await page.waitForTimeout(400);
check('phone stream alternates code segments and explanations',
  (await page.$$('#mstream .ms-code')).length === 3 && (await page.$$('#mstream .ms-walk')).length === 3
  && (await page.textContent('#mstream .ms-gap')).includes('省略：统计字段')
  && (await page.textContent('#mstream')).includes('怎么解决的'));
await browser.close();
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
