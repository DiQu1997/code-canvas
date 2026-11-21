// shot_scene.js —— 机器图（scene.html）的自检 + 固定一组截图
// 用法：node shot_scene.js <scene.html> <输出目录> [--check] [--dsf2] [--steps] [--node <节点id>] [--all]
//   --check  只做机械排版检查（不截图、不点交互，几秒钟）—— 改坐标时反复跑这个，不花看图的钱
//   --node   查下层某个节点（不写就是顶层）：它有机器图就全套检查；是源码页 / 文字页就只截图。截图文件名前面带上节点 id
//   --all    顶层和每个下层节点各查一遍（「未展开」的空页跳过）
// 做四件事：① 交互自检（图层 / 说明卡 / 推演，桌面 1440×900 + 手机 390×844 带触摸）
//          ② 机械排版检查（线标签压框、线穿过别的框、框里放不下、字被截断）—— 打印成 LAYOUT 行
//          ③ 固定 8 张截图（d1~d5 桌面，p1~p3 手机）；--steps 额外把每一步推演各截一张
//          ④ 图上有「钻进去」时：真点一次，看是不是到了下层、下层是不是空页、能不能回来（多截 d6 / p4 两张）
//          ⑤ 每个节点打开后先查「量到的位置 = 点得中的位置」（见 geom）—— 这条是查舞台自己的，报 FAIL 不是内容的错
//   --dsf2   按两倍屏幕密度跑（截图给人看时用；交互和 ⑤ 在两种密度下都该过）
// 只在无头 Chromium 里跑：iOS Safari 的手势和渲染这里验不到。
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
const argv = process.argv.slice(2), pos = [], flags = {};
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--node') flags.node = argv[++i];
  else if (argv[i].slice(0, 2) === '--') flags[argv[i].slice(2)] = true;
  else pos.push(argv[i]);
}
const [file, outDir] = pos;
const DSF = flags.dsf2 ? 2 : 1, ALL = !!flags.steps, CHECK = !!flags.check, EVERY = !!flags.all;
if (!file || !outDir || flags.node === undefined && argv.indexOf('--node') >= 0) { console.error('用法：node shot_scene.js <scene.html> <输出目录> [--check] [--dsf2] [--steps] [--node <节点id>] [--all]'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });
const base = 'file://' + path.resolve(file);
let NODE = null;      // 正在查哪个节点的机器图；null = 顶层
const url = () => base + (NODE ? '#/' + NODE : '');
const tg = t => NODE ? t + ' · ' + NODE : t;
let pass = 0, fail = 0; const lay = new Map(), seen = {};
function ok(c, msg, extra) { if (NODE) msg = msg.replace(/^\[(桌面|手机)\]/, '[$1 · ' + NODE + ']'); if (c) { pass++; console.log('PASS ' + msg); } else { fail++; console.log('FAIL ' + msg + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')); } }
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
const shot = (page, name) => CHECK ? null : page.screenshot({ path: path.join(outDir, (NODE ? NODE + '-' : '') + name + '.png') });

// 机械排版检查：在页面里量真实像素
async function layout(page, vp, where) {
  (seen[vp] = seen[vp] || []).push(where);
  const out = await page.evaluate(() => {
    const s = window.__cv.scene.get(), fig = document.querySelector('.nf.cur > .card .scn'), out = [];
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

// 「量到的位置」是不是「点得中的位置」：抽当前页里几个看得见的可点元素，从它量到的矩形中心往上下左右各扫一遍，
// 看真正能点中它的范围有没有跑到矩形外面去。页面是一层套一层缩小摆进去的，浏览器画一页时会把它的落点取整，
// 排版位置要是带小数，放大几十上百倍以后「量到的」和「画出来的」能差几十像素：镜头停歪、按量到的位置点会点空。
// 舞台已经把落点补成整数（template_zoom.html 的 snapAll），这一条是防它再坏。每个节点、每种屏幕密度都查。
async function geom(page, vp) {
  const r = await page.evaluate(() => {
    const f = document.querySelector('.nf.cur'), W = document.getElementById('world'), vw = innerWidth, vh = innerHeight, out = { n: 0, bad: [] };
    const pick = sel => [...f.querySelectorAll(sel)].filter(e => e.closest('.nf') === f);
    let els = pick('.pt,.wl,a.ref,a.cm,.q,button.tgl,.sx,summary');
    if (els.length < 3) els = els.concat(pick(':scope > .card > *, :scope > .kz > .kzh'));
    for (const e of els) {
      if (out.n >= 6) break;
      const b = e.getBoundingClientRect();
      if (b.width < 8 || b.height < 8 || b.left < 2 || b.top < 50 || b.right > vw - 2 || b.bottom > vh - 2) continue;
      const cx = b.left + b.width / 2, cy = b.top + b.height / 2, c = document.elementFromPoint(cx, cy);
      if (c && !W.contains(c)) continue;                        // 被固定面板盖住的不算
      const scan = (ax, half, oy) => {
        let lo = null, hi = null;
        for (let d = -Math.ceil(half) - 160; d <= Math.ceil(half) + 160; d++) { const t = document.elementFromPoint(cx + (ax ? 0 : d), cy + (ax ? d : oy)); if (t && e.contains(t)) { if (lo === null) lo = d; hi = d; } }
        return lo === null ? null : [lo, hi];
      };
      // 先竖着扫出「真正点得中的那一段」，再在那一段的正中间横着扫
      const y = scan(1, b.height / 2, 0), x = y ? scan(0, b.width / 2, (y[0] + y[1]) / 2) : null, nm = e.tagName.toLowerCase() + (e.classList[0] ? '.' + e.classList[0] : '');
      const inY = y && y[0] >= -b.height / 2 - 2 && y[1] <= b.height / 2 + 2, inX = x && x[0] >= -b.width / 2 - 2 && x[1] <= b.width / 2 + 2;
      if (c && !e.contains(c) && inY && inX) continue;          // 中心被页面里别的东西压住、其余都在矩形里：是遮挡，不是位置偏
      out.n++;
      if (!y || !x) { out.bad.push(nm + '：从量到的中心往上下 160px 都点不中它'); continue; }
      if (!inY || !inX) out.bad.push(nm + '：点得中的位置比量到的位置偏了 竖向 ' + ((y[0] + y[1]) / 2) + 'px、横向 ' + ((x[0] + x[1]) / 2) + 'px（这一页放大 ' + Math.round(window.__cv.t().k * 10) / 10 + ' 倍）');
    }
    return out;
  });
  if (!r.n) { console.log('INFO [' + tg(vp) + '] 这一页当前屏里没有可抽查的元素，「量到的位置 = 点得中的位置」没查'); return; }
  ok(r.bad.length === 0, '[' + vp + '] 量到的位置就是点得中的位置（抽了 ' + r.n + ' 个元素，上下左右各扫一遍）', r.bad.slice(0, 3));
}

// 没有机器图的下层页（源码页 / 文字页）：只看是不是空页、有没有报错，截一张（手机截整页）
async function plain(page, vp, name, errs) {
  const st = await page.evaluate(() => { const f = document.querySelector('.nf.cur'); return { stub: f.classList.contains('stub'), code: f.querySelectorAll(':scope > .card figure.code').length }; });
  console.log('INFO [' + tg(vp) + '] 这一页没有机器图（源码页 / 文字页），只截图：代码块 ' + st.code + ' 段');
  ok(!st.stub, '[' + vp + '] 这一页有内容（不是「未展开」的空页）');
  ok(errs.length === 0, '[' + vp + '] 没有 JS 报错', errs.slice(0, 3));
  if (!CHECK) await page.screenshot({ path: path.join(outDir, NODE + '-' + name + '.png'), fullPage: vp === '手机' });
}

// 「钻进去」：把每张说明卡打开看一眼有没有按钮；有的话真点第一个，看到没到下层、下层是不是空页、回不回得来
async function dive(page, vp, s, touch) {
  const here = await page.evaluate(() => window.__cv.cur()), gos = {};
  for (const id of s.ids) {
    await page.evaluate(x => window.__cv.scene.open(x), id); await page.waitForTimeout(40);
    const g = await page.evaluate(() => [...document.querySelectorAll('#xp .xgo[data-go]')].map(e => e.dataset.go));
    if (g.length) gos[id] = g;
  }
  await page.evaluate(() => window.__cv.scene.close()); await settle(page, 150);
  const from = Object.keys(gos);
  if (!from.length) { console.log('INFO [' + tg(vp) + '] 这张图上没有带「钻进去」的说明卡（这张图下面没有再下一层就不用管）'); return; }
  const tos = [...new Set([].concat(...from.map(k => gos[k])))];
  const stubs = await page.evaluate(ids => ids.filter(i => { const f = document.querySelector('.nf[data-id="' + i + '"]'); return !f || f.classList.contains('stub'); }), tos);
  ok(stubs.length === 0, '[' + vp + '] 「钻进去」指到的 ' + tos.length + ' 个页面都有内容（不是「未展开」的空页）', stubs);
  const id = from[0], to = gos[id][0], btn = '#xp .xgo[data-go="' + to + '"]';
  await page.evaluate(x => window.__cv.scene.open(x), id); await settle(page);
  await page.evaluate(q => document.querySelector(q).scrollIntoView({ block: 'nearest' }), btn); await page.waitForTimeout(120);
  let hit = true;
  try { if (touch) await page.tap(btn, { timeout: 4000 }); else await page.click(btn, { timeout: 4000 }); } catch (e) { hit = false; }
  ok(hit, '[' + vp + '] 说明卡「' + id + '」里的「钻进去」按钮点得到（没被别的东西挡住）');
  if (!hit) { await page.evaluate(() => window.__cv.scene.close()); return; }
  await settle(page);
  const st = await page.evaluate(() => ({ cur: window.__cv.cur(), xp: !document.getElementById('xp').hidden }));
  ok(st.cur === to, '[' + vp + '] 点「钻进去」→ 到了下层「' + to + '」', st);
  ok(!st.xp, '[' + vp + '] 到了下层，上一层的说明卡收起来了', st);
  await shot(page, (touch ? 'p4' : 'd6') + '-dive');
  await page.evaluate(x => window.__cv.go(x), here); await settle(page);
  ok(await page.evaluate(() => window.__cv.cur()) === here, '[' + vp + '] 能回到这张图');
}

async function desktop(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: DSF });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  await page.goto(url()); await page.waitForSelector('.nf.cur'); await settle(page);
  if (NODE) ok(await page.evaluate(() => window.__cv.cur()) === NODE, '[桌面] 打开的是要查的节点', await page.evaluate(() => window.__cv.cur()));
  await geom(page, '桌面');
  let s = await sc(page);
  if (!s && NODE) { await plain(page, '桌面', 'd-page', errs); await ctx.close(); return; }
  ok(!!s, '[桌面] 打开的这一页有机器图');
  if (!s) { await shot(page, 'd1-overview'); await ctx.close(); return; }
  ok(!s.mob, '[桌面] 用的是桌面那套坐标', s.mob);
  let f = await rect(page, '.nf.cur > .card .scn .scw'), pl = await rect(page, '#play');
  ok(f && pl && f.x >= 0 && f.r <= 1440 && f.y >= 48 && f.b <= pl.y + 1, '[桌面] 整张机器图在推演条上方、一屏看全', { fig: f, play: pl });
  await shot(page, 'd1-overview');
  await allLayouts(page, tg('桌面'), s);
  if (CHECK) { ok(errs.length === 0, '[桌面] 没有 JS 报错', errs.slice(0, 3)); await ctx.close(); return; }

  await page.click('#play [data-lens="why"]'); await page.waitForTimeout(250);
  ok((await sc(page)).lens === 'why' && await page.evaluate(() => document.querySelector('.nf.cur > .card .scn').classList.contains('l-why')), '[桌面] 点「为什么」切到为什么图层');
  await shot(page, 'd2-why');
  await page.click('#play [data-lens="trap"]'); await page.waitForTimeout(250);
  ok((await sc(page)).lens === 'trap', '[桌面] 点「易混点」切到易混点图层');
  await shot(page, 'd3-trap');
  await page.click('#play [data-lens="main"]'); await page.waitForTimeout(150);

  const parts = s.ids.filter(i => !s.ends[i]), wires = s.ids.filter(i => s.ends[i]);
  const pid = parts[Math.min(1, parts.length - 1)];
  await page.click('.nf.cur > .card .scn .pt[data-p="' + pid + '"]'); await settle(page);
  let xp = await rect(page, '#xp'), pr = await rect(page, '.nf.cur > .card .scn .pt[data-p="' + pid + '"]');
  ok((await sc(page)).open === pid && !!xp, '[桌面] 点部件 → 说明卡打开', pid);
  ok(xp && pr && pr.r <= xp.x + 1 && pr.x >= 0, '[桌面] 被点的部件没被说明卡挡住', { part: pr, panel: xp });
  const secs = await page.evaluate(() => [...document.querySelectorAll('#xp .xs h5')].map(e => e.textContent));
  ok(secs.length >= 3, '[桌面] 说明卡有 干什么 / 怎么设计 / 为什么 三栏', secs);
  await shot(page, 'd4-card');
  if (wires.length) {
    await page.click('.nf.cur > .card .scn .wl[data-w="' + wires[0] + '"]'); await settle(page);
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
      const fx = await page.evaluate(() => document.querySelectorAll('.nf.cur > .card .scn .pt.fx,.nf.cur > .card .scn .wl.fx').length);
      if (i === 0) ok(fx > 0, '[桌面] 推演时有部件 / 连线被点亮', fx);
      if (ALL) await shot(page, 'ds' + (i + 1) + '-step');
      if (i === mid) await shot(page, 'd5-step' + (i + 1));
    }
    await page.keyboard.press(','); await page.waitForTimeout(200);
    ok((await sc(page)).step === s.n - 2, '[桌面] , 键退一步');
    f = await rect(page, '.nf.cur > .card .scn .scw'); pl = await rect(page, '#play');
    ok(f && pl && f.b <= pl.y + 1, '[桌面] 推演时机器图仍在推演条上方', { fig: f, play: pl });
    await page.evaluate(() => window.__cv.scene.step(-1));
  }
  await dive(page, '桌面', s, false);
  ok(errs.length === 0, '[桌面] 没有 JS 报错', errs.slice(0, 3));
  await ctx.close();
}

async function phone(browser) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: DSF, isMobile: true, hasTouch: true });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  await page.goto(url()); await page.waitForSelector('.nf.cur'); await settle(page);
  if (NODE) ok(await page.evaluate(() => window.__cv.cur()) === NODE, '[手机] 打开的是要查的节点', await page.evaluate(() => window.__cv.cur()));
  await geom(page, '手机');
  const s = await sc(page);
  if (!s && NODE) { await plain(page, '手机', 'p-page', errs); await ctx.close(); return; }
  ok(!!s, '[手机] 打开的这一页有机器图');
  if (!s) { await shot(page, 'p1-overview'); await ctx.close(); return; }
  ok(s.mob, '[手机] 用的是手机那套坐标（m / mw / mh）', s.mob);
  let f = await rect(page, '.nf.cur > .card .scn .scw'), pl = await rect(page, '#play');
  ok(f && f.x >= 0 && f.r <= 390, '[手机] 机器图左右不出屏', f);
  ok(pl && pl.x >= 0 && pl.r <= 390 && pl.b <= 844, '[手机] 推演条不出屏', pl);
  await shot(page, 'p1-overview');
  await allLayouts(page, tg('手机'), s);
  if (CHECK) { ok(errs.length === 0, '[手机] 没有 JS 报错', errs.slice(0, 3)); await ctx.close(); return; }

  const parts = s.ids.filter(i => !s.ends[i]);
  const pid = parts[Math.min(1, parts.length - 1)];
  await page.evaluate(id => { const e = document.querySelector('.nf.cur > .card .scn .pt[data-p="' + id + '"]'); const r = e.getBoundingClientRect(); window.__in = r.top > 56 && r.bottom < innerHeight - 8; }, pid);
  if (!(await page.evaluate(() => window.__in))) { await page.evaluate(id => window.__cv.scene.open(id), pid); await settle(page); await page.evaluate(() => window.__cv.scene.close()); await settle(page, 150); }
  await page.tap('.nf.cur > .card .scn .pt[data-p="' + pid + '"]'); await settle(page);
  let xp = await rect(page, '#xp'), pr = await rect(page, '.nf.cur > .card .scn .pt[data-p="' + pid + '"]');
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
        const e = document.querySelector('.nf.cur > .card .scn .pt[data-p="' + id + '"]'), w = e ? null : document.querySelector('.nf.cur > .card .scn .wl[data-w="' + id + '"]');
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
    await page.evaluate(() => window.__cv.scene.step(-1)); await settle(page, 150);
  }
  await dive(page, '手机', s, true);
  ok(errs.length === 0, '[手机] 没有 JS 报错', errs.slice(0, 3));
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  let subs = [], rest = [];      // 下层节点：带机器图的 / 不带的（源码页、文字页）
  try {
    const pg = await browser.newPage(); await pg.goto(base); await pg.waitForSelector('.nf.cur');
    const all = await pg.evaluate(() => [...document.querySelectorAll('.nf')].filter(e => e.dataset.id !== window.__cv.cur() && !e.classList.contains('stub')).map(e => ({ id: e.dataset.id, sc: e.classList.contains('sc') })));
    subs = all.filter(x => x.sc).map(x => x.id); rest = all.filter(x => !x.sc).map(x => x.id);
    await pg.close();
    if (flags.node && !all.some(x => x.id === flags.node)) console.log('注意：页面里没有「' + flags.node + '」这个有内容的节点（有：' + (all.map(x => x.id).join('、') || '无') + '）');
    for (const n of (EVERY ? [null].concat(subs, rest) : [flags.node || null])) {
      NODE = n;
      if (EVERY) console.log('\n—— ' + (n ? '下层节点 ' + n : '顶层') + ' ——');
      await desktop(browser); await phone(browser);
    }
  } catch (e) { fail++; console.log('FAIL 脚本中断：' + e.message.split('\n')[0]); }
  await browser.close();
  lay.forEach((v, k) => console.log('LAYOUT ' + k + '（' + (v.at.length === seen[v.vp].length ? '所有图层和步骤' : v.at.join('、')) + '）'));
  console.log('\n交互自检 ' + pass + ' PASS / ' + fail + ' FAIL；排版问题 ' + lay.size + ' 条' + (lay.size ? '（每条都要改掉，或说明为什么可以留）' : ''));
  if (!EVERY && !flags.node && (subs.length || rest.length)) console.log('这次只查了顶层那张图。下层还有：带机器图的 ' + (subs.join('、') || '无') + '；源码页 / 文字页 ' + (rest.join('、') || '无') + ' —— 用 --node <id> 单独查（源码页只截图），或 --all 一次全查');
  if (!CHECK) console.log('截图在 ' + path.resolve(outDir));
  process.exit(fail ? 1 : 0);
})();
