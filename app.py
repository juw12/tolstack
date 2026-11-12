"""tolstack web app:  streamlit run app.py"""
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from tolstack.chain import build_chain, find_paths
from tolstack.excel import write_report
from tolstack.pipeline import CONFIG_VERSION, apply_tolerances, expand_drawings, load_model, validate_config
from tolstack.stack import DISTRIBUTIONS, Contributor, analyze
from tolstack.tolerances import DEFAULT_GENERAL_TABLE, tesseract_available

st.set_page_config(page_title="tolstack - tolerance stack-up", layout="wide")
st.title("tolstack - tolerance stack-up from 3D + 2D")
st.caption("1 Load a STEP assembly  ->  2 pick the two faces of the gap / step  ->  3 check the chain  "
           "->  4 read the result  ->  5 export Excel.")

WORK = Path(tempfile.gettempdir()) / "tolstack_app"
WORK.mkdir(exist_ok=True)
NUMERIC = ["nominal", "tol_plus", "tol_minus"]
TYPES = ["Step (offset / flush between two faces)", "Gap (clearance between two faces)"]


@st.cache_resource(show_spinner="Reading STEP and finding contacts...")
def cached_model(step_path: str, axis: str, contact_tol: float, mtime: float):
    return load_model(step_path, axis, contact_tol)


@st.cache_resource(show_spinner="Writing the demo assembly...")
def demo_files():
    from tolstack.demo import write_demo

    step = write_demo(WORK / "demo")
    return str(step), sorted(str(p) for p in (WORK / "demo" / "drawings").glob("*.pdf"))


def reset_chain():
    for key in ("chain", "raw_chain", "candidates", "choices"):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------- 1. input files
with st.sidebar:
    saved = st.file_uploader("Load a saved analysis (YAML config)", type=["yaml", "yml"],
                             help="A config downloaded from this app or written for `tolstack run`.")
    if saved is not None and st.session_state.get("loaded_config_id") != saved.file_id:
        try:
            loaded = yaml.safe_load(saved.getvalue().decode("utf-8")) or {}
            validate_config(loaded)
            st.session_state.loaded_config_id = saved.file_id
            st.session_state.input_mode = "Path on this computer"
            st.session_state.step_input = str(loaded["step"])
            globs = loaded.get("drawings") or []
            if globs:
                st.session_state.drawings_input = str(Path(globs[0]).parent)
            st.session_state.axis = loaded.get("axis", "z")
            st.session_state.contact_tol = float(loaded.get("contact_tol", 0.02))
            st.session_state.sigma_level = float(loaded.get("sigma_level", 3.0))
            spec = loaded.get("spec") or {}
            if spec.get("lsl") is not None:
                st.session_state.lsl = float(spec["lsl"])
            if spec.get("usl") is not None:
                st.session_state.usl = float(spec["usl"])
            st.session_state.ta_type = TYPES[1] if loaded.get("type") == "gap" else TYPES[0]
            st.session_state.pending_config = loaded
            reset_chain()
            st.success("Config loaded - check the faces, then build the chain.")
        except (ValueError, KeyError, yaml.YAMLError) as err:
            st.error(f"Could not load this config: {err}")

    st.header("1. Files")
    mode = st.radio("Input", ["Upload files", "Path on this computer", "Demo assembly"], horizontal=False,
                    key="input_mode",
                    help="The demo is a 5-part box stack with 2D drawings - a quick way to see every step.")
    step_path, drawings = None, []
    if mode == "Upload files":
        step_file = st.file_uploader("3D assembly (STEP)", type=["step", "stp"])
        pdf_files = st.file_uploader("2D drawings (PDF, optional)", type=["pdf"], accept_multiple_files=True,
                                     help="Matched to parts by file name, e.g. SPACER.pdf -> part SPACER.")
        if step_file:
            step_path = WORK / step_file.name
            step_path.write_bytes(step_file.getbuffer())
        for f in pdf_files or []:
            p = WORK / "drawings" / f.name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(f.getbuffer())
            drawings.append(str(p))
    elif mode == "Path on this computer":
        typed = st.text_input("STEP file path", key="step_input")
        folder = st.text_input("2D drawings folder (optional)", key="drawings_input")
        if typed and Path(typed.strip('"')).is_file():
            step_path = Path(typed.strip('"'))
        elif typed:
            st.error("STEP file not found - check the path (quotes are fine).")
        if folder:
            root = Path(folder.strip('"'))
            if not root.is_dir():
                st.error("Drawings folder not found.")
            else:
                drawings = sorted(set(expand_drawings([str(root / "**" / "*.pdf"), str(root / "*.pdf")], Path("."))))
                st.caption(f"{len(drawings)} PDF drawings found")
    else:
        demo_step, drawings = demo_files()
        step_path = Path(demo_step)
        st.caption("Demo: pick POST top -> CAP_TOP top for the +1.6 step.")

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
    general = st.data_editor(pd.DataFrame(DEFAULT_GENERAL_TABLE, columns=["from mm", "to mm", "± tol"]),
                             num_rows="dynamic", key="general")

if step_path is None:
    st.info("Upload a STEP assembly, type its path, or choose **Demo assembly** in the sidebar to start.")
    st.stop()

try:
    model = cached_model(str(step_path), axis, float(contact_tol), Path(step_path).stat().st_mtime)
except Exception as err:  # noqa: BLE001 - show any CAD error as a message, not a traceback
    st.error(f"Could not read this STEP file: {err}")
    st.caption("tolstack needs an assembly STEP (AP203/AP214/AP242) with solid bodies.")
    st.stop()
st.success(f"{len(model.parts)} parts, {len(model.faces)} faces normal to {axis.upper()}, "
           f"{len(model.contacts)} part-to-part contacts (true face outlines, tolerance {contact_tol:g} mm)")

# ---------------------------------------------------------------- 2. choose the analysis
st.header("2. What to analyze")
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

c1, c2 = st.columns(2)
is_gap = ta_type.startswith("Gap")
start = face_picker(c1, "From (reference)", "top")
end = face_picker(c2, "To", "bottom" if is_gap else "top")
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
    st.error("LSL is above USL.")
    st.stop()
if start is None or end is None:
    st.stop()

excluded = {part_index(x) for x in exclude}
options = find_paths(start.part, end.part, model.contacts, excluded - {start.part, end.part})
if not options:
    st.error(f"No contact chain between {model.parts[start.part].name} and {model.parts[end.part].name}. "
             "Check the axis, raise the contact tolerance, or remove parts from the exclude list. Parts held "
             "only by screws or snaps have no face contact - add them as rows in step 3.")
    st.stop()
option_labels = [f"{i + 1}. {o.describe(model.parts)}" for i, o in enumerate(options)]
picked = st.radio("Chain through the assembly (best ranked first - fewest contacts, then largest contact area)",
                  option_labels, key=f"chain_option_{start.part}_{end.part}_{len(options)}", on_change=reset_chain)
chosen = options[option_labels.index(picked)]
if len(options) > 1:
    st.caption(f"{len(options)} possible chains. They all close to the same nominal, but each one adds "
               "different tolerances - pick the one that matches how the parts are really held.")

if st.button("Build chain from 3D", type="primary", key="build"):
    try:
        raw = build_chain(start, end, model.parts, model.contacts, excluded, chosen.steps)
        st.session_state.raw_chain = [asdict(c) for c in raw]
        st.session_state.choices = {}
        st.session_state.drawing_map = {}
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
    found = {}
    apply_tolerances(contributors, drawings, table, WORK / "refs", drawing_map=st.session_state.drawing_map,
                     choices=st.session_state.choices, candidates_out=found, ocr=ocr)
    st.session_state.candidates = found
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
        idx = 0 if current is None else (1 if current == "" else names.index(Path(current).name))
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
    if st.button("Re-read tolerances with these choices", key="reread"):
        st.session_state.drawing_map = new_map
        st.session_state.choices = new_choices
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
    num_rows="dynamic", use_container_width=True, key="editor",
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
                                         [c for c in contributors if c.source != "manual" and "contact gap" not in c.source],
                                         axis, cut_axis)
            st.image(section_png)
        except ValueError as err:
            st.warning(f"section skipped: {err}")
    meta = {"title": "Tolerance stack-up", "project": project, "name": ta_name, "author": author,
            "type": "gap" if is_gap else "step", "axis": axis, "sigma": sigma_level,
            "from": st.session_state.faces[0], "to": st.session_state.faces[1], "step": str(step_path),
            "contact_tol": contact_tol, "general_table": table, "chain": st.session_state.chain_text,
            "alternatives": st.session_state.alternatives}
    out = write_report(WORK / f"{ta_name.replace(' ', '_')}.xlsx", contributors, result, meta, section_png)
    st.download_button("Download Excel", Path(out).read_bytes(), file_name=Path(out).name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

config = {
    "version": CONFIG_VERSION, "name": ta_name, "type": "gap" if is_gap else "step", "author": author,
    "step": str(step_path), "axis": axis,
    "from": {"part": model.parts[start.part].name, "face": start.kind, "level": start.level},
    "to": {"part": model.parts[end.part].name, "face": end.kind, "level": end.level},
    "exclude": [model.parts[i].name for i in sorted(excluded)],
    "chain_option": option_labels.index(picked) + 1, "contact_tol": contact_tol, "sigma_level": sigma_level,
    "spec": {"lsl": lsl, "usl": usl}, "general_tolerance": [list(r) for r in table],
    "drawing_map": st.session_state.drawing_map,
    "extra": [{k: v for k, v in asdict(c).items() if k != "ref_image"} for c in contributors if c.source == "manual"],
    "section": {"cut_axis": cut_axis}, "output": f"{ta_name.replace(' ', '_')}.xlsx",
}
st.download_button("Download this analysis as a config (re-run with `python -m tolstack run`)",
                   yaml.safe_dump(config, sort_keys=False, allow_unicode=True), file_name="tolstack_config.yaml",
                   key="config_download")
