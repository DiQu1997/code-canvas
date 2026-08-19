// Plan-canvas regression: ghost cards, intent badges, plan wires, delta
// badges on annotated diffs, compare.py north-star detection, validate rules.
// Usage: node tests/plan.mjs   (renders demo/cache-plan.html + cache-diff-checked.html first via python)
// Env: CANVAS_TEST_PW, CANVAS_TEST_CHROMIUM.
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
const { chromium } = await import(process.env.CANVAS_TEST_PW || 'playwright');

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const results = [];
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`);
const py = (args, opts) => spawnSync('python3', args, { cwd: root, encoding: 'utf8', ...opts });

// -- renderer assertions on the two demo canvases --
const exe = process.env.CANVAS_TEST_CHROMIUM || '/opt/pw-browsers/chromium';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });

await page.goto('file://' + resolve(root, 'demo/cache-plan.html'));
await page.waitForTimeout(800);
check('ghost card rendered dashed', await page.$eval('#card-jittered', el => el.classList.contains('ghost')));
check('ghost card has 新建 badge', await page.$eval('#card-jittered .pbadge.add', el => el.textContent === '新建'));
check('modify card has 将改 badge', await page.$eval('#card-handler .pbadge.modify', el => el.textContent === '将改'));
check('plan wire drawn dashed green', await page.$('svg path.wire.plan-add') !== null);
check('plan legend present', (await page.textContent('.legend')).includes('幽灵卡'));

await page.goto('file://' + resolve(root, 'demo/cache-diff-checked.html'));
await page.waitForTimeout(800);
check('offplan badge on store card',
  (await page.$eval('#card-store .pbadge.offplan', el => el.textContent)).includes('计划外'));
check('delta legend present', (await page.textContent('.legend')).includes('计划要动、实际没动'));
await browser.close();

// -- compare.py: north-star — off-plan change must be caught --
const cmp = py(['compare.py', 'demo/cache-plan.json', 'demo/cache-diff.json']);
check('compare exits 2 on divergence', cmp.status === 2);
check('compare names offplan card', cmp.stdout.includes('计划外') && cmp.stdout.includes('store'));
check('compare catches unlanded plan wire',
  cmp.stdout.includes('计划线未落地') && cmp.stdout.includes('handler → jittered'));

// happy path: fix the diff so it matches the plan → exit 0
const tmp = mkdtempSync(join(tmpdir(), 'cmp-'));
const diff = JSON.parse(readFileSync(join(root, 'demo/cache-diff.json'), 'utf8'));
delete diff.cards.find(c => c.id === 'store').diff;             // store untouched
diff.wires.push({ id: 'w-plan-jit', kind: 'call',               // planned wire landed
  from: { card: 'handler' }, to: { card: 'jittered' } });
writeFileSync(join(tmp, 'diff-ok.json'), JSON.stringify(diff));
const ok = py(['compare.py', 'demo/cache-plan.json', join(tmp, 'diff-ok.json')]);
check('compare exits 0 when plan matches', ok.status === 0 && ok.stdout.includes('一致'));

// -- compare --repo: git 改动文件必须被 diff 画布覆盖（防漏报/瞒报） --
const { execSync } = await import('child_process');
const repo = mkdtempSync(join(tmpdir(), 'cov-'));
execSync('git init -q ' + repo);
writeFileSync(join(repo, 'a.py'), 'x = 1\n');
writeFileSync(join(repo, 'b.py'), 'y = 1\n');
execSync(`git -C ${repo} add -A && git -C ${repo} commit -qm base`);
writeFileSync(join(repo, 'a.py'), 'x = 2\n');   // covered change
writeFileSync(join(repo, 'b.py'), 'y = 2\n');   // UNCOVERED drift
const covPlan = { meta: { mode: 'plan' }, cards: [
  { id: 'a', name: 'a', file: 'a.py:1', lang: 'py', layout: { col: 0, band: 0 },
    code: 'x = 1', plan: 'modify' }], steps: [] };
const covDiff = { meta: { mode: 'diff' }, cards: [
  { id: 'a', name: 'a', file: 'a.py:1', lang: 'py', layout: { col: 0, band: 0 },
    code: 'x = 2', diff: { added: [1] } }], steps: [] };
writeFileSync(join(tmp, 'cov-plan.json'), JSON.stringify(covPlan));
writeFileSync(join(tmp, 'cov-diff.json'), JSON.stringify(covDiff));
const cov = py(['compare.py', join(tmp, 'cov-plan.json'), join(tmp, 'cov-diff.json'), '--repo', repo]);
check('coverage check flags uncovered file',
  cov.status === 2 && cov.stdout.includes('画布未覆盖') && cov.stdout.includes('b.py'));
rmSync(repo, { recursive: true, force: true });

// -- validate: plan-field rules --
const plain = JSON.parse(readFileSync(join(root, 'demo/nano-vllm.json'), 'utf8'));
plain.cards[0].plan = 'modify';                                  // plan outside mode:"plan"
writeFileSync(join(tmp, 'bad1.json'), JSON.stringify(plain));
check('validate rejects plan outside plan mode',
  py(['validate.py', join(tmp, 'bad1.json')]).status === 1);
const planDoc = JSON.parse(readFileSync(join(root, 'demo/cache-plan.json'), 'utf8'));
planDoc.cards.find(c => c.id === 'handler').plan = 'bogus';
writeFileSync(join(tmp, 'bad2.json'), JSON.stringify(planDoc));
check('validate rejects bogus plan action',
  py(['validate.py', join(tmp, 'bad2.json')]).status === 1);

rmSync(tmp, { recursive: true, force: true });
console.log(results.join('\n'));
process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
