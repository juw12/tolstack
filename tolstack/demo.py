"""Small demo assembly (STEP) and matching 2D drawings (PDF), used by the app's
"Demo assembly" option, the tests and `python -m tolstack demo`.

Stack along Z:  base (pocket at z=3) -> post (3..19)   and   base (top z=5) -> spacer -> pcb -> cap
Step to check:  cap top (20.6) vs post top (19.0)  = +1.6 nominal
"""
from pathlib import Path

import fitz
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_DocumentTool



def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def demo_parts():
    base = BRepAlgoAPI_Cut(box(0, 0, 0, 60, 40, 5), box(44, 9, 3, 56, 31, 6)).Shape()
    return [
        ("BASE", base),
        ("SPACER", box(5, 5, 5, 35, 35, 15)),
        ("PCB", box(2, 2, 15, 38, 38, 16.6)),
        ("CAP_TOP", box(0, 0, 16.6, 40, 40, 20.6)),
        ("POST", box(45, 10, 3, 55, 30, 19)),
    ]


def write_step(path: Path, parts) -> Path:
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    assembly = shape_tool.NewShape()
    TDataStd_Name.Set_s(assembly, TCollection_ExtendedString("DEMO_ASM"))
    for name, shape in parts:
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        component = shape_tool.AddComponent(assembly, label, TopLoc_Location())
        TDataStd_Name.Set_s(component, TCollection_ExtendedString(name))
    shape_tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(str(path))
    return path


def write_drawing(path: Path, title: str, dims: list[str]) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((40, 50), f"DEMO DRAWING - {title}", fontsize=16)
    page.draw_rect(fitz.Rect(250, 200, 550, 330), color=(0, 0, 0), width=1)
    for i, d in enumerate(dims):
        page.insert_text((580, 220 + 30 * i), d, fontsize=12)
    page.insert_text((560, 520), "TOLERANCES UNLESS SPECIFIED: 0~10 ±0.05  >10~50 ±0.10  >50 ±0.15", fontsize=8)
    doc.save(str(path))
    return path


def write_demo(out: Path) -> Path:
    """Write demo_assembly.step and drawings/*.pdf into `out`; returns the STEP path."""
    out = Path(out)
    (out / "drawings").mkdir(parents=True, exist_ok=True)
    write_step(out / "demo_assembly.step", demo_parts())
    write_drawing(out / "drawings" / "SPACER.pdf", "SPACER", ["10.00±0.08", "30.00"])
    write_drawing(out / "drawings" / "CAP_TOP.pdf", "CAP TOP", ["4 +0/-0.05", "40.00"])
    write_drawing(out / "drawings" / "POST.pdf", "POST", ["16.00±0.10"])
    write_drawing(out / "drawings" / "BASE.pdf", "BASE", ["60.00", "5.00"])
    return out / "demo_assembly.step"
