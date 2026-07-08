"""Save/load named experiment presets as JSON for reproducibility.

A preset captures the full behavioral configuration — per-channel protocols,
trigger zones, proximity settings, tracking parameters, and camera specs — so an
experiment can be reproduced exactly later.

The storage directory is settable at runtime (set_dir) so presets can live in a
user-chosen data folder alongside session recordings.
"""
from __future__ import annotations

import os
import re
import json

from config import PRESETS_DIR

_DIR = PRESETS_DIR
os.makedirs(_DIR, exist_ok=True)


def set_dir(path: str | None):
    global _DIR
    _DIR = os.path.expanduser(path) if path else PRESETS_DIR
    os.makedirs(_DIR, exist_ok=True)


def current_dir() -> str:
    return _DIR


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_")[:60] or "preset"


def _path(name: str) -> str:
    return os.path.join(_DIR, _safe(name) + ".json")


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
    if not os.path.isdir(_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(_DIR) if f.endswith(".json"))


def delete(name: str) -> bool:
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False
