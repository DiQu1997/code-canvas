// End-to-end test for reader profile + personalized rewrites, run against a
// live serve.py backed by a stub CLI (echo), so /ask round-trips for real.
// Usage: node tests/personalize.mjs demo/nano-vllm.html
// Env: CANVAS_TEST_PW (playwright pkg path if not resolvable), CANVAS_TEST_CHROMIUM.
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const html = process.argv[2];
if (!html) { console.error('usage: node tests/personalize.mjs <canvas.html>'); process.exit(2); }
const PORT = 8351;
const root = dirname(dirname(fileURLToPath(import.meta.url)));

const server = spawn('python3', [resolve(root, 'serve.py'), resolve(html),
  '--port', String(PORT), '--cli-bin', '/bin/echo'], { stdio: 'ignore' });
const alive = async () => {
  for (let i = 0; i < 30; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/__alive`); if (r.ok) return true; }
    catch (e) {}
    await new Promise(r => setTimeout(r, 200));
  }
  return false;
};
if (!await alive()) { server.kill(); console.error('FAIL server did not start'); process.exit(1); }

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.goto(`http://127.0.0.1:${PORT}/`);
await page.waitForTimeout(900);

// rewrite buttons hidden without a profile
check('rewrite hidden without profile',
  !(await page.isVisible('.bbtn[data-act="rewrite"]')));

// set a profile
await page.click('#profile-btn');
await page.fill('#pp-input', '测试读者：新人，Python 一般，要类比，中文');
await page.click('#pp-save');
check('body gains has-profile', await page.$eval('body', b => b.classList.contains('has-profile')));

// open an explanation and rewrite it through the live stub
await page.click('.bbtn[data-act="explain"]');
const orig = await page.textContent('.blk.explained .bx-text');
await page.click('.blk.explained .bx-tools .bbtn[data-act="rewrite"]');
await page.waitForTimeout(1200);
const after = await page.textContent('.blk.explained .bx-text');
check('rewrite replaces text via /ask', after !== orig && after.includes('读者画像'));
check('personalized badge shown', await page.isVisible('.blk.explained .rw-badge'));

// revert restores the original
await page.click('.blk.explained .bbtn[data-act="revert"]');
check('revert restores original', (await page.textContent('.blk.explained .bx-text')) === orig);

// QA prompt carries the profile (clipboard-free check: live send via stub echo)
const bar = await page.$('.bbar');
await page.click('.bbtn[data-act="ask"]');
await page.fill('#qa-input', '这个块干嘛的？');
await page.click('#qa-send');
await page.waitForTimeout(1200);
const answer = await page.textContent('.qa-a:last-child');
check('QA answer includes profile', answer.includes('读者画像'));

console.log(results.join('\n'));
await browser.close();
server.kill();
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
