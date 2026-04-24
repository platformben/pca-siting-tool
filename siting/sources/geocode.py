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
    if not result:
        return False
    return (result.city or "").strip().lower() in NYC_BOROUGHS


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
    except requests.RequestException:
        return None
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        return None
    first = data["results"][0]
    loc = first["geometry"]["location"]
    comps = {c["types"][0]: c for c in first.get("address_components", []) if c.get("types")}
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

    return GeocodeResult(
        address=first.get("formatted_address", address),
        lat=float(loc["lat"]),
        lon=float(loc["lng"]),
        street=f"{street_number} {route}".strip(),
        city=city,
        state=_long("administrative_area_level_1", "") or "",
        zip=_long("postal_code", ""),
        bbl=None,  # filled in by NYC re-query if applicable
        borough=_long("sublocality_level_1") or _long("sublocality") or None,
        source="google",
    )


def _nyc_geosearch(address: str) -> GeocodeResult | None:
    try:
        r = requests.get(NYC_GEOSEARCH, params={"text": address, "size": 1}, timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return None
    features = r.json().get("features", [])
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
    except requests.RequestException:
        return None
    matches = r.json().get("result", {}).get("addressMatches", [])
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
