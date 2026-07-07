"""Save/load named experiment presets as JSON for reproducibility.

A preset captures the full behavioral configuration — per-channel protocols,
trigger zones, proximity settings, tracking parameters, and camera specs — so an
experiment can be reproduced exactly later.
"""
from __future__ import annotations

import os
import re
import json

from config import PRESETS_DIR

os.makedirs(PRESETS_DIR, exist_ok=True)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_")[:60] or "preset"


def _path(name: str) -> str:
    return os.path.join(PRESETS_DIR, _safe(name) + ".json")


def save(name: str, data: dict) -> str:
    safe = _safe(name)
    data = dict(data, _name=safe)
    with open(_path(name), "w") as f:
        json.dump(data, f, indent=2)
    return safe


def load(name: str):
    p = _path(name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def list_presets():
    return sorted(f[:-5] for f in os.listdir(PRESETS_DIR) if f.endswith(".json"))


def delete(name: str) -> bool:
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False
