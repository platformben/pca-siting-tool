"""Commercial factors — co-tenants, coffee proxy, anchors, top-line metrics.

Pulls from Google Places. Returns a dict the UI renders; degrades
gracefully (returns empty dict) when no API key is configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import haversine_feet
from .sources import google_places
from .sources.google_places import Place


@dataclass
class CommercialSnapshot:
    has_places_key: bool
    cotenants: list = field(default_factory=list)
    coffee: list = field(default_factory=list)
    coffee_summary: dict = field(default_factory=dict)
    attractions: list = field(default_factory=list)
    nearest_supermarket: Place | None = None
    nearest_pharmacy: Place | None = None
    # Storefronts in the cotenants pull marked CLOSED_PERMANENTLY by Google.
    # Three or more in a 500 ft radius is the corridor-distress flag we surface
    # in the UI — that's the threshold a retail-real-estate broker would call
    # out unprompted on a walk-through.
    vacancy_count: int = 0


def gather(lat: float, lon: float) -> CommercialSnapshot:
    if not google_places.has_key():
        return CommercialSnapshot(has_places_key=False)

    cotenants_raw = google_places.nearby_cotenants(lat, lon, radius_ft=500)
    for p in cotenants_raw:
        p.distance_ft = haversine_feet(lat, lon, p.lat, p.lon)
    # Vacancy proxy is computed off the unfiltered pull — we want the
    # CLOSED_PERMANENTLY count even though we don't display those spots in
    # the active-cotenants list. Operational status drops them from the
    # rendered list to avoid double-counting "co-tenants" with shuttered ones.
    #
    # getattr() is defensive: if a stale-bytecode deploy ever serves a Place
    # class missing this field, we degrade to "no vacancy data" instead of
    # 500-erroring the whole evaluation.
    def _is_closed(p) -> bool:
        return getattr(p, "business_status", None) == "CLOSED_PERMANENTLY"

    vacancy_count = sum(1 for p in cotenants_raw if _is_closed(p))
    cotenants = [p for p in cotenants_raw if not _is_closed(p)]
    cotenants.sort(key=lambda p: p.distance_ft)

    coffee = google_places.nearby_coffee(lat, lon, radius_ft=1000)
    for p in coffee:
        p.distance_ft = haversine_feet(lat, lon, p.lat, p.lon)
    coffee.sort(key=lambda p: p.distance_ft)

    attractions_raw = google_places.nearby_attractions(lat, lon, radius_ft=1320)
    for p in attractions_raw:
        p.distance_ft = haversine_feet(lat, lon, p.lat, p.lon)
    # Filter to genuinely notable: 100+ Google reviews OR rating >= 4.4
    # with any review count. Sorted by review count (popularity proxy)
    # then distance.
    attractions = [
        p for p in attractions_raw
        if (p.rating_count and p.rating_count >= 100)
        or (p.rating and p.rating >= 4.4 and (p.rating_count or 0) >= 20)
    ]
    attractions.sort(key=lambda p: (-(p.rating_count or 0), p.distance_ft))

    nearest_supermarket = _closest(google_places.nearby_supermarkets(lat, lon), lat, lon)
    nearest_pharmacy = _closest(google_places.nearby_pharmacies(lat, lon), lat, lon)

    return CommercialSnapshot(
        has_places_key=True,
        cotenants=cotenants,
        coffee=coffee,
        coffee_summary=google_places.summarize_coffee_prices(coffee),
        attractions=attractions,
        nearest_supermarket=nearest_supermarket,
        nearest_pharmacy=nearest_pharmacy,
        vacancy_count=vacancy_count,
    )


def _closest(places: list[Place], lat: float, lon: float) -> Place | None:
    """Annotate distance, sort, return the single closest — or None if empty."""
    if not places:
        return None
    for p in places:
        p.distance_ft = haversine_feet(lat, lon, p.lat, p.lon)
    places.sort(key=lambda p: p.distance_ft)
    return places[0]
