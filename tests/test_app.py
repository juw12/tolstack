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
    assert metrics["Cpk"] != "-"
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
