#!/usr/bin/env python3
"""CAFE dashboard video — the capstone view. Renders an mp4 with the capillary port on
the left and the meniscus drawdown graph drawing itself in sync on the right, with
confirmed feeding events lighting up and a moving time cursor. 2 h compressed to ~15 s.

Run:  python3 cafe_dashboard.py video.mp4 [--samples 260] [--fps 20]
Reads the meniscus line from <video>_lines.csv if present, else uses the built-in RIGHT
tube line; overlays confirmed meals from <video>_meals.csv if present.
"""
import sys, csv, argparse, subprocess
from pathlib import Path
import numpy as np, cv2

# both capillaries for the 06Aug rig (edit per layout). LINE start = tip/liquid side.
LINES = {"LEFT":  ((560, 388), (392, 386)),
         "RIGHT": ((1216, 402), (1400, 404))}
PORT_CROPS = {"LEFT":  (250, 260, 730, 500),      # x0,y0,x1,y1 around each capillary port
              "RIGHT": (1040, 260, 1520, 500)}
COLS = {"LEFT": (60, 60, 200), "RIGHT": (60, 170, 60), "CONTROL": (165, 110, 58)}   # BGR
PANEL_H = 340


def sample_line(g, p0, p1, width=5, npts=400):
    (x0, y0), (x1, y1) = p0, p1
    xs = np.linspace(x0, x1, npts); ys = np.linspace(y0, y1, npts)
    dx, dy = x1 - x0, y1 - y0; L = np.hypot(dx, dy) or 1.0; nx, ny = -dy / L, dx / L
    acc = np.zeros(npts)
    for o in range(-width, width + 1):
        sx = np.clip((xs + nx * o).astype(int), 0, g.shape[1] - 1)
        sy = np.clip((ys + ny * o).astype(int), 0, g.shape[0] - 1)
        acc += g[sy, sx]
    return np.linspace(0, L, npts), acc / (2 * width + 1)


def meniscus(g, p0, p1, width=5, thr_frac=0.5, lo=0.0, hi=1.0):
    dist, prof = sample_line(g, p0, p1, width)
    L = dist[-1] or 1.0
    m = (dist >= lo * L) & (dist <= hi * L)
    if m.sum() < 4: return np.nan
    d = dist[m]; p = cv2.GaussianBlur(prof[m].reshape(-1, 1), (1, 9), 0).ravel()
    thr = p.min() + thr_frac * (p.max() - p.min())
    below = p < thr
    for i in range(1, len(p)):
        if below[i - 1] and not below[i]:
            f = (thr - p[i - 1]) / (p[i] - p[i - 1] + 1e-6)
            return d[i - 1] + f * (d[i] - d[i - 1])
    return np.nan


def draw_graph(PW, PH, t, series, upto, ymax, meals, ylab="meniscus receded (px)"):
    """cv2-drawn recession graph (both sides) revealed up to index `upto`. `series` is
    name -> (recession array, BGR colour)."""
    img = np.full((PH, PW, 3), 255, np.uint8)
    ml, mr, mt, mb = 58, 14, 30, 38
    x0, x1p, y0p, y1p = ml, PW - mr, mt, PH - mb
    Tmax = t[-1] if len(t) else 1
    def X(tt): return int(x0 + (x1p - x0) * tt / Tmax)
    def Y(v): return int(y1p - (y1p - y0p) * (v / (ymax or 1)))
    cv2.rectangle(img, (x0, y0p), (x1p, y1p), (220, 220, 220), 1)
    for gx in range(0, int(Tmax / 60) + 1, 20):
        cv2.line(img, (X(gx * 60), y0p), (X(gx * 60), y1p), (238, 238, 238), 1)
        cv2.putText(img, f"{gx}", (X(gx*60)-8, y1p+20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)
    cv2.putText(img, ylab, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
    now_t = t[upto] if upto < len(t) else Tmax
    for cap_k, ms in meals:
        cv2.line(img, (X(ms), y0p), (X(ms), y1p),
                 (60, 170, 60) if ms <= now_t else (228, 242, 228), 1)
    for name, (rec, col) in series.items():
        pts = [(X(t[i]), Y(rec[i])) for i in range(upto + 1) if not np.isnan(rec[i])]
        for i in range(1, len(pts)): cv2.line(img, pts[i-1], pts[i], col, 2)
        if pts: cv2.circle(img, pts[-1], 4, col, -1)
    if upto < len(t):
        cx = X(now_t); cv2.line(img, (cx, y0p), (cx, y1p), (200, 120, 40), 1)
    # legend + clock
    ly = mt + 14
    for name, (_, col) in series.items():
        cv2.line(img, (x1p-150, ly), (x1p-130, ly), col, 3)
        cv2.putText(img, name, (x1p-124, ly+4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60), 1); ly += 18
    cv2.putText(img, f"t={int(now_t//60)}:{int(now_t%60):02d}", (x0+6, mt+14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 120, 40), 2)
    return img


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video")
    ap.add_argument("--samples", type=int, default=240); ap.add_argument("--fps", type=int, default=20)
    a = ap.parse_args()
    import json
    video = Path(a.video); base = video.with_suffix("")
    cap = cv2.VideoCapture(str(video)); nf = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    meals = []
    mp = Path(f"{base}_meals.csv")
    if mp.exists(): meals = [(r["capillary"], float(r["start_s"])) for r in csv.DictReader(open(mp))]

    # prefer the exact lines you drew in the meniscus GUI; fall back to built-in defaults
    lj = Path(f"{base}_lines.json"); H, W = int(cap.get(4)), int(cap.get(3))
    if lj.exists():
        D = json.load(open(lj))
        lines = {n: (tuple(v["p0"]), tuple(v["p1"])) for n, v in D.items()}
        params = {n: (v.get("width", 5), v.get("thr", 0.5), v.get("lo", 0.0), v.get("hi", 1.0)) for n, v in D.items()}
        def cropbox(p0, p1, pad=150):
            xs, ys = [p0[0], p1[0]], [p0[1], p1[1]]
            return (max(0, int(min(xs)-pad)), max(0, int(min(ys)-pad)),
                    min(W, int(max(xs)+pad)), min(H, int(max(ys)+pad)))
        crop_boxes = {n: cropbox(*lines[n]) for n in lines}
        print(f"using drawn lines from {lj.name}: {list(lines)}")
    else:
        lines = LINES; params = {n: (5, 0.5, 0.0, 1.0) for n in LINES}; crop_boxes = PORT_CROPS
        print("no *_lines.json — using built-in default lines/crops")

    idxs = np.linspace(0, nf - 2, a.samples).astype(int)
    print(f"sampling {a.samples} timepoints ({len(lines)} side(s))…")
    sides = list(lines)
    ts = []; pos = {k: [] for k in sides}; crops = {k: [] for k in sides}
    for k, fi in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi)); ok, f = cap.read()
        if not ok: break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY); ts.append(fi / fps)
        for s in sides:
            wdt, thr, lo, hi = params[s]
            pos[s].append(meniscus(g, lines[s][0], lines[s][1], wdt, thr, lo, hi))
            x0, y0, x1, y1 = crop_boxes[s]; crops[s].append(f[y0:y1, x0:x1].copy())
        if k % 40 == 0: print(f"  {100*k/a.samples:.0f}%", end="\r", flush=True)
    cap.release()
    t = np.array(ts)
    rec = {}
    for s in sides:
        p = np.array(pos[s], float); rec[s] = np.nan_to_num(p - np.nanmean(p[:3]), nan=0.0)
    # graph: if a no-fly CONTROL line is present, plot evaporation-corrected intake
    # (fed - CONTROL); otherwise plot raw meniscus recession per side.
    if "CONTROL" in rec:
        gseries = {f"{fed}-ctrl": (rec[fed] - rec["CONTROL"], COLS.get(fed, (110, 110, 110)))
                   for fed in ("RIGHT", "LEFT") if fed in rec}
        ylab = "intake (px): fed - CONTROL"
    else:
        gseries = {s: (rec[s], COLS.get(s, (110, 110, 110))) for s in sides}
        ylab = "meniscus receded (px)"
    ymax = max(1.0, max(np.nanmax(v[0]) for v in gseries.values()) * 1.15)
    labels = {"LEFT": "LEFT port (fed)", "RIGHT": "RIGHT port (fed)", "CONTROL": "CONTROL (no fly)"}

    def panel(crop, label, col):
        h, w = crop.shape[:2]; vp = cv2.resize(crop, (int(w * PANEL_H / h), PANEL_H))
        cv2.putText(vp, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2); return vp
    PW = 560
    widths = [int(crops[s][0].shape[1] * PANEL_H / crops[s][0].shape[0]) for s in sides]
    out = base.parent / f"{base.name}_dashboard.mp4"; tmp = base.parent / f"{base.name}_dashboard_tmp.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (sum(widths) + PW, PANEL_H))
    for i in range(len(t)):
        panels = [panel(crops[s][i], labels.get(s, f"{s} port"),
                        tuple(int(c) for c in COLS.get(s, (110, 110, 110)))) for s in sides]
        panels.append(draw_graph(PW, PANEL_H, t, gseries, i, ymax, meals, ylab))
        vw.write(np.hstack(panels))
    vw.release()
    # re-encode to browser-friendly H.264
    r = subprocess.run(["ffmpeg", "-y", "-i", str(tmp), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try: tmp.unlink(missing_ok=True)
    except Exception: pass
    print(f"\nsaved {out.name}")


if __name__ == "__main__":
    main()
