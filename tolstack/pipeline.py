"""End-to-end run: STEP (+2D PDFs) -> chain -> tolerances -> analysis -> Excel."""
from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

from .cad import Part, PlanarFace, describe_face, load_step, planar_faces
from .chain import ChainOption, Contact, build_chain, find_contacts, find_paths
from .stack import Contributor, Result, analyze
from .tolerances import (DEFAULT_GENERAL_TABLE, DimensionHit, crop_hit, find_dimension, general_tolerance,
                         match_pdf, pdf_has_text)

CONFIG_VERSION = 1
CONFIG_KEYS = {
    "version", "title", "project", "name", "type", "author", "step", "axis", "from", "to",
    "exclude", "spec", "drawings", "drawing_map", "general_tolerance", "extra", "overrides",
    "section", "output", "contact_tol", "chain_option", "sigma_level", "mc_samples", "ocr",
}


@dataclass
class Model:
    step: str
    axis: str
    parts: list[Part]
    faces: list[PlanarFace]
    contacts: list[Contact]
    contact_tol: float = 0.02

    def faces_of(self, part_index: int) -> list[PlanarFace]:
        return sorted((f for f in self.faces if f.part == part_index), key=lambda f: (f.level, f.sign))

    def label(self, face: PlanarFace) -> str:
        return describe_face(face, self.parts)


def load_model(step_path: str, axis: str = "z", contact_tol: float = 0.02) -> Model:
    parts = load_step(step_path)
    faces = [f for p in parts for f in planar_faces(p, axis)]
    if not parts:
        raise ValueError(f"no solids found in {step_path}; is it an assembly STEP (AP203/AP214/AP242)?")
    if not faces:
        raise ValueError(f"no planar faces normal to {axis.upper()} - try another stack-up axis")
    return Model(str(step_path), axis, parts, faces, find_contacts(faces, tol=contact_tol), contact_tol)


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
                     candidates_out: dict | None = None, ocr: bool = False) -> list[Contributor]:
    """Fill tolerances: 2D dimension with tolerance if found, else the general table.

    drawing_map    {part name: pdf path} overrides the automatic file-name match ("" = no drawing)
    choices        {label: candidate index} picks another 2D hit than the best ranked one
    candidates_out filled with {label: [DimensionHit, ...]} so a UI can show the alternatives
    The source column always says where the tolerance came from; when the 3D nominal differs
    from the drawing nominal, the drawing limits are kept and the difference is noted.
    """
    drawings = drawings or []
    drawing_map = drawing_map or {}
    choices = choices or {}
    text_cache: dict[str, bool] = {}
    for c in contributors:
        if c.tol_plus or c.tol_minus:
            continue  # already set by the user
        pdf = drawing_map[c.part] if c.part in drawing_map else match_pdf(c.part, drawings)
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
            c.source = f"2D {Path(pdf).name} p{hit.page + 1}: {hit.text}{note}{count}"
        else:
            t = general_tolerance(c.nominal, table)
            c.tol_plus = c.tol_minus = t
            if hit is not None:
                where = f"2D {Path(pdf).name}: {hit.text} (no tol){count} -> "
            elif no_text:
                where = f"2D {Path(pdf).name} has no text layer (vector text) - check it by eye -> "
            elif pdf:
                where = f"2D {Path(pdf).name}: not dimensioned -> "
            else:
                where = "no 2D -> "
            c.source = f"{where}general tol ±{t}"
        if hit is not None and ref_dir:
            Path(ref_dir).mkdir(parents=True, exist_ok=True)
            c.ref_image = crop_hit(hit, Path(ref_dir) / f"ref_{c.label}.png")
    return contributors


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
        out.extend(sorted(glob.glob(str(p))))
    return out


def run_config(config: dict, base: str | Path = ".") -> tuple[list[Contributor], Result, str]:
    """Run an analysis described by a dict (see examples/demo_config.yaml)."""
    from .excel import write_report

    warnings = validate_config(config)
    base = Path(base)
    step = Path(config["step"])
    if not step.is_absolute():
        step = base / step
    axis = config.get("axis", "z")
    model = load_model(str(step), axis, float(config.get("contact_tol", 0.02)))
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
    apply_tolerances(contributors, drawings, table, out.parent / f"{out.stem}_refs",
                     drawing_map=drawing_map, ocr=bool(config.get("ocr", False)))

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
                           [c for c in contributors if c.source != "manual" and "contact gap" not in c.source], axis,
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
    }
    write_report(out, contributors, result, meta, section_png)
    return contributors, result, str(out)
