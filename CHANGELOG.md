# Changelog

All notable changes to tolstack. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Config files carry their own `version` (currently 1).

## [0.6.0] - 2026-11-13

### Added
- **Native Creo / Pro-E assemblies are converted to STEP** with the Creo Parametric installed on the PC
  ("Path on this computer"): the assemblies of the folder are listed (newest version of each, most likely top
  assembly first, with how many sub-assemblies and parts it names), and *Convert with Creo* runs Creo in the
  background (no window) with a trail file: open, Save a Copy as STEP, exit. The newest version of every file it
  needs is copied to a private job folder first, so the user's folder is never written and paths with Chinese
  characters do not stop Creo. The STEP joins the assembly list and is reused on the next run. Creo is found in
  `C:/D:/E:\PTC\Creo *` and `Program Files\PTC`, or set `TOLSTACK_CREO`.
- A Creo license refusal (user / host on the license server's EXCLUDE list, or not on its INCLUDE list) is shown
  as such, with who to ask, instead of "no STEP".
- **Memory guard for the server** (`tolstack/resources.py`): the container memory limit is read from the cgroup;
  a STEP file that would not fit is refused with a message before it is read (instead of the pod being killed for
  every user), and the largest upload is sized from the limit (about 110 MB of STEP in a 1 GiB pod, 2 GB on a PC).
  Measured: a 9.7 MB assembly needs +95 MB, a 66.5 MB one +220 MB while it is read.
- At most 2 STEP files and 3 models stay cached in memory at once.

### Not done (on purpose)
- Creo files are not decoded directly. Their geometry is PTC's own compressed binary format; a guessed surface or
  offset would give a wrong stack-up without any warning. Creo itself (or a STEP export) stays the source.
- On the shared server (Linux, no Creo) Creo files are listed with the export steps; conversion needs a PC with Creo.

## [0.5.0] - 2026-11-13

Tuned on 44 real project hand-over folders ("16 交接時的最新版本3D圖面"): 19,327 files, of which 66 loose STEP
files (largest 549 MB), 61 zip / 7z archives (54 of them hold a STEP), 1,488 Creo assemblies and 15,991 Creo parts.

### Added
- **Uploads up to 2 GB per file** (was Streamlit's 200 MB). Large uploads are written to disk in 1 MB chunks
  instead of being copied in memory.
- **7z archives**, uploaded or typed as a path, like zip (STEP and PDF only are unpacked).
- **A folder is searched inside its zip / 7z files too** ("Path on this computer"). Each archive is unpacked once
  into a cache; an archive without STEP or PDF is listed, not unpacked.
- **Repeated STEP files are dropped**: the same file loose and inside an archive (same name and size) shows once.
- **Newest full assembly first**: of the STEP files at least half the size of the largest, the one with the newest
  date in its name (20260805, 260909 or 0903) is the default; the list shows size and folder of each file.
- **Native Creo / Pro-E files** (`.prt.12`, `.asm.3`, `.drw`) are recognised and counted, with the export-to-STEP
  steps, instead of a bare "no STEP file".
- **The 2D folder next to the 3D folder** (`17 ... -2D` beside `16 ... 3D`, found in 41 of the 44 hand-overs) is
  used for the drawings when no drawings folder is given.

- **The STEP file is read once per file**: a new stack-up axis or contact tolerance reuses the parts already
  read (before, every change read the whole file again - minutes for a hand-over assembly).

### Checked on real data
- ZIRIS hand-over STEP `Ziris_system_all_40mm_spk_asm20260716.stp` (66.5 MB, mm): 504 parts, 5,848 faces normal
  to Z, peak memory 436 MB. Folder scan of the ZIRIS, Lory3, NS300 and PD160 hand-overs: the STEP inside their
  7z / zip files is found, repeats dropped, the 2D folder paired (72, 100, 15, 15 PDFs), the Creo-only PD160
  folder explained (3,982 parts, 482 assemblies).

### Changed
- `py7zr` is a new dependency.
- `find_inputs` drops repeats; `extract_zip` is now `extract_archive` (the old name still works).

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
