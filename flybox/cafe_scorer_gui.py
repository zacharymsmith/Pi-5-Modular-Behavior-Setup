#!/usr/bin/env python3
"""CAFE meal scorer — native desktop GUI.  No browser, no server, minimal setup.

Only needs: python3 (with tkinter, which ships with the standard installer),
opencv-python, and numpy.  Double-click the launcher or run:

    python3 cafe_scorer_gui.py            # a file picker opens
    python3 cafe_scorer_gui.py video.mp4  # or pass the recording directly

It scans the recording once for candidate capillary-port visits, then shows each
candidate as a looping clip (±5 s, tip boxed).  Click  ✓ Eating / ✗ Not / Skip
(or press Y / N / S; ← to go back).  Confirmed meals autosave to
<video>_meals.csv next to the recording.
"""
import sys, os, csv, base64, threading
from pathlib import Path
import numpy as np, cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:                                   # Pillow gives rock-solid frame display; PNG is the fallback
    from PIL import Image, ImageTk
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

# -------- config: bright-dish patch just inside each capillary tip (x0,y0,x1,y1) --------
TIPS = {"RIGHT": (1150, 330, 1215, 388), "LEFT": (565, 360, 628, 412)}
PRE, POST = 5.0, 5.0          # seconds padding around each event clip
STRIDE = 5                    # detection sampling (every Nth frame)
DARK = 120                    # p20 below this = a fly on the tip patch
STRONG = 95                   # a dip this dark = a definite fly, flagged even if brief
MIN_DUR, MAX_GAP = 3, 4       # sustained event >= ~3 s present, merge <= 4-sample gaps
DISP_W = 900                  # on-screen clip width (px)
CAP_COL = {"RIGHT": "#2a9d3f", "LEFT": "#c0392b"}


# ----------------------------------------------------------------- detection
def _p20(frame, roi):
    x0, y0, x1, y1 = roi
    return float(np.percentile(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), 20))


def _bouts(t, vals):
    """Candidate = a run below DARK that is EITHER sustained (>= ~MIN_DUR s) OR contains a
    definite fly (a sample below STRONG). The second clause keeps brief-but-clear touches
    (e.g. a quick left-port visit) that a duration-only rule would miss."""
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1.0     # tolerant to fps drift
    present = vals < DARK
    out, i, n = [], 0, len(vals)
    while i < n:
        if present[i]:
            j, gap = i, 0
            while j + 1 < n and (present[j + 1] or gap < MAX_GAP):
                j += 1; gap = 0 if present[j] else gap + 1
            while j > i and not present[j]: j -= 1
            span = t[j] - t[i]; run_min = float(np.min(vals[i:j + 1]))
            if span >= MIN_DUR - dt * 0.75 or run_min < STRONG:
                out.append((round(float(t[i]), 1), round(float(max(span, 1.0)), 1)))
            i = j + 1
        else: i += 1
    return out


def detect(video, events_csv, progress=None):
    if events_csv.exists():
        rows = list(csv.DictReader(open(events_csv)))
        return [dict(id=int(r["id"]), capillary=r["capillary"],
                     start_s=float(r["start_s"]), dur=float(r["dur"])) for r in rows]
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    series = {k: [] for k in TIPS}; ts = []; fi = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if fi % STRIDE == 0:
            ts.append(fi / fps)
            for k, roi in TIPS.items(): series[k].append(_p20(f, roi))
            if progress and fi % (STRIDE * 40) == 0: progress(fi / total)
        fi += 1
    cap.release()
    t = np.array(ts); evs = []
    for k in TIPS:
        for s, d in _bouts(t, np.array(series[k])):
            evs.append(dict(capillary=k, start_s=s, dur=d))
    evs.sort(key=lambda e: e["start_s"])
    for i, e in enumerate(evs): e["id"] = i
    with open(events_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, ["id", "capillary", "start_s", "dur"]); w.writeheader()
        for e in evs: w.writerow({k: e[k] for k in ["id", "capillary", "start_s", "dur"]})
    return evs


# ----------------------------------------------------------------- GUI app
class Scorer:
    def __init__(self, root, video):
        self.root = root; self.video = Path(video)
        base = self.video.with_suffix("")
        self.events_csv = Path(f"{base}_events.csv")
        self.scores_csv = Path(f"{base}_scores.csv")
        self.meals_csv = Path(f"{base}_meals.csv")
        self.cap = cv2.VideoCapture(str(self.video))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 5.0
        self.frames = []; self.fidx = 0; self.cur = 0; self.delay = 110; self._anim = None
        root.title(f"CAFE meal scorer — {self.video.name}")
        root.configure(bg="#0f1216")
        self._build_splash()
        threading.Thread(target=self._scan, daemon=True).start()

    # ---- initial scan ----
    def _build_splash(self):
        self.splash = tk.Frame(self.root, bg="#0f1216"); self.splash.pack(fill="both", expand=True, padx=40, pady=60)
        tk.Label(self.splash, text="Scanning recording for feeding candidates…",
                 fg="#e8eef5", bg="#0f1216", font=("Helvetica", 15)).pack(pady=10)
        self.pbar = ttk.Progressbar(self.splash, length=380, mode="determinate"); self.pbar.pack(pady=8)
        self.plabel = tk.Label(self.splash, text="0%", fg="#9aa7b4", bg="#0f1216"); self.plabel.pack()

    def _scan(self):
        def prog(fr): self.root.after(0, lambda: (self.pbar.config(value=fr * 100),
                                                  self.plabel.config(text=f"{int(fr*100)}%")))
        self.events = detect(self.video, self.events_csv, prog)
        self.scores = {int(r["id"]): r["label"] for r in csv.DictReader(open(self.scores_csv))} \
            if self.scores_csv.exists() else {}
        self.root.after(0, self._build_ui)

    # ---- main UI ----
    def _build_ui(self):
        self.splash.destroy()
        if not self.events:
            messagebox.showinfo("No candidates", "No candidate port visits were detected."); return
        top = tk.Frame(self.root, bg="#1a1f27"); top.pack(fill="x")
        self.info = tk.Label(top, text="", fg="#e8eef5", bg="#1a1f27", font=("Helvetica", 15, "bold"))
        self.info.pack(side="left", padx=14, pady=10)
        self.prog = tk.Label(top, text="", fg="#9aa7b4", bg="#1a1f27", font=("Helvetica", 12))
        self.prog.pack(side="right", padx=14)

        self.canvas = tk.Label(self.root, bg="#000"); self.canvas.pack(padx=10, pady=10)

        btns = tk.Frame(self.root, bg="#0f1216"); btns.pack(pady=(0, 6))
        # colored Label "buttons" — macOS Tk ignores bg on native Buttons, Labels honor it
        def cbtn(txt, col, cmd):
            b = tk.Label(btns, text=txt, bg=col, fg="white", font=("Helvetica", 15, "bold"),
                         width=14, height=2, cursor="hand2", relief="raised", bd=1)
            b.bind("<Button-1>", lambda e: cmd()); return b
        cbtn("✓ Eating  (Y)", "#2a9d3f", lambda: self.mark("eat")).pack(side="left", padx=6)
        cbtn("✗ Not  (N)", "#c0392b", lambda: self.mark("no")).pack(side="left", padx=6)
        cbtn("Skip  (S)", "#556677", lambda: self.mark("skip")).pack(side="left", padx=6)
        row2 = tk.Frame(self.root, bg="#0f1216"); row2.pack(pady=(0, 12))
        mk2 = lambda txt, cmd: tk.Button(row2, text=txt, bg="#2a3340", fg="#e8eef5", bd=0,
                                         width=16, command=cmd)
        mk2("⤺ Back (←)", self.back).pack(side="left", padx=6)
        mk2("⏩ Speed", self.speed).pack(side="left", padx=6)
        mk2("Next unscored »", self.jump_next).pack(side="left", padx=6)
        self.hint = tk.Label(self.root, text="Loops until you answer.  Y eat · N not · S skip · ← back",
                             fg="#9aa7b4", bg="#0f1216"); self.hint.pack(pady=(0, 10))

        for k in ("y", "Y"): self.root.bind(k, lambda e: self.mark("eat"))
        for k in ("n", "N"): self.root.bind(k, lambda e: self.mark("no"))
        for k in ("s", "S"): self.root.bind(k, lambda e: self.mark("skip"))
        self.root.bind("<Left>", lambda e: self.back())
        first = next((i for i, e in enumerate(self.events) if e["id"] not in self.scores), 0)
        self.load(first)

    # ---- clip playback ----
    def load(self, i):
        if self._anim: self.root.after_cancel(self._anim); self._anim = None
        self.cur = max(0, min(i, len(self.events) - 1)); e = self.events[self.cur]
        x0, y0, x1, y1 = TIPS[e["capillary"]]
        s0 = max(0, int((e["start_s"] - PRE) * self.fps))
        n = int((e["dur"] + PRE + POST) * self.fps)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, s0)
        self.frames = []
        for _ in range(n):
            ok, f = self.cap.read()
            if not ok: break
            cv2.rectangle(f, (x0, y0), (x1, y1), (0, 255, 255), 2)
            sc = DISP_W / f.shape[1]
            f = cv2.resize(f, (DISP_W, int(f.shape[0] * sc)))
            self.frames.append(self._photo(f))
        self.fidx = 0
        lab = self.scores.get(e["id"], "")
        mm = f"{int(e['start_s'])//60}:{int(e['start_s'])%60:02d}"
        self.info.config(text=f"  {e['capillary']}  ·  {mm}  ·  {e['dur']:.0f}s at tip",
                         fg=CAP_COL[e["capillary"]])
        n_eat = sum(1 for v in self.scores.values() if v == "eat")
        self.prog.config(text=f"event {self.cur+1}/{len(self.events)}  ·  "
                              f"{len(self.scores)}/{len(self.events)} scored  ·  {n_eat} meals"
                              + (f"  ·  [{lab}]" if lab else ""))
        self._tick()

    def _photo(self, bgr):
        if _HAVE_PIL:
            return ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        ok, buf = cv2.imencode(".png", bgr)     # PNG via base64 works on Tk 8.6+
        return tk.PhotoImage(data=base64.b64encode(buf.tobytes()).decode("ascii"))

    def _tick(self):
        if not self.frames: return
        self.canvas.config(image=self.frames[self.fidx])
        self.fidx = (self.fidx + 1) % len(self.frames)
        self._anim = self.root.after(self.delay, self._tick)

    def speed(self):
        self.delay = {110: 60, 60: 200, 200: 110}[self.delay]   # ~9 / ~16 / ~5 fps

    # ---- scoring ----
    def mark(self, label):
        e = self.events[self.cur]; self.scores[e["id"]] = label; self._save()
        nxt = next((i for i in range(self.cur + 1, len(self.events))
                    if self.events[i]["id"] not in self.scores), None)
        if nxt is None:
            nxt = next((i for i, ev in enumerate(self.events) if ev["id"] not in self.scores), None)
        if nxt is None:
            self._finish(); return
        self.load(nxt)

    def back(self): self.load(self.cur - 1)
    def jump_next(self):
        nxt = next((i for i, e in enumerate(self.events) if e["id"] not in self.scores), None)
        if nxt is not None: self.load(nxt)

    def _save(self):
        with open(self.scores_csv, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["id", "capillary", "start_s", "dur", "label"])
            for k in sorted(self.scores):
                e = next(x for x in self.events if x["id"] == k)
                w.writerow([k, e["capillary"], e["start_s"], e["dur"], self.scores[k]])
        with open(self.meals_csv, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["capillary", "start_s", "start_mmss", "dur_s"])
            for k in sorted(self.scores):
                if self.scores[k] == "eat":
                    e = next(x for x in self.events if x["id"] == k); s = int(e["start_s"])
                    w.writerow([e["capillary"], e["start_s"], f"{s//60}:{s%60:02d}", e["dur"]])

    def _finish(self):
        if self._anim: self.root.after_cancel(self._anim)
        n_eat = sum(1 for v in self.scores.values() if v == "eat")
        self.canvas.config(image="", text=f"\n\nAll {len(self.events)} candidates scored 🎉\n\n"
                           f"{n_eat} confirmed meals saved to\n{self.meals_csv.name}\n",
                           fg="#e8eef5", font=("Helvetica", 16))
        self.info.config(text="  Done"); self.hint.config(text="Close the window, or press ← to review.")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    if not path:
        path = filedialog.askopenfilename(title="Choose a CAFE recording",
                                          filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All", "*.*")])
        if not path: return
    Scorer(root, path)
    root.mainloop()


if __name__ == "__main__":
    main()
