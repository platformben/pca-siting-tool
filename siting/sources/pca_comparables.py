"""PCA portfolio comparables — revenue-overlay for new addresses.

For each new address Scout evaluates, find the K most-similar mature
stores in PCA's existing portfolio and surface their actual revenue
as a range. This is the methodology a real estate broker would apply
informally — comparable-set sizing, not regression — and it stays
honest about uncertainty by showing the underlying evidence rather
than a single black-box number.

Data source: a private CSV bundled at siting/data/pca_portfolio.csv,
gitignored. On Streamlit Cloud, the same content is loaded from the
PCA_PORTFOLIO_CSV secret (a TOML multi-line string). The entire
overlay section is gated on PCA_OVERLAY_KEY also being set — without
it, this module returns empty results and the UI hides the section.

Similarity scoring:
- Each feature normalized via z-score across the portfolio distribution
- Weighted Euclidean distance, weights tuned from pass1 analysis
  (competitor density within 1mi is the strongest single predictor)
- Returns top K mature comparables with similarity %
"""
from __future__ import annotations

import csv
import io
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parents[2] / "data" / "private"
DEFAULT_PORTFOLIO_CSV = Path(__file__).parents[1] / "data" / "pca_portfolio.csv"

# Feature weights, sum to 1.0. Tuned from pass1 analysis:
# competitor density and demographic spend power dominate the spread
# between top and bottom portfolio performers.
FEATURE_WEIGHTS = {
    "comp_within_1mi": 0.25,
    "median_hh_income": 0.15,
    "median_gross_rent": 0.10,
    "bachelors_pct": 0.10,
    "median_age": 0.10,
    "comp_nearest_mi": 0.10,
    "dispensary_count_in_zip": 0.10,
    "population": 0.10,
}

# How many comparables to return by default.
TOP_K = 5


@dataclass
class PortfolioStore:
    store: str
    address: str
    lat: float
    lon: float
    zip_code: str
    revenue_wk: float
    basket: float
    margin_pct: float
    transactions: int
    is_mature: bool
    features: dict  # name -> float, for similarity scoring


@dataclass
class Comparable:
    store: PortfolioStore
    similarity_pct: float  # 0-100, higher = closer match
    distance: float        # raw weighted z-distance (useful for debugging)


@dataclass
class ComparableSet:
    matches: list[Comparable] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.matches

    def revenue_range_annualized(self) -> tuple[float, float, float] | None:
        """Return (low, median, high) annualized revenue across matches.

        Annualization is weekly × 52 — a snapshot, not a forecast. The
        C-suite reads this with that caveat in mind.
        """
        if not self.matches:
            return None
        revenues = sorted(m.store.revenue_wk * 52 for m in self.matches)
        return revenues[0], revenues[len(revenues) // 2], revenues[-1]

    def confidence(self) -> str:
        """Confidence label based on similarity scores + comp count.

        - high: 3+ matches with similarity ≥80
        - medium: 3+ matches with similarity ≥60
        - low: fewer matches or weaker similarity
        """
        if not self.matches:
            return "none"
        top = sorted((m.similarity_pct for m in self.matches), reverse=True)
        strong = sum(1 for s in top if s >= 80)
        decent = sum(1 for s in top if s >= 60)
        if strong >= 3:
            return "high"
        if decent >= 3:
            return "medium"
        return "low"


def _load_csv_text() -> str | None:
    """Return the portfolio CSV text from env var (Streamlit Cloud) or
    local file (dev). Env var takes precedence so prod can override dev."""
    raw = os.getenv("PCA_PORTFOLIO_CSV")
    if raw:
        return raw
    if DEFAULT_PORTFOLIO_CSV.exists():
        return DEFAULT_PORTFOLIO_CSV.read_text()
    return None


def _parse_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "nan") else None
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _portfolio() -> list[PortfolioStore]:
    """Load and parse the portfolio CSV once per process."""
    text = _load_csv_text()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    out: list[PortfolioStore] = []
    for row in reader:
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
            rev = float(row["revenue_wk"])
        except (KeyError, TypeError, ValueError):
            continue
        features: dict[str, float] = {}
        for fname in FEATURE_WEIGHTS:
            v = _parse_float(row.get(fname))
            if v is not None:
                features[fname] = v
        out.append(
            PortfolioStore(
                store=row.get("store", ""),
                address=row.get("address", ""),
                lat=lat, lon=lon,
                zip_code=row.get("zip", ""),
                revenue_wk=rev,
                basket=_parse_float(row.get("basket")) or 0.0,
                margin_pct=_parse_float(row.get("margin_pct")) or 0.0,
                transactions=int(_parse_float(row.get("transactions")) or 0),
                is_mature=(row.get("is_mature", "1") == "1"),
                features=features,
            )
        )
    return out


@lru_cache(maxsize=1)
def _feature_stats() -> dict[str, tuple[float, float]]:
    """For each scoring feature, return (mean, stdev) across MATURE stores.

    Stats computed from mature stores only so the z-normalization isn't
    skewed by first-year operating noise — but matching is done against
    the same pool (mature stores) for consistency.
    """
    portfolio = [s for s in _portfolio() if s.is_mature]
    stats: dict[str, tuple[float, float]] = {}
    for fname in FEATURE_WEIGHTS:
        vals = [s.features[fname] for s in portfolio if fname in s.features]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        stdev = math.sqrt(var) if var > 0 else 1.0  # avoid div-by-zero
        stats[fname] = (mean, stdev)
    return stats


def _z(value: float, fname: str, stats: dict[str, tuple[float, float]]) -> float:
    mean, stdev = stats.get(fname, (0.0, 1.0))
    return (value - mean) / stdev if stdev else 0.0


def overlay_enabled() -> bool:
    """The overlay is active iff both the data and the password key are set."""
    return bool(os.getenv("PCA_OVERLAY_KEY")) and bool(_load_csv_text())


def find_comparables(query_features: dict[str, float], k: int = TOP_K) -> ComparableSet:
    """Find the K most-similar mature portfolio stores.

    Distance is weighted Euclidean in z-space across the features in
    FEATURE_WEIGHTS. Features missing from query OR from a portfolio
    store are excluded from that pair's distance (with the weights
    renormalized) — a partial match still scores rather than getting
    silently zeroed.
    """
    portfolio = [s for s in _portfolio() if s.is_mature]
    stats = _feature_stats()
    if not portfolio or not stats:
        return ComparableSet()

    distances: list[tuple[PortfolioStore, float]] = []
    for store in portfolio:
        used_weight = 0.0
        sq_sum = 0.0
        for fname, w in FEATURE_WEIGHTS.items():
            if fname not in query_features or fname not in store.features:
                continue
            zq = _z(query_features[fname], fname, stats)
            zs = _z(store.features[fname], fname, stats)
            sq_sum += w * (zq - zs) ** 2
            used_weight += w
        if used_weight == 0:
            continue
        # Normalize by used weight so partial-feature stores aren't
        # advantaged by having fewer dimensions to disagree on.
        d = math.sqrt(sq_sum / used_weight)
        distances.append((store, d))

    if not distances:
        return ComparableSet()

    # Map raw z-distance to a 0-100 similarity %. We anchor at
    # "distance 0 = 100% similar" and "distance 2 = 50% similar"
    # (2 std-devs apart is a meaningful but not extreme gap), with
    # asymptotic decay beyond that.
    def _sim(d: float) -> float:
        return max(0.0, 100.0 / (1.0 + d ** 2))

    distances.sort(key=lambda x: x[1])
    top = distances[:k]
    return ComparableSet(
        matches=[Comparable(store=s, distance=d, similarity_pct=_sim(d)) for s, d in top]
    )
