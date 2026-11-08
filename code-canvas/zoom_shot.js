#!/usr/bin/env node
// Screenshot every level of a zoom canvas for self-review.
// Usage: node zoom_shot.js zoom.html outdir [--w 1400 --h 860] [--mobile]
// Writes: 0-system.png, 1-mod-<id>.png (each zoomable module), 2-card-<id>.png (first card of each module),
//         3-code-<story step>.png (a few story steps at code level). Prints JS errors.
const {chromium} = require('playwright');
const path = require('path'), fs = require('fs');
(async () => {
  const a = process.argv.slice(2);
  const file = a[0], out = a[1] || 'shots';
  const opt = (k, d) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : d };
  const mobile = a.includes('--mobile');
  fs.mkdirSync(out, {recursive: true});
  const b = await chromium.launch();
  const p = await b.newPage(mobile ? {viewport: {width: 390, height: 844}, deviceScaleFactor: 1, isMobile: true, hasTouch: true}
                                   : {viewport: {width: +opt('--w', 1400), height: +opt('--h', 860)}, colorScheme: opt('--scheme', 'dark')});
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error' && !/ERR_|net::/.test(m.text())) errs.push(m.text()) });
  await p.goto('file://' + path.resolve(file));
  await p.waitForTimeout(900);
  const shot = async n => { await p.waitForTimeout(+opt('--wait', 2600)); await p.screenshot({path: path.join(out, n)}); console.log(n) };
  await p.screenshot({path: path.join(out, '0-system.png')}); console.log('0-system.png');
  const ok = await p.evaluate(() => !!window.__zoom);
  if (!ok) { console.log('ERRORS:', errs.join('\n') || 'page did not initialise'); await b.close(); process.exit(1) }
  const mods = await p.evaluate(() => Object.values(__zoom.MOD).filter(m => !m.ext).map(m => m.id));
  const cards = await p.evaluate(() => { const by = {}; for (const c of Object.values(__zoom.CARD)) (by[c.M.id] = by[c.M.id] || []).push(c.id); return by });
  for (const m of mods) {
    await p.evaluate(id => __zoom.flyTo(__zoom.fitBox(__zoom.MOD[id].box, 1.04)), m); await shot(`1-mod-${m}.png`);
    const c = (cards[m] || [])[0];
    if (c) { await p.evaluate(id => __zoom.flyTo(__zoom.fitBox(__zoom.CARD[id].wb, 1.25)), c); await shot(`2-card-${c}.png`) }
  }
  const nSteps = await p.evaluate(() => __zoom.steps ? __zoom.steps().length : 0);
  const picks = [...new Set([Math.floor(nSteps * .3), Math.floor(nSteps * .6), nSteps - 2].filter(i => i >= 0))];
  for (const i of picks) { await p.evaluate(i => __zoom.goStep(i), i); await shot(`3-step-${i + 1}.png`) }
  console.log('ERRORS:', errs.join('\n') || 'none');
  await b.close();
})();
