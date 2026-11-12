"""Tolerance sources: general-tolerance table and 2D PDF dimension lookup.

Recognised dimension formats in a PDF text layer (one text line):
    62.73±0.1   62.73 +/- 0.1        symmetric
    4 +0/-0.05  4+0.1-0.05           asymmetric (upper first)
    12.10/12.00  12.00~12.10  12.00-12.10   limit dimensions (two sizes)
    20.14                            plain number (no tolerance -> general table)
"""
from __future__ import annotations

import re
import shutil
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
    value: float               # drawing nominal (limit dimensions: the lower limit)
    tol_plus: float | None     # relative to `value`
    tol_minus: float | None
    bbox: tuple
    kind: str = "plain"        # symmetric | asymmetric | limit | plain
    ocr: bool = False

    @property
    def has_tolerance(self) -> bool:
        return self.tol_plus is not None

    @property
    def lower_limit(self) -> float | None:
        return None if self.tol_minus is None else self.value - self.tol_minus

    @property
    def upper_limit(self) -> float | None:
        return None if self.tol_plus is None else self.value + self.tol_plus

    def relative_to(self, nominal: float) -> tuple[float, float]:
        """(tol_plus, tol_minus) re-expressed around another nominal (e.g. the 3D value),
        keeping the drawing's limits unchanged."""
        return self.upper_limit - nominal, nominal - self.lower_limit

    def label(self) -> str:
        where = f"{Path(self.pdf).name} p{self.page + 1}"
        return f"{self.text}  ({self.kind}{', OCR' if self.ocr else ''}, {where})"


NUMBER = r"(\d+(?:\.\d+)?)"
SYMMETRIC = re.compile(NUMBER + r"\s*(?:±|\+/-|\+-)\s*(\d*\.?\d+)")
ASYMMETRIC = re.compile(NUMBER + r"\s*\+\s*(\d*\.?\d+)\s*/?\s*-\s*(\d*\.?\d+)")
LIMIT = re.compile(r"(?<![\d.])(\d+\.\d+)\s*(?:/|~|-)\s*(\d+\.\d+)(?![\d.])")
PLAIN = re.compile(NUMBER)


NOTE_LINE = re.compile(r"(?i)toleranc|unless|otherwise|general|未注|一般公差|公差")


def _parse_line(text: str):
    """Yield (value, tol_plus, tol_minus, raw, kind) for dimensions found in one text line.

    Title-block notes such as "TOLERANCES UNLESS SPECIFIED: 0~10 ±0.05 ..." are skipped: they
    describe the general table, not a dimension, and would otherwise match every nominal near 10.
    """
    if NOTE_LINE.search(text) or len(SYMMETRIC.findall(text)) > 1:
        return []
    found = []
    for m in SYMMETRIC.finditer(text):
        t = float(m.group(2))
        found.append((float(m.group(1)), t, t, m.group(0), "symmetric"))
    for m in ASYMMETRIC.finditer(text):
        found.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(0), "asymmetric"))
    if not found:
        for m in LIMIT.finditer(text):
            a, b = float(m.group(1)), float(m.group(2))
            lo, hi = min(a, b), max(a, b)
            if 0 < hi - lo <= min(2.0, 0.1 * hi):        # two sizes close together = a limit pair
                found.append((lo, hi - lo, 0.0, m.group(0), "limit"))
    if not found:
        for m in PLAIN.finditer(text):
            found.append((float(m.group(1)), None, None, m.group(0), "plain"))
    return found


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def pdf_has_text(pdf_path: str | Path) -> bool:
    """False for drawings whose text was exported as vector strokes (no searchable text)."""
    import fitz

    with fitz.open(str(pdf_path)) as doc:
        return any(page.get_text("text").strip() for page in doc)


def _lines(page, textpage=None):
    data = page.get_text("dict", textpage=textpage) if textpage is not None else page.get_text("dict")
    for block in data["blocks"]:
        for line in block.get("lines", []):
            yield "".join(span["text"] for span in line["spans"]), tuple(line["bbox"])


def find_dimension(pdf_path: str | Path, nominal: float, window: float = 0.03,
                   ocr: bool = False) -> list[DimensionHit]:
    """Search a 2D PDF for dimensions matching `nominal`, best candidate first.

    A dimension matches when its value is within `window` of the nominal, or - for limit
    dimensions - when the nominal lies inside the limits (± window). Ranking: has a tolerance,
    then closest value. With `ocr=True` and Tesseract installed, pages without a text layer are
    OCR'd (PyMuPDF + Tesseract); otherwise such drawings return no hits.
    """
    import fitz

    hits = []
    doc = fitz.open(str(pdf_path))
    for page_index, page in enumerate(doc):
        textpage, used_ocr = None, False
        if ocr and not page.get_text("text").strip() and tesseract_available():
            try:
                textpage = page.get_textpage_ocr(full=True, dpi=300)
                used_ocr = True
            except Exception:  # noqa: BLE001 - OCR is best effort
                textpage = None
        for text, bbox in _lines(page, textpage):
            for value, tp, tm, raw, kind in _parse_line(text):
                if kind == "limit":
                    ok = value - window <= nominal <= value + tp + window
                else:
                    ok = abs(value - nominal) <= window
                if ok:
                    hits.append(DimensionHit(str(pdf_path), page_index, raw.strip(), value, tp, tm, bbox,
                                             kind, used_ocr))
    doc.close()

    def rank(h):
        centre = h.value + ((h.tol_plus or 0) - (h.tol_minus or 0)) / 2 if h.kind == "limit" else h.value
        return (not h.has_tolerance, abs(centre - nominal) if h.kind == "limit" else abs(h.value - nominal))

    hits.sort(key=rank)
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
    doc.close()
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
