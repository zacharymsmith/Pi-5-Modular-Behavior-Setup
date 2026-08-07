#!/usr/bin/env python3
"""CAFE summary stats — computed entirely from files the pipeline already produced.

Reads next to the video:
  <video>_meals.csv     confirmed feeding bouts (from the scorer)
  <video>_meniscus.csv  meniscus positions over time (from the meniscus tracker)

Reports per capillary: bouts, total feeding time, mean/median/longest bout, meals/hour,
side-preference indices, and meniscus drawdown / net intake (optionally in µL).
Writes <video>_stats.csv + <video>_stats.png.

Run:  python3 cafe_stats.py video.mp4 [--mm_per_px M --bore_mm B]
"""
import sys, csv, argparse
from pathlib import Path
import numpy as np, cv2


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video")
    ap.add_argument("--mm_per_px", type=float, default=0.0)
    ap.add_argument("--bore_mm", type=float, default=0.0)
    a = ap.parse_args()
    base = Path(a.video).with_suffix("")
    cap = cv2.VideoCapture(a.video); dur_min = (cap.get(7) / (cap.get(5) or 5)) / 60.0; cap.release()

    meals = list(csv.DictReader(open(f"{base}_meals.csv"))) if Path(f"{base}_meals.csv").exists() else []
    sides = ["RIGHT", "LEFT"]
    S = {}
    for k in sides:
        d = [float(m["dur_s"]) for m in meals if m["capillary"] == k]
        t = [float(m["start_s"]) for m in meals if m["capillary"] == k]
        S[k] = {
            "bouts": len(d),
            "total_time_s": round(sum(d), 1),
            "mean_bout_s": round(float(np.mean(d)), 1) if d else 0.0,
            "median_bout_s": round(float(np.median(d)), 1) if d else 0.0,
            "longest_bout_s": round(max(d), 1) if d else 0.0,
            "meals_per_hour": round(len(d) / dur_min * 60, 1) if dur_min else 0.0,
            "first_meal_min": round(min(t) / 60, 1) if t else None,
            "last_meal_min": round(max(t) / 60, 1) if t else None,
        }

    def pref(rv, lv):
        return round((rv - lv) / (rv + lv), 3) if (rv + lv) else 0.0
    pref_count = pref(S["RIGHT"]["bouts"], S["LEFT"]["bouts"])
    pref_time = pref(S["RIGHT"]["total_time_s"], S["LEFT"]["total_time_s"])

    # meniscus (consumption)
    men = {}
    mp = Path(f"{base}_meniscus.csv")
    if mp.exists():
        rows = list(csv.DictReader(open(mp)))
        def col(c): return np.array([float(r[c]) for r in rows]) if c in rows[0] else None
        for k in sides:
            rec = col(f"{k}_receded_px")
            if rec is not None: men[k] = float(np.nanmedian(rec[-5:]))
        net = col("net_intake_px")
        if net is not None: men["net_intake_px"] = float(np.nanmedian(net[-5:]))

    # ---- print ----
    print(f"\n===== CAFE stats — {Path(a.video).name}  ({dur_min:.1f} min) =====")
    for k in sides:
        s = S[k]
        print(f"{k:5s}: {s['bouts']:2d} bouts | {s['total_time_s']:6.1f} s feeding | "
              f"mean {s['mean_bout_s']:4.1f}s med {s['median_bout_s']:4.1f}s max {s['longest_bout_s']:4.1f}s | "
              f"{s['meals_per_hour']:.1f}/h")
    tot_b = S['RIGHT']['bouts'] + S['LEFT']['bouts']; tot_t = S['RIGHT']['total_time_s'] + S['LEFT']['total_time_s']
    print(f"TOTAL: {tot_b} bouts | {tot_t:.1f} s feeding")
    print(f"preference (RIGHT vs LEFT):  by bouts {pref_count:+.2f}   by time {pref_time:+.2f}   "
          f"(+1=all right, -1=all left)")
    if men:
        r, l = men.get("RIGHT"), men.get("LEFT")
        print(f"meniscus receded:  RIGHT {r:.1f}px  LEFT {l:.1f}px  net(R-L) {men.get('net_intake_px',0):.1f}px")
        if a.mm_per_px and a.bore_mm:
            area = np.pi * (a.bore_mm/2)**2
            print(f"volume:  RIGHT {r*a.mm_per_px*area:.3f} µL  LEFT {l*a.mm_per_px*area:.3f} µL  "
                  f"net {men.get('net_intake_px',0)*a.mm_per_px*area:+.3f} µL")

    # ---- save CSV ----
    with open(f"{base}_stats.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["metric", "RIGHT", "LEFT"])
        for key in ["bouts", "total_time_s", "mean_bout_s", "median_bout_s", "longest_bout_s",
                    "meals_per_hour", "first_meal_min", "last_meal_min"]:
            w.writerow([key, S["RIGHT"][key], S["LEFT"][key]])
        if men: w.writerow(["meniscus_receded_px", round(men.get("RIGHT", 0), 1), round(men.get("LEFT", 0), 1)])
        w.writerow(["preference_by_bouts", pref_count, ""])
        w.writerow(["preference_by_time", pref_time, ""])

    # ---- figure ----
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    col = {"RIGHT": "#2a9d3f", "LEFT": "#c0392b"}
    panels = [("bouts", "feeding bouts (n)"), ("total_time_s", "total feeding time (s)"),
              ("mean_bout_s", "mean bout (s)")]
    ncol = 4 if men else 3
    fig, ax = plt.subplots(1, ncol, figsize=(3.3*ncol, 4))
    for i, (key, title) in enumerate(panels):
        ax[i].bar(sides, [S[k][key] for k in sides], color=[col[k] for k in sides])
        ax[i].set_title(title); ax[i].grid(axis="y", alpha=.3)
        for j, k in enumerate(sides): ax[i].text(j, S[k][key], f"{S[k][key]:g}", ha="center", va="bottom")
    if men:
        vals = [men.get("RIGHT", 0), men.get("LEFT", 0)]
        ax[3].bar(sides, vals, color=[col[k] for k in sides])
        ax[3].set_title("meniscus receded (px)\n(evaporation-dominated)"); ax[3].grid(axis="y", alpha=.3)
        for j, v in enumerate(vals): ax[3].text(j, v, f"{v:.1f}", ha="center", va="bottom")
    fig.suptitle(f"CAFE stats — {Path(a.video).name}  ·  {dur_min:.0f} min", weight="bold")
    fig.tight_layout(); fig.savefig(f"{base}_stats.png", dpi=120)
    print(f"saved {base.name}_stats.csv + _stats.png")


if __name__ == "__main__":
    main()
