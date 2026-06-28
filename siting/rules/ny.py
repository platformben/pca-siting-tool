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

    # "Authoritative" = the source flagged would actually hold up under
    # § 72 enforcement, i.e. the location is registered with NYS Department of
    # Education as a school. OCM's NYS_Schools layer pulls from NYSED directly.
    # NYC DCP Facilities is partly authoritative — the "SCHOOLS (K-12)" facgroup
    # is sourced from NYC DOE (city's DOE, which the state recognizes), but its
    # "DAY CARE AND PRE-KINDERGARTEN" facgroup is daycares licensed by NY OCFS,
    # NOT NYSED — those don't legally count as schools under § 72.
    # OSM + Google Places are crowd/POI data, never authoritative.
    for s in ocm_schools:
        schools.append({
            "source": "OCM / NYSED",
            "authoritative": True,
            "name": s.name,
            "address": f"{s.address}, {s.city}".strip(", "),
            "lat": s.lat, "lon": s.lon,
            "distance_ft": haversine_feet(lat, lon, s.lat, s.lon),
            "same_street": _same_street(s.address),
            "grades": s.layer_label,
            "type": f"{s.inst_type} / {s.inst_subtype}".strip(" /"),
        })
        seen.add(_key(s.lat, s.lon))
    for s in doe:
        if _key(s.lat, s.lon) in seen:
            continue
        is_authoritative = "SCHOOLS (K-12)" in (s.school_type or "").upper()
        schools.append({
            "source": "NYC DCP Facilities",
            "authoritative": is_authoritative,
            "name": s.name,
            "address": s.address,
            "lat": s.lat, "lon": s.lon,
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
            "authoritative": False,
            "name": o.name or "(unnamed)",
            "address": o.street or "",
            "lat": o.lat, "lon": o.lon,
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
            "authoritative": False,
            "name": g.name,
            "address": g.address,
            "lat": g.lat, "lon": g.lon,
            "distance_ft": haversine_feet(lat, lon, g.lat, g.lon),
            "same_street": _same_street(g.address),
            "grades": "",
            "type": g.primary_type,
        })
        seen.add(_key(g.lat, g.lon))
    schools.sort(key=lambda x: x["distance_ft"])

    # Parks adjacent to schools: NYC's "Jointly Operated Playgrounds" program
    # makes many small parks co-administered with NYSED — the playground IS
    # part of the school's grounds for distance-rule purposes. We can't tell
    # from public data which parks are formally co-administered, so we surface
    # park-adjacent-to-school as REVIEW with explicit guidance: any park
    # within ~300 ft of an authoritative school AND within 500 ft of the
    # candidate is a candidate-extended school zone worth verifying with
    # NYSED before concluding compliance.
    parks = osm.nearby_parks(lat, lon, radius_ft=500)
    park_conflicts: list[dict] = []
    auth_schools_found = [s for s in schools if s["authoritative"]]
    for park in parks:
        park_d_to_candidate = haversine_feet(lat, lon, park.lat, park.lon)
        if park_d_to_candidate >= 500:
            continue  # too far to matter for the 500 ft rule
        # Look for any authoritative school within ~300 ft of this park's center.
        # 300 ft is generous: NYC playgrounds are typically 100-200 ft from the
        # adjacent school building.
        adjacent_school = None
        adjacent_distance = None
        for s in auth_schools_found:
            d = haversine_feet(park.lat, park.lon, s["lat"], s["lon"])
            if d < 300 and (adjacent_distance is None or d < adjacent_distance):
                adjacent_school = s
                adjacent_distance = d
        if adjacent_school is None:
            continue
        park_conflicts.append({
            "source": "OSM (park) + " + adjacent_school["source"],
            "authoritative": False,  # park alone is REVIEW; the school it's attached to FAILed elsewhere
            "name": park.name or "(unnamed park)",
            "address": park.street or "",
            "lat": park.lat, "lon": park.lon,
            "distance_ft": park_d_to_candidate,
            "same_street": _same_street(park.street or ""),
            "grades": "park co-administered with school",
            "type": (
                f"Park adjacent to school: {adjacent_school['name']} "
                f"(~{adjacent_distance:.0f} ft from park center)"
            ),
            "adjacent_school": adjacent_school["name"],
        })

    conflicts = [s for s in schools if s["distance_ft"] < 500 and s["same_street"]]
    auth_conflicts = [c for c in conflicts if c["authoritative"]]
    non_auth_conflicts = [c for c in conflicts if not c["authoritative"]]
    warn_same_street_far = [
        s for s in schools
        if 500 <= s["distance_ft"] < 1000 and s["same_street"]
    ]
    warn_close_diff_street = [
        s for s in schools
        if s["distance_ft"] < 500 and not s["same_street"]
    ]
    auth_close_diff_street = [c for c in warn_close_diff_street if c["authoritative"]]
    details = [
        "Hard rule: same street AND <500 ft from school front door (pre-K through high school).",
        "Authoritative sources for the rule: NYS Department of Education + NYC DOE K-12 list.",
        "Daycares (NY OCFS-licensed) are NOT schools under § 72 — they trigger REVIEW, not FAIL.",
        "Parks adjacent to NYSED schools may be co-administered (NYC Jointly Operated Playgrounds) "
        "— the schoolyard counts as school grounds for the distance rule. Verify with NYSED.",
        "Corner-lot schools count as being on both streets — confirm visually.",
        "Sources: OCM/NYSED (authoritative) + NYC DCP Facilities (K-12 authoritative, daycare not) + OSM (parks) + Google Places.",
    ]
    rule_name = "OCM: school proximity (500 ft same-street rule)"

    # FAIL only when a registered school flagged it. Anything else is REVIEW
    # with explicit guidance to check NYSED before concluding compliance —
    # OSM/Google could be daycares, after-school programs, tutoring centers,
    # learning centers, or test prep places, none of which legally count.
    if auth_conflicts:
        return Finding(
            rule_name,
            "fail",
            f"{len(auth_conflicts)} NYS DOE-registered school within 500 ft on the same street — FAIL",
            details,
            auth_conflicts + non_auth_conflicts + park_conflicts,
        )
    # Park-adjacent-to-school conflicts: the school itself is >500 ft or
    # different-street, but its co-administered playground reaches inside the
    # candidate's 500 ft rule. NYC parks adjacent to NYSED schools are often
    # jointly operated — treat as REVIEW with explicit verify-with-NYSED note.
    same_street_park_conflicts = [p for p in park_conflicts if p["same_street"]]
    if same_street_park_conflicts:
        items = ", ".join(
            f"{p['name']} (adj. {p['adjacent_school']})"
            for p in same_street_park_conflicts[:3]
        )
        return Finding(
            rule_name,
            "warn",
            f"{len(same_street_park_conflicts)} park(s) within 500 ft same-street that "
            f"appear adjacent to a NYSED-registered school — likely co-administered as a "
            f"jointly operated playground (NYC pattern). The schoolyard extends the regulated "
            f"zone. Verify with NYSED before concluding compliance: {items}.",
            details,
            same_street_park_conflicts + non_auth_conflicts,
        )
    if non_auth_conflicts:
        names = ", ".join(f"{c['name']} ({c['source']})" for c in non_auth_conflicts[:3])
        return Finding(
            rule_name,
            "warn",
            f"{len(non_auth_conflicts)} potential school(s) within 500 ft same-street "
            f"but NONE registered with NYS DOE — likely daycare, pre-K (OCFS-licensed), "
            f"after-school program, or tutoring center. "
            f"Verify each on NYSED before concluding compliance: {names}.",
            details,
            non_auth_conflicts,
        )
    if auth_close_diff_street:
        closest = auth_close_diff_street[0]
        return Finding(
            rule_name,
            "warn",
            f"NYS DOE-registered school within 500 ft (different street): {closest['name']} "
            f"({closest['distance_ft']:.0f} ft). Verify corner-lot status.",
            details,
            warn_close_diff_street,
        )
    if warn_close_diff_street:
        closest = warn_close_diff_street[0]
        return Finding(
            rule_name,
            "warn",
            f"Potential school within 500 ft (different street): {closest['name']} "
            f"({closest['source']}) at {closest['distance_ft']:.0f} ft. "
            f"Not in NYS DOE — likely daycare or non-school facility, but worth verifying.",
            details,
            warn_close_diff_street,
        )
    if warn_same_street_far:
        return Finding(
            rule_name,
            "info",
            f"Same-street school(s) between 500-1000 ft — compliant but note for walkup.",
            details,
            warn_same_street_far,
        )
    if not schools:
        return Finding(
            rule_name,
            "warn",
            "No schools found in OCM/NYSED, NYC DCP, OSM, or Google Places within 1,500 ft. Cross-check Google Maps to confirm.",
            details,
        )
    closest = schools[0]
    return Finding(
        rule_name,
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

    # Search radius bumped to 600 ft for the FAIL/REVIEW logic AND to feed
    # the 500 ft REVIEW buffer below — anything 200-500 ft warrants a manual
    # cross-check on Google Maps because point-to-point haversine
    # under-estimates edge-to-edge distance for adjacent buildings.
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

    close = [p for p in places if p.distance_ft < 200]    # FAIL/REVIEW zone
    near = [p for p in places if 200 <= p.distance_ft < 500]  # REVIEW buffer
    details = [
        "Hard rule: 200 ft from a building EXCLUSIVELY used as a house of worship.",
        "Soft buffer: anything 200–500 ft is REVIEW — point-to-point distance "
        "under-counts edge-to-edge for adjacent buildings, and OCM may measure differently.",
        "NYC churches with apartments above = NOT exclusive, so OK at the 200 ft rule.",
        "Sources: OCM (authoritative) + OSM (exclusive-use hints) + Google Places.",
    ]
    rule_name = "OCM: house of worship (200 ft hard / 500 ft review)"

    # No worship found at all — warn, manual cross-check needed
    if not places:
        return Finding(
            rule_name,
            "warn",
            "No houses of worship found in OCM, OSM, or Google Places within 600 ft search radius. Rule triggers at 200 ft — cross-check Google Maps.",
            details,
        )

    # Inside 200 ft — exclusive-use heuristic decides FAIL vs REVIEW
    if close:
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

    # 200–500 ft buffer — REVIEW, not PASS
    if near:
        evidence = [_worship_evidence(p) for p in near]
        nearest = near[0]
        return Finding(
            rule_name,
            "warn",
            f"{len(near)} house of worship between 200 and 500 ft — clears the hard "
            f"200 ft rule but worth a Google Maps walk to verify edge-to-edge distance. "
            f"Nearest: {nearest.name} at {nearest.distance_ft:.0f} ft.",
            details,
            evidence,
        )

    # Past 500 ft — clean PASS
    return Finding(
        rule_name,
        "pass",
        f"Nearest house of worship is {places[0].distance_ft:.0f} ft away — well past the 200 ft rule and 500 ft review buffer.",
        details,
        [_worship_evidence(places[0])],
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
    """Pulls C of O metadata from NYC Open Data (legacy BIS + DOB NOW feeds).

    Both feeds are derived from the live BIS system via ETL pipelines that
    can lag and that historically have missed records. The "no C of O found"
    path always surfaces a BIS C of O search deep-link so the user can
    verify directly.
    """
    rule = "DOB: Certificate of Occupancy"
    details = [
        "Retail dispensaries need a C of O that permits retail / stores / commercial / mercantile use.",
        "Pre-1938 buildings often have no C of O on file; that's not a deal-breaker but requires a Letter of No Objection.",
        "These feeds (legacy BIS + DOB NOW) can be incomplete — the live BIS system is the source of truth.",
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
        # Surface the BIS C of O search URL so the user can click through
        # and verify directly. The Open Data feeds miss records — we don't
        # want a false-negative "no C of O" reading.
        bis_search = nyc_opendata.bis_cofo_search_url(bbl)
        details_with_link = list(details)
        if bis_search:
            details_with_link.append(
                f"Verify on BIS directly: [BIS C of O search for BBL {bbl}]({bis_search})"
            )
        return Finding(
            rule,
            "warn",
            f"No C of O issuances on the NYC Open Data feeds (legacy BIS + DOB NOW) "
            f"for BBL {bbl}. Could be pre-1938, a recent filing not yet ETL'd into "
            f"Open Data, or a feed gap — verify on BIS directly.",
            details_with_link,
        )
    latest = records[0]
    sources = sorted({c.source for c in records})
    sources_str = " + ".join(sources)
    ev = [
        {
            "issue_date": c.issue_date,
            "type": c.issue_type,
            "job_number": c.job_number,
            "job_type": c.job_type,
            "source": c.source,
            "link": c.bis_url,
        }
        for c in records[:5]
    ]
    return Finding(
        rule,
        "info",
        f"{len(records)} C of O filing(s) on record ({sources_str}). Latest: "
        f"{latest.issue_date} ({latest.issue_type}, job {latest.job_number}). "
        f"Read PDF on BIS to confirm retail use.",
        details,
        ev,
    )


def check_zoning(lot: nyc_opendata.PlutoLot | None, bbl: str | None = None) -> Finding:
    """PLUTO-based screen for retail suitability. Fast pre-check before C of O.

    Takes a pre-fetched PlutoLot rather than fetching by BBL itself, so the
    evaluator can pull PLUTO once and use it both for this check and for
    the rendered "The lot" section without making the same Socrata call twice.
    """
    rule = "Zoning & land use (PLUTO)"
    details = [
        "PLUTO is NYC's tax-lot database; lists zoning district, land-use code, and building class.",
        "Cannabis retail needs a commercial zone (C1/C2/C4/C5/C6/C8) or a commercial overlay on a residential lot.",
        "This is a first-pass screen — commercial zoning + retail C of O are both required.",
    ]
    if not lot:
        if not bbl:
            return Finding(rule, "warn", "No BBL — PLUTO lookup needs NYC address.", details)
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
