#!/usr/bin/env python3
"""CAFE meniscus densitometry GUI — draw a line down a capillary, see the intensity
profile along it (like an ImageJ lane plot / Western-blot trace), interactively set the
threshold and the search region (what's signal vs noise), then track the meniscus
sub-pixel over the whole recording.

Why this is better than a fixed box: you place the line on the *actual* (angled, tapered)
tube, average over a width you choose, and anchor exactly where the liquid front is — so
the tracker follows a boundary you can see and verify.

Deps: python3 (tkinter), opencv-python, numpy, pillow, matplotlib.
Run:  python3 cafe_meniscus_gui.py [video.mp4]   (file picker if omitted)

Workflow:
  1. Scrub to a clear frame (Brighten helps).
  2. Drag a line down a tube: START on the liquid/meniscus side, END past the far edge.
  3. Tune  Width (perpendicular averaging),  Threshold,  and the Region sliders until the
     red meniscus marker sits on the boundary and noisy ends are excluded.
  4. "Add line" (name it RIGHT/LEFT), repeat for the other tube.
  5. "Track over time" → drawdown plot + <video>_meniscus.csv.
"""
import sys
from pathlib import Path
import numpy as np, cv2
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

DISP_W = 980


# ---------------------------------------------------------------- core (testable)
def sample_line(gray, p0, p1, width=5, npts=400):
    """Mean intensity along p0->p1, averaged over +-width perpendicular pixels."""
    (x0, y0), (x1, y1) = p0, p1
    xs = np.linspace(x0, x1, npts); ys = np.linspace(y0, y1, npts)
    dx, dy = x1 - x0, y1 - y0; L = np.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L
    acc = np.zeros(npts)
    for o in range(-width, width + 1):
        sx = np.clip((xs + nx * o).astype(int), 0, gray.shape[1] - 1)
        sy = np.clip((ys + ny * o).astype(int), 0, gray.shape[0] - 1)
        acc += gray[sy, sx]
    dist = np.linspace(0, L, npts)
    return dist, acc / (2 * width + 1)


def find_meniscus(dist, prof, thr_frac, lo, hi):
    """Sub-pixel dark->bright crossing (the meniscus) within [lo,hi] of the line.
    Threshold is relative (min + frac*(max-min)) so it adapts to each frame's brightness."""
    m = (dist >= lo) & (dist <= hi)
    if m.sum() < 4: return None, 0.0
    d = dist[m]; p = prof[m]
    p = cv2.GaussianBlur(p.reshape(-1, 1), (1, 9), 0).ravel()
    thr = p.min() + thr_frac * (p.max() - p.min())
    below = p < thr                       # liquid = dark = below threshold
    for i in range(1, len(p)):
        if below[i - 1] and not below[i]:
            f = (thr - p[i - 1]) / (p[i] - p[i - 1] + 1e-6)
            return d[i - 1] + f * (d[i] - d[i - 1]), thr
    return None, thr


def track_line(video, p0, p1, width, thr_frac, lo, hi, every=25, cb=None):
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    ts, pos = [], []; fi = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if fi % every == 0:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            dist, prof = sample_line(g, p0, p1, width)
            mx, _ = find_meniscus(dist, prof, thr_frac, lo, hi)
            ts.append(fi / fps); pos.append(mx if mx is not None else np.nan)
            if cb and fi % (every * 60) == 0: cb(fi / total)
        fi += 1
    cap.release()
    return np.array(ts), np.array(pos, float)


# ---------------------------------------------------------------- GUI
class MenGUI:
    def __init__(self, root, video):
        self.root = root; self.video = Path(video)
        self.cap = cv2.VideoCapture(str(self.video))
        self.nframes = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self.clahe = cv2.createCLAHE(3.0, (8, 8))
        self.p0 = self.p1 = None; self.width = 5; self.thr = 0.5
        self.lo = 0.0; self.hi = 1.0; self.brighten = False
        self.lines = {}                    # name -> dict(p0,p1,width,thr,lo,hi)
        root.title(f"Meniscus densitometry — {self.video.name}")
        self._frame_gray = None; self._scale = 1.0
        self._build(); self._load_frame(0)

    def _build(self):
        bar = tk.Frame(self.root); bar.pack(fill="x", pady=4)
        tk.Label(bar, text="Frame").pack(side="left")
        self.fscrub = tk.Scale(bar, from_=0, to=self.nframes - 1, orient="horizontal",
                               length=300, command=lambda v: self._load_frame(int(v)), showvalue=0)
        self.fscrub.pack(side="left", padx=6)
        tk.Checkbutton(bar, text="Brighten", command=self._toggle_bright).pack(side="left")
        tk.Label(bar, text="  Name").pack(side="left")
        self.name = ttk.Combobox(bar, values=["RIGHT", "LEFT", "CONTROL"], width=9); self.name.set("RIGHT")
        self.name.pack(side="left")
        tk.Button(bar, text="Add line", command=self._add_line).pack(side="left", padx=4)
        tk.Button(bar, text="Track over time ▶", command=self._track).pack(side="left", padx=4)
        self.status = tk.Label(bar, text="drag a line down a tube"); self.status.pack(side="left", padx=10)

        self.canvas = tk.Canvas(self.root, bg="#111", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._up)

        ctl = tk.Frame(self.root); ctl.pack(fill="x", pady=3)
        self.sw = self._slider(ctl, "Width", 1, 15, 5, self._on_change)
        self.st = self._slider(ctl, "Threshold", 0, 100, 50, self._on_change)
        self.slo = self._slider(ctl, "Region start %", 0, 100, 0, self._on_change)
        self.shi = self._slider(ctl, "Region end %", 0, 100, 100, self._on_change)

        self.fig = Figure(figsize=(9.5, 2.6), dpi=100); self.ax = self.fig.add_subplot(111)
        self.pcanvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.pcanvas.get_tk_widget().pack(fill="x", pady=2)

    def _slider(self, parent, label, lo, hi, val, cmd):
        f = tk.Frame(parent); f.pack(side="left", padx=8)
        tk.Label(f, text=label).pack()
        s = tk.Scale(f, from_=lo, to=hi, orient="horizontal", length=170, command=lambda v: cmd())
        s.set(val); s.pack(); return s

    # ---- frame handling ----
    def _toggle_bright(self): self.brighten = not self.brighten; self._load_frame(int(self.fscrub.get()))

    def _load_frame(self, fr):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, fr); ok, f = self.cap.read()
        if not ok: return
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY); self._frame_gray = g
        disp = self.clahe.apply(g) if self.brighten else g
        self._scale = DISP_W / g.shape[1]
        dh = int(g.shape[0] * self._scale)
        im = cv2.resize(cv2.cvtColor(disp, cv2.COLOR_GRAY2RGB), (DISP_W, dh))
        self.canvas.config(width=DISP_W, height=dh)
        self._photo = ImageTk.PhotoImage(Image.fromarray(im))
        self.canvas.delete("all"); self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._redraw_overlay()

    # ---- drawing ----
    def _d2f(self, x, y): return (x / self._scale, y / self._scale)      # display -> full-res
    def _f2d(self, x, y): return (x * self._scale, y * self._scale)

    def _down(self, e): self.p0 = self._d2f(e.x, e.y); self.p1 = self.p0
    def _drag(self, e): self.p1 = self._d2f(e.x, e.y); self._redraw_overlay(); self._update_profile()
    def _up(self, e): self.p1 = self._d2f(e.x, e.y); self._redraw_overlay(); self._update_profile()

    def _on_change(self):
        self.width = int(self.sw.get()); self.thr = self.st.get() / 100.0
        self.lo = self.slo.get() / 100.0; self.hi = self.shi.get() / 100.0
        self._redraw_overlay(); self._update_profile()

    def _redraw_overlay(self):
        self.canvas.delete("ov")
        if not (self.p0 and self.p1): return
        x0, y0 = self._f2d(*self.p0); x1, y1 = self._f2d(*self.p1)
        self.canvas.create_line(x0, y0, x1, y1, fill="#3af", width=2, tags="ov")
        # meniscus marker
        if self._frame_gray is not None:
            dist, prof = sample_line(self._frame_gray, self.p0, self.p1, self.width)
            mx, _ = find_meniscus(dist, prof, self.thr, self.lo * dist[-1], self.hi * dist[-1])
            if mx is not None:
                fr = mx / (dist[-1] or 1)
                mxD = x0 + (x1 - x0) * fr; myD = y0 + (y1 - y0) * fr
                self.canvas.create_oval(mxD-5, myD-5, mxD+5, myD+5, outline="#f33", width=2, tags="ov")

    def _update_profile(self):
        if not (self.p0 and self.p1) or self._frame_gray is None: return
        dist, prof = sample_line(self._frame_gray, self.p0, self.p1, self.width)
        L = dist[-1] or 1
        mx, thr = find_meniscus(dist, prof, self.thr, self.lo * L, self.hi * L)
        self.ax.clear()
        self.ax.plot(dist, prof, color="#333", lw=1)
        self.ax.axhline(thr, color="#e8a", ls=":", lw=1)
        self.ax.axvspan(self.lo * L, self.hi * L, color="#3af", alpha=.08)
        if mx is not None:
            self.ax.axvline(mx, color="#f33", lw=2)
            self.status.config(text=f"meniscus @ {mx:.1f}px along line")
        self.ax.set_xlabel("distance along line (px)  —  START(0)=tip side")
        self.ax.set_ylabel("intensity"); self.fig.tight_layout()
        self.pcanvas.draw()

    def _add_line(self):
        if not (self.p0 and self.p1):
            messagebox.showinfo("Draw first", "Drag a line down a tube first."); return
        nm = self.name.get() or f"line{len(self.lines)+1}"
        self.lines[nm] = dict(p0=self.p0, p1=self.p1, width=self.width,
                              thr=self.thr, lo=self.lo, hi=self.hi)
        self.status.config(text=f"saved '{nm}'  ({len(self.lines)} line(s))")

    def _track(self):
        if self.p0 and self.p1: self._add_line()      # always capture the line on screen now
        if not self.lines:
            self.status.config(text="Draw a line down a tube first.", fg="#f0a0a0"); return
        base = self.video.with_suffix("")
        import matplotlib.pyplot as plt, csv, json
        # persist the drawn lines so the dashboard (and re-runs) use the exact same geometry
        json.dump({n: {"p0": list(l["p0"]), "p1": list(l["p1"]), "width": l["width"],
                       "thr": l["thr"], "lo": l["lo"], "hi": l["hi"]}
                   for n, l in self.lines.items()}, open(f"{base}_lines.json", "w"), indent=2)
        results = {}
        for nm, ln in self.lines.items():
            self.status.config(text=f"tracking {nm}… ({len(self.lines)} line(s))"); self.root.update()
            L = np.hypot(ln["p1"][0]-ln["p0"][0], ln["p1"][1]-ln["p0"][1]) or 1
            t, pos = track_line(self.video, ln["p0"], ln["p1"], ln["width"], ln["thr"],
                                ln["lo"]*L, ln["hi"]*L, every=25)
            results[nm] = (t, pos)
        fig, ax = plt.subplots(figsize=(11, 4.5))
        col = {"RIGHT": "#2a9d3f", "LEFT": "#c0392b", "CONTROL": "#3a6ea5"}
        rec = {}                                       # +ve px = meniscus receded (liquid gone)
        for nm, (t, pos) in results.items():
            rec[nm] = pos - np.nanmean(pos[:3])
            ax.plot(t/60, rec[nm], lw=2, color=col.get(nm), label=nm)
        tref = results[list(results)[0]][0]
        # evaporation-corrected intake. Prefer a true no-fly CONTROL capillary; if there is
        # no CONTROL, fall back to RIGHT-LEFT (LEFT as a rough evaporation proxy).
        intakes = {}
        if "CONTROL" in rec:
            for fed in ("RIGHT", "LEFT"):
                if fed in rec:
                    intakes[f"{fed}_intake"] = rec[fed] - rec["CONTROL"]
                    ax.plot(tref/60, rec[fed] - rec["CONTROL"], "--", lw=2, color=col.get(fed),
                            label=f"{fed} − CONTROL (intake)")
            note = "intake = fed − CONTROL (no-fly capillary)"
        elif "RIGHT" in rec and "LEFT" in rec:
            intakes["net_intake"] = rec["RIGHT"] - rec["LEFT"]
            ax.plot(tref/60, rec["RIGHT"] - rec["LEFT"], "k--", lw=2, label="RIGHT − LEFT (net intake)")
            note = "net = RIGHT − LEFT (LEFT as evap proxy)"
        else:
            note = ""
        with open(f"{base}_meniscus.csv", "w", newline="") as fh:
            w = csv.writer(fh); names = list(results)
            head = ["t_s"] + [f"{n}_pos_px" for n in names] + [f"{n}_receded_px" for n in names] \
                   + [f"{k}_px" for k in intakes]
            w.writerow(head)
            for i in range(len(tref)):
                r = [round(tref[i], 1)] + [round(float(results[n][1][i]), 2) for n in names] \
                    + [round(float(rec[n][i]), 2) for n in names] \
                    + [round(float(intakes[k][i]), 2) for k in intakes]
                w.writerow(r)
        ax.set_xlabel("time (min)")
        ax.set_ylabel("meniscus receded (px)  ·  higher = more liquid gone")
        ax.set_title(f"Meniscus — {self.video.name}" + (f"  ({note})" if note else ""))
        ax.legend(); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(f"{base}_meniscus.png", dpi=120); plt.close(fig)
        self.status.config(text=f"saved {base.name}_meniscus.png + .csv  ({len(self.lines)} line(s))",
                           fg="#7fe39a")
        messagebox.showinfo("Done", f"Saved:\n{base.name}_meniscus.png\n{base.name}_meniscus.csv")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    if not path:
        path = filedialog.askopenfilename(title="Choose a CAFE recording",
                                          filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All", "*.*")])
        if not path: return
    MenGUI(root, path); root.mainloop()


if __name__ == "__main__":
    main()
