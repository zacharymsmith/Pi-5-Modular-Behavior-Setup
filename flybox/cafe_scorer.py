#!/usr/bin/env python3
"""CAFE meal scorer — a small local web app to confirm feeding events.

Pipeline:
  1. Auto-detects candidate port visits (a fly darkening the bright-dish patch at a
     capillary tip) across the whole recording.
  2. Clips each candidate with PRE/POST seconds of padding (browser-playable H.264,
     with the tip highlighted) — built lazily on first view.
  3. Serves a looping-video UI: each clip plays on repeat until you press
     Y (eating) / N (not) / S (skip); Back to revise. Scores autosave.
  4. Confirmed meals are written to <video>_meals.csv as you go.

Run:   python3 cafe_scorer.py  [path/to/video.mp4]
Then open http://127.0.0.1:8020  (opens automatically).

Meniscus tracking is stubbed as a second tab to slot in next.
"""
from __future__ import annotations
import os, sys, csv, json, subprocess, webbrowser, threading
from pathlib import Path
import cv2, numpy as np

# ---------------------------------------------------------------- config
VIDEO = Path(sys.argv[1] if len(sys.argv) > 1 else
             "test_examples/20260806_095436_cafe_test_06Aug2026/20260806_095436.mp4").resolve()
PRE, POST = 5.0, 5.0                      # seconds of padding around each event
STRIDE = 5                               # detection sampling (every Nth frame)
DARK = 120                               # p20 below this = fly on the tip patch
MIN_DUR, MAX_GAP = 3, 4                  # event = >=3s present, merge <=4s gaps
# bright-dish patch just inside each capillary tip (x0,y0,x1,y1). Tune per rig layout.
TIPS = {"RIGHT": (1150, 330, 1215, 388), "LEFT": (565, 360, 628, 412)}
PORT = 8020

BASE = VIDEO.with_suffix("")
CLIPS = Path(f"{BASE}_clips"); CLIPS.mkdir(exist_ok=True)
EVENTS_CSV = Path(f"{BASE}_events.csv")
SCORES_CSV = Path(f"{BASE}_scores.csv")
MEALS_CSV = Path(f"{BASE}_meals.csv")


# ---------------------------------------------------------------- detection
def p20(frame, roi):
    x0, y0, x1, y1 = roi
    g = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return float(np.percentile(g, 20))


def _bouts(t, present):
    # sub-sample tolerance: the recording fps is rarely exactly nominal (e.g. 5.000115),
    # so a nominal 3s run spans 2.9999s — without tolerance every borderline event is lost.
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    out, i, n = [], 0, len(present)
    while i < n:
        if present[i]:
            j, gap = i, 0
            while j + 1 < n and (present[j + 1] or gap < MAX_GAP):
                j += 1; gap = 0 if present[j] else gap + 1
            while j > i and not present[j]: j -= 1
            if t[j] - t[i] >= MIN_DUR - dt * 0.75: out.append((t[i], t[j] - t[i]))
            i = j + 1
        else: i += 1
    return out


def detect():
    """Scan the whole video, return sorted candidate events (cached to CSV)."""
    if EVENTS_CSV.exists():
        rows = list(csv.DictReader(open(EVENTS_CSV)))
        return [dict(id=int(r["id"]), capillary=r["capillary"],
                     start_s=float(r["start_s"]), dur=float(r["dur"])) for r in rows]
    cap = cv2.VideoCapture(str(VIDEO)); fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    series = {k: [] for k in TIPS}; ts = []
    fi = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        if fi % STRIDE == 0:
            ts.append(fi / fps)
            for k, roi in TIPS.items(): series[k].append(p20(f, roi))
        fi += 1
    cap.release()
    t = np.array(ts)
    evs = []
    for k in TIPS:
        for s, d in _bouts(t, np.array(series[k]) < DARK):
            evs.append(dict(capillary=k, start_s=round(float(s), 1), dur=round(float(d), 1)))
    evs.sort(key=lambda e: e["start_s"])
    for i, e in enumerate(evs): e["id"] = i
    with open(EVENTS_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, ["id", "capillary", "start_s", "dur"]); w.writeheader()
        for e in evs: w.writerow({k: e[k] for k in ["id", "capillary", "start_s", "dur"]})
    return evs


EVENTS = detect()
BY_ID = {e["id"]: e for e in EVENTS}


# ---------------------------------------------------------------- clips
def clip_path(i): return CLIPS / f"event_{i:03d}.mp4"


def build_clip(i):
    e = BY_ID[i]; out = clip_path(i)
    if out.exists() and out.stat().st_size > 0: return out
    x0, y0, x1, y1 = TIPS[e["capillary"]]
    ss = max(0.0, e["start_s"] - PRE); dur = e["dur"] + PRE + POST
    vf = f"drawbox=x={x0}:y={y0}:w={x1-x0}:h={y1-y0}:color=yellow@0.9:t=2"
    cmd = ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", str(VIDEO), "-t", f"{dur:.2f}",
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-an", str(out)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return out


# ---------------------------------------------------------------- scores
def load_scores():
    if not SCORES_CSV.exists(): return {}
    return {int(r["id"]): r["label"] for r in csv.DictReader(open(SCORES_CSV))}


def save_score(i, label):
    sc = load_scores(); sc[i] = label
    with open(SCORES_CSV, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["id", "capillary", "start_s", "dur", "label"])
        for k in sorted(sc):
            e = BY_ID[k]; w.writerow([k, e["capillary"], e["start_s"], e["dur"], sc[k]])
    with open(MEALS_CSV, "w", newline="") as fh:            # confirmed meals only
        w = csv.writer(fh); w.writerow(["capillary", "start_s", "start_mmss", "dur_s"])
        for k in sorted(sc):
            if sc[k] == "eat":
                e = BY_ID[k]; s = int(e["start_s"])
                w.writerow([e["capillary"], e["start_s"], f"{s//60}:{s%60:02d}", e["dur"]])
    return sc


# ---------------------------------------------------------------- web app
try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
    import uvicorn
except Exception:
    print("This app needs fastapi + uvicorn:  pip install fastapi uvicorn"); sys.exit(1)

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index(): return HTMLResponse(INDEX_HTML)


@app.get("/api/events")
def api_events():
    sc = load_scores()
    evs = [{**e, "start_mmss": f"{int(e['start_s'])//60}:{int(e['start_s'])%60:02d}",
            "label": sc.get(e["id"], "")} for e in EVENTS]
    n_eat = sum(1 for v in sc.values() if v == "eat")
    return JSONResponse({"video": VIDEO.name, "total": len(EVENTS),
                         "scored": len(sc), "meals": n_eat, "events": evs})


@app.get("/clip/{i}.mp4")
def clip(i: int):
    p = build_clip(i)
    return FileResponse(str(p), media_type="video/mp4")


@app.post("/api/score/{i}/{label}")
def score(i: int, label: str):
    sc = save_score(i, label)
    return {"ok": True, "scored": len(sc),
            "meals": sum(1 for v in sc.values() if v == "eat")}


INDEX_HTML = r"""<!doctype html><html><head><meta charset=utf-8>
<title>CAFE meal scorer</title><style>
:root{--bg:#0f1216;--card:#1a1f27;--fg:#e8eef5;--mut:#9aa7b4;--eat:#2a9d3f;--no:#c0392b;--acc:#3a86ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{padding:12px 18px;background:var(--card);display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header b{font-size:16px}.tab{color:var(--mut);cursor:pointer;padding:4px 10px;border-radius:7px}
.tab.on{background:#243; color:var(--fg)}
.wrap{max-width:900px;margin:22px auto;padding:0 16px}
.card{background:var(--card);border-radius:14px;padding:18px}
video{width:100%;border-radius:10px;background:#000;max-height:62vh}
.meta{display:flex;justify-content:space-between;align-items:baseline;margin:10px 2px}
.pill{padding:2px 10px;border-radius:20px;font-weight:600}
.pill.RIGHT{background:#123d1e;color:#7fe39a}.pill.LEFT{background:#3d1414;color:#f0a0a0}
.btns{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px}
button.act{border:0;border-radius:11px;padding:16px;font-size:16px;font-weight:700;color:#fff;cursor:pointer}
.eat{background:var(--eat)}.no{background:var(--no)}.skip{background:#556}
.row2{display:flex;gap:10px;margin-top:10px}.row2 button{flex:1;background:#2a3340;color:var(--fg);border:0;border-radius:9px;padding:9px;cursor:pointer}
.bar{height:8px;background:#243;border-radius:6px;overflow:hidden;margin-top:6px}
.bar>div{height:100%;background:var(--acc)}
.small{color:var(--mut);font-size:13px}
.done{text-align:center;padding:40px}
.hidden{display:none}
.list{max-height:60vh;overflow:auto}.list div{padding:6px 8px;border-bottom:1px solid #232a33;display:flex;justify-content:space-between;cursor:pointer}
.list div:hover{background:#222}
.tag{font-size:12px;padding:1px 7px;border-radius:10px}.tag.eat{background:var(--eat)}.tag.no{background:var(--no)}.tag.skip{background:#556}
</style></head><body>
<header><b>🪰 CAFE meal scorer</b>
<span class=tab id=tabScore onclick="show('score')">Score</span>
<span class=tab id=tabList onclick="show('list')">Events</span>
<span class=tab id=tabMen onclick="show('men')">Meniscus</span>
<span class=small id=vid></span>
<span class=small id=prog style="margin-left:auto"></span></header>

<div class=wrap>
 <div id=score class=card>
   <video id=vp autoplay loop muted playsinline></video>
   <div class=meta><div><span id=cap class=pill></span> <b id=tm></b> <span class=small id=dur></span></div>
     <div class=small id=idx></div></div>
   <div class=btns>
     <button class="act eat" onclick="mark('eat')">✓ Eating <span class=small>(Y)</span></button>
     <button class="act no" onclick="mark('no')">✗ Not <span class=small>(N)</span></button>
     <button class="act skip" onclick="mark('skip')">Skip <span class=small>(S)</span></button></div>
   <div class=row2><button onclick="back()">⤺ Back</button>
     <button onclick="speed()">⏩ <span id=spd>1×</span></button>
     <button onclick="jumpNext()">Next unscored &raquo;</button></div>
   <div class=bar><div id=pbar></div></div>
   <div class=small style="margin-top:8px">Loops until you answer. Keys: Y eat · N not · S skip · ← back</div>
 </div>

 <div id=list class="card hidden"><div class=list id=listBody></div></div>

 <div id=men class="card hidden">
   <h3>Meniscus tracking</h3>
   <p class=small>Next up: per-capillary meniscus drawdown → nL/µL consumed, and auto-flag
   candidates where a tip visit coincides with a meniscus step (the strongest "real meal" signal).
   Placeholder tab so it drops straight in here.</p></div>
</div>

<div id=doneTpl class=hidden></div>
<script>
let EV=[], cur=0, rate=1;
const $=id=>document.getElementById(id);
function show(t){for(const k of['score','list','men']){$(k).classList.toggle('hidden',k!==t);$('tab'+k[0].toUpperCase()+k.slice(1)).classList.toggle('on',k===t);}
  if(t==='list')renderList();}
async function load(){const r=await fetch('/api/events');const d=await r.json();EV=d.events;$('vid').textContent=d.video;
  updProg(d);let f=EV.findIndex(e=>!e.label);cur=f<0?0:f;render();}
function updProg(d){$('prog').textContent=`${d.scored}/${d.total} scored · ${d.meals} meals`;
  $('pbar').style.width=(100*d.scored/Math.max(1,d.total))+'%';}
function render(){if(!EV.length)return;const e=EV[cur];
  $('vp').src='/clip/'+e.id+'.mp4';$('vp').playbackRate=rate;
  $('cap').textContent=e.capillary;$('cap').className='pill '+e.capillary;
  $('tm').textContent=e.start_mmss;$('dur').textContent='· '+e.dur+'s at tip';
  $('idx').textContent=`event ${cur+1} / ${EV.length}`+(e.label?' · '+e.label.toUpperCase():'');}
async function mark(label){const e=EV[cur];const r=await fetch('/api/score/'+e.id+'/'+label,{method:'POST'});
  const d=await r.json();EV[cur].label=label;updProg({...d,total:EV.length});
  let n=EV.findIndex((x,i)=>i>cur&&!x.label);if(n<0)n=EV.findIndex(x=>!x.label);
  if(n<0){allDone();return;}cur=n;render();}
function back(){cur=Math.max(0,cur-1);render();}
function jumpNext(){let n=EV.findIndex(x=>!x.label);if(n>=0){cur=n;render();}}
function speed(){rate=rate===1?0.5:(rate===0.5?2:1);$('spd').textContent=rate+'×';$('vp').playbackRate=rate;}
function allDone(){$('score').innerHTML='<div class=done><h2>All candidates scored 🎉</h2>'+
  '<p class=small>Confirmed meals saved next to your video as <b>*_meals.csv</b>.</p>'+
  '<button class="act eat" onclick="location.reload()">Review again</button></div>';}
function renderList(){const b=$('listBody');b.innerHTML='';EV.forEach((e,i)=>{const d=document.createElement('div');
  d.innerHTML=`<span><b>${e.capillary}</b> · ${e.start_mmss} · ${e.dur}s</span>`+
   (e.label?`<span class="tag ${e.label}">${e.label}</span>`:'<span class=small>—</span>');
  d.onclick=()=>{cur=i;show('score');render();};b.appendChild(d);});}
document.addEventListener('keydown',ev=>{const k=ev.key.toLowerCase();
  if(k==='y')mark('eat');else if(k==='n')mark('no');else if(k==='s')mark('skip');
  else if(ev.key==='ArrowLeft')back();});
load();
</script></body></html>"""


if __name__ == "__main__":
    print(f"CAFE scorer · {VIDEO.name} · {len(EVENTS)} candidate events")
    print(f"open  http://127.0.0.1:{PORT}")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
