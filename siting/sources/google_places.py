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
    price_range: dict | None = None  # {"start": int, "end": int, "currency": "USD"}
    business_status: str | None = None  # OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY
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
        payload = r.json()
    except (requests.RequestException, ValueError):
        return []
    out = []
    for p in payload.get("places", []):
        loc = p.get("location") or {}
        try:
            plat = float(loc.get("latitude"))
            plon = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        pr = p.get("priceRange") or {}
        price_range = None
        if pr:
            try:
                price_range = {
                    "start": int(pr.get("startPrice", {}).get("units", 0)),
                    "end": int(pr.get("endPrice", {}).get("units", 0)) or None,
                    "currency": pr.get("startPrice", {}).get("currencyCode") or "USD",
                }
            except (TypeError, ValueError):
                price_range = None
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
                price_range=price_range,
                business_status=p.get("businessStatus"),
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
    + ",places.businessStatus"
)
_ENT_PRICE = _PRO_MIX + ",places.priceLevel,places.priceRange"


def _call_split(
    lat: float,
    lon: float,
    types: list[str],
    radius_m: float,
    max_results: int,
    field_mask: str,
) -> list[Place]:
    """Defensive variant of _call() that issues one request per type and merges.

    Places API (New) `searchNearby` silently returns zero results for some
    mixed-type queries — the full worship list
    `[church, synagogue, mosque, hindu_temple, place_of_worship]` zeroes
    out at small radii even though `[church]` alone returns matches at the
    same coordinates and radius. Confirmed against 101 Avenue U Brooklyn,
    where Victory Outreach (294 ft, primaryType=church) was being missed
    by the mixed call but found by a per-type call.

    For compliance-critical lookups (worship + schools) the cost of N extra
    API calls is worth the correctness guarantee. Dedupes by place_id so
    a venue tagged with multiple included types isn't double-returned.
    """
    out: list[Place] = []
    seen: set[str] = set()
    for t in types:
        for p in _call(lat, lon, [t], radius_m, max_results, field_mask):
            if p.place_id in seen:
                continue
            seen.add(p.place_id)
            out.append(p)
    return out


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


SCHOOL_PRIMARY_TYPES = frozenset(
    {"school", "preschool", "primary_school", "secondary_school"}
)


def nearby_private_schools(lat: float, lon: float, radius_ft: float = 1500.0) -> list[Place]:
    """Backup for pre-Ks and private schools the NYC DOE / OSM feeds miss.

    Uses _call_split because Places (New) drops some preschool / specialty
    school records when the full type list is requested in a single call.

    Filters results to places whose PRIMARY type is school-related — Places
    (New) sometimes returns a place that has a school type in its `types[]`
    array but a non-educational primary type (e.g. a wellness shop with
    primary_type='service' that also tags itself as 'school'). Those false
    positives previously triggered FAIL in the proximity gate.
    """
    radius_m = radius_ft / 3.28084
    types = ["school", "preschool", "primary_school", "secondary_school"]
    raw = _call_split(lat, lon, types, radius_m, max_results=20, field_mask=_PRO_MIX)
    return [p for p in raw if (p.primary_type or "").lower() in SCHOOL_PRIMARY_TYPES]


def nearby_worship(lat: float, lon: float, radius_ft: float = 600.0) -> list[Place]:
    """Backup for houses of worship beyond OSM coverage.

    Uses _call_split because Places (New) silently zeroes out the mixed
    worship-type list at small radii — confirmed bug against 101 Avenue U
    Brooklyn, where the mixed call returned 0 records but per-type calls
    returned a primaryType=church 294 ft away (well inside the § 72 200 ft
    rule's ambiguity zone).
    """
    radius_m = radius_ft / 3.28084
    types = ["church", "mosque", "synagogue", "hindu_temple", "place_of_worship"]
    return _call_split(lat, lon, types, radius_m, max_results=15, field_mask=_PRO_MIX)


def nearby_supermarkets(lat: float, lon: float, radius_ft: float = 2640.0) -> list[Place]:
    """Closest full-service supermarkets within walking distance (default ½ mi).

    Uses both `supermarket` and `grocery_store` types — Google's typing is
    inconsistent (Trader Joe's variously appears under either), and we'd
    rather take the union than miss an obvious anchor on a technicality.
    """
    radius_m = radius_ft / 3.28084
    return _call(lat, lon, ["supermarket", "grocery_store"], radius_m,
                 max_results=10, field_mask=_PRO_MIX)


def nearby_pharmacies(lat: float, lon: float, radius_ft: float = 2640.0) -> list[Place]:
    """Closest pharmacies / drugstores within walking distance (default ½ mi).

    Chains (CVS, Walgreens, Duane Reade) anchor a corridor and are an honest
    "is the chain capital still here?" signal. Pulled with both `pharmacy`
    and `drugstore` types since Google classifies the same chain inconsistently.
    """
    radius_m = radius_ft / 3.28084
    return _call(lat, lon, ["pharmacy", "drugstore"], radius_m,
                 max_results=10, field_mask=_PRO_MIX)


def nearby_attractions(lat: float, lon: float, radius_ft: float = 1320.0) -> list[Place]:
    """Major foot-traffic generators within walking distance (1/4 mile default).

    The Places API (New) `includedTypes` is fussy about mixed categories —
    some combinations silently return zero. We split into three category
    calls (culture / commerce / outdoors) and merge results.
    """
    radius_m = radius_ft / 3.28084
    out: list[Place] = []
    seen: set[str] = set()
    for category in (
        ["museum", "art_gallery", "performing_arts_theater", "movie_theater",
         "tourist_attraction", "library"],
        ["stadium", "shopping_mall", "hotel", "university"],
        ["park", "amusement_park", "aquarium", "zoo"],
    ):
        for p in _call(lat, lon, category, radius_m, max_results=20,
                       field_mask=_PRO_MIX):
            if p.place_id in seen:
                continue
            seen.add(p.place_id)
            out.append(p)
    return out


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
        payload = r.json()
    except (requests.RequestException, ValueError):
        return []
    out: list[str] = []
    for s in payload.get("suggestions", []) or []:
        pred = s.get("placePrediction") or {}
        text = (pred.get("text") or {}).get("text")
        if text and text not in out:
            out.append(text)
    return out[:8]


def summarize_coffee_prices(places: list[Place]) -> dict:
    """Summary: count, dollar range, bean rating (1-5), nearest, brands."""
    if not places:
        return {
            "count": 0,
            "avg_price_level": None,
            "dollar_low": None, "dollar_high": None,
            "bean_rating": None, "avg_rating": None,
            "brands": [], "nearest": None,
        }
    levels = [PRICE_LEVEL_NUM[p.price_level] for p in places if p.price_level in PRICE_LEVEL_NUM]

    # Dollar range — average of startPrice / endPrice across shops that
    # report priceRange. Google's priceRange is meal-level not cup-level,
    # so we apply a coffee-specific fallback when only priceLevel is known.
    starts = [p.price_range["start"] for p in places if p.price_range and p.price_range.get("start")]
    ends = [p.price_range["end"] for p in places if p.price_range and p.price_range.get("end")]
    if starts and ends:
        dollar_low = round(sum(starts) / len(starts))
        dollar_high = round(sum(ends) / len(ends))
    else:
        # Fallback mapping (cup-of-coffee dollar bands by Google priceLevel)
        fallback = {1: (2, 4), 2: (4, 7), 3: (7, 10), 4: (10, 15)}
        if levels:
            avg = round(sum(levels) / len(levels))
            dollar_low, dollar_high = fallback.get(avg, (None, None))
        else:
            dollar_low, dollar_high = None, None

    # Bean rating — 1-5 visual, derived from the avg Google rating of the
    # nearest 5 cafes. Heavier weight to closer shops (proxy for "what
    # does coffee taste like in this exact spot").
    near = sorted(places, key=lambda p: p.distance_ft)[:5]
    rated = [p for p in near if p.rating is not None]
    avg_rating = round(sum(p.rating for p in rated) / len(rated), 2) if rated else None
    if avg_rating is None:
        bean_rating = None
    elif avg_rating >= 4.5:
        bean_rating = 5
    elif avg_rating >= 4.2:
        bean_rating = 4
    elif avg_rating >= 3.8:
        bean_rating = 3
    elif avg_rating >= 3.3:
        bean_rating = 2
    else:
        bean_rating = 1

    brands_of_interest = ("starbucks", "blank street", "gregorys", "joe coffee",
                          "blue bottle", "dunkin", "think coffee", "stumptown",
                          "la colombe", "intelligentsia", "birch")
    brands = []
    for p in places:
        n = (p.name or "").lower()
        for b in brands_of_interest:
            if b in n:
                brands.append(p.name)
                break
    return {
        "count": len(places),
        "avg_price_level": round(sum(levels) / len(levels), 2) if levels else None,
        "dollar_low": dollar_low,
        "dollar_high": dollar_high,
        "bean_rating": bean_rating,
        "avg_rating": avg_rating,
        "brands": sorted(set(brands)),
        "nearest": near[0] if near else None,
    }
