"""1-D tolerance stack-up math: worst case, RSS, process capability and Monte Carlo.

Conventions
- A contributor's tolerance band is [nominal - tol_minus, nominal + tol_plus].
- Asymmetric bands are centred first: mean = nominal + (tol_plus - tol_minus) / 2,
  half range h = (tol_plus + tol_minus) / 2.
- Distribution of each contributor inside its band:
    normal      the band is +/- `sigma_level` sigma (default 3)   sigma = h / sigma_level
    uniform     flat over the band                               sigma = h / sqrt(3)
    triangular  peak at the band centre                          sigma = h / sqrt(6)
- RSS result = mean +/- sigma_level * sqrt(sum sigma_i^2). With all-normal contributors this is
  the classic sqrt(sum h_i^2).
- Cp = (USL - LSL) / (6 sigma), Cpk = min(USL - mean, mean - LSL) / (3 sigma), predicted
  out-of-spec ppm from the normal approximation of the sum.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

DISTRIBUTIONS = ("normal", "uniform", "triangular")


@dataclass
class Contributor:
    label: str
    part: str
    description: str
    nominal: float
    tol_plus: float      # upper deviation, e.g. 2 +0.1/-0.05 -> 0.1
    tol_minus: float     # lower deviation magnitude, e.g. 2 +0.1/-0.05 -> 0.05
    direction: int       # +1 additive, -1 subtractive
    source: str = ""
    ref_image: str = ""  # optional path to a 2D screenshot proving the tolerance
    dist: str = "normal"

    @property
    def mean_shift(self) -> float:
        return (self.tol_plus - self.tol_minus) / 2

    @property
    def half_range(self) -> float:
        return (self.tol_plus + self.tol_minus) / 2

    def sigma(self, sigma_level: float = 3.0) -> float:
        return self.half_range / sigma_divisor(self.dist, sigma_level)

    def to_dict(self) -> dict:
        return asdict(self)


def sigma_divisor(dist: str, sigma_level: float = 3.0) -> float:
    dist = (dist or "normal").lower()
    if dist == "uniform":
        return math.sqrt(3)
    if dist == "triangular":
        return math.sqrt(6)
    if dist != "normal":
        raise ValueError(f"unknown distribution '{dist}', use one of {DISTRIBUTIONS}")
    return sigma_level


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class Result:
    nominal: float
    mean: float
    wc_min: float
    wc_max: float
    rss: float
    rss_min: float
    rss_max: float
    contributions: list      # percent of the total variance per contributor
    lsl: float | None = None
    usl: float | None = None
    sigma: float = 0.0       # standard deviation of the sum
    sigma_level: float = 3.0
    cp: float | None = None
    cpk: float | None = None
    ppm: float | None = None  # predicted out-of-spec parts per million (normal approximation)
    mc_mean: float | None = None
    mc_std: float | None = None
    mc_out_pct: float | None = None
    mc_samples: int = 0

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
    if lsl is not None and usl is not None and lsl > usl:
        raise ValueError(f"LSL {lsl} is above USL {usl}")
    for c in contributors:
        if c.direction not in (1, -1):
            raise ValueError(f"{c.label}: direction must be +1 or -1, got {c.direction}")
        if c.half_range < 0:
            raise ValueError(f"{c.label}: tolerance band is negative (+{c.tol_plus}/-{c.tol_minus})")
    nominal = sum(c.direction * c.nominal for c in contributors)
    mean = sum(c.direction * (c.nominal + c.mean_shift) for c in contributors)
    wc = sum(c.half_range for c in contributors)
    variances = [c.sigma(sigma_level) ** 2 for c in contributors]
    sigma = math.sqrt(sum(variances))
    rss = sigma_level * sigma
    total_var = sum(variances) or 1.0
    contributions = [100.0 * v / total_var for v in variances]
    result = Result(nominal, mean, mean - wc, mean + wc, rss, mean - rss, mean + rss,
                    contributions, lsl, usl, sigma=sigma, sigma_level=sigma_level)
    if sigma > 0:
        if lsl is not None and usl is not None:
            result.cp = (usl - lsl) / (6 * sigma)
        sides = []
        if usl is not None:
            sides.append((usl - mean) / (3 * sigma))
        if lsl is not None:
            sides.append((mean - lsl) / (3 * sigma))
        if sides:
            result.cpk = min(sides)
            below = _phi((lsl - mean) / sigma) if lsl is not None else 0.0
            above = 1.0 - _phi((usl - mean) / sigma) if usl is not None else 0.0
            result.ppm = 1e6 * (below + above)
    if mc_samples:
        rng = np.random.default_rng(seed)
        total = np.zeros(mc_samples)
        for c in contributors:
            centre = c.nominal + c.mean_shift
            h = c.half_range
            dist = (c.dist or "normal").lower()
            if h <= 0:
                sample = np.full(mc_samples, centre)
            elif dist == "uniform":
                sample = rng.uniform(centre - h, centre + h, mc_samples)
            elif dist == "triangular":
                sample = rng.triangular(centre - h, centre, centre + h, mc_samples)
            else:
                sample = rng.normal(centre, h / sigma_level, mc_samples)
            total += c.direction * sample
        out = np.zeros(mc_samples, dtype=bool)
        if lsl is not None:
            out |= total < lsl
        if usl is not None:
            out |= total > usl
        result.mc_mean = float(total.mean())
        result.mc_std = float(total.std())
        result.mc_out_pct = float(100.0 * out.mean())
        result.mc_samples = mc_samples
    return result
