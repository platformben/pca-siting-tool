"""Orchestration — run an address through every check and bundle findings."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import commercial
from .commercial import CommercialSnapshot
from .rules import ny
from .rules.ny import Finding
from .sources import census_acs, geocode, subway
from .sources.geocode import GeocodeResult
from .sources.subway import NearestStation
from .sources.census_acs import TractDemographics


@dataclass
class Evaluation:
    input_address: str
    geo: GeocodeResult | None
    findings: list[Finding] = field(default_factory=list)
    nearest_stations: list[NearestStation] = field(default_factory=list)
    demographics: TractDemographics | None = None
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

    findings: list[Finding] = [
        ny.check_dispensary_distance(geo.lat, geo.lon, geo.city),
        ny.check_schools(geo.lat, geo.lon, geo.street),
        ny.check_worship(geo.lat, geo.lon),
        ny.check_zoning(geo.bbl),
        ny.check_cofo(geo.bbl),
    ]
    stations = subway.nearest_stations(geo.lat, geo.lon, n=3)
    demo = census_acs.demographics_for_point(geo.lat, geo.lon)
    comm = commercial.gather(geo.lat, geo.lon)

    return Evaluation(
        input_address=address,
        geo=geo,
        findings=findings,
        nearest_stations=stations,
        demographics=demo,
        commercial=comm,
    )
