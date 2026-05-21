"""Census ACS — MHHI, median rent, population, and 21+ adult cohort by tract.

Uses the free ACS 5-year API. Requires an API key for volume but tolerates
missing key for small ad-hoc requests.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

ACS = "https://api.census.gov/data/2022/acs/acs5"
FCC_BLOCK = "https://geo.fcc.gov/api/census/block/find"

# Census uses -666666666 as the sentinel for "estimate suppressed / not available."
# Any pulled value matching this should be treated as None.
ACS_NULL = "-666666666"

# B01001 (Sex by Age) splits population into sex × age brackets. To compute
# "21 and over" — the cannabis-legal cohort and the operator's true addressable
# market — we sum the under-21 brackets across both sexes and subtract from
# total population. Census doesn't expose a single "21+" cell directly.
UNDER_21_VARS = (
    "B01001_003E",  # Male,   Under 5
    "B01001_004E",  # Male,   5 to 9
    "B01001_005E",  # Male,   10 to 14
    "B01001_006E",  # Male,   15 to 17
    "B01001_007E",  # Male,   18 and 19
    "B01001_008E",  # Male,   20
    "B01001_027E",  # Female, Under 5
    "B01001_028E",  # Female, 5 to 9
    "B01001_029E",  # Female, 10 to 14
    "B01001_030E",  # Female, 15 to 17
    "B01001_031E",  # Female, 18 and 19
    "B01001_032E",  # Female, 20
)

# 21–34 early-adopter cohort — highest-purchase-frequency segment for cannabis
# retail. Summed directly from sex × age cells.
ADULT_21_TO_34_VARS = (
    "B01001_009E",  # Male,   21
    "B01001_010E",  # Male,   22 to 24
    "B01001_011E",  # Male,   25 to 29
    "B01001_012E",  # Male,   30 to 34
    "B01001_033E",  # Female, 21
    "B01001_034E",  # Female, 22 to 24
    "B01001_035E",  # Female, 25 to 29
    "B01001_036E",  # Female, 30 to 34
)


@dataclass
class TractDemographics:
    tract_fips: str  # 11-digit state+county+tract
    mhhi: int | None
    total_population: int | None
    median_gross_rent: int | None  # B25064 — median gross monthly rent (incl. utilities)
    adult_21_plus: int | None      # Cannabis-legal adult population (derived)
    adult_21_to_34: int | None     # 21–34 early-adopter cohort (derived)
    land_area_sqmi: float | None

    @property
    def density_per_sqmi(self) -> float | None:
        if not self.total_population or not self.land_area_sqmi:
            return None
        return self.total_population / self.land_area_sqmi

    @property
    def rent_burden_pct(self) -> float | None:
        """Median gross annual rent as a share of median household income.

        HUD defines households spending >30% on housing as "rent burdened" and
        >50% as "severely rent burdened." This is a tract-aggregate proxy —
        the actual share for any given household varies — but it tracks
        affordability tightly and is the line operators read first.
        """
        if not self.mhhi or not self.median_gross_rent:
            return None
        return (self.median_gross_rent * 12) / self.mhhi * 100

    @property
    def pct_21_plus(self) -> float | None:
        """Adults 21+ as share of total tract population."""
        if not self.total_population or self.adult_21_plus is None:
            return None
        return (self.adult_21_plus / self.total_population) * 100

    @property
    def pct_21_to_34(self) -> float | None:
        """21–34 cohort as share of total tract population."""
        if not self.total_population or self.adult_21_to_34 is None:
            return None
        return (self.adult_21_to_34 / self.total_population) * 100


def demographics_for_point(lat: float, lon: float) -> TractDemographics | None:
    tract = _tract_for_point(lat, lon)
    if not tract:
        return None
    state, county, trct = tract[:2], tract[2:5], tract[5:]
    # B19013_001E = MHHI, B01003_001E = total pop, B25064_001E = median gross rent.
    # Add the under-21 and 21-34 sex × age cells so we can derive 21+ market.
    all_vars = (
        "B19013_001E", "B01003_001E", "B25064_001E",
        *UNDER_21_VARS, *ADULT_21_TO_34_VARS,
    )
    params = {
        "get": ",".join(all_vars),
        "for": f"tract:{trct}",
        "in": f"state:{state} county:{county}",
    }
    key = os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key
    try:
        r = requests.get(ACS, params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError):
        # ValueError covers json.JSONDecodeError — Census returns an HTML
        # rate-limit page on quota exhaustion which crashed every eval
        # before this guard.
        return None
    if not rows or len(rows) < 2:
        return None
    # Header row tells us the column order — don't rely on positional destructure
    # because requesting NAME vs not shifts everything by one.
    header = rows[0]
    data = rows[1]
    by_name = dict(zip(header, data))

    def _int(code: str) -> int | None:
        v = by_name.get(code)
        if not v or v == ACS_NULL:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    total_pop = _int("B01003_001E")

    # 21+ derivation: total - sum(under-21 cells across both sexes). Falls
    # back to None when the under-21 cells are entirely suppressed (rare;
    # tracts with very small populations sometimes have ACS suppression
    # cascade through the age detail).
    under_21_vals = [_int(v) for v in UNDER_21_VARS]
    if total_pop is not None and any(v is not None for v in under_21_vals):
        under_21 = sum(v for v in under_21_vals if v is not None)
        adult_21_plus = max(total_pop - under_21, 0)
    else:
        adult_21_plus = None

    # 21-34 cohort: direct sum of the eight sex × age cells, with the same
    # all-suppressed -> None convention.
    cohort_vals = [_int(v) for v in ADULT_21_TO_34_VARS]
    if any(v is not None for v in cohort_vals):
        adult_21_to_34 = sum(v for v in cohort_vals if v is not None)
    else:
        adult_21_to_34 = None

    return TractDemographics(
        tract_fips=tract,
        mhhi=_int("B19013_001E"),
        total_population=total_pop,
        median_gross_rent=_int("B25064_001E"),
        adult_21_plus=adult_21_plus,
        adult_21_to_34=adult_21_to_34,
        land_area_sqmi=None,  # TIGER land area requires a separate lookup; left off v1
    )


def _tract_for_point(lat: float, lon: float) -> str | None:
    try:
        r = requests.get(FCC_BLOCK, params={"latitude": lat, "longitude": lon, "format": "json"}, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None
    block = payload.get("Block", {}).get("FIPS")
    if not block or len(block) < 11:
        return None
    return block[:11]
