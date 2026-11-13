"""v0.8.0: locating fits found in the CAD (`tolstack fits`) and the hand-written stack in the web app."""
from pathlib import Path

import pytest
import yaml

pytest.importorskip("OCP")

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from tolstack.cad import Part  # noqa: E402
from tolstack.demo import box, write_step  # noqa: E402
from tolstack.fits import find_fits, pick_part  # noqa: E402

ROOT = Path(__file__).parents[1]


def part(index, name, shape):
    return Part(index, name, name, (0, 0, 0, 0, 0, 0), shape)


def frame_and_cover():
    """Frame with a 30.10 x 20.30 opening; a 30.00 x 20.00 cover in it, 0.02 off-centre in +X."""
    frame = BRepAlgoAPI_Cut(box(-20, -15, 0, 20, 15, 5), box(-15.05, -10.15, -1, 15.05, 10.15, 6)).Shape()
    cover = box(-14.98, -10.0, 1, 15.02, 10.0, 4)
    return part(0, "COVER", cover), part(1, "FRAME", frame)


def cylinder(radius, z0, height):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), radius, height).Shape()


def test_walls_give_the_float_both_ways():
    cover, frame = frame_and_cover()
    report = find_fits(cover, frame, "z")
    floats = {f.axis: f for f in report.floats}
    assert floats["x"].plus.clearance == pytest.approx(0.03)
    assert floats["x"].minus.clearance == pytest.approx(0.07)
    assert floats["x"].total == pytest.approx(0.10)
    assert floats["y"].total == pytest.approx(0.30)
    assert not report.bores


def test_wall_rows_are_slot_minus_tongue_whichever_part_comes_first():
    cover, frame = frame_and_cover()
    for a, b in ((cover, frame), (frame, cover)):
        rows = find_fits(a, b, "z").rows()
        x_rows = [r for r in rows if "across X" in r["description"]]
        slot = next(r for r in x_rows if r["direction"] == 1)
        tongue = next(r for r in x_rows if r["direction"] == -1)
        assert (slot["part"], slot["nominal"]) == ("FRAME", pytest.approx(30.10))
        assert (tongue["part"], tongue["nominal"]) == ("COVER", pytest.approx(30.00))
        assert slot["diameter"] and tongue["diameter"]


def test_bore_and_shaft_clearance_and_interference():
    housing = part(1, "HOUSING", BRepAlgoAPI_Cut(box(-10, -10, 0, 10, 10, 8), cylinder(5.025, -1, 10)).Shape())
    pin = part(0, "PIN", cylinder(5.0, 2, 10))
    report = find_fits(pin, housing, "z")
    assert len(report.bores) == 1
    fit = report.bores[0]
    assert (fit.bore_part, fit.shaft_part) == ("HOUSING", "PIN")
    assert fit.clearance == pytest.approx(0.025)
    assert fit.overlap == pytest.approx(6.0)
    press = find_fits(part(0, "PIN", cylinder(5.04, 2, 10)), housing, "z")
    assert press.bores[0].clearance == pytest.approx(-0.015)          # interference is reported, below 0


def test_parts_far_apart_have_no_fit():
    report = find_fits(part(0, "A", box(0, 0, 0, 10, 10, 5)), part(1, "B", box(30, 0, 0, 40, 10, 5)), "z")
    assert not report.bores and not report.walls and not report.floats
    assert "no bore / shaft pair" in report.lines()[-1]


def test_fit_rows_run_as_a_hand_written_stack(tmp_path):
    from tolstack.pipeline import run_config

    cover, frame = frame_and_cover()
    rows = [r for r in find_fits(cover, frame, "z").rows() if "across X" in r["description"]]
    rows[0].update(tol_plus=0.05, tol_minus=0.05)
    rows[1].update(tol_plus=0.04, tol_minus=0.04)
    contributors, result, _ = run_config({"version": 1, "rows": rows, "output": "fit.xlsx"}, tmp_path)
    assert result.nominal == pytest.approx(0.05)                     # one-side float (30.10 - 30.00) / 2
    assert result.wc_max - result.nominal == pytest.approx(0.045)     # (0.05 + 0.04) / 2


def test_pick_part_refuses_an_ambiguous_name():
    parts = [part(0, "COVER_TOP", box(0, 0, 0, 1, 1, 1)), part(1, "COVER_BOTTOM", box(0, 0, 0, 1, 1, 1))]
    assert pick_part(parts, "cover_top").name == "COVER_TOP"
    with pytest.raises(ValueError, match="matches 2 parts"):
        pick_part(parts, "COVER")
    with pytest.raises(ValueError, match="no part matches"):
        pick_part(parts, "BRACKET")


def test_cli_fits_on_a_step_prints_rows(tmp_path, capsys):
    from tolstack.cli import main

    cover, frame = frame_and_cover()
    step = write_step(tmp_path / "fit.step", [("COVER", cover.shape), ("FRAME", frame.shape)])
    main(["fits", str(step), "COVER", "FRAME", "--rows"])
    out = capsys.readouterr().out
    assert "total float 0.100" in out and "total float 0.300" in out
    rows = yaml.safe_load(out.split("check each direction against your stack\n", 1)[1])
    assert {r["part"] for r in rows} == {"FRAME", "COVER"}


def _app():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)


def test_app_hand_rows_mode_matches_the_cli():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("Rows by hand (no STEP)").run()
    assert not at.exception
    assert any("Add the rows" in i.value for i in at.info)
    at.button(key="hand_example").click().run()
    at.button(key="hand_run_button").click().run()
    assert not at.exception and not at.error
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Nominal"] == "+0.300"                 # the same numbers as `tolstack run seal_squeeze_config.yaml`
    assert metrics["Worst case"] == "+0.070 ~ +0.530"
    assert metrics["RSS (3σ)"] == "+0.197 ~ +0.403"
    assert any(c.value.startswith("min squeeze: 0.199") for c in at.caption)
    assert len(at.get("download_button")) == 2
