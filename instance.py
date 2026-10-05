"""Single-instance guard for MD Viewer.

Windows starts a fresh process for every ``.md`` double-click, so without this
each file would pop a second window instead of a tab in the running one.
``QtNetwork`` is deliberately excluded from the frozen build (see
``MDViewer.spec``), so this cannot use ``QLocalServer``.  Instead it uses the
stdlib only:

* a per-session Win32 named mutex tells a new process whether a viewer already
  owns the singleton;
* a tiny file-based request queue hands the paths over.  The owner polls the
  queue and opens the files as tabs, then raises its window.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path

MUTEX_NAME = "Local\\MDViewer-singleton"
ERROR_ALREADY_EXISTS = 183
POLL_MS = 150  # queue poll interval of the owning process


def state_dir() -> Path:
    """Where the hand-off queue lives (``MDVIEWER_STATE_DIR`` overrides it)."""
    override = os.environ.get("MDVIEWER_STATE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or Path.home()
    return Path(base) / "MDViewer"


def requests_dir() -> Path:
    path = state_dir() / "requests"
    path.mkdir(parents=True, exist_ok=True)
    return path


def acquire_singleton():
    """Create the per-session mutex and report whether we are the owner.

    Returns ``(handle, already_running)``.  ``handle`` must be kept alive for
    the process lifetime; Windows releases the mutex on exit, so a crash never
    leaves a stale lock behind.
    """
    if os.name != "nt":
        return None, False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    already = bool(handle) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    return handle, already


def write_request(paths) -> None:
    """Queue a set of paths for the running instance (atomic: temp + rename)."""
    folder = requests_dir()
    stamp = f"{time.time_ns()}-{os.getpid()}"
    payload = [str(Path(path)) for path in paths]
    tmp = folder / f".{stamp}.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, folder / f"{stamp}.req")


def take_requests() -> list[list[str]]:
    """Return and delete every queued request, oldest first."""
    folder = requests_dir()
    batch: list[list[str]] = []
    for path in sorted(folder.glob("*.req")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        batch.append(data if isinstance(data, list) else [])
    return batch


def make_watcher(callback, interval_ms: int = POLL_MS):
    """Build the owning process's queue poller (imports Qt lazily).

    The second-instance hand-off path must not import Qt at all, so the QObject
    timer is created here on demand rather than at module import. The returned
    object exposes `stop()` and keeps itself alive via its parent/timer.
    """
    from PyQt6.QtCore import QObject, QTimer

    class _Watcher(QObject):
        def __init__(self, cb):
            super().__init__()
            self._callback = cb
            self._timer = QTimer(self)
            self._timer.setInterval(interval_ms)
            self._timer.timeout.connect(self._poll)
            self._timer.start()

        def _poll(self) -> None:
            for paths in take_requests():
                self._callback(paths)

        def stop(self) -> None:
            self._timer.stop()

    return _Watcher(callback)
