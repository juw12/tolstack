"""Excel report with live formulas, contribution chart, section image and 2D evidence."""
from __future__ import annotations

import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .stack import Contributor, Result

HEADERS = ["Item", "Part", "Description", "Source", "Nominal", "+Tol", "-Tol", "Dir (+1/-1)",
           "Mean shift", "Half range", "Signed mean", "Variance", "Contribution %"]
THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
YELLOW = PatternFill("solid", fgColor="FFF200")


def _fit_image(path, max_width_px):
    img = XLImage(str(path))
    if img.width > max_width_px:
        ratio = max_width_px / img.width
        img.width, img.height = int(img.width * ratio), int(img.height * ratio)
    return img


def write_report(path: str | Path, contributors: list[Contributor], result: Result, meta: dict,
                 section_png: str | None = None) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = "Stack-up"
    ws["A1"] = meta.get("title", "Tolerance stack-up")
    ws["A1"].font = Font(size=16, bold=True)
    info = [
        ("Project", meta.get("project", "")),
        ("Analysis", meta.get("name", "")),
        ("Type", meta.get("type", "")),
        ("Axis", meta.get("axis", "")),
        ("From", meta.get("from", "")),
        ("To", meta.get("to", "")),
        ("Sign convention", "Result = To level - From level (+ = along +axis)"),
        ("Author / date", f"{meta.get('author', '')}  {datetime.date.today().isoformat()}"),
        ("Sigma level (RSS)", meta.get("sigma", 3)),
    ]
    for i, (k, v) in enumerate(info, start=2):
        ws.cell(i, 1, k).font = Font(bold=True)
        ws.cell(i, 2, v)

    head = 13
    for col, name in enumerate(HEADERS, start=1):
        cell = ws.cell(head, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(wrap_text=True, horizontal="center")
    first = head + 1
    last = head + len(contributors)
    for r, c in enumerate(contributors, start=first):
        values = [c.label, c.part, c.description, c.source, c.nominal, c.tol_plus, c.tol_minus, c.direction]
        for col, v in enumerate(values, start=1):
            ws.cell(r, col, v).border = BOX
        ws.cell(r, 1).fill = YELLOW
        ws.cell(r, 9, f"=(F{r}-G{r})/2")
        ws.cell(r, 10, f"=(F{r}+G{r})/2")
        ws.cell(r, 11, f"=H{r}*(E{r}+I{r})")
        ws.cell(r, 12, f"=J{r}^2")
        ws.cell(r, 13, f"=IF(SUM($L${first}:$L${last})=0,0,100*L{r}/SUM($L${first}:$L${last}))")
        for col in range(5, 14):
            ws.cell(r, col).number_format = "0.000"
            ws.cell(r, col).border = BOX

    s = last + 2
    summary = [
        ("Nominal", f"=SUMPRODUCT(H{first}:H{last},E{first}:E{last})"),
        ("Mean (with asymmetric tol shift)", f"=SUM(K{first}:K{last})"),
        ("Worst case ±", f"=SUM(J{first}:J{last})"),
        ("Worst case min", f"=B{s + 1}-B{s + 2}"),
        ("Worst case max", f"=B{s + 1}+B{s + 2}"),
        ("RSS ±", f"=SQRT(SUM(L{first}:L{last}))"),
        ("RSS min", f"=B{s + 1}-B{s + 5}"),
        ("RSS max", f"=B{s + 1}+B{s + 5}"),
        ("Spec LSL", result.lsl if result.lsl is not None else ""),
        ("Spec USL", result.usl if result.usl is not None else ""),
        ("RSS verdict", f'=IF(AND(OR(B{s + 8}="",B{s + 6}>=B{s + 8}),OR(B{s + 9}="",B{s + 7}<=B{s + 9})),"PASS","FAIL")'),
        ("WC verdict", f'=IF(AND(OR(B{s + 8}="",B{s + 3}>=B{s + 8}),OR(B{s + 9}="",B{s + 4}<=B{s + 9})),"PASS","FAIL")'),
        ("Monte Carlo mean / std", "" if result.mc_mean is None else f"{result.mc_mean:.4f} / {result.mc_std:.4f}"),
        ("Monte Carlo out of spec %", "" if result.mc_out_pct is None else round(result.mc_out_pct, 3)),
    ]
    ws.cell(s - 1, 1, "Result").font = Font(size=13, bold=True)
    for i, (k, v) in enumerate(summary):
        ws.cell(s + i, 1, k).font = Font(bold=True)
        cell = ws.cell(s + i, 2, v)
        cell.number_format = "0.000"
        cell.border = BOX

    chart = BarChart()
    chart.type = "bar"
    chart.title = "RSS contribution %"
    chart.y_axis.title = "%"
    chart.add_data(Reference(ws, min_col=13, min_row=head, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    chart.height = max(6, 0.6 * len(contributors) + 3)
    chart.width = 14
    ws.add_chart(chart, f"D{s}")

    widths = [7, 22, 46, 34, 10, 8, 8, 9, 10, 10, 11, 10, 13]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    row_img = s + len(summary) + 2
    if section_png and Path(section_png).exists():
        ws.cell(row_img, 1, "Section").font = Font(size=13, bold=True)
        ws.add_image(_fit_image(section_png, 1100), f"A{row_img + 1}")

    refs = [c for c in contributors if c.ref_image and Path(c.ref_image).exists()]
    if refs:
        ws2 = wb.create_sheet("2D references")
        ws2["A1"] = "2D reference - where each tolerance comes from (red box = dimension used)"
        ws2["A1"].font = Font(size=14, bold=True)
        row = 3
        for c in refs:
            ws2.cell(row, 1, f"{c.label}  {c.part}: {c.nominal} +{c.tol_plus}/-{c.tol_minus}  ({c.source})").font = Font(bold=True)
            img = _fit_image(c.ref_image, 900)
            ws2.add_image(img, f"A{row + 1}")
            row += int(img.height / 20) + 3
    wb.save(str(path))
    return str(path)
