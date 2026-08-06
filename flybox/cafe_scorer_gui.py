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
# detection is RELATIVE to each patch's own "empty" brightness, so it adapts to any
# framing/exposure (a fixed cutoff broke when the camera/exposure changed):
DROP = 16                     # present if the patch darkens this far below its empty baseline
STRONG_DROP = 38              # a drop this deep = a definite fly (flagged even if brief)
MIN_DUR, MAX_GAP = 3, 4       # sustained event >= ~3 s present, merge <= 4-sample gaps
MAX_EVENT_S = 120             # a real port visit is never minutes; longer = mis-placed patch, skip
MAX_CLIP_FRAMES = 260         # never load more than this many frames for one clip (memory safety)
DISP_W = 900                  # on-screen clip width (px)
CAP_COL = {"RIGHT": "#2a9d3f", "LEFT": "#c0392b"}


# ----------------------------------------------------------------- detection
def _p20(frame, roi):
    x0, y0, x1, y1 = roi
    return float(np.percentile(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), 20))


def _bouts(t, vals, dark, strong):
    """Candidate = a run below `dark` that is EITHER sustained (>= ~MIN_DUR s) OR contains a
    definite fly (a sample below `strong`). Thresholds are relative to the patch's own empty
    baseline (passed in), so detection adapts to each video's brightness."""
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1.0     # tolerant to fps drift
    present = vals < dark
    out, i, n = [], 0, len(vals)
    while i < n:
        if present[i]:
            j, gap = i, 0
            while j + 1 < n and (present[j + 1] or gap < MAX_GAP):
                j += 1; gap = 0 if present[j] else gap + 1
            while j > i and not present[j]: j -= 1
            span = t[j] - t[i]; run_min = float(np.min(vals[i:j + 1]))
            if span > MAX_EVENT_S:           # absurdly long = patch on rim/dark, not a visit
                i = j + 1; continue
            if span >= MIN_DUR - dt * 0.75 or run_min < strong:
                out.append((round(float(t[i]), 1), round(float(max(span, 1.0)), 1)))
            i = j + 1
        else: i += 1
    return out


def detect(video, events_csv, tips, progress=None):
    if events_csv.exists():
        rows = list(csv.DictReader(open(events_csv)))
        return [dict(id=int(r["id"]), capillary=r["capillary"],
                     start_s=float(r["start_s"]), dur=float(r["dur"])) for r in rows]
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    series = {k: [] for k in tips}; ts = []; fi = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if fi % STRIDE == 0:
            ts.append(fi / fps)
            for k, roi in tips.items(): series[k].append(_p20(f, roi))
            if progress and fi % (STRIDE * 40) == 0: progress(fi / total)
        fi += 1
    cap.release()
    t = np.array(ts); evs = []
    for k in tips:
        vals = np.array(series[k])
        base = np.percentile(vals, 85)        # this patch's "empty" (fly-free) brightness
        dark, strong = base - DROP, base - STRONG_DROP
        for s, d in _bouts(t, vals, dark, strong):
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
        self.tips_path = Path(f"{base}_tips.json")
        self.cap = cv2.VideoCapture(str(self.video))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 5.0
        self.nframes = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self.frames = []; self.fidx = 0; self.cur = 0; self.delay = 110; self._anim = None
        root.title(f"CAFE meal scorer — {self.video.name}")
        root.configure(bg="#0f1216")
        self.tips = self._load_tips()
        if self.tips:
            self._build_splash(); threading.Thread(target=self._scan, daemon=True).start()
        else:
            self._build_tipsetup()          # first time on this video: place the tip patches

    def _load_tips(self):
        import json
        if self.tips_path.exists():
            try: return {k: tuple(v) for k, v in json.load(open(self.tips_path)).items()}
            except Exception: pass
        return None

    # ---- tip-patch setup (per video / framing) ----
    def _build_tipsetup(self):
        self.tips = {}
        self.setup = tk.Frame(self.root, bg="#0f1216"); self.setup.pack(fill="both", expand=True)
        bar = tk.Frame(self.setup, bg="#1a1f27"); bar.pack(fill="x")
        tk.Label(bar, text="Drag a small box on the bright dish just INSIDE each capillary tip "
                 "(avoid the dark rim). Right, then Left.", fg="#e8eef5", bg="#1a1f27").pack(side="left", padx=8, pady=6)
        self.tipname = ttk.Combobox(bar, values=["RIGHT", "LEFT"], width=7); self.tipname.set("RIGHT")
        self.tipname.pack(side="left", padx=6)
        self.tstatus = tk.Label(bar, text="0/2 set", fg="#9aa7b4", bg="#1a1f27"); self.tstatus.pack(side="right", padx=8)
        tk.Button(bar, text="Start scan ▶", command=self._finish_tipsetup).pack(side="right", padx=8)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.nframes // 2); ok, f = self.cap.read()
        if not ok: self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0); ok, f = self.cap.read()
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY); disp = cv2.createCLAHE(3.0, (8, 8)).apply(g)
        self._frame_w, self._frame_h = g.shape[1], g.shape[0]
        dw = min(DISP_W, self._frame_w); dh = int(self._frame_h * dw / self._frame_w)
        im = cv2.resize(cv2.cvtColor(disp, cv2.COLOR_GRAY2RGB), (dw, dh))
        self.tcanvas = tk.Canvas(self.setup, bg="#000", highlightthickness=0)
        self.tcanvas.pack(padx=8, pady=8)
        self._tphoto = ImageTk.PhotoImage(Image.fromarray(im))
        self._img_id = self.tcanvas.create_image(0, 0, anchor="nw", image=self._tphoto)
        self.root.update_idletasks()
        bb = self.tcanvas.bbox(self._img_id)          # the image's REAL on-screen size (Retina-safe)
        self.tcanvas.config(width=bb[2]-bb[0], height=bb[3]-bb[1])
        self._tstart = None
        self.tcanvas.bind("<Button-1>", lambda e: setattr(self, "_tstart", (e.x, e.y)))
        self.tcanvas.bind("<B1-Motion>", self._tdrag)
        self.tcanvas.bind("<ButtonRelease-1>", self._tup)

    def _c2f(self, x, y):     # canvas point -> full-res pixel via the image's actual displayed bbox
        b = self.tcanvas.bbox(self._img_id); w = max(1, b[2]-b[0]); h = max(1, b[3]-b[1])
        return (max(0, min(self._frame_w, int((x-b[0]) / w * self._frame_w))),
                max(0, min(self._frame_h, int((y-b[1]) / h * self._frame_h))))

    def _f2c(self, box):      # full-res box -> canvas coords for drawing it back
        b = self.tcanvas.bbox(self._img_id)
        sx = (b[2]-b[0]) / self._frame_w; sy = (b[3]-b[1]) / self._frame_h
        return (b[0]+box[0]*sx, b[1]+box[1]*sy, b[0]+box[2]*sx, b[1]+box[3]*sy)

    def _tdrag(self, e):
        self.tcanvas.delete("live")
        if self._tstart:
            self.tcanvas.create_rectangle(*self._tstart, e.x, e.y, outline="#ff0", width=2, tags="live")

    def _tup(self, e):
        if not self._tstart: return
        a = self._c2f(*self._tstart); b = self._c2f(e.x, e.y)
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        if box[2]-box[0] < 6 or box[3]-box[1] < 6: return
        self.tips[self.tipname.get() or "RIGHT"] = box
        self.tcanvas.delete("live"); self.tcanvas.delete("box")
        for nm, bx in self.tips.items():
            c = self._f2c(bx)
            self.tcanvas.create_rectangle(*c, outline="#ff0", width=2, tags="box")
            self.tcanvas.create_text(c[0]+4, c[1]-8, text=nm, fill="#ff0", anchor="w", tags="box")
        self.tstatus.config(text=f"{len(self.tips)}/2 set")
        self.tipname.set("LEFT" if self.tipname.get() == "RIGHT" else "RIGHT")

    def _finish_tipsetup(self):
        if not self.tips:
            messagebox.showinfo("Set tips", "Drag at least one tip box first."); return
        import json
        json.dump({k: list(v) for k, v in self.tips.items()}, open(self.tips_path, "w"), indent=2)
        self.events_csv.unlink(missing_ok=True)      # force re-detect with the new tips
        self.setup.destroy()
        self._build_splash(); threading.Thread(target=self._scan, daemon=True).start()

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
        self.events = detect(self.video, self.events_csv, self.tips, prog)
        self.scores = {int(r["id"]): r["label"] for r in csv.DictReader(open(self.scores_csv))} \
            if self.scores_csv.exists() else {}
        self.root.after(0, self._build_ui)

    def _rescan(self, redo_tips=False):
        if self._anim: self.root.after_cancel(self._anim); self._anim = None
        try: self.events_csv.unlink(missing_ok=True)
        except Exception: pass
        if redo_tips:
            try: self.tips_path.unlink(missing_ok=True)
            except Exception: pass
        for w in list(self.root.winfo_children()): w.destroy()
        self.tips = self._load_tips()
        if self.tips:
            self._build_splash(); threading.Thread(target=self._scan, daemon=True).start()
        else:
            self._build_tipsetup()

    # ---- main UI ----
    def _build_ui(self):
        self.splash.destroy()
        if not self.events:
            if messagebox.askyesno("No candidates",
                    "No candidate port visits detected.\n\nRe-place the tip patches and try again?"):
                self._rescan(redo_tips=True)
            return
        top = tk.Frame(self.root, bg="#1a1f27"); top.pack(fill="x")
        self.info = tk.Label(top, text="", fg="#e8eef5", bg="#1a1f27", font=("Helvetica", 15, "bold"))
        self.info.pack(side="left", padx=14, pady=10)
        self.prog = tk.Label(top, text="", fg="#9aa7b4", bg="#1a1f27", font=("Helvetica", 12))
        self.prog.pack(side="right", padx=14)
        tk.Button(top, text="↻ Re-scan", command=self._rescan).pack(side="right", padx=4, pady=6)
        tk.Button(top, text="Set tips", command=lambda: self._rescan(redo_tips=True)).pack(side="right", pady=6)

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
        n = min(int((e["dur"] + PRE + POST) * self.fps), MAX_CLIP_FRAMES)   # memory-safe cap
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
