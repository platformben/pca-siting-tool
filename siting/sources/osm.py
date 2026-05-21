"""OpenStreetMap Overpass API — free schools + houses of worship.

We query a radius around the candidate address for features the OCM
regulations care about. OSM is incomplete in places (Overpass is
crowd-sourced) so callers must still link out to Google Maps for
manual verification — per regulation workflow.
"""
from __future__ import annotations

import requests
from dataclasses import dataclass

OVERPASS = "https://overpass-api.de/api/interpreter"


@dataclass
class OsmFeature:
    osm_id: int
    kind: str           # "school" | "kindergarten" | "place_of_worship" | "park"
    name: str | None
    lat: float
    lon: float
    street: str | None  # addr:street tag if present
    tags: dict
    distance_ft: float = 0.0


def nearby_schools(lat: float, lon: float, radius_ft: float = 1500.0) -> list[OsmFeature]:
    radius_m = int(radius_ft / 3.28084)
    q = f"""
    [out:json][timeout:25];
    (
      node["amenity"="school"](around:{radius_m},{lat},{lon});
      way["amenity"="school"](around:{radius_m},{lat},{lon});
      relation["amenity"="school"](around:{radius_m},{lat},{lon});
      node["amenity"="kindergarten"](around:{radius_m},{lat},{lon});
      way["amenity"="kindergarten"](around:{radius_m},{lat},{lon});
      relation["amenity"="kindergarten"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    return _run(q)


def nearby_worship(lat: float, lon: float, radius_ft: float = 600.0) -> list[OsmFeature]:
    radius_m = int(radius_ft / 3.28084)
    q = f"""
    [out:json][timeout:25];
    (
      node["amenity"="place_of_worship"](around:{radius_m},{lat},{lon});
      way["amenity"="place_of_worship"](around:{radius_m},{lat},{lon});
      relation["amenity"="place_of_worship"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    return _run(q)


def nearby_parks(lat: float, lon: float, radius_ft: float = 1500.0) -> list[OsmFeature]:
    radius_m = int(radius_ft / 3.28084)
    q = f"""
    [out:json][timeout:25];
    (
      way["leisure"="park"](around:{radius_m},{lat},{lon});
      relation["leisure"="park"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    return _run(q)


def _run(q: str) -> list[OsmFeature]:
    try:
        r = requests.post(OVERPASS, data={"data": q}, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return []
    out: list[OsmFeature] = []
    for el in payload.get("elements", []):
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center", {})
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        kind = tags.get("amenity") or tags.get("leisure") or "unknown"
        out.append(
            OsmFeature(
                osm_id=el["id"],
                kind=kind,
                name=tags.get("name"),
                lat=float(lat),
                lon=float(lon),
                street=tags.get("addr:street"),
                tags=tags,
            )
        )
    return out


def building_exclusive_use_hint(tags: dict) -> tuple[bool, str]:
    """Best-effort judgment on whether a place-of-worship building is
    exclusively used as such. Returns (probably_exclusive, reason).

    Heuristics only — the regulation needs confirmation from site visit
    or C of O. NYC ground-floor church / upstairs apartments are the
    classic case this tries to catch by looking for mixed-use tags.
    """
    if tags.get("building:use") == "mixed":
        return (False, "OSM tagged building:use=mixed")
    if tags.get("residential") == "yes":
        return (False, "residential=yes tag present")
    levels = tags.get("building:levels")
    try:
        if levels and int(levels) >= 3:
            return (False, f"tall building (levels={levels}) — check for apartments above")
    except ValueError:
        pass
    if tags.get("building") in {"apartments", "residential", "house"}:
        return (False, f"building={tags.get('building')}")
    return (True, "no mixed-use signals in OSM tags")
