"""Colour-filled section picture of the chain with level lines and lettered arrows."""
from __future__ import annotations

import colorsys

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

from .cad import AXES, Part, PlanarFace, to_edge
from .stack import Contributor


def _section_region(shape, normal_axis: int, offset: float, u_axis: int, v_axis: int):
    origin = [0.0, 0.0, 0.0]
    origin[normal_axis] = offset
    direction = [0.0, 0.0, 0.0]
    direction[normal_axis] = 1.0
    section = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(*origin), gp_Dir(*direction)), False)
    section.Build()
    lines = []
    explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(to_edge(explorer.Current()))
        sampler = GCPnts_QuasiUniformDeflection(curve, 0.005)
        if sampler.IsDone() and sampler.NbPoints() > 1:
            pts = []
            for i in range(1, sampler.NbPoints() + 1):
                p = sampler.Value(i)
                c = (p.X(), p.Y(), p.Z())
                pts.append((c[u_axis], c[v_axis]))
            lines.append(LineString(pts))
        explorer.Next()
    if not lines:
        return None
    region = None
    for face in polygonize(unary_union(lines)):
        region = face if region is None else region.symmetric_difference(face)
    return region


def _font(size):
    for name in ("msjhbd.ttc", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_section(path: str, parts: list[Part], chain_parts: list[int], start: PlanarFace,
                   end: PlanarFace, contributors: list[Contributor], axis: str, cut_axis: str,
                   cut_offset: float | None = None, width_px: int = 1400) -> str:
    """Cut the assembly with a plane normal to `cut_axis` (through the start face by default)."""
    a = AXES[axis]
    c = AXES[cut_axis]
    u = [i for i in range(3) if i not in (a, c)][0]
    if cut_offset is None:
        r = start.rect
        others = [i for i in range(3) if i != a]
        idx = others.index(c)
        cut_offset = (r[idx] + r[idx + 2]) / 2
    lo_v = min(start.level, end.level)
    hi_v = max(start.level, end.level)
    for part_index in chain_parts:
        b = parts[part_index].bbox
        lo_v = min(lo_v, b[a])
        hi_v = max(hi_v, b[a + 3])
    pad = 0.08 * (hi_v - lo_v) + 1
    lo_v -= pad
    hi_v += pad
    regions = []
    lo_u, hi_u = float("inf"), float("-inf")
    for part in parts:
        b = part.bbox
        if not (b[c] - 1e-6 <= cut_offset <= b[c + 3] + 1e-6):
            continue
        if b[a + 3] < lo_v or b[a] > hi_v:
            continue
        region = _section_region(part.shape, c, cut_offset, u, a)
        if region is None or region.is_empty:
            continue
        regions.append((part.index, region))
        if part.index in chain_parts:
            ru = region.bounds
            lo_u, hi_u = min(lo_u, ru[0]), max(hi_u, ru[2])
    if not regions:
        raise ValueError("section plane misses the parts; choose another cut offset")
    if lo_u == float("inf"):
        lo_u = min(r.bounds[0] for _, r in regions)
        hi_u = max(r.bounds[2] for _, r in regions)
    span_u = hi_u - lo_u
    lo_u -= 0.15 * span_u + 1
    hi_u += 0.15 * span_u + 1
    margin = 420
    scale = width_px / max(hi_u - lo_u, hi_v - lo_v)
    w = int((hi_u - lo_u) * scale) + margin
    h = int((hi_v - lo_v) * scale) + 90
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    def to_px(uu, vv):
        return ((uu - lo_u) * scale, 60 + (hi_v - vv) * scale)

    for idx, (part_index, region) in enumerate(regions):
        if part_index in chain_parts:
            hue = (chain_parts.index(part_index) * 0.17) % 1.0
            fill = tuple(int(255 * x) for x in colorsys.hsv_to_rgb(hue, 0.55, 0.95))
        else:
            fill = (215, 215, 220)
        geoms = region.geoms if hasattr(region, "geoms") else [region]
        for g in geoms:
            if g.geom_type != "Polygon":
                continue
            d.polygon([to_px(*p) for p in g.exterior.coords], fill=fill, outline=(40, 40, 40))
            for hole in g.interiors:
                d.polygon([to_px(*p) for p in hole.coords], fill="white", outline=(40, 40, 40))

    # level lines + arrows
    yellow = (240, 176, 0)
    x_right = int((hi_u - lo_u) * scale)
    level = start.level
    levels = [level]
    for cb in contributors:
        level += cb.direction * cb.nominal
        levels.append(level)
    for lv in levels:
        _, py = to_px(lo_u, lv)
        d.line((x_right - 40, py, x_right + margin - 60, py), fill=yellow, width=3)
    font = _font(24)
    for i, cb in enumerate(contributors):
        x = x_right + 40 + (i % 4) * 85
        _, y0 = to_px(lo_u, levels[i])
        _, y1 = to_px(lo_u, levels[i + 1])
        color = (215, 0, 0) if cb.direction < 0 else (0, 0, 0)
        d.line((x, y0, x, y1), fill=color, width=4)
        dirn = 1 if y1 > y0 else -1
        d.polygon([(x, y1), (x - 7, y1 - dirn * 13), (x + 7, y1 - dirn * 13)], fill=color)
        tw = d.textlength(cb.label, font=font)
        ym = (y0 + y1) / 2
        d.rectangle((x + 8, ym - 15, x + 18 + tw, ym + 15), fill=(255, 245, 0))
        d.text((x + 13, ym - 14), cb.label, font=font, fill="black")
    d.text((10, 10), f"Section {cut_axis.upper()} = {cut_offset:.2f}   (red = subtractive, black = additive)",
           font=_font(22), fill="black")
    img.save(path)
    return path
