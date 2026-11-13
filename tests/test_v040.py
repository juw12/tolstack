"""v0.4.0: folder / zip intake, the general-tolerance note of a drawing, the 3-D view."""
import zipfile
from pathlib import Path

import pytest

from tolstack.intake import extract_zip, find_inputs, sync_inbox
from tolstack.stack import Contributor
from tolstack.tolerances import _page_note, find_dimension, find_general_note


def _drawing(path: Path, placed: list[tuple[tuple[float, float], str]]) -> Path:
    """A one-page PDF with each text at its (x, y) - ASCII and ± only: the built-in font has no CJK."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    for point, words in placed:
        page.insert_text(point, words, fontsize=9)
    doc.save(str(path))
    return path


# ---------------------------------------------------------------- intake: zip, folder, uploads


def _bundle(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_zip_keeps_step_and_pdf_inside_the_target(tmp_path):
    names = ["asm/top.step", "asm/drawings/A.PDF", "../escape.pdf", "/abs/root.stp", "C:\\win\\drive.pdf",
             "notes.txt", "__MACOSX/asm/._top.step"]
    archive = _bundle(tmp_path / "bundle.zip", {name: b"0123456789" for name in names})
    target = tmp_path / "out"
    written = extract_zip(archive, target)
    assert sorted(p.relative_to(target.resolve()).as_posix() for p in written) == [
        "abs/root.stp", "asm/drawings/A.PDF", "asm/top.step", "escape.pdf", "win/drive.pdf"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle.zip", "out"]       # nothing left the target


def test_zip_that_is_broken_or_too_big_is_refused(tmp_path):
    not_a_zip = tmp_path / "plain.zip"
    not_a_zip.write_text("not a zip")
    with pytest.raises(ValueError, match="not a readable zip"):
        extract_zip(not_a_zip, tmp_path / "a")
    archive = _bundle(tmp_path / "big.zip", {"part.step": b"0123456789"})
    with pytest.raises(ValueError, match="unpacks to more than"):
        extract_zip(archive, tmp_path / "b", max_bytes=3)


def test_find_inputs_searches_sub_folders_largest_step_first(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "2D" / "rev B").mkdir(parents=True)
    (tmp_path / "parts" / "pin.stp").write_bytes(b"x" * 10)
    (tmp_path / "ASSEMBLY.STEP").write_bytes(b"x" * 500)
    (tmp_path / "2D" / "rev B" / "PIN.pdf").write_bytes(b"x")
    (tmp_path / "readme.txt").write_bytes(b"x")
    steps, pdfs = find_inputs(tmp_path)
    assert [Path(p).name for p in steps] == ["ASSEMBLY.STEP", "pin.stp"]
    assert [Path(p).name for p in pdfs] == ["PIN.pdf"]
    assert find_inputs(tmp_path / "missing") == ([], [])


def test_sync_inbox_stores_uploads_once_and_unpacks_zips(tmp_path):
    inbox = tmp_path / "inbox"
    reads = []

    def upload(name: str, data: bytes):
        def read() -> bytes:
            reads.append(name)
            return data
        return name, read

    packed = _bundle(tmp_path / "set.zip", {"drawings/SPACER.pdf": b"pdf", "asm.step": b"step"}).read_bytes()
    assert sync_inbox(inbox, {"id-1": upload("old.step", b"old"), "id-2": upload("set.zip", packed)}) == []
    steps, pdfs = find_inputs(inbox)
    assert sorted(Path(p).name for p in steps) == ["asm.step", "old.step"]
    assert [Path(p).name for p in pdfs] == ["SPACER.pdf"]
    assert not list(inbox.rglob("*.zip"))                       # the archive itself is not kept

    kept = next(p for p in inbox.rglob("asm.step"))
    stamp = kept.stat().st_mtime_ns
    reads.clear()
    problems = sync_inbox(inbox, {"id-2": upload("set.zip", packed), "id-3": upload("broken.zip", b"nope")})
    assert reads == ["broken.zip"]                              # the upload that is already there is not read again
    assert kept.stat().st_mtime_ns == stamp                     # ... so the STEP is not parsed again either
    assert len(problems) == 1 and problems[0].startswith("broken.zip: ")
    assert sorted(p.name for p in inbox.iterdir()) == ["id-2"]  # id-1 was removed by the user, id-3 never stored


# ---------------------------------------------------------------- general-tolerance note on the drawing


def test_general_note_demo_drawing_is_a_size_table(tmp_path):
    from tolstack.demo import write_drawing

    note = find_general_note(write_drawing(tmp_path / "SPACER.pdf", "SPACER", ["10.00±0.08", "30.00"]))
    assert note.kind == "size table"
    assert note.table == [(0.0, 10.0, 0.05), (10.0, 50.0, 0.10), (50.0, 1e9, 0.15)]
    assert [note.tolerance(size) for size in (5, 10, 30, 90.5)] == [0.05, 0.05, 0.10, 0.15]
    assert note.label() == "size table: 0-10 ±0.05, 10-50 ±0.1, >50 ±0.15  (SPACER.pdf p1)"


def test_general_note_table_written_cell_by_cell(tmp_path):
    pdf = _drawing(tmp_path / "PLATE.pdf", [
        ((500, 400), "GENERAL TOLERANCES"),
        ((500, 415), "0-6"), ((560, 415), "±0.05"),
        ((500, 430), "6-30"), ((560, 430), "±0.10"),
        ((500, 445), "OVER 30 UP TO 120 +/-0.15"),
        ((500, 460), "ANGLES ±0.5°"),
        ((100, 100), "2-5.5 ±0.05"),                # "2 holes of 5.5", far from the note: a dimension
        ((300, 200), "30.00"),
    ])
    note = find_general_note(pdf)
    assert note.kind == "size table"
    assert note.table == [(0.0, 6.0, 0.05), (6.0, 30.0, 0.10), (30.0, 120.0, 0.15)]      # no angle, no hole callout
    assert note.tolerance(300) is None                                                  # not covered: caller falls back
    assert [hit.text for hit in find_dimension(pdf, 30)] == ["30.00"]                   # the row "6-30" is not a dimension
    assert [(hit.value, hit.tol_plus) for hit in find_dimension(pdf, 5.5)] == [(5.5, 0.05)]


@pytest.mark.parametrize("words, kind, expected", [
    ("GENERAL TOLERANCE: ISO 2768-mK", "ISO 2768-m", [None, 0.1, 0.1, 0.2, 0.3, 0.5]),
    ("ISO 2768-f", "ISO 2768-f", [None, 0.05, 0.05, 0.1, 0.15, 0.2]),
    ("JIS B 0405 medium", "JIS B 0405-m", [None, 0.1, 0.1, 0.2, 0.3, 0.5]),
    ("GB/T 1804-c", "GB/T 1804-c", [None, 0.2, 0.3, 0.5, 0.8, 1.2]),
])
def test_general_note_standard_class(tmp_path, words, kind, expected):
    note = find_general_note(_drawing(tmp_path / "PART.pdf", [((500, 500), words), ((300, 200), "30.00")]))
    assert note.kind == kind
    assert [note.tolerance(size) for size in (0.3, 2, 6, 20, 100, 300)] == expected     # below 0.5 mm: not covered
    assert note.label() == f"{kind}  (PART.pdf p1)"


def test_general_note_decimal_places_needs_the_written_dimension(tmp_path):
    note = find_general_note(_drawing(tmp_path / "PART.pdf", [
        ((500, 400), "UNLESS OTHERWISE SPECIFIED TOLERANCES:"),
        ((500, 415), "X.X ±0.1"),
        ((500, 430), "X.XX ±0.05"),
    ]))
    assert (note.kind, note.decimals) == ("decimal places", {1: 0.1, 2: 0.05})
    assert note.tolerance(30, "30.00") == 0.05
    assert note.tolerance(30, "30.0") == 0.1
    assert note.tolerance(30) is None               # the drawing does not show the dimension: no rule to apply
    assert note.tolerance(30, "30.000") is None     # three places are not in the note


def test_general_note_single_value_and_no_note(tmp_path):
    single = find_general_note(_drawing(tmp_path / "A.pdf", [((500, 400), "UNLESS OTHERWISE SPECIFIED ±0.1")]))
    assert (single.kind, single.tolerance(0.2), single.tolerance(900)) == ("single value", 0.1, 0.1)
    assert single.label() == "single value: ±0.1  (A.pdf p1)"
    # one size row under a heading that is no tolerance note is a dimension, not a table
    lonely = _drawing(tmp_path / "B.pdf", [((500, 400), "SURFACE FINISH NOTES"), ((500, 415), "2-5.5 ±0.05")])
    assert find_general_note(lonely) is None
    assert find_general_note(_drawing(tmp_path / "C.pdf", [((300, 200), "30.00±0.1")])) is None


def test_general_note_in_chinese():
    table = _page_note([
        ("未注公差", (500, 400, 560, 410)),
        ("0～6 ±0.05", (500, 415, 560, 425)),
        ("＞6～30 ±0.1", (500, 430, 560, 440)),
        ("大于30 ±0.2", (500, 445, 560, 455)),
    ])
    assert table.table == [(0.0, 6.0, 0.05), (6.0, 30.0, 0.1), (30.0, 1e9, 0.2)]
    assert table.rows == {1, 2, 3}
    assert _page_note([("未注公差按 GB/T 1804-m", (500, 400, 600, 410))]).standard == "GB/T 1804-m"


def test_apply_tolerances_follows_the_note_of_each_drawing(tmp_path):
    from tolstack.pipeline import apply_tolerances

    pdf = _drawing(tmp_path / "SPACER.pdf", [((500, 500), "ISO 2768-m"), ((300, 200), "30.00")])

    def rows():
        return [Contributor("A", "SPACER", "", 30.0, 0, 0, 1), Contributor("B", "SPACER", "", 0.3, 0, 0, 1),
                Contributor("C", "PCB", "", 1.6, 0, 0, 1), Contributor("X", "SPACER", "", 0.0, 0.1, 0.1, 1)]

    found: dict = {}
    a, b, c, x = apply_tolerances(rows(), [str(pdf)], general_out=found)
    assert (a.tol_plus, a.tol_minus) == (0.2, 0.2)                                  # app table would give 0.10
    assert a.source == "2D SPACER.pdf: 30.00 (no tol) -> general tol from SPACER.pdf (ISO 2768-m) ±0.2"
    assert b.tol_plus == 0.05                                                       # 0.3 mm is below ISO 2768
    assert b.source.endswith("general tol ±0.05 (app table: drawing note does not cover this size)")
    assert c.source == "no 2D -> general tol ±0.05"                                 # no drawing: app table
    assert (x.tol_plus, x.tol_minus) == (0.1, 0.1)                                  # set by the user: untouched
    assert list(found) == ["SPACER"] and found["SPACER"].kind == "ISO 2768-m"

    found = {}
    a, b, _, _ = apply_tolerances(rows(), [str(pdf)], follow_drawing=False, general_out=found)
    assert a.source == "2D SPACER.pdf: 30.00 (no tol) -> general tol ±0.1"
    assert (a.tol_plus, b.tol_plus, found) == (0.1, 0.05, {})


def test_decimal_places_rule_applies_to_a_dimension_found_on_the_drawing(tmp_path):
    from tolstack.pipeline import apply_tolerances

    pdf = _drawing(tmp_path / "SPACER.pdf", [
        ((500, 400), "TOLERANCES UNLESS OTHERWISE SPECIFIED"),
        ((500, 415), "X.X ±0.2"),
        ((500, 430), "X.XX ±0.02"),
        ((300, 200), "30.00"),
    ])
    shown, hidden = apply_tolerances([Contributor("A", "SPACER", "", 30.0, 0, 0, 1),
                                      Contributor("B", "SPACER", "", 12.0, 0, 0, 1)], [str(pdf)])
    assert shown.tol_plus == 0.02 and "(decimal places) ±0.02" in shown.source
    assert hidden.tol_plus == 0.10 and "app table" in hidden.source                 # 12 is not on the drawing


def test_expand_drawings_recursive_without_duplicates(tmp_path):
    from tolstack.pipeline import expand_drawings

    (tmp_path / "sub").mkdir()
    (tmp_path / "A.pdf").write_bytes(b"x")
    (tmp_path / "sub" / "B.pdf").write_bytes(b"x")
    found = expand_drawings(["*.pdf", "**/*.pdf"], tmp_path)
    assert sorted(Path(p).name for p in found) == ["A.pdf", "B.pdf"]


def test_run_config_reports_the_general_note_and_keeps_the_demo_result(tmp_path):
    pytest.importorskip("OCP")
    import yaml
    from openpyxl import load_workbook

    from tolstack.demo import DEMO_CONFIG, write_demo
    from tolstack.pipeline import run_config

    write_demo(tmp_path)
    notes: list[str] = []
    rows, result, out = run_config(yaml.safe_load(DEMO_CONFIG), tmp_path, notes)
    assert result.nominal == pytest.approx(1.6)
    assert "general tolerance on the drawing: SPACER: size table: 0-10 ±0.05, 10-50 ±0.1, >50 ±0.15  (SPACER.pdf p1)" in notes
    base = next(row for row in rows if row.part == "BASE")
    assert base.source.endswith("general tol from BASE.pdf (size table) ±0.05")

    off = dict(yaml.safe_load(DEMO_CONFIG), general_from_drawing=False, output="out/off.xlsx")
    notes_off: list[str] = []
    rows_off, result_off, _ = run_config(off, tmp_path, notes_off)
    assert not [line for line in notes_off if line.startswith("general tolerance")]
    assert [(r.tol_plus, r.tol_minus) for r in rows_off] == [(r.tol_plus, r.tol_minus) for r in rows]   # same numbers
    assert result_off.wc_max == pytest.approx(result.wc_max)

    cells = [str(cell.value) for sheet in load_workbook(out) for row in sheet.iter_rows() for cell in row if cell.value]
    assert "General tolerance on the drawing" in cells


# ---------------------------------------------------------------- 3-D view


@pytest.fixture(scope="module")
def demo_model(tmp_path_factory):
    pytest.importorskip("OCP")
    from tolstack.demo import write_demo
    from tolstack.pipeline import load_model

    return load_model(str(write_demo(tmp_path_factory.mktemp("view"))))


def test_meshes_of_boxes_parts_and_faces(demo_model):
    import numpy as np

    from tolstack.pipeline import find_face, find_part
    from tolstack.view3d import box_mesh, face_mesh, part_mesh

    post = find_part(demo_model, "POST")
    vertices, triangles = box_mesh(post.bbox)
    assert vertices.shape == (8, 3) and triangles.shape == (12, 3)
    assert np.allclose(vertices.min(axis=0), post.bbox[:3]) and np.allclose(vertices.max(axis=0), post.bbox[3:])

    vertices, triangles = part_mesh(post)
    assert len(triangles) >= 12 and triangles.max() < len(vertices)
    assert np.allclose(vertices.min(axis=0), post.bbox[:3], atol=1e-3)
    assert np.allclose(vertices.max(axis=0), post.bbox[3:], atol=1e-3)
    assert part_mesh(post) is post.mesh                         # meshed once per loaded model

    top = find_face(demo_model, "POST", "top")
    bottom = find_face(demo_model, "POST", "bottom")
    assert np.allclose(face_mesh(top)[0][:, 2], 19.0)
    assert np.allclose(face_mesh(top, 0.5)[0][:, 2], 19.5)      # lifted along the outward normal ...
    assert np.allclose(face_mesh(bottom, 0.5)[0][:, 2], 2.5)    # ... which points down for a bottom face


def test_scene_figure_highlights_the_picked_parts_and_faces(demo_model):
    pytest.importorskip("plotly")
    from tolstack.pipeline import find_face, find_part
    from tolstack.view3d import VIEWS, scene_figure

    start = find_face(demo_model, "POST", "top")
    end = find_face(demo_model, "CAP_TOP", "top")
    chain = [find_part(demo_model, name).index for name in ("POST", "BASE", "SPACER", "PCB", "CAP_TOP")]

    figure, boxed = scene_figure(demo_model.parts, start, end, chain)
    names = [trace.name for trace in figure.data if trace.name]
    assert names == ["From part: POST", "To part: CAP_TOP", "chain: BASE", "chain: SPACER", "chain: PCB",
                     "From face: top @ 19.000", "To face: top @ 20.600", "To - From = +1.600 mm"]
    assert boxed == 0

    picked_only, _ = scene_figure(demo_model.parts, start, end, chain, show=VIEWS[1])
    assert not [trace.name for trace in picked_only.data if (trace.name or "").startswith(("chain", "other"))]

    whole, boxed = scene_figure(demo_model.parts, start, end, [], show=VIEWS[2], max_triangles=1)
    assert "other parts (3)" in [trace.name for trace in whole.data]
    assert boxed == 3                                           # out of budget: far parts become boxes
    assert len(whole.data[0].i) >= 12                           # ... the picked parts never do

    everything, boxed = scene_figure(demo_model.parts, start, end, chain[1:3], show=VIEWS[2])
    names = [trace.name for trace in everything.data if trace.name]
    assert names[:5] == ["From part: POST", "To part: CAP_TOP", "chain: BASE", "chain: SPACER", "other parts (1)"]
    assert boxed == 0                                           # inside the budget every part keeps its real shape
    others = everything.data[4]
    assert len(others.i) == len(find_part(demo_model, "PCB").mesh[1]) and max(others.i) < len(others.x)

    same_part, _ = scene_figure(demo_model.parts, find_face(demo_model, "POST", "bottom"), start)
    assert same_part.data[0].name == "From + To part: POST"
