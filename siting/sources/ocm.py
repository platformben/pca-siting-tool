"""OCM ArcGIS feature-service client.

Public, no auth. Endpoints discovered from the OCM Location Review map:
https://experience.arcgis.com/experience/af166ae6b42c411295a90699a92036fe
"""
from __future__ import annotations

import requests
from dataclasses import dataclass

BASE = "https://services8.arcgis.com/CsL4l0ex90otnsI9/arcgis/rest/services"
ACTIVE = f"{BASE}/ActiveLicensesV1/FeatureServer/0/query"
PENDING = f"{BASE}/PendingLicensesV1/FeatureServer/0/query"

# Schools and houses of worship backing the OCM LOCAL Map. NYSED schools
# are hosted on a different ArcGIS org (services6 vs services8).
NYS_SCHOOLS_BASE = "https://services6.arcgis.com/EbVsqZ18sv1kVJ3k/arcgis/rest/services/NYS_Schools/FeatureServer"
NYS_SCHOOLS_LAYERS = {
    "K-12 (all)": 1,
    "Kindergartens (all buildings)": 14,
    "Approved preschool (SWD)": 13,
}
OCM_WORSHIP = f"{BASE}/PlacesOfWorshipESRI/FeatureServer/0/query"

RETAIL_LIKE = "License_Type LIKE '%Retail%'"


@dataclass
class School:
    name: str
    address: str
    city: str
    inst_type: str        # e.g. "NON-PUBLIC SCHOOLS", "PUBLIC SCHOOLS"
    inst_subtype: str     # e.g. "JEWISH", "CATHOLIC", "CHARTER"
    layer_label: str      # e.g. "K-12 (all)", "Kindergartens"
    lat: float
    lon: float
    distance_ft: float = 0.0


@dataclass
class Worship:
    name: str
    feat_type: str        # always "Place of Worship" in current feed
    lat: float
    lon: float
    distance_ft: float = 0.0


@dataclass
class Dispensary:
    license_number: str | None
    dba: str | None
    entity_name: str | None
    address: str
    city: str
    license_type: str
    operational_status: str | None
    status: str  # "active" | "pending"
    lat: float
    lon: float
    distance_ft: float = 0.0  # populated by caller


def nearby_dispensaries(lat: float, lon: float, radius_ft: float = 2500.0) -> list[Dispensary]:
    """Return every active + pending retail license point within radius_ft."""
    radius_m = radius_ft / 3.28084
    out: list[Dispensary] = []
    for endpoint, status in ((ACTIVE, "active"), (PENDING, "pending")):
        out.extend(_query(endpoint, lat, lon, radius_m, status))
    return out


def nearby_schools(lat: float, lon: float, radius_ft: float = 1500.0) -> list[School]:
    """Pull schools + kindergartens + approved-preschool-SWD in one radius.

    These are the layers the OCM LOCAL Map draws from. Output is deduped
    by (name, address) across layers since the K-12 layer overlaps with
    kindergartens where a building has both.
    """
    radius_m = radius_ft / 3.28084
    seen: set[tuple] = set()
    out: list[School] = []
    for label, layer_id in NYS_SCHOOLS_LAYERS.items():
        endpoint = f"{NYS_SCHOOLS_BASE}/{layer_id}/query"
        params = {
            "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "distance": radius_m,
            "units": "esriSRUnit_Meter",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "LEGAL_NAME,PHYSADDRLINE1,PHYSCITY,INST_TYPE_DESC,INSTSUBTYPDESC",
            "outSR": "4326",
            "f": "json",
        }
        try:
            r = requests.get(endpoint, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException:
            continue
        for feat in r.json().get("features", []):
            a = feat["attributes"]
            geom = feat.get("geometry") or {}
            glat, glon = geom.get("y"), geom.get("x")
            if glat is None or glon is None:
                continue
            key = (a.get("LEGAL_NAME", ""), a.get("PHYSADDRLINE1", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                School(
                    name=a.get("LEGAL_NAME", "") or "",
                    address=a.get("PHYSADDRLINE1", "") or "",
                    city=a.get("PHYSCITY", "") or "",
                    inst_type=a.get("INST_TYPE_DESC", "") or "",
                    inst_subtype=a.get("INSTSUBTYPDESC", "") or "",
                    layer_label=label,
                    lat=float(glat),
                    lon=float(glon),
                )
            )
    return out


def nearby_worship(lat: float, lon: float, radius_ft: float = 600.0) -> list[Worship]:
    radius_m = radius_ft / 3.28084
    params = {
        "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NAME,FEATTYPE",
        "outSR": "4326",
        "f": "json",
    }
    try:
        r = requests.get(OCM_WORSHIP, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out: list[Worship] = []
    for feat in r.json().get("features", []):
        a = feat["attributes"]
        geom = feat.get("geometry") or {}
        glat, glon = geom.get("y"), geom.get("x")
        if glat is None or glon is None:
            continue
        out.append(
            Worship(
                name=a.get("NAME", "") or "(unnamed)",
                feat_type=a.get("FEATTYPE", "") or "",
                lat=float(glat),
                lon=float(glon),
            )
        )
    return out


def _query(endpoint: str, lat: float, lon: float, radius_m: float, status: str) -> list[Dispensary]:
    params = {
        "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "where": RETAIL_LIKE,
        "outFields": (
            "License_Number,DBA,Entity_Name,Address_Line_1,City,"
            "License_Type,Operational_Status,Latitude,Longitude"
        ),
        "outSR": "4326",
        "f": "json",
    }
    try:
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out: list[Dispensary] = []
    for feat in r.json().get("features", []):
        a = feat["attributes"]
        geom = feat.get("geometry") or {}
        glat = geom.get("y") or a.get("Latitude")
        glon = geom.get("x") or a.get("Longitude")
        if glat is None or glon is None:
            continue
        out.append(
            Dispensary(
                license_number=a.get("License_Number"),
                dba=a.get("DBA"),
                entity_name=a.get("Entity_Name"),
                address=a.get("Address_Line_1", "") or "",
                city=a.get("City", "") or "",
                license_type=a.get("License_Type", "") or "",
                operational_status=a.get("Operational_Status"),
                status=status,
                lat=float(glat),
                lon=float(glon),
            )
        )
    return out
