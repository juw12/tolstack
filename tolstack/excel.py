"""Excel report: live formulas, verdicts, capability, contribution chart, section image,
2D evidence, an assumptions sheet and a reviewer sign-off block.

Every number a reviewer may want to change (nominal, tolerances, direction, distribution,
sigma level, spec limits) is an input cell; everything derived from them is a formula, so
editing one tolerance in Excel recalculates the whole stack. Monte Carlo is the only
snapshot value (it needs the Python sampler) and is labelled as such.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from .stack import Contributor, Result

HEADERS = ["Item", "Part", "Description", "Source", "Nominal", "+Tol", "-Tol", "Dir (+1/-1)", "Dist",
           "Mean shift", "Half range", "Signed mean", "Sigma", "Variance", "Contribution %"]
THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
INPUT_FILL = PatternFill("solid", fgColor="FFF9DB")
YELLOW = PatternFill("solid", fgColor="FFF200")
GREY = Font(color="666666", italic=True)


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
        ("Sigma level (RSS)", float(meta.get("sigma", result.sigma_level))),
    ]
    for i, (k, v) in enumerate(info, start=2):
        ws.cell(i, 1, k).font = Font(bold=True)
        ws.cell(i, 2, v)
    sigma_cell = "$B$10"
    ws[sigma_cell.replace("$", "")].fill = INPUT_FILL
    ws["C10"] = "input: tolerance = +/- this many sigma for 'normal' rows"
    ws["C10"].font = GREY
    ws["A11"] = "Yellow cells are inputs; everything else is a formula. Monte Carlo values are a snapshot."
    ws["A11"].font = GREY

    head = 13
    for col, name in enumerate(HEADERS, start=1):
        cell = ws.cell(head, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    first = head + 1
    last = head + len(contributors)
    dist_rule = DataValidation(type="list", formula1='"normal,uniform,triangular"', allow_blank=False)
    dir_rule = DataValidation(type="list", formula1='"1,-1"', allow_blank=False)
    ws.add_data_validation(dist_rule)
    ws.add_data_validation(dir_rule)
    for r, c in enumerate(contributors, start=first):
        values = [c.label, c.part, c.description, c.source, c.nominal, c.tol_plus, c.tol_minus, c.direction,
                  c.dist or "normal"]
        for col, v in enumerate(values, start=1):
            ws.cell(r, col, v).border = BOX
        ws.cell(r, 1).fill = YELLOW
        for col in (5, 6, 7, 8, 9):
            ws.cell(r, col).fill = INPUT_FILL
        ws.cell(r, 4).alignment = Alignment(wrap_text=True, vertical="top")
        dist_rule.add(ws.cell(r, 9))
        dir_rule.add(ws.cell(r, 8))
        ws.cell(r, 10, f"=(F{r}-G{r})/2")
        ws.cell(r, 11, f"=(F{r}+G{r})/2")
        ws.cell(r, 12, f"=H{r}*(E{r}+J{r})")
        ws.cell(r, 13, f'=K{r}/IF(I{r}="uniform",SQRT(3),IF(I{r}="triangular",SQRT(6),{sigma_cell}))')
        ws.cell(r, 14, f"=M{r}^2")
        ws.cell(r, 15, f"=IF(SUM($N${first}:$N${last})=0,0,100*N{r}/SUM($N${first}:$N${last}))")
        for col in range(5, 16):
            if col in (8, 9):
                continue
            ws.cell(r, col).number_format = "0.000"
            ws.cell(r, col).border = BOX

    s = last + 3
    rows = [
        ("Nominal", f"=SUMPRODUCT(H{first}:H{last},E{first}:E{last})"),                 # s
        ("Mean (with asymmetric tol shift)", f"=SUM(L{first}:L{last})"),               # s+1
        ("Worst case ±", f"=SUM(K{first}:K{last})"),                                   # s+2
        ("Worst case min", f"=B{s + 1}-B{s + 2}"),                                     # s+3
        ("Worst case max", f"=B{s + 1}+B{s + 2}"),                                     # s+4
        ("Sigma of the sum", f"=SQRT(SUM(N{first}:N{last}))"),                         # s+5
        ("RSS ±", f"={sigma_cell}*B{s + 5}"),                                          # s+6
        ("RSS min", f"=B{s + 1}-B{s + 6}"),                                            # s+7
        ("RSS max", f"=B{s + 1}+B{s + 6}"),                                            # s+8
        ("Spec LSL", result.lsl if result.lsl is not None else ""),                    # s+9
        ("Spec USL", result.usl if result.usl is not None else ""),                    # s+10
        ("RSS verdict", f'=IF(AND(OR(B{s + 9}="",B{s + 7}>=B{s + 9}),OR(B{s + 10}="",B{s + 8}<=B{s + 10})),"PASS","FAIL")'),
        ("WC verdict", f'=IF(AND(OR(B{s + 9}="",B{s + 3}>=B{s + 9}),OR(B{s + 10}="",B{s + 4}<=B{s + 10})),"PASS","FAIL")'),
        ("Cp", f'=IF(OR(B{s + 9}="",B{s + 10}="",B{s + 5}=0),"",(B{s + 10}-B{s + 9})/(6*B{s + 5}))'),
        ("Cpk", f'=IF(B{s + 5}=0,"",IF(AND(B{s + 9}="",B{s + 10}=""),"",MIN(IF(B{s + 10}="",1E+99,(B{s + 10}-B{s + 1})/(3*B{s + 5})),'
                f'IF(B{s + 9}="",1E+99,(B{s + 1}-B{s + 9})/(3*B{s + 5})))))'),
        ("Predicted out of spec (ppm)", f'=IF(B{s + 5}=0,"",1000000*(IF(B{s + 9}="",0,NORMSDIST((B{s + 9}-B{s + 1})/B{s + 5}))'
                                        f'+IF(B{s + 10}="",0,1-NORMSDIST((B{s + 10}-B{s + 1})/B{s + 5}))))'),
        ("Monte Carlo mean / std (snapshot)", "" if result.mc_mean is None else f"{result.mc_mean:.4f} / {result.mc_std:.4f}"),
        ("Monte Carlo out of spec % (snapshot)", "" if result.mc_out_pct is None else round(result.mc_out_pct, 4)),
    ]
    ws.cell(s - 1, 1, "Result").font = Font(size=13, bold=True)
    for i, (k, v) in enumerate(rows):
        ws.cell(s + i, 1, k).font = Font(bold=True)
        cell = ws.cell(s + i, 2, v)
        cell.number_format = "0.000"
        cell.border = BOX
    for spec_row in (s + 9, s + 10):
        ws.cell(spec_row, 2).fill = INPUT_FILL
    ws.cell(s + 15, 2).number_format = "0.0"
    if result.mc_samples:
        ws.cell(s + 17, 3, f"{result.mc_samples:,} samples, per-row distributions; re-run tolstack after edits").font = GREY

    chart = BarChart()
    chart.type = "bar"
    chart.title = "Variance contribution %"
    chart.y_axis.title = "%"
    chart.add_data(Reference(ws, min_col=15, min_row=head, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    chart.height = max(6, 0.6 * len(contributors) + 3)
    chart.width = 14
    ws.add_chart(chart, f"E{s}")

    sign = s + len(rows) + 2
    ws.cell(sign, 1, "Review").font = Font(size=13, bold=True)
    for col, name in enumerate(["Role", "Name", "Date", "Signature / comment"], start=1):
        cell = ws.cell(sign + 1, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
    for i, role in enumerate(["Prepared by", "Checked by", "Approved by"], start=sign + 2):
        ws.cell(i, 1, role).border = BOX
        for col in (2, 3, 4):
            ws.cell(i, col).border = BOX
            ws.cell(i, col).fill = INPUT_FILL
    ws.cell(sign + 2, 2, meta.get("author", ""))

    widths = [7, 22, 40, 44, 10, 8, 8, 9, 11, 10, 10, 11, 9, 10, 13]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = ws.cell(first, 3)

    row_img = sign + 7
    if section_png and Path(section_png).exists():
        ws.cell(row_img, 1, "Section").font = Font(size=13, bold=True)
        ws.add_image(_fit_image(section_png, 1100), f"A{row_img + 1}")

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{head}:{head}"

    _assumptions_sheet(wb, meta, result)

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


def _assumptions_sheet(wb, meta: dict, result: Result):
    ws = wb.create_sheet("Assumptions")
    ws["A1"] = "Inputs, method and assumptions"
    ws["A1"].font = Font(size=14, bold=True)
    lines = [
        ("Model", meta.get("step", "")),
        ("Chain used", meta.get("chain", "")),
    ]
    for alt in meta.get("alternatives", []):
        lines.append(("Other chain found", alt))
    lines += [
        ("Contact tolerance (mm)", meta.get("contact_tol", "")),
        ("Method", "1-D stack along the axis; worst case = sum of half ranges; RSS = k * sqrt(sum sigma^2)"),
        ("Asymmetric tolerances", "centred first: mean shift = (+tol - -tol)/2, half range = (+tol + -tol)/2"),
        ("Distributions", "normal: tol = +/- k sigma (k = sigma level); uniform: sigma = h/sqrt(3); triangular: sigma = h/sqrt(6)"),
        ("Cp / Cpk / ppm", "from the mean and sigma of the sum, normal approximation"),
        ("Monte Carlo", f"{result.mc_samples:,} samples, seed 1; snapshot, not recalculated by Excel"),
        ("Not covered", "angles, GD&T (position, flatness, parallelism), part deformation, temperature"),
        ("3D vs 2D", "the 3D nominal is used; when a drawing gives a different nominal its limits are kept "
                     "and the source column notes the difference"),
    ]
    for w in meta.get("warnings", []):
        lines.append(("Warning", w))
    row = 3
    for k, v in lines:
        ws.cell(row, 1, k).font = Font(bold=True)
        ws.cell(row, 2, str(v)).alignment = Alignment(wrap_text=True, vertical="top")
        row += 1
    row += 1
    ws.cell(row, 1, "General tolerance table (used when the 2D gives no tolerance)").font = Font(bold=True)
    row += 1
    for col, name in enumerate(["from mm", "to mm", "± tol"], start=1):
        cell = ws.cell(row, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
    for lo, hi, tol in meta.get("general_table", []):
        row += 1
        ws.cell(row, 1, lo)
        ws.cell(row, 2, hi if hi < 1e8 else "and above")
        ws.cell(row, 3, tol)
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
