"""NYC Open Data — DOE school locations and DOB Certificate of Occupancy.

Docs:
  - DOE Location Points (schools, inc. pre-K): https://data.cityofnewyork.us/resource/jfju-ynrr.json
  - DOB CofO Issuance (1938+): https://data.cityofnewyork.us/resource/bs8b-p36w.json

All endpoints are SoQL (Socrata). App token increases rate limits but
isn't required.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

FACILITIES_DB = "https://data.cityofnewyork.us/resource/ji82-xba5.json"
DOB_COFO = "https://data.cityofnewyork.us/resource/bs8b-p36w.json"
MAPPLUTO = "https://data.cityofnewyork.us/resource/64uk-42ks.json"

# Facility groups that satisfy the OCM "pre-K through high school" rule.
SCHOOL_FACGROUPS = ("SCHOOLS (K-12)", "DAY CARE AND PRE-KINDERGARTEN")


@dataclass
class DoeSchool:
    name: str
    address: str
    lat: float
    lon: float
    grades: str
    school_type: str
    distance_ft: float = 0.0


@dataclass
class CertOfOccupancy:
    bbl: str
    bin: str
    job_number: str
    issue_date: str
    issue_type: str   # "Final" | "Temporary" | ...
    job_type: str     # "A1" (major alt), "NB" (new building), etc.
    bis_url: str


@dataclass
class PlutoLot:
    bbl: str
    address: str
    landuse_code: str       # "05" = commercial & office, "04" = mixed residential/commercial, etc.
    landuse_label: str
    bldgclass: str          # "K*" stores, "O*" offices, "RK" store w/ apts above, etc.
    zonedist1: str
    overlay1: str | None    # commercial overlay (e.g. "C1-5") in residential districts
    owner: str
    year_built: int | None
    year_altered: int | None    # yearalter1 — last major alteration on file
    num_floors: float | None
    num_buildings: int | None
    bldg_area: int | None
    lot_area: int | None
    lot_frontage_ft: int | None  # storefront width — material for retail siting
    lot_depth_ft: int | None
    built_far: float | None       # current built floor-area ratio
    max_commercial_far: float | None  # commfar — max commercial FAR allowed
    assessed_total: int | None    # DOF assessed total value (land + improvements)

    @property
    def far_utilization_pct(self) -> float | None:
        """Built FAR as % of max commercial FAR — proxy for development headroom.

        100% = lot is built out to its zoning ceiling (no easy expansion).
        50% = significant unused development rights — lever for negotiation
        or future build-out plans.
        """
        if not self.built_far or not self.max_commercial_far:
            return None
        return (self.built_far / self.max_commercial_far) * 100


def _headers() -> dict:
    tok = os.getenv("NYC_APP_TOKEN")
    return {"X-App-Token": tok} if tok else {}


def schools_near(lat: float, lon: float, radius_ft: float = 1500.0) -> list[DoeSchool]:
    """Return K-12 + pre-K facilities within radius_ft, from NYC DCP Facilities DB."""
    # SoQL `within_circle` wants meters, but the facilities DB stores lat/lon as
    # strings on the `latitude`/`longitude` fields, not a `point` type. Use a
    # bounding box — adequate at these scales and much faster than a geom cast.
    dlat = radius_ft / 364_000.0
    dlon = radius_ft / (288_200.0 * max(0.01, abs(__import__('math').cos(__import__('math').radians(lat)))))
    groups = "'" + "','".join(SCHOOL_FACGROUPS) + "'"
    params = {
        "$where": (
            f"facgroup IN ({groups}) "
            f"AND latitude BETWEEN '{lat - dlat}' AND '{lat + dlat}' "
            f"AND longitude BETWEEN '{lon - dlon}' AND '{lon + dlon}'"
        ),
        "$limit": "500",
    }
    try:
        r = requests.get(FACILITIES_DB, params=params, headers=_headers(), timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out = []
    for row in r.json():
        try:
            slat = float(row.get("latitude"))
            slon = float(row.get("longitude"))
        except (TypeError, ValueError):
            continue
        out.append(
            DoeSchool(
                name=row.get("facname", "") or "",
                address=row.get("address", "") or "",
                lat=slat,
                lon=slon,
                grades="",  # facilities DB doesn't expose grades directly
                school_type=f"{row.get('facgroup','')} / {row.get('factype','')}",
            )
        )
    return out


def cofo_for_bbl(bbl: str) -> list[CertOfOccupancy]:
    """Return every C of O issuance on file for a BBL, newest first.

    Note: NYC Open Data only exposes issuance *metadata* — job #, issue
    date, final/temp flag. The permissible-use narrative lives in the
    actual C of O PDF on BIS, not this feed.
    """
    if not bbl:
        return []
    params = {
        "bbl": bbl,
        "$order": "c_o_issue_date DESC",
        "$limit": "25",
    }
    try:
        r = requests.get(DOB_COFO, params=params, headers=_headers(), timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out = []
    for row in r.json():
        bin_ = row.get("bin_number") or row.get("bin") or ""
        job = row.get("job_number", "")
        bis_url = (
            f"https://a810-bisweb.nyc.gov/bisweb/JobsQueryByNumberServlet?passjobnumber={job}"
            if job
            else ""
        )
        out.append(
            CertOfOccupancy(
                bbl=row.get("bbl", ""),
                bin=bin_,
                job_number=job,
                issue_date=(row.get("c_o_issue_date") or "")[:10],
                issue_type=row.get("issue_type", "") or row.get("filing_status_raw", ""),
                job_type=row.get("job_type", ""),
                bis_url=bis_url,
            )
        )
    return out


_LANDUSE_LABELS = {
    "01": "One & Two Family Buildings",
    "02": "Multi-Family Walk-Up Buildings",
    "03": "Multi-Family Elevator Buildings",
    "04": "Mixed Residential & Commercial Buildings",
    "05": "Commercial & Office Buildings",
    "06": "Industrial & Manufacturing",
    "07": "Transportation & Utility",
    "08": "Public Facilities & Institutions",
    "09": "Open Space & Outdoor Recreation",
    "10": "Parking Facilities",
    "11": "Vacant Land",
}

# Building class prefixes consistent with retail occupancy. DOB 'bldgclass'
# is a 2-char code; full list at NYC DOF. We flag anything starting with
# these letters as retail-capable (K=Store, O=Office, R=mixed-use incl.
# stores, M=Religious-with-commercial, etc.)
RETAIL_BLDG_CLASS_PREFIX = ("K", "RK", "RM")
MIXED_USE_BLDG_CLASS_PREFIX = ("R", "S")


def pluto_for_bbl(bbl: str) -> PlutoLot | None:
    """Fetch NYC MapPLUTO record for a BBL (zoning + land-use screening)."""
    if not bbl:
        return None
    try:
        r = requests.get(MAPPLUTO, params={"bbl": bbl, "$limit": "1"},
                         headers=_headers(), timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return None
    rows = r.json()
    if not rows:
        return None
    row = rows[0]
    landuse = str(row.get("landuse", "")).zfill(2) if row.get("landuse") else ""
    return PlutoLot(
        bbl=str(row.get("bbl", "")).split(".")[0],
        address=row.get("address", ""),
        landuse_code=landuse,
        landuse_label=_LANDUSE_LABELS.get(landuse, "Unknown"),
        bldgclass=row.get("bldgclass", ""),
        zonedist1=row.get("zonedist1", ""),
        overlay1=row.get("overlay1"),
        owner=row.get("ownername", ""),
        year_built=_to_int(row.get("yearbuilt")),
        year_altered=_to_int(row.get("yearalter1")),
        num_floors=_to_float(row.get("numfloors")),
        num_buildings=_to_int(row.get("numbldgs")),
        bldg_area=_to_int(row.get("bldgarea")),
        lot_area=_to_int(row.get("lotarea")),
        lot_frontage_ft=_to_int(row.get("lotfront")),
        lot_depth_ft=_to_int(row.get("lotdepth")),
        built_far=_to_float(row.get("builtfar")),
        max_commercial_far=_to_float(row.get("commfar")),
        assessed_total=_to_int(row.get("assesstot")),
    )


def _to_int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def zoning_supports_retail(pluto: PlutoLot | None) -> tuple[str, str]:
    """Return ('pass'|'warn'|'fail', evidence) for retail suitability."""
    if pluto is None:
        return ("warn", "No PLUTO record — manual zoning check required.")

    bc = (pluto.bldgclass or "").upper()
    z = (pluto.zonedist1 or "").upper()
    overlay = (pluto.overlay1 or "").upper()

    # Clearly retail-capable building class
    retail_bc = any(bc.startswith(p) for p in RETAIL_BLDG_CLASS_PREFIX)
    # Commercial zone (C*) or commercial overlay (C1/C2) inside residential
    has_commercial_zone = z.startswith("C") or z.startswith("M")
    has_overlay = overlay.startswith("C1") or overlay.startswith("C2")

    if retail_bc and (has_commercial_zone or has_overlay):
        return (
            "pass",
            f"Bldg class {bc} + zoning {z}"
            + (f" / overlay {overlay}" if overlay else "")
            + " — retail permissible.",
        )
    if has_commercial_zone or has_overlay:
        return (
            "warn",
            f"Zoning {z}"
            + (f" / overlay {overlay}" if overlay else "")
            + f" allows commercial, but bldg class {bc} is not clearly retail — confirm C of O.",
        )
    if retail_bc:
        return (
            "warn",
            f"Bldg class {bc} looks retail but zoning {z} is residential — check for overlay or nonconforming use.",
        )
    return (
        "fail",
        f"Zoning {z} + bldg class {bc} — residential only, retail not permissible without variance.",
    )
