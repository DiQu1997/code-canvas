#!/usr/bin/env python3
"""cfg_extract.py — 从函数卡的真实代码行机械推出控制结构树（零 LLM，Python 3.8 兼容）。

build_zoom.py 对每张函数卡调用 card_cfg()，结果挂在 card["cfg"] 上，供模板的 `cfg` 图元
画「函数骨架图」：主干、分支菱形、循环框、return 终点、字段写入标签、对其他卡的调用。

节点（紧凑键）：
  {"k":"run","ln":[a,b],"c":首行代码,"n":语句数,"s":摘要?,"w":[被写字段],"calls":[卡 id]}
  {"k":"if","ln":[a,b],"s":?,"arms":[{"c":"if cond"|"elif cond"|"else","ln":a,"s":?,"body":[...]}]}
  {"k":"loop","ln":[a,b],"c":"for x in y","s":?,"body":[...]}
  {"k":"ret","ln":[a,a],"c":"return x"|"raise E","s":?}
  {"k":"jump","ln":[a,a],"c":"break"|"continue"}
摘要 s 取自卡片 blocks（作者写的中文块摘要）：每个块的摘要给落在该块行范围内的第一个节点。
两套解析：Python 走缩进（_logical + seq）；C 家族（花括号 + 圆括号条件：C/C++/Java/JS/TS/C#）走 _c_tree，
多出 switch（每个 case 一条分支）、do-while、goto（jump）和标号（另起一段）。只在 C 上实测过。
其他语言（Go/Rust 等条件不带括号的）退化为「每个 block 一个 run 节点」（lang_ok=False）。
"""
import bisect
import re

_OPEN, _CLOSE = "([{", ")]}"
_MUT = "append|add|pop|remove|clear|update|extend|insert|discard|popitem|setdefault|appendleft|popleft"
_W_ASSIGN = re.compile(r"((?:\w+\.)+)(\w+)\s*(?:\[[^\]=]*\])?\s*(?:[+\-*/|&]?=)(?!=)")
_W_MUT = re.compile(r"(?:\w+\.)+(\w+)\.(?:%s)\(" % _MUT)
_W_DEL = re.compile(r"\bdel\s+(?:\w+\.)+(\w+)\s*\[")


def _strip_comment(t):
    out, q, i = [], None, 0
    while i < len(t):
        ch = t[i]
        if q:
            if ch == "\\":
                out.append(t[i:i + 2]); i += 2; continue
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            break
        out.append(ch); i += 1
    return "".join(out).rstrip()


def _depth(t):
    d, q, i = 0, None, 0
    while i < len(t):
        ch = t[i]
        if q:
            if ch == "\\":
                i += 2; continue
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch in _OPEN:
            d += 1
        elif ch in _CLOSE:
            d -= 1
        i += 1
    return d


def _logical(code):
    """[(first_ln, last_ln, indent, text)] — continuation lines joined, blanks/comments/docstrings dropped."""
    out, i, rows = [], 0, [(n, t) for n, t in code if n]
    while i < len(rows):
        n, raw = rows[i]
        s = raw.strip()
        if s[:3] in ('"""', "'''") or s[:4] in ('r"""', "f\"\"\"", "b\"\"\""):
            q = '"""' if '"""' in s[:4] else "'''"
            if s.count(q) < 2:
                i += 1
                while i < len(rows) and q not in rows[i][1]:
                    i += 1
            i += 1
            continue
        t = _strip_comment(raw)
        if not t.strip():
            i += 1
            continue
        ind = len(t) - len(t.lstrip())
        txt, d, last = t.strip(), _depth(t), n
        while (d > 0 or txt.endswith("\\")) and i + 1 < len(rows):
            i += 1
            nx = _strip_comment(rows[i][1]).strip()
            txt = (txt[:-1].rstrip() if txt.endswith("\\") else txt) + (" " if txt[-1:] not in _OPEN else "") + nx
            d += _depth(nx)
            last = rows[i][0]
        out.append((n, last, ind, re.sub(r"\s+", " ", txt)))
        i += 1
    return out


def _writes(txt):
    w = []
    for m in _W_ASSIGN.finditer(txt):
        w.append(m.group(2))
    for rx in (_W_MUT, _W_DEL):
        for m in rx.finditer(txt):
            w.append(m.group(1))
    return w


_BRACE_EXT = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".java", ".js", ".ts", ".jsx", ".tsx", ".cs")
_WC_ASSIGN = re.compile(r"[\w\)\]]\s*(?:->|\.)\s*(\w+)\s*(?:\[[^\]=]*\])*\s*(?:<<|>>|[+\-*/|&^%])?=(?!=)")
_WC_INC = re.compile(r"(?:->|\.)\s*(\w+)\s*(?:\[[^\]]*\])*\s*(?:\+\+|--)")
_C_ID = re.compile(r"[A-Za-z_]\w*")
_C_LABEL = re.compile(r"([A-Za-z_]\w*)\s*:(?![:=])")
_C_DECL = re.compile(r"(?:(?:const|static|unsigned|signed|struct|enum|union|long|short|volatile|register)\s+)*"
                     r"[A-Za-z_]\w*(?:\s*\*+\s*|\s+)[A-Za-z_]\w*(?:\[[^\]]*\])?"
                     r"(?:\s*,\s*\**\s*[A-Za-z_]\w*(?:\[[^\]]*\])?)*$")
_C_KW = ("if", "else", "for", "while", "do", "switch", "case", "default", "return", "break", "continue", "goto", "throw")


def _writes_c(txt):
    return [m.group(1) for rx in (_WC_ASSIGN, _WC_INC) for m in rx.finditer(txt)]


def _c_clean(rows):
    """-> (src, skel, offs, lns): src has comments/preprocessor blanked; skel also has string contents blanked,
    so brace/paren/semicolon scanning on skel can't be fooled. offs[i] is the offset where line lns[i] starts."""
    src, skel, offs, lns, off, in_c, pp = [], [], [], [], 0, False, False
    for n, t in rows:
        offs.append(off); lns.append(n)
        a, b, i, q = [], [], 0, None
        if pp or (not in_c and t.lstrip().startswith("#")):
            pp = t.rstrip().endswith("\\")
            a = b = [" "] * len(t)
        else:
            while i < len(t):
                ch = t[i]
                if in_c:
                    if t.startswith("*/", i):
                        in_c = False; a += "  "; b += "  "; i += 2; continue
                    a.append(" "); b.append(" ")
                elif q:
                    if ch == "\\":
                        a += t[i:i + 2]; b += "  "[:len(t[i:i + 2])]; i += 2; continue
                    a.append(ch); b.append(ch if ch == q else " ")
                    if ch == q:
                        q = None
                elif t.startswith("//", i):
                    break
                elif t.startswith("/*", i):
                    in_c = True; a += "  "; b += "  "; i += 2; continue
                else:
                    if ch in "\"'":
                        q = ch
                    a.append(ch); b.append(ch)
                i += 1
        src.append("".join(a) + "\n"); skel.append("".join(b) + "\n")
        off += len(src[-1])
    return "".join(src), "".join(skel), offs, lns


def _c_tree(rows, meta, plain):
    """Recursive-descent over statements of a brace language. meta(node, text) tags writes/calls;
    plain(out, a, b, text) appends an ordinary statement (merging runs)."""
    src, sk, offs, lns = _c_clean(rows)
    N = len(sk)

    def ln(o):
        return lns[max(0, bisect.bisect_right(offs, min(o, N - 1)) - 1)]

    def ws(p):
        while p < N and sk[p].isspace():
            p += 1
        return p

    def word(p):
        m = _C_ID.match(sk, p)
        return m.group(0) if m else ""

    def close(p):
        """p at an opener -> offset just past its matching closer."""
        d = 0
        while p < N:
            if sk[p] in _OPEN:
                d += 1
            elif sk[p] in _CLOSE:
                d -= 1
                if d == 0:
                    return p + 1
            p += 1
        return N

    def semi(p):
        d = 0
        while p < N:
            ch = sk[p]
            if ch in _OPEN:
                d += 1
            elif ch in _CLOSE:
                d -= 1
                if d < 0:
                    return p
            elif ch == ";" and d == 0:
                return p + 1
            p += 1
        return N

    def text(a, b):
        return re.sub(r"\s+", " ", src[a:b]).strip()

    def cond(p):
        """p after the keyword -> (condition text without the outer parens, offset past it)."""
        q = ws(p)
        if q < N and sk[q] == "(":
            e = close(q)
            return text(q + 1, e - 1), e
        return "", q

    def last(nodes, dflt):
        return max([dflt] + [n["ln"][1] for n in nodes])

    def block(p, out):
        while True:
            p = ws(p)
            if p >= N:
                return N
            if sk[p] == "}":
                return p + 1
            p = stmt(p, out)

    def stmt(p, out):
        p = ws(p)
        if p >= N:
            return N
        ch = sk[p]
        if ch == "{":
            return block(p + 1, out)
        if ch in "};":
            return p + 1
        w, a = word(p), ln(p)
        if w == "if":
            node = {"k": "if", "ln": [a, a], "arms": []}
            out.append(node)
            kw, q = "if", p + 2
            while True:
                c, e = cond(q) if kw != "else" else ("", q)
                arm = {"c": (kw + " " + c).strip(), "ln": ln(q - 1), "body": []}
                if c:
                    meta(node, c)
                p = stmt(e, arm["body"])
                node["arms"].append(arm)
                node["ln"][1] = last(arm["body"], max(node["ln"][1], ln(max(e - 1, 0))))
                q = ws(p)
                if kw == "else" or word(q) != "else":
                    return p
                q = ws(q + 4)
                if word(q) == "if":
                    kw, q = "else if", q + 2
                else:
                    kw = "else"
        if w in ("for", "while"):
            c, e = cond(p + len(w))
            node = {"k": "loop", "ln": [a, ln(max(e - 1, 0))], "c": w + " " + c, "body": []}
            meta(node, c)
            p = stmt(e, node["body"])
            node["ln"][1] = last(node["body"], node["ln"][1])
            out.append(node)
            return p
        if w == "do":
            node = {"k": "loop", "ln": [a, a], "c": "do", "body": []}
            out.append(node)
            p = ws(stmt(p + 2, node["body"]))
            if word(p) == "while":
                c, e = cond(p + 5)
                node["c"] = "do … while " + c
                meta(node, c)
                node["ln"][1] = ln(max(e - 1, 0))
                p = semi(e)
            node["ln"][1] = last(node["body"], node["ln"][1])
            return p
        if w == "switch":
            c, e = cond(p + 6)
            node = {"k": "if", "ln": [a, a], "arms": []}
            meta(node, c)
            out.append(node)
            p = ws(e)
            if p >= N or sk[p] != "{":
                return p
            p, arm, sel = p + 1, None, "switch " + c
            while True:
                p = ws(p)
                if p >= N or sk[p] == "}":
                    p = min(p + 1, N)
                    break
                if word(p) in ("case", "default"):
                    e = p
                    while e < N and sk[e] != ":":
                        e += 1
                    lab = text(p, e)
                    if arm is None or arm["body"]:
                        arm = {"c": (sel + " · " if not node["arms"] else "") + lab, "ln": ln(p), "body": []}
                        node["arms"].append(arm)
                    else:
                        arm["c"] += ", " + re.sub(r"^case\s+", "", lab)
                    p = e + 1
                    continue
                if arm is None:
                    arm = {"c": sel, "ln": ln(p), "body": []}
                    node["arms"].append(arm)
                p = stmt(p, arm["body"])
            for arm in node["arms"]:
                if arm["body"] and arm["body"][-1]["k"] == "jump" and arm["body"][-1]["c"] == "break":
                    arm["body"].pop()
                node["ln"][1] = last(arm["body"], node["ln"][1])
            node["ln"][1] = max(node["ln"][1], ln(p - 1))
            return p
        if w == "else":            # an else whose if was elided out of this card
            return p + 4
        if w in ("case", "default"):
            while p < N and sk[p] != ":":
                p += 1
            return p + 1
        e = semi(p)
        if e <= p:
            return p + 1
        t = text(p, e).rstrip(";").strip()
        b = ln(e - 1)
        if w in ("return", "throw"):
            node = {"k": "ret", "ln": [a, b], "c": t}
            meta(node, t)
            out.append(node)
            return e
        if w in ("break", "continue", "goto"):
            out.append({"k": "jump", "ln": [a, b], "c": t})
            return e
        m = _C_LABEL.match(sk, p)
        if m and w not in _C_KW:
            out.append({"k": "run", "ln": [a, a], "c": m.group(1) + ":", "n": 0})
            return m.end()
        if t and not _C_DECL.match(t):
            plain(out, a, b, t)
        return e

    # skip the function header (signature up to its opening brace) when the card starts with one
    p = ws(0)
    q = p
    while q < N and sk[q] not in "{;":
        q += 1
    out = []
    if q < N and sk[q] == "{" and "(" in sk[p:q] and not re.search(r"\b(?:if|for|while|switch|do|else)\b|[:=]", sk[p:q]):
        p = block(q + 1, out)
    while p < N:
        p = stmt(p, out)
    return out


def _add(lst, items):
    for x in items:
        if x not in lst:
            lst.append(x)


def card_cfg(card, fn_names):
    """fn_names: {function name: card id} for call detection. Returns (nodes, lang_ok)."""
    blocks = [b for b in card.get("blocks") or [] if isinstance(b, list) and len(b) >= 3]
    fname = str(card.get("file", "")).lower()
    brace = fname.endswith(_BRACE_EXT)
    if not fname.endswith(".py") and not brace:
        return [{"k": "run", "ln": [b[0], b[1]], "s": b[2], "n": b[1] - b[0] + 1} for b in blocks], False
    rows = [] if brace else _logical(card.get("code") or [])
    # drop decorators + the def/class header (possibly several when ranges stitch a signature in)
    body, base = [], None
    for r in rows:
        if re.match(r"(async\s+def|def|class)\s", r[3]) and (base is None or r[2] < base) or r[3].startswith("@"):
            if not body:
                continue
        body.append(r)
    if not body and not brace:
        return [], True
    own = re.sub(r"\(.*$", "", str(card.get("name") or card.get("title") or "")).split(".")[-1].strip()
    call_rx = None
    names = sorted((n for n in fn_names if n and len(n) > 2), key=len, reverse=True)
    if names:
        call_rx = re.compile(r"(?<![\w])(%s)\s*\(" % "|".join(re.escape(n) for n in names))

    def meta(node, txt):
        w = _writes_c(txt) if brace else _writes(txt)
        if w:
            _add(node.setdefault("w", []), w)
        if call_rx:
            txt = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', txt)   # a name inside a string is not a call
            cs = [fn_names[m.group(1)] for m in call_rx.finditer(txt)]
            cs = [c for c in cs if c != card.get("id") or m_self(txt)]
            if cs:
                _add(node.setdefault("calls", []), cs)

    def m_self(txt):
        return bool(own) and re.search(r"(?<![\w])%s\s*\(" % re.escape(own), txt) is not None

    pos = [0]

    def seq(ind):
        out = []
        while pos[0] < len(body):
            a, b, i, t = body[pos[0]]
            if i < ind:
                break
            head = re.match(r"(if|elif|else|for|async for|while|try|except|finally|with|async with)\b(.*):\s*(.*)$", t)
            kw = head.group(1) if head else None
            if kw in ("if", "elif", "else", "try", "except", "finally"):
                if kw in ("if", "try") or not out or out[-1]["k"] != "if":
                    out.append({"k": "if", "ln": [a, b], "arms": []})
                node = out[-1]
                pos[0] += 1
                arm = {"c": (kw + head.group(2)).strip(), "ln": a, "body": inline(head.group(3), a, b) or sub(i)}
                if kw in ("if", "elif"):
                    meta(node, head.group(2))
                node["arms"].append(arm)
                node["ln"][1] = max(node["ln"][1], last_ln(arm["body"], b))
            elif kw in ("for", "async for", "while"):
                pos[0] += 1
                node = {"k": "loop", "ln": [a, b], "c": (kw + head.group(2)).strip()}
                meta(node, head.group(2))
                node["body"] = inline(head.group(3), a, b) or sub(i)
                node["ln"][1] = last_ln(node["body"], b)
                out.append(node)
            elif kw in ("with", "async with"):
                pos[0] += 1
                stmt(out, a, b, t.rstrip(":"))
                out.extend(inline(head.group(3), a, b) or sub(i))
            else:
                pos[0] += 1
                stmt(out, a, b, t)
        return out

    def sub(ind):
        if pos[0] < len(body) and body[pos[0]][2] > ind:
            return seq(body[pos[0]][2])
        return []

    def inline(rest, a, b):
        out = []
        if rest:
            stmt(out, a, b, rest)
        return out

    def stmt(out, a, b, t):
        m = re.match(r"(return|raise|break|continue|pass)\b", t)
        if m and m.group(1) in ("return", "raise"):
            node = {"k": "ret", "ln": [a, b], "c": t}
            meta(node, t)
            out.append(node)
        elif m and m.group(1) in ("break", "continue"):
            out.append({"k": "jump", "ln": [a, b], "c": t})
        elif m:
            return
        else:
            plain(out, a, b, t)

    def plain(out, a, b, t):
        if out and out[-1]["k"] == "run" and not _split_here(out[-1], a):
            node = out[-1]
            node["ln"][1] = b
            node["n"] += 1
        else:
            node = {"k": "run", "ln": [a, b], "c": t, "n": 1}
            out.append(node)
        meta(node, t)

    starts = {b[0] for b in blocks}

    def _split_here(run, a):
        # a statement run breaks where the author's block summaries break
        return any(run["ln"][0] < s <= a for s in starts)

    def last_ln(nodes, dflt):
        return max([dflt] + [n["ln"][1] for n in nodes])

    if brace:
        tree = _c_tree([(n, t) for n, t in card.get("code") or [] if n], meta, plain)
    else:
        tree = seq(body[0][2])

    # hand each block summary to the first node (or arm) that starts inside it
    left = sorted(blocks, key=lambda b: b[0])

    def give(start, target):
        for bk in left:
            if bk[0] <= start <= bk[1]:
                target["s"] = bk[2]
                left.remove(bk)
                return

    def walk(nodes):
        for n in nodes:
            give(n["ln"][0], n)
            if n["k"] == "if":
                for j, arm in enumerate(n["arms"]):
                    if j:
                        give(arm["ln"], arm)
                    walk(arm["body"])
            elif n["k"] == "loop":
                walk(n["body"])

    walk(tree)
    return tree, True
