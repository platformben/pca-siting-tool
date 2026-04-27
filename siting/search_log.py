"""In-session log of evaluated addresses + CSV export.

Streamlit Cloud's filesystem is ephemeral, so we keep the log in
``st.session_state`` and let the user download a CSV snapshot any
time. CSV opens straight into Excel; we don't pull pandas/openpyxl
just for export — Render's free plan doesn't have the headroom.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

import streamlit as st

LOG_KEY = "_pca_search_log"

COLUMNS: list[str] = [
    "timestamp", "address", "lat", "lon", "bbl", "overall",
    "dispensary", "schools", "worship", "zoning", "cofo",
    "nearest_dispensary", "nearest_dispensary_ft",
    "nearest_school", "nearest_school_ft", "nearest_school_same_street",
    "nearest_worship", "nearest_worship_ft",
    "lot_zoning", "lot_overlay", "lot_bldgclass", "lot_owner",
    "lot_area_sqft", "lot_frontage_ft", "lot_depth_ft",
    "bldg_area_sqft", "year_built", "year_altered", "num_floors",
    "built_far", "max_commercial_far", "assessed_total",
    "nearest_subway", "nearest_subway_ft", "subway_weekday_2023",
    "tract_mhhi", "tract_population",
    "tract_adult_21_plus", "tract_pct_21_plus", "tract_adult_21_to_34",
    "tract_median_rent", "tract_rent_burden_pct",
    "zip_zori_asking_rent", "zip_zori_month",
    "zip_offpremises_count",
    "nearest_competitor", "nearest_competitor_mi", "nearest_competitor_license_type",
    "nearest_supermarket", "nearest_supermarket_ft",
    "nearest_pharmacy", "nearest_pharmacy_ft",
    "coffee_count", "coffee_dollar_low", "coffee_dollar_high",
    "coffee_bean_rating", "coffee_avg_google_rating", "coffee_brands",
    "cotenants_count", "vacancy_count", "attractions_count", "top_attraction",
]


def _log() -> list[dict[str, Any]]:
    if LOG_KEY not in st.session_state:
        st.session_state[LOG_KEY] = []
    return st.session_state[LOG_KEY]


def record(evaluation) -> None:
    """Append one evaluation to the session log."""
    geo = evaluation.geo
    findings = {f.rule.split(" (")[0].split(": ", 1)[-1]: f for f in evaluation.findings}

    def status(rule_key: str) -> str:
        f = findings.get(rule_key)
        return f.status.upper() if f else "—"

    nearest_disp_ev = (findings.get("dispensary proximity").evidence or [{}])[0] \
        if findings.get("dispensary proximity") else {}
    nearest_school_ev = (findings.get("school proximity").evidence or [{}])[0] \
        if findings.get("school proximity") else {}
    nearest_worship_ev = (findings.get("house of worship").evidence or [{}])[0] \
        if findings.get("house of worship") else {}

    nearest_station = evaluation.nearest_stations[0] if evaluation.nearest_stations else None
    demo = evaluation.demographics
    comm = evaluation.commercial
    lot = evaluation.pluto

    cs = (comm.coffee_summary if comm else {}) or {}

    row = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "address": geo.address if geo else evaluation.input_address,
        "lat": geo.lat if geo else None,
        "lon": geo.lon if geo else None,
        "bbl": geo.bbl if geo else None,
        "overall": evaluation.overall,
        "dispensary": status("dispensary proximity"),
        "schools": status("school proximity"),
        "worship": status("house of worship"),
        "zoning": status("Zoning & land use"),
        "cofo": status("Certificate of Occupancy"),
        "nearest_dispensary": nearest_disp_ev.get("name"),
        "nearest_dispensary_ft": nearest_disp_ev.get("distance_ft"),
        "nearest_school": nearest_school_ev.get("name"),
        "nearest_school_ft": nearest_school_ev.get("distance_ft"),
        "nearest_school_same_street": nearest_school_ev.get("same_street"),
        "nearest_worship": nearest_worship_ev.get("name"),
        "nearest_worship_ft": nearest_worship_ev.get("distance_ft"),
        "lot_zoning": lot.zonedist1 if lot else None,
        "lot_overlay": (lot.overlay1 if lot else None) or None,
        "lot_bldgclass": lot.bldgclass if lot else None,
        "lot_owner": lot.owner if lot else None,
        "lot_area_sqft": lot.lot_area if lot else None,
        "lot_frontage_ft": lot.lot_frontage_ft if lot else None,
        "lot_depth_ft": lot.lot_depth_ft if lot else None,
        "bldg_area_sqft": lot.bldg_area if lot else None,
        "year_built": lot.year_built if lot else None,
        "year_altered": lot.year_altered if lot else None,
        "num_floors": lot.num_floors if lot else None,
        "built_far": lot.built_far if lot else None,
        "max_commercial_far": lot.max_commercial_far if lot else None,
        "assessed_total": lot.assessed_total if lot else None,
        "nearest_subway": nearest_station.stop_name if nearest_station else None,
        "nearest_subway_ft": int(nearest_station.distance_ft) if nearest_station else None,
        "subway_weekday_2023": (
            int(nearest_station.ridership.weekday_2023)
            if nearest_station and nearest_station.ridership and nearest_station.ridership.weekday_2023
            else None
        ),
        "tract_mhhi": demo.mhhi if demo else None,
        "tract_population": demo.total_population if demo else None,
        "tract_adult_21_plus": demo.adult_21_plus if demo else None,
        "tract_pct_21_plus": (
            round(demo.pct_21_plus, 1)
            if demo and demo.pct_21_plus is not None else None
        ),
        "tract_adult_21_to_34": demo.adult_21_to_34 if demo else None,
        "tract_median_rent": demo.median_gross_rent if demo else None,
        "tract_rent_burden_pct": (
            round(demo.rent_burden_pct, 1)
            if demo and demo.rent_burden_pct is not None else None
        ),
        "zip_zori_asking_rent": (
            round(evaluation.zori.asking_rent) if evaluation.zori else None
        ),
        "zip_zori_month": evaluation.zori.month if evaluation.zori else None,
        "zip_offpremises_count": evaluation.offpremises_zip_count,
        "nearest_competitor": (
            (evaluation.nearest_dispensaries[0].dba
             or evaluation.nearest_dispensaries[0].entity_name)
            if evaluation.nearest_dispensaries else None
        ),
        "nearest_competitor_mi": (
            round(evaluation.nearest_dispensaries[0].distance_ft / 5280, 2)
            if evaluation.nearest_dispensaries else None
        ),
        "nearest_competitor_license_type": (
            evaluation.nearest_dispensaries[0].license_type
            if evaluation.nearest_dispensaries else None
        ),
        "nearest_supermarket": (
            comm.nearest_supermarket.name if comm and comm.nearest_supermarket else None
        ),
        "nearest_supermarket_ft": (
            round(comm.nearest_supermarket.distance_ft)
            if comm and comm.nearest_supermarket else None
        ),
        "nearest_pharmacy": (
            comm.nearest_pharmacy.name if comm and comm.nearest_pharmacy else None
        ),
        "nearest_pharmacy_ft": (
            round(comm.nearest_pharmacy.distance_ft)
            if comm and comm.nearest_pharmacy else None
        ),
        "coffee_count": cs.get("count"),
        "coffee_dollar_low": cs.get("dollar_low"),
        "coffee_dollar_high": cs.get("dollar_high"),
        "coffee_bean_rating": cs.get("bean_rating"),
        "coffee_avg_google_rating": cs.get("avg_rating"),
        "coffee_brands": ", ".join(cs.get("brands", [])) if cs.get("brands") else None,
        "cotenants_count": len(comm.cotenants) if comm else None,
        "vacancy_count": comm.vacancy_count if comm else None,
        "attractions_count": len(comm.attractions) if comm else None,
        "top_attraction": (comm.attractions[0].name if comm and comm.attractions else None),
    }
    log = _log()
    # Avoid duplicate consecutive identical address evaluations within a minute.
    if log and log[-1]["address"] == row["address"] and log[-1]["timestamp"][:16] == row["timestamp"][:16]:
        log[-1] = row
    else:
        log.append(row)


def count() -> int:
    return len(_log())


def clear() -> None:
    st.session_state[LOG_KEY] = []


def to_csv_bytes() -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in _log():
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS})
    return buf.getvalue().encode("utf-8")


def to_rows() -> list[dict[str, Any]]:
    """Return the log as a list of dicts — Streamlit's st.dataframe accepts this directly."""
    return list(_log())
