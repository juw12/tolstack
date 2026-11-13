"""Two pictures of a finished stack-up, sized to paste into a mail or a slide.

<report>_chart.png   range bars (spec, worst case, RSS, each measured group, the limit line) next to the
                     variance contribution of every row
<report>_table.png   the stack table and the result block as one clean picture

Both are drawn from the same numbers as the Excel report. matplotlib is imported only here, with the Agg
backend, so nothing needs a display.
"""
from __future__ import annotations

from pathlib import Path

from .measured import Group, Limit
from .stack import Contributor, Result

BLUE, ORANGE, GREEN, RED, GREY = "#2F6DB5", "#E08A1E", "#3A9A5B", "#C8362E", "#8A8F98"
CJK_FONTS = ("Microsoft JhengHei", "Microsoft YaHei", "PingFang TC", "Noto Sans CJK TC", "Noto Sans CJK SC")


def _pyplot():
    try:
        import matplotlib
    except ImportError as err:                                    # pragma: no cover - depends on the install
        raise ValueError("pictures need matplotlib: pip install matplotlib") from err
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    cjk = [name for name in CJK_FONTS if name in installed]      # part names are often not ASCII
    plt.rcParams["font.sans-serif"] = [*cjk, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def chart_rows(result: Result, groups: list[Group]) -> list[tuple[str, float, float, str]]:
    """(label, low, high, colour) of the range bars, top to bottom."""
    rows = []
    if result.lsl is not None and result.usl is not None:
        rows.append(("Spec", result.lsl, result.usl, GREY))
    rows.append(("Worst case", result.wc_min, result.wc_max, ORANGE))
    rows.append((f"RSS (±{result.sigma_level:g}σ)", result.rss_min, result.rss_max, BLUE))
    for g in groups:
        name = f"{g.name} ({g.result})" if g.result else g.name
        rows.append((f"Measured: {name}", g.low, g.high, GREEN))
    return rows


def write_chart(path: str | Path, contributors: list[Contributor], result: Result, groups: list[Group] | None = None,
                limit: Limit | None = None, title: str = "") -> str:
    plt = _pyplot()
    groups = groups or []
    rows = chart_rows(result, groups)
    height = max(3.2, 0.55 * max(len(rows), len(contributors)) + 1.6)
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, height), gridspec_kw={"width_ratios": [3, 2]})

    values_of = {f"Measured: {g.name} ({g.result})" if g.result else f"Measured: {g.name}": g.values for g in groups}
    for y, (label, low, high, colour) in enumerate(rows):
        left.barh(y, high - low, left=low, height=0.5, color=colour, alpha=0.85)
        left.text(high, y, f"  {low:.3f} ~ {high:.3f}", va="center", fontsize=9)
        points = values_of.get(label)
        if points:
            left.plot(points, [y] * len(points), "o", color="black", markersize=3.5, zorder=3)
    left.set_yticks(range(len(rows)))
    left.set_yticklabels([row[0] for row in rows])
    left.invert_yaxis()
    left.axvline(result.nominal, color="black", linewidth=1, linestyle=":")
    left.text(result.nominal, -0.62, f"nominal {result.nominal:.3f}", ha="center", fontsize=9)
    for edge, name in ((result.lsl, "LSL"), (result.usl, "USL")):
        if edge is not None and not (result.lsl is not None and result.usl is not None):
            left.axvline(edge, color=GREY, linewidth=1.5)
            left.text(edge, len(rows) - 0.45, f"{name} {edge:g}", ha="center", fontsize=9, color=GREY)
    if limit is not None:
        left.axvline(limit.value, color=RED, linewidth=1.8, linestyle="--")
        left.text(limit.value, len(rows) - 0.45, f"{limit.label} {limit.value:.3f}", ha="center", fontsize=9, color=RED,
                  bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5})
    low_edge, high_edge = left.get_xlim()
    left.set_xlim(low_edge, high_edge + 0.22 * (high_edge - low_edge))       # room for the value labels
    left.set_ylim(len(rows) - 0.2, -0.9)
    left.set_xlabel("result (mm)")
    left.set_title("Range of the result")
    left.grid(axis="x", alpha=0.3)

    labels = [c.label for c in contributors]
    shares = result.contributions
    right.barh(range(len(labels)), shares, color=BLUE, alpha=0.85)
    for y, share in enumerate(shares):
        right.text(share, y, f" {share:.1f} %", va="center", fontsize=9)
    right.set_yticks(range(len(labels)))
    right.set_yticklabels(labels)
    right.invert_yaxis()
    right.set_xlim(0, max(shares or [1]) * 1.25 or 1)
    right.set_xlabel("% of the variance")
    right.set_title("Contribution of each row")
    right.grid(axis="x", alpha=0.3)

    if title:
        fig.suptitle(title, fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(str(path), dpi=200)
    plt.close(fig)
    return str(path)


def table_cells(contributors: list[Contributor], result: Result, limit: Limit | None = None) -> tuple[list, list]:
    """(stack rows, result rows) as text, the content of the table picture."""
    stack = [["Item", "Part", "Description", "Nominal", "+Tol", "-Tol", "Dir", "Contribution"]]
    for c, share in zip(contributors, result.contributions, strict=True):
        stack.append([c.label, c.part, c.description, f"{c.nominal:.3f}", f"+{c.tol_plus:.3f}", f"-{c.tol_minus:.3f}",
                      "+" if c.direction > 0 else "-", f"{share:.1f} %"])
    spec = ""
    if result.lsl is not None or result.usl is not None:
        low = "-" if result.lsl is None else f"{result.lsl:g}"
        high = "-" if result.usl is None else f"{result.usl:g}"
        spec = f"{low} ~ {high}"
    summary = [
        ["Nominal", f"{result.nominal:.3f}", ""],
        ["Worst case", f"{result.wc_min:.3f} ~ {result.wc_max:.3f}", result.verdict("wc") if spec else ""],
        [f"RSS (±{result.sigma_level:g}σ)", f"{result.rss_min:.3f} ~ {result.rss_max:.3f}",
         result.verdict("rss") if spec else ""],
    ]
    if spec:
        summary.append(["Spec", spec, ""])
    if limit is not None:
        summary.append([limit.label, f"{limit.value:.3f}", f"{limit.side} limit"])
    return stack, summary


def write_table(path: str | Path, contributors: list[Contributor], result: Result, limit: Limit | None = None,
                title: str = "") -> str:
    plt = _pyplot()
    stack, summary = table_cells(contributors, result, limit)
    # one axes per table, each table filling its axes, so the row height is the same in both
    fig, (ax, ax_summary) = plt.subplots(2, 1, figsize=(12, 0.36 * (len(stack) + len(summary)) + 1.0),
                                         gridspec_kw={"height_ratios": [len(stack), len(summary)], "hspace": 0.18})
    ax.axis("off")
    ax_summary.axis("off")
    widths = [0.05, 0.16, 0.37, 0.09, 0.08, 0.08, 0.05, 0.12]
    top = ax.table(cellText=stack[1:], colLabels=stack[0], colWidths=widths, cellLoc="center", bbox=[0, 0, 1, 1])
    bottom = ax_summary.table(cellText=summary, colWidths=[0.21, 0.25, 0.16], cellLoc="center", bbox=[0, 0, 0.62, 1])
    for table in (top, bottom):
        table.auto_set_font_size(False)
        table.set_fontsize(10)
    for (row, col), cell in top.get_celld().items():
        cell.set_edgecolor("#999999")
        if row == 0:
            cell.set_facecolor("#DCE6F1")
            cell.set_text_props(fontweight="bold")
        elif col in (1, 2):
            cell.set_text_props(ha="left")
            cell.PAD = 0.02
    for (_row, col), cell in bottom.get_celld().items():
        cell.set_edgecolor("#999999")
        if col == 0:
            cell.set_facecolor("#DCE6F1")
            cell.set_text_props(fontweight="bold")
        if cell.get_text().get_text() in ("PASS", "FAIL"):
            cell.set_text_props(color=GREEN if cell.get_text().get_text() == "PASS" else RED, fontweight="bold")
    if title:
        ax.set_title(title, fontsize=13, fontweight="bold", loc="left")
    fig.savefig(str(path), dpi=200, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return str(path)


def write_pictures(stem: str | Path, contributors: list[Contributor], result: Result,
                   groups: list[Group] | None = None, limit: Limit | None = None, title: str = "") -> tuple[str, str]:
    """Write `<stem>_chart.png` and `<stem>_table.png`; returns both paths."""
    stem = Path(stem)
    chart = write_chart(stem.with_name(f"{stem.name}_chart.png"), contributors, result, groups, limit, title)
    table = write_table(stem.with_name(f"{stem.name}_table.png"), contributors, result, limit, title)
    return chart, table
