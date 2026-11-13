"""tolstack web app:  streamlit run app.py"""
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
import yaml

from tolstack import creo
from tolstack.cad import load_step
from tolstack.chain import build_chain, find_paths
from tolstack.excel import write_report
from tolstack.intake import (
    assembly_order,
    extract_archive,
    find_inputs,
    is_archive,
    native_kind,
    scan_folder,
    sibling_drawing_folder,
    sync_inbox,
)
from tolstack.pipeline import (
    CONFIG_VERSION,
    apply_tolerances,
    expand_drawings,
    gdt_contributor,
    load_model,
    portable_paths,
    resolve_drawing_map,
    validate_config,
)
from tolstack.resources import check_step
from tolstack.stack import DISTRIBUTIONS, Contributor, analyze, is_extra, sensitivity
from tolstack.tolerances import DEFAULT_GENERAL_TABLE, tesseract_available
from tolstack.view3d import VIEWS, scene_figure
from tolstack.workspace import ROOT, clean_old, session_dir

st.set_page_config(page_title="tolstack - tolerance stack-up", layout="wide")
st.title("tolstack - tolerance stack-up from 3D + 2D")
st.caption("1 Load a STEP assembly  ->  2 pick the two faces of the gap / step  ->  3 check the chain  "
           "->  4 read the result  ->  5 export Excel.")

with st.expander("How it works / what to prepare"):
    st.markdown(
        "**You need** one assembly STEP file (AP203 / AP214 / AP242, parts as separate solids, in their "
        "assembled position). **Optional:** the 2D drawings as PDF, one per part, named like the part "
        "(`SPACER.pdf` for part `SPACER`).\n\n"
        "1. **Files** (sidebar) - upload the STEP and PDFs as files, as a whole folder or as one zip / 7z "
        "(up to 2 GB per file), or type the path of a hand-over folder on your PC: the STEP inside its zip / "
        "7z files is found too, and the '17 ... 2D' folder next to a '16 ... 3D' folder is used for the "
        "drawings. Native Creo / Pro-E files (.prt / .asm): with *Path on this computer* on a PC that has Creo "
        "installed, pick the assembly and press *Convert with Creo* - Creo saves a STEP copy in the background "
        "(your folder is not changed). Or try the *Demo assembly*.\n"
        "2. **What to analyze** - pick the stack-up axis, then for *From* and *To* the part and its face. The "
        "3D view marks both parts and both faces while you choose. The result is the distance To - From along "
        "that axis: a step (flush / offset) or a gap (clearance).\n"
        "3. **Check the chain** - the app walks from one face to the other through parts that touch. Each row is "
        "one dimension of one part. Tolerances come from the drawing when the dimension is found there with a "
        "tolerance; else from the general-tolerance note of that part's drawing (a size table, ISO 2768-m, "
        "X.XX ±0.05); else from the general tolerance table in the sidebar. Edit any cell; add rows for what "
        "CAD cannot see.\n"
        "4. **Result** - worst case, RSS, Cpk and Monte Carlo against your spec limits, and which row matters most.\n"
        "5. **Excel report** - live formulas, the section picture, the 2D evidence and a sign-off block.\n\n"
        "Everything is in **mm**. The source column always says where a tolerance came from - check it before "
        "you trust the number."
    )


@st.cache_resource(ttl=3600, show_spinner=False)
def housekeeping():
    """Remove uploads of sessions idle for a day; at most once an hour per server."""
    return clean_old()


housekeeping()
WORK = session_dir(st.session_state.setdefault("work_id", uuid4().hex[:12]))    # private to this browser session
NUMERIC = ["nominal", "tol_plus", "tol_minus"]
TYPES = ["Step (offset / flush between two faces)", "Gap (clearance between two faces)"]


@st.cache_resource(max_entries=2, show_spinner="Reading the STEP file - a hand-over assembly of 50-150 MB takes minutes; "
                                "it is read once per file, a new axis or contact tolerance reuses it...")
def cached_parts(step_path: str, mtime: float):
    return load_step(step_path)


@st.cache_resource(max_entries=3, show_spinner="Finding faces and contacts...")
def cached_model(step_path: str, axis: str, contact_tol: float, mtime: float):
    return load_model(step_path, axis, contact_tol, parts=cached_parts(step_path, mtime))


@st.cache_resource(show_spinner="Writing the demo assembly...")
def demo_files():
    from tolstack.demo import write_demo

    step = write_demo(ROOT / "demo")
    return str(step), sorted(str(p) for p in (ROOT / "demo" / "drawings").glob("*.pdf"))


def reset_chain():
    for key in ("chain", "raw_chain", "candidates", "choices", "gdt"):
        st.session_state.pop(key, None)


def reread_tolerances():
    """Fill the tolerances from the 2D again; the rows added by hand are kept."""
    if "chain" in st.session_state:
        st.session_state.extra_rows = st.session_state.get("current_extra", [])
        st.session_state.pop("chain")


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_") or "analysis"


UPLOAD_LIMIT_MB = int(st.get_option("server.maxUploadSize"))


def step_label(step: str) -> str:
    """Name, size and the folder it came from, so two STEP files of the same name can be told apart."""
    path = Path(step)
    return f"{path.name}  ({path.stat().st_size / 1e6:.1f} MB, in {path.parent.name})"


CREO_EXE = creo.find_creo()


def creo_panel(native_paths: list[Path], key: str) -> list[str]:
    """List the Creo assemblies among `native_paths`; convert the chosen one to STEP with the local Creo.

    Returns the STEP files converted so far for this folder (they join the assembly list).
    """
    files = creo.latest_versions(native_paths)
    found = creo.assemblies(files)
    done: dict[str, list[str]] = st.session_state.setdefault("creo_steps", {})
    converted = [s for s in done.get(key, []) if Path(s).is_file()]
    if not found:
        return converted
    summary = creo.summary(native_paths) or ""
    if CREO_EXE is None:
        st.caption(f"Native Creo files here ({summary}). Creo Parametric is not installed where this app runs, so "
                   "they can not be converted here: run tolstack on a PC with Creo (start.bat), or export the top "
                   "assembly as STEP in Creo (File > Save As > Save a Copy > STEP).")
        return converted
    with st.expander(f"Creo files: {summary}", expanded=not converted):
        pick = st.selectbox("Creo assembly to convert", found, key=f"creo_pick_{key}",
                            format_func=lambda a: a.label,
                            help="Most likely top assembly first (its name says all / top / main, or it holds the "
                                 "most sub-assemblies) - check it is the one you need.")
        newer = creo.too_new([pick.file], CREO_EXE)
        if newer:
            st.warning(f"{pick.file.model} was saved in {newer[0][1]}; the {creo.installed_release(CREO_EXE)} "
                       "installed here can not open files from a newer Creo. Convert it on a PC with that release "
                       "or newer, or ask for a STEP export.")
        if st.button("Convert with Creo (STEP)", key=f"creo_go_{key}", disabled=bool(newer),
                     help=f"Runs {CREO_EXE} in the background (no window). The newest version of every file is "
                          "copied to a private folder first; your folder is not changed. A large assembly takes "
                          "minutes."):
            with st.spinner(f"Creo is exporting {pick.file.model} to STEP..."):
                try:
                    step = creo.export_step(pick.file, files, ROOT / "creo")
                except creo.CreoError as err:
                    st.error(str(err))
                else:
                    done[key] = [str(step), *[s for s in converted if s != str(step)]]
                    converted = done[key]
                    st.success(f"STEP written: {step.name} ({step.stat().st_size / 1e6:.1f} MB)")
    return converted


# ---------------------------------------------------------------- 1. input files
with st.sidebar:
    saved = st.file_uploader("Load a saved analysis (YAML config)", type=["yaml", "yml"],
                             help="A config downloaded from this app or written for `tolstack run`.")
    if saved is not None and st.session_state.get("loaded_config_id") != saved.file_id:
        try:
            loaded = yaml.safe_load(saved.getvalue().decode("utf-8")) or {}
            validate_config(loaded)
            if "step" not in loaded:
                raise ValueError("it has hand-written `rows` and no STEP file - run it with `tolstack run <config>`")
            st.session_state.loaded_config_id = saved.file_id
            config_step = Path(str(loaded["step"]))
            globs = loaded.get("drawings") or []
            if config_step.is_absolute() and config_step.is_file():
                st.session_state.input_mode = "Path on this computer"
                st.session_state.step_input = str(config_step)
                if globs:
                    st.session_state.drawings_input = str(Path(globs[0]).parent)
                st.session_state.pop("config_needs", None)
            else:                                   # a portable config, or one from another computer
                st.session_state.input_mode = "Upload files"
                st.session_state.config_needs = config_step.name
            st.session_state.config_map = loaded.get("drawing_map") or {}
            st.session_state.config_extra = loaded.get("extra") or []
            st.session_state.axis = loaded.get("axis", "z")
            st.session_state.contact_tol = float(loaded.get("contact_tol", 0.02))
            st.session_state.sigma_level = float(loaded.get("sigma_level", 3.0))
            st.session_state.follow_drawing = bool(loaded.get("general_from_drawing", True))
            spec = loaded.get("spec") or {}
            if spec.get("lsl") is not None:
                st.session_state.lsl = float(spec["lsl"])
            if spec.get("usl") is not None:
                st.session_state.usl = float(spec["usl"])
            st.session_state.ta_type = TYPES[1] if loaded.get("type") == "gap" else TYPES[0]
            st.session_state.pending_config = loaded
            reset_chain()
            if st.session_state.get("config_needs"):
                st.success("Config loaded - now upload its STEP file and drawings below.")
            else:
                st.success("Config loaded - check the faces, then build the chain.")
        except (ValueError, KeyError, yaml.YAMLError) as err:
            st.error(f"Could not load this config: {err}")

    st.header("1. Files")
    mode = st.radio("Input", ["Upload files", "Path on this computer", "Demo assembly"], horizontal=False,
                    key="input_mode",
                    help="The demo is a 5-part box stack with 2D drawings - a quick way to see every step.")
    step_path, drawings, drawing_globs = None, [], []
    steps: list[str] = []                       # every STEP found in what was handed over, largest first
    portable = mode != "Path on this computer"  # the config then names the files instead of pointing at them
    if mode == "Upload files":
        kinds = ["step", "stp", "pdf", "zip", "7z"]
        files = st.file_uploader(f"STEP assembly and 2D drawings (PDF) - or one zip / 7z of them "
                                 f"(up to {UPLOAD_LIMIT_MB / 1024:g} GB per file)", type=kinds,
                                 accept_multiple_files=True,
                                 help="One assembly STEP with every part as its own solid, in the assembled "
                                      "position, plus the part drawings. Drawings are matched to parts by file "
                                      "name, e.g. SPACER.pdf -> part SPACER. A zip or 7z is unpacked here (STEP and "
                                      "PDF only, sub-folders included). Files stay in a private temporary folder "
                                      "and are deleted after a day without use. For very large STEP files on your "
                                      "own PC, 'Path on this computer' is faster: nothing is copied.")
        folder_files = st.file_uploader("... or a whole folder", type=kinds, accept_multiple_files="directory",
                                        help="Pick the project folder: every STEP, PDF, zip and 7z in it and in its "
                                             "sub-folders is uploaded. Native Creo files are skipped.")
        uploads = {u.file_id: (u.name, u) for u in [*(files or []), *(folder_files or [])]}
        for problem in sync_inbox(WORK / "inbox", uploads):
            st.error(problem)
        steps, drawings = find_inputs(WORK / "inbox")
        if not steps and st.session_state.get("config_needs"):
            st.info(f"The loaded config expects **{st.session_state.config_needs}** - upload it here, "
                    "with the same drawings.")
        elif uploads and not steps:
            st.warning("No STEP file in the upload yet. Native Creo / Pro-E files (.prt, .asm) are not uploaded: "
                       "on a PC with Creo use 'Path on this computer' and Convert with Creo, or export the top "
                       "assembly as STEP first.")
        if drawings:
            st.caption(f"{len(drawings)} PDF drawings")
    elif mode == "Path on this computer":
        typed = st.text_input("STEP file, folder, zip or 7z", key="step_input",
                              help="A path this app can read: on your own PC when you run it locally. A folder, zip "
                                   "or 7z is searched for the STEP and the PDF drawings, sub-folders and the "
                                   "archives inside the folder included. Point it at a hand-over '16 ... 3D' "
                                   "folder and the '17 ... 2D' folder next to it is used for the drawings. "
                                   "On a shared server use Upload files.")
        folder = st.text_input("2D drawings folder (optional)", key="drawings_input",
                               help="Only needed when the drawings are not in the folder above or in the "
                                    "'17 ... 2D' folder next to it.")
        source = Path(typed.strip().strip('"')) if typed.strip() else None
        beside: list[str] = []                  # the PDFs that came with the STEP
        if source is None:
            pass
        elif source.is_dir():
            with st.spinner("Looking through the folder (archives are unpacked once)..."):
                found = scan_folder(source, WORK / "archives")
            steps, beside = found.steps, found.pdfs
            drawing_globs = [str(source / "*.pdf"), str(source / "**" / "*.pdf")]
            for note in found.notes:
                st.caption(note)
            if found.native:
                native_paths = [p for p in Path(source).rglob("*") if native_kind(p.name) and p.is_file()]
                steps = creo_panel(native_paths, str(source)) + steps
            if not steps:
                st.error("No STEP file in this folder or in its zip / 7z files."
                         + ("" if found.native else " Add the assembly as STEP."))
        elif source.is_file() and is_archive(source):
            stat = source.stat()
            unpacked = WORK / "archives" / f"{safe_name(source.stem)}_{stat.st_size}_{int(stat.st_mtime)}"
            try:
                if not unpacked.exists():       # unpack once: a rewritten STEP would be read again on every rerun
                    with st.spinner(f"Unpacking {source.name}..."):
                        extract_archive(source, unpacked)
                steps, beside = find_inputs(unpacked)
                portable = True
                if not steps:
                    st.error(f"No STEP file in this {source.suffix.lstrip('.')} - native Creo / Pro-E files "
                             "can not be read; export the top assembly as STEP.")
            except (ValueError, OSError) as err:
                shutil.rmtree(unpacked, ignore_errors=True)
                st.error(f"Could not open this archive: {err}")
        elif source.is_file() and native_kind(source.name):
            native_paths = [p for p in source.parent.iterdir() if native_kind(p.name) and p.is_file()]
            steps = creo_panel(native_paths, str(source.parent))
            if not steps:
                st.info(f"{source.name} is a native Creo / Pro-E file: convert its assembly above, or type the "
                        "folder that holds it.")
        elif source.is_file():
            steps = [str(source)]
        else:
            st.error("Path not found - check it (quotes are fine).")
        paired = None
        if not folder and source is not None and source.exists():
            paired = sibling_drawing_folder(source)
        if folder or paired:
            root = Path(folder.strip().strip('"')) if folder else paired
            if not root.is_dir():
                st.error("Drawings folder not found.")
            else:
                drawing_globs = [str(root / "*.pdf"), str(root / "**" / "*.pdf")]
                drawings = sorted(set(expand_drawings(drawing_globs, Path(".")) + beside))
                where = "found" if folder else f"found, from the 2D folder next to it: {root.name}"
                st.caption(f"{len(drawings)} PDF drawings {where}")
        elif beside:
            drawings = beside
            st.caption(f"{len(drawings)} PDF drawings found with the STEP")
    else:
        demo_step, drawings = demo_files()
        step_path = Path(demo_step)
        st.caption("Demo: pick POST top -> CAP_TOP top for the +1.6 step.")
    steps = assembly_order(steps)
    if len(steps) > 1:
        if st.session_state.get("step_pick") not in steps:
            st.session_state.pop("step_pick", None)
        expected = st.session_state.get("config_needs", "")
        step_path = Path(st.selectbox(
            f"Assembly STEP ({len(steps)} found, newest full assembly first)", steps, key="step_pick",
            on_change=reset_chain,
            index=next((i for i, s in enumerate(steps) if Path(s).name == expected), 0),
            format_func=step_label))
    elif steps:
        step_path = Path(steps[0])

    st.header("Settings")
    axis = st.selectbox("Stack-up direction (axis)", ["z", "y", "x"], key="axis", on_change=reset_chain)
    contact_tol = st.number_input("Contact tolerance (mm)", 0.001, 1.0, 0.02, 0.005, format="%.3f",
                                  help="Two faces closer than this count as touching. Raise it if a contact "
                                       "is missed because of a small modelling gap.", key="contact_tol",
                                  on_change=reset_chain)
    sigma_level = st.number_input("Sigma level for 'normal' rows", 1.0, 6.0, 3.0, 0.5,
                                  help="Tolerance = +/- this many sigma. 3 is the usual choice.", key="sigma_level")
    ocr = st.checkbox("OCR drawings without a text layer", value=False, disabled=not tesseract_available(),
                      help="Needs Tesseract installed. Without it, such drawings fall back to the general table.")
    st.subheader("General tolerance")
    st.session_state.setdefault("follow_drawing", True)
    follow_drawing = st.checkbox("General tolerance follows the 2D drawing", key="follow_drawing",
                                 on_change=reread_tolerances,
                                 help="A dimension without its own tolerance takes the general tolerance written on "
                                      "that part's drawing: a size table under 'TOLERANCES UNLESS OTHERWISE "
                                      "SPECIFIED' / '未注公差', one ± value, ISO 2768 / JIS B 0405 / GB/T 1804 "
                                      "class f, m, c or v, or a decimal-places rule (X.XX ±0.05).")
    st.caption("Table below: used for a part without a drawing, and when the drawing has no general-tolerance "
               "note or the note does not cover the size." if follow_drawing else
               "Table below: used for every dimension without its own tolerance.")
    general = st.data_editor(pd.DataFrame(DEFAULT_GENERAL_TABLE, columns=["from mm", "to mm", "± tol"]),
                             num_rows="dynamic", key="general")

if step_path is None:
    st.info("Upload a STEP assembly (files, a folder or a zip), type its path, or choose **Demo assembly** in the "
            "sidebar to start.")
    st.stop()

too_big = check_step(Path(step_path).stat().st_size)
if too_big and st.session_state.get("model_key") != str(step_path):
    st.error(too_big)
    st.stop()
try:
    model = cached_model(str(step_path), axis, float(contact_tol), Path(step_path).stat().st_mtime)
    st.session_state.model_key = str(step_path)
except Exception as err:  # noqa: BLE001 - show any CAD error as a message, not a traceback
    st.error(f"Could not read this STEP file: {err}")
    st.caption("tolstack needs an assembly STEP (AP203/AP214/AP242) with solid bodies.")
    st.stop()
st.success(f"{len(model.parts)} parts, {len(model.faces)} faces normal to {axis.upper()}, "
           f"{len(model.contacts)} part-to-part contacts (true face outlines, tolerance {contact_tol:g} mm)")
if model.unit_note:
    st.warning(model.unit_note[0].upper() + model.unit_note[1:] + ".")

# ---------------------------------------------------------------- 2. choose the analysis
st.header("2. What to analyze")
st.caption("Pick the two faces whose distance matters. *From* is the reference; the result is To - From along "
           f"the {axis.upper()} axis, so a positive number means the To face sits further along +{axis.upper()}.")
c1, c2 = st.columns(2)
ta_type = c1.radio("Type", TYPES, key="ta_type")
ta_name = c2.text_input("Analysis name", "Step A vs B" if ta_type.startswith("Step") else "Gap A to B")
part_names = [f"[{p.index}] {p.name}" for p in model.parts]


def part_index(label: str) -> int:
    return int(label[1:label.index("]")])


def face_picker(column, title, default_kind):
    column.subheader(title)
    label = column.selectbox("Part", part_names, key=f"{title}_part", on_change=reset_chain)
    faces = model.faces_of(part_index(label))
    if not faces:
        column.warning("This part has no planar face normal to the axis - pick another part or axis.")
        return None
    labels = [f"{f.kind} @ {f.level:.3f}  (area {f.area:.1f})" for f in faces]
    default = max(range(len(faces)), key=lambda i: (faces[i].kind == default_kind, faces[i].sign * faces[i].level))
    choice = column.selectbox("Face", labels, index=default, key=f"{title}_face", on_change=reset_chain)
    return faces[labels.index(choice)]


pending = st.session_state.pop("pending_config", None)
if pending:
    def preset(title, spec):
        matches = [p for p in model.parts if p.name.lower() == str(spec.get("part", "")).lower()]
        if not matches:
            st.warning(f"Config part '{spec.get('part')}' is not in this STEP.")
            return
        part = matches[0]
        st.session_state[f"{title}_part"] = f"[{part.index}] {part.name}"
        faces = [f for f in model.faces_of(part.index) if f.kind == spec.get("face", "top")]
        if faces:
            level = spec.get("level")
            face = min(faces, key=lambda f: abs(f.level - level)) if level is not None else max(
                faces, key=lambda f: (f.sign * f.level, f.area))
            st.session_state[f"{title}_face"] = f"{face.kind} @ {face.level:.3f}  (area {face.area:.1f})"

    preset("From (reference)", pending.get("from", {}))
    preset("To", pending.get("to", {}))
    names = {p.name.lower(): f"[{p.index}] {p.name}" for p in model.parts}
    st.session_state.exclude = [names[n.lower()] for n in pending.get("exclude", []) if n.lower() in names]

is_gap = ta_type.startswith("Gap")
# Streamlit forgets a widget that is not drawn: keep the "Show" choice while the 3D view is switched off
st.session_state.view_show = st.session_state.get("view_show", VIEWS[0])
show_3d = st.toggle("3D view of the picked parts and faces", value=True, key="show_3d",
                    help="Follows the Part and Face choices as you change them. Drag to turn, scroll to zoom, "
                         "click a legend entry to hide that part. Switch it off for a very large assembly.")
if show_3d:
    c1, view_column = st.columns([2, 3])
    c2 = c1
    view_show = view_column.radio("Show", VIEWS, horizontal=True, key="view_show")
    view_slot = view_column.empty()
else:
    c1, c2 = st.columns(2)
start = face_picker(c1, "From (reference)", "top")
end = face_picker(c2, "To", "bottom" if is_gap else "top")


def draw_view(chain_parts=()):
    """The 3D picture of the current choice. Called once per run, when the chain is known or can not be found."""
    if not show_3d or start is None or end is None:
        return
    figure, boxed = scene_figure(model.parts, start, end, chain_parts, view_show)
    with view_slot.container():
        st.plotly_chart(figure, key="view_3d", use_container_width=True)
        st.caption("Blue = From, orange / red = To, dashed line = the measured distance along "
                   f"{axis.upper()}. Faces are picked in the lists on the left, not by clicking here."
                   + (f" {boxed} part(s) are drawn as boxes to keep the view fast." if boxed else ""))


exclude = st.multiselect("Parts the chain must NOT pass through (e.g. a big housing that touches everything)",
                         part_names, key="exclude", on_change=reset_chain)
s1, s2 = st.columns(2)
if mode == "Demo assembly" and not st.session_state.get("demo_spec_set"):
    st.session_state.lsl, st.session_state.usl = 1.2, 2.0      # the demo's real requirement
    st.session_state.demo_spec_set = True
st.session_state.setdefault("lsl", None)
st.session_state.setdefault("usl", None)
lsl = s1.number_input("Spec lower limit (LSL, optional)", value=None, format="%.3f", key="lsl",
                      placeholder="no lower limit")
usl = s2.number_input("Spec upper limit (USL, optional)", value=None, format="%.3f", key="usl",
                      placeholder="no upper limit")
if lsl is None and usl is None:
    st.caption("No spec limits yet: you get nominal, worst case and RSS; the verdict and Cpk need a limit.")
if lsl is not None and usl is not None and lsl > usl:
    draw_view()
    st.error("LSL is above USL.")
    st.stop()
if start is None or end is None:
    st.stop()

excluded = {part_index(x) for x in exclude}
options = find_paths(start.part, end.part, model.contacts, excluded - {start.part, end.part})
if not options:
    draw_view()
    st.error(f"No contact chain between {model.parts[start.part].name} and {model.parts[end.part].name}. "
             "Check the axis, raise the contact tolerance, or remove parts from the exclude list. Parts held "
             "only by screws or snaps have no face contact - add them as rows in step 3.")
    st.stop()
option_labels = [f"{i + 1}. {o.describe(model.parts)}" for i, o in enumerate(options)]
picked = st.radio("Chain through the assembly (best ranked first - fewest contacts, then largest contact area)",
                  option_labels, key=f"chain_option_{start.part}_{end.part}_{len(options)}", on_change=reset_chain)
chosen = options[option_labels.index(picked)]
draw_view([index for parent, child, _ in chosen.steps for index in (parent, child)])
if len(options) > 1:
    st.caption(f"{len(options)} possible chains. They all close to the same nominal, but each one adds "
               "different tolerances - pick the one that matches how the parts are really held.")

if st.button("Build chain from 3D", type="primary", key="build"):
    try:
        raw = build_chain(start, end, model.parts, model.contacts, excluded, chosen.steps)
        st.session_state.raw_chain = [asdict(c) for c in raw]
        st.session_state.choices = {}
        st.session_state.drawing_map = resolve_drawing_map(st.session_state.get("config_map"), drawings)
        st.session_state.extra_rows = list(st.session_state.get("config_extra", []))
        st.session_state.faces = (model.label(start), model.label(end))
        st.session_state.chain_text = chosen.describe(model.parts)
        st.session_state.alternatives = [o.describe(model.parts) for o in options if o is not chosen]
        st.session_state.pop("chain", None)
    except ValueError as err:
        st.error(str(err))

if "raw_chain" not in st.session_state:
    st.stop()

# ---------------------------------------------------------------- 3. tolerances from 2D, check / edit the chain
table = [tuple(r) for r in general.dropna().values.tolist()] or DEFAULT_GENERAL_TABLE
if "chain" not in st.session_state:
    contributors = [Contributor(**r) for r in st.session_state.raw_chain]
    for row in st.session_state.get("extra_rows", []):          # rows from a loaded config, or kept on a re-read
        try:
            contributors.append(Contributor(**{"source": "manual", **row}))
        except TypeError as err:
            st.warning(f"Extra row skipped ({err}).")
    found, gdt_found, notes_found = {}, {}, {}
    apply_tolerances(contributors, drawings, table, WORK / "refs", drawing_map=st.session_state.drawing_map,
                     choices=st.session_state.choices, candidates_out=found, ocr=ocr, gdt_out=gdt_found,
                     follow_drawing=follow_drawing, general_out=notes_found)
    st.session_state.candidates = found
    st.session_state.gdt = gdt_found
    st.session_state.general_notes = notes_found
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
    st.session_state.chain = [asdict(c) for c in contributors]

st.header("3. Check the chain")
st.caption(f"From **{st.session_state.faces[0]}**  ->  To **{st.session_state.faces[1]}**.  "
           f"Chain: {st.session_state.chain_text}.  direction +1 = additive, -1 = subtractive.  Result = To - From.")

with st.expander("2D drawings: which drawing belongs to which part, and which dimension was used"):
    names = ["(auto)", "(none)"] + [Path(d).name for d in drawings]
    chain_parts = sorted({r["part"] for r in st.session_state.raw_chain if "/" not in r["part"]})
    new_map = {}
    for part in chain_parts:
        current = st.session_state.drawing_map.get(part)
        current_name = Path(current).name if current else ""
        idx = 0 if current is None else (1 if current == "" else (names.index(current_name) if current_name in names else 0))
        pick = st.selectbox(f"Drawing for {part}", names, index=idx, key=f"map_{part}")
        if pick == "(none)":
            new_map[part] = ""
        elif pick != "(auto)":
            new_map[part] = next(d for d in drawings if Path(d).name == pick)
    new_choices = {}
    for label, hits in st.session_state.candidates.items():
        if len(hits) > 1:
            labels = [h.label() for h in hits]
            current = st.session_state.choices.get(label, 0)
            pick = st.selectbox(f"{label}: dimension used ({len(hits)} candidates)", labels, index=current,
                                key=f"cand_{label}")
            new_choices[label] = labels.index(pick)
    if not drawings:
        st.caption("No drawings loaded - every tolerance comes from the general table.")
    elif not follow_drawing:
        st.caption("General-tolerance notes on the drawings are not used (sidebar: General tolerance).")
    for part, note in st.session_state.get("general_notes", {}).items():
        st.caption(f"General tolerance of {part}: "
                   + (note.label() if note else "no note found on its drawing - the app table is used"))
    if st.button("Re-read tolerances with these choices", key="reread"):
        st.session_state.drawing_map = new_map
        st.session_state.choices = new_choices
        st.session_state.extra_rows = st.session_state.get("current_extra", [])     # keep the rows added by hand
        st.session_state.pop("chain", None)
        st.rerun()

with st.expander("Chain picture (section through the From face)", expanded=True):
    from tolstack.section import render_section

    chain_rows = [Contributor(**r) for r in st.session_state.raw_chain if "contact gap" not in r["source"]]
    chain_idx = []
    for c in chain_rows:
        idx = next((p.index for p in model.parts if p.name == c.part), None)
        if idx is not None and idx not in chain_idx:
            chain_idx.append(idx)
    preview_axis = st.selectbox("Section plane normal", [a for a in "xyz" if a != axis], key="preview_cut")
    try:
        st.image(render_section(str(WORK / "preview.png"), model.parts, chain_idx, start, end, chain_rows,
                                axis, preview_axis))
    except ValueError as err:
        st.caption(f"No section picture: {err}")

st.caption("Edit anything. Add rows for things the CAD cannot see: PCB bow, switch travel, assembly float, glue.")
edited = st.data_editor(
    pd.DataFrame(st.session_state.chain)[["label", "part", "description", "nominal", "tol_plus", "tol_minus",
                                          "direction", "dist", "source", "ref_image"]],
    num_rows="dynamic", use_container_width=True, key=f"editor_{st.session_state.get('editor_version', 0)}",
    column_config={
        "direction": st.column_config.SelectboxColumn("direction", options=[1, -1], required=True),
        "dist": st.column_config.SelectboxColumn("dist", options=list(DISTRIBUTIONS), required=True),
        "nominal": st.column_config.NumberColumn("nominal", format="%.4f"),
        "tol_plus": st.column_config.NumberColumn("+tol", min_value=0.0, format="%.4f"),
        "tol_minus": st.column_config.NumberColumn("-tol", min_value=0.0, format="%.4f"),
        "ref_image": st.column_config.TextColumn("2D evidence", disabled=True),
    },
)
frame = edited.copy()
for column in NUMERIC:
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
bad = frame[frame["label"].isna() | (frame["label"].astype(str).str.strip() == "") | frame[NUMERIC].isna().any(axis=1)]
if len(bad):
    st.warning(f"{len(bad)} row(s) skipped: every row needs a label, a nominal and both tolerances.")
frame = frame.drop(bad.index)
frame["direction"] = pd.to_numeric(frame["direction"], errors="coerce").fillna(1).astype(int)
frame = frame.fillna({"ref_image": "", "source": "manual", "description": "", "part": "", "dist": "normal"})
contributors = [Contributor(**{**r, "label": str(r["label"])}) for r in frame.to_dict("records")]
if not contributors:
    st.stop()
st.session_state.current_extra = [{k: v for k, v in asdict(c).items() if k != "ref_image"}
                                  for c in contributors if is_extra(c)]

gdt_hits = [(part, hit) for part, hits in st.session_state.get("gdt", {}).items() for hit in hits]
if gdt_hits:
    with st.expander(f"GD&T on the drawings: {len(gdt_hits)} callout(s) found - not in the stack unless you add them"):
        st.caption("A zone of total width t is added as a row with nominal 0 and ± t/2. Add a callout only when "
                   "no size tolerance in the chain already covers it. Under ASME Y14.5 Rule #1 (envelope "
                   "principle) form tolerances such as flatness lie inside the size limits, so adding them counts "
                   "the same variation twice. They do add under ISO 8015 independency, and so do datum-related "
                   "orientation, profile and runout callouts on a chain face. Callouts are read from the text "
                   "of the drawing - check each one against the drawing.")
        used = {(c.part, c.source) for c in contributors}
        gdt_labels = [f"{i + 1}. {part}: {hit.label()}" for i, (part, hit) in enumerate(gdt_hits)]
        suggested = [label for label, (part, hit) in zip(gdt_labels, gdt_hits, strict=True)
                     if hit.along_axis and (part, gdt_contributor(hit, part, "").source) not in used]
        gdt_picked = st.multiselect("Callouts to add (preselected: zones that can act along the stack axis)",
                                    gdt_labels, default=suggested, key=f"gdt_pick_{len(used)}")
        if st.button("Add as rows", key="gdt_add", disabled=not gdt_picked):
            count = sum(1 for c in contributors if c.source.startswith("GD&T"))
            rows = [asdict(c) for c in contributors]
            for label in gdt_picked:
                part, hit = gdt_hits[gdt_labels.index(label)]
                count += 1
                rows.append(asdict(gdt_contributor(hit, part, f"G{count}")))
            st.session_state.chain = rows
            st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
            st.rerun()

# ---------------------------------------------------------------- 4. result
try:
    result = analyze(contributors, lsl, usl, sigma_level=sigma_level)
except ValueError as err:
    st.error(str(err))
    st.stop()
st.header("4. Result")
has_spec = lsl is not None or usl is not None
m = st.columns(3)
m[0].metric("Nominal", f"{result.nominal:+.3f}", help=f"mean with asymmetric tolerances {result.mean:+.3f}")
m[1].metric("Worst case", f"{result.wc_min:+.3f} ~ {result.wc_max:+.3f}",
            help=f"WC verdict {result.verdict('wc') if has_spec else 'no spec'}")
m[2].metric(f"RSS ({sigma_level:g}σ)", f"{result.rss_min:+.3f} ~ {result.rss_max:+.3f}")
m = st.columns(3)
m[0].metric("RSS verdict", result.verdict("rss") if has_spec else "no spec")
m[1].metric("Cpk", "-" if result.cpk is None else f"{result.cpk:.2f}",
            help="Cpk >= 1.33 is a common target. From the mean and sigma of the sum."
            + ("" if result.yield_pct is None else f" Predicted yield {result.yield_pct:.4f} %."))
m[2].metric("Monte Carlo out of spec", f"{result.mc_out_pct:.3f} %" if has_spec else "-",
            help=f"{result.mc_samples:,} samples, per-row distributions. Normal approximation: {result.ppm:.0f} ppm"
            if result.ppm is not None else None)
st.bar_chart(pd.DataFrame({"variance contribution %": result.contributions}, index=[c.label for c in contributors]))
with st.expander("Sensitivity: which tolerance to tighten first"):
    tornado = pd.DataFrame(sensitivity(contributors, result)).drop(columns="index")
    tornado["gain"] = result.rss - tornado["rss_without"]
    tornado.columns = ["Item", "Part", "Low", "High", "Swing", "Variance %", "Worst-case %", "RSS ± without", "Gain"]
    st.dataframe(tornado, hide_index=True, use_container_width=True,
                 column_config={name: st.column_config.NumberColumn(name, format="%.1f" if "%" in name else "%.3f")
                                for name in tornado.columns[2:]})
    st.caption("Low / High: shift of the result with that row alone at its lower / upper limit. "
               f"RSS ± without: the RSS band (now ± {result.rss:.3f}) if the row had no tolerance; Gain is the "
               "difference - the most that tightening this one row can buy.")
for c in contributors:
    if c.ref_image and Path(c.ref_image).exists():
        with st.expander(f"2D evidence {c.label} - {c.part}: {c.source}"):
            st.image(c.ref_image)

# ---------------------------------------------------------------- 5. export
st.header("5. Excel report")
e1, e2, e3 = st.columns(3)
author = e1.text_input("Prepared by", "")
project = e2.text_input("Project", "")
cut_axis = st.session_state.get("preview_cut", [a for a in "xyz" if a != axis][0])
e3.caption(f"Section plane normal: {cut_axis.upper()} (set in step 3)")
with_section = st.checkbox("Add colour section picture", value=True)
if st.button("Create Excel", key="excel"):
    section_png = None
    if with_section:
        from tolstack.section import render_section

        chain_parts = []
        for c in contributors:
            idx = next((p.index for p in model.parts if p.name == c.part), None)
            if idx is not None and idx not in chain_parts:
                chain_parts.append(idx)
        try:
            section_png = render_section(str(WORK / "section.png"), model.parts, chain_parts, start, end,
                                         [c for c in contributors if not is_extra(c) and "contact gap" not in c.source],
                                         axis, cut_axis)
            st.image(section_png)
        except ValueError as err:
            st.warning(f"section skipped: {err}")
    meta = {"title": "Tolerance stack-up", "project": project, "name": ta_name, "author": author,
            "type": "gap" if is_gap else "step", "axis": axis, "sigma": sigma_level,
            "from": st.session_state.faces[0], "to": st.session_state.faces[1], "step": str(step_path),
            "contact_tol": contact_tol, "general_table": table, "chain": st.session_state.chain_text,
            "alternatives": st.session_state.alternatives,
            "units": " + ".join(model.units) or "not declared",
            "warnings": [model.unit_note] if model.unit_note else [],
            "gdt": [f"{part}: {hit.label()}" for part, hit in gdt_hits],
            "general_notes": [f"{part}: {note.label()}"
                              for part, note in st.session_state.get("general_notes", {}).items() if note]}
    out = write_report(WORK / f"{safe_name(ta_name)}.xlsx", contributors, result, meta, section_png)
    st.download_button("Download Excel", Path(out).read_bytes(), file_name=Path(out).name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if not portable:
    paths = {"step": str(step_path), "drawing_map": st.session_state.drawing_map}
    if drawing_globs:
        paths["drawings"] = drawing_globs
else:                       # uploaded files live in a temporary folder: write names, not paths
    paths = portable_paths(step_path, st.session_state.drawing_map, drawings)
config = {
    "version": CONFIG_VERSION, "name": ta_name, "type": "gap" if is_gap else "step", "author": author,
    **paths, "axis": axis,
    "from": {"part": model.parts[start.part].name, "face": start.kind, "level": start.level},
    "to": {"part": model.parts[end.part].name, "face": end.kind, "level": end.level},
    "exclude": [model.parts[i].name for i in sorted(excluded)],
    "chain_option": option_labels.index(picked) + 1, "contact_tol": contact_tol, "sigma_level": sigma_level,
    "spec": {"lsl": lsl, "usl": usl}, "general_from_drawing": follow_drawing,
    "general_tolerance": [list(r) for r in table],
    "extra": st.session_state.current_extra,
    "section": {"cut_axis": cut_axis}, "output": f"{safe_name(ta_name)}.xlsx",
}
st.download_button("Download this analysis as a config (re-run with `python -m tolstack run`)",
                   yaml.safe_dump(config, sort_keys=False, allow_unicode=True), file_name="tolstack_config.yaml",
                   key="config_download")
if portable:
    st.caption(f"The config names the files instead of pointing at this server. To run it again, keep it in one "
               f"folder with `{paths['step']}` and the PDFs in a `drawings` sub-folder, then "
               "`python -m tolstack run tolstack_config.yaml` - or load it here and upload the same files.")
