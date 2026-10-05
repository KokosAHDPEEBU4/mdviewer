"""Persist unsaved editor drafts on disk, so a crash loses nothing.

An editor crash (or a hard ``0xC0000409``) would otherwise take the user's
unsaved markdown with it.  The autosave timer in ``DocumentView`` writes the
current editor text here; the next time the file is opened the draft is offered
back.  The store lives under the same state directory as the single-instance
queue (``instance.state_dir()``), so ``MDVIEWER_STATE_DIR`` redirects it in
tests.

Each draft is one JSON file named after a stable hash of the *resolved* path, so
renaming the file leaves its draft orphaned rather than mismatched, and the same
file opened from either a relative or an absolute path finds the same draft.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import instance


def _drafts_dir() -> Path:
    """Where drafts live (``MDVIEWER_STATE_DIR`` overrides it via instance)."""
    path = instance.state_dir() / "drafts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _draft_path(path) -> Path:
    """Stable draft file for `path`: sha1 of its resolved, string form."""
    key = hashlib.sha1(str(Path(path).resolve()).encode("utf-8")).hexdigest()
    return _drafts_dir() / f"{key}.json"


def save_draft(path, text) -> None:
    """Write `text` as the pending draft for `path` (atomic: temp + rename)."""
    target = _draft_path(path)
    payload = {
        "path": str(Path(path).resolve()),
        "text": text,
        "time": time.time(),
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def load_draft(path) -> str | None:
    """The pending draft text for `path`, or None when there is none."""
    target = _draft_path(path)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    text = data.get("text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else None


def clear_draft(path) -> None:
    """Drop the pending draft for `path` (missing file is not an error)."""
    try:
        _draft_path(path).unlink()
    except OSError:
        pass


def pending_drafts() -> list[dict]:
    """Every stored draft as ``{"path", "text", "time"}``, newest first."""
    result: list[dict] = []
    for item in _drafts_dir().glob("*.json"):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("path"), str):
            result.append(data)
    result.sort(key=lambda draft: draft.get("time", 0), reverse=True)
    return result
