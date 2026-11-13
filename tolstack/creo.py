"""Native Creo / Pro-E files: list them, and turn an assembly into STEP with the Creo installed on this PC.

tolstack reads STEP. Creo part and assembly files (.prt.12, .asm.3) are PTC's own compressed binary format,
so they are not decoded here: a guessed surface or offset would silently give a wrong stack-up. Instead
the Creo Parametric installed on this computer opens the assembly in batch mode (no window) and saves a
STEP copy, the same as File > Save As > STEP by hand. The user's folder is never written to: the files are
copied into a private job folder first (Creo also fails on paths with non-ASCII characters).

Without Creo (for example on the Linux server) the files are still listed with their kind, Creo release
and the assemblies they hold, so the user knows which assembly to export.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_NAME = re.compile(r"^(?P<stem>.+)\.(?P<kind>prt|asm|drw)(?:\.(?P<version>\d+))?$", re.IGNORECASE)
_HEADER = re.compile(rb"#UGC:\d+\s+(PART|ASSEMBLY|DRAWING)\b")
_RELEASE = re.compile(rb"#(Creo|Pro/ENGINEER|Wildfire)\s+TM\s+([0-9.]+)[^\n]*?\b(M\d{3})?\b", re.IGNORECASE)
_TOKEN = re.compile(rb"[A-Za-z0-9_\-]{2,80}")
_TOP_HINT = re.compile(r"(^|[_\-])(all|top|main|total|final|full)([_\-]|$)", re.IGNORECASE)
_LICENSE = re.compile(r"(EXCLUDE list|INCLUDE list|license|licence|FLEXlm|lmgrd|No such feature)", re.IGNORECASE)
_STEP_TYPE = "db_539"        # the STEP entry of the Save a Copy "Type" list (Creo 2 - Creo 10)


class CreoError(RuntimeError):
    """Creo could not export the STEP file; the message says why and what to do."""


class CreoLicenseError(CreoError):
    """Creo started but got no license."""


@dataclass(frozen=True)
class CreoFile:
    """One native file: name.asm.3 -> stem 'name', kind 'asm', version 3."""
    path: Path
    stem: str
    kind: str
    version: int

    @property
    def model(self) -> str:
        """The name Creo opens it by, e.g. 'name.asm' (lower case, no version)."""
        return f"{self.stem}.{self.kind}"


@dataclass
class Assembly:
    """An assembly and the models of the same folder set that it refers to."""
    file: CreoFile
    parts: list[str] = field(default_factory=list)         # referenced part stems
    assemblies: list[str] = field(default_factory=list)    # referenced sub-assembly stems

    @property
    def label(self) -> str:
        bits = [f"{self.file.model} (version {self.file.version})"]
        if self.assemblies or self.parts:
            bits.append(f"refers to {len(self.assemblies)} sub-assemblies, {len(self.parts)} parts")
        return ", ".join(bits)


def parse_name(path: str | Path) -> CreoFile | None:
    """A CreoFile for a native file name, or None for anything else."""
    p = Path(path)
    match = _NAME.match(p.name)
    if not match:
        return None
    return CreoFile(p, match["stem"].lower(), match["kind"].lower(), int(match["version"] or 0))


def latest_versions(paths: Iterable[str | Path]) -> list[CreoFile]:
    """The newest version of every model: name.prt.12 wins over name.prt.11 (then the later time stamp)."""
    best: dict[str, CreoFile] = {}
    for path in paths:
        item = parse_name(path)
        if item is None:
            continue
        held = best.get(item.model)
        if held is None or (item.version, item.path.stat().st_mtime) > (held.version, held.path.stat().st_mtime):
            best[item.model] = item
    return sorted(best.values(), key=lambda f: f.model)


def read_header(path: str | Path) -> dict[str, str]:
    """{'kind': 'PART' | 'ASSEMBLY' | 'DRAWING', 'release': 'Creo 2.0 M250'} from the text header; {} if none."""
    with open(path, "rb") as handle:
        head = handle.read(4096)
    info: dict[str, str] = {}
    kind = _HEADER.search(head)
    if kind:
        info["kind"] = kind.group(1).decode()
    release = _RELEASE.search(head)
    if release:
        product, number, build = (g.decode() if g else "" for g in release.groups())
        info["release"] = " ".join(x for x in (product, number, build) if x)
    return info


def assemblies(files: Iterable[CreoFile]) -> list[Assembly]:
    """Every assembly with the models it names, most likely top assembly first.

    Creo stores component names as plain text inside the assembly file, so the references are found by
    matching the names of the other files handed over. Sub-assemblies may also name their parent (skeleton
    and external references), so "not used by another assembly" does not find the top reliably. The order
    is a guess the user confirms: a name with all / top / main / total / final / full first, then the assembly that
    holds the most sub-assemblies, then the most parts.
    """
    files = list(files)
    known = {f.stem: f.kind for f in files if f.kind in ("prt", "asm")}
    found = []
    for item in files:
        if item.kind != "asm":
            continue
        tokens = {t.decode().lower() for t in _TOKEN.findall(item.path.read_bytes())}
        named = sorted((tokens & known.keys()) - {item.stem})
        found.append(Assembly(item,
                              parts=[n for n in named if known[n] == "prt"],
                              assemblies=[n for n in named if known[n] == "asm"]))
    return sorted(found, key=lambda a: (not _TOP_HINT.search(a.file.stem), -len(a.assemblies), -len(a.parts),
                                        a.file.stem))


def summary(paths: Iterable[str | Path]) -> str | None:
    """'12 parts, 3 assemblies (Creo 2.0 M250)' for the native files among `paths`; None when there are none."""
    files = latest_versions(paths)
    if not files:
        return None
    counts: dict[str, int] = {}
    for f in files:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    names = {"prt": "parts", "asm": "assemblies", "drw": "drawings"}
    text = ", ".join(f"{n} {names[k]}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    releases: dict[str, int] = {}
    for f in files[:400]:                                   # enough to show the mix without reading every file
        release = read_header(f.path).get("release", "unknown release")
        releases[release] = releases.get(release, 0) + 1
    mix = ", ".join(f"{r} x{n}" if len(releases) > 1 else r
                    for r, n in sorted(releases.items(), key=lambda kv: -kv[1]))
    return f"{text} ({mix})" if mix else text


def needed_files(assembly: CreoFile, files: Iterable[CreoFile]) -> list[CreoFile]:
    """The newest version of `assembly` and of every model it names, through its sub-assemblies."""
    newest = {f.stem: f for f in latest_versions(f.path for f in files)}
    newest[assembly.stem] = assembly
    wanted = {assembly.stem}
    queue = [assembly]
    while queue:
        item = queue.pop()
        if item.kind != "asm":
            continue
        tokens = {t.decode().lower() for t in _TOKEN.findall(item.path.read_bytes())}
        for stem in sorted((tokens & newest.keys()) - wanted):
            wanted.add(stem)
            queue.append(newest[stem])
    return sorted((newest[s] for s in wanted), key=lambda f: f.model)


def release_rank(release: str | None) -> float | None:
    """A number that orders releases: Pro/ENGINEER Wildfire 5.0 < Creo 2.0 < Creo 5.0. None if unknown."""
    match = re.search(r"(Creo|Pro/ENGINEER|Wildfire)\s*([0-9]+(?:\.[0-9]+)?)", release or "", re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(2))
    return 10 + number if match.group(1).lower() == "creo" else number / 100


def installed_release(creo_exe: str | Path | None) -> str | None:
    """'Creo 2.0' for D:/PTC/Creo 2.0/Parametric/bin/parametric.exe; None when the path does not say."""
    if creo_exe is None:
        return None
    for part in reversed(Path(creo_exe).parts):
        match = re.match(r"Creo\s*([0-9]+(?:\.[0-9]+)?)", part, re.IGNORECASE)
        if match:
            return f"Creo {match.group(1)}"
    return None


def too_new(files: Iterable[CreoFile], creo_exe: str | Path | None) -> list[tuple[CreoFile, str]]:
    """The files saved by a newer Creo than the installed one (Creo can not open files from a newer release)."""
    have = release_rank(installed_release(creo_exe))
    if have is None:
        return []
    newer = []
    for item in files:
        release = read_header(item.path).get("release")
        rank = release_rank(release)
        if rank is not None and rank > have + 1e-9:
            newer.append((item, release or ""))
    return newer


# ------------------------------------------------------------------ export with the installed Creo
def find_creo() -> Path | None:
    """parametric.exe of the newest Creo Parametric on this computer, or None.

    Set TOLSTACK_CREO to the full path of parametric.exe (or of a Creo start script) to choose one.
    """
    chosen = os.environ.get("TOLSTACK_CREO", "").strip().strip('"')
    if chosen:
        return Path(chosen) if Path(chosen).is_file() else None
    if os.name != "nt":
        return None
    found: list[Path] = []
    for drive in ("C:", "D:", "E:"):
        for pattern in ("PTC/Creo */Parametric/bin/parametric.exe",
                        "Program Files/PTC/Creo */Parametric/bin/parametric.exe"):
            try:
                found.extend(Path(drive + "/").glob(pattern))
            except OSError:
                continue
    if not found:
        return None
    return max(found, key=lambda p: _release_number(p.parts[-4]))


def _release_number(folder: str) -> tuple[float, str]:
    number = re.search(r"(\d+(?:\.\d+)?)", folder)
    return (float(number.group(1)) if number else 0.0, folder)


def _trail(model: str, out_name: str) -> str:
    """A Creo trail file: open the model, Save a Copy as STEP, exit."""
    lines = [
        "!trail file version No. 1600",
        "!Creo  TM  2.0  (c) by PTC Inc.  All Rights Reserved.",
        "~ Command `ProCmdModelOpen` ",
        f"~ Update `file_open` `Inputname` `{model}`",
        "~ Activate `file_open` `Open`",
        "~ Command `ProCmdModelSaveAs` ",
        "~ Open `file_saveas` `type_option`",
        "~ Close `file_saveas` `type_option`",
        f"~ Select `file_saveas` `type_option` 1 `{_STEP_TYPE}`",
        f"~ Update `file_saveas` `Inputname` `{out_name}`",
        "~ Activate `file_saveas` `OK`",
        "~ Activate `intf_export` `OkPushBtn`",
        "~ Command `ProCmdOSExit` ",
        "~ FocusIn `UI Message Dialog` `yes`",
        "~ Activate `UI Message Dialog` `yes`",
    ]
    return "\n".join(lines) + "\n"


def license_problem(std_out: str) -> str | None:
    """The license lines of a Creo std.out, or None when it shows no license problem."""
    hits = [line.strip() for line in std_out.splitlines()
            if _LICENSE.search(line) and ("not" in line.lower() or "denied" in line.lower()
                                          or "error" in line.lower() or "list" in line.lower())]
    return "\n".join(dict.fromkeys(hits)) or None


def _ascii(text: str) -> str:
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_.") else "_" for ch in text)


def export_step(assembly: CreoFile, files: Iterable[CreoFile], out_dir: str | Path,
                creo: str | Path | None = None, timeout: float = 1800) -> Path:
    """Export `assembly` to STEP with Creo and return the .stp file.

    files    every native file it may need (its parts and sub-assemblies); the newest version of each is
             copied into a job folder below `out_dir` under a plain ASCII name, so the user's folder is
             never changed and paths with Chinese characters do not stop Creo.
    A STEP made earlier from the same files is reused. Raises CreoError (CreoLicenseError when the PTC
    license server refuses) with a message for the user.
    """
    creo_exe = Path(creo) if creo else find_creo()
    if creo_exe is None or not creo_exe.is_file():
        raise CreoError("Creo Parametric was not found on this computer. Install it, or set TOLSTACK_CREO to "
                        "the full path of parametric.exe; or export the assembly as STEP in Creo yourself.")
    newest = needed_files(assembly, files)
    newer = too_new(newest, creo_exe)
    if newer:
        first, release = newer[0]
        raise CreoError(f"{len(newer)} of the {len(newest)} files this assembly needs (for example {first.path.name}) were "
                        f"saved in {release}; the {installed_release(creo_exe)} on this computer can not open files "
                        "from a newer Creo. Convert them on a PC with that Creo release or newer, or ask the owner "
                        "for a STEP export.")
    stamp = max(int(f.path.stat().st_mtime) for f in newest)
    job = Path(out_dir) / f"{_ascii(assembly.stem)[:60]}_{len(newest)}_{stamp}"
    out_name = _ascii(assembly.stem)[:60] or "assembly"
    done = sorted(job.glob(f"{out_name}*.stp")) if job.is_dir() else []
    if done:
        return done[0]
    job.mkdir(parents=True, exist_ok=True)
    for item in newest:
        target = job / f"{item.stem}.{item.kind}.1"
        if not target.exists():
            shutil.copy2(item.path, target)
    (job / "tolstack_export.txt").write_text(_trail(assembly.model, out_name), encoding="ascii", errors="replace")
    started = time.time()
    timed_out = False
    try:
        subprocess.run([str(creo_exe), "-g:no_graphics", "tolstack_export.txt"], cwd=job,
                       timeout=timeout, check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)    # no pipes: Creo's name server (nmsd) outlives it
    except subprocess.TimeoutExpired:
        timed_out = True
    except OSError as err:
        raise CreoError(f"Creo could not be started ({err}).") from err
    std_out = (job / "std.out").read_text(encoding="utf-8", errors="replace") if (job / "std.out").exists() else ""
    produced = sorted((p for p in job.glob("*.stp") if p.stat().st_mtime >= started - 1), key=lambda p: p.name)
    if produced:
        final = job / f"{out_name}.stp"
        if produced[0] != final:
            produced[0].replace(final)
        return final
    problem = license_problem(std_out)
    if problem:
        raise CreoLicenseError("Creo started but the PTC license server refused this user / PC:\n" + problem
                               + "\nAsk your PTC license administrator to allow this user on the Creo license "
                                 "(the EXCLUDE / INCLUDE lists of the license server). Until then export the "
                                 "STEP in a Creo session that has a license.")
    if timed_out:
        raise CreoError(f"Creo did not finish within {timeout / 60:.0f} minutes; the assembly may be too large "
                        "for a batch export - export it as STEP in Creo by hand.")
    tail = "\n".join(std_out.strip().splitlines()[-8:])
    raise CreoError("Creo ran but wrote no STEP file. The last lines of its log:\n" + (tail or "(no log)"))
