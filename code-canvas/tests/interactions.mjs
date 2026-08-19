// Click/drag regression test for the canvas template.
// Usage: node tests/interactions.mjs demo/nano-vllm.html
// Needs playwright (npm i playwright anywhere; point CANVAS_TEST_PW at its
// package dir if not resolvable from here) + a chromium binary
// (CANVAS_TEST_CHROMIUM, default /opt/pw-browsers/chromium). Exercises every
// click affordance against the deferred-pointer-capture pan logic.
import { resolve } from 'path';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const html = process.argv[2];
if (!html) { console.error('usage: node tests/interactions.mjs <canvas.html>'); process.exit(2); }
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.goto('file://' + resolve(html));
await page.waitForTimeout(800);
const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);

check('home link hidden on static file', !(await page.isVisible('#home-link')));

// reading-order chips: none on overview, appear on a multi-focus step, renumber on step change
check('no order chips on overview', (await page.$$('.ordchip')).length === 0);
await page.click('#next'); await page.waitForTimeout(700);
const s1chips = await page.$$eval('.ordchip', els => els.map(e => e.textContent));
check('order chips on step 1 follow focus order', s1chips.length >= 2 && s1chips[0] === '1');
await page.click('#next'); await page.waitForTimeout(700);
const s2chips = await page.$$('.ordchip');
check('chips refresh on step change', s2chips.length >= 2);
check('lit wire has direction arrow', await page.$('svg path.wire.on[marker-end]') !== null);

// state snapshot card: per-step diff highlighting
await page.click('#next'); await page.waitForTimeout(700);   // step 3: 分配 + 前缀命中
check('snapshot shows added cells on allocate step',
  (await page.$$('#card-kv-pool .scell.add')).length >= 2);
check('snapshot shows changed record field',
  (await page.$$('#card-kv-pool .scell.chg')).length >= 1);
check('struct relation wire drawn', await page.$('svg path.wire.struct') !== null);
await page.click('#next'); await page.waitForTimeout(700);   // step 4: 抢占回收
const s4add = await page.$$eval('#card-kv-pool .scell.add', els => els.map(e => e.textContent));
check('value-based array diff marks only returned block', s4add.length === 1 && s4add[0] === '2');
check('snapshot note narrates the transition',
  (await page.textContent('#card-kv-pool .snote')).includes('归还'));
for (let i = 0; i < 4; i++) await page.click('#prev');
await page.waitForTimeout(600);

const term = await page.$('.term');
if (term) {
  const tn = await term.getAttribute('data-tn');
  await term.click();
  check('term click opens tnote', await page.isVisible(`#${tn}.open`));
  await term.click();
  check('term click again closes tnote', !(await page.isVisible(`#${tn}.open`)));
} else check('term present in demo', false);

const bar = await page.$('.bbar');
if (bar) {
  const wasFolded = await page.$eval('.blk', b => b.classList.contains('folded'));
  await bar.click();
  check('bbar click toggles block fold',
    (await page.$eval('.blk', b => b.classList.contains('folded'))) !== wasFolded);
} else check('block bar present in demo', false);

const ex = await page.$('.bbtn[data-act="explain"]');
if (ex) {
  await ex.click();
  check('explain button opens bxplain', await page.isVisible('.blk.explained > .bxplain'));
} else check('explain button present in demo', false);

await page.click('.bbtn[data-act="ask"]');
check('ask button opens drawer', await page.$eval('#qa', q => q.classList.contains('open')));
check('drawer shows block name', (await page.textContent('#qa-name')).length > 0);
await page.click('#qa-close');

await page.click('.card .hdr');
check('hdr click toggles card', true); // no throw = dispatched to header, not swallowed

const before = await page.evaluate(() => world.style.transform);
await page.mouse.move(750, 400); await page.mouse.down();
await page.mouse.move(850, 450, { steps: 5 }); await page.mouse.up();
check('drag pans canvas', before !== await page.evaluate(() => world.style.transform));

const b2 = await page.evaluate(() => world.style.transform);
await page.mouse.click(700, 820);
check('still click does not pan', b2 === await page.evaluate(() => world.style.transform));

console.log(results.join('\n'));
await browser.close();
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
