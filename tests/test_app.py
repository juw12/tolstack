from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("OCP")

ROOT = Path(__file__).parents[1]


def test_app_builds_chain_from_path(tmp_path):
    import sys

    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(ROOT / "examples"))
    import make_demo

    step = make_demo.write_step(tmp_path / "demo.step", make_demo.demo_parts())
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value("Path on this computer").run()
    at.sidebar.text_input[0].set_value(str(step)).run()
    assert not at.exception
    at.selectbox(key="From (reference)_part").set_value("[4] POST").run()
    at.selectbox(key="To_part").set_value("[3] CAP_TOP").run()
    at.button[0].click().run()
    assert not at.exception
    nominal = [m for m in at.metric if m.label == "Nominal"][0]
    assert nominal.value == "+1.600"
