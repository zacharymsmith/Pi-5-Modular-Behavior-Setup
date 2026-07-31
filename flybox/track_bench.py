"""Rigorous tracker benchmark — runs the REAL Tracker over real recordings and
perturbations, reporting the metrics that matter for closed-loop opto:

  coverage   = fraction of frames where all `expected` flies are detected
               (a miss = a frame where opto can't fire -> the number to maximise)
  total_ids  = distinct ID labels ever assigned; ideal == expected.
               (total_ids - expected) = churn / re-identifications ("ID switches")
  gaps       = frames with fewer than `expected` confirmed tracks (opto-blind frames)
  longest    = the two longest-lived IDs and how much of the video they each span

Runs headless from the sandbox; no camera / picamera2 needed.
"""
from __future__ import annotations
import sys, time, cv2, numpy as np
sys.path.insert(0, '.')
from tracker import Tracker


def build_ref_frames(cap, n=25):
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ids = np.linspace(0, max(1, total - 1), n).astype(int)
    frs = []
    for i in ids:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i)); ok, f = cap.read()
        if ok: frs.append(f)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return frs


def run(path, expected=2, resize=None, stride=1, noise=0.0, use_arena=True,
        max_frames=None, label=""):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"label": label, "error": "cannot open"}
    frs = build_ref_frames(cap)
    if resize:
        frs = [cv2.resize(f, resize) for f in frs]
    t = Tracker(); t.enabled = True; t.expected_flies = expected
    if use_arena:
        try: t.auto_arena(frs[len(frs) // 2])
        except Exception: pass
    t.build_background(frs)

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    seen_ids, count_hist, id_frames = set(), [], {}
    fidx = proc = 0
    t0 = time.time()
    while True:
        ok, f = cap.read()
        if not ok: break
        fidx += 1
        if (fidx - 1) % stride: continue
        if resize: f = cv2.resize(f, resize)
        if noise: f = np.clip(f.astype(np.int16) +
                              np.random.normal(0, noise, f.shape).astype(np.int16),
                              0, 255).astype(np.uint8)
        _, tracks = t.process(f)
        ids = {tr["id"] for tr in tracks if not tr.get("coasting", False)}
        count_hist.append(len(ids))
        seen_ids |= ids
        for i in ids: id_frames[i] = id_frames.get(i, 0) + 1
        proc += 1
        if max_frames and proc >= max_frames: break
    dt = time.time() - t0
    cap.release()

    ch = np.array(count_hist) if count_hist else np.array([0])
    cov = float(np.mean(ch >= expected))
    gaps = int(np.sum(ch < expected))
    top = sorted(id_frames.values(), reverse=True)[:expected]
    top_span = [round(v / proc, 3) for v in top] if proc else []
    return {
        "label": label, "frames": proc, "res": resize or "native",
        "expected": expected, "coverage": round(cov, 4),
        "total_ids": len(seen_ids), "churn": len(seen_ids) - expected,
        "gap_frames": gaps, "mean_count": round(float(ch.mean()), 3),
        "top_id_span": top_span, "proc_fps": round(proc / dt, 0) if dt else 0,
    }


V = {
    "A": "../test_examples/drive-download-20260730T195442Z-1-001/20260730_150419_two_gtacr_gr64f_chrimson_on_atr_30jul26/20260730_150419.mp4",
    "B": "../test_examples/drive-download-20260730T195442Z-1-001/20260730_152040_two_gtacr_gr64f_chrimson_on_atr_novel_atr_male_add/20260730_152040.mp4",
    "C": "../test_examples/10jul26/flybox/recordings/20260708_145923.mp4",
    "D": "../test_examples/13jul26/20260713_130514.mp4",
    "E": "../test_examples/10jul26/flybox/recordings/20260709_151639_09Jul2026_test2/20260709_151639.mp4",
    "F": "../test_examples/10jul26/flybox/recordings/20260709_100737.mp4",
}

# 10 rigorous iterations. The two 31k-frame 50fps videos (A,B) are sampled at
# stride 3 so a batch fits the sandbox time budget while still spanning the ENTIRE
# timeline (every merge/interaction); short videos run every frame.
ITER = {
    1:  ("1  A 800x600@50 full-span (2-fly today)", dict(path=V["A"], expected=2, stride=3)),
    2:  ("2  B 800x600@50 full-span (2-fly today)", dict(path=V["B"], expected=2, stride=3)),
    3:  ("3  C 1024x768@30 native (2-fly)",         dict(path=V["C"], expected=2)),
    4:  ("4  D 1024x768@15 native (2-fly)",         dict(path=V["D"], expected=2)),
    5:  ("5  E 1332x990@12 native (2-fly)",         dict(path=V["E"], expected=2)),
    6:  ("6  F 1024x768@21 native (2-fly)",         dict(path=V["F"], expected=2)),
    7:  ("7  A stride6 (big frame jumps stress)",   dict(path=V["A"], expected=2, stride=6)),
    8:  ("8  B +sensor noise sigma8",               dict(path=V["B"], expected=2, stride=3, noise=8.0)),
    9:  ("9  A downscaled 640x480 (res-indep)",     dict(path=V["A"], expected=2, stride=3, resize=(640, 480))),
    10: ("10 A upscaled 1024x768 (res-indep)",      dict(path=V["A"], expected=2, stride=3, resize=(1024, 768))),
}

if __name__ == "__main__":
    import json
    sel = [int(x) for x in sys.argv[1:]] or list(ITER)
    out = "/tmp/bench_results.jsonl"
    for k in sel:
        name, kw = ITER[k]; kw = dict(kw); kw["label"] = name
        r = run(**kw); r["n"] = k
        with open(out, "a") as fh:
            fh.write(json.dumps(r) + "\n")
        if "error" in r:
            print(f"{name:42s}  ERROR {r['error']}", flush=True); continue
        spans = "/".join(f"{s:.0%}" for s in r["top_id_span"])
        print(f"{name:42s} f={r['frames']:6d} cov={r['coverage']:6.1%} "
              f"ids={r['total_ids']:3d} churn={r['churn']:3d} gaps={r['gap_frames']:5d} "
              f"spans={spans:>10} {r['proc_fps']:4.0f}fps", flush=True)
