"""Temporary work folders for the web app: one per browser session, old ones removed."""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "tolstack_app"
KEEP = ("demo",)            # shared, regenerated on demand


def session_dir(session_id: str, root: Path = ROOT) -> Path:
    """The private folder of one app session (uploads, previews, reports)."""
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_") or "session"
    path = root / "sessions" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_old(root: Path = ROOT, max_age_hours: float = 24.0, now: float | None = None) -> list[str]:
    """Delete session folders (and loose files left by older versions) not touched for `max_age_hours`.

    Uploaded customer CAD must not pile up in the temp folder of a shared server. Returns the removed names.
    """
    now = time.time() if now is None else now
    removed = []
    candidates = [p for p in root.glob("*") if p.name not in KEEP + ("sessions",)]
    candidates += list((root / "sessions").glob("*"))
    for path in candidates:
        try:
            newest = max([path.stat().st_mtime] + [p.stat().st_mtime for p in path.rglob("*")] if path.is_dir()
                         else [path.stat().st_mtime])
            if now - newest < max_age_hours * 3600:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path.name)
        except OSError:
            continue        # in use by another session or already gone
    return removed
