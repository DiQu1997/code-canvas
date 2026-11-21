#!/usr/bin/env node
// shot.js —— 给指定节点截图（桌面 1440×900 + 手机 390×844，整页），并做机械检查。
// 用法: node shot.js <canvas.html> <输出目录> <节点id> [节点id...] [--sheet] [--nav]
//   每个节点输出 <id>.desktop.png 和 <id>.mobile.png，并打印：控制台错误、是否横向溢出。
//   --sheet  手机整页图很长时，另存 <id>.mobile.sheet.png：把长图切成几栏并排，方便一眼看完。
//   --nav    额外跑一遍交互自测（地图/面包屑/上一层/图例/锚点/逐帧/下钻…），打印 PASS/FAIL。
// 退出码：有控制台错误、横向溢出、节点不存在或 FAIL 时为 1。
const path = require('path'), fs = require('fs');
const { chromium } = require('playwright');

const argv = process.argv.slice(2);
const args = argv.filter(a => !a.startsWith('--'));
const flags = new Set(argv.filter(a => a.startsWith('--')));
if (args.length < 3) {
  console.error('用法: node shot.js <canvas.html> <输出目录> <节点id> [节点id...] [--sheet] [--nav]');
  process.exit(2);
}
const [htmlPath, outDir, ...ids] = args;
const url = 'file://' + path.resolve(htmlPath);
const DEV = [
  { name: 'desktop', opts: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 } },
  { name: 'mobile', opts: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true } },
];
const COL_H = 1100, GAP = 8;
let bad = 0;

function listen(page, errs) {
  page.on('pageerror', e => errs.push('pageerror: ' + String(e.message || e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
}

async function sheet(browser, png, W, H, file) {
  const cols = Math.ceil(H / COL_H);
  const pg = await browser.newPage({ viewport: { width: cols * (W + GAP) + GAP, height: Math.min(H, COL_H) + 2 * GAP }, deviceScaleFactor: 1 });
  let h = '<style>body{margin:0;background:#3a3f4b}div{position:absolute;top:' + GAP + 'px;width:' + W + 'px;background-image:url(data:image/png;base64,' + png.toString('base64') + ');background-repeat:no-repeat}</style>';
  for (let i = 0; i < cols; i++) {
    h += '<div style="left:' + (GAP + i * (W + GAP)) + 'px;height:' + Math.min(COL_H, H - i * COL_H) + 'px;background-position:0 -' + (i * COL_H) + 'px"></div>';
  }
  await pg.setContent(h);
  await pg.screenshot({ path: file });
  await pg.close();
}

async function shoot(browser, dev) {
  const ctx = await browser.newContext(dev.opts);
  for (const id of ids) {
    const page = await ctx.newPage(), errs = [];
    listen(page, errs);
    await page.goto(url + '#/' + id);
    await page.waitForSelector('#main h1', { timeout: 5000 }).catch(() => errs.push('页面没渲染出标题（脚本可能中途报错）'));
    await page.waitForTimeout(250);
    const info = await page.evaluate(() => {
      const de = document.documentElement, over = [];
      if (de.scrollWidth > de.clientWidth) {
        document.querySelectorAll('body *').forEach(e => {
          const r = e.getBoundingClientRect();
          if (r.width && r.right > de.clientWidth + 1 && over.length < 6) {
            over.push(e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '') + '(右缘 ' + Math.round(r.right) + ')');
          }
        });
      }
      const want = decodeURIComponent(location.hash.replace(/^#\//, '').split('~')[0]);
      return { known: window.__cv ? window.__cv.has(want) : null, sw: de.scrollWidth, cw: de.clientWidth, h: de.scrollHeight,
        h1: (document.querySelector('#main h1') || {}).textContent || '', over };
    });
    const file = path.join(outDir, id + '.' + dev.name + '.png');
    const png = await page.screenshot({ path: file, fullPage: true });
    let extra = '';
    if (flags.has('--sheet') && dev.name === 'mobile' && info.h > COL_H) {
      const sf = path.join(outDir, id + '.mobile.sheet.png');
      await sheet(browser, png, info.cw, info.h, sf);
      extra = '  分栏图 ' + sf;
    }
    const ovf = info.sw > info.cw;
    if (ovf || errs.length || info.known === false) bad++;
    console.log('[' + dev.name.padEnd(7) + '] ' + id + '  ' + info.cw + '×' + info.h +
      '  横向溢出: ' + (ovf ? '有（scrollWidth ' + info.sw + ' > ' + info.cw + '）' : '无') +
      '  控制台错误: ' + errs.length + (info.known === false ? '  !! 画布里没有这个节点 id（显示的是顶层）' : '') +
      '  → ' + file + extra);
    if (ovf) console.log('    撑宽页面的元素（前几个）: ' + info.over.join('  '));
    errs.forEach(e => console.log('    ' + e));
    await page.close();
  }
  await ctx.close();
}

async function navTest(browser, dev) {
  const ctx = await browser.newContext(dev.opts), page = await ctx.newPage(), errs = [], T = dev.name + '：';
  listen(page, errs);
  const ok = (name, c) => { if (!c) bad++; console.log((c ? '  PASS ' : '  FAIL ') + T + name); };
  const cur = () => page.evaluate(() => window.__cv.cur());
  const vis = sel => page.evaluate(s => { const e = document.querySelector(s); if (!e || e.hidden) return false; const r = e.getBoundingClientRect(); return getComputedStyle(e).display !== 'none' && r.width > 0 && r.height > 0; }, sel);
  const has = async s => (await page.locator(s).count()) > 0;
  const tick = () => page.waitForTimeout(120);
  const go = async id => { await page.evaluate(h => { location.hash = h; }, '#/' + id); await tick(); };
  await page.goto(url);
  await page.waitForSelector('#main h1');
  const root = await cur();
  if (dev.name === 'mobile') {
    ok('地图默认收起', !(await vis('#side')));
    await page.click('#bmap'); await tick();
    ok('点「☰ 地图」展开，且标出当前位置', (await vis('#side')) && (await has('#side a.ol.cur')));
  } else {
    ok('侧边地图常驻，且标出当前位置', (await vis('#side')) && (await page.locator('#side a.ol.cur').count()) === 1);
  }
  if ((await page.locator('#side a.ol').count()) > 1) {
    await page.locator('#side a.ol').nth(1).click(); await tick();
    ok('在地图里点一项能跳过去' + (dev.name === 'mobile' ? '并收起地图' : ''), (await cur()) !== root && (dev.name !== 'mobile' || !(await vis('#side'))));
  }
  await go(root);
  if (await has('.kid')) {
    await page.locator('.kid').first().click(); await tick();
    const c1 = await cur();
    ok('点子节点卡片下钻', c1 !== root);
    await page.locator('.navrow a.nb2').first().click(); await tick();
    ok('「↑ 上一层」回到上层', (await cur()) === root);
    await go(c1); await page.locator('#crumb a').first().click(); await tick();
    ok('点面包屑回到顶层', (await cur()) === root);
    await go(c1); await page.keyboard.press('u'); await tick();
    ok('键盘 u 回上一层', (await cur()) === root);
  }
  await page.click('#bleg'); await tick();
  ok('图例能打开', await vis('#legend'));
  await page.click('#bleg'); await tick();   // 手机上图例几乎盖满正文，所以用「再点一次按钮」来收
  ok('再点一次「图例」收起', !(await vis('#legend')));
  for (const id of ids) {
    await go(id);
    if ((await cur()) !== id) { ok(id + ' 存在', false); continue; }
    const depth = await page.locator('.where a, .where b').count();
    ok(id + '：页首路径条显示第 ' + depth + ' 层，面包屑非空', depth >= 1 && (await has('#crumb b')));
    if (await has('#main a.ref')) { await page.locator('#main a.ref').first().click(); await tick(); ok(id + '：点锚点展开原文', await has('#main .snip .cl')); }
    if (await has('#main a.cm')) { await page.locator('#main a.cm').first().click(); await tick(); ok(id + '：点提交展开日期标题', await has('#main .cinfo')); }
    if (await has('#main .q')) { await page.locator('#main .q').first().click(); await tick(); ok(id + '：点引文展开全文', await has('#main .q.open')); }
    if (await has('details.more summary')) { await page.locator('details.more summary').first().click(); await tick(); ok(id + '：more 折叠能展开', await has('details.more[open]')); }
    if (await has('.tgl')) { await page.locator('.tgl').first().click(); await tick(); ok(id + '：「只看代码」隐藏批注', await has('figure.code.nonotes')); await page.locator('.tgl').first().click(); }
    if (await has('.fr-nav .fx')) { await page.locator('.fr-nav .fx').first().click(); await tick(); ok(id + '：frames「下一步」翻到第 2 步', (await page.locator('.fr-top').first().innerText()).indexOf('第 2 /') >= 0); }
    if (await has('.dbx')) ok(id + '：diagram 画出了框和线', (await has('.dgi polyline')) && (await page.locator('.dgw').first().evaluate(e => e.clientHeight > 40)));
    if (await has('.dbx.go')) { await page.locator('.dbx.go').first().click(); await tick(); ok(id + '：diagram 里带 › 的框能下钻', (await cur()) !== id); await go(id); }
    if (await has('.fs[data-to] .ft')) { await page.locator('.fs[data-to] .ft').first().click(); await tick(); ok(id + '：flow 步骤能下钻', (await cur()) !== id); await go(id); }
    if (await has('a.goto')) { await page.locator('a.goto').first().click(); await tick(); ok(id + '：collect「去看」跳到批注并高亮', (await cur()) !== id && (await has('.note.flash'))); await go(id); }
  }
  if (errs.length) { bad++; console.log('  交互过程中的控制台错误 ' + errs.length + ' 条:'); errs.forEach(e => console.log('    ' + e)); }
  await ctx.close();
}

(async () => {
  if (!fs.existsSync(path.resolve(htmlPath))) { console.error('找不到 ' + htmlPath); process.exit(2); }
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const dev of DEV) await shoot(browser, dev);
    if (flags.has('--nav')) { console.log('---- 交互自测 ----'); for (const dev of DEV) await navTest(browser, dev); }
  } finally {
    await browser.close();
  }
  console.log(bad ? '结论：有 ' + bad + ' 处问题（见上）' : '结论：没有控制台错误，没有横向溢出');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
