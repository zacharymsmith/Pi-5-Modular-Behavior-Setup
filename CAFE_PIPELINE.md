# CAFE feeding-analysis pipeline (reproducible methods)

This documents exactly how a CAFE recording becomes a list of confirmed feeding events,
so the numbers are fully reproducible. The pipeline is **deterministic**: the same video
with the same parameters always yields the same candidate list. Manual Y/N scoring is the
only human step, and it is recorded to CSV as an audit trail.

Worked example throughout: `20260806_095436.mp4` (06 Aug 2026, 2 h, GtACR/Gr64f/Chrimson).

---

## Data provenance (file lineage)

```
20260806_095436.mp4              the raw recording (input)
        │  cafe_scorer_gui.py  →  detect()          [deterministic]
        ▼
20260806_095436_events.csv       candidate events (id, capillary, start_s, dur)
        │  cafe_scorer_gui.py  →  clips + manual Y/N [human]
        ▼
20260806_095436_scores.csv       every candidate + your label (eat / no / skip)
        │  derived automatically
        ▼
20260806_095436_meals.csv        confirmed meals only (label == eat)
```

All four files sit next to each other. Nothing is hidden; every number traces back to a
frame range in the video.

---

## Step 1 — Recording

| setting | value |
|---|---|
| camera | Pi Camera Module 3 NoIR (IMX708) |
| resolution | 1536 × 864 |
| frame rate | ~5 fps (nominal; **true fps 5.000115**, see note) |
| duration | 2 h (36007 frames) |
| format | H.264 → mp4 (crash-proof raw `.h264` + remux) |

Low fps is deliberate for a multi-hour feeding assay (visits play out over seconds) and
keeps files small (~600 MB / 2 h).

---

## Step 2 — Candidate detection  (`detect()` in `flybox/cafe_scorer_gui.py`)

The insight: a fly feeding at a capillary sits on the **bright dish right at the tip**, so
its dark body darkens a small fixed patch there. We watch that patch's brightness over time.
This deliberately avoids the dark dish **rim**, where a dark fly on a dark background defeats
background-subtraction (that failure mode was measured and discarded).

**Fixed tip patches** (full-res pixels, `x0,y0,x1,y1`) — *rig-specific; re-measure if framing changes*:

| capillary | patch |
|---|---|
| RIGHT | 1150, 330, 1215, 388 |
| LEFT  | 565, 360, 628, 412 |

**Per-frame signal.** Sample every `STRIDE = 5` frames (~1 Hz). In each patch compute
`p20` = the 20th-percentile of grayscale intensity. Empty patch ≈ 168–180 (bright dish);
a fly present drops it to ~40–90.

**Event rule.** With `DARK = 120`, `STRONG = 95`, `MIN_DUR = 3 s`, `MAX_GAP = 4` samples:
a run of samples with `p20 < DARK` (gaps ≤ `MAX_GAP` bridged) is a **candidate** if it is
either **sustained** (`≥ MIN_DUR` seconds) **or** contains a **definite fly**
(`min p20 < STRONG`). The second clause keeps brief-but-clear touches (e.g. quick left-port
visits) that a duration-only rule would drop.

> **fps note (important for reproducibility):** the camera's true rate is 5.000115 fps, not
> 5.0, so a nominal 3 s event actually spans 2.9999 s. A strict `≥ 3 s` test silently drops
> every borderline event. The code uses a sub-sample tolerance (`MIN_DUR − 0.75·dt`) to fix
> this. Without it you get ~27 events; with it, the correct ~44 sustained events.

Output: `*_events.csv` (cached — delete it to force a fresh scan).

---

## Step 3 — Clip + manual confirmation  (`cafe_scorer_gui.py` GUI)

Each candidate is played as a looping clip, `PRE = 5 s` before to `POST = 5 s` after, with
the tip patch boxed. You press **Y** (eating) / **N** (not) / **S** (skip); every keystroke
writes `*_scores.csv`, and confirmed meals (`eat`) are mirrored to `*_meals.csv`. Resumable.

Run: double-click **`Launch CAFE Scorer.command`**, or `python3 cafe_scorer_gui.py video.mp4`.
Dependencies: Python + tkinter, opencv-python, numpy, pillow.

---

## Results — 20260806_095436 (this session)

Scored on the **sustained-only** candidate set (44 candidates, all right-side):

| | RIGHT | LEFT |
|---|---|---|
| candidates | 44 | 0 (sustained) |
| **confirmed meals (Y)** | **31** | 0 |
| rejected (N) | 13 | — |

Longest confirmed meals: 79:07 (+16 s), 35:40 (+14 s), 36:28 (+10 s), 88:37 (+10 s).

This is corroborated by two independent signals:
- **Meniscus drawdown**: the right capillary's liquid front recedes steadily over 2 h; the
  left is essentially flat (→ right consumed, left not).
- **Both-sides detector**: with the brief-touch clause enabled, the left shows only **3**
  fleeting single-frame touches (21:03, 28:08, 63:22) and **no sustained visits** — flies
  explored the left port but did not take meals there.

Conclusion: flies fed at the **right** CAFE (31 meals) and not the left. Three independent
methods agree.

---

## Reproducibility checklist

- **Deterministic** — detection has no randomness; same video + same parameters → same events.
- **Parameters live in one place** — the constants block at the top of `cafe_scorer_gui.py`
  (`TIPS`, `STRIDE`, `DARK`, `STRONG`, `MIN_DUR`, `MAX_GAP`, `PRE`, `POST`). Record these with
  any result; they are the full recipe.
- **Audit trail** — `_events.csv` (what the algorithm proposed) and `_scores.csv` (what you
  decided) are both kept, so any meal count can be traced to a timestamp and a frame range.
- **Re-run from scratch** — delete `*_events.csv` and relaunch to re-detect; delete
  `*_scores.csv` to re-score.
- **Per-rig calibration** — the only rig-specific values are the two tip patches in `TIPS`.
  If the camera/dish is repositioned, re-measure them (open a frame, read the pixel box at
  each tip) and note the new values with the dataset.

## How to run (anyone, from scratch)

**Double-click `flybox/Launch CAFE Suite.command`** (first run installs deps). One window opens:

1. **Choose recording** — pick the `.mp4`.
2. **① Score feeding events** — auto-detects candidate port visits, clips each ±5 s; press
   Y/N/S. Writes `_events.csv`, `_scores.csv`, `_meals.csv`.
3. **② Meniscus tracker** — draw a line down each tube, tune threshold/region on the live
   profile, track. Writes `_meniscus.csv`, `_meniscus.png`.
4. **③ Render dashboard video** — port video + meniscus graph + meal markers → `_dashboard.mp4`.

Every output lands next to the recording, so a dataset folder is fully self-contained and
another person gets identical results from the same video + the ROIs recorded in each script.

## Scripts (`flybox/`)
- `cafe_suite.py` — **the launcher hub** (start here)
- `cafe_scorer_gui.py` — event detection + Y/N scoring GUI
- `cafe_meniscus_gui.py` — draw-the-line densitometry meniscus tracker
- `cafe_dashboard.py` — synced port + meniscus-graph dashboard video
- `cafe_events.py`, `cafe_analyze.py`, `cafe_meniscus.py` — headless / batch / QC variants
