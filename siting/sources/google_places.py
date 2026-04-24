"""Google Places API (New) — co-tenants, coffee proxy, backup school/worship.

The "(New)" endpoint is POST-based with a required field mask that controls
which SKU tier is billed:
  - Essentials (10k free/mo): id, displayName, location, types, viewport
  - Pro (5k free/mo):         formattedAddress, rating, userRatingCount
  - Enterprise (1k free/mo):  priceLevel, reviews, photos

Each call below declares its mask explicitly so cost is predictable.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

PLACES_NEARBY = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_AUTOCOMPLETE = "https://places.googleapis.com/v1/places:autocomplete"

# Price level enum values returned by Places API (New).
PRICE_LEVEL_NUM = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


@dataclass
class Place:
    place_id: str
    name: str
    address: str
    lat: float
    lon: float
    primary_type: str
    types: list[str]
    rating: float | None
    rating_count: int | None
    price_level: str | None
    distance_ft: float = 0.0


def has_key() -> bool:
    return bool(os.getenv("GOOGLE_MAPS_API_KEY"))


def _call(
    lat: float,
    lon: float,
    included_types: list[str],
    radius_m: float,
    max_results: int,
    field_mask: str,
) -> list[Place]:
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return []
    body = {
        "includedTypes": included_types,
        "maxResultCount": min(max(max_results, 1), 20),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": radius_m,
            }
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": field_mask,
    }
    try:
        r = requests.post(PLACES_NEARBY, json=body, headers=headers, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out = []
    for p in r.json().get("places", []):
        loc = p.get("location") or {}
        try:
            plat = float(loc.get("latitude"))
            plon = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        out.append(
            Place(
                place_id=p.get("id", ""),
                name=(p.get("displayName") or {}).get("text", ""),
                address=p.get("formattedAddress", ""),
                lat=plat,
                lon=plon,
                primary_type=p.get("primaryType", "") or "",
                types=p.get("types", []) or [],
                rating=p.get("rating"),
                rating_count=p.get("userRatingCount"),
                price_level=p.get("priceLevel"),
            )
        )
    return out


# Field masks per call type — tuned for cost ceiling.

_ESSENTIALS = (
    "places.id,places.displayName,places.location,places.types,places.primaryType"
)
_PRO_MIX = (
    _ESSENTIALS
    + ",places.formattedAddress,places.rating,places.userRatingCount"
)
_ENT_PRICE = _PRO_MIX + ",places.priceLevel"


def nearby_cotenants(lat: float, lon: float, radius_ft: float = 500.0) -> list[Place]:
    """Proxy for foot-traffic partners: restaurants, bars, gyms, retail, grocery."""
    radius_m = radius_ft / 3.28084
    types = [
        "restaurant", "cafe", "bar", "bakery",
        "clothing_store", "convenience_store", "grocery_store",
        "pharmacy", "gym", "beauty_salon", "hair_care",
    ]
    return _call(lat, lon, types, radius_m, max_results=20, field_mask=_PRO_MIX)


def nearby_coffee(lat: float, lon: float, radius_ft: float = 1000.0) -> list[Place]:
    """Coffee shops with price level — proxy for 'cup-of-coffee' index."""
    radius_m = radius_ft / 3.28084
    return _call(lat, lon, ["cafe", "coffee_shop"], radius_m, max_results=10, field_mask=_ENT_PRICE)


def nearby_private_schools(lat: float, lon: float, radius_ft: float = 1500.0) -> list[Place]:
    """Backup for pre-Ks and private schools the NYC DOE / OSM feeds miss."""
    radius_m = radius_ft / 3.28084
    types = ["school", "preschool", "primary_school", "secondary_school"]
    return _call(lat, lon, types, radius_m, max_results=20, field_mask=_PRO_MIX)


def nearby_worship(lat: float, lon: float, radius_ft: float = 600.0) -> list[Place]:
    """Backup for houses of worship beyond OSM coverage."""
    radius_m = radius_ft / 3.28084
    types = ["church", "mosque", "synagogue", "hindu_temple", "place_of_worship"]
    return _call(lat, lon, types, radius_m, max_results=15, field_mask=_PRO_MIX)


def autocomplete_address(query: str) -> list[str]:
    """Return up to 8 US address suggestions (strings, one per line).

    Uses Google Places Autocomplete (New), restricted to street addresses
    in the US. Essentials tier — 10k free calls/month. Callers should
    debounce in the UI layer.
    """
    q = (query or "").strip()
    if len(q) < 3:
        return []
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return []
    body = {
        "input": q,
        "includedRegionCodes": ["us"],
        "includedPrimaryTypes": ["street_address", "premise", "subpremise", "route"],
    }
    try:
        r = requests.post(
            PLACES_AUTOCOMPLETE,
            json=body,
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": key},
            timeout=5,
        )
        r.raise_for_status()
    except requests.RequestException:
        return []
    out: list[str] = []
    for s in r.json().get("suggestions", []) or []:
        pred = s.get("placePrediction") or {}
        text = (pred.get("text") or {}).get("text")
        if text and text not in out:
            out.append(text)
    return out[:8]


def summarize_coffee_prices(places: list[Place]) -> dict:
    """Produce a small summary: count, avg price level, nearest branded shop."""
    if not places:
        return {"count": 0, "avg_price": None, "brands": [], "nearest": None}
    levels = [PRICE_LEVEL_NUM[p.price_level] for p in places if p.price_level in PRICE_LEVEL_NUM]
    brands_of_interest = ("starbucks", "blank street", "gregorys", "joe coffee",
                          "blue bottle", "dunkin", "think coffee")
    brands = []
    for p in places:
        n = (p.name or "").lower()
        for b in brands_of_interest:
            if b in n:
                brands.append(p.name)
                break
    places_sorted = sorted(places, key=lambda p: p.distance_ft)
    return {
        "count": len(places),
        "avg_price": round(sum(levels) / len(levels), 2) if levels else None,
        "brands": sorted(set(brands)),
        "nearest": places_sorted[0] if places_sorted else None,
    }
