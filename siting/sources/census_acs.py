"""Census ACS — median household income + population density by tract.

Uses the free ACS 5-year API. Requires an API key for volume but tolerates
missing key for small ad-hoc requests.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

ACS = "https://api.census.gov/data/2022/acs/acs5"
FCC_BLOCK = "https://geo.fcc.gov/api/census/block/find"


@dataclass
class TractDemographics:
    tract_fips: str  # 11-digit state+county+tract
    mhhi: int | None
    total_population: int | None
    land_area_sqmi: float | None

    @property
    def density_per_sqmi(self) -> float | None:
        if not self.total_population or not self.land_area_sqmi:
            return None
        return self.total_population / self.land_area_sqmi


def demographics_for_point(lat: float, lon: float) -> TractDemographics | None:
    tract = _tract_for_point(lat, lon)
    if not tract:
        return None
    state, county, trct = tract[:2], tract[2:5], tract[5:]
    params = {
        "get": "B19013_001E,B01003_001E",  # MHHI, total pop
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
    mhhi_raw = by_name.get("B19013_001E")
    pop_raw = by_name.get("B01003_001E")
    return TractDemographics(
        tract_fips=tract,
        mhhi=int(mhhi_raw) if mhhi_raw and mhhi_raw != "-666666666" else None,
        total_population=int(pop_raw) if pop_raw else None,
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
