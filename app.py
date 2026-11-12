"""tolstack web app:  streamlit run app.py"""
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from tolstack.chain import build_chain
from tolstack.excel import write_report
from tolstack.pipeline import apply_tolerances, expand_drawings, load_model
from tolstack.stack import Contributor, analyze
from tolstack.tolerances import DEFAULT_GENERAL_TABLE

st.set_page_config(page_title="tolstack - tolerance stack-up", layout="wide")
st.title("tolstack - tolerance stack-up from 3D + 2D")
st.caption("Load a STEP assembly, pick the two faces of the gap / step, check the chain, export to Excel.")

WORK = Path(tempfile.gettempdir()) / "tolstack_app"
WORK.mkdir(exist_ok=True)


@st.cache_resource(show_spinner="Reading STEP and finding contacts...")
def cached_model(step_path: str, axis: str, mtime: float):
    return load_model(step_path, axis)


# ---------------------------------------------------------------- 1. input files
with st.sidebar:
    st.header("1. Files")
    mode = st.radio("Input", ["Upload files", "Path on this computer"], horizontal=True)
    step_path, drawings = None, []
    if mode == "Upload files":
        step_file = st.file_uploader("3D assembly (STEP)", type=["step", "stp"])
        pdf_files = st.file_uploader("2D drawings (PDF, optional)", type=["pdf"], accept_multiple_files=True)
        if step_file:
            step_path = WORK / step_file.name
            step_path.write_bytes(step_file.getbuffer())
        for f in pdf_files or []:
            p = WORK / "drawings" / f.name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(f.getbuffer())
            drawings.append(str(p))
    else:
        typed = st.text_input("STEP file path")
        folder = st.text_input("2D drawings folder (optional)")
        if typed and Path(typed.strip('"')).exists():
            step_path = Path(typed.strip('"'))
        elif typed:
            st.error("STEP file not found")
        if folder:
            drawings = expand_drawings([str(Path(folder.strip('"')) / "**" / "*.pdf")], Path("."))
            drawings += expand_drawings([str(Path(folder.strip('"')) / "*.pdf")], Path("."))
            drawings = sorted(set(drawings))
            st.caption(f"{len(drawings)} PDF drawings found")
    axis = st.selectbox("Stack-up direction (axis)", ["z", "y", "x"])
    st.header("General tolerance")
    general = st.data_editor(pd.DataFrame(DEFAULT_GENERAL_TABLE, columns=["from mm", "to mm", "± tol"]),
                             num_rows="dynamic", key="general")

if step_path is None:
    st.info("Upload a STEP assembly or type its path in the sidebar to start. "
            "Try `examples/demo/demo_assembly.step` after running `python examples/make_demo.py`.")
    st.stop()

model = cached_model(str(step_path), axis, Path(step_path).stat().st_mtime)
st.success(f"{len(model.parts)} parts, {len(model.faces)} faces normal to {axis.upper()}, "
           f"{len(model.contacts)} part-to-part contacts")

# ---------------------------------------------------------------- 2. choose the analysis
st.header("2. What to analyze")
c1, c2 = st.columns(2)
ta_type = c1.radio("Type", ["Step (offset / flush between two faces)", "Gap (clearance between two faces)"])
ta_name = c2.text_input("Analysis name", "Step A vs B" if ta_type.startswith("Step") else "Gap A to B")
part_names = [f"[{p.index}] {p.name}" for p in model.parts]


def face_picker(column, title, default_kind):
    column.subheader(title)
    part_label = column.selectbox("Part", part_names, key=f"{title}_part")
    index = int(part_label[1:part_label.index("]")])
    faces = model.faces_of(index)
    if not faces:
        column.warning("this part has no planar face normal to the axis")
        return None
    labels = [f"{f.kind} @ {f.level:.3f}  (area {f.area:.1f})" for f in faces]
    default = max(range(len(faces)), key=lambda i: (faces[i].kind == default_kind, faces[i].sign * faces[i].level))
    choice = column.selectbox("Face", labels, index=default, key=f"{title}_face")
    return faces[labels.index(choice)]


c1, c2 = st.columns(2)
is_gap = ta_type.startswith("Gap")
start = face_picker(c1, "From (reference)", "top")
end = face_picker(c2, "To", "bottom" if is_gap else "top")
exclude = st.multiselect("Parts the chain must NOT pass through (e.g. a big housing that touches everything)",
                         part_names)
s1, s2 = st.columns(2)
lsl = s1.number_input("Spec lower limit (LSL)", value=0.0 if is_gap else -0.5, format="%.3f")
usl = s2.number_input("Spec upper limit (USL)", value=1.0 if is_gap else 0.5, format="%.3f")

if st.button("Build chain from 3D", type="primary") and start and end:
    try:
        excluded = {int(x[1:x.index("]")]) for x in exclude}
        chain = build_chain(start, end, model.parts, model.contacts, excluded)
        table = [tuple(r) for r in general.dropna().values.tolist()]
        apply_tolerances(chain, drawings, table, WORK / "refs")
        st.session_state.chain = [asdict(c) for c in chain]
        st.session_state.faces = (model.label(start), model.label(end))
    except ValueError as err:
        st.error(str(err))

if "chain" not in st.session_state:
    st.stop()

# ---------------------------------------------------------------- 3. check / edit the chain
st.header("3. Check the chain (edit anything, add rows for bow, gaps, switch travel...)")
st.caption(f"From **{st.session_state.faces[0]}**  ->  To **{st.session_state.faces[1]}**.  "
           "direction +1 = additive, -1 = subtractive.  Result = To - From.")
edited = st.data_editor(
    pd.DataFrame(st.session_state.chain)[["label", "part", "description", "nominal", "tol_plus", "tol_minus",
                                          "direction", "source", "ref_image"]],
    num_rows="dynamic", use_container_width=True, key="editor",
    column_config={"ref_image": st.column_config.TextColumn("2D evidence", disabled=True)},
)
rows = edited.fillna({"ref_image": "", "source": "", "description": ""}).to_dict("records")
contributors = [Contributor(**{**r, "direction": int(r["direction"])}) for r in rows if r.get("label")]

# ---------------------------------------------------------------- 4. result
result = analyze(contributors, lsl, usl)
st.header("4. Result")
m = st.columns(5)
m[0].metric("Nominal", f"{result.nominal:+.3f}")
m[1].metric("Worst case", f"{result.wc_min:+.3f} ~ {result.wc_max:+.3f}")
m[2].metric("RSS (3σ)", f"{result.rss_min:+.3f} ~ {result.rss_max:+.3f}")
m[3].metric("RSS verdict", result.verdict("rss"))
m[4].metric("Monte Carlo out of spec", f"{result.mc_out_pct:.2f} %")
st.bar_chart(pd.DataFrame({"contribution %": result.contributions}, index=[c.label for c in contributors]))
for c in contributors:
    if c.ref_image and Path(c.ref_image).exists():
        with st.expander(f"2D evidence {c.label} - {c.part}: {c.source}"):
            st.image(c.ref_image)

# ---------------------------------------------------------------- 5. export
st.header("5. Excel report")
with_section = st.checkbox("Add colour section picture", value=True)
cut_axis = st.selectbox("Section plane normal", [a for a in "xyz" if a != axis])
if st.button("Create Excel"):
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
                                         [c for c in contributors if c.source != "manual"], axis, cut_axis)
            st.image(section_png)
        except ValueError as err:
            st.warning(f"section skipped: {err}")
    meta = {"title": "Tolerance stack-up", "name": ta_name, "type": "gap" if is_gap else "step", "axis": axis,
            "from": st.session_state.faces[0], "to": st.session_state.faces[1]}
    out = write_report(WORK / f"{ta_name.replace(' ', '_')}.xlsx", contributors, result, meta, section_png)
    st.download_button("Download Excel", Path(out).read_bytes(), file_name=Path(out).name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
