"""CAFE feeding-EVENT screener: flags candidate feeding windows at each capillary tip
so you can jump straight to them and confirm. Signal = darkening of a small bright-dish
patch at the tip when a fly's body occupies it (20th-percentile intensity). Robust because
it avoids the dark rim entirely. Not a classifier — a search-narrowing tool.

Usage:  python3 cafe_events.py <start_frame> <end_frame>     # appends samples to CSV
        python3 cafe_events.py report                         # detect events + plot
"""
import sys, cv2, numpy as np, csv, os

VID = "../test_examples/20260806_095436_cafe_test_06Aug2026/20260806_095436.mp4"
CSV = "/tmp/cafe_events.csv"
STRIDE = 5                                   # sample ~1 Hz (5 fps recording)
RROI = (1150, 330, 1215, 388)                # bright-dish patch just inside RIGHT tip
LROI = (565, 360, 628, 412)                  # bright-dish patch just inside LEFT tip
DARK = 120                                   # p20 below this => a fly is on the patch


def p20(f, roi):
    x0, y0, x1, y1 = roi
    g = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return float(np.percentile(g, 20))


def sample(start, end):
    cap = cv2.VideoCapture(VID); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    new = not os.path.exists(CSV)
    fh = open(CSV, "a", newline=""); w = csv.writer(fh)
    if new: w.writerow(["frame", "t_s", "Rp20", "Lp20"])
    fi = start
    while fi < end:
        ok, f = cap.read()
        if not ok: break
        if (fi - start) % STRIDE == 0:
            w.writerow([fi, round(fi / fps, 1), round(p20(f, RROI), 1), round(p20(f, LROI), 1)])
        fi += 1
    fh.close(); cap.release(); print(f"sampled {start}..{fi}")


def events(t, present, min_dur=3, max_gap=4):
    """contiguous present-runs (allowing small gaps) >= min_dur seconds."""
    out = []; i = 0; n = len(present)
    while i < n:
        if present[i]:
            j = i; gap = 0
            while j + 1 < n and (present[j + 1] or gap < max_gap):
                j += 1; gap = 0 if present[j] else gap + 1
            while j > i and not present[j]: j -= 1
            if t[j] - t[i] >= min_dur: out.append((t[i], t[j], t[j] - t[i]))
            i = j + 1
        else: i += 1
    return out


def report():
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    rows = sorted(csv.DictReader(open(CSV)), key=lambda r: int(r["frame"]))
    t = np.array([float(r["t_s"]) for r in rows])
    Rp = np.array([float(r["Rp20"]) for r in rows]); Lp = np.array([float(r["Lp20"]) for r in rows])
    eR = events(t, Rp < DARK); eL = events(t, Lp < DARK)
    def fmt(e): return ", ".join(f"{int(s//60)}:{int(s%60):02d}(+{int(d)}s)" for s, _, d in e)
    print(f"RIGHT candidate events ({len(eR)}): {fmt(eR)}")
    print(f"LEFT  candidate events ({len(eL)}): {fmt(eL)}")
    with open("/tmp/cafe_event_list.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["capillary", "start_mmss", "start_s", "dur_s"])
        for s, e, d in eR: w.writerow(["RIGHT", f"{int(s//60)}:{int(s%60):02d}", int(s), int(d)])
        for s, e, d in eL: w.writerow(["LEFT", f"{int(s//60)}:{int(s%60):02d}", int(s), int(d)])
    fig, ax = plt.subplots(2, 1, figsize=(13, 6), sharex=True)
    for a, p, e, name, col in [(ax[0], Rp, eR, "RIGHT tip", "#2a9d3f"),
                               (ax[1], Lp, eL, "LEFT tip", "#c0392b")]:
        a.plot(t/60, p, lw=.5, color="#333"); a.axhline(DARK, ls=":", color="gray")
        for s, en, d in e: a.axvspan(s/60, en/60, color=col, alpha=.35)
        a.set_ylabel(f"{name}\ndark-tail (p20)"); a.set_ylim(0, 210); a.invert_yaxis()
    ax[0].set_title("CAFE candidate feeding events (shaded = fly at tip -> go confirm)")
    ax[1].set_xlabel("time (min)")
    plt.tight_layout(); plt.savefig("/sessions/amazing-exciting-knuth/mnt/outputs/cafe_events.png", dpi=120)
    print("saved cafe_events.png + cafe_event_list.csv")


if __name__ == "__main__":
    if sys.argv[1] == "report": report()
    else: sample(int(sys.argv[1]), int(sys.argv[2]))
