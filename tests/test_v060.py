"""v0.6: native Creo files (list, top assembly, STEP export through the installed Creo) and the memory guard."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tolstack import creo, resources

HEADER = (b"#UGC:2 ASSEMBLY 1905 1360 809 1 1 15 3600 2018050 000002d0 \\\n"
          b"#- VERS 0 0 \\\n#Creo  TM  2.0  (c) 2012 by PTC Inc.  All Rights Reserved. M250 \\\n")


def _asm(path: Path, *components: str) -> Path:
    body = b"".join(b"\x01\xe0\nname\x00" + c.upper().encode() + b"\x00\xe0\nfamily_name\x00" for c in components)
    path.write_bytes(HEADER + b"\x00" * 64 + body)
    return path


def _prt(path: Path) -> Path:
    path.write_bytes(HEADER.replace(b"ASSEMBLY", b"PART") + b"\x00" * 32)
    return path


@pytest.fixture
def handover(tmp_path: Path) -> Path:
    tmp_path = tmp_path / "handover"
    tmp_path.mkdir()
    _prt(tmp_path / "cover.prt.3")
    _prt(tmp_path / "cover.prt.4")
    _prt(tmp_path / "base.prt.1")
    _prt(tmp_path / "screw.prt.2")
    _asm(tmp_path / "sub_asm.asm.2", "screw", "base")
    _asm(tmp_path / "product_all_asm.asm.7", "sub_asm", "cover")
    _asm(tmp_path / "jig.asm.1", "screw")
    (tmp_path / "notes.txt").write_text("not CAD")
    return tmp_path


def test_parse_name_and_latest_versions(handover: Path) -> None:
    item = creo.parse_name(handover / "cover.prt.4")
    assert item is not None and (item.stem, item.kind, item.version, item.model) == ("cover", "prt", 4, "cover.prt")
    assert creo.parse_name(handover / "notes.txt") is None
    assert creo.parse_name("plain.asm").version == 0                     # type: ignore[union-attr]
    latest = {f.model: f.version for f in creo.latest_versions(handover.iterdir())}
    assert latest["cover.prt"] == 4 and len(latest) == 6


def test_header_release_and_summary(handover: Path) -> None:
    info = creo.read_header(handover / "sub_asm.asm.2")
    assert info["kind"] == "ASSEMBLY" and info["release"].startswith("Creo 2.0")
    text = creo.summary(handover.iterdir())
    assert text is not None and "3 parts" in text and "3 assemblies" in text and "Creo 2.0" in text
    assert creo.summary([handover / "notes.txt"]) is None


def test_assemblies_top_first_and_needed_files(handover: Path) -> None:
    files = creo.latest_versions(handover.iterdir())
    found = creo.assemblies(files)
    top = found[0]
    assert top.file.stem == "product_all_asm"                    # name says "all", holds a sub-assembly
    assert top.assemblies == ["sub_asm"] and top.parts == ["cover"]
    assert "1 sub-assemblies, 1 parts" in top.label
    needed = {f.model for f in creo.needed_files(top.file, files)}
    assert needed == {"product_all_asm.asm", "sub_asm.asm", "cover.prt", "screw.prt", "base.prt"}   # not jig


def test_trail_has_open_saveas_step_and_exit() -> None:
    trail = creo._trail("product_all_asm.asm", "product_all_asm")
    assert "`file_open` `Inputname` `product_all_asm.asm`" in trail
    assert "`db_539`" in trail and "ProCmdModelSaveAs" in trail and "ProCmdOSExit" in trail


def test_license_problem_is_found() -> None:
    log = ("PROE_EngineerIII: User/host on EXCLUDE list (ptw-mid-index)\n"
           "PROE_EngineerIIBMX: User/host not on INCLUDE list for feature. (ptw-creo-02)\nOther line\n")
    found = creo.license_problem(log)
    assert found is not None and "EXCLUDE list" in found and "INCLUDE list" in found
    assert creo.license_problem("WARNING - config.pro parameter can not be reset") is None


def test_export_step_success_copies_newest_and_reuses(handover: Path, tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "parametric.exe"
    exe.write_text("")
    calls = []

    def fake_run(args, cwd, **_kw):
        calls.append(Path(cwd))
        job = Path(cwd)
        assert (job / "cover.prt.1").read_bytes() == (handover / "cover.prt.4").read_bytes()
        assert not (job / "jig.asm.1").exists()
        (job / "product_all_asm.stp").write_text("ISO-10303-21;")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(creo.subprocess, "run", fake_run)
    files = creo.latest_versions(handover.iterdir())
    top = creo.assemblies(files)[0]
    out = creo.export_step(top.file, files, tmp_path / "jobs", creo=exe)
    assert out.name == "product_all_asm.stp" and out.read_text() == "ISO-10303-21;"
    again = creo.export_step(top.file, files, tmp_path / "jobs", creo=exe)
    assert again == out and len(calls) == 1                       # second call reuses the STEP
    assert sorted(p.name for p in handover.iterdir()) == sorted(                       # user folder unchanged
        ["base.prt.1", "cover.prt.3", "cover.prt.4", "jig.asm.1", "notes.txt", "product_all_asm.asm.7",
         "screw.prt.2", "sub_asm.asm.2"])


def test_export_step_license_error(handover: Path, tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "parametric.exe"
    exe.write_text("")

    def fake_run(args, cwd, **_kw):
        (Path(cwd) / "std.out").write_text("PROE_EngineerIII: User/host on EXCLUDE list (ptw-mid-index)\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(creo.subprocess, "run", fake_run)
    files = creo.latest_versions(handover.iterdir())
    with pytest.raises(creo.CreoLicenseError, match="license administrator"):
        creo.export_step(files[0], files, tmp_path / "jobs", creo=exe)


def test_export_step_without_creo(handover: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOLSTACK_CREO", str(tmp_path / "missing.exe"))
    files = creo.latest_versions(handover.iterdir())
    assert creo.find_creo() is None
    with pytest.raises(creo.CreoError, match="not found"):
        creo.export_step(files[0], files, tmp_path / "jobs")


def test_memory_limit_from_env_and_upload_size(monkeypatch) -> None:
    monkeypatch.setenv("TOLSTACK_MEMORY_LIMIT_MB", "1024")
    assert resources.memory_limit_bytes() == 1024 * 2**20
    size = resources.max_upload_mb()
    assert 50 <= size < 200                                   # 1 GiB pod: about 100 MB of STEP
    assert resources.max_upload_mb(4 * 2**30) > size          # more memory, larger uploads
    monkeypatch.delenv("TOLSTACK_MEMORY_LIMIT_MB")
    assert resources.max_upload_mb(None) in (resources.LOCAL_UPLOAD_MB, size) or resources.memory_limit_bytes()


def test_check_step_refuses_only_what_does_not_fit() -> None:
    limit = 1024 * 2**20
    used = 400 * 2**20
    assert resources.check_step(50 * 2**20, limit, used) is None
    message = resources.check_step(300 * 2**20, limit, used)
    assert message is not None and "300 MB" in message and "own PC" in message
    assert resources.check_step(10 * 2**30, limit_bytes=0) is None or resources.memory_limit_bytes()


def test_cgroup_files_are_read(tmp_path: Path, monkeypatch) -> None:
    v2 = tmp_path / "memory.max"
    v2.write_text("1073741824\n")
    monkeypatch.delenv("TOLSTACK_MEMORY_LIMIT_MB", raising=False)
    monkeypatch.setattr(resources, "_V2_LIMIT", v2)
    assert resources.memory_limit_bytes() == 2**30
    v2.write_text("max\n")
    monkeypatch.setattr(resources, "_V1_LIMIT", tmp_path / "absent")
    assert resources.memory_limit_bytes() is None


def test_newer_creo_files_are_refused_before_creo_runs(handover: Path, tmp_path: Path, monkeypatch) -> None:
    assert creo.release_rank("Creo 5.0") > creo.release_rank("Creo 2.0") > creo.release_rank("Pro/ENGINEER 5.0")
    exe = tmp_path / "PTC" / "Creo 2.0" / "Parametric" / "bin" / "parametric.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert creo.installed_release(exe) == "Creo 2.0"
    newer = handover / "cover.prt.4"
    newer.write_bytes(newer.read_bytes().replace(b"Creo  TM  2.0", b"Creo  TM  5.0"))
    files = creo.latest_versions(handover.iterdir())
    assert [f.path.name for f, _ in creo.too_new(files, exe)] == ["cover.prt.4"]
    monkeypatch.setattr(creo.subprocess, "run", lambda *a, **k: pytest.fail("Creo must not start"))
    top = creo.assemblies(files)[0]
    with pytest.raises(creo.CreoError, match="Creo 5.0"):
        creo.export_step(top.file, files, tmp_path / "jobs", creo=exe)
    assert "Creo 5.0 x1" in (creo.summary(handover.iterdir()) or "")
