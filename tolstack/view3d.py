"""Triangle meshes of parts and faces, and the 3-D picture the app shows while the two faces are picked."""
from __future__ import annotations

import threading

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location

from .cad import AXES, Part, PlanarFace, to_face

VIEWS = ("Chain parts", "Picked parts only", "Whole assembly")
FROM_COLOUR, TO_COLOUR = "#1f77b4", "#ff7f0e"
FROM_FACE, TO_FACE = "#0b2f8a", "#d62728"
CHAIN_COLOUR, REST_COLOUR = "#8c8c8c", "#c4c4c4"
MAX_TRIANGLES = 80_000         # more than this is slow to send to the browser and to turn
_MESHING = threading.Lock()    # the demo model is shared between browser sessions; OCCT meshing is not re-entrant

Mesh = tuple[np.ndarray, np.ndarray]          # (vertices n x 3, triangles m x 3)


def shape_mesh(shape, deflection: float) -> Mesh:
    """Triangles of a solid or a single face, in world coordinates."""
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    with _MESHING:
        BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, False)
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = to_face(explorer.Current())
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            if triangulation is not None:
                offset = len(vertices)
                transform = location.Transformation()
                for i in range(1, triangulation.NbNodes() + 1):
                    p = triangulation.Node(i).Transformed(transform)
                    vertices.append((p.X(), p.Y(), p.Z()))
                flip = face.Orientation() == TopAbs_REVERSED
                for i in range(1, triangulation.NbTriangles() + 1):
                    t = triangulation.Triangle(i)
                    a, b, c = (t.Value(k) - 1 + offset for k in (1, 2, 3))
                    triangles.append((a, c, b) if flip else (a, b, c))
            explorer.Next()
    return (np.array(vertices, dtype=np.float32).reshape(-1, 3),
            np.array(triangles, dtype=np.int32).reshape(-1, 3))


def _diagonal(bbox: tuple) -> float:
    return float(np.linalg.norm(np.subtract(bbox[3:], bbox[:3])))


def part_mesh(part: Part) -> Mesh:
    """Display mesh of a part (kept on the part: a part is meshed once per loaded model)."""
    if part.mesh is None:
        part.mesh = shape_mesh(part.shape, max(0.02, 0.004 * _diagonal(part.bbox)))
    return part.mesh


def box_mesh(bbox: tuple) -> Mesh:
    """The bounding box as 12 triangles: the stand-in for a part when the picture gets too heavy."""
    x0, y0, z0, x1, y1, z1 = bbox
    vertices = np.array([(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], dtype=np.float32)
    triangles = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                          (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)], dtype=np.int32)
    return vertices, triangles


def face_mesh(face: PlanarFace, lift: float = 0.0) -> Mesh:
    """Triangles of one planar face, moved `lift` mm along its outward normal so it shows on top of its part."""
    vertices, triangles = shape_mesh(face.shape, 0.02)
    vertices = vertices.copy()
    vertices[:, AXES[face.axis]] += face.sign * lift
    return vertices, triangles


def _merge(meshes: list[Mesh]) -> Mesh:
    vertices, triangles, offset = [], [], 0
    for v, t in meshes:
        vertices.append(v)
        triangles.append(t + offset)
        offset += len(v)
    if not vertices:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int32)
    return np.vstack(vertices), np.vstack(triangles)


def scene_figure(parts: list[Part], start: PlanarFace, end: PlanarFace, chain_parts=(), show: str = VIEWS[0],
                 max_triangles: int = MAX_TRIANGLES):
    """Plotly figure of the assembly with the From / To parts and faces highlighted.

    chain_parts   part indices of the chain between the two parts (drawn grey, named in the legend)
    show          one of VIEWS: the chain, only the two picked parts, or every part
    Returns (figure, boxed): `boxed` parts are drawn as their bounding box because the triangle
    budget ran out - the picked parts and faces are always drawn in full.
    """
    import plotly.graph_objects as go

    picked = list(dict.fromkeys([start.part, end.part]))
    chain = [i for i in dict.fromkeys(chain_parts) if i not in picked] if show != VIEWS[1] else []
    rest = [p.index for p in parts if p.index not in picked and p.index not in chain] if show == VIEWS[2] else []
    budget = max_triangles
    boxed = 0

    def mesh_within_budget(index: int, always: bool = False) -> Mesh:
        nonlocal budget, boxed
        if not always and budget <= 0:
            boxed += 1
            return box_mesh(parts[index].bbox)
        mesh = part_mesh(parts[index])
        budget -= len(mesh[1])
        return mesh

    def solid(mesh: Mesh, colour: str, opacity: float, name: str, hover: bool = True):
        v, t = mesh
        return go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=t[:, 0], j=t[:, 1], k=t[:, 2], color=colour,
                         opacity=opacity, name=name, showlegend=True, flatshading=True,
                         hoverinfo="name" if hover else "skip",
                         lighting={"ambient": 0.65, "diffuse": 0.6, "specular": 0.1})

    figure = go.Figure()
    same = start.part == end.part
    for index in picked:
        role = "From + To part" if same else ("From part" if index == start.part else "To part")
        colour = FROM_COLOUR if index == start.part else TO_COLOUR
        figure.add_trace(solid(mesh_within_budget(index, always=True), colour, 0.45,
                               f"{role}: {parts[index].name}"))
    for index in chain:
        figure.add_trace(solid(mesh_within_budget(index), CHAIN_COLOUR, 0.3, f"chain: {parts[index].name}"))
    if rest:
        figure.add_trace(solid(_merge([mesh_within_budget(i) for i in rest]), REST_COLOUR, 0.12,
                               f"other parts ({len(rest)})", hover=False))

    shown = [parts[i].bbox for i in picked + chain + rest]
    lows = np.min([b[:3] for b in shown], axis=0)
    highs = np.max([b[3:] for b in shown], axis=0)
    lift = 0.002 * float(np.linalg.norm(highs - lows))
    centres = []
    for face, colour, role in ((start, FROM_FACE, "From"), (end, TO_FACE, "To")):
        mesh = face_mesh(face, lift)
        figure.add_trace(solid(mesh, colour, 1.0, f"{role} face: {face.kind} @ {face.level:.3f}"))
        centre = mesh[0].mean(axis=0)
        centres.append(centre)
        figure.add_trace(go.Scatter3d(x=[centre[0]], y=[centre[1]], z=[centre[2]], mode="markers+text",
                                      text=[role], textposition="top center", textfont={"size": 14, "color": colour},
                                      marker={"size": 4, "color": colour}, hoverinfo="skip", showlegend=False))

    # the measured distance: from the From face, along the stack axis, to the level of the To face
    a = AXES[start.axis]
    foot = centres[0].copy()
    foot[a] = centres[1][a]
    middle = (centres[0] + foot) / 2
    figure.add_trace(go.Scatter3d(x=[centres[0][0], foot[0]], y=[centres[0][1], foot[1]], z=[centres[0][2], foot[2]],
                                  mode="lines", line={"color": "#111111", "width": 5, "dash": "dash"},
                                  name=f"To - From = {end.level - start.level:+.3f} mm", hoverinfo="name"))
    figure.add_trace(go.Scatter3d(x=[middle[0]], y=[middle[1]], z=[middle[2]], mode="text",
                                  text=[f"{end.level - start.level:+.3f}"], textfont={"size": 13, "color": "#111111"},
                                  hoverinfo="skip", showlegend=False))
    # True proportions, scaled so the longest side is 1: Plotly's own "data" mode scales by the geometric mean,
    # which pushes a flat plate out of the frame.
    spans = np.maximum(highs - lows, 1e-6)
    pad = 0.05 * float(spans.max())
    spans = spans + 2 * pad
    ratio = spans / float(spans.max())
    # fewer ticks on a short axis, or their labels pile up on a flat assembly
    axes = {name: {"title": name.upper(), "range": [float(lows[k] - pad), float(highs[k] + pad)],
                   "nticks": max(2, round(7 * float(ratio[k])))}
            for name, k in AXES.items()}
    figure.update_layout(
        height=520, margin={"l": 0, "r": 0, "t": 0, "b": 0}, uirevision="tolstack-view",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0},
        scene={"aspectmode": "manual", "aspectratio": {"x": float(ratio[0]), "y": float(ratio[1]), "z": float(ratio[2])},
               "xaxis": axes["x"], "yaxis": axes["y"], "zaxis": axes["z"],
               "camera": {"eye": {"x": 0.9, "y": -1.05, "z": 0.75}}},
    )
    return figure, boxed
