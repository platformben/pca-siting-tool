"""Commercial factors — co-tenants, coffee proxy, and top-line metrics.

Pulls from Google Places. Returns a dict the UI renders; degrades
gracefully (returns empty dict) when no API key is configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import haversine_feet
from .sources import google_places


@dataclass
class CommercialSnapshot:
    has_places_key: bool
    cotenants: list = field(default_factory=list)
    coffee: list = field(default_factory=list)
    coffee_summary: dict = field(default_factory=dict)
    attractions: list = field(default_factory=list)


def gather(lat: float, lon: float) -> CommercialSnapshot:
    if not google_places.has_key():
        return CommercialSnapshot(has_places_key=False)

    cotenants = google_places.nearby_cotenants(lat, lon, radius_ft=500)
    for p in cotenants:
        p.distance_ft = haversine_feet(lat, lon, p.lat, p.lon)
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

    return CommercialSnapshot(
        has_places_key=True,
        cotenants=cotenants,
        coffee=coffee,
        coffee_summary=google_places.summarize_coffee_prices(coffee),
        attractions=attractions,
    )
