# Changelog

All notable changes to tolstack. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Config files carry their own `version` (currently 1).

## [0.4.0] - 2026-11-13

### Added
- Upload a **whole folder** or **one zip** as well as loose files. STEP and PDF files are taken from it,
  sub-folders included; anything else in a zip is skipped. "Path on this computer" also accepts a folder
  or a zip. With several STEP files you choose the assembly (largest first).
- **General tolerance follows the 2D drawing**: a dimension without its own tolerance now takes the general
  tolerance written on that part's drawing. Read: a size table under "TOLERANCES UNLESS OTHERWISE
  SPECIFIED" / "未注公差", one ± value, ISO 2768 / JIS B 0405 / GB/T 1804 class f, m, c or v, and a
  decimal-places rule (`X.XX ±0.05`, used when the dimension is found on the drawing). The source column
  names the drawing and the kind of note; the notes are listed in the app and on the "Assumptions" sheet.
- **3D view** of the assembly in the app (toggle): the From and To parts and the picked faces are
  highlighted and follow the Part / Face lists as you change them; the chain parts can be shown too.
  Faces are still picked in the lists, not by clicking in the view.
- Config key `general_from_drawing` (default `true`); drawing globs may use `**` for sub-folders.

### Changed
- **Results can change**: with a general-tolerance note on a drawing, rows that used the app table before
  now use the drawing's value. Untick "General tolerance follows the 2D drawing" (or set
  `general_from_drawing: false`) to get the 0.3.0 behaviour. The app table is still used for a part
  without a drawing, a drawing without a note, and a size the note does not cover.
- The app needs Streamlit 1.49 or newer (folder upload) and Plotly (3D view).
- Streamlit's source-file watcher is off (`.streamlit/config.toml`, also copied into the Docker image):
  its module scan runs on the server's event loop and made the page stall on a busy machine.

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
- Configs: a path written on Windows (`C:\...\part.pdf`) is now reduced to its file name on Linux
  too, so a config saved on a PC also loads on a Linux server (and the other way round).

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
