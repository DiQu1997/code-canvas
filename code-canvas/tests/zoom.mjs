// Zoom canvas, function card: every block is a row that opens in place into its pseudo-code and source.
// Folding, the zoom steps (open one block → open all → fold → leave), story marks, short cards; desktop and phone.
// Usage: node tests/zoom.mjs     (fixture: tests/fixtures/zoom-kafka.json, a built canvas with its code)
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mkdtempSync, writeFileSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const html = join(mkdtempSync(join(tmpdir(), 'canvas-zoom-')), 'zoom.html');
writeFileSync(html, readFileSync(join(root, 'template/zoom.html'), 'utf8').replace('/*ZOOM_DATA*/null',
  () => readFileSync(join(root, 'tests/fixtures/zoom-kafka.json'), 'utf8').replace(/<\//g, '<\\/')));
let fails = 0; const check = (n, ok) => { console.log((ok ? 'PASS ' : 'FAIL ') + n); if (!ok) fails++; };
const b = await chromium.launch(process.env.CANVAS_TEST_CHROMIUM ? {executablePath: process.env.CANVAS_TEST_CHROMIUM} : {});
for (const mobile of [false, true]) {
  const tag = mobile ? 'phone: ' : '';
    const p = await b.newPage(mobile ? {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true} : {viewport: {width: 1400, height: 860}});
    const errs = []; p.on('pageerror', e => errs.push(e.message));
    await p.goto('file://' + html); await p.waitForTimeout(800);
    const idle = async () => { for (let i = 0; i < 80; i++) { if (!(await p.evaluate(() => __zoom.busy()))) break; await p.waitForTimeout(60); } await p.waitForTimeout(250); };
    const st = () => p.evaluate(() => ({type: __zoom.cur().type, open: document.querySelectorAll('.sg.open:not(.gap)').length, total: document.querySelectorAll('.sg:not(.gap)').length, cur: document.querySelectorAll('.cl.cur').length, tf: __zoom.cur().el.style.transform}));
    const wheel = (dy, sel) => p.evaluate(([dy, sel]) => { const e = document.querySelector(sel); const r = e.getBoundingClientRect(); e.dispatchEvent(new WheelEvent('wheel', {deltaY: dy, ctrlKey: true, clientX: r.left + 40, clientY: r.top + r.height / 2, bubbles: true, cancelable: true})); }, [dy, sel]);
    await p.evaluate(() => __zoom.goto({type: 'mod', id: 'apis'})); await idle();
    await p.evaluate(() => __zoom.goto({type: 'card', ids: ['api_produce'], mod: 'apis'})); await idle();
    let s = await st(); check(tag + 'a long card opens folded', s.type === 'card' && s.open === 0 && s.total >= 3);
    if (!mobile) {
      const heads = await p.$$('.sg:not(.gap) > .bh'); const bb = await heads[1].boundingBox();
      await p.mouse.click(bb.x + 60, bb.y + bb.height / 2); await p.waitForTimeout(200); s = await st(); check(tag + 'clicking a row opens that block only', s.open === 1);
      await p.mouse.click(bb.x + 60, bb.y + bb.height / 2); await p.waitForTimeout(200); s = await st(); check(tag + 'clicking it again folds it', s.open === 0);
      const third = await p.evaluate(() => '.sg[data-a="' + __zoom.CARD.api_produce.cd.blocks[2][0] + '"] > .bh');
      await wheel(-120, third); await p.waitForTimeout(150); s = await st(); check(tag + 'zoom in opens the block under the pointer', s.open === 1 && s.type === 'card');
      await wheel(-120, '.sg.open > .bh'); await p.waitForTimeout(150); s = await st(); check(tag + 'zoom steps inside one gesture are not doubled', s.open === 1);
      await p.waitForTimeout(500); await wheel(-120, '.sg.open > .bh'); await p.waitForTimeout(150); s = await st(); check(tag + 'zoom in again opens every block', s.open === s.total);
      await p.waitForTimeout(500); await wheel(120, '.cd'); await p.waitForTimeout(150); s = await st(); check(tag + 'zoom out folds back to the outline, still on the card', s.open === 0 && s.type === 'card');
      await p.waitForTimeout(500); await wheel(400, '.cd'); await p.waitForTimeout(1200); await idle(); s = await st(); check(tag + 'zoom out again leaves to the module', s.type === 'mod');
      await p.evaluate(() => __zoom.goto({type: 'card', ids: ['api_produce'], mod: 'apis'})); await idle();
    }
    await p.click('[data-all]'); await p.waitForTimeout(200); s = await st(); check(tag + '"全部展开" opens every block', s.open === s.total);
    await p.click('[data-all]'); await p.waitForTimeout(200); s = await st(); check(tag + 'and folds them again', s.open === 0);
    // a story step that lands on a line opens just that block and marks the line
    const line = await p.evaluate(() => __zoom.CARD.api_produce.cd.blocks[2][0] + 1);
    await p.evaluate(l => __zoom.cur().mark('api_produce', 'x = 1', l, l + 1), line); await p.waitForTimeout(700); s = await st();
    check(tag + 'marking a line opens its block and highlights it', s.open === 1 && s.cur === 2);
    // leaving and coming back by Escape: no transform left behind
    await p.keyboard.press('Escape'); await idle(); s = await st(); check(tag + 'Escape leaves to the module with no leftover transform', s.type === 'mod' && !s.tf);
    // a short card has nothing to fold
    const shortId = await p.evaluate(() => Object.values(__zoom.CARD).find(c => c.cd.code.length <= 12).id);
    await p.evaluate(id => { const c = __zoom.CARD[id]; return __zoom.goto({type: 'card', ids: [id], mod: c.mod}); }, shortId); await idle();
    check(tag + 'a short card is fully open and has no fold button', await p.evaluate(() => __zoom.cur().short && !document.querySelector('[data-all]') && !document.querySelector('.sg:not(.gap):not(.open)')));
    check(tag + 'no page errors', errs.length === 0); if (errs.length) console.log(errs.join('\n'));
    await p.close();
}
await b.close();
process.exit(fails ? 1 : 0);
