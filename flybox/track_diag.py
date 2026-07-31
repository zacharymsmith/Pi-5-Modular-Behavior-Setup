"""Deep per-frame diagnostics for a single recording: WHERE coverage gaps and
ID churn happen, so fixes target real failure moments rather than averages."""
from __future__ import annotations
import sys, cv2, numpy as np
sys.path.insert(0, '.')
from tracker import Tracker
import track_bench as tb


def diag(path, expected, stride=2, label="", **tk):
    cap = cv2.VideoCapture(path)
    ref = tb.build_ref_frames(cap, 25)
    t = Tracker(); t.enabled = True; t.expected_flies = expected
    for k, v in tk.items(): setattr(t, k, v)
    try: t.auto_arena(ref[12])
    except Exception: pass
    t.build_background(ref)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    counts, idsets = [], []
    id_first, id_frames = {}, {}
    fidx = proc = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        fidx += 1
        if (fidx - 1) % stride: continue
        _, tr = t.process(f)
        ids = {x["id"] for x in tr if not x.get("coasting", False)}
        counts.append(len(ids)); idsets.append(ids)
        for i in ids:
            id_first.setdefault(i, proc); id_frames[i] = id_frames.get(i, 0) + 1
        proc += 1
    cap.release()
    c = np.array(counts)

    # gap runs (count < expected) -> opto-blind stretches
    gaps = []
    i = 0
    while i < len(c):
        if c[i] < expected:
            j = i
            while j < len(c) and c[j] < expected: j += 1
            gaps.append((i, j - 1, j - i)); i = j
        else: i += 1
    # churn: IDs born after warmup (frame>50) = re-identifications
    births = sorted([(fr, i) for i, fr in id_first.items() if fr > 50])
    # persistent IDs (the "true" flies) vs transient
    ranked = sorted(id_frames.items(), key=lambda kv: kv[1], reverse=True)

    print(f"\n===== {label}  ({proc} frames, stride {stride}, expect {expected}) =====")
    print(f"coverage(>=exp) {np.mean(c>=expected):6.1%} | exact {np.mean(c==expected):6.1%} "
          f"| over {np.mean(c>expected):5.1%} | mean {c.mean():.2f}")
    print(f"distinct IDs {len(id_frames)} | churn(re-IDs) {len(births)} | gap-runs {len(gaps)}")
    print("top IDs (id: %span):", ", ".join(f"{i}:{n/proc:.0%}" for i, n in ranked[:expected+2]))
    if gaps:
        gaps.sort(key=lambda g: g[2], reverse=True)
        print("longest gap-runs (frame_start..end xlen):",
              ", ".join(f"{a}..{b}x{L}" for a, b, L in gaps[:6]))
        print(f"gap frames total {sum(g[2] for g in gaps)} | median gap len {int(np.median([g[2] for g in gaps]))}")
    if births:
        print("re-ID births (frame:newid):", ", ".join(f"{fr}:{i}" for fr, i in births[:12]))
    return dict(proc=proc, cov=float(np.mean(c>=expected)), ids=len(id_frames),
                churn=len(births), gaps=gaps, births=births, ranked=ranked)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "A"
    extra = {}
    for a in sys.argv[2:]:
        k, v = a.split("="); extra[k] = int(v) if v.isdigit() else float(v)
    if which == "A":
        diag(tb.V["A"], 2, label="150419 2-fly", **extra)
    else:
        diag(tb.V["B"], 3, label="152040 3-fly", **extra)
