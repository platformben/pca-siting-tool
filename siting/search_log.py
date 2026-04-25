"""In-session log of evaluated addresses + xlsx export.

Streamlit Cloud's filesystem is ephemeral, so we keep the log in
`st.session_state` and let the user download an xlsx snapshot any time.
For persistence across sessions, the user re-downloads each time and
saves the xlsx locally — good enough for v1.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

LOG_KEY = "_pca_search_log"


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
        "nearest_subway": nearest_station.stop_name if nearest_station else None,
        "nearest_subway_ft": int(nearest_station.distance_ft) if nearest_station else None,
        "subway_weekday_2023": (
            int(nearest_station.ridership.weekday_2023)
            if nearest_station and nearest_station.ridership and nearest_station.ridership.weekday_2023
            else None
        ),
        "tract_mhhi": demo.mhhi if demo else None,
        "tract_population": demo.total_population if demo else None,
        "coffee_count": cs.get("count"),
        "coffee_dollar_low": cs.get("dollar_low"),
        "coffee_dollar_high": cs.get("dollar_high"),
        "coffee_bean_rating": cs.get("bean_rating"),
        "coffee_avg_google_rating": cs.get("avg_rating"),
        "coffee_brands": ", ".join(cs.get("brands", [])) if cs.get("brands") else None,
        "cotenants_count": len(comm.cotenants) if comm else None,
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


def to_xlsx_bytes() -> bytes:
    df = pd.DataFrame(_log())
    if df.empty:
        df = pd.DataFrame(columns=["timestamp", "address", "overall"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Searches", index=False)
        # Auto-size columns to ~min(width+2, 60)
        ws = writer.sheets["Searches"]
        for i, col in enumerate(df.columns, start=1):
            max_len = max([len(str(col))] + [len(str(v)) for v in df[col].fillna("")])
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max_len + 2, 60)
    buf.seek(0)
    return buf.getvalue()


def to_dataframe() -> pd.DataFrame:
    return pd.DataFrame(_log())
