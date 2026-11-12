"""End-to-end run: STEP (+2D PDFs) -> chain -> tolerances -> analysis -> Excel."""
from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

from .cad import Part, PlanarFace, describe_face, load_step, planar_faces
from .chain import Contact, build_chain, find_contacts
from .stack import Contributor, Result, analyze
from .tolerances import DEFAULT_GENERAL_TABLE, crop_hit, find_dimension, general_tolerance, match_pdf


@dataclass
class Model:
    step: str
    axis: str
    parts: list[Part]
    faces: list[PlanarFace]
    contacts: list[Contact]

    def faces_of(self, part_index: int) -> list[PlanarFace]:
        return sorted((f for f in self.faces if f.part == part_index), key=lambda f: (f.level, f.sign))

    def label(self, face: PlanarFace) -> str:
        return describe_face(face, self.parts)


def load_model(step_path: str, axis: str = "z", contact_tol: float = 0.02) -> Model:
    parts = load_step(step_path)
    faces = [f for p in parts for f in planar_faces(p, axis)]
    return Model(str(step_path), axis, parts, faces, find_contacts(faces, tol=contact_tol))


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
                     window: float = 0.03) -> list[Contributor]:
    """Fill tolerances: 2D dimension with tolerance if found, else general table."""
    drawings = drawings or []
    for c in contributors:
        if c.tol_plus or c.tol_minus:
            continue  # already set by the user
        pdf = match_pdf(c.part, drawings)
        hit = None
        if pdf:
            hits = find_dimension(pdf, c.nominal, window)
            hit = hits[0] if hits else None
        if hit is not None and hit.has_tolerance:
            c.tol_plus, c.tol_minus = hit.tol_plus, hit.tol_minus
            c.source = f"2D {Path(pdf).name} p{hit.page + 1}: {hit.text}"
        else:
            t = general_tolerance(c.nominal, table)
            c.tol_plus = c.tol_minus = t
            where = f"2D {Path(pdf).name}: {hit.text} (no tol) -> " if hit else (f"2D {Path(pdf).name}: not dimensioned -> " if pdf else "")
            c.source = f"{where}general tol ±{t}"
        if hit is not None and ref_dir:
            Path(ref_dir).mkdir(parents=True, exist_ok=True)
            c.ref_image = crop_hit(hit, Path(ref_dir) / f"ref_{c.label}.png")
    return contributors


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

    base = Path(base)
    step = Path(config["step"])
    if not step.is_absolute():
        step = base / step
    axis = config.get("axis", "z")
    model = load_model(str(step), axis)
    f, t = config["from"], config["to"]
    start = find_face(model, f["part"], f.get("face", "top"), f.get("level"))
    end = find_face(model, t["part"], t.get("face", "top"), t.get("level"))
    exclude = {find_part(model, q).index for q in config.get("exclude", [])}
    contributors = build_chain(start, end, model.parts, model.contacts, exclude)

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
    apply_tolerances(contributors, drawings, table, out.parent / f"{out.stem}_refs")

    spec = config.get("spec", {})
    result = analyze(contributors, spec.get("lsl"), spec.get("usl"))

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
        render_section(section_png, model.parts, chain_parts, start, end,
                       [c for c in contributors if c.source != "manual"], axis,
                       section.get("cut_axis", "y" if axis != "y" else "x"), section.get("offset"))

    meta = {
        "title": config.get("title", "Tolerance stack-up"),
        "project": config.get("project", ""),
        "name": config.get("name", ""),
        "type": config.get("type", "step"),
        "axis": axis,
        "from": model.label(start),
        "to": model.label(end),
        "author": config.get("author", ""),
    }
    write_report(out, contributors, result, meta, section_png)
    return contributors, result, str(out)
