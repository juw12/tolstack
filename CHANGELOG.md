# Changelog

All notable changes to tolstack. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Config files carry their own `version` (currently 1).

## [0.3.0] - 2026-11-13

### Added
- STEP unit check: the length unit declared in the file is read and shown. A file in inch or metre, with
  several units, or with none gets a warning in the app, the CLI and the report (OpenCascade converts the
  geometry to mm on import; the warning tells you to check a known dimension).
- Sensitivity table (app, and a "Sensitivity" sheet in the Excel with live formulas): rows ranked by their
  share of the variance, with the shift of the result at each row's lower / upper limit, so you see which
  tolerance to tighten first.
- GD&T feature control frames on the drawings are read and listed (flatness, parallelism, profile,
  position ...; symbols, spelled-out names and the GDT symbol font). They are **not** in the stack unless
  you add one as a row: a zone of total width t becomes nominal 0, ± t/2. Kinds that cannot act along the
  axis (position, circularity, diameter zones) are marked.
- Upper-only and lower-only tolerances (`4 +0.1/0`, `4 0/-0.05`) are read. The dimension parser is now
  tested against a corpus of formats, and a GD&T frame is never read as a dimension.
- Portable configs: "Download this analysis as a config" names the STEP and the drawings (`drawings/`)
  instead of pointing at one computer; a loaded config asks for the files it needs and matches drawings
  by file name.
- App: every browser session works in its own folder; folders idle for a day are removed.
- `start.bat` (Windows: creates `.venv`, installs, opens the app) and a `Dockerfile` (port 8501, non-root).
- `ruff` and `mypy` in CI (GitHub and GitLab) and in the `dev` extra.

### Fixed
- App: an uploaded STEP was rewritten on every rerun, which changed its time stamp and reloaded the
  model each time. Uploads are now written once.

## [0.2.0] - 2026-11-12

### Added
- Ranked candidate chains (`tolstack paths`, chain picker in the app): fewest contacts first, then the
  largest weakest contact area. Every contact level between two parts is its own option (stepped seats).
- Contacts use the true face outlines (holes, L-shapes) instead of bounding boxes; contact tolerance is
  configurable (`contact_tol`, app sidebar).
- Chain closure check against the 3D; modelled gaps inside the contact tolerance become their own row.
- Per-row distributions (`normal`, `uniform`, `triangular`), Cp, Cpk, predicted ppm and yield, all as live
  Excel formulas; Monte Carlo samples each row with its own distribution.
- Limit dimensions (`12.10/12.00`, `5.95~6.05`), ranked 2D candidates with a picker, part-to-drawing
  mapping (`drawing_map`), detection of drawings without a text layer, optional OCR with Tesseract.
- Excel: input cells in yellow, distribution and direction drop-downs, Assumptions sheet (method, chain
  used, other chains found, general table), Prepared / Checked / Approved block, landscape print setup.
- App: Demo assembly option, chain picture right after the chain is built, load a saved YAML config,
  download the current analysis as a config, clear messages instead of tracebacks.
- CLI: `tolstack demo [folder] --run` (one-command demo), `tolstack paths <config>`.
- Config schema `version: 1`; unknown keys are rejected.

### Fixed
- Title-block notes such as "TOLERANCES UNLESS SPECIFIED 0~10 ±0.05" are no longer read as a dimension.
- When the 3D nominal differs from the drawing nominal, the drawing limits are kept (was: drawing
  tolerance applied around the 3D value).

## [0.1.0] - 2026-11-12

### Added
- First release: STEP assembly -> 1-D chain between two faces -> tolerances from 2D PDFs or a general
  table -> worst case, RSS, Monte Carlo -> Excel with formulas, section picture and 2D evidence.
  Streamlit app and command line.
