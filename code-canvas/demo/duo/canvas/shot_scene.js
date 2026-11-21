// shot_scene.js —— 机器图（scene.html）的自检 + 固定一组截图
// 用法：node shot_scene.js <scene.html> <输出目录> [--check] [--dsf2] [--steps]
//   --check  只做机械排版检查（不截图、不点交互，几秒钟）—— 改坐标时反复跑这个，不花看图的钱
// 做三件事：① 交互自检（图层 / 说明卡 / 推演，桌面 1440×900 + 手机 390×844 带触摸）
//          ② 机械排版检查（线标签压框、线穿过别的框、框里放不下、字被截断）—— 打印成 LAYOUT 行
//          ③ 固定 8 张截图（d1~d5 桌面，p1~p3 手机）；--steps 额外把每一步推演各截一张
// 只在无头 Chromium 里跑：iOS Safari 的手势和渲染这里验不到。
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
const [file, outDir] = process.argv.slice(2);
const DSF = process.argv.includes('--dsf2') ? 2 : 1, ALL = process.argv.includes('--steps'), CHECK = process.argv.includes('--check');
if (!file || !outDir) { console.error('用法：node shot_scene.js <scene.html> <输出目录> [--check] [--dsf2] [--steps]'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });
const url = 'file://' + path.resolve(file);
let pass = 0, fail = 0; const lay = new Map(), seen = {};
function ok(c, msg, extra) { if (c) { pass++; console.log('PASS ' + msg); } else { fail++; console.log('FAIL ' + msg + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')); } }
function listen(page, errs) {
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
}
async function settle(page, extra) {
  await page.waitForTimeout(90);
  await page.evaluate(() => { window.__lt = null; window.__ln = 0; });
  await page.waitForFunction(() => {
    const c = window.__cv; if (!c || c.flying()) { window.__ln = 0; return false; }
    const t = c.t(), k = [t.x.toFixed(2), t.y.toFixed(2), t.k.toFixed(5)].join();
    window.__ln = window.__lt === k ? window.__ln + 1 : 0; window.__lt = k; return window.__ln >= 2;
  }, null, { polling: 140, timeout: 9000 });
  await page.waitForTimeout(extra == null ? 450 : extra);
}
const sc = page => page.evaluate(() => window.__cv.scene.get());
const rect = (page, sel) => page.evaluate(s => { const e = document.querySelector(s); if (!e || e.hidden) return null; const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom }; }, sel);
const shot = (page, name) => CHECK ? null : page.screenshot({ path: path.join(outDir, name + '.png') });

// 机械排版检查：在页面里量真实像素
async function layout(page, vp, where) {
  (seen[vp] = seen[vp] || []).push(where);
  const out = await page.evaluate(() => {
    const s = window.__cv.scene.get(), fig = document.querySelector('.nf.cur .scn'), out = [];
    if (!s || !fig) return out;
    const R = e => e.getBoundingClientRect();
    const hit = (a, b, pad) => a.left < b.right - pad && a.right > b.left + pad && a.top < b.bottom - pad && a.bottom > b.top + pad;
    const parts = [...fig.querySelectorAll('.pt')], labels = [...fig.querySelectorAll('.wl')], box = R(fig.querySelector('.sci'));
    labels.forEach(l => { const r = R(l); if (r.left < box.left - 2 || r.right > box.right + 2 || r.top < box.top - 2 || r.bottom > box.bottom + 2) out.push('线标签「' + l.dataset.w + '」伸到画幅外面了'); });
    labels.forEach(l => parts.forEach(p => { if (hit(R(l), R(p), 2)) out.push('线标签「' + l.dataset.w + '」压在部件「' + p.dataset.p + '」上'); }));
    labels.forEach((l, i) => labels.slice(i + 1).forEach(m => { if (hit(R(l), R(m), 2)) out.push('线标签「' + l.dataset.w + '」和「' + m.dataset.w + '」叠在一起'); }));
    parts.forEach(p => {
      const id = p.dataset.p, g = p.querySelector('.ptg'), t = p.querySelector('.pts'), h = p.querySelector('.pth');
      if (g && g.firstElementChild && (g.scrollHeight > g.clientHeight + 1 || g.scrollWidth > g.clientWidth + 1)) out.push('部件「' + id + '」框里的小图放不下（框太小或小图太大）');
      if (g && g.firstElementChild && g.clientHeight < 8) out.push('部件「' + id + '」的小图被挤没了（框太矮）');
      if (t && t.scrollHeight > t.clientHeight + 1) out.push('部件「' + id + '」框底那句话超过两行被截断');
      if (h && h.scrollWidth > h.clientWidth + 1) out.push('部件「' + id + '」的名字一行放不下');
    });
    fig.querySelectorAll('.wr').forEach(g => {
      const id = g.dataset.w, ends = s.ends[id] || [], p = g.querySelector('.wp'), L = p.getTotalLength(), m = p.getScreenCTM(), bad = {};
      for (let i = 1; i < 28; i++) {
        const q = p.getPointAtLength(L * i / 28), x = m.a * q.x + m.c * q.y + m.e, y = m.b * q.x + m.d * q.y + m.f;
        parts.forEach(pt => { if (ends.indexOf(pt.dataset.p) >= 0) return; const r = R(pt); if (x > r.left + 2 && x < r.right - 2 && y > r.top + 2 && y < r.bottom - 2) bad[pt.dataset.p] = 1; });
      }
      Object.keys(bad).forEach(k => out.push('连线「' + id + '」从部件「' + k + '」身上穿过去了'));
    });
    return out;
  });
  out.forEach(m => { const k = '[' + vp + '] ' + m; if (!lay.has(k)) lay.set(k, { vp, at: [] }); const a = lay.get(k).at; if (a.indexOf(where) < 0) a.push(where); });
}
async function allLayouts(page, tag, s) {
  for (const l of ['main', 'why', 'trap']) { await page.evaluate(x => window.__cv.scene.lens(x), l); await page.waitForTimeout(60); await layout(page, tag, ({ main: '主流程', why: '为什么', trap: '易混点' })[l] + '图层'); }
  await page.evaluate(() => window.__cv.scene.lens('main'));
  for (let i = 0; i < s.n; i++) { await page.evaluate(x => window.__cv.scene.step(x), i); await page.waitForTimeout(60); await layout(page, tag, '第 ' + (i + 1) + ' 步'); }
  await page.evaluate(() => window.__cv.scene.step(-1)); await settle(page, 200);
}

async function desktop(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: DSF });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  await page.goto(url); await page.waitForSelector('.nf.cur'); await settle(page);
  let s = await sc(page);
  ok(!!s, '[桌面] 打开的这一页有机器图');
  if (!s) { await shot(page, 'd1-overview'); await ctx.close(); return; }
  ok(!s.mob, '[桌面] 用的是桌面那套坐标', s.mob);
  let f = await rect(page, '.nf.cur .scn .scw'), pl = await rect(page, '#play');
  ok(f && pl && f.x >= 0 && f.r <= 1440 && f.y >= 48 && f.b <= pl.y + 1, '[桌面] 整张机器图在推演条上方、一屏看全', { fig: f, play: pl });
  await shot(page, 'd1-overview');
  await allLayouts(page, '桌面', s);
  if (CHECK) { ok(errs.length === 0, '[桌面] 没有 JS 报错', errs.slice(0, 3)); await ctx.close(); return; }

  await page.click('#play [data-lens="why"]'); await page.waitForTimeout(250);
  ok((await sc(page)).lens === 'why' && await page.evaluate(() => document.querySelector('.nf.cur .scn').classList.contains('l-why')), '[桌面] 点「为什么」切到为什么图层');
  await shot(page, 'd2-why');
  await page.click('#play [data-lens="trap"]'); await page.waitForTimeout(250);
  ok((await sc(page)).lens === 'trap', '[桌面] 点「易混点」切到易混点图层');
  await shot(page, 'd3-trap');
  await page.click('#play [data-lens="main"]'); await page.waitForTimeout(150);

  const parts = s.ids.filter(i => !s.ends[i]), wires = s.ids.filter(i => s.ends[i]);
  const pid = parts[Math.min(1, parts.length - 1)];
  await page.click('.nf.cur .scn .pt[data-p="' + pid + '"]'); await settle(page);
  let xp = await rect(page, '#xp'), pr = await rect(page, '.nf.cur .scn .pt[data-p="' + pid + '"]');
  ok((await sc(page)).open === pid && !!xp, '[桌面] 点部件 → 说明卡打开', pid);
  ok(xp && pr && pr.r <= xp.x + 1 && pr.x >= 0, '[桌面] 被点的部件没被说明卡挡住', { part: pr, panel: xp });
  const secs = await page.evaluate(() => [...document.querySelectorAll('#xp .xs h5')].map(e => e.textContent));
  ok(secs.length >= 3, '[桌面] 说明卡有 干什么 / 怎么设计 / 为什么 三栏', secs);
  await shot(page, 'd4-card');
  if (wires.length) {
    await page.click('.nf.cur .scn .wl[data-w="' + wires[0] + '"]'); await settle(page);
    ok((await sc(page)).open === wires[0], '[桌面] 点线上的标签 → 换成这条线的说明卡', wires[0]);
    await page.keyboard.press('ArrowRight'); await settle(page, 150);
    ok((await sc(page)).open === s.ids[(s.ids.indexOf(wires[0]) + 1) % s.ids.length], '[桌面] → 键翻到下一张说明卡');
  }
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  ok((await sc(page)).open === null && !(await rect(page, '#xp')), '[桌面] Esc 关掉说明卡');

  if (s.n) {
    await page.click('#play [data-st="1"]'); await settle(page);
    ok((await sc(page)).step === 0, '[桌面] 点 ▶ 进入第 1 步');
    const mid = Math.min(s.n - 1, Math.max(1, Math.floor(s.n / 2)));
    for (let i = 0; i < s.n; i++) {
      await page.evaluate(x => window.__cv.scene.step(x), i); await settle(page, 250);
      const fx = await page.evaluate(() => document.querySelectorAll('.nf.cur .scn .pt.fx,.nf.cur .scn .wl.fx').length);
      if (i === 0) ok(fx > 0, '[桌面] 推演时有部件 / 连线被点亮', fx);
      if (ALL) await shot(page, 'ds' + (i + 1) + '-step');
      if (i === mid) await shot(page, 'd5-step' + (i + 1));
    }
    await page.keyboard.press(','); await page.waitForTimeout(200);
    ok((await sc(page)).step === s.n - 2, '[桌面] , 键退一步');
    f = await rect(page, '.nf.cur .scn .scw'); pl = await rect(page, '#play');
    ok(f && pl && f.b <= pl.y + 1, '[桌面] 推演时机器图仍在推演条上方', { fig: f, play: pl });
    await page.evaluate(() => window.__cv.scene.step(-1));
  }
  ok(errs.length === 0, '[桌面] 没有 JS 报错', errs.slice(0, 3));
  await ctx.close();
}

async function phone(browser) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: DSF, isMobile: true, hasTouch: true });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  await page.goto(url); await page.waitForSelector('.nf.cur'); await settle(page);
  const s = await sc(page);
  ok(!!s, '[手机] 打开的这一页有机器图');
  if (!s) { await shot(page, 'p1-overview'); await ctx.close(); return; }
  ok(s.mob, '[手机] 用的是手机那套坐标（m / mw / mh）', s.mob);
  let f = await rect(page, '.nf.cur .scn .scw'), pl = await rect(page, '#play');
  ok(f && f.x >= 0 && f.r <= 390, '[手机] 机器图左右不出屏', f);
  ok(pl && pl.x >= 0 && pl.r <= 390 && pl.b <= 844, '[手机] 推演条不出屏', pl);
  await shot(page, 'p1-overview');
  await allLayouts(page, '手机', s);
  if (CHECK) { ok(errs.length === 0, '[手机] 没有 JS 报错', errs.slice(0, 3)); await ctx.close(); return; }

  const parts = s.ids.filter(i => !s.ends[i]);
  const pid = parts[Math.min(1, parts.length - 1)];
  await page.evaluate(id => { const e = document.querySelector('.nf.cur .scn .pt[data-p="' + id + '"]'); const r = e.getBoundingClientRect(); window.__in = r.top > 56 && r.bottom < innerHeight - 8; }, pid);
  if (!(await page.evaluate(() => window.__in))) { await page.evaluate(id => window.__cv.scene.open(id), pid); await settle(page); await page.evaluate(() => window.__cv.scene.close()); await settle(page, 150); }
  await page.tap('.nf.cur .scn .pt[data-p="' + pid + '"]'); await settle(page);
  let xp = await rect(page, '#xp'), pr = await rect(page, '.nf.cur .scn .pt[data-p="' + pid + '"]');
  ok((await sc(page)).open === pid && !!xp, '[手机] 点部件 → 说明卡从底部升起', pid);
  ok(xp && pr && pr.b <= xp.y + 1 && pr.y >= 48, '[手机] 被点的部件露在说明卡上方', { part: pr, panel: xp });
  ok(xp && xp.h <= 844 * .6 + 2, '[手机] 说明卡不超过屏幕六成高', xp);
  await shot(page, 'p2-card');
  await page.tap('#xp .xx'); await page.waitForTimeout(250);
  ok((await sc(page)).open === null, '[手机] 点 ✕ 关掉说明卡');

  await page.tap('#play [data-lens="why"]'); await page.waitForTimeout(200);
  ok((await sc(page)).lens === 'why', '[手机] 点「为什么」切图层');
  await page.tap('#play [data-lens="main"]'); await page.waitForTimeout(150);
  if (s.n) {
    await page.tap('#play [data-st="1"]'); await settle(page);
    ok((await sc(page)).step === 0, '[手机] 点 ▶ 进入第 1 步');
    const mid = Math.min(s.n - 1, Math.max(1, Math.floor(s.n / 2))); const hid = [];
    for (let i = 0; i < s.n; i++) {
      await page.evaluate(x => window.__cv.scene.step(x), i); await settle(page, 250);
      if (ALL) await shot(page, 'ps' + (i + 1) + '-step');
      // 每一步的主角（focus 的第一个）都得露在推演条上方：部件要整个露出来，连线露出一截就算
      pl = await rect(page, '#play');
      const vis = await page.evaluate(top => {
        const id = (window.__cv.scene.get().focus || [])[0]; if (!id) return null;
        const e = document.querySelector('.nf.cur .scn .pt[data-p="' + id + '"]'), w = e ? null : document.querySelector('.nf.cur .scn .wl[data-w="' + id + '"]');
        if (!e && !w) return null;
        const r = (e || w).getBoundingClientRect();
        return e ? (r.top >= 47 && r.bottom <= top + 1 && r.left >= -1 && r.right <= 391) : (r.bottom > 60 && r.top < top - 12 && r.right > 0 && r.left < 390);
      }, pl.y);
      if (vis === false) hid.push(i + 1);
      if (i === mid) await shot(page, 'p3-step' + (i + 1));
    }
    ok(hid.length === 0, '[手机] 每一步的主角（focus 的第一个）都在推演条上方看得见', hid.length ? '看不见的步：' + hid.join('、') : null);
    pl = await rect(page, '#play');
    ok(pl && pl.h <= 844 * .5, '[手机] 推演条不超过半屏', pl);
  }
  ok(errs.length === 0, '[手机] 没有 JS 报错', errs.slice(0, 3));
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  try { await desktop(browser); await phone(browser); } catch (e) { fail++; console.log('FAIL 脚本中断：' + e.message.split('\n')[0]); }
  await browser.close();
  lay.forEach((v, k) => console.log('LAYOUT ' + k + '（' + (v.at.length === seen[v.vp].length ? '所有图层和步骤' : v.at.join('、')) + '）'));
  console.log('\n交互自检 ' + pass + ' PASS / ' + fail + ' FAIL；排版问题 ' + lay.size + ' 条' + (lay.size ? '（每条都要改掉，或说明为什么可以留）' : ''));
  if (!CHECK) console.log('截图在 ' + path.resolve(outDir));
  process.exit(fail ? 1 : 0);
})();
