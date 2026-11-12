"""Tolerance sources: general-tolerance table and 2D PDF dimension lookup."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# (min size, max size, ± tolerance) in mm. Editable in the app; default = a common
# "unless otherwise specified" block for injection-moulded plastic parts.
DEFAULT_GENERAL_TABLE = [
    (0.0, 10.0, 0.05),
    (10.0, 50.0, 0.10),
    (50.0, 1e9, 0.15),
]


def general_tolerance(nominal: float, table=DEFAULT_GENERAL_TABLE) -> float:
    for lo, hi, tol in table:
        if lo <= abs(nominal) <= hi:
            return tol
    return table[-1][2]


@dataclass
class DimensionHit:
    pdf: str
    page: int
    text: str
    value: float
    tol_plus: float | None
    tol_minus: float | None
    bbox: tuple

    @property
    def has_tolerance(self) -> bool:
        return self.tol_plus is not None


NUMBER = r"(\d+(?:\.\d+)?)"
SYMMETRIC = re.compile(NUMBER + r"\s*(?:±|\+/-|\+-)\s*(\d*\.?\d+)")
ASYMMETRIC = re.compile(NUMBER + r"\s*\+\s*(\d*\.?\d+)\s*/?\s*-\s*(\d*\.?\d+)")
PLAIN = re.compile(NUMBER)


def _parse_line(text: str):
    """Yield (value, tol_plus, tol_minus, raw) for dimensions found in one text line."""
    found = []
    for m in SYMMETRIC.finditer(text):
        t = float(m.group(2))
        found.append((float(m.group(1)), t, t, m.group(0)))
    for m in ASYMMETRIC.finditer(text):
        found.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(0)))
    if not found:
        for m in PLAIN.finditer(text):
            found.append((float(m.group(1)), None, None, m.group(0)))
    return found


def find_dimension(pdf_path: str | Path, nominal: float, window: float = 0.03) -> list[DimensionHit]:
    """Search the text layer of a 2D PDF for a dimension close to `nominal`.

    Works for PDFs exported with real text. Drawings whose text was exported as strokes
    have no text layer; those need the general table or a manual entry.
    """
    import fitz

    hits = []
    doc = fitz.open(str(pdf_path))
    for page_index, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"])
                for value, tp, tm, raw in _parse_line(text):
                    if abs(value - nominal) <= window:
                        hits.append(DimensionHit(str(pdf_path), page_index, raw.strip(), value, tp, tm,
                                                 tuple(line["bbox"])))
    hits.sort(key=lambda h: (not h.has_tolerance, abs(h.value - nominal)))
    return hits


def crop_hit(hit: DimensionHit, out_png: str | Path, margin: float = 60, dpi: int = 250) -> str:
    """Save a screenshot of the drawing around a hit with a red box on the dimension."""
    import fitz

    doc = fitz.open(hit.pdf)
    page = doc[hit.page]
    x0, y0, x1, y1 = hit.bbox
    page.draw_rect(fitz.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2), color=(0.85, 0, 0), width=1.2)
    clip = fitz.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin) & page.rect
    page.get_pixmap(dpi=dpi, clip=clip).save(str(out_png))
    return str(out_png)


def match_pdf(part_name: str, pdf_paths: list[str | Path]) -> str | None:
    """Pick the drawing whose file name best matches a part name (shared word tokens)."""
    def tokens(s):
        return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 2}

    want = tokens(part_name)
    best, score = None, 0
    for p in pdf_paths:
        s = len(want & tokens(Path(p).stem))
        if s > score:
            best, score = str(p), s
    return best
