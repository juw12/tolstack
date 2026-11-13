"""End-to-end run: STEP (+2D PDFs) -> chain -> tolerances -> analysis -> Excel."""
from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .cad import Part, PlanarFace, describe_face, load_step, planar_faces, step_length_units, unit_note
from .chain import Contact, build_chain, find_contacts, find_paths
from .stack import Contributor, Result, analyze, is_extra
from .tolerances import (
    DEFAULT_GENERAL_TABLE,
    DimensionHit,
    GdtHit,
    GeneralNote,
    crop_hit,
    find_dimension,
    find_gdt,
    find_general_note,
    general_tolerance,
    match_pdf,
    pdf_has_text,
)

CONFIG_VERSION = 1
CONFIG_KEYS = {
    "version", "title", "project", "name", "type", "author", "step", "axis", "from", "to",
    "exclude", "spec", "drawings", "drawing_map", "general_tolerance", "extra", "overrides",
    "section", "output", "contact_tol", "chain_option", "sigma_level", "mc_samples", "ocr",
    "general_from_drawing",
}


@dataclass
class Model:
    step: str
    axis: str
    parts: list[Part]
    faces: list[PlanarFace]
    contacts: list[Contact]
    contact_tol: float = 0.02
    units: tuple[str, ...] = ("mm",)     # length units declared in the STEP file

    @property
    def unit_note(self) -> str | None:
        """Warning text when the STEP file is not plainly in millimetres, else None."""
        return unit_note(self.units)

    def faces_of(self, part_index: int) -> list[PlanarFace]:
        return sorted((f for f in self.faces if f.part == part_index), key=lambda f: (f.level, f.sign))

    def label(self, face: PlanarFace) -> str:
        return describe_face(face, self.parts)


def load_model(step_path: str, axis: str = "z", contact_tol: float = 0.02,
               parts: list[Part] | None = None) -> Model:
    """Faces normal to `axis` and contacts of a STEP assembly.

    parts   the solids of that STEP when already read (load_step), so a new axis or contact tolerance does
            not read a large file again; read here when None.
    """
    parts = load_step(step_path) if parts is None else parts
    faces = [f for p in parts for f in planar_faces(p, axis)]
    if not parts:
        raise ValueError(f"no solids found in {step_path}; is it an assembly STEP (AP203/AP214/AP242)?")
    if not faces:
        raise ValueError(f"no planar faces normal to {axis.upper()} - try another stack-up axis")
    return Model(str(step_path), axis, parts, faces, find_contacts(faces, tol=contact_tol), contact_tol,
                 step_length_units(step_path))


def find_part(model: Model, query: str) -> Part:
    q = query.lower()
    exact = [p for p in model.parts if p.name.lower() == q]
    matches = exact or [p for p in model.parts if q in p.name.lower() or q in p.path.lower()]
    if not matches:
        raise ValueError(f"no part matches '{query}'")
    return matches[0]


def find_face(model: Model, part_query: str, kind: str = "top", level: float | None = None) -> PlanarFace:
    part = find_part(model, part_query)
    sign = 1 if kind == "top" else -1
    faces = [f for f in model.faces_of(part.index) if f.sign == sign]
    if not faces:
        raise ValueError(f"part '{part.name}' has no {kind} face normal to {model.axis}")
    if level is not None:
        return min(faces, key=lambda f: abs(f.level - level))
    # default: the outermost face of that kind, largest area as tie-break
    return max(faces, key=lambda f: (sign * f.level, f.area))


def apply_tolerances(contributors: list[Contributor], drawings: list[str] | None = None,
                     table=DEFAULT_GENERAL_TABLE, ref_dir: str | Path | None = None,
                     window: float = 0.03, drawing_map: dict | None = None, choices: dict | None = None,
                     candidates_out: dict | None = None, ocr: bool = False,
                     gdt_out: dict | None = None, follow_drawing: bool = True,
                     general_out: dict | None = None) -> list[Contributor]:
    """Fill tolerances: 2D dimension with tolerance if found, else the general tolerance.

    The general tolerance is the one written on the part's own drawing ("TOLERANCES UNLESS
    SPECIFIED ...", ISO 2768-m, X.XX ±0.05); `table` is used when the drawing has no such note,
    when the note does not cover the size, or when `follow_drawing` is False.

    drawing_map    {part name: pdf path} overrides the automatic file-name match ("" = no drawing)
    choices        {label: candidate index} picks another 2D hit than the best ranked one
    candidates_out filled with {label: [DimensionHit, ...]} so a UI can show the alternatives
    gdt_out        filled with {part name: [GdtHit, ...]} - GD&T callouts on the drawings of the chain
                   parts. They are reported only; see `gdt_contributor` to add one as a row.
    general_out    filled with {part name: GeneralNote or None} - the general-tolerance note read
                   from the drawing of each chain part that has one
    The source column always says where the tolerance came from; when the 3D nominal differs
    from the drawing nominal, the drawing limits are kept and the difference is noted.
    """
    drawings = drawings or []
    drawing_map = drawing_map or {}
    choices = choices or {}
    text_cache: dict[str, bool] = {}
    note_cache: dict[str, GeneralNote | None] = {}
    for c in contributors:
        pdf = drawing_map[c.part] if c.part in drawing_map else match_pdf(c.part, list(drawings))
        if gdt_out is not None and pdf and c.part not in gdt_out and not is_extra(c):
            gdt_out[c.part] = find_gdt(pdf, ocr=ocr)
        general = None
        if pdf and follow_drawing and not is_extra(c):
            if pdf not in note_cache:
                note_cache[pdf] = find_general_note(pdf, ocr=ocr)
            general = note_cache[pdf]
            if general_out is not None:
                general_out.setdefault(c.part, general)
        if c.tol_plus or c.tol_minus:
            continue  # already set by the user
        pdf_name = Path(pdf).name if pdf else ""
        hits: list[DimensionHit] = []
        no_text = False
        if pdf:
            if pdf not in text_cache:
                text_cache[pdf] = pdf_has_text(pdf)
            no_text = not text_cache[pdf]
            if not no_text or ocr:
                hits = find_dimension(pdf, c.nominal, window, ocr=ocr)
        if candidates_out is not None:
            candidates_out[c.label] = hits
        pick = choices.get(c.label, 0)
        hit = hits[pick] if 0 <= pick < len(hits) else (hits[0] if hits else None)
        count = f" [{pick + 1}/{len(hits)} candidates]" if len(hits) > 1 else ""
        if hit is not None and hit.has_tolerance:
            c.tol_plus, c.tol_minus = (round(v, 6) for v in hit.relative_to(c.nominal))
            note = ""
            if hit.kind != "limit" and abs(hit.value - c.nominal) > 1e-4:
                note = f" (3D {c.nominal:g} vs 2D {hit.value:g}: drawing limits kept)"
            c.source = f"2D {pdf_name} p{hit.page + 1}: {hit.text}{note}{count}"
        else:
            from_note = general.tolerance(c.nominal, hit.text if hit is not None else None) if general else None
            t = general_tolerance(c.nominal, table) if from_note is None else from_note
            c.tol_plus = c.tol_minus = t
            if hit is not None:
                where = f"2D {pdf_name}: {hit.text} (no tol){count} -> "
            elif no_text:
                where = f"2D {pdf_name} has no text layer (vector text) - check it by eye -> "
            elif pdf:
                where = f"2D {Path(pdf).name}: not dimensioned -> "
            else:
                where = "no 2D -> "
            if from_note is not None and general is not None:
                c.source = f"{where}general tol from {pdf_name} ({general.kind}) ±{t}"
            elif general is not None:
                c.source = f"{where}general tol ±{t} (app table: drawing note does not cover this size)"
            else:
                c.source = f"{where}general tol ±{t}"
        if hit is not None and ref_dir:
            Path(ref_dir).mkdir(parents=True, exist_ok=True)
            c.ref_image = crop_hit(hit, Path(ref_dir) / f"ref_{c.label}.png")
    return contributors


def gdt_contributor(hit: GdtHit, part: str, label: str) -> Contributor:
    """A stack row for one GD&T callout: a zone of total width t moves the surface by ± t/2."""
    half = round(hit.value / 2, 6)
    return Contributor(label, part, f"{hit.kind} {hit.value:g} (zone width -> ± half)", 0.0, half, half, 1,
                       source=f"GD&T {Path(hit.pdf).name} p{hit.page + 1}: {hit.text}")


def _file_name(path: str | Path) -> str:
    """File name of a path written on any system: a config saved on Windows is also read on a Linux server."""
    return PureWindowsPath(str(path)).name        # splits on both \ and /


def portable_paths(step_path: str | Path, drawing_map: dict | None, drawings: list[str]) -> dict:
    """Config entries that do not depend on this computer: the STEP by file name, drawings in `drawings/`.

    Used when the files were uploaded (they live in a temporary folder): put the config next to the
    STEP, the PDFs in a `drawings` folder beside it, and `tolstack run` - or the app - finds them again.
    """
    out: dict = {"step": _file_name(step_path)}
    if drawings:
        out["drawings"] = ["drawings/*.pdf"]
    out["drawing_map"] = {part: (f"drawings/{_file_name(pdf)}" if pdf else "")
                          for part, pdf in (drawing_map or {}).items()}
    return out


def resolve_drawing_map(drawing_map: dict | None, drawings: list[str]) -> dict:
    """Match a config's drawing_map to the drawings at hand by file name ("" = no drawing, kept as is).

    Entries whose file is not among `drawings` are dropped, so the automatic name match takes over.
    """
    by_name = {_file_name(d).lower(): d for d in drawings}
    out = {}
    for part, pdf in (drawing_map or {}).items():
        if not pdf:
            out[part] = ""
        elif _file_name(pdf).lower() in by_name:
            out[part] = by_name[_file_name(pdf).lower()]
    return out


def validate_config(config: dict) -> list[str]:
    """Raise on errors that would give a wrong result; return warnings for the rest."""
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unknown config keys {unknown}; allowed: {sorted(CONFIG_KEYS)}")
    version = config.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ValueError(f"config version {version} is not supported (this tolstack reads version {CONFIG_VERSION})")
    for key in ("step", "from", "to"):
        if key not in config:
            raise ValueError(f"config needs '{key}'")
    if config.get("axis", "z") not in ("x", "y", "z"):
        raise ValueError("axis must be x, y or z")
    warnings = []
    if "version" not in config:
        warnings.append(f"config has no 'version'; assuming {CONFIG_VERSION}")
    spec = config.get("spec", {})
    if spec.get("lsl") is None and spec.get("usl") is None:
        warnings.append("no spec limits: verdict, Cp/Cpk and out-of-spec % are not meaningful")
    return warnings


def expand_drawings(patterns: list[str] | None, base: Path) -> list[str]:
    out = []
    for pattern in patterns or []:
        p = Path(pattern)
        if not p.is_absolute():
            p = base / p
        out.extend(sorted(glob.glob(str(p), recursive=True)))      # "drawings/**/*.pdf" = at any depth
    return list(dict.fromkeys(out))                                 # "*.pdf" and "**/*.pdf" overlap


def run_config(config: dict, base: str | Path = ".",
               notes: list[str] | None = None) -> tuple[list[Contributor], Result, str]:
    """Run an analysis described by a dict (see examples/demo_config.yaml).

    `notes`, when given, is filled with the warnings and the GD&T callouts found on the drawings,
    the same lines that go to the Excel Assumptions sheet.
    """
    from .excel import write_report

    warnings = validate_config(config)
    base = Path(base)
    step = Path(config["step"])
    if not step.is_absolute():
        step = base / step
    axis = config.get("axis", "z")
    model = load_model(str(step), axis, float(config.get("contact_tol", 0.02)))
    if model.unit_note:
        warnings.append(model.unit_note)
    f, t = config["from"], config["to"]
    start = find_face(model, f["part"], f.get("face", "top"), f.get("level"))
    end = find_face(model, t["part"], t.get("face", "top"), t.get("level"))
    exclude = {find_part(model, q).index for q in config.get("exclude", [])}
    options = find_paths(start.part, end.part, model.contacts, exclude - {start.part, end.part})
    if not options:
        build_chain(start, end, model.parts, model.contacts, exclude)  # raises with the explanation
    choice = int(config.get("chain_option", 1))
    if not 1 <= choice <= len(options):
        raise ValueError(f"chain_option {choice} out of range 1..{len(options)}")
    contributors = build_chain(start, end, model.parts, model.contacts, exclude, options[choice - 1].steps)

    for row in config.get("extra", []):
        contributors.append(Contributor(**{"source": "manual", **row}))
    overrides = config.get("overrides", {})
    for c in contributors:
        for k, v in overrides.get(c.label, {}).items():
            setattr(c, k, v)

    out = Path(config.get("output", "tolstack_report.xlsx"))
    if not out.is_absolute():
        out = base / out
    out.parent.mkdir(parents=True, exist_ok=True)
    table = [tuple(r) for r in config.get("general_tolerance", DEFAULT_GENERAL_TABLE)]
    drawings = expand_drawings(config.get("drawings"), base)
    drawing_map = {}
    for part, pdf in (config.get("drawing_map") or {}).items():
        drawing_map[part] = "" if not pdf else str(pdf if Path(pdf).is_absolute() else base / pdf)
    gdt: dict[str, list[GdtHit]] = {}
    general: dict[str, GeneralNote | None] = {}
    apply_tolerances(contributors, drawings, table, out.parent / f"{out.stem}_refs",
                     drawing_map=drawing_map, ocr=bool(config.get("ocr", False)), gdt_out=gdt,
                     follow_drawing=bool(config.get("general_from_drawing", True)), general_out=general)
    gdt_lines = [f"{part}: {hit.label()}" for part, hits in gdt.items() for hit in hits]
    general_lines = [f"{part}: {note.label()}" for part, note in general.items() if note is not None]

    spec = config.get("spec", {})
    sigma_level = float(config.get("sigma_level", 3.0))
    result = analyze(contributors, spec.get("lsl"), spec.get("usl"),
                     mc_samples=int(config.get("mc_samples", 100_000)), sigma_level=sigma_level)

    section_png = None
    section = config.get("section")
    if section is not None:
        from .section import render_section

        chain_parts = []
        for c in contributors:
            idx = next((p.index for p in model.parts if p.name == c.part), None)
            if idx is not None and idx not in chain_parts:
                chain_parts.append(idx)
        section_png = str(out.parent / f"{out.stem}_section.png")
        try:
            render_section(section_png, model.parts, chain_parts, start, end,
                           [c for c in contributors if not is_extra(c) and "contact gap" not in c.source], axis,
                           section.get("cut_axis", "y" if axis != "y" else "x"), section.get("offset"))
        except ValueError as err:
            warnings.append(f"section picture skipped: {err}")
            section_png = None

    meta = {
        "title": config.get("title", "Tolerance stack-up"),
        "project": config.get("project", ""),
        "name": config.get("name", ""),
        "type": config.get("type", "step"),
        "axis": axis,
        "from": model.label(start),
        "to": model.label(end),
        "author": config.get("author", ""),
        "sigma": sigma_level,
        "contact_tol": model.contact_tol,
        "general_table": table,
        "chain": options[choice - 1].describe(model.parts),
        "alternatives": [o.describe(model.parts) for i, o in enumerate(options) if i != choice - 1],
        "warnings": warnings,
        "step": str(step),
        "units": " + ".join(model.units) or "not declared",
        "gdt": gdt_lines,
        "general_notes": general_lines,
    }
    write_report(out, contributors, result, meta, section_png)
    if notes is not None:
        notes.extend(f"warning: {w}" for w in warnings)
        notes.extend(f"GD&T (not in the stack): {line}" for line in gdt_lines)
        notes.extend(f"general tolerance on the drawing: {line}" for line in general_lines)
    return contributors, result, str(out)
