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
