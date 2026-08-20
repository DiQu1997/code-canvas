#!/usr/bin/env python3
"""serve.py — Code Canvas 问答服务，单画布模式或 hub 模式。

单画布（原有形态，serve 一个 HTML + 块级问答桥）：
    python3 serve.py canvas.html [--port 8340] [--cli claude|codex]
                     [--model <id>] [--repo <path>] [--cli-bin <path>]

hub（常驻服务，首页 = 生成入口，库 = 生成历史）：
    python3 serve.py --hub <画布库目录> [--host 100.x.y.z] [--port 8340] ...

hub 路由：
    GET  /                     生成表单 + 任务状态 + 画布库 +「示例」区
                               （示例 = <hub>/examples/ 子目录，不冒充产物）
    GET  /c/<name>/            某画布页（库优先，其次示例；页面内
                               __alive/ask 相对解析到本画布）
    POST /c/<name>/ask         块级问答（桥 CLI），历史存 <name>.html.qa.json
    POST /generate             三选一喂代码，后台起 claude 按 SKILL.md 管线生成：
                               {"git_url": "https://…"}   盒子上 shallow clone
                               {"repo": "/盒子上的路径"}
                               {"code": "粘贴的代码…"}     落到临时目录
                               + "ask": 主题/关注点（必填）, "name": 画布名（可选）
    GET  /jobs                 生成任务列表（JSON）

安全：默认只绑 127.0.0.1。部署在有公网 IP 的机器上时用 --host 绑
tailscale 接口地址，不要绑 0.0.0.0——/ask 和 /generate 都会花钱。
兼容 Python 3.8。
"""
import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TIMEOUT_S = 300
ERROR_NEEDLES = ("Not logged in", "Please run /login", "API key not", "rate limit", "401 Unauthorized")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

ARGS = None
SKILL_DIR = Path(__file__).resolve().parent


def parse_claude_metrics(obj: dict) -> dict:
    """claude -p --output-format json 的结果对象 → 统一指标。
    注意：订阅用户 cost 是按 API 价折算的参考值。"""
    u = obj.get("usage") or {}
    return {
        "cost_usd": round(obj.get("total_cost_usd") or 0, 4),
        "dur_s": round((obj.get("duration_ms") or 0) / 1000, 1),
        "api_s": round((obj.get("duration_api_ms") or 0) / 1000, 1),
        "turns": obj.get("num_turns"),
        "in_tok": u.get("input_tokens"),
        "out_tok": u.get("output_tokens"),
        "cache_read": u.get("cache_read_input_tokens"),
        "cache_write": u.get("cache_creation_input_tokens"),
    }


def run_cli(prompt: str) -> dict:
    if ARGS.cli == "claude":
        cmd = [ARGS.cli_bin or "claude", "-p", "--output-format", "json"]
        if ARGS.model:
            cmd += ["--model", ARGS.model]
    else:
        cmd = [ARGS.cli_bin or "codex", "exec"]
        if ARGS.model:
            cmd += ["-m", ARGS.model]
    cmd.append(prompt)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                              cwd=str(ARGS.repo) if ARGS.repo else None)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "CLI 超时"}
    except OSError as e:
        return {"ok": False, "error": "无法启动 {}: {}".format(cmd[0], e)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    metrics = None
    if ARGS.cli == "claude":
        try:  # 解析指标信封；stub（echo 等）不产 JSON 时走文本回退
            obj = json.loads(out)
            metrics = parse_claude_metrics(obj)
            out = (obj.get("result") or "").strip()
            if obj.get("is_error"):
                return {"ok": False, "error": out[:2000], "metrics": metrics}
        except ValueError:
            pass
    if proc.returncode != 0 or (out and len(out) < 400 and any(n.lower() in out.lower() for n in ERROR_NEEDLES)):
        return {"ok": False, "error": (err or out)[:2000]}
    if not out:
        return {"ok": False, "error": "CLI 返回为空" + ("（stderr: {}）".format(err[:200]) if err else "")}
    res = {"ok": True, "answer": out, "elapsed_s": round(time.time() - t0, 1)}
    if metrics:
        res["metrics"] = metrics
    return res


def persist(html_path: Path, record: dict) -> None:
    path = html_path.with_suffix(html_path.suffix + ".qa.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        data = []
    data.append(record)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- hub：画布库 ----------

def maybe_rerender(html_path: Path) -> None:
    """画布 html 是模板快照：若旁边有 json 且 html 比模板旧，自动重渲染。"""
    sib = html_path.with_suffix(".json")
    tpl = SKILL_DIR / "template" / "canvas.html"
    if not (sib.exists() and tpl.exists()):
        return
    if html_path.stat().st_mtime >= tpl.stat().st_mtime:
        return
    try:
        subprocess.run([sys.executable, str(SKILL_DIR / "render.py"), str(sib), str(html_path)],
                       capture_output=True, timeout=30)
    except Exception:
        pass  # 渲染失败就照旧 serve 旧版本


def canvas_meta(html_path: Path) -> dict:
    """列表页所需元数据：优先读同名 .json 的 meta，缺了退回文件名。"""
    meta = {"name": html_path.stem, "title": html_path.stem, "subtitle": "", "mode": ""}
    sib = html_path.with_suffix(".json")
    if sib.exists():
        try:
            m = json.loads(sib.read_text(encoding="utf-8")).get("meta", {})
            meta["title"] = m.get("title") or meta["title"]
            meta["subtitle"] = m.get("subtitle") or ""
            meta["mode"] = m.get("mode") or ""
        except Exception:
            pass
    meta["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(html_path.stat().st_mtime))
    return meta


def list_canvases() -> list:
    return sorted(ARGS.hub.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)


def list_examples() -> list:
    d = ARGS.hub / "examples"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)


def jobs_dir() -> Path:
    d = ARGS.hub / ".jobs"
    d.mkdir(exist_ok=True)
    return d


def list_jobs() -> list:
    out = []
    for mp in sorted(jobs_dir().glob("*.meta.json"), reverse=True):
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        sp = mp.with_name(mp.name.replace(".meta.json", ".status"))
        if sp.exists():
            code = sp.read_text().strip()
            meta["status"] = "done" if code == "0" else "failed({})".format(code)
        else:
            meta["status"] = "running"
        # 指标：分段计时 + claude JSON 信封（token/成本/时长）
        try:
            tm = mp.with_name(mp.name.replace(".meta.json", ".timing.json"))
            if tm.exists():
                meta["timing"] = json.loads(tm.read_text(encoding="utf-8"))
            rf = mp.with_name(mp.name.replace(".meta.json", ".result.json"))
            if rf.exists():
                meta["metrics"] = parse_claude_metrics(json.loads(rf.read_text(encoding="utf-8")))
        except Exception:
            pass
        out.append(meta)
    return out


def spawn_generate(src: dict, ask: str, name: str) -> dict:
    """src: {"kind": "git"|"path"|"code", ...}。返回 job meta。"""
    job_id = time.strftime("j%Y%m%d-%H%M%S")
    jd = jobs_dir()
    prompt_f, log_f, status_f = jd / (job_id + ".prompt"), jd / (job_id + ".log"), jd / (job_id + ".status")
    meta_f = jd / (job_id + ".meta.json")
    workdir = jd / job_id
    workdir.mkdir(exist_ok=True)

    cli = ARGS.cli_bin or "claude"
    result_f = jd / (job_id + ".result.json")
    timing_f = jd / (job_id + ".timing.json")
    # claude 输出 JSON 信封（含 token/成本/时长）单独落 result.json；人读日志仍在 log
    gen = ('T1=$(date +%s); {cli} -p "$(cat {pf})" --output-format json '
           '--dangerously-skip-permissions > {res} 2>> {log}; rc=$?; T2=$(date +%s); '
           'printf \'{{"clone_s": %s, "claude_s": %s}}\' "$((T1-T0))" "$((T2-T1))" > {tm}; '
           'echo $rc > {st}').format(
               cli=shlex.quote(cli), pf=shlex.quote(str(prompt_f)), res=shlex.quote(str(result_f)),
               log=shlex.quote(str(log_f)), tm=shlex.quote(str(timing_f)), st=shlex.quote(str(status_f)))
    tail = ("步骤：1) 产出 {work}/canvas.json；2) python3 {skill}/validate.py 清零 ERROR；\n"
            "3) python3 {skill}/render.py 渲染；4) 把最终 html 复制为 {hub}/{name}.html，"
            "json 复制为 {hub}/{name}.json。完成后打印 DONE。").format(
                skill=SKILL_DIR, work=workdir, hub=ARGS.hub, name=name)
    head = "阅读 {skill}/SKILL.md 并严格按其管线执行（规模闸门、验证器、截图自检都算数）。\n".format(skill=SKILL_DIR)

    if src["kind"] == "git":
        clone_dir = workdir / "repo"
        prompt = head + "任务：为仓库 {d}（clone 自 {u}）生成一张 Code Canvas，主题/关注点：{a}\n".format(
            d=clone_dir, u=src["git_url"], a=ask) + tail
        # clone 失败：跳过 claude，status 记非零码，timing 只有 clone 段
        script = ('T0=$(date +%s); git clone --depth 1 {u} {d} >> {log} 2>&1 && cd {d} && {gen}'
                  ' || {{ rc=$?; T1=$(date +%s); '
                  'printf \'{{"clone_s": %s, "claude_s": 0}}\' "$((T1-T0))" > {tm}; '
                  'echo $rc > {st}; }}').format(
                      u=shlex.quote(src["git_url"]), d=shlex.quote(str(clone_dir)),
                      log=shlex.quote(str(log_f)), gen=gen,
                      tm=shlex.quote(str(timing_f)), st=shlex.quote(str(status_f)))
        cwd = str(ARGS.hub)
        source_desc = src["git_url"]
    elif src["kind"] == "path":
        prompt = head + "任务：为仓库 {r} 生成一张 Code Canvas，主题/关注点：{a}\n".format(
            r=src["repo"], a=ask) + tail
        script, cwd, source_desc = "T0=$(date +%s); " + gen, src["repo"], src["repo"]
    else:  # code：粘贴的代码片段
        src_dir = workdir / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "pasted.txt").write_text(src["code"], encoding="utf-8")
        prompt = head + ("任务：为 {p} 里的代码生成一张 Code Canvas（内容可能含多个文件片段，"
                         "file 字段按片段内自述或写 pasted.txt），主题/关注点：{a}\n").format(
                             p=src_dir / "pasted.txt", a=ask) + tail
        script, cwd, source_desc = "T0=$(date +%s); " + gen, str(src_dir), "粘贴代码 {} 字符".format(len(src["code"]))

    prompt_f.write_text(prompt, encoding="utf-8")
    subprocess.Popen(["bash", "-c", script], cwd=cwd, start_new_session=True)
    meta = {"id": job_id, "name": name, "ask": ask, "source": source_desc,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


LIST_CSS = """
body{margin:0;background:#f6f8fa;color:#1f2328;font:15px/1.6 -apple-system,'PingFang SC',sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:#57606a;font-size:13px;margin-bottom:18px}
a.card{display:block;background:#ffffff;border:1px solid #d0d7de;border-radius:10px;
  padding:14px 16px;margin-bottom:10px;text-decoration:none;color:inherit;
  box-shadow:0 1px 3px rgba(31,35,40,.06);position:relative}
a.card:active{background:#f3f4f6}
.del{position:absolute;top:10px;right:10px;width:28px;height:28px;line-height:28px;
  text-align:center;border-radius:8px;color:#8c959f;font-size:14px}
.del:hover{color:#cf222e;background:rgba(207,34,46,.08)}
.t{font-weight:600}
.m{color:#57606a;font-size:12.5px;margin-top:2px}
.badge{display:inline-block;font-size:11px;border:1px solid #d0d7de;border-radius:999px;
  padding:0 8px;margin-left:8px;color:#57606a;vertical-align:1px}
.badge.diff{color:#9a6700;border-color:#bf8700}
.badge.plan{color:#1a7f37;border-color:#2da44e}
h2{font-size:15px;color:#57606a;margin:26px 0 8px}
.job{font-size:13px;color:#57606a;padding:6px 0;border-bottom:1px solid #e4e8ec}
.job .st-running{color:#9a6700}.job .st-done{color:#1a7f37}.job .st-failed{color:#cf222e}
.jobm{font-size:11.5px;color:#8c959f;margin-top:2px;font-family:ui-monospace,Menlo,monospace}
.costnote{font-size:10.5px;color:#8c959f;font-weight:400;margin-left:8px}
#gen{background:#ffffff;border:1px solid #d0d7de;border-radius:12px;padding:16px;margin-bottom:22px;
  box-shadow:0 1px 3px rgba(31,35,40,.06)}
#gen .row{display:flex;gap:14px;margin-bottom:10px;font-size:13.5px;flex-wrap:wrap}
#gen label{color:#24292f}
#gen input[type=text],#gen textarea{width:100%;box-sizing:border-box;background:#ffffff;
  color:#1f2328;border:1px solid #d0d7de;border-radius:8px;padding:9px 11px;font-size:14px;
  margin-bottom:10px;font-family:inherit}
#gen textarea{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;min-height:130px;display:none}
#gen button{background:#1f883d;color:#fff;border:0;border-radius:8px;padding:10px 22px;
  font-size:14.5px;font-weight:600;width:100%}
#gen button:disabled{opacity:.5}
#gen .hintline{color:#57606a;font-size:12px;margin:-4px 0 10px}
#genmsg{font-size:13px;margin-top:8px;color:#9a6700}
"""

GEN_FORM = """
<div id=gen>
  <div class=row>
    <label><input type=radio name=kind value=git checked> git 地址</label>
    <label><input type=radio name=kind value=path> 盒子路径</label>
    <label><input type=radio name=kind value=code> 粘贴代码</label>
  </div>
  <input type=text id=g-src placeholder="https://github.com/owner/repo">
  <textarea id=g-code placeholder="粘贴一段或几个文件的代码…（可用 # file: name.py 注释行分隔）"></textarea>
  <input type=text id=g-ask placeholder="主题 / 关注点（例：讲讲调度器怎么工作）">
  <input type=text id=g-name placeholder="画布名（可选，字母数字-_.）">
  <div class=hintline>生成一张画布约 20-40 分钟，完成后出现在下面的库里；页面会自动刷新任务状态</div>
  <button id=g-go>生成画布</button>
  <div id=genmsg></div>
</div>
<script>
const $=id=>document.getElementById(id);
const srcPH={git:"https://github.com/owner/repo",path:"/home/ubuntu/…（盒子上的仓库路径）"};
document.querySelectorAll('input[name=kind]').forEach(r=>r.onchange=()=>{
  const k=document.querySelector('input[name=kind]:checked').value;
  $('g-src').style.display=k==='code'?'none':'block';
  $('g-code').style.display=k==='code'?'block':'none';
  if(k!=='code')$('g-src').placeholder=srcPH[k];
});
$('g-go').onclick=async()=>{
  const k=document.querySelector('input[name=kind]:checked').value;
  const body={ask:$('g-ask').value.trim(),name:$('g-name').value.trim()||undefined};
  if(k==='git')body.git_url=$('g-src').value.trim();
  if(k==='path')body.repo=$('g-src').value.trim();
  if(k==='code')body.code=$('g-code').value;
  if(!body.ask){$('genmsg').textContent='主题/关注点是必填的——这张图为谁、讲什么';return;}
  $('g-go').disabled=true;$('genmsg').textContent='提交中…';
  try{
    const r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const j=await r.json();
    $('genmsg').textContent=j.ok?('任务已开始：'+j.job.id+' · '+j.job.name):('出错：'+j.error);
    if(j.ok)setTimeout(()=>location.reload(),1200);
  }catch(e){$('genmsg').textContent='请求失败：'+e}
  $('g-go').disabled=false;
};
document.addEventListener('click',async e=>{
  const d=e.target.closest('.del');
  if(!d)return;
  e.preventDefault();e.stopPropagation();
  const n=d.dataset.name;
  if(!confirm('删除画布「'+n+'」？连同问答记录一起删除，不可恢复。'))return;
  const r=await fetch('/c/'+n,{method:'DELETE'});
  const j=await r.json();
  if(j.ok)location.reload();else alert('删除失败：'+(j.error||''));
});
const fmtTok=n=>n==null?'?':(n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':''+n);
function jobMetrics(x){
  if(!x.metrics&&!x.timing)return'';
  const m=x.metrics||{},t=x.timing||{};
  const dur=m.dur_s?(m.dur_s>=60?Math.floor(m.dur_s/60)+'m'+Math.round(m.dur_s%60)+'s':m.dur_s+'s'):'';
  const parts=[];
  if(dur)parts.push('⏱ '+dur+(t.clone_s?`（clone ${t.clone_s}s + claude ${Math.floor((t.claude_s||0)/60)}m${(t.claude_s||0)%60}s）`:''));
  if(m.in_tok!=null)parts.push(`↑${fmtTok(m.in_tok)} ↓${fmtTok(m.out_tok)}${m.cache_read?` ⟳${fmtTok(m.cache_read)}`:''}`);
  if(m.cost_usd)parts.push('$'+m.cost_usd+'*');
  return parts.length?`<div class=jobm>${parts.join(' · ')}</div>`:'';
}
async function poll(){
  try{
    const j=await(await fetch('/jobs')).json();
    const el=$('jobs');
    if(el&&j.jobs.length){
      el.innerHTML='<h2>生成任务 <span class=costnote>*成本为 API 价折算参考（订阅不按量计费）</span></h2>'+j.jobs.map(x=>
        `<div class=job>${x.id} · ${x.name} · <span class="st-${x.status.split('(')[0]}">${x.status}</span> · ${(x.source||'')} · ${x.ask.slice(0,50)}${jobMetrics(x)}</div>`).join('');
      if(j.jobs.some(x=>x.status==='running'))setTimeout(poll,5000);
    }
  }catch(e){}
}
poll();
</script>
"""


def canvas_rows(paths, url_prefix="/c/", deletable=False) -> str:
    rows = []
    for p in paths:
        m = canvas_meta(p)
        badge = '<span class="badge {mode}">{mode}</span>'.format(mode=m["mode"]) if m["mode"] else ""
        delbtn = '<span class="del" data-name="{}" title="删除画布">✕</span>'.format(p.stem) if deletable else ""
        rows.append(
            '<a class="card" href="{pre}{name}/">{delbtn}<div class="t">{title}{badge}</div>'
            '<div class="m">{subtitle}{sep}{mtime}</div></a>'.format(
                pre=url_prefix, name=p.stem, delbtn=delbtn, title=m["title"], badge=badge,
                subtitle=m["subtitle"], sep=" · " if m["subtitle"] else "", mtime=m["mtime"]))
    return "".join(rows)


def render_list_page() -> str:
    lib = list_canvases()
    ex = list_examples()
    lib_html = ("<h2>画布库</h2>" + canvas_rows(lib, deletable=True)) if lib else \
        "<h2>画布库</h2><div class='m'>还没有生成过画布——在上面喂一段代码试试</div>"
    ex_html = ("<h2>示例（质量基准，非产物）</h2>" + canvas_rows(ex)) if ex else ""
    return ("<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Canvas Hub</title><style>{css}</style><div class=wrap>"
            "<h1>Canvas Hub</h1><div class=sub>喂一个 repo / 一段代码，生成可问答的 2D 画布</div>"
            "{form}<div id=jobs></div>{lib}{ex}</div>").format(
                css=LIST_CSS, form=GEN_FORM, lib=lib_html, ex=ex_html)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[serve] " + fmt % args + "\n")

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body_bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _canvas_path(self):
        """/c/<name>/... → (html_path, rest) 或 None。name 白名单校验；库优先，其次示例。"""
        m = re.match(r"^/c/([^/]+)(/.*)?$", self.path)
        if not m or not NAME_RE.match(m.group(1)):
            return None
        for base in (ARGS.hub, ARGS.hub / "examples"):
            p = base / (m.group(1) + ".html")
            if p.exists():
                return (p, m.group(2) or "")
        return None

    def do_GET(self):
        if ARGS.hub:
            if self.path.rstrip("/") in ("", "/index.html"):
                return self._html(render_list_page().encode("utf-8"))
            if self.path.startswith("/jobs"):
                return self._json(200, {"ok": True, "jobs": list_jobs()})
            if self.path.startswith("/__alive"):
                return self._json(200, {"ok": True, "hub": True, "cli": ARGS.cli})
            hit = self._canvas_path()
            if hit:
                html_path, rest = hit
                if rest == "":  # /c/<name> → 补斜杠，保证页面内相对路径成立
                    self.send_response(301)
                    self.send_header("Location", self.path + "/")
                    self.end_headers()
                    return
                if rest == "/":
                    maybe_rerender(html_path)
                    return self._html(html_path.read_bytes())
                if rest.startswith("/download"):
                    maybe_rerender(html_path)
                    body = html_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     'attachment; filename="{}"'.format(html_path.name))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                if rest.startswith("/__alive"):
                    return self._json(200, {"ok": True, "html": html_path.name, "cli": ARGS.cli})
            return self._json(404, {"ok": False, "error": "not found"})
        # 单画布模式
        if self.path.rstrip("/") in ("", "/index.html") or self.path == "/":
            return self._html(ARGS.html.read_bytes())
        if self.path.startswith("/__alive"):
            return self._json(200, {"ok": True, "html": ARGS.html.name, "cli": ARGS.cli})
        self._json(404, {"ok": False, "error": "not found"})

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_POST(self):
        if ARGS.hub:
            hit = self._canvas_path()
            if hit and hit[1].startswith("/ask"):
                return self._ask(hit[0])
            if self.path.startswith("/generate"):
                try:
                    req = self._read_body()
                    ask = (req.get("ask") or "").strip()
                except Exception as e:
                    return self._json(400, {"ok": False, "error": "bad request: {}".format(e)})
                if not ask:
                    return self._json(400, {"ok": False, "error": "ask（主题/关注点）是必填的"})
                # 三选一确定代码来源
                if req.get("git_url"):
                    u = req["git_url"].strip()
                    if not re.match(r"^https?://\S+$", u):
                        return self._json(400, {"ok": False, "error": "git_url 只接受 http(s) 地址"})
                    src = {"kind": "git", "git_url": u}
                    default = re.sub(r"\.git$", "", u.rstrip("/").rsplit("/", 1)[-1])
                elif req.get("repo"):
                    r = req["repo"].strip()
                    if not Path(r).is_dir():
                        return self._json(400, {"ok": False, "error": "盒子上不存在目录 {}".format(r)})
                    src = {"kind": "path", "repo": r}
                    default = Path(r).name
                elif req.get("code"):
                    if not req["code"].strip():
                        return self._json(400, {"ok": False, "error": "code 是空的"})
                    src = {"kind": "code", "code": req["code"]}
                    default = time.strftime("pasted-%m%d-%H%M")
                else:
                    return self._json(400, {"ok": False, "error": "git_url / repo / code 三选一"})
                name = req.get("name") or default
                if not NAME_RE.match(name):
                    return self._json(400, {"ok": False, "error": "name 只允许 [A-Za-z0-9._-]"})
                return self._json(200, {"ok": True, "job": spawn_generate(src, ask, name)})
            return self._json(404, {"ok": False, "error": "not found"})
        if self.path.startswith("/ask"):
            return self._ask(ARGS.html)
        self._json(404, {"ok": False, "error": "not found"})

    def do_DELETE(self):
        """DELETE /c/<name> —— 删除库里的画布（html+json+qa sidecar）。
        examples/ 是质量基准，不可删。"""
        if not ARGS.hub:
            return self._json(404, {"ok": False, "error": "not found"})
        m = re.match(r"^/c/([^/]+)/?$", self.path)
        if not m or not NAME_RE.match(m.group(1)):
            return self._json(404, {"ok": False, "error": "not found"})
        p = ARGS.hub / (m.group(1) + ".html")
        if not p.exists():
            if (ARGS.hub / "examples" / (m.group(1) + ".html")).exists():
                return self._json(403, {"ok": False, "error": "示例画布是质量基准，不可删除"})
            return self._json(404, {"ok": False, "error": "画布不存在"})
        removed = []
        for f in (p, p.with_suffix(".json"), p.with_suffix(p.suffix + ".qa.json")):
            if f.exists():
                f.unlink()
                removed.append(f.name)
        self._json(200, {"ok": True, "removed": removed})

    def _ask(self, html_path: Path):
        try:
            req = self._read_body()
            prompt = req["prompt"]
        except Exception as e:
            return self._json(400, {"ok": False, "error": "bad request: {}".format(e)})
        result = run_cli(prompt)
        if result.get("ok"):
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "card": req.get("card"),
                   "block": req.get("block"), "question": req.get("question"),
                   "answer": result["answer"]}
            if result.get("metrics"):
                rec["metrics"] = result["metrics"]
            persist(html_path, rec)
        self._json(200, result)


def main() -> None:
    global ARGS
    p = argparse.ArgumentParser()
    p.add_argument("html", type=Path, nargs="?", help="单画布模式：canvas.html")
    p.add_argument("--hub", type=Path, default=None, help="hub 模式：画布库目录")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8340)
    p.add_argument("--cli", choices=["claude", "codex"], default="claude")
    p.add_argument("--model", default=None)
    p.add_argument("--repo", type=Path, default=None, help="问答 CLI 子进程的工作目录")
    p.add_argument("--cli-bin", default=None, help="CLI 可执行文件路径覆盖（调试用）")
    ARGS = p.parse_args()
    if not ARGS.hub and not ARGS.html:
        sys.exit("要么给 canvas.html（单画布），要么 --hub <目录>")
    if ARGS.hub:
        if not ARGS.hub.is_dir():
            sys.exit("{} 不是目录".format(ARGS.hub))
        ARGS.hub = ARGS.hub.resolve()
    elif not ARGS.html.exists():
        sys.exit("{} 不存在——先用 render.py 渲染".format(ARGS.html))
    if not ARGS.cli_bin and not shutil.which(ARGS.cli):
        sys.exit("{} 不在 PATH 上".format(ARGS.cli))
    what = "hub {}".format(ARGS.hub) if ARGS.hub else str(ARGS.html)
    print("serving {} at http://{}:{}/  (cli: {})".format(what, ARGS.host, ARGS.port, ARGS.cli))
    ThreadingHTTPServer((ARGS.host, ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
