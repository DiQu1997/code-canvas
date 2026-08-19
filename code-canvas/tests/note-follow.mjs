// Notes must follow their target cards through dynamic push-down and never
// overlap a card. Renders tests/fixtures/squeeze.json, expands the tall top
// card (pushing the note's target down), and asserts non-overlap.
// Usage: node tests/note-follow.mjs
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';
import { mkdtempSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const out = resolve(mkdtempSync(resolve(tmpdir(), 'canvas-nf-')), 'squeeze.html');
const r = spawnSync('python3', [resolve(root, 'render.py'),
  resolve(root, 'tests/fixtures/squeeze.json'), out]);
if (r.status !== 0) { console.error('FAIL render', r.stderr.toString()); process.exit(1); }

const browser = await chromium.launch({
  executablePath: process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium',
  args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.goto('file://' + out + '#s1');
await page.waitForTimeout(800);

const boxes = await page.evaluate(() => {
  const world = document.getElementById('world');
  const wp = el => {
    let x = 0, y = 0, e = el;
    while (e && e !== world) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
    return { x, y, w: el.offsetWidth, h: el.offsetHeight };
  };
  return {
    note: wp(document.getElementById('note-nb')),
    top: wp(document.getElementById('card-top')),
    bottom: wp(document.getElementById('card-bottom')),
  };
});
const overlap = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
check('bottom card was pushed below expanded top',
  boxes.bottom.y > boxes.top.y + boxes.top.h - 5);
check('note does not overlap top card', !overlap(boxes.note, boxes.top));
check('note does not overlap bottom card', !overlap(boxes.note, boxes.bottom));
check('note stays near its target (within 260px)',
  Math.abs(boxes.note.y - boxes.bottom.y) < 260);

console.log(results.join('\n'));
await browser.close();
process.exit(results.some(x => x.startsWith('FAIL')) ? 1 : 0);
