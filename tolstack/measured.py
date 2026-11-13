"""Measured parts against the stack-up, and a functional limit on the result.

A stack-up says what the drawings allow. Two more questions usually follow:

- where do the measured parts sit inside that range?  -> `measured` groups (good units, failed units ...)
- which result still works?                           -> `limit`, a value typed in or read off a test fit

The limit can be fitted from test points: x = the stack result (squeeze, gap ...), y = what was measured in
the test (hold pressure, force ...). A straight line y = slope * x + intercept is fitted by least squares
and the limit is the x where the line crosses `y_limit`.

The share of assemblies beyond the limit comes from the mean and sigma of the stack (normal approximation),
the same way as the predicted ppm out of spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .stack import Result, _phi

SIDES = ("lower", "upper")


@dataclass
class Group:
    name: str
    low: float
    high: float
    values: list[float] = field(default_factory=list)   # empty when only min / max was given
    result: str = ""                                    # free text, e.g. "leak", "pass"

    @property
    def count(self) -> int | None:
        return len(self.values) or None

    @property
    def mean(self) -> float | None:
        return sum(self.values) / len(self.values) if self.values else None


@dataclass
class Limit:
    value: float
    side: str = "lower"            # "lower": results below the limit fail; "upper": results above it fail
    label: str = "limit"
    x: list[float] = field(default_factory=list)        # fit data, empty when the value was typed in
    y: list[float] = field(default_factory=list)
    y_limit: float | None = None
    slope: float | None = None
    intercept: float | None = None
    r2: float | None = None

    @property
    def fitted(self) -> bool:
        return self.slope is not None

    def beyond(self, value: float) -> bool:
        return value < self.value if self.side == "lower" else value > self.value


def fit_line(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Least-squares line y = slope * x + intercept; returns (slope, intercept, r squared)."""
    if len(x) != len(y):
        raise ValueError(f"limit fit: x has {len(x)} points and y has {len(y)}")
    if len(x) < 2:
        raise ValueError("limit fit needs at least 2 points")
    n = len(x)
    mean_x, mean_y = sum(x) / n, sum(y) / n
    sxx = sum((v - mean_x) ** 2 for v in x)
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    syy = sum((v - mean_y) ** 2 for v in y)
    if sxx == 0:
        raise ValueError("limit fit: all x values are the same")
    slope = sxy / sxx
    r2 = 1.0 if syy == 0 else sxy * sxy / (sxx * syy)
    return slope, mean_y - slope * mean_x, r2


def read_groups(rows: list[dict] | None) -> list[Group]:
    """`measured` of a config: [{name, values: [...]}, {name, min, max}], each with an optional `result`."""
    groups = []
    for index, row in enumerate(rows or [], start=1):
        unknown = sorted(set(row) - {"name", "values", "min", "max", "result"})
        if unknown:
            raise ValueError(f"measured group {index}: unknown keys {unknown}")
        name = str(row.get("name", f"group {index}"))
        values = [float(v) for v in row.get("values") or []]
        if values:
            low, high = min(values), max(values)
        elif "min" in row and "max" in row:
            low, high = float(row["min"]), float(row["max"])
            if low > high:
                raise ValueError(f"measured group '{name}': min {low} is above max {high}")
        else:
            raise ValueError(f"measured group '{name}' needs 'values' or both 'min' and 'max'")
        groups.append(Group(name, low, high, values, str(row.get("result", ""))))
    return groups


def read_limit(config: dict | None) -> Limit | None:
    """`limit` of a config: {value} or {x: [...], y: [...], y_limit}, plus optional side and label."""
    if not config:
        return None
    unknown = sorted(set(config) - {"value", "x", "y", "y_limit", "side", "label"})
    if unknown:
        raise ValueError(f"limit: unknown keys {unknown}")
    side = str(config.get("side", "lower")).lower()
    if side not in SIDES:
        raise ValueError(f"limit side must be one of {SIDES}, got '{side}'")
    label = str(config.get("label", "limit"))
    if "value" in config:
        if "x" in config or "y" in config:
            raise ValueError("limit: give either 'value' or the fit data 'x', 'y', 'y_limit', not both")
        return Limit(float(config["value"]), side, label)
    for key in ("x", "y", "y_limit"):
        if key not in config:
            raise ValueError("limit needs 'value', or 'x', 'y' and 'y_limit' for a fitted limit")
    x = [float(v) for v in config["x"]]
    y = [float(v) for v in config["y"]]
    y_limit = float(config["y_limit"])
    slope, intercept, r2 = fit_line(x, y)
    if slope == 0:
        raise ValueError("limit fit: the fitted line is flat, so it never crosses y_limit")
    return Limit((y_limit - intercept) / slope, side, label, x, y, y_limit, slope, intercept, r2)


def beyond_limit_pct(result: Result, limit: Limit) -> float:
    """Predicted % of assemblies beyond the limit, from the mean and sigma of the stack (normal approximation)."""
    if result.sigma <= 0:
        return 100.0 if limit.beyond(result.mean) else 0.0
    below = _phi((limit.value - result.mean) / result.sigma)
    return 100.0 * (below if limit.side == "lower" else 1.0 - below)


def margins(result: Result, limit: Limit) -> tuple[float, float]:
    """(RSS margin, worst-case margin) to the limit; negative = that range reaches past the limit."""
    if limit.side == "lower":
        return result.rss_min - limit.value, result.wc_min - limit.value
    return limit.value - result.rss_max, limit.value - result.wc_max


def compare(groups: list[Group], result: Result, limit: Limit | None = None) -> list[dict]:
    """One line per measured group: is it inside the RSS range, the worst-case range, and the limit?

    vs_limit   "all inside", "some beyond" or "all beyond" ("" without a limit); `beyond` counts the readings
               past the limit when the group was given as values, else None.
    """
    rows = []
    for g in groups:
        row: dict = {
            "name": g.name, "result": g.result, "count": g.count, "low": g.low, "high": g.high, "mean": g.mean,
            "in_rss": result.rss_min <= g.low and g.high <= result.rss_max,
            "in_wc": result.wc_min <= g.low and g.high <= result.wc_max,
            "vs_limit": "", "beyond": None,
        }
        if limit is not None:
            ends = [limit.beyond(g.low), limit.beyond(g.high)]
            row["vs_limit"] = "all beyond" if all(ends) else "some beyond" if any(ends) else "all inside"
            if g.values:
                row["beyond"] = sum(limit.beyond(v) for v in g.values)
        rows.append(row)
    return rows


def summary_lines(groups: list[Group], result: Result, limit: Limit | None = None) -> list[str]:
    """Plain-text lines for the command line and the Assumptions sheet."""
    lines = []
    if limit is not None:
        how = "typed in"
        if limit.fitted:
            how = (f"fit y = {limit.slope:.4g} x + {limit.intercept:.4g} at y = {limit.y_limit:g} "
                   f"({len(limit.x)} points, R² {limit.r2:.3f})")
        rss_margin, wc_margin = margins(result, limit)
        lines.append(f"{limit.label}: {limit.value:.3f} ({limit.side} limit, {how}) | RSS margin {rss_margin:+.3f} | "
                     f"WC margin {wc_margin:+.3f} | predicted {beyond_limit_pct(result, limit):.2f} % beyond")
    for row in compare(groups, result, limit):
        count = "" if row["count"] is None else f" n={row['count']}"
        text = (f"measured {row['name']}{count}: {row['low']:.3f} ~ {row['high']:.3f} | "
                f"{'inside' if row['in_rss'] else 'outside'} RSS range | "
                f"{'inside' if row['in_wc'] else 'outside'} WC range")
        if row["vs_limit"]:
            text += f" | {row['vs_limit']} the limit"
            if row["beyond"] is not None:
                text += f" ({row['beyond']} of {row['count']} beyond)"
        lines.append(text)
    return lines
