"""STEP loading and planar-face extraction (OpenCascade via cadquery-ocp)."""
from __future__ import annotations

from dataclasses import dataclass, field

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.collections import Sequence_TDF_Label

AXES = {"x": 0, "y": 1, "z": 2}

# OCP renamed the static casts between versions (TopoDS.Face vs TopoDS.Face_s)
to_face = getattr(TopoDS, "Face_s", None) or TopoDS.Face
to_edge = getattr(TopoDS, "Edge_s", None) or TopoDS.Edge


@dataclass
class Part:
    index: int
    name: str
    path: str
    bbox: tuple  # (xmin, ymin, zmin, xmax, ymax, zmax)
    shape: TopoDS_Shape = field(repr=False)


@dataclass
class PlanarFace:
    """A planar face whose normal is parallel to the stack-up axis."""
    part: int
    face_id: int
    level: float      # coordinate along the axis
    sign: int         # +1: outward normal points to +axis (a "top" face), -1: "bottom" face
    rect: tuple       # (u0, v0, u1, v1) bbox in the two other axes
    area: float
    shape: TopoDS_Face | None = field(default=None, repr=False, compare=False)
    axis: str = "z"

    @property
    def kind(self) -> str:
        return "top" if self.sign > 0 else "bottom"


def _label_name(label) -> str:
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString()
    return ""


def load_step(path: str) -> list[Part]:
    """Read a STEP assembly and return every leaf solid with its name and world bbox."""
    document = TDocStd_Document(TCollection_ExtendedString("tolstack"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError(f"cannot read STEP file: {path}")
    reader.Transfer(document)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = Sequence_TDF_Label()
    shape_tool.GetFreeShapes(roots)
    parts: list[Part] = []

    def walk(label, location, names):
        name = _label_name(label)
        if shape_tool.IsReference_s(label):
            referred = TDF_Label()
            shape_tool.GetReferredShape_s(label, referred)
            walk(referred, location.Multiplied(shape_tool.GetLocation_s(label)),
                 names + [name or _label_name(referred)])
            return
        if shape_tool.IsAssembly_s(label):
            children = Sequence_TDF_Label()
            shape_tool.GetComponents_s(label, children)
            for i in range(1, children.Length() + 1):
                walk(children.Value(i), location, names)
            return
        shape = shape_tool.GetShape_s(label)
        if shape.IsNull():
            return
        located = shape.Moved(location)
        box = Bnd_Box()
        BRepBndLib.Add_s(located, box, True)
        if box.IsVoid():
            return
        lo, hi = box.CornerMin(), box.CornerMax()
        clean = [n for n in names if n]
        parts.append(Part(
            index=len(parts),
            name=clean[-1] if clean else f"part_{len(parts)}",
            path="/".join(clean),
            bbox=(lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()),
            shape=located,
        ))

    for i in range(1, roots.Length() + 1):
        root = roots.Value(i)
        walk(root, TopLoc_Location(), [_label_name(root)])
    return parts


def planar_faces(part: Part, axis: str, min_area: float = 0.01) -> list[PlanarFace]:
    """All planar faces of a part that are perpendicular to the given axis."""
    a = AXES[axis]
    others = [i for i in range(3) if i != a]
    result = []
    explorer = TopExp_Explorer(part.shape, TopAbs_FACE)
    face_id = 0
    while explorer.More():
        face = to_face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            plane = surface.Plane()
            normal = plane.Axis().Direction()
            n = [normal.X(), normal.Y(), normal.Z()]
            if face.Orientation() == TopAbs_REVERSED:
                n = [-c for c in n]
            if abs(abs(n[a]) - 1.0) < 1e-6:
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                area = props.Mass()
                if area >= min_area:
                    box = Bnd_Box()
                    BRepBndLib.Add_s(face, box, False)
                    lo, hi = box.CornerMin(), box.CornerMax()
                    lo_c = (lo.X(), lo.Y(), lo.Z())
                    hi_c = (hi.X(), hi.Y(), hi.Z())
                    loc = plane.Location()
                    level = (loc.X(), loc.Y(), loc.Z())[a]
                    result.append(PlanarFace(
                        part=part.index,
                        face_id=face_id,
                        level=round(level, 4),
                        sign=1 if n[a] > 0 else -1,
                        rect=(lo_c[others[0]], lo_c[others[1]], hi_c[others[0]], hi_c[others[1]]),
                        area=area,
                        shape=face,
                        axis=axis,
                    ))
        face_id += 1
        explorer.Next()
    return result


_OUTLINES: dict = {}


def face_outline(face: PlanarFace, deflection: float = 0.02):
    """True 2-D outline of a planar face (holes included) as a shapely geometry, projected on the
    plane normal to the stack-up axis. Built from the face triangulation; None if meshing fails."""
    key = (id(face.shape), face.part, face.face_id)
    if key in _OUTLINES:
        return _OUTLINES[key]
    outline = None
    if face.shape is not None:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        BRepMesh_IncrementalMesh(face.shape, deflection, False, 0.5, False)
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.shape, location)
        if triangulation is not None:
            a = AXES[face.axis]
            others = [i for i in range(3) if i != a]
            transform = location.Transformation()
            nodes = []
            for i in range(1, triangulation.NbNodes() + 1):
                p = triangulation.Node(i).Transformed(transform)
                c = (p.X(), p.Y(), p.Z())
                nodes.append((c[others[0]], c[others[1]]))
            triangles = []
            for i in range(1, triangulation.NbTriangles() + 1):
                t = triangulation.Triangle(i)
                corners = [nodes[t.Value(k) - 1] for k in (1, 2, 3)]
                tri = Polygon(corners)
                if tri.area > 1e-12:
                    triangles.append(tri)
            if triangles:
                outline = unary_union(triangles).buffer(0)
    _OUTLINES[key] = outline
    return outline


def describe_face(face: PlanarFace, parts: list[Part]) -> str:
    return f"{parts[face.part].name} | {face.kind} face @ {face.level:.3f} (area {face.area:.1f})"
