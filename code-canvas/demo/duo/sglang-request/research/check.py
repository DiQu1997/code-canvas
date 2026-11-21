"""Mechanical check of manuscript anchors, quotes and commit hashes.

usage: python3 -I check.py <repo> <manuscript.md> <manifest.tsv>
"""
import os
import re
import subprocess
import sys

repo, ms_path, manifest_path = sys.argv[1:4]
GIT = ["git", "-C", repo, "-c", "safe.directory=*", "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0"]

text = open(ms_path, encoding="utf-8").read()
lines = text.split("\n")

manifest = {}
for row in open(manifest_path, encoding="utf-8").read().split("\n"):
    if row.strip():
        k, v = row.split("\t", 1)
        manifest[k] = v

ANCHOR = re.compile(r"`((?:python|test)/[^`:]+):(\d+)(?:-(\d+))?`")
HASH = re.compile(r"`([0-9a-f]{10})`")
QUOTE = re.compile(r"「([^」]+)」")

file_cache = {}


def read_range(path, a, b):
    full = os.path.join(repo, path)
    if full not in file_cache:
        if not os.path.isfile(full):
            file_cache[full] = None
        else:
            file_cache[full] = open(full, encoding="utf-8").read().split("\n")
    src = file_cache[full]
    if src is None:
        return None
    if a < 1 or b > len(src) or a > b:
        return None
    return "\n".join(src[a - 1 : b])


git_cache = {}


def git(*args):
    key = args
    if key not in git_cache:
        r = subprocess.run(GIT + list(args), capture_output=True, text=True)
        git_cache[key] = (r.returncode, r.stdout)
    return git_cache[key]


# ---- anchors
anchor_total = anchor_ok = 0
anchor_fail = []
seen = set()
for m in ANCHOR.finditer(text):
    path, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
    key = f"{path}:{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")
    if key in seen:
        continue
    seen.add(key)
    anchor_total += 1
    rng = read_range(path, a, b)
    exp = manifest.get(key)
    if rng is None:
        anchor_fail.append((key, "range/file invalid"))
    elif exp is None:
        anchor_fail.append((key, "no manifest entry"))
    elif exp not in rng:
        anchor_fail.append((key, f"expected {exp!r} not in range"))
    else:
        anchor_ok += 1

# ---- commits
hash_total = hash_ok = 0
hash_fail = []
hseen = set()
for i, line in enumerate(lines):
    for hm in HASH.finditer(line):
        h = hm.group(1)
        # files claimed for this hash: backticked paths after "改了" in the same parenthesis
        claimed = []
        tail = line[hm.end():]
        mm = re.match(r"[^（]*?（改了([^）]*)）", tail)
        if mm and len(tail[: mm.start(1)]) < 400:
            claimed = re.findall(r"`((?:python|test)/[^`]+)`", mm.group(1))
        key = (h, tuple(claimed))
        if key in hseen:
            continue
        hseen.add(key)
        hash_total += 1
        rc, _ = git("cat-file", "-e", h + "^{commit}")
        if rc != 0:
            hash_fail.append((h, "no such commit"))
            continue
        rc, names = git("show", "--name-only", "--format=", h)
        names = set(names.split())
        missing = [c for c in claimed if c not in names]
        if missing:
            hash_fail.append((h, f"did not touch {missing}"))
            continue
        hash_ok += 1

# ---- quotes
quote_total = quote_ok = 0
quote_fail = []
for i, line in enumerate(lines):
    quotes = QUOTE.findall(line)
    if not quotes:
        continue
    srcs = []
    for m in ANCHOR.finditer(line):
        r = read_range(m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)))
        if r:
            srcs.append(("anchor", m.group(0), r))
    for hm in HASH.finditer(line):
        _, msg = git("show", "-s", "--format=%B", hm.group(1))
        _, patch = git("show", "--format=", hm.group(1))
        srcs.append(("commit-msg", hm.group(1), msg))
        srcs.append(("commit-diff", hm.group(1), patch))
    for q in quotes:
        quote_total += 1
        hit = [s for s in srcs if q in s[2]]
        if hit:
            quote_ok += 1
        else:
            quote_fail.append((i + 1, q))

# ---- inline diff quotes in backticks that start with -/+ (claimed diff lines)
for i, line in enumerate(lines):
    for dm in re.finditer(r"`([-+] {4}[^`]+)`", line):
        quote_total += 1
        ok = False
        for hm in HASH.finditer(line):
            _, patch = git("show", "--format=", hm.group(1))
            if dm.group(1) in patch:
                ok = True
        if ok:
            quote_ok += 1
        else:
            quote_fail.append((i + 1, dm.group(1)))

print(f"anchors: {anchor_ok}/{anchor_total}")
for f in anchor_fail:
    print("  ANCHOR FAIL", f)
print(f"commits: {hash_ok}/{hash_total}")
for f in hash_fail:
    print("  HASH FAIL", f)
print(f"quotes: {quote_ok}/{quote_total}")
for f in quote_fail:
    print("  QUOTE FAIL", f)
