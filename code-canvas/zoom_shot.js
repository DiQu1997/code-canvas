#!/usr/bin/env node
// Screenshot every level of a zoom canvas for self-review.
// Usage: node zoom_shot.js zoom.html outdir [--w 1400 --h 860] [--mobile] [--scheme dark|light] [--all]
// Writes: 0-system.png, 1-mod-<id>.png, 2-card-<id>.png (first card of each module; every card with --all),
//         3-code-<id>.png, t-*.png (frames in the middle of a zoom transition), s-step-N.png (story steps).
const {chromium} = require('playwright');
const path = require('path'), fs = require('fs');
(async () => {
  const a = process.argv.slice(2);
  const file = a[0], out = a[1] || 'shots';
  const opt = (k, d) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : d };
  const mobile = a.includes('--mobile'), all = a.includes('--all');
  fs.mkdirSync(out, {recursive: true});
  const b = await chromium.launch();
  const p = await b.newPage(mobile ? {viewport: {width: 390, height: 844}, deviceScaleFactor: 1, isMobile: true, hasTouch: true, colorScheme: opt('--scheme', 'dark')}
                                   : {viewport: {width: +opt('--w', 1400), height: +opt('--h', 860)}, colorScheme: opt('--scheme', 'dark')});
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error' && !/ERR_|net::/.test(m.text())) errs.push(m.text()) });
  await p.goto('file://' + path.resolve(file));
  await p.waitForTimeout(900);
  const idle = async () => { for (let i = 0; i < 60; i++) { if (!(await p.evaluate(() => __zoom.busy()))) break; await p.waitForTimeout(60) } await p.waitForTimeout(120) };
  const shot = async n => { await idle(); await p.screenshot({path: path.join(out, n)}); console.log(n) };
  await p.screenshot({path: path.join(out, '0-system.png')}); console.log('0-system.png');
  if (!(await p.evaluate(() => !!window.__zoom))) { console.log('ERRORS:', errs.join('\n') || 'page did not initialise'); await b.close(); process.exit(1) }
  const mods = await p.evaluate(() => Object.values(__zoom.MOD).filter(m => !m.ext).map(m => m.id));
  const cards = await p.evaluate(() => __zoom.MODCARDS);
  // frames inside the first transition
  if (mods[0]) { await p.evaluate(id => { __zoom.enter({type: 'mod', id}) }, mods[0]);
    for (const ms of [130, 110, 110]) { await p.waitForTimeout(ms); await p.screenshot({path: path.join(out, `t-${mods[0]}-${ms}-${Date.now() % 1000}.png`)}) }
    await idle(); await p.evaluate(() => __zoom.goto({type: 'sys'})); await idle() }
  for (const m of mods) {
    await p.evaluate(id => __zoom.goto({type: 'mod', id}), m); await shot(`1-mod-${m}.png`);
    for (const c of (cards[m] || []).slice(0, all ? 99 : 1)) {
      await p.evaluate(([id, m]) => __zoom.goto({type: 'card', ids: [id], mod: m}), [c, m]); await shot(`2-card-${c}.png`);
      const short = await p.evaluate(() => !!__zoom.cur().short);
      if (!short) { await p.evaluate(([id, m]) => __zoom.goto({type: 'code', ids: [id], mod: m, card: id}), [c, m]); await shot(`3-code-${c}.png`) }
      await p.evaluate(id => __zoom.goto({type: 'mod', id}), m); await idle();
    }
  }
  const n = await p.evaluate(() => __zoom.steps().length);
  for (const i of [...new Set([0, Math.floor(n * .3), Math.floor(n * .6), n - 2].filter(i => i >= 0 && i < n))]) {
    await p.evaluate(i => __zoom.goStep(i), i); await p.waitForTimeout(400); await shot(`s-step-${i + 1}.png`) }
  await p.evaluate(() => __zoom.goto({type: 'sys'})); await idle();
  const cross = await p.evaluate(() => window.__zoomCross || []);
  console.log('CROSSINGS (连线穿过了别的模块，用 sides / dy1 / dy2 / 挪位置修掉):', cross.length ? '\n  ' + cross.join('\n  ') : 'none');
  console.log('ERRORS:', errs.join('\n') || 'none');
  await b.close();
})();
