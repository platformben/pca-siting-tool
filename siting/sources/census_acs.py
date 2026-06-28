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

# B15003 (Educational Attainment, 25+ population) — bachelor's degree and
# higher are cells 022-025. Summed and divided by B15003_001E (total 25+)
# to get the "% bachelor's or higher" line operators read as an
# income-and-acceptance proxy.
BACHELORS_PLUS_VARS = (
    "B15003_022E",  # Bachelor's degree
    "B15003_023E",  # Master's degree
    "B15003_024E",  # Professional degree
    "B15003_025E",  # Doctorate degree
)


@dataclass
class TractDemographics:
    tract_fips: str  # 11-digit state+county+tract
    mhhi: int | None
    total_population: int | None
    median_gross_rent: int | None  # B25064 — median gross monthly rent (incl. utilities)
    adult_21_plus: int | None      # Cannabis-legal adult population (derived)
    adult_21_to_34: int | None     # 21–34 early-adopter cohort (derived)
    median_age: float | None              # B01002 — tract median age (years)
    pct_bachelors_plus: float | None      # B15003 — bachelor's+ share of 25+ pop
    pct_renter_occupied: float | None     # B25003 — renter share of occupied units
    pct_transit_commute: float | None     # B08301 — public-transit share of workers 16+
    pct_below_poverty: float | None       # B17001 — below-poverty share of pop for whom status determined
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
    # Headline vars:
    #   B19013_001E — median household income
    #   B01003_001E — total population
    #   B25064_001E — median gross rent
    #   B01002_001E — median age
    # Plus age-cohort cells for 21+ derivation, education attainment cells
    # for bachelor's+ ratio, and four paired numerator/denominator vars
    # for the renter / transit-commute / poverty ratios computed below.
    all_vars = (
        "B19013_001E", "B01003_001E", "B25064_001E", "B01002_001E",
        *UNDER_21_VARS, *ADULT_21_TO_34_VARS,
        "B15003_001E", *BACHELORS_PLUS_VARS,
        "B25003_001E", "B25003_003E",  # total occupied / renter occupied
        "B08301_001E", "B08301_010E",  # total commute / public transit
        "B17001_001E", "B17001_002E",  # poverty universe / below poverty
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

    def _float(code: str) -> float | None:
        v = by_name.get(code)
        if not v or v == ACS_NULL:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _ratio_pct(numer_code: str, denom_code: str) -> float | None:
        """Numerator / denominator as a percentage. None if either is missing
        or the denominator is zero (which Census reports for small / suppressed
        tracts and would otherwise raise ZeroDivisionError)."""
        n, d = _int(numer_code), _int(denom_code)
        if n is None or not d:
            return None
        return (n / d) * 100

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

    # Bachelor's+ share of the 25+ population. Sum the four degree levels
    # (bachelor's, master's, professional, doctorate) and divide by total 25+.
    pop_25_plus = _int("B15003_001E")
    bach_vals = [_int(v) for v in BACHELORS_PLUS_VARS]
    if pop_25_plus and any(v is not None for v in bach_vals):
        bach_sum = sum(v for v in bach_vals if v is not None)
        pct_bachelors_plus = (bach_sum / pop_25_plus) * 100
    else:
        pct_bachelors_plus = None

    return TractDemographics(
        tract_fips=tract,
        mhhi=_int("B19013_001E"),
        total_population=total_pop,
        median_gross_rent=_int("B25064_001E"),
        adult_21_plus=adult_21_plus,
        adult_21_to_34=adult_21_to_34,
        median_age=_float("B01002_001E"),
        pct_bachelors_plus=pct_bachelors_plus,
        pct_renter_occupied=_ratio_pct("B25003_003E", "B25003_001E"),
        pct_transit_commute=_ratio_pct("B08301_010E", "B08301_001E"),
        pct_below_poverty=_ratio_pct("B17001_002E", "B17001_001E"),
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
