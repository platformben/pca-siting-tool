"""Orchestration — run an address through every check and bundle findings."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import commercial
from .commercial import CommercialSnapshot
from .geo import haversine_feet
from .rules import ny
from .rules.ny import Finding
from .sources import (
    acris, census_acs, geocode, nyc_opendata, nys_liquor, ocm, subway, zillow_rent,
)
from .sources.acris import Deed
from .sources.geocode import GeocodeResult
from .sources.nyc_opendata import PlutoLot
from .sources.ocm import Dispensary
from .sources.subway import NearestStation
from .sources.census_acs import TractDemographics
from .sources.zillow_rent import ZoriObservation

# Competitive radius for the "nearest dispensaries" anchor view. The compliance
# gate uses a tighter ~2,500 ft window to test § 72 distance rules; this widens
# to 1.5 mi so the operator gets a real competitive picture even in NYC's
# denser cannabis corridors where multiple competitors clear the legal gate.
COMPETITOR_RADIUS_FT = 7920.0


@dataclass
class Evaluation:
    input_address: str
    geo: GeocodeResult | None
    findings: list[Finding] = field(default_factory=list)
    nearest_stations: list[NearestStation] = field(default_factory=list)
    demographics: TractDemographics | None = None
    zori: ZoriObservation | None = None
    nearest_dispensaries: list[Dispensary] = field(default_factory=list)
    offpremises_zip_count: int | None = None  # NYS SLA off-premises licenses in this ZIP
    pluto: PlutoLot | None = None              # NYC parcel record (NY-only)
    recent_deeds: list[Deed] = field(default_factory=list)  # ACRIS, newest-first (NY-only)
    commercial: CommercialSnapshot | None = None

    @property
    def overall(self) -> str:
        statuses = {f.status for f in self.findings}
        if "fail" in statuses:
            return "FAIL"
        if "warn" in statuses:
            return "REVIEW"
        return "PASS"


def evaluate(address: str) -> Evaluation:
    geo = geocode.geocode(address)
    if not geo:
        return Evaluation(input_address=address, geo=None, findings=[
            Finding(
                rule="Geocoding",
                status="fail",
                summary=f"Could not resolve '{address}'. Check spelling / format.",
            )
        ])

    # Pull PLUTO once and pass it through to both the zoning gate and the
    # rendered "The lot" section. Avoids two Socrata round-trips per eval.
    pluto = nyc_opendata.pluto_for_bbl(geo.bbl) if geo.bbl else None
    # ACRIS deed history — same NY-only gate via BBL availability.
    recent_deeds = acris.recent_deeds_for_bbl(geo.bbl) if geo.bbl else []

    findings: list[Finding] = [
        ny.check_dispensary_distance(geo.lat, geo.lon, geo.city),
        ny.check_schools(geo.lat, geo.lon, geo.street),
        ny.check_worship(geo.lat, geo.lon),
        ny.check_zoning(pluto, bbl=geo.bbl),
        ny.check_cofo(geo.bbl),
    ]
    stations = subway.nearest_stations(geo.lat, geo.lon, n=3)
    demo = census_acs.demographics_for_point(geo.lat, geo.lon)
    zori = zillow_rent.lookup(geo.zip)
    comm = commercial.gather(geo.lat, geo.lon)
    nearest_dispensaries = _nearest_competitors(geo.lat, geo.lon)
    # SLA dataset is NY-only; skip the lookup for out-of-state addresses so a
    # zero-count for, say, a New Jersey ZIP doesn't read as a real signal.
    offpremises_zip_count = (
        nys_liquor.lookup_count(geo.zip)
        if (geo.state or "").upper() in {"NY", "NEW YORK"} else None
    )

    return Evaluation(
        input_address=address,
        geo=geo,
        findings=findings,
        nearest_stations=stations,
        demographics=demo,
        zori=zori,
        nearest_dispensaries=nearest_dispensaries,
        offpremises_zip_count=offpremises_zip_count,
        pluto=pluto,
        recent_deeds=recent_deeds,
        commercial=comm,
    )


def _nearest_competitors(lat: float, lon: float) -> list[Dispensary]:
    """Top 3 active dispensaries within the competitive radius, by walking distance.

    Filters to the active-license endpoint only — pending licenses are future
    competitors but not present-day ones. OCM's record schema includes a free-text
    `operational_status` we surface in the UI but don't filter on, since that
    field is inconsistently populated.
    """
    competitors = [
        d for d in ocm.nearby_dispensaries(lat, lon, radius_ft=COMPETITOR_RADIUS_FT)
        if d.status == "active"
    ]
    for d in competitors:
        d.distance_ft = haversine_feet(lat, lon, d.lat, d.lon)
    competitors.sort(key=lambda d: d.distance_ft)
    return competitors[:3]
