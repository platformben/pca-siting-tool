"""New York OCM retail siting rules.

Each check returns a Finding: a structured pass / fail / warn record
with the data needed to render a clear report line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..geo import haversine_feet, normalize_street, extract_street_from_address
from ..sources import ocm, osm, nyc_opendata, population, google_places

Status = Literal["pass", "fail", "warn", "info"]


@dataclass
class Finding:
    rule: str
    status: Status
    summary: str
    details: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


def check_dispensary_distance(lat: float, lon: float, city: str) -> Finding:
    over_20k = population.population_over_20k(city or "")
    min_ft = 1000.0 if over_20k else 2000.0
    rule_note = (
        f"City >20,000 pop: 1,000 ft rule"
        if over_20k
        else (
            "City <=20,000 pop: 2,000 ft rule"
            if over_20k is False
            else "Population unknown — applying stricter 2,000 ft rule as fail-safe"
        )
    )
    nearby = ocm.nearby_dispensaries(lat, lon, radius_ft=min_ft + 500)
    for d in nearby:
        d.distance_ft = haversine_feet(lat, lon, d.lat, d.lon)
    nearby.sort(key=lambda d: d.distance_ft)

    conflicts = [d for d in nearby if d.distance_ft < min_ft]
    details = [rule_note]
    if not nearby:
        return Finding(
            rule="OCM: dispensary proximity (1,000 / 2,000 ft rule)",
            status="pass",
            summary=f"No licensed retail within {int(min_ft + 500)} ft.",
            details=details,
        )
    closest = nearby[0]
    details.append(
        f"Closest: {closest.dba or closest.entity_name or '(unnamed)'} "
        f"at {closest.address}, {closest.city} — {closest.distance_ft:.0f} ft "
        f"({closest.status}, {closest.license_type})"
    )
    status: Status = "fail" if conflicts else "pass"
    summary = (
        f"{len(conflicts)} dispensary within {int(min_ft)} ft — FAIL"
        if conflicts
        else f"Nearest dispensary {closest.distance_ft:.0f} ft away (limit {int(min_ft)} ft)"
    )
    evidence = [
        {
            "name": d.dba or d.entity_name,
            "address": f"{d.address}, {d.city}",
            "distance_ft": round(d.distance_ft),
            "license_type": d.license_type,
            "status": d.status,
            "operational": d.operational_status,
        }
        for d in nearby[:10]
    ]
    return Finding("OCM: dispensary proximity (1,000 / 2,000 ft rule)", status, summary, details, evidence)


def check_schools(
    lat: float, lon: float, candidate_street: str
) -> Finding:
    cand_street_norm = normalize_street(extract_street_from_address(candidate_street))

    def _same_street(school_street_raw: str) -> bool:
        s_street = normalize_street(extract_street_from_address(school_street_raw))
        return bool(cand_street_norm and s_street and (
            cand_street_norm == s_street
            or cand_street_norm in s_street
            or s_street in cand_street_norm
        ))

    # Four complementary feeds, in priority order:
    #   1. OCM / NYSED — the layer the regulator uses on the LOCAL Map.
    #      Covers the whole state, private + public + charter + pre-K SWD.
    #   2. NYC DCP Facilities DB — NYC-only, adds day care / pre-K.
    #   3. OSM — crowd-sourced, catches anything missed above.
    #   4. Google Places — private / parochial fallback, especially upstate.
    ocm_schools = ocm.nearby_schools(lat, lon, radius_ft=1500)
    doe = nyc_opendata.schools_near(lat, lon, radius_ft=1500)
    osm_schools = osm.nearby_schools(lat, lon, radius_ft=1500)
    google_schools = google_places.nearby_private_schools(lat, lon, radius_ft=1500)

    schools = []
    seen: set[tuple] = set()

    def _key(la, lo):
        return (round(la, 5), round(lo, 5))

    for s in ocm_schools:
        schools.append({
            "source": "OCM / NYSED",
            "name": s.name,
            "address": f"{s.address}, {s.city}".strip(", "),
            "distance_ft": haversine_feet(lat, lon, s.lat, s.lon),
            "same_street": _same_street(s.address),
            "grades": s.layer_label,
            "type": f"{s.inst_type} / {s.inst_subtype}".strip(" /"),
        })
        seen.add(_key(s.lat, s.lon))
    for s in doe:
        if _key(s.lat, s.lon) in seen:
            continue
        schools.append({
            "source": "NYC DCP Facilities",
            "name": s.name,
            "address": s.address,
            "distance_ft": haversine_feet(lat, lon, s.lat, s.lon),
            "same_street": _same_street(s.address),
            "grades": s.grades,
            "type": s.school_type,
        })
        seen.add(_key(s.lat, s.lon))
    for o in osm_schools:
        if _key(o.lat, o.lon) in seen:
            continue
        schools.append({
            "source": "OSM",
            "name": o.name or "(unnamed)",
            "address": o.street or "",
            "distance_ft": haversine_feet(lat, lon, o.lat, o.lon),
            "same_street": _same_street(o.street or ""),
            "grades": "",
            "type": o.kind,
        })
        seen.add(_key(o.lat, o.lon))
    for g in google_schools:
        if _key(g.lat, g.lon) in seen:
            continue
        schools.append({
            "source": "Google Places",
            "name": g.name,
            "address": g.address,
            "distance_ft": haversine_feet(lat, lon, g.lat, g.lon),
            "same_street": _same_street(g.address),
            "grades": "",
            "type": g.primary_type,
        })
        seen.add(_key(g.lat, g.lon))
    schools.sort(key=lambda x: x["distance_ft"])

    conflicts = [s for s in schools if s["distance_ft"] < 500 and s["same_street"]]
    warn_same_street_far = [
        s for s in schools
        if 500 <= s["distance_ft"] < 1000 and s["same_street"]
    ]
    warn_close_diff_street = [
        s for s in schools
        if s["distance_ft"] < 500 and not s["same_street"]
    ]
    details = [
        "Rule: same street AND <500 ft from school front door (pre-K through high school).",
        "Corner-lot schools count as being on both streets — confirm visually.",
        "Sources: OCM/NYSED (authoritative) + NYC DCP Facilities + OSM + Google Places.",
    ]
    if conflicts:
        return Finding(
            "OCM: school proximity (500 ft same-street rule)",
            "fail",
            f"{len(conflicts)} school within 500 ft on the same street — FAIL",
            details,
            conflicts,
        )
    if warn_close_diff_street:
        closest = warn_close_diff_street[0]
        return Finding(
            "OCM: school proximity (500 ft same-street rule)",
            "warn",
            f"School within 500 ft (different street): {closest['name']} "
            f"({closest['distance_ft']:.0f} ft). Verify corner-lot status.",
            details,
            warn_close_diff_street,
        )
    if warn_same_street_far:
        return Finding(
            "OCM: school proximity (500 ft same-street rule)",
            "info",
            f"Same-street school(s) between 500-1000 ft — compliant but note for walkup.",
            details,
            warn_same_street_far,
        )
    if not schools:
        return Finding(
            "OCM: school proximity (500 ft same-street rule)",
            "warn",
            "No schools found in OCM/NYSED, NYC DCP, OSM, or Google Places within 1,500 ft. Cross-check Google Maps to confirm.",
            details,
        )
    closest = schools[0]
    return Finding(
        "OCM: school proximity (500 ft same-street rule)",
        "pass",
        f"Nearest school {closest['distance_ft']:.0f} ft away — no same-street conflict.",
        details,
        [schools[0]],
    )


def check_worship(lat: float, lon: float) -> Finding:
    # Common shape the downstream logic expects: .name, .street, .tags,
    # .lat, .lon, .distance_ft. Adapters below normalize each source.
    class _W:
        __slots__ = ("name", "street", "tags", "lat", "lon", "distance_ft", "source")

    def _adapt(name, street, tags, la, lo, source):
        w = _W()
        w.name, w.street, w.tags = name or "(unnamed)", street or "", tags or {}
        w.lat, w.lon = float(la), float(lo)
        w.distance_ft = haversine_feet(lat, lon, w.lat, w.lon)
        w.source = source
        return w

    places: list = []
    seen: set[tuple] = set()

    # 1. OCM — authoritative feed the regulator uses.
    for o in ocm.nearby_worship(lat, lon, radius_ft=600):
        key = (round(o.lat, 5), round(o.lon, 5))
        if key in seen:
            continue
        seen.add(key)
        places.append(_adapt(o.name, "", {}, o.lat, o.lon, "OCM"))

    # 2. OSM — exposes mixed-use tags needed for exclusive-use heuristics.
    for o in osm.nearby_worship(lat, lon, radius_ft=600):
        key = (round(o.lat, 5), round(o.lon, 5))
        if key in seen:
            continue
        seen.add(key)
        places.append(_adapt(o.name, o.street, o.tags, o.lat, o.lon, "OSM"))

    # 3. Google Places — broad coverage, no mixed-use info.
    for g in google_places.nearby_worship(lat, lon, radius_ft=600):
        key = (round(g.lat, 5), round(g.lon, 5))
        if key in seen:
            continue
        seen.add(key)
        places.append(_adapt(g.name, g.address, {}, g.lat, g.lon, "Google"))

    places.sort(key=lambda p: p.distance_ft)

    close = [p for p in places if p.distance_ft < 200]
    details = [
        "Rule: 200 ft from a building EXCLUSIVELY used as a house of worship.",
        "NYC churches with apartments above = NOT exclusive, so OK.",
        "Sources: OCM (authoritative) + OSM (exclusive-use hints) + Google Places.",
    ]
    rule_name = "OCM: house of worship (200 ft rule)"
    if not close:
        if not places:
            return Finding(
                rule_name,
                "warn",
                "No houses of worship found in OCM, OSM, or Google Places within 600 ft search radius. Rule triggers at 200 ft — cross-check Google Maps.",
                details,
            )
        return Finding(
            rule_name,
            "pass",
            f"Nearest house of worship is {places[0].distance_ft:.0f} ft away — well past the 200 ft rule.",
            details,
            [_worship_evidence(places[0])],
        )
    blocking = []
    probably_ok = []
    for p in close:
        exclusive, reason = osm.building_exclusive_use_hint(p.tags)
        entry = _worship_evidence(p) | {"likely_exclusive": exclusive, "reason": reason}
        (blocking if exclusive else probably_ok).append(entry)
    if blocking:
        return Finding(
            rule_name,
            "fail",
            f"{len(blocking)} house of worship within 200 ft, appearing exclusively used — FAIL pending verification",
            details,
            blocking + probably_ok,
        )
    return Finding(
        rule_name,
        "warn",
        f"{len(probably_ok)} house of worship within 200 ft, likely mixed-use (e.g. apartments above). Verify on-site.",
        details,
        probably_ok,
    )


def _worship_evidence(p) -> dict:
    return {
        "source": getattr(p, "source", ""),
        "name": p.name or "(unnamed)",
        "address": p.street or "",
        "distance_ft": round(p.distance_ft),
        "denomination": p.tags.get("religion", ""),
        "building": p.tags.get("building", ""),
        "levels": p.tags.get("building:levels", ""),
    }


def check_cofo(bbl: str | None) -> Finding:
    """NYC Open Data only exposes C of O issuance *metadata* — not the
    permissible-use text. This check confirms a C of O exists and links
    out to BIS so you can read the actual PDF.
    """
    rule = "DOB: Certificate of Occupancy"
    details = [
        "Retail dispensaries need a C of O that permits retail / stores / commercial / mercantile use.",
        "NYC Open Data exposes issuance dates only — click through to BIS to read the PDF.",
        "Pre-1938 buildings often have no C of O on file; that's not a deal-breaker but requires a Letter of No Objection.",
    ]
    if not bbl:
        return Finding(
            rule,
            "warn",
            "No BBL resolved — C of O lookup only works for NYC addresses.",
            details,
        )
    records = nyc_opendata.cofo_for_bbl(bbl)
    if not records:
        return Finding(
            rule,
            "warn",
            f"No C of O issuances on file for BBL {bbl}. Likely pre-1938 building or recent filing pending.",
            details,
        )
    latest = records[0]
    ev = [
        {
            "issue_date": c.issue_date,
            "type": c.issue_type,
            "job_number": c.job_number,
            "job_type": c.job_type,
            "bis_link": c.bis_url,
        }
        for c in records[:5]
    ]
    return Finding(
        rule,
        "info",
        f"{len(records)} C of O filing(s) on record. Latest: {latest.issue_date} "
        f"({latest.issue_type}, job {latest.job_number}). Read PDF on BIS to confirm retail use.",
        details,
        ev,
    )


def check_zoning(bbl: str | None) -> Finding:
    """PLUTO-based screen for retail suitability. Fast pre-check before C of O."""
    rule = "Zoning & land use (PLUTO)"
    details = [
        "PLUTO is NYC's tax-lot database; lists zoning district, land-use code, and building class.",
        "Cannabis retail needs a commercial zone (C1/C2/C4/C5/C6/C8) or a commercial overlay on a residential lot.",
        "This is a first-pass screen — commercial zoning + retail C of O are both required.",
    ]
    if not bbl:
        return Finding(rule, "warn", "No BBL — PLUTO lookup needs NYC address.", details)
    lot = nyc_opendata.pluto_for_bbl(bbl)
    if not lot:
        return Finding(rule, "warn", f"No PLUTO record for BBL {bbl}.", details)
    status, evidence = nyc_opendata.zoning_supports_retail(lot)
    ev = [{
        "address": lot.address,
        "landuse": f"{lot.landuse_code} — {lot.landuse_label}",
        "bldgclass": lot.bldgclass,
        "zonedist1": lot.zonedist1,
        "overlay1": lot.overlay1 or "—",
        "owner": lot.owner,
        "year_built": lot.year_built,
        "num_floors": lot.num_floors,
        "bldg_area_sqft": lot.bldg_area,
        "lot_area_sqft": lot.lot_area,
    }]
    return Finding(rule, status, evidence, details, ev)
