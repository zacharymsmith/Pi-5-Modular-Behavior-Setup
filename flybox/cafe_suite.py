#!/usr/bin/env python3
"""CAFE Analysis Suite — one window to run the whole pipeline reproducibly.

Pick a recording once, then launch each step. Every step reads/writes files next to the
video, so results are self-contained and anyone can reproduce them from the same recording.

Steps
  1. Score feeding events   (cafe_scorer_gui.py)   -> <video>_events.csv, _scores.csv, _meals.csv
  2. Meniscus tracker       (cafe_meniscus_gui.py)  -> <video>_meniscus.csv, _meniscus.png
  3. Render dashboard video (cafe_dashboard.py)     -> <video>_dashboard.mp4

Run:  python3 cafe_suite.py   (or double-click "Launch CAFE Suite.command")
"""
import sys, subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

HERE = Path(__file__).resolve().parent
STEPS = [
    ("1 · Score feeding events", "cafe_scorer_gui.py", "gui",
     "Auto-detect candidate port visits, clip each ±5 s, confirm meals with Y/N."),
    ("2 · Meniscus tracker", "cafe_meniscus_gui.py", "gui",
     "Draw a line down each tube, tune threshold/region on the live profile, sub-pixel track."),
    ("3 · Render dashboard video", "cafe_dashboard.py", "cli",
     "Port video + meniscus graph + meal markers, synced, as an mp4 (runs in the terminal)."),
]


class Suite:
    def __init__(self, root):
        self.root = root; self.video = None
        root.title("CAFE Analysis Suite"); root.configure(bg="#0f1216")
        tk.Label(root, text="🪰  CAFE Analysis Suite", fg="#e8eef5", bg="#0f1216",
                 font=("Helvetica", 18, "bold")).pack(pady=(16, 4))
        tk.Label(root, text="Pick a recording, then run each step. Outputs save next to the video.",
                 fg="#9aa7b4", bg="#0f1216").pack()

        row = tk.Frame(root, bg="#0f1216"); row.pack(pady=12, padx=18, fill="x")
        tk.Button(row, text="Choose recording…", command=self.choose,
                  font=("Helvetica", 13)).pack(side="left")
        self.vlabel = tk.Label(row, text="no video selected", fg="#f0a0a0", bg="#0f1216")
        self.vlabel.pack(side="left", padx=12)

        self.btns = []
        for title, script, kind, desc in STEPS:
            card = tk.Frame(root, bg="#1a1f27"); card.pack(fill="x", padx=18, pady=6)
            b = tk.Label(card, text=title, bg="#2a3340", fg="#8a97a4",
                         font=("Helvetica", 14, "bold"), width=26, height=2, cursor="arrow")
            b.pack(side="left", padx=10, pady=10)
            b.bind("<Button-1>", lambda e, s=script, k=kind: self.launch(s, k))
            self.btns.append(b)
            tk.Label(card, text=desc, fg="#c8d2dc", bg="#1a1f27", justify="left",
                     wraplength=430).pack(side="left", padx=6)

        self.status = tk.Label(root, text="", fg="#7fe39a", bg="#0f1216")
        self.status.pack(pady=(6, 14))

    def choose(self):
        p = filedialog.askopenfilename(title="Choose a CAFE recording",
                                       filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All", "*.*")])
        if not p: return
        self.video = p
        self.vlabel.config(text=Path(p).name, fg="#7fe39a")
        for b in self.btns:                       # enable (green) once a video is chosen
            b.config(bg="#2a9d3f", fg="white", cursor="hand2")
        self.status.config(text="ready — click a step")

    def launch(self, script, kind):
        if not self.video:
            self.status.config(text="Choose a recording first.", fg="#f0a0a0"); return
        subprocess.Popen([sys.executable, str(HERE / script), self.video], cwd=str(HERE))
        self.status.config(
            text=(f"Rendering… saves next to the video (watch the terminal)." if kind == "cli"
                  else f"Launched {script} — its window will open."), fg="#7fe39a")


def main():
    try:
        import cv2, numpy, PIL  # noqa
    except Exception:
        print("Missing deps. Run: python3 -m pip install opencv-python numpy pillow matplotlib")
    root = tk.Tk(); Suite(root); root.mainloop()


if __name__ == "__main__":
    main()
