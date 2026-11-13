"""How much memory this server may use, and whether a STEP file of a given size fits in it.

On a container (a Kubernetes pod) the memory limit is the cgroup limit: going over it kills the app for every
user at once. So the app reads the limit, sizes the largest upload from it, and refuses a STEP file that
would not fit before it starts reading it - with a message instead of a crash. On a PC there is no cgroup
limit and nothing is refused.

The estimate is measured, not guessed: reading a STEP file and finding its faces and contacts took
(Windows, peak working set over the loaded app)
    9.7 MB  camera board assembly          ->  +95 MB
    66.5 MB full product assembly          -> +220 MB
so PEAK_PER_MB = 4.5 and PEAK_FIXED_MB = 60 cover both with room to spare, and an upload also sits in
memory once while it is received.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_BASE_MB = 400           # Streamlit, OpenCascade, numpy, plotly loaded, nothing read yet
PEAK_FIXED_MB = 60          # per STEP read, whatever its size
PEAK_PER_MB = 4.5           # per MB of STEP file: OpenCascade shapes, faces, contacts, the upload itself
HEADROOM = 0.9              # use at most this share of the limit
LOCAL_UPLOAD_MB = 2048      # no container limit: the largest upload on a PC
MIN_UPLOAD_MB = 50
_UNLIMITED = 1 << 60

_V2_LIMIT = Path("/sys/fs/cgroup/memory.max")
_V2_USED = Path("/sys/fs/cgroup/memory.current")
_V1_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_V1_USED = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if 0 < value < _UNLIMITED else None


def memory_limit_bytes() -> int | None:
    """The container memory limit in bytes; None when there is none (a PC).

    TOLSTACK_MEMORY_LIMIT_MB overrides it (for tests, or a server without cgroups).
    """
    forced = os.environ.get("TOLSTACK_MEMORY_LIMIT_MB", "").strip()
    if forced:
        try:
            return int(float(forced) * 2**20) or None
        except ValueError:
            return None
    return _read_int(_V2_LIMIT) or _read_int(_V1_LIMIT)


def memory_used_bytes() -> int | None:
    """Memory the container uses now (cgroup); None when unknown (then APP_BASE_MB is assumed)."""
    return _read_int(_V2_USED) or _read_int(_V1_USED)


def step_peak_mb(step_mb: float) -> float:
    """Extra memory reading a STEP file of `step_mb` MB takes at its peak (estimate, MB)."""
    return PEAK_FIXED_MB + PEAK_PER_MB * step_mb


def max_upload_mb(limit_bytes: int | None = None) -> int:
    """The largest STEP / archive upload (MB) that can still be read within the memory limit."""
    limit = memory_limit_bytes() if limit_bytes is None else limit_bytes
    if not limit:
        return LOCAL_UPLOAD_MB
    room = HEADROOM * limit / 2**20 - APP_BASE_MB - PEAK_FIXED_MB
    return int(max(MIN_UPLOAD_MB, min(LOCAL_UPLOAD_MB, room / PEAK_PER_MB)))


def check_step(step_bytes: int, limit_bytes: int | None = None, used_bytes: int | None = None) -> str | None:
    """None when a STEP file of `step_bytes` can be read now; else a message saying why not.

    Only a container limit is checked; on a PC (no limit) this always returns None.
    """
    limit = memory_limit_bytes() if limit_bytes is None else limit_bytes
    if not limit:
        return None
    used = memory_used_bytes() if used_bytes is None else used_bytes
    used_mb = (used or APP_BASE_MB * 2**20) / 2**20
    limit_mb = limit / 2**20
    need_mb = step_peak_mb(step_bytes / 2**20)
    if used_mb + need_mb <= HEADROOM * limit_mb:
        return None
    return (f"This STEP file ({step_bytes / 2**20:.0f} MB) needs about {need_mb:.0f} MB of memory to read; this "
            f"server has {limit_mb:.0f} MB in all and {used_mb:.0f} MB in use, so reading it would stop the app "
            f"for everyone. Run tolstack on your own PC for a file this big (start.bat - no size limit), or "
            f"export only the sub-assembly around the gap as STEP (files up to about {max_upload_mb(limit)} MB "
            f"fit here).")


def main(argv: list[str] | None = None) -> int:
    """python -m tolstack.resources max-upload-mb   -> prints the upload limit for streamlit."""
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["max-upload-mb"]:
        print(max_upload_mb())
        return 0
    limit = memory_limit_bytes()
    print(f"memory limit: {limit / 2**20:.0f} MB" if limit else "memory limit: none (not in a container)")
    print(f"largest upload: {max_upload_mb(limit)} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
