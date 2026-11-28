The inner page for the "L1 存储" box is built. It's all in `content/content_l1.py`, plus one fix suggestion for the top layer and a list of stage issues. Final numbers: 0 build WARNs, 0 layout issues, and `--all` passed with 566 PASS / 0 FAIL. I used 2 of the 3 screenshot rounds.

## 1. What I drew and why

**The diagram: 6 boxes, 8 lines.**
- **Two outside callers** (dimmed): 传输模块 and 查找模块. They're there only so the reader can see where things come in.
- **暂存表 `_staging` and 常驻表 `_objects` as two separate boxes.** The draft says readers get most confused because an object's "state" isn't a field: it's which table the object is in, plus whether its lock is still valid. Drawing the two tables apart, and having "转正" be a line between them, puts that idea right on the picture.
- **预取控制器**: this is where "查 + 上锁" actually happens. It runs three steps (上锁 → 按前缀留 → 收尾), and its small graphic ticks them off one by one.
- **淘汰线程 + LRU as one box**, with a "每 1 秒" loop on it.

**What I left out:**
- **No box for the allocator.** Its two important facts (one batch either fully succeeds or all goes OUT_OF_MEMORY; it never evicts on the spot) are in the 暂存表 card and on the source page.
- **No separate box for TTLLock.** I tried one, but there was no phone layout without lines crossing. Its count and expiry time appear as values on each row in 常驻表 instead.
- **The StorageManager facade isn't a box.** Its fixed tag shows up in the 申请暂存 line's card and in the A2 step.
- **L2 and the ARC policy aren't covered at all.**

**The walkthrough (8 steps)** reuses the top layer's numbers: 36 MiB objects, a 20 GiB L1, TP=1. The steps are: A reserves 3 objects → A2 tries the same keys at once and is quietly skipped → A finishes copying and the objects are promoted, with the LRU order h2, h1, h0 → B's lookup locks them (count 1, expires t=300) → kept by prefix, returns 768 → L1 hits 80% and the eviction thread runs → B retrieves and releases its locks → alternative ending: B crashes and the locks expire.

**No deeper diagrams below this one.** Each box is either one function or a few short ones, so I went straight to four source pages:
- **写入：预留与转正**
- **读锁与 TTLLock**
- **预取：查 + 上锁**
- **淘汰一圈**

Each page first shows the data being worked on (field definitions, plus a small table of this example's values), then the code with notes pinned to the lines.

## 2. Where it's still weak

- **The eviction step uses a scenario I set up.** I assumed B waits in the queue while 453 other objects get written. That puts h0, h1 and h2 at the oldest end of the LRU list, so the step can show locked keys being skipped without using up any of the 91 slots. It's labelled 设 on the page, but it's a big jump. The numbers come from the draft's own estimate: 456 objects, 91 deleted, about 64% left.
- **I made up t=90 for the retrieve.** The draft uses t=2 for its normal case, which didn't fit after that long wait. It's labelled 设.
- **The objects box mostly shows ready / locked.** It only briefly holds the state where a write was abandoned (in the eviction step's card and on the source page).
- **On the phone, the step panel covers the bottom half of the diagram.** That's how the stage works, so readers have to scroll.

## 3. Inferences and gaps in the research

- **Marked as 推断 (inference):**
  - Why the write lock is checked before promotion.
  - The benefit of polling for eviction.
  - That the TTLLock's increment and expiry refresh aren't a single atomic step.
  - That OOM never wakes the eviction thread (a conclusion from reading the code).
- **Not stated anywhere in the repo:** why the defaults are 300 / 600 / 0.8 / 0.2 / 1 second. The pages say so.
- **No test or comment covers this case:** after the same-tag handover, what the old writer's `finish_write` means.
- Nothing is missing that would block the explanation.

## 4. One fix for the top layer (`top-fixes-l1.md`)

The card on the `w_rw` line says promotion notifies the 淘汰控制器. It actually notifies the LRU through its listener bridge (and the store controller when L2 exists), not the eviction controller.

## 5. Stage issues (`framework-issues-l1.md`)

- `.cpp` and `.rst` files can't be cited in cards, so I cite the TTLLock header (`.h`) and put the `.cpp` code on the source page.
- `g_kv` pushes the value to the far right in wide boxes, which leaves a big gap.
- There's no small table graphic (several columns like key / lock / count / expiry).

Files are in `/root/canvas-duo-runs/LMCache-l1data-20261128/content/`:
- content_l1.py
- top-fixes-l1.md
- framework-issues-l1.md