"""Tolerance sources: general-tolerance table and 2D PDF dimension lookup.

Recognised dimension formats in a PDF text layer (one text line):
    62.73±0.1   62.73 +/- 0.1        symmetric
    4 +0/-0.05  4+0.1-0.05           asymmetric (upper first)
    4 +0.1/0    4 0/-0.05            asymmetric with a bare zero on one side
    12.10/12.00  12.00~12.10  12.00-12.10   limit dimensions (two sizes)
    20.14                            plain number (no tolerance -> general table)

GD&T feature control frames are read too (`find_gdt`): the ISO/ASME symbol or its name, the
tolerance value, a diameter zone, a material modifier and up to three datums, e.g.
    ⏥ 0.05      ⌖ ⌀0.1 Ⓜ A B      FLATNESS 0.05      平面度 0.05      ∥ 0.05 A
They are reported, not applied: a 1-D stack cannot decide by itself which callout acts along
the axis, so the user adds the ones that matter as rows (zone t -> ± t/2).
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
        if self.tol_plus is None or self.tol_minus is None:
            raise ValueError(f"{self.text!r} has no tolerance")
        return self.value + self.tol_plus - nominal, nominal - (self.value - self.tol_minus)

    def label(self) -> str:
        where = f"{Path(self.pdf).name} p{self.page + 1}"
        return f"{self.text}  ({self.kind}{', OCR' if self.ocr else ''}, {where})"


NUMBER = r"(\d+(?:\.\d+)?)"
SYMMETRIC = re.compile(NUMBER + r"\s*(?:±|\+/-|\+-)\s*(\d*\.?\d+)")
ASYMMETRIC = re.compile(NUMBER + r"\s*\+\s*(\d*\.?\d+)\s*/?\s*-\s*(\d*\.?\d+)")
UPPER_ONLY = re.compile(NUMBER + r"\s*\+\s*(\d*\.?\d+)\s*/\s*-?0(?![\d.])")      # 4 +0.1/0
LOWER_ONLY = re.compile(NUMBER + r"\s+\+?0\s*/\s*-\s*(\d*\.?\d+)")                # 4 0/-0.05
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
    text = GDT.sub(" ", text)          # a feature control frame is not a size: "FLATNESS 0.05" is no 0.05 dimension
    found: list[tuple[float, float | None, float | None, str, str]] = []
    for m in SYMMETRIC.finditer(text):
        t = float(m.group(2))
        found.append((float(m.group(1)), t, t, m.group(0), "symmetric"))
    for m in ASYMMETRIC.finditer(text):
        found.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(0), "asymmetric"))
    if not found:
        for m in UPPER_ONLY.finditer(text):
            found.append((float(m.group(1)), float(m.group(2)), 0.0, m.group(0), "asymmetric"))
        for m in LOWER_ONLY.finditer(text):
            found.append((float(m.group(1)), 0.0, float(m.group(2)), m.group(0), "asymmetric"))
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


# Letters that CAD "GDT" fonts (AutoCAD gdt.shx, SolidWorks SWGDT, Y14.5) draw as symbols.
GDT_FONT = {"a": "∠", "b": "⊥", "c": "⏥", "d": "⌓", "e": "○", "f": "∥", "g": "⌭", "h": "↗", "i": "⌯",
            "j": "⌖", "k": "⌒", "l": "Ⓛ", "m": "Ⓜ", "n": "⌀", "r": "◎", "t": "⌰", "u": "⏤"}


def translate_gdt_font(text: str) -> str:
    """Text typed in a GDT symbol font -> the Unicode symbols it shows on the drawing."""
    return "".join(GDT_FONT.get(ch, ch) for ch in text)


def _span_text(span: dict) -> str:
    return translate_gdt_font(span["text"]) if "gdt" in str(span.get("font", "")).lower() else span["text"]


def _lines(page, textpage=None):
    data = page.get_text("dict", textpage=textpage) if textpage is not None else page.get_text("dict")
    for block in data["blocks"]:
        for line in block.get("lines", []):
            yield "".join(_span_text(span) for span in line["spans"]), tuple(line["bbox"])


GDT_KINDS = {
    "straightness": ("⏤", "straightness", "真直度", "直線度", "直线度"),
    "flatness": ("⏥", "▱", "flatness", "平面度"),
    "circularity": ("○", "circularity", "roundness", "真圓度", "真圆度", "圓度", "圆度"),
    "cylindricity": ("⌭", "cylindricity", "圓柱度", "圆柱度"),
    "profile of a line": ("⌒", "profile of a line", "line profile", "線輪廓度", "线轮廓度"),
    "profile of a surface": ("⌓", "profile of a surface", "surface profile", "profile", "面輪廓度", "面轮廓度",
                             "輪廓度", "轮廓度"),
    "parallelism": ("∥", "⫽", "//", "parallelism", "平行度"),
    "perpendicularity": ("⊥", "⟂", "perpendicularity", "squareness", "垂直度"),
    "angularity": ("∠", "angularity", "傾斜度", "倾斜度"),
    "position": ("⌖", "⊕", "true position", "position", "位置度"),
    "concentricity": ("◎", "concentricity", "coaxiality", "同心度", "同軸度", "同轴度"),
    "symmetry": ("⌯", "symmetry", "對稱度", "对称度"),
    "total runout": ("⌰", "total runout", "全跳動", "全跳动", "全偏擺"),
    "circular runout": ("↗", "circular runout", "runout", "圓跳動", "圆跳动", "偏擺", "跳動", "跳动"),
}
# kinds whose tolerance zone is a slab between two parallel planes: these can act along a stack axis
GDT_ALONG_AXIS = ("flatness", "parallelism", "profile of a surface", "perpendicularity", "angularity",
                  "total runout", "circular runout")
_GDT_TOKEN = {token.lower(): kind for kind, tokens in GDT_KINDS.items() for token in tokens}
_GDT_NAMES = sorted(_GDT_TOKEN, key=len, reverse=True)          # longest first: "total runout" before "runout"
GDT = re.compile(
    "(" + "|".join(re.escape(name) for name in _GDT_NAMES) + ")"
    r"\s*[:=|]?\s*"
    r"(S?\s*[⌀ØøΦφ∅])?\s*"                                      # diameter / spherical zone
    r"(\d*\.?\d+)(?![\d.])\s*"
    r"(Ⓜ|Ⓛ|\(M\)|\(L\))?"                                      # material condition modifier
    r"((?:\s*\|?\s*(?-i:[A-Z])(?![A-Za-z0-9.])(?:\s*(?:Ⓜ|Ⓛ|\(M\)|\(L\)))?){0,3})",   # up to three datums
    re.IGNORECASE,
)


_DATUM_RUN = re.compile(r"\s*([A-Z]{2,3})(?![A-Za-z0-9.])")
_NOT_DATUMS = {"MAX", "MIN", "TYP", "REF", "THK", "BSC", "TP"}


@dataclass
class GdtHit:
    pdf: str
    page: int
    kind: str                  # flatness, position, parallelism ...
    value: float               # total width (or diameter) of the tolerance zone
    diameter: bool             # zone given with a diameter sign
    modifier: str              # "M", "L" or ""
    datums: tuple
    text: str
    bbox: tuple

    @property
    def along_axis(self) -> bool:
        """True when this kind of zone can add to a 1-D stack (a slab, not a cylinder or a form of a hole)."""
        return self.kind in GDT_ALONG_AXIS and not self.diameter

    def label(self) -> str:
        zone = f"{'⌀' if self.diameter else ''}{self.value:g}"
        datums = f" | {' '.join(self.datums)}" if self.datums else ""
        return f"{self.kind} {zone}{' (' + self.modifier + ')' if self.modifier else ''}{datums}  " \
               f"({Path(self.pdf).name} p{self.page + 1})"


def parse_gdt(text: str) -> list[tuple]:
    """(kind, value, diameter, modifier, datums, raw) for each feature control frame in one text line."""
    found = []
    for m in GDT.finditer(text):
        token = m.group(1).lower()
        if token.isascii() and token.isalpha() or " " in token:
            before = text[m.start() - 1] if m.start() else " "
            if before.isalpha():                       # "REPOSITION 0.1" is not a position callout
                continue
        modifier = (m.group(4) or "").strip("()").replace("Ⓜ", "M").replace("Ⓛ", "L").upper()
        datums = tuple(re.findall(r"[A-Z](?![a-z])", re.sub(r"\(M\)|\(L\)", "", m.group(5) or "")))
        if not datums and not token.isascii():
            # a symbol frame exported cell by cell has no separators left: "⌖⌀0.1ⓂAB" means datums A, B
            run = _DATUM_RUN.match(text, m.end())
            if run and run.group(1) not in _NOT_DATUMS:
                datums = tuple(run.group(1))
        found.append((_GDT_TOKEN[token], float(m.group(3)), bool(m.group(2)), modifier, datums, m.group(0).strip()))
    return found


def find_gdt(pdf_path: str | Path, ocr: bool = False) -> list[GdtHit]:
    """Every GD&T feature control frame readable in a drawing's text layer (or OCR text)."""
    import fitz

    hits = []
    with fitz.open(str(pdf_path)) as doc:
        for page_index, page in enumerate(doc):
            textpage = None
            if ocr and not page.get_text("text").strip() and tesseract_available():
                try:
                    textpage = page.get_textpage_ocr(full=True, dpi=300)
                except Exception:  # noqa: BLE001 - OCR is best effort
                    textpage = None
            for text, bbox in _lines(page, textpage):
                for kind, value, diameter, modifier, datums, raw in parse_gdt(text):
                    hits.append(GdtHit(str(pdf_path), page_index, kind, value, diameter, modifier, datums, raw, bbox))
    return hits


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
