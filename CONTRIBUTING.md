# Contributing to tolstack

Thanks for helping. Bug reports with a small STEP that shows the problem are the most useful thing you
can send: please strip anything confidential first (a few boxes that reproduce the contact are enough).

## Set up

```bash
git clone https://github.com/juw12/tolstack.git
cd tolstack
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[app,dev]"
pytest -q                                         # 133 tests, about two minutes
ruff check . && mypy                              # lint and types, both run in CI
streamlit run app.py
```

## How the code is laid out

| module | job |
|---|---|
| `tolstack/cad.py` | read STEP (OpenCascade), planar faces along the axis, true face outlines |
| `tolstack/chain.py` | contacts, ranked candidate chains, chain -> contributors with closure check |
| `tolstack/tolerances.py` | general table, PDF dimension search, evidence crops |
| `tolstack/stack.py` | WC, RSS, distributions, Cp/Cpk/ppm, Monte Carlo |
| `tolstack/excel.py` | the report (formulas, chart, measured sheet, assumptions, sign-off) |
| `tolstack/measured.py` | measured groups and a typed or fitted limit against the stack |
| `tolstack/pictures.py` | chart and table PNG for a mail or a slide |
| `tolstack/pipeline.py` | config validation and the end-to-end `run_config` |
| `tolstack/section.py` | colour section picture |
| `tolstack/workspace.py` | per-session work folders of the app and their clean-up |
| `app.py`, `tolstack/cli.py` | web app and command line |

## Rules for changes

- Every change to the math, the chain logic or the dimension parser needs a test in `tests/`. Build test
  geometry from boxes with `tolstack.demo.box` / `write_step` instead of committing STEP files.
- Anything that changes a number in the report must keep the Excel formulas and the Python result equal.
- New config keys: add them to `CONFIG_KEYS` in `pipeline.py`, the README table and `CHANGELOG.md`.
  A change that breaks old configs bumps the config `version` and needs a migration note.
- Keep the app usable without the command line and the command line usable without the app.
- No company data, real part numbers or drawings in the repo, issues or tests.

## Pull requests

1. Branch from `main`, keep the change focused.
2. `pytest -q`, `ruff check .` and `mypy` pass locally; CI runs them on Linux and Windows.
3. Add a line under "Unreleased" in `CHANGELOG.md`.
