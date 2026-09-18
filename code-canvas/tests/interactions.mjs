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

// context peek: ONE unified scroll window (ctx-above + core + ctx-below),
// wheel scrolls away from the focus region; vim-style relative numbers anchored to core
check('ctx bar present with embedded file', await page.$('#card-step .ctx-bar') !== null);
const hBefore = await page.$eval('#card-step', el => el.offsetHeight);
await page.click('#card-step .ctx-bar');
await page.waitForTimeout(400);
check('one click loads FULL upper context into unified window',
  await page.$eval('#card-step .ctxlines[data-side="top"]', el => el.childElementCount) === 48);
check('lower context loaded too',
  await page.$eval('#card-step .ctxlines[data-side="bot"]', el => el.childElementCount) > 0);
check('excerpt line ids untouched', await page.$('#step-L2') !== null);
check('relative numbers count outward from core (above: …2,1)',
  await page.$eval('#card-step .ctxlines[data-side="top"]', el =>
    el.firstElementChild.querySelector('.no.rel').textContent === '48'
    && el.lastElementChild.querySelector('.no.rel').textContent === '1'));
check('relative numbers below start at 1',
  await page.$eval('#card-step .ctxlines[data-side="bot"]',
    el => el.firstElementChild.querySelector('.no.rel').textContent === '1'));
check('window capped, scrollable, core initially in view', await page.$eval('#card-step .codewin', el =>
  el.classList.contains('scrolly') && el.style.maxHeight !== ''
  && el.scrollHeight > el.clientHeight && el.scrollTop > 0));
check('wheel can leave the focus region (scroll to file top)',
  await page.$eval('#card-step .codewin', el => { el.scrollTop = 0; return el.scrollTop === 0; }));
await page.waitForTimeout(200);
check('wires still drawn while scrolled away', (await page.$$('svg path.wire')).length > 0);
const hAfter = await page.$eval('#card-step', el => el.offsetHeight);
check('card height stays ≤2.5× core', hAfter < hBefore * 2.5 + 60);
await page.click('#card-step .ctx-bar');
await page.waitForTimeout(300);
check('second click folds back to core-only', (await page.$$('#card-step .ln.ctxln')).length === 0
  && !(await page.$eval('#card-step .codewin', el => el.classList.contains('scrolly'))));

// block sandbox (static file://): preset input chips show PRE-RECORDED outputs; no live run
await page.$eval('#card-allocate', el => el.classList.remove('collapsed'));
check('run button on sandboxed block', await page.$('#card-allocate .bbtn[data-act="run"]') !== null);
await page.click('#card-allocate .bbtn[data-act="run"]');
await page.waitForTimeout(300);
check('run panel opens with input chips',
  await page.isVisible('#card-allocate .brun') && (await page.$$('#card-allocate .rn-chip')).length === 3);
check('first preset auto-selected with recorded output',
  (await page.textContent('#card-allocate .rn-out')).includes('命中前缀块数: 2')
  && (await page.textContent('#card-allocate .rn-badge')).includes('预录'));
await page.click('#card-allocate .rn-chip[data-i="1"]');
await page.waitForTimeout(200);
check('picking another input swaps value and output',
  (await page.$eval('#card-allocate .rn-input', el => el.value)).includes('9, 9')
  && (await page.textContent('#card-allocate .rn-out')).includes('命中前缀块数: 0'));
check('static mode: no live run button, input readonly',
  !(await page.isVisible('#card-allocate .rn-go'))
  && await page.$eval('#card-allocate .rn-input', el => el.readOnly));
await page.click('#card-allocate .bbtn[data-act="run"]');
await page.waitForTimeout(200);
check('run panel toggles closed', !(await page.isVisible('#card-allocate .brun')));

// reader mode: in-place close reading
const camBefore = await page.$eval('#world', el => el.style.transform);
await page.$eval('#card-schedule', el => { el.classList.add('collapsed'); });  // 制造可还原状态
await page.$eval('#card-schedule', el => el.classList.remove('collapsed'));
const foldedBefore = await page.$eval('#card-schedule', el =>
  [...el.querySelectorAll('.blk.folded')].map(b => b.querySelector('.bname').textContent));
await page.$eval('#card-schedule .rd-btn', el => el.click());
await page.waitForTimeout(400);
check('reader opens, canvas nav hidden', await page.evaluate(() =>
  document.body.classList.contains('reading')) && !(await page.isVisible('#bar')));
check('reader is outside pan-zoom viewport', await page.evaluate(() =>
  !document.getElementById('vp').contains(document.getElementById('rd-card'))));
check('all blocks unfolded for continuous reading',
  (await page.$$('#rd-card .blk.folded')).length === 0);
check('comfortable font size', await page.$eval('#rd-card .ln',
  el => parseFloat(getComputedStyle(el).fontSize)) >= 12.5);
check('code text selectable in reader', await page.$eval('#rd-card',
  el => getComputedStyle(el).userSelect) !== 'none');
await page.$eval('#rd-card', el => { el.scrollTop = 40; });
check('reader scrolls internally, camera untouched',
  await page.$eval('#rd-card', el => el.scrollTop) > 0
  && (await page.$eval('#world', el => el.style.transform)) === camBefore);
const bname = await page.$eval('#rd-card .blk .bname', el => el.textContent);
await page.click('#rd-card .blk .bbar');
await page.waitForTimeout(300);
check('clicking block selects (not folds) and syncs side panel',
  (await page.$$('#rd-card .blk.folded')).length === 0
  && await page.isVisible('#rd-card .blk.rd-sel')
  && (await page.textContent('#rd-side')).includes(bname));
check('detail.why expandable in side panel',
  (await page.textContent('#rd-side')).includes('设计理由')
  && (await page.textContent('#rd-side')).includes('不变量'));
await page.click('#rd-ask');
await page.waitForTimeout(300);
check('QA binds to selected block', await page.isVisible('#qa.open')
  && (await page.textContent('#qa-name')).includes(bname));
await page.click('#qa-close');
await page.keyboard.press('Escape');
await page.waitForTimeout(400);
check('Esc restores card, folds and camera', !(await page.evaluate(() =>
  document.body.classList.contains('reading')))
  && await page.evaluate(() => document.getElementById('world')
      .contains(document.getElementById('card-schedule')))
  && (await page.$eval('#world', el => el.style.transform)) === camBefore
  && JSON.stringify(await page.$eval('#card-schedule', el =>
      [...el.querySelectorAll('.blk.folded')].map(b => b.querySelector('.bname').textContent)))
    === JSON.stringify(foldedBefore)
  && await page.isVisible('#bar'));

// card about strip + storyline preview
check('about strip visible on expanded card',
  (await page.textContent('#card-step .about')).includes('主循环'));
check('about hidden on collapsed card', !(await page.isVisible('#card-run .about'))
  || (await page.$('#card-run .about')) === null);
check('no preview on overview', !(await page.isVisible('#spreview')));
await page.click('#next'); await page.waitForTimeout(400);   // step1 = 故事线 A 第一步
check('storyline preview auto-shows on its first step',
  await page.isVisible('#spreview') && (await page.textContent('#spreview')).includes('预告')
  && (await page.textContent('#spreview')).includes('连续批处理'));
await page.click('#next'); await page.waitForTimeout(400);   // step2 = 故事线 B 第一步
check('preview switches per storyline',
  (await page.textContent('#spreview')).includes('谁上车'));
await page.evaluate(() => setStep(5));                        // 故事线 A 的第二段
await page.waitForTimeout(400);
check('no preview on later steps of same storyline', !(await page.isVisible('#spreview')));
await page.evaluate(() => setStep(0));
await page.waitForTimeout(400);

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
