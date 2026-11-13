"""Locating fits between two parts: where one part is centred or guided in another, across the stack axis.

A 1-D stack often depends on how two parts are located sideways: a pilot in a bore, a cover dropped into
a frame, a rib between two walls. Such a fit is not a contact along the stack axis, so the chain walk does
not see it, yet its clearance lets one part shift and adds to the stack. This module finds those fits in
the CAD from two kinds of face pairs:

- bore / shaft: a cylindrical face of each part, both axes parallel to the stack axis and on one line,
  one face a bore (material outside) and the other a shaft (material inside).
  Radial clearance = bore radius - shaft radius.
- facing walls: a planar face of each part, normal across the stack axis (along one of the two other
  axes), the two faces facing each other and overlapping. Clearance = distance between the planes.

A clearance near 0 (or below 0: interference, a press fit) is what locates the parts. Along one direction
the part can move by the smallest clearance on each side; the sum of the two is the total float.

The fit is turned into two rows for a stack, like a hand-written locating fit: the bore / slot size and the
shaft / tongue size, each marked `diameter: true` so the size and tolerance are halved (one side), which
gives the one-side float (S - T) / 2 with the tolerances of both sizes. Check the direction of the two rows
against your stack: they are written so that more float adds to the result.

Only axis-aligned geometry is read (cylinder axes along the stack axis, wall normals along X / Y / Z),
the same assumption as the rest of tolstack. The overlap of two walls is checked on their bounding boxes,
so an L-shaped or holed face can show an overlap that is not really there: look at the section picture.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp_Face
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

from .cad import AXES, Part, to_face

NAMES = "xyz"


@dataclass
class Cylinder:
    part: int
    center: tuple[float, float]      # position of the axis in the two lateral coordinates
    radius: float
    bore: bool                       # True: material outside the cylinder (a hole); False: a shaft
    span: tuple[float, float]        # extent along the stack axis


@dataclass
class Wall:
    part: int
    axis: int                        # 0, 1, 2: the coordinate the normal runs along
    sign: int                        # +1: outward normal points to +axis
    level: float                     # coordinate of the plane along `axis`
    lo: tuple[float, float, float]   # bounding box of the face
    hi: tuple[float, float, float]
    area: float


@dataclass
class BoreFit:
    bore_part: str
    shaft_part: str
    bore_diameter: float
    shaft_diameter: float
    center: tuple[float, float]
    overlap: float                   # length along the stack axis where both faces exist

    @property
    def clearance(self) -> float:
        """Radial clearance; below 0 is interference."""
        return round((self.bore_diameter - self.shaft_diameter) / 2, 6)

    def label(self) -> str:
        return (f"bore Ø{self.bore_diameter:.3f} ({self.bore_part}) / shaft Ø{self.shaft_diameter:.3f} "
                f"({self.shaft_part}): radial clearance {self.clearance:+.3f} over {self.overlap:.2f} mm")


@dataclass
class WallFit:
    axis: str                        # "x", "y" or "z": the direction the clearance is measured in
    low_part: str                    # part whose wall faces +axis (its material is on the low side)
    high_part: str                   # part whose wall faces -axis
    low_level: float
    high_level: float
    overlap: float                   # overlapping area of the two faces (bounding boxes), mm²

    @property
    def clearance(self) -> float:
        return round(self.high_level - self.low_level, 6)

    def label(self) -> str:
        return (f"{self.axis.upper()}: {self.low_part} wall @ {self.low_level:.3f} faces {self.high_part} wall "
                f"@ {self.high_level:.3f}: clearance {self.clearance:+.3f} (overlap {self.overlap:.1f} mm²)")


@dataclass
class Float:
    """How far part A can move against part B along one lateral axis, from the closest walls."""
    axis: str
    plus: WallFit | None             # A's +axis face against B: A can move +axis by this clearance
    minus: WallFit | None            # A's -axis face against B: A can move -axis by this clearance

    @property
    def total(self) -> float | None:
        if self.plus is None or self.minus is None:
            return None
        return round(self.plus.clearance + self.minus.clearance, 6)


@dataclass
class FitReport:
    part_a: str
    part_b: str
    axis: str
    bores: list[BoreFit]
    walls: list[WallFit]
    floats: list[Float]

    def lines(self) -> list[str]:
        out = [f"locating fits between {self.part_a} and {self.part_b} across the {self.axis.upper()} stack axis"]
        if self.bores:
            out.append("bore / shaft (coaxial, axis along the stack):")
            out.extend(f"  {fit.label()}" for fit in self.bores)
        for fl in self.floats:
            plus = "-" if fl.plus is None else f"{fl.plus.clearance:+.3f}"
            minus = "-" if fl.minus is None else f"{fl.minus.clearance:+.3f}"
            total = "open on one side" if fl.total is None else f"total float {fl.total:.3f}"
            out.append(f"{fl.axis.upper()}: {self.part_a} can move +{fl.axis} by {plus}, -{fl.axis} by {minus} "
                       f"({total})")
        if self.walls:
            out.append("facing walls, smallest clearance first:")
            out.extend(f"  {fit.label()}" for fit in self.walls)
        if not self.bores and not self.walls:
            out.append("no bore / shaft pair and no facing walls within the clearance limit")
        return out

    def rows(self, prefix: str = "L") -> list[dict]:
        """Hand-written stack rows for the locating fits found: two rows per fit, halved (one side)."""
        rows: list[dict] = []
        n = 0
        for fit in self.bores[:1]:                    # the closest bore / shaft fit locates the part
            n += 1
            rows.append({"label": f"{prefix}{n}a", "part": fit.bore_part, "nominal": round(fit.bore_diameter, 4),
                         "direction": 1, "diameter": True,
                         "description": "locating bore Ø (radial float = (bore - shaft) / 2)"})
            rows.append({"label": f"{prefix}{n}b", "part": fit.shaft_part, "nominal": round(fit.shaft_diameter, 4),
                         "direction": -1, "diameter": True, "description": "locating shaft Ø"})
        for fl in self.floats:
            if fl.plus is None or fl.minus is None:
                continue
            # A's +axis wall minus its -axis wall: positive when A is the body between B's walls (a tongue),
            # negative when those two walls of A face each other across an opening (a slot holding B)
            span_a = fl.plus.low_level - fl.minus.high_level
            span_b = fl.plus.high_level - fl.minus.low_level
            if span_a >= 0:
                slot, tongue, slot_part, tongue_part = span_b, span_a, self.part_b, self.part_a
            else:
                slot, tongue, slot_part, tongue_part = -span_a, -span_b, self.part_a, self.part_b
            n += 1
            rows.append({"label": f"{prefix}{n}a", "part": slot_part, "nominal": round(abs(slot), 4),
                         "direction": 1, "diameter": True,
                         "description": f"locating slot / opening width across {fl.axis.upper()} "
                                        "(one-side float = (slot - tongue) / 2)"})
            rows.append({"label": f"{prefix}{n}b", "part": tongue_part, "nominal": round(abs(tongue), 4),
                         "direction": -1, "diameter": True,
                         "description": f"locating tongue / body width across {fl.axis.upper()}"})
        return rows


def _face_bbox(face) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return (lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z())


def _normal_at_middle(face) -> tuple[gp_Pnt, gp_Vec]:
    """Point and outward (material-side aware) normal in the middle of the face's parameter range."""
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    point, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, point, normal)
    return point, normal


def _area(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def lateral_features(part: Part, axis: str, min_area: float = 0.01) -> tuple[list[Cylinder], list[Wall]]:
    """Cylinders with their axis along `axis`, and planar walls with the normal across it."""
    a = AXES[axis]
    lateral = [i for i in range(3) if i != a]
    cylinders: list[Cylinder] = []
    walls: list[Wall] = []
    explorer = TopExp_Explorer(part.shape, TopAbs_FACE)
    while explorer.More():
        face = to_face(explorer.Current())
        explorer.Next()
        surface = BRepAdaptor_Surface(face)
        kind = surface.GetType()
        if kind not in (GeomAbs_Cylinder, GeomAbs_Plane):
            continue
        area = _area(face)
        if area < min_area:
            continue
        lo, hi = _face_bbox(face)
        point, normal = _normal_at_middle(face)
        if normal.Magnitude() < 1e-12:
            continue
        normal.Normalize()
        n = (normal.X(), normal.Y(), normal.Z())
        if kind == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            direction = cylinder.Axis().Direction()
            d = (direction.X(), direction.Y(), direction.Z())
            if abs(abs(d[a]) - 1.0) > 1e-6:
                continue
            origin = cylinder.Axis().Location()
            o = (origin.X(), origin.Y(), origin.Z())
            p = (point.X(), point.Y(), point.Z())
            radial = [p[i] - o[i] for i in lateral]
            outward = sum(r * n[i] for r, i in zip(radial, lateral, strict=True))
            cylinders.append(Cylinder(part.index, (o[lateral[0]], o[lateral[1]]), cylinder.Radius(),
                                      bore=outward < 0, span=(lo[a], hi[a])))
        else:
            for k in lateral:
                if abs(abs(n[k]) - 1.0) < 1e-6:
                    plane_point = surface.Plane().Location()
                    level = (plane_point.X(), plane_point.Y(), plane_point.Z())[k]
                    walls.append(Wall(part.index, k, 1 if n[k] > 0 else -1, round(level, 6), lo, hi, area))
                    break
    return cylinders, walls


def pick_part(parts: list[Part], query: str) -> Part:
    """The part named `query`: an exact name first, else the one part whose name or path contains it."""
    q = query.lower()
    exact = [p for p in parts if p.name.lower() == q]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [p for p in parts if q in p.name.lower() or q in p.path.lower()]
    if not matches:
        raise ValueError(f"no part matches '{query}'")
    if len(matches) > 1:
        listed = ", ".join(f"[{p.index}] {p.name}" for p in matches[:8])
        raise ValueError(f"'{query}' matches {len(matches)} parts ({listed}{', ...' if len(matches) > 8 else ''}); "
                         "use the full name")
    return matches[0]


def _overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> float:
    return min(hi1, hi2) - max(lo1, lo2)


def find_fits(part_a: Part, part_b: Part, axis: str = "z", max_clearance: float = 0.5,
              max_interference: float = 0.2, center_tol: float = 0.02, min_overlap: float = 0.05,
              limit: int = 10) -> FitReport:
    """Locating fits between two parts across the stack axis `axis`.

    max_clearance     pairs further apart than this are not a fit (mm)
    max_interference  overlapping pairs deeper than this are not a fit either (two walls of one body)
    center_tol        two cylinder axes closer than this count as one line (mm)
    min_overlap       the two faces must overlap by at least this much in each direction (mm)
    limit             facing-wall pairs listed, smallest clearance first
    """
    a = AXES[axis]
    cyl_a, walls_a = lateral_features(part_a, axis)
    cyl_b, walls_b = lateral_features(part_b, axis)
    names = {part_a.index: part_a.name, part_b.index: part_b.name}

    bores: list[BoreFit] = []
    for c1 in cyl_a:
        for c2 in cyl_b:
            if c1.bore == c2.bore:
                continue
            if math.dist(c1.center, c2.center) > center_tol:
                continue
            overlap = _overlap(*c1.span, *c2.span)
            if overlap < min_overlap:
                continue
            bore, shaft = (c1, c2) if c1.bore else (c2, c1)
            clearance = bore.radius - shaft.radius
            if -max_interference <= clearance <= max_clearance:
                bores.append(BoreFit(names[bore.part], names[shaft.part], round(2 * bore.radius, 6),
                                     round(2 * shaft.radius, 6), bore.center, round(overlap, 4)))
    bores = _unique(sorted(bores, key=lambda f: (abs(f.clearance), -f.overlap)),
                    key=lambda f: (f.bore_part, f.shaft_part, f.bore_diameter, f.shaft_diameter))

    walls: list[WallFit] = []
    for w1 in walls_a:
        for w2 in walls_b:
            if w1.axis != w2.axis or w1.sign == w2.sign:
                continue
            low, high = (w1, w2) if w1.sign > 0 else (w2, w1)       # low faces +axis, high faces -axis
            clearance = high.level - low.level
            if not -max_interference <= clearance <= max_clearance:
                continue
            others = [i for i in range(3) if i != w1.axis]
            spans = [_overlap(w1.lo[i], w1.hi[i], w2.lo[i], w2.hi[i]) for i in others]
            if min(spans) < min_overlap:
                continue
            walls.append(WallFit(NAMES[w1.axis], names[low.part], names[high.part], low.level, high.level,
                                 round(spans[0] * spans[1], 3)))
    walls = _unique(sorted(walls, key=lambda f: (abs(f.clearance), -f.overlap)),
                    key=lambda f: (f.axis, f.low_part, f.high_part, round(f.low_level, 4), round(f.high_level, 4)))

    floats = []
    for k in (i for i in range(3) if i != a):
        name = NAMES[k]
        plus = [w for w in walls if w.axis == name and w.low_part == part_a.name and w.high_part == part_b.name]
        minus = [w for w in walls if w.axis == name and w.low_part == part_b.name and w.high_part == part_a.name]
        if plus or minus:
            floats.append(Float(name, min(plus, key=lambda w: w.clearance, default=None),
                                min(minus, key=lambda w: w.clearance, default=None)))
    return FitReport(part_a.name, part_b.name, axis, bores, walls[:limit], floats)


def _unique(items: list, key) -> list:
    seen, out = set(), []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out
