const { chromium } = require('playwright');
(async () => {
  const [html, out, ...specs] = process.argv.slice(2);
  const b = await chromium.launch();
  for (const spec of specs) {
    const m = spec.match(/^([^@+]+)(?:@([md]))?(?:\+(\d+))?$/);
    const id = m[1], dev = m[2] || 'm', n = +(m[3] || 0);
    const ctx = await b.newContext({ viewport: dev === 'm' ? {width:390,height:844} : {width:1440,height:900}, deviceScaleFactor: 2 });
    const p = await ctx.newPage();
    const errs = []; p.on('pageerror', e => errs.push(String(e)));
    await p.goto('file://' + html + '#/' + id);
    await p.waitForTimeout(400);
    for (let i = 0; i < n; i++) { await p.click('.fx'); await p.waitForTimeout(120); }
    const f = `${out}/${id}.${dev}${n ? '.step' + (n + 1) : ''}.png`;
    await p.screenshot({ path: f, fullPage: true });
    console.log(f, 'errors=' + errs.length);
    await ctx.close();
  }
  await b.close();
})();
