"""Find the STEP assembly and the 2D drawings in what the user hands over: loose files, a folder or a zip."""
from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

STEP_SUFFIXES = (".step", ".stp")
WANTED = STEP_SUFFIXES + (".pdf",)
MAX_UNZIPPED = 4 * 1024 ** 3          # bytes written from one zip; a "zip bomb" stops here
_CHUNK = 1 << 20


def _clean(name: str) -> str:
    """One path component that is safe on Windows and Linux."""
    return "".join("_" if ch in '<>:"|?*' or ord(ch) < 32 else ch for ch in name).strip(" .") or "_"


def extract_zip(zip_path: str | Path, target: str | Path, max_bytes: int = MAX_UNZIPPED) -> list[Path]:
    """Unpack the STEP and PDF files of a zip into `target`, keeping the zip's folders.

    Everything else in the archive is ignored. Entries can not leave `target`: drive letters,
    leading slashes and ".." are dropped from their paths. Raises ValueError for a file that is
    not a zip, or one that unpacks to more than `max_bytes`.
    """
    root = Path(target).resolve()
    written: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                pieces = [p for p in PurePosixPath(member.filename.replace("\\", "/")).parts
                          if p not in ("/", ".", "..") and not p.endswith(":")]
                if member.is_dir() or not pieces or not pieces[-1].lower().endswith(WANTED):
                    continue
                if pieces[-1].startswith("._") or "__MACOSX" in pieces:      # macOS resource forks, not real files
                    continue
                destination = root.joinpath(*(_clean(p) for p in pieces))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as sink:
                    while chunk := source.read(_CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"{Path(zip_path).name} unpacks to more than "
                                             f"{max_bytes / 1024 ** 3:.1f} GB - not extracted")
                        sink.write(chunk)
                written.append(destination)
    except zipfile.BadZipFile as err:
        raise ValueError(f"{Path(zip_path).name} is not a readable zip file ({err})") from err
    return written


def find_inputs(folder: str | Path) -> tuple[list[str], list[str]]:
    """(STEP files, PDF drawings) below a folder, at any depth.

    STEP files come largest first: in a folder of part files plus one assembly, the assembly is
    normally the biggest file, so it is the default choice.
    """
    root = Path(folder)
    files = [p for p in root.rglob("*") if p.is_file()] if root.is_dir() else []
    steps = sorted((p for p in files if p.suffix.lower() in STEP_SUFFIXES),
                   key=lambda p: (-p.stat().st_size, str(p).lower()))
    pdfs = sorted((p for p in files if p.suffix.lower() == ".pdf"), key=lambda p: str(p).lower())
    return [str(p) for p in steps], [str(p) for p in pdfs]


def sync_inbox(inbox: str | Path, uploads: Mapping[str, tuple[str, Callable[[], bytes]]]) -> list[str]:
    """Make `inbox` hold exactly the given uploads, one sub-folder per upload id.

    Returns one message per upload that could not be stored (a broken zip, a full disk); the
    other uploads are still stored.

    uploads   {upload id: (file name, function returning the bytes)}
    A zip is unpacked (STEP and PDF only); other files are stored under their own name. An upload
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
            saved.write_bytes(read())
            if file_name.lower().endswith(".zip"):
                extract_zip(saved, slot / "unzipped")
                saved.unlink()
        except (OSError, ValueError) as err:
            shutil.rmtree(slot, ignore_errors=True)        # do not keep half an upload
            problems.append(f"{name}: {err}")
    return problems
