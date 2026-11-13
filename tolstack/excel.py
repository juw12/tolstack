"""Excel report: live formulas, verdicts, capability, contribution chart, section image,
2D evidence, a sensitivity (tornado) sheet, an assumptions sheet and a reviewer sign-off block.

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

from .measured import Group, Limit
from .stack import Contributor, Result, sensitivity

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
                 section_png: str | None = None, measured: list[Group] | None = None, limit: Limit | None = None,
                 chart_png: str | None = None) -> str:
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
        ("Sign convention", meta.get("sign", "Result = To level - From level (+ = along +axis)")),
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
        ("Cpk", f'=IF(B{s + 5}=0,"",IF(AND(B{s + 9}="",B{s + 10}=""),"",'
                f'MIN(IF(B{s + 10}="",1E+99,(B{s + 10}-B{s + 1})/(3*B{s + 5})),'
                f'IF(B{s + 9}="",1E+99,(B{s + 1}-B{s + 9})/(3*B{s + 5})))))'),
        ("Predicted out of spec (ppm)", f'=IF(B{s + 5}=0,"",1000000*(IF(B{s + 9}="",0,NORMSDIST((B{s + 9}-B{s + 1})/B{s + 5}))'
                                        f'+IF(B{s + 10}="",0,1-NORMSDIST((B{s + 10}-B{s + 1})/B{s + 5}))))'),
        ("Predicted yield %", f'=IF(B{s + 15}="","",100-B{s + 15}/10000)'),
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
    ws.cell(s + 16, 2).number_format = "0.000"
    if result.mc_samples:
        ws.cell(s + 18, 3, f"{result.mc_samples:,} samples, per-row distributions; re-run tolstack after edits").font = GREY

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

    _sensitivity_sheet(wb, contributors, result, first, last, rss_row=s + 6, sigma_cell=sigma_cell)
    if measured or limit is not None:
        _measured_sheet(wb, measured or [], limit, result_row=s)
    if chart_png and Path(chart_png).exists():
        ws_chart = wb.create_sheet("Chart")
        ws_chart["A1"] = "Range of the result and contribution of each row (picture from the export, not live)"
        ws_chart["A1"].font = Font(size=14, bold=True)
        ws_chart.add_image(_fit_image(chart_png, 1100), "A3")
    _assumptions_sheet(wb, meta, result)

    refs = [c for c in contributors if c.ref_image and Path(c.ref_image).exists()]
    if refs:
        ws2 = wb.create_sheet("2D references")
        ws2["A1"] = "2D reference - where each tolerance comes from (red box = dimension used)"
        ws2["A1"].font = Font(size=14, bold=True)
        row = 3
        for c in refs:
            title = f"{c.label}  {c.part}: {c.nominal} +{c.tol_plus}/-{c.tol_minus}  ({c.source})"
            ws2.cell(row, 1, title).font = Font(bold=True)
            img = _fit_image(c.ref_image, 900)
            ws2.add_image(img, f"A{row + 1}")
            row += int(img.height / 20) + 3
    wb.save(str(path))
    return str(path)


def _sensitivity_sheet(wb, contributors: list[Contributor], result: Result, first: int, last: int,
                       rss_row: int, sigma_cell: str) -> None:
    """Tornado table and chart. The row order is fixed at export (largest variance share first);
    every number is a formula on the Stack-up sheet, so it follows edits made there."""
    ws = wb.create_sheet("Sensitivity")
    ws["A1"] = "Sensitivity - which tolerance moves the result most"
    ws["A1"].font = Font(size=14, bold=True)
    ws["A2"] = ("Low / High = shift of the result when that row alone sits at its lower / upper limit. "
                "'RSS ± without' = the RSS band if the row had no tolerance; 'Gain' = what tightening it to zero "
                "would buy. Row order is from the export; the values are live formulas.")
    ws["A2"].font = GREY
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:I2")
    ws.row_dimensions[2].height = 44
    head = 4
    names = ["Item", "Part", "Low", "High", "Swing", "Variance %", "Worst-case %", "RSS ± without", "Gain"]
    for col, name in enumerate(names, start=1):
        cell = ws.cell(head, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    src = "'Stack-up'!"
    half_sum = f"SUM({src}$K${first}:$K${last})"
    var_sum = f"SUM({src}$N${first}:$N${last})"
    rows = sensitivity(contributors, result)
    for r, row in enumerate(rows, start=head + 1):
        k = first + row["index"]
        ends = f"{src}H{k}*({src}J{k}-{src}K{k}),{src}H{k}*({src}J{k}+{src}K{k})"
        formulas = [
            f"={src}A{k}",
            f"={src}B{k}",
            f"=MIN({ends})",
            f"=MAX({ends})",
            f"=D{r}-C{r}",
            f"={src}O{k}",
            f"=IF({half_sum}=0,0,100*{src}K{k}/{half_sum})",
            f"={src}{sigma_cell}*SQRT(MAX(0,{var_sum}-{src}N{k}))",
            f"={src}$B${rss_row}-H{r}",
        ]
        for col, formula in enumerate(formulas, start=1):
            cell = ws.cell(r, col, formula)
            cell.border = BOX
            if col >= 3:
                cell.number_format = "0.0" if col in (6, 7) else "0.000"
    end = head + len(rows)
    chart = BarChart()
    chart.type = "bar"
    chart.grouping = "clustered"
    chart.overlap = 100
    chart.title = "Tornado: result shift with each row at its limits"
    chart.y_axis.title = "shift of the result"
    chart.x_axis.scaling.orientation = "maxMin"          # largest contributor on top
    chart.add_data(Reference(ws, min_col=3, max_col=4, min_row=head, max_row=end), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=head + 1, max_row=end))
    chart.height = max(7, 0.7 * len(rows) + 3)
    chart.width = 18
    ws.add_chart(chart, f"A{end + 2}")
    for letter, width in zip("ABCDEFGHI", [8, 24, 10, 10, 10, 11, 13, 14, 10], strict=True):
        ws.column_dimensions[letter].width = width


def _measured_sheet(wb, groups: list[Group], limit: Limit | None, result_row: int) -> None:
    """Measured groups and the limit against the stack. The stack values are live references to the
    Stack-up sheet and the fitted limit is SLOPE / INTERCEPT on the fit points, so editing a tolerance,
    a reading or a test point recalculates every verdict here."""
    ws = wb.create_sheet("Measured")
    ws["A1"] = "Measured parts and the limit against the stack-up"
    ws["A1"].font = Font(size=14, bold=True)
    ws["A2"] = ("Yellow cells are inputs. The stack values come from the Stack-up sheet. 'Beyond the limit %' uses "
                "the mean and sigma of the stack (normal approximation).")
    ws["A2"].font = GREY
    src = "'Stack-up'!$B$"
    stack = [("Mean", result_row + 1), ("RSS min", result_row + 7), ("RSS max", result_row + 8),
             ("Worst case min", result_row + 3), ("Worst case max", result_row + 4), ("Sigma of the sum", result_row + 5)]
    for r, (name, row) in enumerate(stack, start=4):                      # B4..B9
        ws.cell(r, 1, name).font = Font(bold=True)
        cell = ws.cell(r, 2, f"={src}{row}")
        cell.number_format = "0.000"
        cell.border = BOX

    head = 17
    fit_row = head + len(groups) + 3                                      # first row of the fit block
    names = [(11, "Limit"), (12, "Limit side (lower / upper)"), (13, "Predicted beyond the limit %"),
             (14, "RSS margin to the limit"), (15, "Worst-case margin to the limit")]
    for r, name in names:
        ws.cell(r, 1, name).font = Font(bold=True)
        ws.cell(r, 2).border = BOX
        ws.cell(r, 2).number_format = "0.000"
    if limit is not None:
        ws["C11"] = limit.label
        ws["C11"].font = GREY
        if limit.fitted:
            ws["B11"] = f"=(B{fit_row + 3}-B{fit_row + 2})/B{fit_row + 1}"
        else:
            ws["B11"] = limit.value
            ws["B11"].fill = INPUT_FILL
        ws["B12"] = limit.side
        ws["B12"].fill = INPUT_FILL
        side_rule = DataValidation(type="list", formula1='"lower,upper"', allow_blank=False)
        ws.add_data_validation(side_rule)
        side_rule.add(ws["B12"])
    ws["B13"] = ('=IF($B$11="","",IF($B$9=0,IF(OR(AND($B$12="lower",$B$4<$B$11),AND($B$12="upper",$B$4>$B$11)),100,0),'
                 '100*IF($B$12="lower",NORMSDIST(($B$11-$B$4)/$B$9),1-NORMSDIST(($B$11-$B$4)/$B$9))))')
    ws["B13"].number_format = "0.00"
    ws["B14"] = '=IF($B$11="","",IF($B$12="lower",$B$5-$B$11,$B$11-$B$6))'
    ws["B15"] = '=IF($B$11="","",IF($B$12="lower",$B$7-$B$11,$B$11-$B$8))'

    columns = ["Group", "Result", "n", "Min", "Max", "Mean", "Inside RSS range", "Inside worst-case range",
               "Against the limit", "Readings beyond the limit", "Readings ->"]
    for col, name in enumerate(columns, start=1):
        cell = ws.cell(head, col, name)
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.border = BOX
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
    for r, g in enumerate(groups, start=head + 1):
        ws.cell(r, 1, g.name)
        ws.cell(r, 2, g.result)
        if g.values:
            first_col = ws.cell(r, 12).column_letter
            last_col = ws.cell(r, 11 + len(g.values)).column_letter
            readings = f"${first_col}{r}:${last_col}{r}"
            for col, value in enumerate(g.values, start=12):
                ws.cell(r, col, value).fill = INPUT_FILL
            ws.cell(r, 3, f"=COUNT({readings})")
            ws.cell(r, 4, f"=MIN({readings})")
            ws.cell(r, 5, f"=MAX({readings})")
            ws.cell(r, 6, f"=AVERAGE({readings})")
            ws.cell(r, 10, f'=IF($B$11="","",IF($B$12="lower",COUNTIF({readings},"<"&$B$11),COUNTIF({readings},">"&$B$11)))')
        else:
            ws.cell(r, 4, g.low).fill = INPUT_FILL
            ws.cell(r, 5, g.high).fill = INPUT_FILL
        ws.cell(r, 7, f'=IF(AND(D{r}>=$B$5,E{r}<=$B$6),"yes","no")')
        ws.cell(r, 8, f'=IF(AND(D{r}>=$B$7,E{r}<=$B$8),"yes","no")')
        ws.cell(r, 9, f'=IF($B$11="","",IF($B$12="lower",'
                      f'IF(E{r}<$B$11,"all beyond",IF(D{r}>=$B$11,"all inside","some beyond")),'
                      f'IF(D{r}>$B$11,"all beyond",IF(E{r}<=$B$11,"all inside","some beyond"))))')
        for col in range(1, 11):
            ws.cell(r, col).border = BOX
        for col in (4, 5, 6):
            ws.cell(r, col).number_format = "0.000"

    if limit is not None and limit.fitted:
        last = fit_row + 5 + len(limit.x)
        points_x, points_y = f"$A${fit_row + 6}:$A${last}", f"$B${fit_row + 6}:$B${last}"
        ws.cell(fit_row, 1, f"Limit fit: y = slope * x + intercept, {limit.label} = x where y = y limit").font = Font(bold=True)
        block = [("Slope", f"=SLOPE({points_y},{points_x})"), ("Intercept", f"=INTERCEPT({points_y},{points_x})"),
                 ("y limit", limit.y_limit), ("R squared", f"=RSQ({points_y},{points_x})")]
        for r, (name, content) in enumerate(block, start=fit_row + 1):
            ws.cell(r, 1, name).font = Font(bold=True)
            cell = ws.cell(r, 2, content)
            cell.border = BOX
            cell.number_format = "0.0000"
        ws.cell(fit_row + 3, 2).fill = INPUT_FILL
        for col, name in enumerate(["x (result)", "y (test)"], start=1):
            cell = ws.cell(fit_row + 5, col, name)
            cell.font = Font(bold=True)
            cell.fill = HEAD_FILL
            cell.border = BOX
        for r, (x, y) in enumerate(zip(limit.x, limit.y, strict=True), start=fit_row + 6):
            for col, value in ((1, x), (2, y)):
                cell = ws.cell(r, col, value)
                cell.fill = INPUT_FILL
                cell.border = BOX
    for letter, width in zip("ABCDEFGHIJK", [30, 12, 8, 10, 10, 10, 12, 14, 14, 14, 11], strict=True):
        ws.column_dimensions[letter].width = width


def _assumptions_sheet(wb, meta: dict, result: Result):
    ws = wb.create_sheet("Assumptions")
    ws["A1"] = "Inputs, method and assumptions"
    ws["A1"].font = Font(size=14, bold=True)
    lines = [("Model", meta.get("step", ""))]
    if meta.get("manual"):
        lines.append(("Length unit", "mm, as written in the rows"))
    else:
        lines.append(("Length unit of the STEP file", f"{meta.get('units', 'mm')} (all values in this report are in mm)"))
    lines.append(("Chain used", meta.get("chain", "")))
    for alt in meta.get("alternatives", []):
        lines.append(("Other chain found", alt))
    if not meta.get("manual"):
        lines.append(("Contact tolerance (mm)", meta.get("contact_tol", "")))
    if meta.get("diameter_rows"):
        lines.append(("Diameter rows", f"{', '.join(meta['diameter_rows'])}: written as a diameter on the drawing; the "
                                       "tolerance was looked up at that size, then size and tolerance were halved "
                                       "for a one-side (radial) stack"))
    for line in meta.get("measured_lines", []):
        lines.append(("Measured / limit (snapshot)", line))
    lines += [
        ("Method", "1-D stack along the axis; worst case = sum of half ranges; RSS = k * sqrt(sum sigma^2)"),
        ("Asymmetric tolerances", "centred first: mean shift = (+tol - -tol)/2, half range = (+tol + -tol)/2"),
        ("Distributions",
         "normal: tol = +/- k sigma (k = sigma level); uniform: sigma = h/sqrt(3); triangular: sigma = h/sqrt(6)"),
        ("Cp / Cpk / ppm", "from the mean and sigma of the sum, normal approximation"),
        ("Monte Carlo", f"{result.mc_samples:,} samples, seed 1; snapshot, not recalculated by Excel"),
        ("Not covered", "angles, part deformation, temperature. GD&T callouts found on the drawings are listed "
                        "below; they are in the stack only when added as a row (zone width t -> ± t/2)"),
        ("3D vs 2D", "the 3D nominal is used; when a drawing gives a different nominal its limits are kept "
                     "and the source column notes the difference"),
    ]
    for callout in meta.get("gdt", []):
        lines.append(("GD&T on the drawings", callout))
    for general in meta.get("general_notes", []):
        lines.append(("General tolerance on the drawing", general))
    for w in meta.get("warnings", []):
        lines.append(("Warning", w))
    row = 3
    for k, v in lines:
        ws.cell(row, 1, k).font = Font(bold=True)
        ws.cell(row, 2, str(v)).alignment = Alignment(wrap_text=True, vertical="top")
        row += 1
    row += 1
    heading = "General tolerance table (used when the 2D gives no tolerance and no general-tolerance note)"
    ws.cell(row, 1, heading).font = Font(bold=True)
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
