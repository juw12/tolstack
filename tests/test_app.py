from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("OCP")

ROOT = Path(__file__).parents[1]


def _app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)


def test_app_demo_flow_builds_chain_and_result():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("Demo assembly").run()
    assert not at.exception
    at.selectbox(key="From (reference)_part").set_value("[4] POST").run()
    at.selectbox(key="To_part").set_value("[3] CAP_TOP").run()
    chain_radio = [r for r in at.radio if r.label.startswith("Chain through")][0]
    assert any("POST -> BASE -> SPACER -> PCB -> CAP_TOP" in o for o in chain_radio.options)
    at.button(key="build").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Nominal"] == "+1.600"
    assert metrics["RSS verdict"] == "PASS"
    assert float(metrics["Cpk"]) > 1.33          # demo spec 1.2..2.0 is preset
    spacer = [row for row in at.session_state["chain"] if row["part"] == "SPACER"][0]
    assert (spacer["tol_plus"], spacer["tol_minus"]) == (0.08, 0.08)
    assert "SPACER.pdf" in spacer["source"]


def test_app_path_mode_reports_unreadable_step(tmp_path):
    bad = tmp_path / "broken.step"
    bad.write_text("not a step file")
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("Path on this computer").run()
    at.sidebar.text_input[0].set_value(str(bad)).run()
    assert not at.exception
    assert any("Could not read this STEP" in e.value for e in at.error)


def _open_path_mode(at, step, drawings=None):
    at.run()
    at.sidebar.radio[0].set_value("Path on this computer").run()
    at.text_input(key="step_input").set_value(str(step)).run()
    if drawings is not None:
        at.text_input(key="drawings_input").set_value(str(drawings)).run()
    return at


def _build_demo_chain(at):
    at.selectbox(key="From (reference)_part").set_value("[4] POST").run()
    at.selectbox(key="To_part").set_value("[3] CAP_TOP").run()
    at.button(key="build").click().run()
    assert not at.exception
    return at


def test_app_shows_sensitivity_table():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("Demo assembly").run()
    _build_demo_chain(at)
    tables = [d.value for d in at.dataframe if "Swing" in d.value.columns]
    assert len(tables) == 1
    table = tables[0]
    assert len(table) == len(at.session_state["chain"])
    assert table["Variance %"].sum() == pytest.approx(100.0)
    assert list(table["Variance %"]) == sorted(table["Variance %"], reverse=True)     # biggest driver first
    assert (table["Gain"] >= 0).all()
    assert at.session_state["gdt"] == {} or not any(at.session_state["gdt"].values())  # demo drawings have none


def test_app_adds_a_gdt_callout_as_a_row(tmp_path):
    from tolstack.demo import write_demo, write_drawing

    step = write_demo(tmp_path)
    write_drawing(tmp_path / "drawings" / "SPACER.pdf", "SPACER", ["10.00±0.08", "FLATNESS 0.05"])
    at = _build_demo_chain(_open_path_mode(_app(), step, tmp_path / "drawings"))
    assert [hit.kind for hit in at.session_state["gdt"]["SPACER"]] == ["flatness"]
    before = {m.label: m.value for m in at.metric}["Worst case"]
    assert not [row for row in at.session_state["chain"] if row["source"].startswith("GD&T")]   # reported, not added

    at.button(key="gdt_add").click().run()
    assert not at.exception
    added = [row for row in at.session_state["chain"] if row["source"].startswith("GD&T")]
    assert [(row["label"], row["part"], row["nominal"], row["tol_plus"], row["tol_minus"]) for row in added] == [
        ("G1", "SPACER", 0.0, 0.025, 0.025)]
    after = {m.label: m.value for m in at.metric}
    assert after["Nominal"] == "+1.600"                 # a form zone moves no nominal
    assert after["Worst case"] != before                # ... but it widens the band
    picker = [m for m in at.multiselect if m.label.startswith("Callouts to add")][0]
    assert picker.value == []                           # not offered a second time


def test_app_warns_when_the_step_is_not_in_mm(tmp_path):
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Controller

    from tolstack.demo import demo_parts, write_step

    STEPControl_Controller.Init_s()
    assert Interface_Static.SetCVal_s("write.step.unit", "M")
    try:
        step = write_step(tmp_path / "metre.step", demo_parts())
    finally:
        Interface_Static.SetCVal_s("write.step.unit", "MM")
    at = _open_path_mode(_app(), step)
    assert not at.exception
    assert any("not mm" in w.value for w in at.warning)
    _build_demo_chain(at)
    assert {m.label: m.value for m in at.metric}["Nominal"] == "+1.600"      # still read in mm


# ---------------------------------------------------------------- v0.4.0: 3-D view, folder / zip, drawing note


def _demo_zip(folder, name="demo_set.zip"):
    """The demo assembly and its drawings as one zip, the way a user would hand a project over."""
    import zipfile

    from tolstack.demo import write_demo

    source = folder / "source"
    write_demo(source)
    archive = folder / name
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            bundle.write(path, f"project/{path.relative_to(source).as_posix()}")
    return archive, source


def _nominal(at):
    return {m.label: m.value for m in at.metric}["Nominal"]


def test_app_3d_view_follows_the_picked_faces_and_can_be_switched_off():
    at = _app()
    at.run()
    at.sidebar.radio[0].set_value("Demo assembly").run()
    at.selectbox(key="From (reference)_part").set_value("[4] POST").run()
    at.selectbox(key="To_part").set_value("[3] CAP_TOP").run()
    assert not at.exception
    assert at.toggle(key="show_3d").value is True
    assert len(at.get("plotly_chart")) == 1
    at.radio(key="view_show").set_value("Whole assembly").run()
    assert not at.exception and len(at.get("plotly_chart")) == 1
    at.toggle(key="show_3d").set_value(False).run()
    assert not at.exception
    assert len(at.get("plotly_chart")) == 0
    assert at.selectbox(key="To_part").value == "[3] CAP_TOP"          # the pickers stay as they were
    at.toggle(key="show_3d").set_value(True).run()
    assert not at.exception and len(at.get("plotly_chart")) == 1
    assert at.radio(key="view_show").value == "Whole assembly"         # ... and so does the view choice
    at.toggle(key="show_3d").set_value(False).run()
    _build_demo_chain(at)
    assert _nominal(at) == "+1.600"


def test_app_path_mode_takes_a_folder_or_a_zip(tmp_path):
    archive, source = _demo_zip(tmp_path)
    for handed_over in (source, archive):
        at = _build_demo_chain(_open_path_mode(_app(), handed_over))
        assert _nominal(at) == "+1.600"
        spacer = [row for row in at.session_state["chain"] if row["part"] == "SPACER"][0]
        assert (spacer["tol_plus"], "SPACER.pdf" in spacer["source"]) == (0.08, True)   # drawings found with the STEP
    empty = tmp_path / "empty"
    empty.mkdir()
    at = _open_path_mode(_app(), empty)
    assert any("No STEP file in this folder" in e.value for e in at.error)


def test_app_upload_takes_loose_files_or_one_zip(tmp_path):
    archive, source = _demo_zip(tmp_path)
    loose = [(path.name, path.read_bytes(), "application/octet-stream")
             for path in sorted(p for p in source.rglob("*") if p.is_file())]
    packed = [(archive.name, archive.read_bytes(), "application/zip")]
    for uploaded in (loose, packed):
        at = _app()
        at.run()
        assert at.sidebar.radio[0].value == "Upload files"
        at.sidebar.file_uploader[1].set_value(uploaded).run()           # [0] loads a config, [2] takes a folder
        assert not at.exception and not at.error
        _build_demo_chain(at)
        assert _nominal(at) == "+1.600"
        base = [row for row in at.session_state["chain"] if row["part"] == "BASE"][0]
        assert "general tol from BASE.pdf (size table)" in base["source"]

    at = _app()
    at.run()
    at.sidebar.file_uploader[1].set_value([("broken.zip", b"not a zip", "application/zip")]).run()
    assert not at.exception
    assert any(e.value.startswith("broken.zip: ") for e in at.error)


def test_app_general_tolerance_follows_the_drawing_until_switched_off(tmp_path):
    import fitz

    from tolstack.demo import write_demo

    step = write_demo(tmp_path)
    doc = fitz.open()                                   # BASE drawing: no dimension tolerances, a single-value note
    page = doc.new_page(width=842, height=595)
    page.insert_text((300, 200), "5.00", fontsize=12)
    page.insert_text((560, 520), "UNLESS OTHERWISE SPECIFIED ±0.3", fontsize=8)
    doc.save(str(tmp_path / "drawings" / "BASE.pdf"))

    at = _build_demo_chain(_open_path_mode(_app(), step, tmp_path / "drawings"))
    base = [row for row in at.session_state["chain"] if row["part"] == "BASE"]
    assert base and all(row["tol_plus"] == 0.3 and "(single value) ±0.3" in row["source"] for row in base)
    assert at.session_state["general_notes"]["BASE"].kind == "single value"
    followed = {m.label: m.value for m in at.metric}["Worst case"]

    at.sidebar.checkbox(key="follow_drawing").uncheck().run()
    assert not at.exception
    base = [row for row in at.session_state["chain"] if row["part"] == "BASE"]
    assert base and all(row["tol_plus"] < 0.3 and "single value" not in row["source"] for row in base)
    assert {m.label: m.value for m in at.metric}["Worst case"] != followed
    assert _nominal(at) == "+1.600"
