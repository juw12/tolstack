import math
from pathlib import Path

import pytest

from tolstack.stack import Contributor, analyze
from tolstack.tolerances import _parse_line, general_tolerance


def c(nom, tp, tm, d):
    return Contributor("A", "p", "", nom, tp, tm, d)


def test_worst_case_and_rss():
    r = analyze([c(10, 0.1, 0.1, 1), c(4, 0.05, 0.05, -1), c(2, 0.0, 0.05, 1)], mc_samples=0)
    assert r.nominal == pytest.approx(8.0)
    assert r.mean == pytest.approx(8.0 - 0.025)
    assert r.wc_max - r.mean == pytest.approx(0.1 + 0.05 + 0.025)
    assert r.rss == pytest.approx(math.sqrt(0.1 ** 2 + 0.05 ** 2 + 0.025 ** 2))
    assert sum(r.contributions) == pytest.approx(100)


def test_monte_carlo_close_to_rss():
    r = analyze([c(10, 0.3, 0.3, 1), c(5, 0.3, 0.3, -1)], lsl=4.0, usl=6.0, mc_samples=200_000)
    assert r.mc_std == pytest.approx(r.rss / 3, rel=0.02)
    assert r.verdict() == "PASS"


def test_general_table():
    assert general_tolerance(5) == 0.05
    assert general_tolerance(20) == 0.10
    assert general_tolerance(90.5) == 0.15


def test_parse_dimension_text():
    assert _parse_line("62.73±0.1")[0][:3] == (62.73, 0.1, 0.1)
    assert _parse_line("4 +0/-0.05")[0][:3] == (4.0, 0.0, 0.05)
    assert _parse_line("20.14")[0][:3] == (20.14, None, None)
    assert _parse_line("TOLERANCES UNLESS SPECIFIED: 0~10 ±0.05  >10~50 ±0.10") == []


# ---------------------------------------------------------------- round 1: math, parsing, chains


def test_distributions_change_sigma_and_rss():
    normal = analyze([Contributor("A", "p", "", 10, 0.3, 0.3, 1)], mc_samples=0)
    uniform = analyze([Contributor("A", "p", "", 10, 0.3, 0.3, 1, dist="uniform")], mc_samples=0)
    assert normal.sigma == pytest.approx(0.1)
    assert uniform.sigma == pytest.approx(0.3 / math.sqrt(3))
    assert uniform.rss == pytest.approx(3 * 0.3 / math.sqrt(3))
    with pytest.raises(ValueError):
        analyze([Contributor("A", "p", "", 10, 0.3, 0.3, 1, dist="weibull")], mc_samples=0)


def test_cp_cpk_ppm_agree_with_monte_carlo():
    parts = [Contributor("A", "p", "", 10, 0.3, 0.3, 1), Contributor("B", "q", "", 9.2, 0.2, 0.2, -1)]
    r = analyze(parts, lsl=0.5, usl=1.2, mc_samples=400_000)
    sigma = math.sqrt(0.1 ** 2 + (0.2 / 3) ** 2)
    assert r.sigma == pytest.approx(sigma)
    assert r.cp == pytest.approx(0.7 / (6 * sigma))
    assert r.cpk == pytest.approx(min(1.2 - 0.8, 0.8 - 0.5) / (3 * sigma))
    assert r.mc_out_pct == pytest.approx(r.ppm / 1e4, rel=0.08)


def test_uniform_monte_carlo_matches_uniform_sigma():
    r = analyze([Contributor("A", "p", "", 5, 0.2, 0.2, 1, dist="uniform")], mc_samples=200_000)
    assert r.mc_std == pytest.approx(0.2 / math.sqrt(3), rel=0.02)


def test_input_validation():
    with pytest.raises(ValueError):
        analyze([Contributor("A", "p", "", 1, 0.1, 0.1, 2)], mc_samples=0)
    with pytest.raises(ValueError):
        analyze([Contributor("A", "p", "", 1, 0.1, 0.1, 1)], lsl=2, usl=1, mc_samples=0)


def test_limit_dimensions_and_relative_tolerance():
    from tolstack.tolerances import DimensionHit

    value, tp, tm, _, kind = _parse_line("12.10/12.00")[0]
    assert (value, kind) == (12.0, "limit")
    assert (tp, tm) == pytest.approx((0.10, 0.0))
    assert _parse_line("5.95~6.05")[0][4] == "limit"
    assert _parse_line("2024.10/1999.05") == [] or _parse_line("2024.10/1999.05")[0][4] != "limit"
    hit = DimensionHit("x.pdf", 0, "10.00±0.08", 10.0, 0.08, 0.08, (0, 0, 1, 1), "symmetric")
    plus, minus = hit.relative_to(9.98)          # 3D modelled at 9.98: limits 9.92..10.08 kept
    assert (plus, minus) == pytest.approx((0.10, 0.06))


def test_pdf_without_text_layer_is_flagged(tmp_path):
    import fitz

    from tolstack.pipeline import apply_tolerances
    from tolstack.tolerances import pdf_has_text

    doc = fitz.open()
    page = doc.new_page()
    page.draw_line((50, 50), (300, 50))          # strokes only, like a CAD export with vector text
    pdf = tmp_path / "PLATE.pdf"
    doc.save(str(pdf))
    assert pdf_has_text(pdf) is False
    rows = apply_tolerances([Contributor("A", "PLATE", "", 3.0, 0, 0, 1)], [str(pdf)])
    assert "no text layer" in rows[0].source and rows[0].tol_plus == 0.05


def test_candidates_and_choice(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
    import make_demo

    from tolstack.pipeline import apply_tolerances

    pdf = make_demo.write_drawing(tmp_path / "SPACER.pdf", "SPACER", ["10.00±0.08", "10.0 +0.1/-0", "10.00"])
    found = {}
    first = apply_tolerances([Contributor("A", "SPACER", "", 10.0, 0, 0, 1)], [str(pdf)], candidates_out=found)
    assert len(found["A"]) == 3 and "[1/3 candidates]" in first[0].source
    second = apply_tolerances([Contributor("A", "SPACER", "", 10.0, 0, 0, 1)], [str(pdf)], choices={"A": 1})
    assert (second[0].tol_plus, second[0].tol_minus) == (0.1, 0.0)
    unmapped = apply_tolerances([Contributor("A", "SPACER", "", 10.0, 0, 0, 1)], [str(pdf)],
                                drawing_map={"SPACER": ""})
    assert unmapped[0].source.startswith("no 2D")


def test_config_validation():
    from tolstack.pipeline import validate_config

    with pytest.raises(ValueError, match="unknown config keys"):
        validate_config({"step": "a.step", "from": {}, "to": {}, "tolerance": 1})
    with pytest.raises(ValueError, match="version"):
        validate_config({"version": 2, "step": "a.step", "from": {}, "to": {}})
    assert validate_config({"version": 1, "step": "a.step", "from": {}, "to": {}, "spec": {"usl": 1}}) == []


ocp = pytest.importorskip("OCP")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
    import make_demo

    folder = tmp_path_factory.mktemp("demo")
    step = make_demo.write_step(folder / "demo.step", make_demo.demo_parts())
    (folder / "drawings").mkdir()
    make_demo.write_drawing(folder / "drawings" / "SPACER.pdf", "SPACER", ["10.00±0.08"])
    make_demo.write_drawing(folder / "drawings" / "CAP_TOP.pdf", "CAP", ["4 +0/-0.05"])
    return folder, step


def test_chain_from_step(demo):
    from tolstack.chain import build_chain
    from tolstack.pipeline import find_face, load_model

    folder, step = demo
    model = load_model(str(step), "z")
    assert [p.name for p in model.parts] == ["BASE", "SPACER", "PCB", "CAP_TOP", "POST"]
    start = find_face(model, "POST", "top")
    end = find_face(model, "CAP_TOP", "top")
    chain = build_chain(start, end, model.parts, model.contacts)
    assert [(x.part, x.nominal, x.direction) for x in chain] == [
        ("POST", 16.0, -1), ("BASE", 2.0, 1), ("SPACER", 10.0, 1), ("PCB", 1.6, 1), ("CAP_TOP", 4.0, 1)]
    assert sum(x.direction * x.nominal for x in chain) == pytest.approx(end.level - start.level)


def test_full_run_writes_excel(demo):
    import openpyxl

    from tolstack.pipeline import run_config

    folder, step = demo
    config = {
        "step": str(step), "axis": "z",
        "from": {"part": "POST", "face": "top"}, "to": {"part": "CAP_TOP", "face": "top"},
        "drawings": [str(folder / "drawings" / "*.pdf")],
        "spec": {"lsl": 1.2, "usl": 2.0}, "section": {"cut_axis": "y"},
        "output": str(folder / "out" / "ta.xlsx"),
    }
    contributors, result, out = run_config(config, folder)
    by_part = {x.part: x for x in contributors}
    assert (by_part["SPACER"].tol_plus, by_part["SPACER"].tol_minus) == (0.08, 0.08)
    assert (by_part["CAP_TOP"].tol_plus, by_part["CAP_TOP"].tol_minus) == (0.0, 0.05)
    assert by_part["POST"].tol_plus == 0.10          # general table, no drawing
    assert result.nominal == pytest.approx(1.6)
    wb = openpyxl.load_workbook(out)
    assert "Stack-up" in wb.sheetnames and "2D references" in wb.sheetnames
    assert Path(out).with_name("ta_section.png").exists()


def test_true_outline_rejects_bbox_only_overlap(tmp_path):
    """A post standing in the hole of a ring: boxes overlap, material does not touch."""
    import sys

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
    import make_demo

    from tolstack.pipeline import load_model

    ring = BRepAlgoAPI_Cut(make_demo.box(0, 0, 0, 40, 40, 5), make_demo.box(10, 10, -1, 30, 30, 6)).Shape()
    parts = [("RING", ring), ("POST", make_demo.box(15, 15, 5, 25, 25, 12)),
             ("LID", make_demo.box(0, 0, 5, 40, 40, 7))]
    step = make_demo.write_step(tmp_path / "ring.step", parts)
    model = load_model(str(step), "z")
    pairs = {(model.parts[c.lower].name, model.parts[c.upper].name) for c in model.contacts}
    assert ("RING", "LID") in pairs
    assert ("RING", "POST") not in pairs           # bbox-only overlap must not count
    ring_lid = [c for c in model.contacts if model.parts[c.lower].name == "RING"][0]
    assert ring_lid.overlap == pytest.approx(40 * 40 - 20 * 20, rel=1e-3)


def test_parallel_paths_are_listed_and_selectable(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
    import make_demo

    from tolstack.chain import build_chain, find_paths
    from tolstack.pipeline import find_face, load_model

    parts = [("BASE", make_demo.box(0, 0, 0, 60, 40, 5)),
             ("SPACER_BIG", make_demo.box(2, 2, 5, 30, 38, 15)),
             ("SPACER_SMALL", make_demo.box(40, 10, 5, 50, 30, 15.0)),
             ("COVER", make_demo.box(0, 0, 15, 60, 40, 17))]
    step = make_demo.write_step(tmp_path / "par.step", parts)
    model = load_model(str(step), "z")
    start = find_face(model, "BASE", "bottom")
    end = find_face(model, "COVER", "top")
    options = find_paths(start.part, end.part, model.contacts)
    names = [[model.parts[s[1]].name for s in o.steps] for o in options]
    assert names[0] == ["SPACER_BIG", "COVER"]     # strongest (largest overlap) path first
    assert ["SPACER_SMALL", "COVER"] in names
    alt = build_chain(start, end, model.parts, model.contacts, steps=options[1].steps)
    assert sum(c.direction * c.nominal for c in alt) == pytest.approx(17.0)


def test_contact_gap_within_tolerance_keeps_closure(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
    import make_demo

    from tolstack.chain import build_chain
    from tolstack.pipeline import find_face, load_model

    parts = [("BASE", make_demo.box(0, 0, 0, 20, 20, 5)),
             ("TOP", make_demo.box(0, 0, 5.01, 20, 20, 8))]      # 0.01 mm modelling gap
    step = make_demo.write_step(tmp_path / "gap.step", parts)
    model = load_model(str(step), "z", contact_tol=0.02)
    start = find_face(model, "BASE", "bottom")
    end = find_face(model, "TOP", "top")
    chain = build_chain(start, end, model.parts, model.contacts)
    assert any("contact gap" in c.source for c in chain)
    assert sum(c.direction * c.nominal for c in chain) == pytest.approx(8.0)



def test_excel_report_has_review_assumptions_and_capability(demo):
    import openpyxl

    from tolstack.pipeline import run_config

    folder, step = demo
    config = {
        "version": 1, "step": str(step), "axis": "z",
        "from": {"part": "POST", "face": "top"}, "to": {"part": "CAP_TOP", "face": "top"},
        "spec": {"lsl": 1.2, "usl": 2.0}, "output": str(folder / "out" / "cap.xlsx"),
        "overrides": {"A": {"dist": "uniform"}},
    }
    _, result, out = run_config(config, folder)
    wb = openpyxl.load_workbook(out)
    ws = wb["Stack-up"]
    labels = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert labels["Cpk"].startswith("=") and labels["RSS ±"].startswith("=")
    assert "Approved by" in labels and "Assumptions" in wb.sheetnames
    assert ws.cell(14, 9).value == "uniform"
    assert result.cpk == pytest.approx(min(2.0 - result.mean, result.mean - 1.2) / (3 * result.sigma))



def test_stepped_seat_gives_one_option_per_contact_level(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    from tolstack.chain import build_chain, find_paths
    from tolstack.demo import box, write_step
    from tolstack.pipeline import find_face, load_model

    base = BRepAlgoAPI_Cut(box(0, 0, 0, 40, 20, 7), box(-1, -1, 5, 20, 21, 8)).Shape()     # tops at 5 and 7
    top = BRepAlgoAPI_Cut(box(0, 0, 5, 40, 20, 10), box(20, -1, 4, 41, 21, 7)).Shape()     # bottoms at 5 and 7
    step = write_step(tmp_path / "stepped.step", [("BASE", base), ("TOP", top)])
    model = load_model(str(step), "z")
    start = find_face(model, "BASE", "bottom")
    end = find_face(model, "TOP", "top")
    options = find_paths(start.part, end.part, model.contacts)
    levels = sorted(round(o.steps[0][2].level, 3) for o in options)
    assert levels == [5.0, 7.0]
    for option in options:
        chain = build_chain(start, end, model.parts, model.contacts, steps=option.steps)
        assert sum(c.direction * c.nominal for c in chain) == pytest.approx(10.0)
