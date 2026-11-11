"""Saved forecasts, so the app starts in seconds instead of retraining on every start.

What is saved per store: the daily forecasts, their error (sigma), days observed per product,
learning/model mode, history length, the day they were trained for, the learner used and a
version number. The previous version is kept next to it (`.prev`), so a broken save never leaves
the shop without forecasts.

Flow (see api/main.py):
  start    -> load the saved forecasts at once; if they are from an earlier day, retrain in the
              background and swap them in when ready (the old ones keep serving meanwhile)
  daily    -> a background check retrains once per day
  failure  -> the retrain error is recorded and the last good forecasts stay in use
"""
from __future__ import annotations

import os
import pickle
from datetime import date, datetime
from pathlib import Path

FORMAT = 1
FIELDS = ("forecasts", "sigma", "observed", "mode", "history_days")


def path_for(db_path: str | Path, store_id: int) -> Path:
    base = Path(os.getenv("STOCKORA_MODEL_DIR") or Path(db_path).resolve().parent / "model_cache")
    return base / f"store{store_id}.pkl"


def save(brain, path: Path, learner: str = "") -> dict:
    """Write atomically; keep the previous file as .prev. Returns the saved metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load_meta(path)
    meta = {"format": FORMAT, "version": (old or {}).get("version", 0) + 1, "trained_for": brain.today.isoformat(),
            "saved_at": datetime.now().isoformat(timespec="seconds"), "learner": learner,
            "products": len(brain.forecasts), "mode": brain.mode, "history_days": brain.history_days}
    payload = {"meta": meta, **{f: getattr(brain, f) for f in FIELDS}}
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    if path.exists():
        os.replace(path, path.with_suffix(".prev"))
    os.replace(tmp, path)
    return meta


def _read(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)          # our own file, written by save() above
        return data if data.get("meta", {}).get("format") == FORMAT else None
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, KeyError, TypeError):
        return None


def load_meta(path: Path) -> dict | None:
    data = _read(path)
    return data["meta"] if data else None


def load(brain, path: Path) -> dict | None:
    """Fill brain with saved forecasts (falling back to the previous version). Returns metadata or None."""
    for p in (path, path.with_suffix(".prev")):
        data = _read(p)
        if data:
            for f in FIELDS:
                setattr(brain, f, data[f])
            return {**data["meta"], "file": p.name}
    return None


def is_current(meta: dict | None, today: date) -> bool:
    return bool(meta) and meta.get("trained_for") == today.isoformat()
