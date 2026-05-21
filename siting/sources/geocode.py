"""Address -> lat/lng + parsed components.

Lookup order:
  1. Google Geocoding — accurate nationwide, primary when a key is set.
  2. NYC Planning Geosearch — used ONLY for NYC addresses (to get the BBL
     for PLUTO and C of O lookups). Never used as a primary geocoder
     because loose matches lead it astray on out-of-NYC inputs.
  3. US Census Geocoder — free, no-key fallback for when Google isn't set.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

NYC_GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
GOOGLE_GEOCODE = "https://maps.googleapis.com/maps/api/geocode/json"

NYC_BOROUGHS = {
    "manhattan", "bronx", "brooklyn", "queens", "staten island",
    "new york", "new york city",
}
NYC_COUNTIES = {
    "new york county", "kings county", "bronx county",
    "queens county", "richmond county",
}

# Map county -> borough name. Used to backfill `borough` when Google's
# geocoder returns a neighborhood (Astoria, Williamsburg) as locality and
# omits sublocality_level_1 — without this, _looks_like_nyc() falsely
# rejected those addresses as out-of-NYC and BBL/PLUTO/ACRIS never loaded.
_COUNTY_TO_BOROUGH = {
    "new york county": "Manhattan",
    "kings county": "Brooklyn",
    "bronx county": "Bronx",
    "queens county": "Queens",
    "richmond county": "Staten Island",
}


@dataclass
class GeocodeResult:
    address: str           # normalized full address the service returned
    lat: float
    lon: float
    street: str            # street line only, e.g. "345 E 115th St"
    city: str
    state: str
    zip: str
    bbl: str | None        # NYC borough-block-lot (10-digit), only for NYC
    borough: str | None
    source: str            # "nyc" or "census"


def geocode(address: str) -> GeocodeResult | None:
    if os.getenv("GOOGLE_MAPS_API_KEY"):
        result = _google_geocode(address)
        if result:
            if _looks_like_nyc(result):
                # Re-query NYC Geosearch on the normalized address just to
                # pick up the BBL — the lat/lon from Google stays authoritative.
                nyc = _nyc_geosearch(result.address or address)
                if nyc and nyc.bbl:
                    result.bbl = nyc.bbl
                    result.borough = result.borough or nyc.borough
            return result

    # No Google key: legacy path. Census first so we don't misroute
    # out-of-NYC addresses to Geosearch.
    result = _census_geocode(address)
    if result and _looks_like_nyc(result):
        nyc = _nyc_geosearch(result.address or address)
        if nyc and nyc.bbl:
            result.bbl = nyc.bbl
    if result:
        return result
    return _nyc_geosearch(address)


def _looks_like_nyc(result: GeocodeResult) -> bool:
    """Detect NYC addresses across all five boroughs.

    Has to handle three Google-geocoder shapes for outer-borough addresses:
      - city = "Brooklyn" (clean — usually for "Brooklyn, NY" inputs)
      - city = "Astoria"  (neighborhood — common for "30-30 Northern Blvd" inputs)
      - city = "" + sublocality_level_1 = "Queens" (rare but seen)

    The borough field is the most reliable signal once we backfill it from
    county in _google_geocode(). Checking both city and borough catches all
    three shapes without false-positives on out-of-NYC addresses.
    """
    if not result:
        return False
    if (result.city or "").strip().lower() in NYC_BOROUGHS:
        return True
    if (result.borough or "").strip().lower() in NYC_BOROUGHS:
        return True
    return False


def _google_geocode(address: str) -> GeocodeResult | None:
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            GOOGLE_GEOCODE,
            params={"address": address, "key": key, "region": "us"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if data.get("status") != "OK" or not data.get("results"):
        return None
    first = data["results"][0]
    loc = first["geometry"]["location"]
    # Index each component under ALL its types — Google often lists "political"
    # first for sublocality components, which would hide them from a types[0] lookup.
    comps: dict[str, dict] = {}
    for c in first.get("address_components", []):
        for t in c.get("types", []):
            comps.setdefault(t, c)
    def _long(kind: str, default: str = "") -> str:
        return (comps.get(kind) or {}).get("long_name", default)

    street_number = _long("street_number")
    route = _long("route")
    city = _long("locality") or _long("sublocality") or _long("postal_town") or _long("administrative_area_level_3")
    # NYC quirk: Google returns borough as 'sublocality_level_1' or 'sublocality'
    if not city:
        city = _long("sublocality_level_1") or _long("administrative_area_level_2")
    # Normalize NYC boroughs so downstream NYC detection works.
    county = _long("administrative_area_level_2").lower()
    if county in NYC_COUNTIES and not city:
        city = "New York"

    # Resolve borough independently of city. For outer-borough addresses
    # Google often fills locality with a neighborhood (Astoria, Williamsburg,
    # Bay Ridge) and *also* omits sublocality_level_1, leaving borough None.
    # Backfill from county so downstream NYC detection survives.
    borough = _long("sublocality_level_1") or _long("sublocality") or None
    if not borough and county in NYC_COUNTIES:
        borough = _COUNTY_TO_BOROUGH.get(county)

    return GeocodeResult(
        address=first.get("formatted_address", address),
        lat=float(loc["lat"]),
        lon=float(loc["lng"]),
        street=f"{street_number} {route}".strip(),
        city=city,
        state=_long("administrative_area_level_1", "") or "",
        zip=_long("postal_code", ""),
        bbl=None,  # filled in by NYC re-query if applicable
        borough=borough,
        source="google",
    )


def _nyc_geosearch(address: str) -> GeocodeResult | None:
    try:
        r = requests.get(NYC_GEOSEARCH, params={"text": address, "size": 1}, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None
    features = payload.get("features", [])
    if not features:
        return None
    f = features[0]
    props = f["properties"]
    lon, lat = f["geometry"]["coordinates"]
    if props.get("layer") not in {"address", "venue"}:
        return None
    return GeocodeResult(
        address=props.get("label", address),
        lat=lat,
        lon=lon,
        street=props.get("housenumber", "") + " " + props.get("street", ""),
        city=props.get("locality") or props.get("borough") or "",
        state=props.get("region_a") or "NY",
        zip=props.get("postalcode", ""),
        bbl=props.get("addendum", {}).get("pad", {}).get("bbl"),
        borough=props.get("borough"),
        source="nyc",
    )


def _census_geocode(address: str) -> GeocodeResult | None:
    try:
        r = requests.get(
            CENSUS_GEOCODER,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None
    matches = payload.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    coords = m["coordinates"]
    comps = m["addressComponents"]
    return GeocodeResult(
        address=m["matchedAddress"],
        lat=coords["y"],
        lon=coords["x"],
        street=f"{comps.get('fromAddress','')} {comps.get('streetName','')} {comps.get('suffixType','')}".strip(),
        city=comps.get("city", ""),
        state=comps.get("state", ""),
        zip=comps.get("zip", ""),
        bbl=None,
        borough=None,
        source="census",
    )
