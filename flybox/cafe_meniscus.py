#!/usr/bin/env python3
"""CAFE meniscus tracker — measures liquid drawdown in BOTH capillaries over time.

Method (drift-proof): for each capillary a fixed tube band is sampled over the run. In each
frame the tube is collapsed to a per-column brightness profile and split liquid-vs-empty with
a threshold computed *from that same frame* (midpoint of its own dark/bright levels). Because
the threshold adapts per frame, uniform illumination drift over the 2 h cancels out — only the
meniscus boundary actually moving changes the liquid-column length. (A fixed-brightness metric
is confounded by drift; that was verified and rejected.)

Outputs next to the video:
  <video>_meniscus.csv   t_s, RIGHT_liquid_px, LEFT_liquid_px, RIGHT_men_x, LEFT_men_x
  <video>_meniscus.png   drawdown curves for both sides (confirmed meals overlaid if present)

Run:  python3 cafe_meniscus.py video.mp4 [--every 25] [--mm_per_px M --bore_mm B]
With --mm_per_px and --bore_mm it also reports consumed volume in µL.
"""
import sys, csv, argparse
from pathlib import Path
import numpy as np, cv2

# tube bands (x0, x1, y0, y1): the liquid column of each capillary, away from arena/flies.
# Re-measure per rig layout (open a frame, read the box over each capillary tube).
BANDS = {"RIGHT": (1230, 1500, 388, 414), "LEFT": (250, 545, 384, 410)}
# which end of the band is the TIP (arena side): liquid recedes AWAY from the tip.
TIP_SIDE = {"RIGHT": "left", "LEFT": "right"}


def meniscus(band):
    """Return (liquid_length_px, meniscus_x_in_band) for one tube band (grayscale)."""
    col = band.mean(0).astype(np.float32)
    col = cv2.GaussianBlur(col.reshape(-1, 1), (1, 9), 0).ravel()
    thr = 0.5 * (col.min() + col.max())          # per-frame adaptive split (drift-proof)
    liquid = col < thr                            # liquid is darker than empty glass
    return int(liquid.sum()), liquid


def track(video, every, progress=True):
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    ts, out = [], {k: [] for k in BANDS}
    fi = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if fi % every == 0:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            ts.append(fi / fps)
            for k, (x0, x1, y0, y1) in BANDS.items():
                n, _ = meniscus(g[y0:y1, x0:x1])
                out[k].append(n)
            if progress and fi % (every * 60) == 0:
                print(f"  {100*fi/total:4.0f}%", end="\r", flush=True)
        fi += 1
    cap.release()
    t = np.array(ts)
    # median-smooth to suppress per-frame jitter
    def sm(a, w=9):
        a = np.array(a, float); k = w // 2
        return np.array([np.median(a[max(0, i-k):i+k+1]) for i in range(len(a))])
    return t, {k: sm(out[k]) for k in BANDS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--every", type=int, default=25, help="sample every Nth frame (25 = 5 s)")
    ap.add_argument("--mm_per_px", type=float, default=0.0, help="calibration: mm per pixel")
    ap.add_argument("--bore_mm", type=float, default=0.0, help="capillary internal bore (mm)")
    a = ap.parse_args()
    video = Path(a.video); base = video.with_suffix("")
    print(f"Tracking meniscus in both capillaries: {video.name}")
    t, sig = track(video, a.every)
    tmin = t / 60.0

    # drawdown = how much the liquid column shrank from its start (px receded)
    draw = {k: sig[k][:3].mean() - sig[k] for k in BANDS}     # +ve = receded (consumed)
    for k in BANDS:
        print(f"{k}: receded {draw[k][-3:].mean():+.1f} px over run "
              f"(start {sig[k][:3].mean():.0f}px liquid)")

    vol = {}
    if a.mm_per_px and a.bore_mm:
        area = np.pi * (a.bore_mm / 2) ** 2           # mm^2
        for k in BANDS:
            vol[k] = draw[k] * a.mm_per_px * area      # mm^3 = µL
            print(f"{k}: ~{vol[k][-3:].mean():.3f} µL consumed")

    # confirmed meals overlay (if the scorer produced them)
    meals = []
    mp = Path(f"{base}_meals.csv")
    if mp.exists():
        meals = [(r["capillary"], float(r["start_s"])) for r in csv.DictReader(open(mp))]

    with open(f"{base}_meniscus.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["t_s", "RIGHT_liquid_px", "LEFT_liquid_px"])
        for i in range(len(t)):
            w.writerow([round(t[i], 1), round(sig["RIGHT"][i], 1), round(sig["LEFT"][i], 1)])

    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5))
    col = {"RIGHT": "#2a9d3f", "LEFT": "#c0392b"}
    for k in BANDS:
        y = vol[k] if vol else draw[k]
        ax.plot(tmin, y, color=col[k], lw=2, label=f"{k}")
    for cap_k, s in meals:
        ax.axvline(s/60, color=col.get(cap_k, "#888"), alpha=0.18, lw=1)
    ax.set_xlabel("time (min)")
    ax.set_ylabel("consumed (µL)" if vol else "meniscus drawdown (px receded)")
    ax.set_title("CAFE meniscus drawdown — both capillaries"
                 + ("  (vertical lines = confirmed meals)" if meals else ""))
    ax.legend(); ax.grid(alpha=.3)
    plt.tight_layout(); plt.savefig(f"{base}_meniscus.png", dpi=120)
    print(f"saved {base.name}_meniscus.csv and _meniscus.png")


if __name__ == "__main__":
    main()
