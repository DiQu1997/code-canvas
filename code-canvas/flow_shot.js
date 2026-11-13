#!/usr/bin/env node
// Screenshot every page (level) of a logic-line canvas, plus a few example-walk steps and one source reader.
// Usage: node flow_shot.js flow.html outdir [--mobile] [--scheme dark]
const {chromium, devices} = require('playwright');
const path = require('path'), fs = require('fs');
(async () => {
  const a = process.argv.slice(2), file = a[0], out = a[1] || 'shots';
  const opt = (k, d) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : d };
  const mobile = a.includes('--mobile');
  fs.mkdirSync(out, {recursive: true});
  const b = await chromium.launch();
  const p = await b.newPage(mobile ? {...devices['iPhone 13'], colorScheme: opt('--scheme', 'light')} : {viewport: {width: 1440, height: 900}, colorScheme: opt('--scheme', 'light')});
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error' && !/ERR_|net::/.test(m.text())) errs.push(m.text()) });
  await p.goto('file://' + path.resolve(file));
  await p.waitForTimeout(800);
  if (!(await p.evaluate(() => !!window.__flow))) { console.log('ERRORS:', errs.join('\n') || 'page did not initialise'); await b.close(); process.exit(1) }
  const pre = mobile ? 'm-' : '';
  const pages = await p.evaluate(() => __flow.pages());
  let i = 0;
  for (const id of pages) {
    await p.evaluate(id => __flow.nav(id, {push: false}), id); await p.waitForTimeout(900);
    const clipped = await p.evaluate(() => [...document.querySelectorAll('.node .t, .ps .t')].filter(e => e.scrollHeight > e.clientHeight + 2).map(e => e.textContent));
    const n = `${pre}${String(i++).padStart(2, '0')}-${id.replace(/[^\w-]/g, '_')}.png`;
    await p.screenshot({path: path.join(out, n)});
    console.log(n + (clipped.length ? '   CLIPPED: ' + clipped.join(' | ') : ''));
  }
  const nW = await p.evaluate(() => __flow.walk().length);
  for (const k of [...new Set([0, Math.floor(nW * .4), Math.floor(nW * .75)])].filter(k => k < nW)) {
    await p.evaluate(k => __flow.goStep(k), k); await p.waitForTimeout(1300);
    await p.screenshot({path: path.join(out, `${pre}walk-${k + 1}.png`)}); console.log(`${pre}walk-${k + 1}.png`);
  }
  const leaf = await p.evaluate(() => Object.values(__flow.N).find(n => !(n.children || []).length && n.file && n.kind !== 'box')?.id);
  if (leaf) { await p.evaluate(id => __flow.openReader(id), leaf); await p.waitForTimeout(500); await p.screenshot({path: path.join(out, `${pre}reader.png`)}); console.log(`${pre}reader.png`) }
  console.log('ERRORS:', errs.join('\n') || 'none');
  await b.close();
})();
