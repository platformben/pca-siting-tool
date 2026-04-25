"""PCA Siting Tool — Streamlit UI.

Run: streamlit run app.py
"""
from __future__ import annotations

import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_searchbox import st_searchbox

from siting import search_log
from siting.evaluator import evaluate
from siting.sources import google_places
from siting.sources.google_places import PRICE_LEVEL_NUM

load_dotenv()

STATUS_ICON = {
    "pass": ":material/check_circle:",
    "fail": ":material/cancel:",
    "warn": ":material/warning:",
    "info": ":material/info:",
}
STATUS_COLOR = {"pass": "green", "fail": "red", "warn": "orange", "info": "blue"}


st.set_page_config(
    page_title="PCA Siting Tool",
    page_icon="🌿",
    layout="wide",
)


def _autocomplete(query: str) -> list[tuple[str, str]]:
    """Return [(label, value)] suggestions.

    Uses Google Places Autocomplete (US-wide) when a key is configured;
    falls back to NYC Planning Geosearch (NYC only, free, no key) when
    there's no key. Google covers the whole US including the five boroughs.
    """
    q = (query or "").strip()
    if len(q) < 3:
        return []

    if google_places.has_key():
        suggestions = google_places.autocomplete_address(q)
        return [(s, s) for s in suggestions]

    # No Google key — fall back to NYC-only Geosearch.
    try:
        r = requests.get(
            "https://geosearch.planninglabs.nyc/v2/autocomplete",
            params={"text": q, "size": 8},
            timeout=5,
        )
        r.raise_for_status()
    except requests.RequestException:
        return []
    out = []
    seen = set()
    for f in r.json().get("features", []):
        label = f["properties"].get("label")
        if not label or label in seen:
            continue
        seen.add(label)
        out.append((label, label))
    return out


st.title("PCA Siting Tool")
st.caption("NY OCM compliance + commercial snapshot for a candidate dispensary address. **OCM** = NY State Office of Cannabis Management.")

# Sidebar: search log + xlsx export
with st.sidebar:
    st.header("Search log")
    n = search_log.count()
    st.caption(f"{n} address{'es' if n != 1 else ''} evaluated this session.")
    if n:
        st.download_button(
            label="Download log as .xlsx",
            data=search_log.to_xlsx_bytes(),
            file_name="pca-siting-log.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        with st.expander("Preview last 10"):
            st.dataframe(
                search_log.to_dataframe()[["timestamp", "address", "overall"]].tail(10),
                use_container_width=True,
                hide_index=True,
            )
        if st.button("Clear log", use_container_width=True):
            search_log.clear()
            st.rerun()
    else:
        st.caption("Run an evaluation to start logging.")
    st.caption("⚠️ Log lives only in this browser session — download before closing.")

_autocomplete_source = "Google Places (US-wide)" if google_places.has_key() else "NYC only (no API key)"
selected = st_searchbox(
    _autocomplete,
    key="address_search",
    placeholder="Start typing an address…",
    default=None,
    clear_on_submit=False,
    rerun_on_update=True,
)

col_submit, col_info = st.columns([1, 3])
with col_submit:
    run_now = st.button("Evaluate", type="primary", disabled=not selected)
with col_info:
    st.caption(f"Address suggestions: {_autocomplete_source}")

if run_now and selected:
    with st.spinner("Geocoding + running OCM checks…"):
        result = evaluate(selected.strip())
        search_log.record(result)

    if not result.geo:
        st.error(result.findings[0].summary if result.findings else "Geocoding failed.")
        st.stop()

    geo = result.geo
    overall_color = {"PASS": "green", "REVIEW": "orange", "FAIL": "red"}[result.overall]
    st.subheader(f"Overall: :{overall_color}[{result.overall}]")
    st.write(f"**Resolved:** {geo.address} · lat {geo.lat:.5f}, lon {geo.lon:.5f}"
             + (f" · BBL {geo.bbl}" if geo.bbl else ""))
    st.link_button(
        "Open in Google Maps (cross-check schools/churches)",
        f"https://www.google.com/maps/search/?api=1&query={geo.lat},{geo.lon}",
    )

    st.divider()
    st.subheader("OCM compliance")
    st.caption(
        "Gates come from NY Cannabis Law § 72: 1,000 / 2,000 ft from other dispensaries, "
        "500 ft + same street from pre-K – HS schools, 200 ft from buildings exclusively used as houses of worship."
    )
    for f in result.findings:
        icon = STATUS_ICON.get(f.status, "")
        color = STATUS_COLOR.get(f.status, "gray")
        with st.container(border=True):
            st.markdown(f"{icon} **{f.rule}** — :{color}[{f.summary}]")
            for d in f.details:
                st.caption(d)
            if f.evidence:
                with st.expander(f"Evidence ({len(f.evidence)})"):
                    st.dataframe(f.evidence, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Commercial snapshot")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Nearest subway stations (2023 ridership)**")
        if not result.nearest_stations:
            st.caption("No stations loaded — check internet connection.")
        for s in result.nearest_stations:
            wd = f"{int(s.ridership.weekday_2023):,}" if s.ridership and s.ridership.weekday_2023 else "—"
            rk = f"#{s.ridership.rank_2023}" if s.ridership and s.ridership.rank_2023 else ""
            st.write(
                f"• **{s.stop_name}** ({s.routes}) · "
                f"{s.distance_ft:,.0f} ft · avg weekday {wd} {rk}"
            )

    with col_b:
        st.markdown("**Demographics (Census tract)**")
        d = result.demographics
        if not d:
            st.caption("Demographics unavailable.")
        else:
            mhhi = f"${d.mhhi:,}" if d.mhhi else "—"
            pop = f"{d.total_population:,}" if d.total_population else "—"
            st.write(f"• Tract FIPS: {d.tract_fips}")
            st.write(f"• Median HHI: {mhhi}")
            st.write(f"• Tract population: {pop}")

    st.divider()
    comm = result.commercial
    if comm and comm.has_places_key:
        st.subheader("Co-tenants, coffee & attractions (Google Places)")

        col_x, col_y = st.columns(2)
        with col_x:
            st.markdown("**Coffee index** (nearest 10 within 1,000 ft)")
            cs = comm.coffee_summary or {}
            if not comm.coffee:
                st.caption("No coffee shops within 1,000 ft.")
            else:
                # Dollar range
                lo, hi = cs.get("dollar_low"), cs.get("dollar_high")
                if lo and hi:
                    range_str = f"~${lo}–${hi} per cup (typical)"
                elif lo:
                    range_str = f"~${lo}+ per cup"
                else:
                    range_str = "price not reported"
                # Bean rating
                bean = cs.get("bean_rating")
                avg_rating = cs.get("avg_rating")
                if bean:
                    bean_viz = "🫘" * bean + "·" * (5 - bean)
                    bean_str = f"{bean_viz}  ({bean}/5 — avg Google rating {avg_rating})"
                else:
                    bean_str = "—"

                st.write(f"• **{cs['count']}** coffee shops in radius")
                st.write(f"• **{range_str}**")
                st.write(f"• Quality: {bean_str}")
                if cs.get("brands"):
                    st.write("• Brand presence: " + ", ".join(cs["brands"]))
                if cs.get("nearest"):
                    n = cs["nearest"]
                    pr = n.price_range
                    if pr and pr.get("start") and pr.get("end"):
                        npr = f"${pr['start']}–${pr['end']}"
                    else:
                        npr = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}.get(
                            PRICE_LEVEL_NUM.get(n.price_level), "—")
                    st.write(f"• Nearest: **{n.name}** · {n.distance_ft:,.0f} ft · {npr}")

                with st.expander(f"All coffee ({len(comm.coffee)})"):
                    rows = [
                        {
                            "name": p.name,
                            "rating": p.rating,
                            "reviews": p.rating_count,
                            "price_level": p.price_level or "—",
                            "price_range": (
                                f"${p.price_range['start']}-${p.price_range['end']}"
                                if p.price_range and p.price_range.get("start") and p.price_range.get("end")
                                else "—"
                            ),
                            "distance_ft": round(p.distance_ft),
                            "address": p.address,
                        }
                        for p in comm.coffee
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        with col_y:
            st.markdown("**Co-tenants** (within 500 ft)")
            if not comm.cotenants:
                st.caption("No co-tenants found in radius.")
            else:
                st.write(f"**{len(comm.cotenants)}** businesses")
                from collections import Counter
                type_mix = Counter([p.primary_type or "other" for p in comm.cotenants])
                top_types = ", ".join(f"{t} ({n})" for t, n in type_mix.most_common(5))
                st.caption(f"Top types: {top_types}")
                with st.expander(f"All co-tenants ({len(comm.cotenants)})"):
                    rows = [
                        {
                            "name": p.name,
                            "type": p.primary_type,
                            "rating": p.rating,
                            "reviews": p.rating_count,
                            "distance_ft": round(p.distance_ft),
                            "address": p.address,
                        }
                        for p in comm.cotenants
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        # Major attractions — full-width row below the two-column grid.
        st.markdown("**Major attractions** (within ¼ mile · 1,320 ft)")
        if not comm.attractions:
            st.caption("No major attractions in radius (filter: 100+ reviews or 4.4★ with 20+ reviews).")
        else:
            st.write(f"**{len(comm.attractions)}** attractions ranked by popularity (review count).")
            top = comm.attractions[:5]
            for a in top:
                stars = f"★ {a.rating}" if a.rating else "—"
                reviews = f"{a.rating_count:,} reviews" if a.rating_count else ""
                st.write(
                    f"• **{a.name}** ({a.primary_type or 'attraction'}) · "
                    f"{a.distance_ft:,.0f} ft · {stars} · {reviews}"
                )
            with st.expander(f"All attractions ({len(comm.attractions)})"):
                rows = [
                    {
                        "name": p.name,
                        "type": p.primary_type,
                        "rating": p.rating,
                        "reviews": p.rating_count,
                        "distance_ft": round(p.distance_ft),
                        "address": p.address,
                    }
                    for p in comm.attractions
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)
    elif comm is not None and not comm.has_places_key:
        st.info("Add a `GOOGLE_MAPS_API_KEY` to `.env` (or Streamlit Cloud Secrets) to unlock co-tenants, coffee, and attractions.")

    st.divider()
    with st.expander("To-do (not yet automated)"):
        st.markdown(
            "- Corner-lot detection via NYC PLUTO geometry (automated same-street check)\n"
            "- Population density (needs TIGER land area)\n"
            "- Alerting on new listings (Phase 2)\n"
            "- Multi-state rules modules (Phase 3)"
        )
