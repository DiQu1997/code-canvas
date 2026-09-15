// Mobile card-stream regression: <700px viewport renders the same canvas data
// as a step-driven vertical stream (cards/blocks/sandbox/context all usable),
// with a mode toggle back to the 2D canvas.
// Usage: node tests/mobile.mjs demo/nano-vllm.html
import { resolve } from 'path';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const html = process.argv[2];
if (!html) { console.error('usage: node tests/mobile.mjs <canvas.html>'); process.exit(2); }
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto('file://' + resolve(html));
await page.waitForTimeout(900);
const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);

// 1. auto-enters stream mode on phone viewport
check('stream mode auto-on under 700px',
  await page.evaluate(() => document.body.classList.contains('mstream')));
check('canvas viewport hidden', !(await page.isVisible('#vp')));
check('overview lists storyline sections', (await page.$$('#mstream .ms-sect')).length >= 2);
check('overview shows per-storyline previews', (await page.$$('#mstream .ms-prev')).length >= 3);
check('about strip present in stream', (await page.$$('#mstream .about')).length >= 1);
check('cards are stacked full-width', await page.$eval('#mstream .card',
  el => Math.abs(el.getBoundingClientRect().width - (390 - 20)) < 30));

// 2. stepping rebuilds the stream in focus order with wire chips
await page.click('#next');
await page.waitForTimeout(400);
const s1cards = await page.$$eval('#mstream .card', els => els.map(e => e.id));
check('step 1 shows only its focus cards in order', s1cards.length >= 1 && s1cards.length <= 4);
check('numbered section per card on multi-focus step',
  s1cards.length < 2 || (await page.$$('#mstream .ms-ord')).length === s1cards.length);
// find a step with lit wires for chips
let chipStep = -1;
const nSteps = await page.evaluate(() => DATA.steps.length);
for (let i = 1; i < nSteps; i++) {
  const has = await page.evaluate(i2 => (DATA.steps[i2].wires || []).length > 0, i);
  if (has) { chipStep = i; break; }
}
if (chipStep > 0) {
  await page.evaluate(i => setStep(i), chipStep);
  await page.waitForTimeout(400);
  check('lit wires become tappable relation chips', (await page.$$('#mstream .ms-wire')).length >= 1);
} else check('lit wires become tappable relation chips (no wired step)', true);

// 3. block sandbox + context window still work inside the stream
await page.evaluate(() => setStep(0));
await page.waitForTimeout(400);
await page.$eval('#card-allocate', el => el.classList.remove('collapsed'));
await page.click('#card-allocate .bbtn[data-act="run"]');
await page.waitForTimeout(300);
check('sandbox panel opens in stream', await page.isVisible('#card-allocate .brun')
  && (await page.textContent('#card-allocate .rn-out')).includes('命中前缀块数'));
await page.click('#card-allocate .ctx-bar');
await page.waitForTimeout(400);
check('context window scrolls in stream', await page.$eval('#card-allocate .codewin',
  el => el.classList.contains('scrolly') && el.scrollHeight > el.clientHeight));

// 4. mode toggle escapes to 2D canvas and back
check('mode button visible on phone', await page.isVisible('#mode-btn'));
await page.click('#mode-btn');
await page.waitForTimeout(400);
check('toggle switches to 2D canvas',
  !(await page.evaluate(() => document.body.classList.contains('mstream')))
  && await page.isVisible('#vp') && (await page.$$('#vp .card')).length > 3);
await page.click('#mode-btn');
await page.waitForTimeout(400);
check('toggle returns to stream',
  await page.evaluate(() => document.body.classList.contains('mstream')));

// 5. desktop viewport never enters stream
await page.setViewportSize({ width: 1400, height: 900 });
await page.waitForTimeout(500);
check('desktop resize exits stream and redraws canvas',
  !(await page.evaluate(() => document.body.classList.contains('mstream')))
  && (await page.$$('svg path.wire')).length > 0);

await browser.close();
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
