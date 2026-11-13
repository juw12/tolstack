"""v0.3.0: STEP units, portable configs, sensitivity, GD&T, more dimension formats, OCR path, work folders."""
import math
import time
from pathlib import Path

import pytest

pytest.importorskip("OCP")
fitz = pytest.importorskip("fitz")

from tolstack.cad import step_length_units, unit_note  # noqa: E402
from tolstack.demo import box, write_demo, write_drawing, write_step  # noqa: E402
from tolstack.pipeline import apply_tolerances, gdt_contributor, load_model, portable_paths, resolve_drawing_map, run_config  # noqa: E402
from tolstack.stack import Contributor, analyze, is_extra, sensitivity  # noqa: E402
from tolstack.tolerances import GdtHit, _parse_line, _span_text, find_dimension, find_gdt, parse_gdt, translate_gdt_font  # noqa: E402
from tolstack.workspace import clean_old, session_dir  # noqa: E402

MM = "#10=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));\n#11=(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.));\n"


# ---------------------------------------------------------------- units


@pytest.mark.parametrize("text, expected", [
    (MM, ("mm",)),
    (MM + "#12=(CONVERSION_BASED_UNIT('INCH',#13)LENGTH_UNIT()NAMED_UNIT(#14));\n", ("inch",)),
    ("#10=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));\n", ("m",)),
    ("#10=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT( .CENTI. , .METRE. ) );\n", ("cm",)),
    (MM + "#12=(CONVERSION_BASED_UNIT('DEGREE',#13)NAMED_UNIT(#14)PLANE_ANGLE_UNIT());\n", ("mm",)),
    ("ISO-10303-21;\nDATA;\nENDSEC;\n", ()),
])
def test_step_length_units_from_file_text(tmp_path, text, expected):
    path = tmp_path / "units.step"
    path.write_text(text)
    assert step_length_units(path) == expected


def test_unit_note_only_speaks_when_not_plain_mm():
    assert unit_note(("mm",)) is None
    assert "inch, not mm" in unit_note(("inch",))
    assert "does not declare" in unit_note(())
    assert "several length units" in unit_note(("m", "mm"))


def test_metre_step_is_converted_to_mm_and_flagged(tmp_path):
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Controller

    parts = [("BASE", box(0, 0, 0, 60, 40, 5)), ("TOP", box(0, 0, 5, 60, 40, 8))]
    STEPControl_Controller.Init_s()                      # the unit setting does not exist before this
    assert Interface_Static.SetCVal_s("write.step.unit", "M")
    try:
        step = write_step(tmp_path / "metre.step", parts)
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    model = load_model(str(step), "z")
    assert model.units == ("m",)
    assert "not mm" in model.unit_note
    assert max(f.level for f in model.faces) == pytest.approx(8.0)       # geometry arrives in mm
    assert load_model(str(write_step(tmp_path / "mm.step", parts)), "z").unit_note is None


# ---------------------------------------------------------------- portable configs


def test_portable_config_runs_from_its_own_folder(tmp_path):
    write_demo(tmp_path)
    drawings = sorted(str(p) for p in (tmp_path / "drawings").glob("*.pdf"))
    spacer = str(tmp_path / "drawings" / "SPACER.pdf")
    paths = portable_paths(r"C:\Temp\tolstack_app\sessions\abc\demo_assembly.step", {"SPACER": spacer, "BASE": ""},
                           drawings)
    assert paths == {"step": "demo_assembly.step", "drawings": ["drawings/*.pdf"],
                     "drawing_map": {"SPACER": "drawings/SPACER.pdf", "BASE": ""}}
    config = {"version": 1, **paths, "from": {"part": "POST", "face": "top"}, "to": {"part": "CAP_TOP", "face": "top"},
              "spec": {"lsl": 1.2, "usl": 2.0}, "output": "out/portable.xlsx"}
    contributors, result, out = run_config(config, tmp_path)
    by_part = {c.part: c for c in contributors}
    assert by_part["SPACER"].tol_plus == 0.08 and "SPACER.pdf" in by_part["SPACER"].source
    assert by_part["BASE"].source.startswith("no 2D")
    assert Path(out) == tmp_path / "out" / "portable.xlsx" and Path(out).exists()

    resolved = resolve_drawing_map({**paths["drawing_map"], "PCB": "drawings/MISSING.pdf"}, drawings)
    assert resolved == {"SPACER": spacer, "BASE": ""}                    # unknown file -> automatic match again
    # a config written on Windows must also resolve on a Linux server, and the other way round
    assert resolve_drawing_map({"SPACER": r"C:\Work\drawings\SPACER.pdf"}, drawings) == {"SPACER": spacer}
    assert resolve_drawing_map({"SPACER": "/srv/job/drawings/SPACER.pdf"}, drawings) == {"SPACER": spacer}


# ---------------------------------------------------------------- sensitivity


def test_sensitivity_ranks_rows_and_shows_the_gain():
    rows_in = [Contributor("B", "q", "", 5, 0.1, 0.1, -1), Contributor("A", "p", "", 10, 0.3, 0.3, 1),
               Contributor("C", "r", "", 2, 0.0, 0.2, 1)]
    result = analyze(rows_in, mc_samples=0)
    rows = sensitivity(rows_in, result)
    assert [r["label"] for r in rows][0] == "A" and rows[0]["index"] == 1
    assert sum(r["contribution"] for r in rows) == pytest.approx(100)
    assert sum(r["wc_share"] for r in rows) == pytest.approx(100)
    by = {r["label"]: r for r in rows}
    assert (by["A"]["low"], by["A"]["high"]) == pytest.approx((-0.3, 0.3))
    assert (by["B"]["low"], by["B"]["high"]) == pytest.approx((-0.1, 0.1))
    assert (by["C"]["low"], by["C"]["high"]) == pytest.approx((-0.2, 0.0))  # one-sided tolerance
    assert result.rss == pytest.approx(math.sqrt(0.11))
    assert by["A"]["rss_without"] == pytest.approx(math.sqrt(0.02))
    assert by["B"]["rss_without"] == pytest.approx(math.sqrt(0.10))


def test_excel_has_sensitivity_sheet_with_live_formulas_and_the_unit(tmp_path):
    import openpyxl

    write_demo(tmp_path)
    config = {"version": 1, "step": "demo_assembly.step", "drawings": ["drawings/*.pdf"],
              "from": {"part": "POST", "face": "top"}, "to": {"part": "CAP_TOP", "face": "top"},
              "spec": {"lsl": 1.2, "usl": 2.0}, "output": "out/s.xlsx"}
    contributors, result, out = run_config(config, tmp_path)
    wb = openpyxl.load_workbook(out)
    ws, stack = wb["Sensitivity"], wb["Stack-up"]
    assert [ws.cell(4, col).value for col in (1, 3, 4, 6, 8, 9)] == ["Item", "Low", "High", "Variance %",
                                                                    "RSS ± without", "Gain"]
    top = sensitivity(contributors, result)[0]
    item_ref = ws.cell(5, 1).value                                       # ='Stack-up'!A<row> of the top contributor
    assert stack[item_ref.split("!")[1]].value == top["label"]
    assert ws.cell(5, 3).value.startswith("=MIN(") and ws.cell(5, 8).value.startswith("='Stack-up'!")
    rss_ref = ws.cell(5, 9).value.split("!")[1].split("-")[0].replace("$", "")
    assert stack[f"A{rss_ref[1:]}"].value == "RSS ±"                     # Gain subtracts from the RSS cell
    assert ws.max_row == 4 + len(contributors) and len(ws._charts) == 1
    notes = {row[0].value: row[1].value for row in wb["Assumptions"].iter_rows(min_row=3) if row[0].value}
    assert notes["Length unit of the STEP file"].startswith("mm")


# ---------------------------------------------------------------- GD&T


@pytest.mark.parametrize("text, expected", [
    ("⏥ 0.05", ("flatness", 0.05, False, "", ())),
    ("⌖ ⌀0.1 Ⓜ A B", ("position", 0.1, True, "M", ("A", "B"))),
    ("FLATNESS 0.05", ("flatness", 0.05, False, "", ())),
    ("平面度 0.05", ("flatness", 0.05, False, "", ())),
    ("∥ 0.05 A", ("parallelism", 0.05, False, "", ("A",))),
    ("// 0.1 A", ("parallelism", 0.1, False, "", ("A",))),
    ("Position Ø0.2 (M) A B C", ("position", 0.2, True, "M", ("A", "B", "C"))),
    ("⊥ 0.03 | B", ("perpendicularity", 0.03, False, "", ("B",))),
    ("TOTAL RUNOUT 0.1 A", ("total runout", 0.1, False, "", ("A",))),
    ("PROFILE OF A SURFACE 0.2 A B", ("profile of a surface", 0.2, False, "", ("A", "B"))),
    ("位置度 Φ0.1 A", ("position", 0.1, True, "", ("A",))),
])
def test_parse_gdt_frames(text, expected):
    found = parse_gdt(text)
    assert len(found) == 1 and found[0][:5] == expected


@pytest.mark.parametrize("text", ["REPOSITION 0.1", "10.00±0.08", "SEE NOTE 4", "FLATNESS PER NOTE"])
def test_parse_gdt_ignores_other_text(text):
    assert parse_gdt(text) == []


def test_gdt_symbol_font_is_translated():
    assert translate_gdt_font("c") == "⏥"
    assert parse_gdt(translate_gdt_font("jn0.1mAB"))[0][:5] == ("position", 0.1, True, "M", ("A", "B"))
    assert parse_gdt("⌖ 0.1 MAX")[0][4] == ()             # a word after the frame is not a datum list
    assert _span_text({"text": "c", "font": "SWGDT"}) == "⏥"
    assert _span_text({"text": "cap", "font": "Arial"}) == "cap"


def test_gdt_frame_is_not_read_as_a_dimension():
    assert _parse_line("FLATNESS 0.05") == []
    assert _parse_line("10.00±0.08  FLATNESS 0.05")[0][:3] == (10.0, 0.08, 0.08)


def test_gdt_on_a_drawing_is_reported_and_can_become_a_row(tmp_path):
    pdf = write_drawing(tmp_path / "SPACER.pdf", "SPACER", [
        "10.00±0.08", "FLATNESS 0.05", "PARALLELISM 0.1 A", "POSITION DIA 0.2 (M) A B", "POSITION 0.2 (M) A B", "30.00"])
    hits = find_gdt(pdf)
    assert [(h.kind, h.value, h.along_axis) for h in hits] == [
        ("flatness", 0.05, True), ("parallelism", 0.1, True), ("position", 0.2, False)]
    assert hits[2].datums == ("A", "B") and hits[2].modifier == "M"
    assert "flatness 0.05" in hits[0].label() and "SPACER.pdf p1" in hits[0].label()

    gdt = {}
    rows = apply_tolerances([Contributor("A", "SPACER", "", 10.0, 0, 0, 1), Contributor("B", "SPACER", "", 0.05, 0, 0, 1)],
                            [str(pdf)], gdt_out=gdt)
    assert rows[0].tol_plus == 0.08                                     # the dimension is still found
    assert "not dimensioned" in rows[1].source                           # 0.05 of the flatness frame is no dimension
    assert [h.kind for h in gdt["SPACER"]] == ["flatness", "parallelism", "position"]

    row = gdt_contributor(hits[0], "SPACER", "G1")
    assert (row.nominal, row.tol_plus, row.tol_minus) == (0.0, 0.025, 0.025)
    assert row.source.startswith("GD&T SPACER.pdf p1") and is_extra(row)
    assert not is_extra(rows[0]) and is_extra(Contributor("X", "", "", 0, 0.1, 0.1, 1, source="manual"))
    wider = analyze(rows[:1] + [row], mc_samples=0)
    assert wider.rss == pytest.approx(math.sqrt(0.08 ** 2 + 0.025 ** 2))


def test_gdt_kinds_that_can_act_along_the_axis():
    def hit(kind, diameter=False):
        return GdtHit("a.pdf", 0, kind, 0.1, diameter, "", (), "", (0, 0, 1, 1))

    assert hit("flatness").along_axis and hit("profile of a surface").along_axis
    assert not hit("position").along_axis and not hit("circularity").along_axis
    assert not hit("parallelism", diameter=True).along_axis


# ---------------------------------------------------------------- dimension formats


@pytest.mark.parametrize("text, expected", [
    ("62.73±0.1", (62.73, 0.1, 0.1, "symmetric")),
    ("62.73 +/- 0.1", (62.73, 0.1, 0.1, "symmetric")),
    ("⌀6.00±0.02", (6.0, 0.02, 0.02, "symmetric")),
    ("2X R2.50±0.05", (2.5, 0.05, 0.05, "symmetric")),
    ("4 +0/-0.05", (4.0, 0.0, 0.05, "asymmetric")),
    ("4+0.1-0.05", (4.0, 0.1, 0.05, "asymmetric")),
    ("10.0 +0.1/-0", (10.0, 0.1, 0.0, "asymmetric")),
    ("4 +0.1/0", (4.0, 0.1, 0.0, "asymmetric")),
    ("4.00 +0.10/0", (4.0, 0.1, 0.0, "asymmetric")),
    ("4 0/-0.05", (4.0, 0.0, 0.05, "asymmetric")),
    ("4.00 +0/-0.05", (4.0, 0.0, 0.05, "asymmetric")),
    ("12.10/12.00", (12.0, 0.1, 0.0, "limit")),
    ("12.00~12.10", (12.0, 0.1, 0.0, "limit")),
    ("12.00-12.10", (12.0, 0.1, 0.0, "limit")),
    ("20.14", (20.14, None, None, "plain")),
])
def test_dimension_format_corpus(text, expected):
    value, plus, minus, _, kind = _parse_line(text)[0]
    assert (value, kind) == (expected[0], expected[3])
    assert (plus, minus) == (pytest.approx(expected[1]), pytest.approx(expected[2])) if plus is not None else (
        expected[1] is None and minus is None)


def test_dimension_corpus_through_a_pdf(tmp_path):
    lines = ["10.00±0.08", "4 +0.1/0", "6 0/-0.05", "12.10/12.00", "FLATNESS 0.05", "25.00"]
    pdf = write_drawing(tmp_path / "PART.pdf", "PART", lines)
    expected = {10.0: (0.08, 0.08), 4.0: (0.1, 0.0), 6.0: (0.0, 0.05), 12.05: (0.05, 0.05), 25.0: (0.1, 0.1)}
    rows = [Contributor(str(i), "PART", "", nominal, 0, 0, 1) for i, nominal in enumerate(expected)]
    apply_tolerances(rows, [str(pdf)])
    for row in rows:
        assert (row.tol_plus, row.tol_minus) == pytest.approx(expected[row.nominal]), row.source
    assert "general tol" in rows[-1].source and "general tol" not in rows[0].source


# ---------------------------------------------------------------- OCR path (Tesseract mocked)


def test_ocr_path_reads_drawings_without_a_text_layer(tmp_path, monkeypatch):
    doc = fitz.open()
    doc.new_page().draw_line((50, 50), (300, 50))                        # strokes only: no text layer
    pdf = tmp_path / "SCAN.pdf"
    doc.save(str(pdf))
    calls = []

    def fake_ocr(page, **kwargs):
        calls.append(kwargs)
        page.insert_text((100, 100), "10.00±0.08")
        page.insert_text((100, 130), "FLATNESS 0.05")
        return page.get_textpage()

    monkeypatch.setattr("tolstack.tolerances.tesseract_available", lambda: True)
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    assert find_dimension(pdf, 10.0) == [] and not calls                 # OCR is opt-in
    hits = find_dimension(pdf, 10.0, ocr=True)
    assert (hits[0].tol_plus, hits[0].ocr) == (0.08, True) and calls[0]["dpi"] == 300
    assert [h.kind for h in find_gdt(pdf, ocr=True)] == ["flatness"]
    row = apply_tolerances([Contributor("A", "SCAN", "", 10.0, 0, 0, 1)], [str(pdf)], ocr=True)[0]
    assert row.tol_plus == 0.08 and "SCAN.pdf" in row.source
    plain = apply_tolerances([Contributor("A", "SCAN", "", 10.0, 0, 0, 1)], [str(pdf)])[0]
    assert "no text layer" in plain.source

    monkeypatch.setattr("tolstack.tolerances.tesseract_available", lambda: False)
    assert find_dimension(pdf, 10.0, ocr=True) == []                     # no Tesseract: falls back quietly


# ---------------------------------------------------------------- work folders of the web app


def test_session_folders_are_private_and_old_ones_are_removed(tmp_path):
    mine = session_dir("abc123", tmp_path)
    other = session_dir("../..\\evil", tmp_path)
    assert mine != other and other.parent == tmp_path / "sessions" and other.name == "evil"
    (mine / "customer.step").write_text("x")
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "demo_assembly.step").write_text("x")
    (tmp_path / "left_by_0.2.step").write_text("x")
    assert clean_old(tmp_path) == []                                     # everything is fresh
    removed = clean_old(tmp_path, now=time.time() + 25 * 3600)
    assert sorted(removed) == ["abc123", "evil", "left_by_0.2.step"]
    assert not mine.exists() and (tmp_path / "demo" / "demo_assembly.step").exists()
