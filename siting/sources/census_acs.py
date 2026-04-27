"""Census ACS — median household income, median gross rent, population by tract.

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


@dataclass
class TractDemographics:
    tract_fips: str  # 11-digit state+county+tract
    mhhi: int | None
    total_population: int | None
    median_gross_rent: int | None  # B25064 — median gross monthly rent (incl. utilities)
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


def demographics_for_point(lat: float, lon: float) -> TractDemographics | None:
    tract = _tract_for_point(lat, lon)
    if not tract:
        return None
    state, county, trct = tract[:2], tract[2:5], tract[5:]
    params = {
        # B19013_001E = MHHI, B01003_001E = total pop, B25064_001E = median gross rent
        "get": "B19013_001E,B01003_001E,B25064_001E",
        "for": f"tract:{trct}",
        "in": f"state:{state} county:{county}",
    }
    key = os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key
    try:
        r = requests.get(ACS, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return None
    rows = r.json()
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

    return TractDemographics(
        tract_fips=tract,
        mhhi=_int("B19013_001E"),
        total_population=_int("B01003_001E"),
        median_gross_rent=_int("B25064_001E"),
        land_area_sqmi=None,  # TIGER land area requires a separate lookup; left off v1
    )


def _tract_for_point(lat: float, lon: float) -> str | None:
    try:
        r = requests.get(FCC_BLOCK, params={"latitude": lat, "longitude": lon, "format": "json"}, timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return None
    block = r.json().get("Block", {}).get("FIPS")
    if not block or len(block) < 11:
        return None
    return block[:11]
