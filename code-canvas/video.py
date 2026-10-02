#!/usr/bin/env python3
"""
video.py — 把视频脚本（video.json，规格见 video-spec.md）渲染成 3Blue1Brown 风格的
精读带读视频：Manim 动画 + 中文配音 + 字幕，音画逐拍同步。

  python3 video.py --check video.json canvas.json          # 只校验（py3.8 可跑，不需要 manim）
  python3 video.py video.json canvas.json out.mp4 [-q l|m|h]

代码画面一律从画布 JSON 原文提取（脚本只给卡 id 与行号）。配音用 macOS `say`；
每拍先合成音频、量时长，动画按时长排——旁白和画面不会错位。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KINDS = ("title", "idea", "flow", "sim", "table", "code", "recap")
OPS = ("focus", "consume", "refund", "label", "mark", "move", "add", "remove")
STATES = ("good", "bad", "done", "none")
LANGS = {"py": "python", "python": "python", "js": "javascript", "ts": "typescript", "rs": "rust",
         "go": "go", "java": "java", "c": "c", "cpp": "cpp", "cc": "cpp", "h": "c", "sh": "bash"}


def card_lines(card):
    return (card.get("code") or "").split("\n")


def file_start(card):
    m = re.search(r":(\d+)$", card.get("file") or "")
    return int(m.group(1)) if m else 1


def check(v, canvas):
    E, W = [], []
    cards = {c.get("id"): c for c in canvas.get("cards", [])}
    scenes = v.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["scenes 必须是非空列表"], W
    chars = 0
    for i, sc in enumerate(scenes):
        sp = "scenes[{}]".format(i)
        k = sc.get("kind")
        if k not in KINDS:
            E.append("{}: kind 只能是 {}".format(sp, " | ".join(KINDS)))
            continue
        beats = sc.get("beats")
        if not isinstance(beats, list) or not beats:
            E.append("{}: beats 必须非空".format(sp))
            continue
        for j, b in enumerate(beats):
            say = (b.get("say") or "").strip()
            if not say:
                E.append("{}.beats[{}]: 缺 say".format(sp, j))
            elif len(say) > 70:
                W.append("{}.beats[{}]: 旁白 {} 字（建议 ≤60）".format(sp, j, len(say)))
            chars += len(say)
        if k in ("flow", "recap"):
            items = sc.get("items") or []
            if not items:
                E.append("{}: items 必须非空".format(sp))
            for j, b in enumerate(beats):
                if "item" in b and not (isinstance(b["item"], int) and 0 <= b["item"] < len(items)):
                    E.append("{}.beats[{}]: item 越界".format(sp, j))
        elif k == "code":
            c = cards.get(sc.get("card"))
            if not c or not c.get("code"):
                E.append("{}: 卡 {} 不存在或没有代码".format(sp, sc.get("card")))
                continue
            n = len(card_lines(c))
            rng = sc.get("lines")
            if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng)):
                E.append("{}: lines 应为 [起, 止]".format(sp))
                continue
            a, z = rng
            if not (1 <= a <= z <= n):
                E.append("{}: lines {} 越界（卡 {} 共 {} 行）".format(sp, sc.get("lines"), c["id"], n))
                continue
            if z - a + 1 > 16:
                E.append("{}: lines 跨 {} 行（≤16）".format(sp, z - a + 1))
            for j, b in enumerate(beats):
                hl = b.get("hl")
                if hl is not None and not (isinstance(hl, list) and len(hl) == 2 and a <= hl[0] <= hl[1] <= z):
                    E.append("{}.beats[{}]: hl {} 不在 lines {} 内".format(sp, j, hl, sc.get("lines")))
        elif k == "table":
            cols = sc.get("cols") or []
            if not (1 <= len(cols) <= 6):
                E.append("{}: cols 需 1-6 列".format(sp))
            if len(beats) > 7:
                W.append("{}: {} 行（建议 ≤7）".format(sp, len(beats)))
            for j, b in enumerate(beats):
                row = b.get("row")
                if row is not None and len(row) != len(cols):
                    E.append("{}.beats[{}]: row 有 {} 格，cols 有 {} 列".format(sp, j, len(row), len(cols)))
                for h in b.get("hl") or []:
                    if not (isinstance(h, int) and 0 <= h < len(cols)):
                        E.append("{}.beats[{}]: hl {} 越界".format(sp, j, h))
        elif k == "sim":
            lanes = sc.get("lanes") or []
            if not (1 <= len(lanes) <= 3):
                E.append("{}: lanes 需 1-3 条".format(sp))
            live = {}
            for it in sc.get("items") or []:
                if it.get("id") in live or it.get("lane") not in lanes:
                    E.append("{}: 方块 {} 重复或道名不对".format(sp, it.get("id")))
                live[it.get("id")] = it.get("lane")
            for j, b in enumerate(beats):
                for o in b.get("ops") or []:
                    op, item = o.get("op"), o.get("item")
                    bp = "{}.beats[{}] op {}".format(sp, j, op)
                    if op not in OPS:
                        E.append("{}: 未知 op（{}）".format(bp, " | ".join(OPS)))
                    elif op in ("consume", "refund"):
                        if not sc.get("budget"):
                            E.append("{}: 没有 budget 却要扣/退".format(bp))
                        if not (isinstance(o.get("n"), int) and o["n"] > 0):
                            E.append("{}: n 必须是正整数".format(bp))
                    elif op == "add":
                        if item in live or o.get("lane") not in lanes:
                            E.append("{}: 方块 {} 已存在或道名不对".format(bp, item))
                        live[item] = o.get("lane")
                    elif item not in live:
                        E.append("{}: 方块 {} 不在画面上".format(bp, item))
                    elif op == "move":
                        if o.get("lane") not in lanes:
                            E.append("{}: 道 {} 不存在".format(bp, o.get("lane")))
                        live[item] = o.get("lane")
                    elif op == "remove":
                        live.pop(item, None)
                    elif op == "mark" and o.get("state") not in STATES:
                        E.append("{}: state 只能是 {}".format(bp, " | ".join(STATES)))
                for lane in lanes:
                    if sum(1 for x in live.values() if x == lane) > 6:
                        W.append("{}.beats[{}]: 道 {} 超过 6 个方块".format(sp, j, lane))
    secs = chars / 4.2
    if not (90 <= secs <= 480):
        W.append("全片旁白约 {:.0f} 秒（建议 3-6 分钟）".format(secs))
    return E, W


# ---------------------------------------------------------------- 渲染

def wrap(s, width):
    """按显示宽度折行：CJK 记 1、ASCII 记 0.55。"""
    out, line, w = [], "", 0.0
    for ch in s:
        cw = 1.0 if ord(ch) > 0x2e80 else 0.55
        if w + cw > width and line:
            out.append(line)
            line, w = "", 0.0
        line += ch
        w += cw
    if line:
        out.append(line)
    return "\n".join(out)


def tts(text, voice, path):
    spoken = re.sub(r"[_`*]", " ", text)
    subprocess.run(["say", "-v", voice, "-o", path, "--data-format=LEI16@22050", spoken], check=True)
    out = subprocess.run(["ffprobe", "-loglevel", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def render(v, canvas, out_path, quality):
    import manim as M

    font, mono = "PingFang SC", "Menlo"
    cards = {c.get("id"): c for c in canvas.get("cards", [])}
    work = Path(tempfile.mkdtemp(prefix="canvas-video-"))
    voice = v.get("voice") or "Tingting"
    for si, sc in enumerate(v["scenes"]):
        for bi, b in enumerate(sc["beats"]):
            p = str(work / "a{}_{}.wav".format(si, bi))
            b["_audio"], b["_dur"] = p, tts(b["say"], voice, p)
            print("配音 {}/{} 拍 {:.1f}s".format(si + 1, bi + 1, b["_dur"]), file=sys.stderr)

    ACC, DIM, BG2 = M.YELLOW, M.GREY_B, "#1f232a"
    STATE_COLOR = {"good": M.GREEN_C, "bad": M.RED_C, "done": M.GREY_C, "none": M.BLUE_C}

    def T(s, size=30, color=M.WHITE, **kw):
        return M.Text(s, font=font, font_size=size, color=color, **kw)

    class CanvasVideo(M.Scene):
        def construct(self):
            self.sub = None
            for sc in v["scenes"]:
                getattr(self, "k_" + sc["kind"])(sc)
                keep = [m for m in self.mobjects if m is not self.sub]
                if keep:
                    self.play(*[M.FadeOut(m) for m in keep], run_time=0.45)

        # 一拍：放音频、换字幕、跑本拍动画、等满音频时长
        def beat(self, b, steps):
            self.add_sound(b["_audio"])
            d = b["_dur"]
            sub = T(wrap(b["say"], 30), 22, DIM).to_edge(M.DOWN, buff=0.28)
            subs = [M.FadeIn(sub)] + ([M.FadeOut(self.sub)] if self.sub else [])
            self.sub = sub
            # 每步可以是动画列表，或返回动画列表的函数——后者在播放前一刻才构造，
            # 这样它看到的是前几步改完的画面（.animate 会在构造时快照目标）
            steps = [s for s in steps if s]
            used = 0.0
            if not steps:
                self.play(*subs, run_time=0.3)
                used = 0.3
            for i, mk in enumerate(steps):
                anims = mk() if callable(mk) else mk
                rt = max(0.35, min(1.1, d * 0.45 / len(steps)))
                if anims or i == 0:
                    self.play(*(anims + (subs if i == 0 else [])), run_time=rt)
                    used += rt
            self.wait(max(0.15, d - used + 0.3))

        def k_title(self, sc):
            t = T(sc.get("text", ""), 46).shift(M.UP * 0.4)
            s = T(sc.get("sub", ""), 26, M.BLUE_B).next_to(t, M.DOWN, buff=0.45)
            for i, b in enumerate(sc["beats"]):
                self.beat(b, [[M.Write(t), M.FadeIn(s, shift=M.UP * 0.2)]] if i == 0 else [])

        def k_idea(self, sc):
            t2c = {e: ACC for e in sc.get("emph") or []}
            t = T(wrap(sc.get("text", ""), 18), 40, t2c=t2c, line_spacing=1.2)
            marks = [t[i:i + len(e)] for e in sc.get("emph") or [] for i in [t.text.find(e)] if i >= 0]
            for i, b in enumerate(sc["beats"]):
                if i == 0:
                    self.beat(b, [[M.Write(t)]])
                else:
                    m = marks[(i - 1) % len(marks)] if marks else t
                    self.beat(b, [[M.Circumscribe(m, color=ACC, buff=0.08)]])

        def _list(self, items, numbered):
            rows = M.VGroup(*[T(wrap(s, 28), 28) for s in items]).arrange(M.DOWN, aligned_edge=M.LEFT, buff=0.32)
            rows.scale_to_fit_height(min(rows.height, 5.2)).move_to(M.UP * 0.35)
            return rows

        def k_flow(self, sc):
            rows = self._list(sc["items"], True).set_opacity(0.35)
            self.play(M.FadeIn(rows), run_time=0.5)
            arrow = M.Triangle(color=ACC, fill_opacity=1).scale(0.12).rotate(-M.PI / 2)
            shown = None
            for b in sc["beats"]:
                i = b.get("item")
                steps = []
                if i is not None:
                    target = arrow.copy().next_to(rows[i], M.LEFT, buff=0.25)
                    an = [rows[i].animate.set_opacity(1).set_color(ACC)]
                    if shown is not None and shown != i:
                        an.append(rows[shown].animate.set_opacity(0.75).set_color(M.WHITE))
                    an.append(M.Transform(arrow, target) if arrow in self.mobjects else M.FadeIn(target))
                    if arrow not in self.mobjects:
                        arrow = target
                    steps.append(an)
                    shown = i
                self.beat(b, steps)

        def k_recap(self, sc):
            rows = self._list(["· " + s for s in sc["items"]], False)
            for b in sc["beats"]:
                i = b.get("item")
                self.beat(b, [[M.FadeIn(rows[i], shift=M.RIGHT * 0.3)]] if i is not None else [])

        def k_table(self, sc):
            cols = sc["cols"]
            cw = min(2.6, 12.0 / len(cols))
            x0 = -cw * len(cols) / 2 + cw / 2
            head = M.VGroup(*[T(c, 22, M.BLUE_B).move_to([x0 + k * cw, 2.6, 0]) for k, c in enumerate(cols)])
            line = M.Line([x0 - cw / 2, 2.3, 0], [x0 + cw * (len(cols) - 0.5), 2.3, 0], color=M.GREY_D)
            self.play(M.FadeIn(head), M.Create(line), run_time=0.5)
            y = 1.85
            for b in sc["beats"]:
                row = b.get("row")
                if row is None:
                    self.beat(b, [])
                    continue
                cells = M.VGroup(*[T(str(c), 22, ACC if k in (b.get("hl") or []) else M.WHITE).move_to([x0 + k * cw, y, 0])
                                   for k, c in enumerate(row)])
                for cell in cells:
                    if cell.width > cw * 0.92:
                        cell.scale_to_fit_width(cw * 0.92)
                self.beat(b, [[M.FadeIn(cells, shift=M.DOWN * 0.15)]])
                y -= 0.62

        def k_code(self, sc):
            c = cards[sc["card"]]
            a, z = sc["lines"]
            src = "\n".join(card_lines(c)[a - 1:z])
            code = M.Code(code_string=src, language=LANGS.get((c.get("lang") or "py").lower(), "python"),
                          formatter_style="monokai", add_line_numbers=True,
                          line_numbers_from=file_start(c) + a - 1,
                          paragraph_config={"font": mono}, background="rectangle",
                          background_config={"fill_color": BG2, "stroke_color": M.GREY_D})
            code.scale_to_fit_width(min(code.width, 12.6))
            if code.height > 5.2:
                code.scale_to_fit_height(5.2)
            head = T(c.get("name", ""), 26, M.BLUE_B).to_edge(M.UP, buff=0.35)
            code.next_to(head, M.DOWN, buff=0.3)
            fname = T((c.get("file") or "").split(":")[0], 18, M.GREY_C).next_to(code, M.DOWN, buff=0.12).align_to(code, M.RIGHT)
            self.play(M.FadeIn(head), M.FadeIn(code), M.FadeIn(fname), run_time=0.6)
            lines = code.code_lines
            box = None
            for b in sc["beats"]:
                hl = b.get("hl")
                if not hl:
                    self.beat(b, [])
                    continue
                sel = M.VGroup(*lines[hl[0] - a:hl[1] - a + 1])
                rect = M.SurroundingRectangle(sel, color=ACC, buff=0.06, stroke_width=2,
                                              fill_color=ACC, fill_opacity=0.08)
                rect.stretch_to_fit_width(code.background.width - 0.1).align_to(code.background, M.LEFT).shift(M.RIGHT * 0.05)
                dims = [ln.animate.set_opacity(1 if hl[0] - a <= k <= hl[1] - a else 0.35) for k, ln in enumerate(lines)]
                self.beat(b, [dims + ([M.Transform(box, rect)] if box else [M.Create(rect)])])
                box = box or rect

        def k_sim(self, sc):
            lanes = sc["lanes"]
            ys = {ln: 1.25 - k * 1.55 for k, ln in enumerate(lanes)}
            order = {ln: [] for ln in lanes}
            boxes = {}
            group = M.VGroup()
            for ln in lanes:
                group.add(T(ln, 24, M.BLUE_B).move_to([-5.6, ys[ln], 0]))
                group.add(M.Line([-4.6, ys[ln] - 0.55, 0], [6.4, ys[ln] - 0.55, 0], color=M.GREY_D, stroke_width=1))

            def make(it):
                r = M.RoundedRectangle(corner_radius=0.12, width=1.3, height=0.85, color=STATE_COLOR["none"],
                                       fill_color=STATE_COLOR["none"], fill_opacity=0.18)
                name = T(it["id"], 22).move_to(r.get_center() + M.UP * 0.16)
                lab = fit(T(it.get("label", ""), 16, M.GREY_A)).move_to(r.get_center() + M.DOWN * 0.2)
                return M.VGroup(r, name, lab)

            def fit(m, w=1.16):   # 标签放不下方块就缩小，不许溢出边框
                return m.scale_to_fit_width(w) if m.width > w else m

            def spot(ln, k):
                return M.np.array([-3.7 + k * 1.6, ys[ln], 0])

            for it in sc.get("items") or []:
                boxes[it["id"]] = make(it).move_to(spot(it["lane"], len(order[it["lane"]])))
                order[it["lane"]].append(it["id"])
                group.add(boxes[it["id"]])
            bud = sc.get("budget")
            if bud:
                W = 7.5
                state = {"left": bud["total"]}
                track = M.Rectangle(width=W, height=0.32, color=M.GREY_D).move_to([0.6, 2.85, 0])
                fill = M.Rectangle(width=W, height=0.32, stroke_width=0, fill_color=M.TEAL_C, fill_opacity=0.85).move_to(track)
                blab = T("{} = {}".format(bud["name"], bud["total"]), 22).next_to(track, M.LEFT, buff=0.3)
                group.add(track, fill, blab)
            self.play(M.FadeIn(group), run_time=0.7)

            def relayout():
                return [boxes[i].animate.move_to(spot(ln, k)) for ln in lanes for k, i in enumerate(order[ln])]

            def apply(o):
                op, it = o["op"], o.get("item")
                if op == "focus":
                    return [M.Indicate(boxes[it], color=ACC, scale_factor=1.15)]
                if op in ("consume", "refund"):
                    state["left"] += o["n"] if op == "refund" else -o["n"]
                    w = max(0.001, W * state["left"] / bud["total"])
                    nf = M.Rectangle(width=w, height=0.32, stroke_width=0, fill_color=M.TEAL_C,
                                     fill_opacity=0.85).align_to(track, M.LEFT).set_y(track.get_y())
                    nl = T("{} = {}".format(bud["name"], state["left"]), 22).move_to(blab)
                    return [M.Transform(fill, nf), M.Transform(blab, nl)]
                if op == "label":
                    old = boxes[it][2]
                    return [M.Transform(old, fit(T(o.get("text", ""), 16, ACC)).move_to(old))]
                if op == "mark":
                    col = STATE_COLOR[o["state"]]
                    return [boxes[it][0].animate.set_stroke(col).set_fill(col, opacity=0.25)]
                if op == "move":
                    for ln in lanes:
                        if it in order[ln]:
                            order[ln].remove(it)
                    dst = order[o["lane"]]
                    dst.insert(o.get("at", len(dst)), it)
                    return relayout()
                if op == "add":
                    dst = order[o["lane"]]
                    boxes[it] = make({"id": it, "label": o.get("label", "")}).move_to(spot(o["lane"], len(dst)))
                    dst.append(it)
                    return [M.FadeIn(boxes[it], shift=M.LEFT * 0.4)]
                if op == "remove":
                    for ln in lanes:
                        if it in order[ln]:
                            order[ln].remove(it)
                    return [M.FadeOut(boxes.pop(it), shift=M.RIGHT * 0.4)] + relayout()
                return []

            for b in sc["beats"]:
                self.beat(b, [(lambda o=o: apply(o)) for o in b.get("ops") or []])

    qmap = {"l": "low_quality", "m": "medium_quality", "h": "high_quality"}
    with M.tempconfig({"quality": qmap[quality], "media_dir": str(work), "disable_caching": True,
                       "background_color": "#14161a", "verbosity": "WARNING", "progress_bar": "none"}):
        scene = CanvasVideo()
        scene.render()
        produced = Path(scene.renderer.file_writer.movie_file_path)
    shutil.copyfile(str(produced), out_path)
    return out_path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    q = "l"
    if "-q" in sys.argv:
        q = sys.argv[sys.argv.index("-q") + 1]
        args = [a for a in args if a != q]
    if "--check" in sys.argv and len(args) == 2:
        v, c = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args)
        E, W = check(v, c)
        for m in E:
            print("ERROR ", m)
        for m in W:
            print("warn  ", m)
        print("\n{} errors, {} warnings".format(len(E), len(W)))
        return 1 if E else 0
    if len(args) != 3 or q not in ("l", "m", "h"):
        print(__doc__.strip())
        return 1
    v, c = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args[:2])
    E, W = check(v, c)
    if E:
        print("\n".join("ERROR  " + m for m in E))
        return 1
    print("wrote", render(v, c, args[2], q))
    return 0


if __name__ == "__main__":
    sys.exit(main())
