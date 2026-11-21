// shot_zoom.js —— 可缩放画布（zoom.html）的交互自检 + 截图
// 用法：node shot_zoom.js <zoom.html> <输出目录> [--dsf2]
// 桌面 1440×900 / 手机 390×844（带触摸）。只在无头 Chromium 里跑：iOS Safari 的手势和渲染这里验不到。
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
const [file, outDir] = process.argv.slice(2);
const DSF = process.argv.includes('--dsf2') ? 2 : 1;
if (!file || !outDir) { console.error('用法：node shot_zoom.js <zoom.html> <输出目录> [--dsf2]'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });
const url = 'file://' + path.resolve(file);
let pass = 0, fail = 0;
function ok(c, msg, extra) { if (c) { pass++; console.log('PASS ' + msg); } else { fail++; console.log('FAIL ' + msg + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')); } }
function listen(page, errs) {
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
}
async function settle(page, extra) {
  // 先让 hashchange 之类的异步事件落地，再要求「不在飞 + 连续两次取样位置不变」
  await page.waitForTimeout(90);
  await page.evaluate(() => { window.__lt = null; window.__ln = 0; });
  await page.waitForFunction(() => {
    const c = window.__cv; if (!c || c.flying()) { window.__ln = 0; return false; }
    const t = c.t(), k = [t.x.toFixed(2), t.y.toFixed(2), t.k.toFixed(5)].join();
    window.__ln = window.__lt === k ? window.__ln + 1 : 0; window.__lt = k; return window.__ln >= 2;
  }, null, { polling: 140, timeout: 9000 });
  await page.waitForTimeout(extra == null ? 450 : extra);
}
const cur = page => page.evaluate(() => window.__cv.cur());
const T = page => page.evaluate(() => window.__cv.t());
const rect = (page, sel) => page.evaluate(s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom }; }, sel);
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name + '.png') });
const nf = id => '.nf[data-id="' + id + '"]';

async function desktop(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: DSF });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  await page.goto(url); await page.waitForSelector('.nf.cur'); await settle(page);
  ok(await cur(page) === 'root', '[桌面] 打开落在 root');
  let r = await rect(page, nf('root'));
  ok(r && r.x >= 0 && r.r <= 1440, '[桌面] root 整页宽度在视口内', r);
  await shot(page, 'd1-root');

  // 点一块 → 飞进去
  await page.click('.tile[data-to="main"] .tl');
  await settle(page);
  ok(await cur(page) === 'main', '[桌面] 点「main」那块 → 当前页变成 main', await cur(page));
  r = await rect(page, nf('main'));
  ok(r && r.x >= -1 && r.r <= 1441 && Math.abs(r.y - 64) < 3, '[桌面] main 飞到位（贴顶、不出屏）', r);
  const km = await page.evaluate(() => window.__cv.K('main'));
  ok(km > 0.95 && km < 1.16, '[桌面] main 以接近 1:1 显示', km);
  await shot(page, 'd2-main');

  // 普通滚轮 = 平移；Ctrl+滚轮 = 缩放
  let t0 = await T(page);
  // 2 倍模拟下 Chromium 合成滚轮事件的 deltaY 会被减半（空白页同样复现），所以对「页面实际收到的量」比，不对 300 比
  await page.evaluate(() => { window.__wy = 0; addEventListener('wheel', e => { window.__wy += e.deltaY; }, { capture: true, passive: true }); });
  await page.mouse.move(700, 500); await page.mouse.wheel(0, 300); await page.waitForTimeout(250);
  let t1 = await T(page);
  const wy = await page.evaluate(() => window.__wy);
  ok(wy > 0 && Math.abs((t0.y - t1.y) - wy) < 2 && Math.abs(t1.k - t0.k) < 1e-9, '[桌面] 普通滚轮只平移（平移量 = 收到的滚轮量）、不缩放', { dy: t0.y - t1.y, wheel: wy, k0: t0.k, k1: t1.k });
  await page.keyboard.down('Control'); await page.mouse.wheel(0, -200); await page.keyboard.up('Control'); await page.waitForTimeout(300);
  let t2 = await T(page);
  ok(t2.k > t1.k * 1.05, '[桌面] Ctrl+滚轮放大', { k1: t1.k, k2: t2.k });

  // 一直缩小 → 当前页自动退回上层；再缩 → 小块只剩名字
  await page.mouse.move(720, 420);
  await page.keyboard.down('Control');
  for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 240); await page.waitForTimeout(120); }
  await page.keyboard.up('Control'); await settle(page);
  ok(await cur(page) === 'root', '[桌面] 缩小后当前页自动退回 root', await cur(page));
  await shot(page, 'd3-zoomed-out');
  await page.click('[data-act="all"]'); await settle(page);
  const far = await page.evaluate(() => document.querySelectorAll('.nf.far').length);
  ok(far > 0, '[桌面] 看全图时远处的页只留名字（far 标签 ' + far + ' 个）');
  await shot(page, 'd4-overview');

  // 深层节点 + 逐帧动画
  await page.evaluate(() => { location.hash = '#/pool.demo'; }); await settle(page);
  ok(await cur(page) === 'pool.demo', '[桌面] #/pool.demo 直达');
  r = await rect(page, nf('pool.demo'));
  ok(r && r.x >= -1 && r.r <= 1441, '[桌面] pool.demo 不出屏', r);
  const frm = nf('pool.demo') + '>.card figure.frm';
  await page.click(frm + ' .fx'); await page.waitForTimeout(120);
  const moving = await page.evaluate(s => document.querySelector(s).getAnimations({ subtree: true }).length, frm);
  await page.click(frm + ' .fx'); await page.click(frm + ' .fx'); await page.waitForTimeout(700);
  ok(await page.evaluate(s => document.querySelector(s).dataset.step, frm) === '3', '[桌面] 逐帧演示点三下到第 4 步');
  ok(moving > 0, '[桌面] 换步时格子有动画在跑（' + moving + ' 个）');
  await shot(page, 'd5-pooldemo-step4');
  await page.click(frm + ' .fa'); await page.waitForTimeout(1900);
  ok(await page.evaluate(s => document.querySelector(s).dataset.step, frm) === '4', '[桌面] 播放会自动走到下一步');
  await page.click(frm + ' .fa');

  // 批注直达：闪的必须是这一页自己的那条
  const g = await page.evaluate(() => { const a = document.querySelector('a.goto'); return a ? a.getAttribute('href') : null; });
  if (g) {
    const m = /^#\/([\w.]+)~(\w+)/.exec(g);
    await page.evaluate(h => { location.hash = h; }, g); await settle(page, 300);
    const hit = await page.evaluate(([id, k]) => { const f = document.querySelector('.nf[data-id="' + id + '"]'); const n = f.querySelector(':scope>.card [id="nt-' + k + '"]'); const all = document.querySelectorAll('.flash'); const r = n.getBoundingClientRect(); return { mine: n.classList.contains('flash'), total: all.length, y: r.top, vh: innerHeight }; }, [m[1], m[2]]);
    ok(await cur(page) === m[1] && hit.mine && hit.total === 1 && hit.y > 48 && hit.y < hit.vh, '[桌面] 批注直达 ' + g + '：闪的是本页那条且在屏内', hit);
    await shot(page, 'd6-note');
  } else ok(false, '[桌面] 没找到可测的批注直达链接');

  // 键盘
  await page.evaluate(() => { location.hash = '#/main'; }); await settle(page);
  await page.keyboard.press('ArrowRight'); await settle(page);
  const nx = await cur(page);
  await page.keyboard.press('ArrowUp'); await settle(page);
  ok(nx === 'main.skel' && await cur(page) === 'main', '[桌面] → 到顺序下一页，↑ 回上层', nx);
  // 锚点就地展开（当前页上的 文件:行号）
  const hasRef = await page.evaluate(() => { const a = document.querySelector('.nf.cur>.card a.ref'); if (!a) return false; a.scrollIntoView({ block: 'center' }); return true; });
  if (hasRef) {
    await page.evaluate(() => document.querySelector('.nf.cur>.card a.ref').click()); await page.waitForTimeout(150);
    ok(await page.evaluate(() => !!document.querySelector('.nf.cur>.card .snip')), '[桌面] 点 文件:行号 就地展开原文');
  }
  // 地图 / 图例
  await page.click('#bmap'); await page.waitForTimeout(350); await shot(page, 'd7-map');
  await page.click('#side a.ol[data-id="loop.take"]'); await settle(page);
  ok(await cur(page) === 'loop.take', '[桌面] 地图里点 loop.take 飞过去');
  await shot(page, 'd8-looptake');
  await page.click('#bleg'); await page.waitForTimeout(200); await shot(page, 'd9-legend'); await page.click('#legend .lx');
  // 拖动画布（在页面以外的底板上按住拖）
  await page.evaluate(() => { location.hash = '#/why'; }); await settle(page);
  t0 = await T(page);
  await page.mouse.move(60, 500); await page.mouse.down(); await page.mouse.move(160, 560, { steps: 6 }); await page.mouse.up(); await page.waitForTimeout(200);
  t1 = await T(page);
  ok(Math.abs(t1.x - t0.x - 100) < 3 && Math.abs(t1.y - t0.y - 60) < 3, '[桌面] 按住底板拖动 = 平移', { dx: t1.x - t0.x, dy: t1.y - t0.y });
  await shot(page, 'd10-why');
  ok(errs.length === 0, '[桌面] 控制台 0 错误', errs.slice(0, 5));
  await ctx.close();
}

async function phone(browser) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: DSF, isMobile: true, hasTouch: true });
  const page = await ctx.newPage(); const errs = []; listen(page, errs);
  const cdp = await ctx.newCDPSession(page);
  await page.goto(url); await page.waitForSelector('.nf.cur'); await settle(page);
  ok(await cur(page) === 'root' && (await page.evaluate(() => window.__cv.mode())) === 'nar/ph', '[手机] 打开落在 root，手机排版');
  let r = await rect(page, nf('root'));
  ok(r && r.x >= 0 && r.r <= 390, '[手机] root 宽度在屏内', r);
  const dock = await rect(page, '#dock');
  ok(dock && dock.x >= 0 && dock.r <= 390, '[手机] 底部按钮条不出屏', dock);
  await shot(page, 'm1-root');

  // 单指上滑：只走纵向（轴锁），松手有惯性
  let t0 = await T(page);
  const pts = (x, y) => [{ x, y, id: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pts(200, 640) });
  for (let i = 1; i <= 10; i++) { await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pts(200 + i * 2, 640 - i * 36) }); await page.waitForTimeout(16); }
  let tm = await T(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(900);
  let t1 = await T(page);
  ok(Math.abs(tm.x - t0.x) < 0.5 && tm.y < t0.y - 300, '[手机] 单指上滑只纵向移动（横向锁住）', { dx: tm.x - t0.x, dy: tm.y - t0.y });
  ok(t1.y < tm.y - 20, '[手机] 松手后有惯性继续滑', { extra: t1.y - tm.y });
  await settle(page, 200);
  await shot(page, 'm2-root-scrolled');

  // 下层按钮 → 滑到下一层的小块区；点小块飞进去
  await page.tap('[data-act="fit"]'); await settle(page);
  await page.tap('[data-act="kids"]'); await settle(page);
  await shot(page, 'm3-root-kids');
  const tl = await rect(page, '.tile[data-to="main"]');
  ok(tl && tl.y > 48 && tl.y < 800, '[手机]「下层」把小块区带进屏内', tl);
  await page.tap('.tile[data-to="main"] .tl'); await settle(page);
  ok(await cur(page) === 'main', '[手机] 点「main」小块 → 飞进 main', await cur(page));
  r = await rect(page, nf('main'));
  ok(r && r.x >= 0 && r.r <= 390 && Math.abs(r.y - 56) < 3, '[手机] main 到位、不出屏', r);
  await shot(page, 'm4-main');
  await page.tap('[data-act="next"]'); await settle(page);
  ok(await cur(page) === 'main.skel', '[手机]「下页」到 main.skel', await cur(page));
  await shot(page, 'm5-mainskel');
  await page.tap('[data-act="up"]'); await settle(page);
  ok(await cur(page) === 'main', '[手机]「上层」回 main');

  // 双指捏合放大
  t0 = await T(page);
  const two = d => [{ x: 195 - d, y: 420, id: 1 }, { x: 195 + d, y: 420, id: 2 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: two(40) });
  for (let i = 1; i <= 8; i++) { await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: two(40 + i * 8) }); await page.waitForTimeout(16); }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(300);
  t1 = await T(page);
  ok(t1.k > t0.k * 1.5, '[手机] 双指张开放大', { k0: t0.k, k1: t1.k });
  await shot(page, 'm6-pinched');

  // 逐帧演示
  await page.evaluate(() => { location.hash = '#/pool.demo'; }); await settle(page);
  const frm = nf('pool.demo') + '>.card figure.frm';
  await page.evaluate(s => document.querySelector(s).scrollIntoView, frm);
  const fr = await rect(page, frm);
  if (fr && fr.b > 760) { await page.mouse.wheel(0, fr.b - 700); await page.waitForTimeout(250); }
  for (let i = 0; i < 4; i++) { await page.tap(frm + ' .fx'); await page.waitForTimeout(120); }
  await page.waitForTimeout(700);
  ok(await page.evaluate(s => document.querySelector(s).dataset.step, frm) === '4', '[手机] 逐帧演示点到第 5 步');
  const fr2 = await rect(page, frm);
  ok(fr2 && fr2.x >= 0 && fr2.r <= 390, '[手机] 逐帧演示不出屏', fr2);
  await shot(page, 'm7-pooldemo-step5');
  await page.evaluate(() => { location.hash = '#/pool'; }); await settle(page);
  await shot(page, 'm8-pool');
  await page.tap('#bmap'); await page.waitForTimeout(350); await shot(page, 'm9-map');
  await page.tap('#side a.ol[data-id="acct"]'); await settle(page);
  ok(await cur(page) === 'acct', '[手机] 地图里点 acct 飞过去');
  await shot(page, 'm10-acct');
  await page.tap('#bleg'); await page.waitForTimeout(250); await shot(page, 'm11-legend'); await page.tap('#legend .lx');
  ok(errs.length === 0, '[手机] 控制台 0 错误', errs.slice(0, 5));
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  try { await desktop(browser); } catch (e) { fail++; console.log('FAIL [桌面] 脚本中断：' + e.message.split('\n')[0]); }
  try { await phone(browser); } catch (e) { fail++; console.log('FAIL [手机] 脚本中断：' + e.message.split('\n')[0]); }
  await browser.close();
  console.log('\n' + pass + ' PASS / ' + fail + ' FAIL  截图在 ' + outDir);
  process.exit(fail ? 1 : 0);
})();
