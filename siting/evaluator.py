"""Orchestration — run an address through every check and bundle findings."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import commercial
from .commercial import CommercialSnapshot
from .geo import haversine_feet
from .rules import ny
from .rules.ny import Finding
from .sources import (
    acris, census_acs, geocode, nyc_opendata, nys_liquor, ocm,
    pca_comparables, subway, zillow_rent,
)
from .sources.acris import Deed
from .sources.geocode import GeocodeResult
from .sources.nyc_opendata import PlutoLot
from .sources.ocm import Dispensary
from .sources.pca_comparables import ComparableSet
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
    competitor_count_within_1mi: int = 0       # active dispensaries inside 1 mi
    pca_comparables: ComparableSet | None = None  # internal PCA overlay (gated)

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
    nearest_dispensaries, competitor_count_1mi = _nearest_competitors(geo.lat, geo.lon)
    # SLA dataset is NY-only; skip the lookup for out-of-state addresses so a
    # zero-count for, say, a New Jersey ZIP doesn't read as a real signal.
    offpremises_zip_count = (
        nys_liquor.lookup_count(geo.zip)
        if (geo.state or "").upper() in {"NY", "NEW YORK"} else None
    )

    # PCA portfolio overlay — only when both the data and the password key
    # are configured. Builds a feature vector from the just-fetched
    # evaluation data and finds the K nearest mature stores.
    pca_set = None
    if pca_comparables.overlay_enabled() and demo is not None:
        query_features = _comparable_features(
            demo=demo,
            zori=zori,
            zip_offpremises=offpremises_zip_count,
            competitor_count_1mi=competitor_count_1mi,
            nearest_competitor_mi=(
                (nearest_dispensaries[0].distance_ft / 5280)
                if nearest_dispensaries else None
            ),
        )
        if query_features:
            pca_set = pca_comparables.find_comparables(query_features)

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
        competitor_count_within_1mi=competitor_count_1mi,
        pca_comparables=pca_set,
    )


def _nearest_competitors(lat: float, lon: float) -> tuple[list[Dispensary], int]:
    """Top 3 active dispensaries by walking distance, plus the within-1mi count.

    The within-1mi count is a strong predictor of revenue per PCA's portfolio
    analysis (pass1), so it rides along on the same OCM call to avoid a
    second round-trip.
    """
    competitors = [
        d for d in ocm.nearby_dispensaries(lat, lon, radius_ft=COMPETITOR_RADIUS_FT)
        if d.status == "active"
    ]
    for d in competitors:
        d.distance_ft = haversine_feet(lat, lon, d.lat, d.lon)
    competitors.sort(key=lambda d: d.distance_ft)
    count_1mi = sum(1 for d in competitors if d.distance_ft <= 5280)
    return competitors[:3], count_1mi


def _comparable_features(
    demo: TractDemographics,
    zori: ZoriObservation | None,
    zip_offpremises: int | None,
    competitor_count_1mi: int,
    nearest_competitor_mi: float | None,
) -> dict[str, float]:
    """Build the feature vector for PCA-comparables similarity scoring.

    Returns only the features that are actually populated — find_comparables()
    handles partial vectors by renormalizing weights.
    """
    f: dict[str, float] = {}
    if demo:
        if demo.total_population is not None:
            f["population"] = float(demo.total_population)
        if demo.mhhi is not None:
            f["median_hh_income"] = float(demo.mhhi)
        if demo.median_age is not None:
            f["median_age"] = float(demo.median_age)
        if demo.pct_bachelors_plus is not None:
            f["bachelors_pct"] = float(demo.pct_bachelors_plus)
        if demo.median_gross_rent is not None:
            f["median_gross_rent"] = float(demo.median_gross_rent)
    f["comp_within_1mi"] = float(competitor_count_1mi)
    if nearest_competitor_mi is not None:
        f["comp_nearest_mi"] = float(nearest_competitor_mi)
    if zip_offpremises is not None:
        f["dispensary_count_in_zip"] = float(zip_offpremises)
    return f
