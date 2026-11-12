"""1-D tolerance stack-up math: worst case, RSS and Monte Carlo."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class Contributor:
    label: str
    part: str
    description: str
    nominal: float
    tol_plus: float      # upper deviation (>= 0)
    tol_minus: float     # lower deviation magnitude (>= 0), e.g. 2 +0/-0.05 -> tol_minus = 0.05
    direction: int       # +1 additive, -1 subtractive
    source: str = ""
    ref_image: str = ""  # optional path to a 2D screenshot proving the tolerance

    @property
    def mean_shift(self) -> float:
        return (self.tol_plus - self.tol_minus) / 2

    @property
    def half_range(self) -> float:
        return (self.tol_plus + self.tol_minus) / 2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    nominal: float
    mean: float
    wc_min: float
    wc_max: float
    rss: float
    rss_min: float
    rss_max: float
    contributions: list      # percent of RSS variance per contributor
    lsl: float | None = None
    usl: float | None = None
    mc_mean: float | None = None
    mc_std: float | None = None
    mc_out_pct: float | None = None

    def verdict(self, method: str = "rss") -> str:
        lo, hi = (self.rss_min, self.rss_max) if method == "rss" else (self.wc_min, self.wc_max)
        if self.lsl is not None and lo < self.lsl:
            return "FAIL"
        if self.usl is not None and hi > self.usl:
            return "FAIL"
        return "PASS"


def analyze(contributors: list[Contributor], lsl: float | None = None, usl: float | None = None,
            mc_samples: int = 100_000, sigma_level: float = 3.0, seed: int = 1) -> Result:
    if not contributors:
        raise ValueError("no contributors")
    nominal = sum(c.direction * c.nominal for c in contributors)
    mean = sum(c.direction * (c.nominal + c.mean_shift) for c in contributors)
    halves = [c.half_range for c in contributors]
    wc = sum(halves)
    rss = math.sqrt(sum(h * h for h in halves))
    variance = sum(h * h for h in halves) or 1.0
    contributions = [100.0 * h * h / variance for h in halves]
    result = Result(nominal, mean, mean - wc, mean + wc, rss, mean - rss, mean + rss,
                    contributions, lsl, usl)
    if mc_samples:
        rng = np.random.default_rng(seed)
        total = np.zeros(mc_samples)
        for c in contributors:
            sigma = c.half_range / sigma_level
            total += c.direction * rng.normal(c.nominal + c.mean_shift, sigma, mc_samples) if sigma > 0 \
                else c.direction * (c.nominal + c.mean_shift)
        out = np.zeros(mc_samples, dtype=bool)
        if lsl is not None:
            out |= total < lsl
        if usl is not None:
            out |= total > usl
        result.mc_mean = float(total.mean())
        result.mc_std = float(total.std())
        result.mc_out_pct = float(100.0 * out.mean())
    return result
