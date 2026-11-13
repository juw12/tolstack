"""v0.5.0: real hand-over folders - 7z archives, STEP inside archives, repeats, native Creo files, the 2D folder."""
import zipfile
from pathlib import Path

import py7zr
import pytest

from tolstack.intake import (
    assembly_order,
    extract_archive,
    find_inputs,
    name_date,
    native_kind,
    native_note,
    scan_folder,
    sibling_drawing_folder,
    sync_inbox,
    unique_steps,
)


def _seven(path: Path, members: dict[str, bytes]) -> Path:
    with py7zr.SevenZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(data, name)
    return path


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_extract_7z_keeps_only_step_and_pdf(tmp_path):
    archive = _seven(tmp_path / "set.7z", {
        "asm/top.stp": b"step",
        "asm/top.asm.3": b"creo",
        "drw/A.pdf": b"pdf",
    })
    written = extract_archive(archive, tmp_path / "out")
    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in written) == ["asm/top.stp", "drw/A.pdf"]
    assert not (tmp_path / "out" / ".staging").exists()


def test_extract_7z_rejects_broken_and_too_large(tmp_path):
    broken = tmp_path / "broken.7z"
    broken.write_bytes(b"not a 7z")
    with pytest.raises(ValueError, match="not a readable 7z"):
        extract_archive(broken, tmp_path / "a")
    big = _seven(tmp_path / "big.7z", {"top.step": b"x" * 100})
    with pytest.raises(ValueError, match="more than"):
        extract_archive(big, tmp_path / "b", max_bytes=10)


def test_native_creo_names_are_recognised():
    assert native_kind("cover.prt.12") == "parts"
    assert native_kind("TOP.ASM") == "assemblies"
    assert native_kind("cover.drw.2") == "drawings"
    assert native_kind("cover.stp") is None
    assert native_note({}) is None
    assert "STEP" in native_note({"parts": 3})


def test_unique_steps_drops_repeats_of_same_name_and_size(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = tmp_path / "a" / "TOP.stp"
    first.write_bytes(b"12345")
    same = tmp_path / "b" / "top.stp"
    same.write_bytes(b"12345")
    other = tmp_path / "b" / "top_v2.stp"
    other.write_bytes(b"12345")
    assert unique_steps([first, same, other]) == [str(first), str(other)]


def test_scan_folder_finds_step_inside_archives_once(tmp_path):
    project = tmp_path / "16-3D"
    project.mkdir()
    (project / "all_asm.stp").write_bytes(b"s" * 50)                     # loose copy
    _seven(project / "all_asm.7z", {"all_asm.stp": b"s" * 50})            # the same file, packed
    _seven(project / "newer.7z", {"all_asm_0903.stp": b"n" * 80})         # a newer, bigger one
    _zip(project / "native.zip", {"top.asm.1": b"c", "a.prt.4": b"c", "b.prt.2": b"c"})
    (project / "cover.prt.7").write_bytes(b"c")
    cache = tmp_path / "cache"
    found = scan_folder(project, cache)
    assert sorted(Path(s).name for s in found.steps) == ["all_asm.stp", "all_asm_0903.stp"]
    loose = next(s for s in found.steps if Path(s).name == "all_asm.stp")
    assert Path(loose).parent == project                                  # the loose copy wins the repeat
    assert found.native == {"parts": 3, "assemblies": 1}
    assert any("native.zip" in note and "no STEP" in note for note in found.notes)
    again = scan_folder(project, cache)                                    # second run: nothing unpacked again
    assert again.steps == found.steps


def test_sibling_2d_folder_is_found_next_to_the_3d_folder(tmp_path):
    handover = tmp_path / "PRODUCT_handover"
    three_d = handover / "16-latest 3D (Proe)"
    (three_d / "3D").mkdir(parents=True)
    (handover / "17-key dimensions-2D").mkdir()
    (handover / "03 PCBA placement dxf").mkdir()
    step = three_d / "3D" / "top.stp"
    step.write_bytes(b"s")
    assert sibling_drawing_folder(three_d).name == "17-key dimensions-2D"
    assert sibling_drawing_folder(step).name == "17-key dimensions-2D"     # from a file two levels down
    assert sibling_drawing_folder(tmp_path / "nowhere" / "x", levels=1) is None


def test_sync_inbox_streams_file_objects_and_unpacks_7z(tmp_path):
    import io

    packed = _seven(tmp_path / "up.7z", {"top.step": b"step", "A.pdf": b"pdf"}).read_bytes()
    native_only = _seven(tmp_path / "native.7z", {"top.asm.2": b"creo"}).read_bytes()
    inbox = tmp_path / "inbox"
    problems = sync_inbox(inbox, {
        "id-1": ("up.7z", io.BytesIO(packed)),
        "id-2": ("loose.stp", io.BytesIO(b"loose step")),
        "id-3": ("native.7z", io.BytesIO(native_only)),
    })
    assert len(problems) == 1 and "native.7z" in problems[0] and "no STEP" in problems[0]
    steps, pdfs = find_inputs(inbox)
    assert sorted(Path(s).name for s in steps) == ["loose.stp", "top.step"]
    assert [Path(p).name for p in pdfs] == ["A.pdf"]


def test_upload_limit_is_raised_for_large_step_files():
    config = (Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    line = next(row for row in config.splitlines() if row.startswith("maxUploadSize"))
    assert int(line.split("=")[1]) >= 1024


def test_name_date_reads_the_date_styles_used_in_handover_files():
    assert name_date("producta_system_all_40mm_spk_asm20260716.stp", 2025) == (2026, 7, 16)
    assert name_date("productb_g_cam_all_asm_micX3_260909.stp", 2025) == (2026, 9, 9)
    assert name_date("productc3_all_new_asm_0903.stp", 2025) == (2025, 9, 3)
    assert name_date("motor_metal_cnc1.stp", 2025) is None
    assert name_date("part_40mm.stp", 2025) is None


def test_newest_full_assembly_comes_first_not_just_the_largest(tmp_path):
    older_big = tmp_path / "producta_system_all_asm20260716.stp"
    older_big.write_bytes(b"x" * 1000)
    newer = tmp_path / "productb_g_system_all_asm20260805.stp"
    newer.write_bytes(b"x" * 980)
    small_new = tmp_path / "bracket_20261001.stp"
    small_new.write_bytes(b"x" * 10)
    order = [Path(p).name for p in assembly_order([older_big, small_new, newer])]
    assert order == [newer.name, older_big.name, small_new.name]


def test_load_model_reuses_parts_already_read(tmp_path, monkeypatch):
    from tolstack import pipeline
    from tolstack.cad import load_step
    from tolstack.demo import write_demo

    step = write_demo(tmp_path / "demo")
    parts = load_step(str(step))

    def no_second_read(path):
        raise AssertionError("the STEP file was read again")

    monkeypatch.setattr(pipeline, "load_step", no_second_read)
    along_z = pipeline.load_model(str(step), "z", 0.02, parts=parts)
    along_y = pipeline.load_model(str(step), "y", 0.05, parts=parts)
    assert along_z.parts is parts and along_y.parts is parts
    assert along_z.axis == "z" and along_y.axis == "y"
