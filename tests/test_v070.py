"""v0.7.0: stacks written by hand (no STEP), diameter rows, measured groups, a fitted limit, pictures."""
import math
from pathlib import Path

import numpy as np
import openpyxl
import pytest
import yaml

from tolstack.measured import Limit, beyond_limit_pct, compare, fit_line, margins, read_groups, read_limit, summary_lines
from tolstack.pipeline import run_config, validate_config
from tolstack.stack import Contributor, analyze

EXAMPLE = Path(__file__).parents[1] / "examples" / "seal_squeeze_config.yaml"

# squeeze = cord - (bore radius - groove radius) + (pilot radius - spigot radius); all numbers are made up
ROWS = [
    {"label": "C", "part": "SEAL", "nominal": 1.50, "tol_plus": 0.05, "tol_minus": 0.05, "direction": 1},
    {"label": "A", "part": "HOUSING", "nominal": 30.00, "tol_plus": 0.10, "tol_minus": 0.10, "direction": -1, "diameter": True},
    {"label": "B", "part": "SHAFT", "nominal": 27.60, "tol_plus": 0.08, "tol_minus": 0.08, "direction": 1, "diameter": True},
    {"label": "D1", "part": "HOUSING", "nominal": 29.00, "direction": 1, "diameter": True},
    {"label": "D2", "part": "SHAFT", "nominal": 29.00, "tol_plus": 0.08, "tol_minus": 0.08, "direction": -1, "diameter": True},
]
MEASURED = [
    {"name": "Leaking units", "result": "leak", "values": [0.14, 0.17, 0.19, 0.16]},
    {"name": "Good units", "result": "pass", "values": [0.27, 0.31, 0.29, 0.33, 0.30]},
]
FIT = {"label": "min squeeze", "side": "lower", "x": [0.10, 0.15, 0.20, 0.25, 0.30], "y": [3.1, 3.9, 5.1, 5.9, 7.1], "y_limit": 5}


def _config(tmp_path, **more):
    return {"rows": [dict(r) for r in ROWS], "mc_samples": 2000, "output": str(tmp_path / "out" / "ta.xlsx"), **more}


def _stack():
    """The five rows of the example after halving, as plain contributors."""
    halves = [("C", 1.5, 0.05, 1), ("A", 15.0, 0.05, -1), ("B", 13.8, 0.04, 1), ("D1", 14.5, 0.05, 1), ("D2", 14.5, 0.04, -1)]
    return analyze([Contributor(label, "", "", nominal, tol, tol, direction) for label, nominal, tol, direction in halves],
                   mc_samples=0)


def test_hand_written_stack_matches_the_hand_calculation(tmp_path):
    contributors, result, out = run_config(_config(tmp_path), tmp_path)
    assert [(c.label, c.nominal, c.tol_plus, c.direction) for c in contributors] == [
        ("C", 1.5, 0.05, 1), ("A", 15.0, 0.05, -1), ("B", 13.8, 0.04, 1), ("D1", 14.5, 0.05, 1), ("D2", 14.5, 0.04, -1)]
    worst = 0.05 + 0.05 + 0.04 + 0.05 + 0.04
    rss = math.sqrt(3 * 0.05 ** 2 + 2 * 0.04 ** 2)
    assert result.nominal == pytest.approx(0.30)
    assert (result.wc_min, result.wc_max) == pytest.approx((0.30 - worst, 0.30 + worst))
    assert (result.rss_min, result.rss_max) == pytest.approx((0.30 - rss, 0.30 + rss))
    assert result.sigma == pytest.approx(rss / 3)
    share_big, share_small = 100 * 0.05 ** 2 / rss ** 2, 100 * 0.04 ** 2 / rss ** 2
    assert result.contributions == pytest.approx([share_big, share_big, share_small, share_big, share_small])
    assert Path(out).exists()


def test_diameter_row_reads_the_general_tolerance_at_the_diameter_then_halves(tmp_path):
    contributors, _, _ = run_config(_config(tmp_path), tmp_path)
    pilot = next(c for c in contributors if c.label == "D1")
    # 29 falls in the 10-50 band of the default table (±0.10); 14.5 would too, so also check a size that changes band
    assert (pilot.nominal, pilot.tol_plus, pilot.tol_minus) == (14.5, 0.05, 0.05)
    assert pilot.source.startswith("no 2D") and "Ø29 on the drawing -> half" in pilot.source
    seal = next(c for c in contributors if c.label == "C")
    assert seal.source == "manual"                                     # a row without `diameter` is left alone

    rows = [{"label": "D", "nominal": 16.0, "diameter": True}, {"label": "L", "nominal": 8.0, "direction": -1}]
    contributors, result, _ = run_config({"rows": rows, "mc_samples": 0, "output": str(tmp_path / "band.xlsx")}, tmp_path)
    # Ø16 is looked up as 16 (±0.10) and halved to ±0.05; looked up at the radius 8 it would have been ±0.05 -> ±0.025
    assert (contributors[0].nominal, contributors[0].tol_plus) == (8.0, 0.05)
    assert (contributors[1].nominal, contributors[1].tol_plus) == (8.0, 0.05)
    assert result.nominal == pytest.approx(0.0)


def test_diameter_row_with_a_2d_drawing(tmp_path):
    from tolstack.demo import write_drawing

    write_drawing(tmp_path / "SPACER.pdf", "SPACER", ["30.00±0.10"])
    rows = [{"label": "A", "part": "SPACER", "nominal": 30.0, "diameter": True}]
    config = {"rows": rows, "drawings": [str(tmp_path / "*.pdf")], "mc_samples": 0, "output": str(tmp_path / "drw.xlsx")}
    contributors, _, _ = run_config(config, tmp_path)
    assert (contributors[0].nominal, contributors[0].tol_plus, contributors[0].tol_minus) == (15.0, 0.05, 0.05)
    assert contributors[0].source.startswith("2D") and "SPACER.pdf" in contributors[0].source


def test_fit_line_and_fitted_limit_match_numpy():
    slope, intercept, r2 = fit_line(FIT["x"], FIT["y"])
    np_slope, np_intercept = np.polyfit(FIT["x"], FIT["y"], 1)
    assert (slope, intercept) == pytest.approx((np_slope, np_intercept))
    assert r2 == pytest.approx(np.corrcoef(FIT["x"], FIT["y"])[0, 1] ** 2)
    limit = read_limit(FIT)
    assert limit.fitted and limit.value == pytest.approx((5 - np_intercept) / np_slope)
    assert limit.value == pytest.approx(0.199)
    typed = read_limit({"value": 0.2})
    assert (typed.value, typed.side, typed.fitted) == (0.2, "lower", False)
    assert read_limit(None) is None


def test_fit_and_limit_errors():
    with pytest.raises(ValueError, match="x has 3 points and y has 2"):
        fit_line([1, 2, 3], [1, 2])
    with pytest.raises(ValueError, match="at least 2 points"):
        fit_line([1], [1])
    with pytest.raises(ValueError, match="all x values are the same"):
        fit_line([1, 1], [1, 2])
    with pytest.raises(ValueError, match="flat"):
        read_limit({"x": [1, 2, 3], "y": [4, 4, 4], "y_limit": 4})
    with pytest.raises(ValueError, match="unknown keys"):
        read_limit({"value": 1, "units": "mm"})
    with pytest.raises(ValueError, match="not both"):
        read_limit({"value": 1, "x": [1, 2], "y": [1, 2]})
    with pytest.raises(ValueError, match="limit side"):
        read_limit({"value": 1, "side": "left"})
    with pytest.raises(ValueError, match="limit needs 'value'"):
        read_limit({"x": [1, 2], "y": [1, 2]})


def test_measured_group_errors():
    with pytest.raises(ValueError, match="unknown keys"):
        read_groups([{"name": "a", "values": [1], "unit": "mm"}])
    with pytest.raises(ValueError, match="needs 'values' or both 'min' and 'max'"):
        read_groups([{"name": "a", "min": 1}])
    with pytest.raises(ValueError, match="min 2.0 is above max 1.0"):
        read_groups([{"name": "a", "min": 2, "max": 1}])
    assert read_groups(None) == []


def test_share_beyond_the_limit_is_the_normal_tail():
    result = _stack()
    lower = Limit(0.25, "lower")
    z = (0.25 - result.mean) / result.sigma
    tail = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    assert beyond_limit_pct(result, lower) == pytest.approx(100 * tail)
    assert beyond_limit_pct(result, Limit(0.25, "upper")) == pytest.approx(100 * (1 - tail))
    assert margins(result, lower) == pytest.approx((result.rss_min - 0.25, result.wc_min - 0.25))
    assert margins(result, Limit(0.45, "upper")) == pytest.approx((0.45 - result.rss_max, 0.45 - result.wc_max))
    no_spread = analyze([Contributor("A", "", "", 1.0, 0, 0, 1)], mc_samples=0)
    assert (beyond_limit_pct(no_spread, Limit(2.0, "lower")), beyond_limit_pct(no_spread, Limit(0.5, "lower"))) == (100.0, 0.0)


def test_compare_measured_groups_against_ranges_and_limit():
    result = _stack()                                                  # RSS 0.197 ~ 0.403, worst case 0.07 ~ 0.53
    groups = read_groups([*MEASURED, {"name": "Lot range", "min": 0.18, "max": 0.25},
                          {"name": "Way out", "min": 0.02, "max": 0.6}])
    leak, good, lot, out = compare(groups, result, read_limit(FIT))    # limit 0.199
    assert (leak["in_rss"], leak["in_wc"], leak["vs_limit"], leak["beyond"], leak["count"]) == (False, True, "all beyond", 4, 4)
    assert (good["in_rss"], good["in_wc"], good["vs_limit"], good["beyond"]) == (True, True, "all inside", 0)
    assert good["mean"] == pytest.approx(0.30)
    assert (lot["in_rss"], lot["vs_limit"], lot["beyond"], lot["count"], lot["mean"]) == (False, "some beyond", None, None, None)
    assert (out["in_rss"], out["in_wc"]) == (False, False)
    assert [row["vs_limit"] for row in compare(groups, result)] == ["", "", "", ""]
    upper = compare(groups, result, Limit(0.26, "upper"))
    assert [row["vs_limit"] for row in upper] == ["all inside", "all beyond", "all inside", "some beyond"]
    lines = summary_lines(groups, result, read_limit(FIT))
    assert lines[0].startswith("min squeeze: 0.199 (lower limit, fit y = 20 x + 1.02 at y = 5")
    assert ("measured Leaking units n=4: 0.140 ~ 0.190 | outside RSS range | inside WC range | "
            "all beyond the limit (4 of 4 beyond)") in lines


def test_config_validation_for_hand_written_rows():
    with pytest.raises(ValueError, match="needs 'step' .* or 'rows'"):
        validate_config({"spec": {"usl": 1}})
    with pytest.raises(ValueError, match="'rows' is for a stack without a STEP file"):
        validate_config({"step": "a.step", "from": {}, "to": {}, "rows": [{"label": "A", "nominal": 1}]})
    with pytest.raises(ValueError, match=r"rows row 1: unknown keys \['tolerance'\]"):
        validate_config({"rows": [{"label": "A", "nominal": 1, "tolerance": 0.1}]})
    with pytest.raises(ValueError, match="rows row 2 needs 'label'"):
        validate_config({"rows": [{"label": "A", "nominal": 1}, {"nominal": 2}]})
    with pytest.raises(ValueError, match="extra row 1 needs 'nominal'"):
        validate_config({"step": "a.step", "from": {}, "to": {}, "extra": [{"label": "A"}]})
    with pytest.raises(ValueError, match="measured group 1: unknown keys"):
        validate_config({"rows": [{"label": "A", "nominal": 1}], "measured": [{"name": "a", "value": 1}]})
    with pytest.raises(ValueError, match="limit side"):
        validate_config({"rows": [{"label": "A", "nominal": 1}], "limit": {"value": 1, "side": "min"}})
    with pytest.raises(ValueError, match="x has 2 points and y has 3"):
        validate_config({"rows": [{"label": "A", "nominal": 1}], "limit": {"x": [1, 2], "y": [1, 2, 3], "y_limit": 2}})
    assert validate_config({"version": 1, "rows": [{"label": "A", "nominal": 1}], "spec": {"lsl": 0}, "pictures": True}) == []


def test_measured_sheet_is_live_and_matches_python(tmp_path):
    notes: list[str] = []
    _, result, out = run_config(_config(tmp_path, measured=MEASURED, limit=FIT, pictures=True), tmp_path, notes)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Stack-up", "Sensitivity", "Measured", "Chart", "Assumptions"]
    ws, stack = wb["Measured"], wb["Stack-up"]
    # B4..B9 point at the result block of the Stack-up sheet, so an edited tolerance moves every verdict
    labels = {}
    for row in range(4, 10):
        formula = ws.cell(row, 2).value
        assert formula.startswith("='Stack-up'!$B$")
        labels[ws.cell(row, 1).value] = stack.cell(int(formula.rsplit("$", 1)[1]), 1).value
    assert labels["Mean"].startswith("Mean") and "RSS" in labels["RSS min"] and "orst" in labels["Worst case max"]
    assert "igma" in labels["Sigma of the sum"]
    assert ws["A18"].value == "Leaking units" and ws["C18"].value == "=COUNT($L18:$O18)"
    assert [ws.cell(18, col).value for col in range(12, 16)] == MEASURED[0]["values"]
    assert 'COUNTIF($L18:$O18,"<"&$B$11)' in ws["J18"].value
    fit_row = 17 + len(MEASURED) + 3
    assert ws["B11"].value == f"=(B{fit_row + 3}-B{fit_row + 2})/B{fit_row + 1}"
    assert ws.cell(fit_row + 1, 2).value.startswith("=SLOPE(") and ws.cell(fit_row + 2, 2).value.startswith("=INTERCEPT(")
    assert ws.cell(fit_row + 3, 2).value == 5
    points = [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(fit_row + 6, fit_row + 6 + len(FIT["x"]))]
    assert points == list(zip(FIT["x"], FIT["y"], strict=True))
    assert len(wb["Chart"]._images) == 1
    assumptions = "\n".join(str(c.value) for row in wb["Assumptions"].iter_rows() for c in row if c.value)
    assert "written by hand" in assumptions and "D1" in assumptions
    assert any(line.startswith("min squeeze: 0.199") for line in notes)
    assert [Path(n.split(": ", 1)[1]).name for n in notes if n.startswith("picture: ")] == ["ta_chart.png", "ta_table.png"]


def test_typed_limit_and_min_max_group_in_excel(tmp_path):
    groups = [{"name": "Lot range", "min": 0.18, "max": 0.25}]
    _, _, out = run_config(_config(tmp_path, measured=groups, limit={"value": 0.2, "side": "upper"}), tmp_path)
    wb = openpyxl.load_workbook(out)
    ws = wb["Measured"]
    assert (ws["B11"].value, ws["B12"].value) == (0.2, "upper")
    assert (ws["D18"].value, ws["E18"].value, ws["C18"].value, ws["J18"].value) == (0.18, 0.25, None, None)
    assert not any(str(c.value).startswith("=SLOPE") for row in ws.iter_rows() for c in row if c.value)
    assert "Chart" not in wb.sheetnames                                # no `pictures`, no picture sheet
    _, _, plain = run_config(_config(tmp_path, output=str(tmp_path / "plain.xlsx")), tmp_path)
    assert "Measured" not in openpyxl.load_workbook(plain).sheetnames  # nothing measured, no sheet


def test_pictures_are_written_next_to_the_excel(tmp_path):
    from PIL import Image

    _, _, out = run_config(_config(tmp_path, measured=MEASURED, limit=FIT, pictures=True, name="Seal squeeze"), tmp_path)
    for suffix in ("_chart.png", "_table.png"):
        picture = Path(out).with_name(Path(out).stem + suffix)
        with Image.open(picture) as image:
            assert image.format == "PNG" and image.width > 800 and image.height > 300


def test_section_is_skipped_without_a_step_file(tmp_path):
    notes: list[str] = []
    run_config(_config(tmp_path, section={"cut_axis": "y"}), tmp_path, notes)
    assert "warning: section picture skipped: it needs a STEP file" in notes


def test_paths_command_on_a_hand_written_config(tmp_path, capsys):
    from tolstack.cli import main

    config_path = tmp_path / "manual.yaml"
    config_path.write_text(yaml.safe_dump(_config(tmp_path)), encoding="utf-8")
    main(["paths", str(config_path)])
    assert "hand-written rows and no STEP file" in capsys.readouterr().out
    main(["run", str(config_path)])
    assert "ta.xlsx" in capsys.readouterr().out


def test_example_config_runs(tmp_path):
    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert validate_config(config) == []
    config.update({"output": str(tmp_path / "seal.xlsx"), "mc_samples": 2000})
    notes: list[str] = []
    contributors, result, out = run_config(config, tmp_path, notes)
    assert [c.label for c in contributors] == ["C", "A", "B", "D1", "D2"]
    assert result.nominal == pytest.approx(0.30)
    assert (result.rss_min, result.rss_max) == pytest.approx((0.1966, 0.4034), abs=1e-4)
    assert (result.lsl, result.usl) == (0.15, 0.45)
    assert Path(out).exists() and (tmp_path / "seal_chart.png").exists() and (tmp_path / "seal_table.png").exists()
