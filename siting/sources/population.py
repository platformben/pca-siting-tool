"""Population lookup to pick the OCM dispensary buffer size.

Rule: >=1,000 ft from another dispensary in cities >20,000 pop,
      >=2,000 ft in cities <=20,000 pop.

NYC's five boroughs are all >20,000 (obviously), so we short-circuit
for NYC without hitting Census. For non-NYC addresses we use the Census
ACS 5-year "Total Population" B01003_001E by Place (city / CDP).
"""
from __future__ import annotations

import os
import requests

NYC_BOROUGHS = {
    "manhattan", "bronx", "brooklyn", "queens", "staten island",
    "new york", "new york city",
    # County-form labels — Google sometimes returns these as the "city" when
    # the address-component picker doesn't surface a sublocality.
    "kings county", "new york county", "bronx county",
    "queens county", "richmond county",
}

ACS_PLACE = "https://api.census.gov/data/2022/acs/acs5"


def population_over_20k(city: str, state: str = "NY") -> bool | None:
    """Return True if the place has >20,000 people, False otherwise.

    Returns None if we can't determine it; callers should treat None
    as "assume the stricter 2,000 ft rule" to fail-safe.
    """
    if (city or "").strip().lower() in NYC_BOROUGHS:
        return True
    return _acs_place_population(city, state)


def _acs_place_population(city: str, state: str) -> bool | None:
    if not city:
        return None
    state_fips = _STATE_FIPS.get(state.upper())
    if not state_fips:
        return None
    params = {
        "get": "NAME,B01003_001E",
        "for": "place:*",
        "in": f"state:{state_fips}",
    }
    key = os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key
    try:
        r = requests.get(ACS_PLACE, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return None
    rows = r.json()
    if not rows or len(rows) < 2:
        return None
    header = rows[0]
    name_idx = header.index("NAME")
    pop_idx = header.index("B01003_001E")
    needle = city.strip().lower()
    best = None
    for data in rows[1:]:
        label = data[name_idx].lower()
        if needle in label:
            try:
                n = int(data[pop_idx])
            except (TypeError, ValueError):
                continue
            if best is None or n > best:
                best = n
    if best is None:
        return None
    return best > 20_000


_STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}
