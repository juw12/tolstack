"""Find the STEP assembly and the 2D drawings in what the user hands over: loose files, a folder, a zip or a 7z.

Built around real project hand-over folders ("16 ... 3D", "17 ... 2D"): the STEP assembly often sits next to
a .7z or .zip holding the same or a newer STEP, next to hundreds of native Creo / Pro-E files (.prt.12,
.asm.3; tolstack.creo converts them with an installed Creo), and the part drawings are in a sibling "2D" folder.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Union

STEP_SUFFIXES = (".step", ".stp")
WANTED = STEP_SUFFIXES + (".pdf",)
ARCHIVE_SUFFIXES = (".zip", ".7z")
MAX_UNZIPPED = 4 * 1024 ** 3          # bytes written from one archive; a "zip bomb" stops here
_CHUNK = 1 << 20
_VERSION = re.compile(r"\.\d+$")      # Creo / Pro-E keep versions as name.prt.12
_NATIVE = {".prt": "parts", ".asm": "assemblies", ".drw": "drawings"}
_TWO_D = re.compile(r"(?<![a-z0-9])2d(?![a-z0-9])", re.IGNORECASE)     # "17-2D圖面", "AX_2D"; not "a2d4f" in a hash
_THREE_D = re.compile(r"(?<![a-z0-9])3d(?![a-z0-9])", re.IGNORECASE)

Upload = tuple[str, Union[Callable[[], bytes], IO[bytes]]]


def _clean(name: str) -> str:
    """One path component that is safe on Windows and Linux."""
    return "".join("_" if ch in '<>:"|?*' or ord(ch) < 32 else ch for ch in name).strip(" .") or "_"


def _safe_parts(member_name: str) -> list[str]:
    """Path components of an archive entry that can not leave the target folder."""
    return [p for p in PurePosixPath(member_name.replace("\\", "/")).parts
            if p not in ("/", ".", "..") and not p.endswith(":")]


def _wanted(parts: list[str]) -> bool:
    if not parts or not parts[-1].lower().endswith(WANTED):
        return False
    return not (parts[-1].startswith("._") or "__MACOSX" in parts)      # macOS resource forks, not real files


def is_archive(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


def native_kind(name: str) -> str | None:
    """'parts' / 'assemblies' / 'drawings' for a native Creo or Pro-E file name (versioned or not)."""
    return _NATIVE.get(Path(_VERSION.sub("", name.lower())).suffix)


def _extract_zip(zip_path: Path, root: Path, max_bytes: int) -> list[Path]:
    written: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                pieces = _safe_parts(member.filename)
                if member.is_dir() or not _wanted(pieces):
                    continue
                destination = root.joinpath(*(_clean(p) for p in pieces))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as sink:
                    while chunk := source.read(_CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"{zip_path.name} unpacks to more than "
                                             f"{max_bytes / 1024 ** 3:.1f} GB - not extracted")
                        sink.write(chunk)
                written.append(destination)
    except zipfile.BadZipFile as err:
        raise ValueError(f"{zip_path.name} is not a readable zip file ({err})") from err
    return written


def _extract_7z(archive_path: Path, root: Path, max_bytes: int) -> list[Path]:
    try:
        import py7zr
        from py7zr.exceptions import ArchiveError
    except ImportError as err:                       # pragma: no cover - py7zr is a dependency
        raise ValueError("reading .7z files needs the py7zr package (pip install py7zr)") from err
    written: list[Path] = []
    try:
        with py7zr.SevenZipFile(archive_path, "r") as archive:
            entries = [e for e in archive.list() if not e.is_directory and _wanted(_safe_parts(e.filename))]
            if not entries:
                return []
            size = sum(int(e.uncompressed or 0) for e in entries)
            if size > max_bytes:
                raise ValueError(f"{archive_path.name} unpacks to more than {max_bytes / 1024 ** 3:.1f} GB "
                                 "- not extracted")
            staging = root / ".staging"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            archive.extract(path=staging, targets=[e.filename for e in entries])
    except (ArchiveError, OSError, EOFError) as err:
        raise ValueError(f"{archive_path.name} is not a readable 7z file ({err})") from err
    staged = staging.resolve()
    for entry in entries:
        pieces = _safe_parts(entry.filename)
        source = (staging / Path(*pieces)).resolve()
        if not source.is_file() or staged not in source.parents:
            continue
        destination = root.joinpath(*(_clean(p) for p in pieces))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), destination)
        written.append(destination)
    shutil.rmtree(staging, ignore_errors=True)
    return written


def extract_archive(archive_path: str | Path, target: str | Path, max_bytes: int = MAX_UNZIPPED) -> list[Path]:
    """Unpack the STEP and PDF files of a .zip or .7z into `target`, keeping the archive's folders.

    Everything else in the archive (native CAD files, images) is ignored. Entries can not leave `target`:
    drive letters, leading slashes and ".." are dropped from their paths. Raises ValueError for a file that
    is not a readable archive, or one that unpacks to more than `max_bytes`.
    """
    source = Path(archive_path)
    root = Path(target).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".7z":
        return _extract_7z(source, root, max_bytes)
    return _extract_zip(source, root, max_bytes)


def extract_zip(zip_path: str | Path, target: str | Path, max_bytes: int = MAX_UNZIPPED) -> list[Path]:
    """Kept for callers of 0.4: same as extract_archive."""
    return extract_archive(zip_path, target, max_bytes)


def archive_names(archive_path: str | Path) -> list[str]:
    """File names inside a .zip or .7z (no extraction); [] when it can not be read."""
    path = Path(archive_path)
    try:
        if path.suffix.lower() == ".7z":
            import py7zr
            with py7zr.SevenZipFile(path, "r") as archive:
                return [e.filename for e in archive.list() if not e.is_directory]
        with zipfile.ZipFile(path) as archive:
            return [m.filename for m in archive.infolist() if not m.is_dir()]
    except Exception:  # noqa: BLE001 - a broken archive is reported by the caller, not raised here
        return []


def _files_below(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for dirpath, _dirs, files in os.walk(folder):
        found.extend(Path(dirpath) / name for name in files)
    return found


def unique_steps(paths: Iterable[str | Path]) -> list[str]:
    """Drop repeated STEP files (same name and size, e.g. a loose STEP and the same STEP inside a 7z).

    The first one wins, so pass loose files before unpacked ones. Order is otherwise kept.
    """
    seen: set[tuple[str, int]] = set()
    kept = []
    for path in paths:
        p = Path(path)
        key = (p.name.lower(), p.stat().st_size)
        if key not in seen:
            seen.add(key)
            kept.append(str(p))
    return kept


def find_inputs(folder: str | Path) -> tuple[list[str], list[str]]:
    """(STEP files, PDF drawings) below a folder, at any depth.

    STEP files come largest first: in a folder of part files plus one assembly, the assembly is
    normally the biggest file, so it is the default choice. Repeats (same name and size) are dropped.
    """
    files = _files_below(Path(folder))
    steps = sorted((p for p in files if p.suffix.lower() in STEP_SUFFIXES),
                   key=lambda p: (-p.stat().st_size, str(p).lower()))
    pdfs = sorted((p for p in files if p.suffix.lower() == ".pdf"), key=lambda p: str(p).lower())
    return unique_steps(steps), [str(p) for p in pdfs]


_DATE8 = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_DATE6 = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")
_DATE4 = re.compile(r"(?<!\d)(\d{2})(\d{2})(?!\d)")


def _valid(year: int, month: int, day: int) -> bool:
    return 2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31


def name_date(name: str, fallback_year: int) -> tuple[int, int, int] | None:
    """The date written in a file name: 20260805, 260909 or 0903 (that one in `fallback_year`); else None."""
    stem = Path(name).stem
    for match in _DATE8.finditer(stem):
        y, m, d = (int(g) for g in match.groups())
        if _valid(y, m, d):
            return y, m, d
    for match in _DATE6.finditer(stem):
        y, m, d = 2000 + int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _valid(y, m, d):
            return y, m, d
    for match in reversed(list(_DATE4.finditer(stem))):
        m, d = int(match.group(1)), int(match.group(2))
        if _valid(fallback_year, m, d):
            return fallback_year, m, d
    return None


def assembly_order(steps: Iterable[str | Path], share: float = 0.5) -> list[str]:
    """STEP files with the most likely top assembly first.

    Hand-over folders keep several dated copies of the full assembly next to small part files. The full
    assemblies are the files of at least `share` times the largest size; among them the newest date in the
    file name comes first (else the newest time stamp, then the larger file). The rest follow, largest first.
    """
    import datetime

    paths = [Path(s) for s in steps]
    if not paths:
        return []
    largest = max(p.stat().st_size for p in paths)

    def newest(p: Path) -> tuple:
        stat = p.stat()
        stamp = datetime.date.fromtimestamp(stat.st_mtime)
        written = name_date(p.name, stamp.year) or (stamp.year, stamp.month, stamp.day)
        return written, stat.st_mtime, stat.st_size

    full = sorted((p for p in paths if p.stat().st_size >= share * largest), key=newest, reverse=True)
    rest = sorted((p for p in paths if p.stat().st_size < share * largest), key=lambda p: -p.stat().st_size)
    return [str(p) for p in full + rest]


@dataclass
class Handover:
    """What a project folder holds, as far as tolstack is concerned."""
    steps: list[str] = field(default_factory=list)         # likely top assembly first, repeats dropped
    pdfs: list[str] = field(default_factory=list)
    native: dict[str, int] = field(default_factory=dict)    # Creo / Pro-E file counts, loose and in archives
    notes: list[str] = field(default_factory=list)          # one line per archive or problem worth telling


def scan_folder(folder: str | Path, cache: str | Path, max_bytes: int = MAX_UNZIPPED) -> Handover:
    """Everything usable below `folder`, including the STEP and PDF files inside its .zip / .7z archives.

    Archives are unpacked once into `cache` (one sub-folder per archive, keyed by name, size and time
    stamp), so a rerun does not unpack again. An archive with no STEP or PDF is listed, not unpacked.
    """
    root = Path(folder)
    cache = Path(cache)
    files = _files_below(root)
    result = Handover()
    native: dict[str, int] = {}
    for path in files:
        kind = native_kind(path.name)
        if kind:
            native[kind] = native.get(kind, 0) + 1
    unpacked_roots = []
    for archive in sorted((p for p in files if is_archive(p)), key=lambda p: str(p).lower()):
        stat = archive.stat()
        slot = cache / f"{_clean(archive.stem)[:60]}_{stat.st_size}_{int(stat.st_mtime)}"
        label = archive.relative_to(root).as_posix() if root in archive.parents else archive.name
        if (slot / ".empty").exists():
            continue
        if not slot.exists():
            names = archive_names(archive)
            if not names:
                result.notes.append(f"{label}: could not be read")
                continue
            for name in names:
                kind = native_kind(PurePosixPath(name.replace("\\", "/")).name)
                if kind:
                    native[kind] = native.get(kind, 0) + 1
            if not any(_wanted(_safe_parts(n)) for n in names):
                slot.mkdir(parents=True, exist_ok=True)
                (slot / ".empty").write_text(", ".join(sorted({Path(n).suffix for n in names}))[:500],
                                             encoding="utf-8")
                result.notes.append(f"{label}: no STEP or PDF inside ({len(names)} other files)")
                continue
            try:
                extract_archive(archive, slot, max_bytes)
            except (ValueError, OSError) as err:
                shutil.rmtree(slot, ignore_errors=True)
                result.notes.append(f"{label}: {err}")
                continue
        unpacked_roots.append(slot)
    loose_steps, loose_pdfs = find_inputs(root)
    packed_steps: list[str] = []
    packed_pdfs: list[str] = []
    for slot in unpacked_roots:
        steps, pdfs = find_inputs(slot)
        packed_steps += steps
        packed_pdfs += pdfs
    every_step = sorted(loose_steps + packed_steps, key=lambda s: -Path(s).stat().st_size)
    loose_first = sorted(every_step, key=lambda s: s not in loose_steps)     # loose copy wins a repeat
    keep = set(unique_steps(loose_first))
    result.steps = assembly_order(s for s in every_step if s in keep)
    result.pdfs = loose_pdfs + packed_pdfs
    result.native = native
    return result


def native_note(native: Mapping[str, int]) -> str | None:
    """A sentence explaining native Creo / Pro-E files, or None when there are none."""
    if not native:
        return None
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(native.items(), key=lambda kv: -kv[1]))
    return (f"native Creo / Pro-E files ({counts}) - tolstack reads STEP: on a PC with Creo use 'Path on this "
            "computer' and Convert with Creo, or in Creo open the top assembly, File > Save As > Save a Copy, "
            "type STEP, and add that file (or zip / 7z it)")


def sibling_drawing_folder(path: str | Path, levels: int = 3) -> Path | None:
    """The project's 2D folder next to a 3D folder, e.g. '17 ... -2D' beside '16 ... 3D'.

    Looks at the siblings of `path` (and of up to `levels` parents) for a folder whose name says 2D but not
    3D; a name starting with 17 wins a tie. None when there is no such folder.
    """
    here = Path(path)
    if here.is_file():
        here = here.parent
    for _ in range(levels):
        parent = here.parent
        if parent == here:
            break
        try:
            candidates = [d for d in parent.iterdir()
                          if d.is_dir() and d != here and _TWO_D.search(d.name) and not _THREE_D.search(d.name)]
        except OSError:
            return None
        if candidates:
            candidates.sort(key=lambda d: (not d.name.lstrip().startswith("17"), d.name.lower()))
            return candidates[0]
        here = parent
    return None


def _store(destination: Path, source: Callable[[], bytes] | IO[bytes]) -> None:
    if callable(source):
        destination.write_bytes(source())
        return
    if hasattr(source, "seek"):
        source.seek(0)
    with open(destination, "wb") as sink:
        shutil.copyfileobj(source, sink, _CHUNK)        # no second copy of a large upload in memory


def sync_inbox(inbox: str | Path, uploads: Mapping[str, Upload]) -> list[str]:
    """Make `inbox` hold exactly the given uploads, one sub-folder per upload id.

    Returns one message per upload that could not be stored (a broken archive, a full disk); the
    other uploads are still stored.

    uploads   {upload id: (file name, function returning the bytes, or a readable file object)}
    A .zip or .7z is unpacked (STEP and PDF only); other files are stored under their own name. An upload
    that is already there is left alone, so the STEP keeps its time stamp and is not read again
    when the user only adds a drawing. Folders of uploads that are gone are removed.
    """
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    slots = {"".join(ch for ch in upload_id if ch.isalnum() or ch in "-_") or "upload": item
             for upload_id, item in uploads.items()}
    for stale in inbox.iterdir():
        if stale.name not in slots:
            shutil.rmtree(stale, ignore_errors=True)
    problems: list[str] = []
    for slot_name, (name, read) in slots.items():
        slot = inbox / slot_name
        if slot.exists():
            continue
        slot.mkdir()
        file_name = _clean(PurePosixPath(name.replace("\\", "/")).name)
        try:
            saved = slot / file_name
            _store(saved, read)
            if is_archive(file_name):
                extracted = extract_archive(saved, slot / "unpacked")
                saved.unlink()
                if not extracted:
                    problems.append(f"{name}: no STEP or PDF inside (native CAD files are not read - "
                                    "export the assembly as STEP)")
        except (OSError, ValueError) as err:
            shutil.rmtree(slot, ignore_errors=True)        # do not keep half an upload
            problems.append(f"{name}: {err}")
    return problems
