"""PCA Scout — Streamlit UI.

Run: streamlit run app.py
"""
from __future__ import annotations

import os
from collections import Counter

import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_searchbox import st_searchbox

# Streamlit Community Cloud injects secrets via st.secrets (a TOML file in
# the dashboard) rather than process env vars. Bridge them into os.environ
# so the os.getenv() calls in siting/* keep working unchanged. Local dev
# keeps using .env via load_dotenv() below.
load_dotenv()
try:
    for _k, _v in dict(st.secrets).items():
        os.environ.setdefault(_k, str(_v))
except (FileNotFoundError, AttributeError):
    pass

from siting import branding, search_log
from siting.sources import google_places
from siting.sources.google_places import PRICE_LEVEL_NUM


def _humanize(s: str | None) -> str:
    """Google Places types come back as `grocery_store`, `hair_salon`, etc.
    Render them as `Grocery store`, `Hair salon` for display."""
    if not s:
        return "—"
    return s.replace("_", " ").strip().capitalize()


def _routes(s: str | None) -> str:
    """MTA's daytime_routes is space-separated ("2 5", "B D F M"). Show
    as "2/5", "B/D/F/M" — the way New Yorkers actually say them."""
    if not s:
        return ""
    return "/".join(s.split())

# `evaluate` and its transitive imports (OCM, OSM, NYS Schools, Census, MTA,
# NYC OpenData, etc.) are deferred until the user clicks Evaluate. Keeping
# them out of the autocomplete path lowers the resident memory baseline.


st.set_page_config(
    page_title="PCA Scout",
    page_icon=branding.page_icon_path(),
    layout="wide",
)

branding.apply()


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


# ---------- Hero ----------

branding.hero(
    title_html="PCA <em>Scout</em>",
    sub_html=(
        "Real estate due diligence for <em>independent</em> dispensary operators. "
        "OCM compliance gates and a commercial snapshot, on any address."
    ),
    ref="LOCATION REVIEW",
)


@st.dialog("Search log", width="large")
def _show_log_dialog() -> None:
    n = search_log.count()
    st.caption(f"{n} address{'es' if n != 1 else ''} evaluated this session.")
    if not n:
        st.info("Run an evaluation to start logging.")
        return
    rows = search_log.to_rows()
    st.dataframe(rows, use_container_width=True, hide_index=True)
    cols = st.columns([1, 1, 2])
    with cols[0]:
        st.download_button(
            label="Download .csv",
            data=search_log.to_csv_bytes(),
            file_name="pca-scout-log.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with cols[1]:
        if st.button("Clear log", use_container_width=True):
            search_log.clear()
            st.rerun()
    st.caption("Log lives only in this browser session — download before closing the tab.")


# ---------- Address input ----------

_autocomplete_source = (
    "Google Places · US-wide" if google_places.has_key() else "NYC only · no API key"
)
selected = st_searchbox(
    _autocomplete,
    key="address_search",
    placeholder="Start typing an address…",
    default=None,
    clear_on_submit=False,
    rerun_on_update=True,
)

# Single-column form area — Evaluate button on the left, autocomplete-source
# caption flows beneath as muted helper text. (The previous 1:3 column split
# rendered the caption inside an input-shaped wrapper that read as a broken
# second field.)
btn_col, _btn_pad = st.columns([1, 4])
with btn_col:
    run_now = st.button(
        "Evaluate", type="primary", disabled=not selected, use_container_width=True
    )
st.caption(f"Address suggestions: {_autocomplete_source}")


# ---------- Result rendering ----------

if run_now and selected:
    with st.spinner("Geocoding and running compliance checks…"):
        # Lazy import — first click pulls in the data clients; subsequent
        # clicks reuse Python's import cache.
        from siting.evaluator import evaluate
        import gc

        result = evaluate(selected.strip())
        search_log.record(result)
        gc.collect()

    if not result.geo:
        st.error(result.findings[0].summary if result.findings else "Geocoding failed.")
        st.stop()

    geo = result.geo
    branding.overall_verdict(result.overall)

    bbl_html = (
        f"<span style='font-family:Geist Mono,monospace;font-size:0.8125rem;color:{branding.TEXT_SECONDARY};'>"
        f" · BBL {geo.bbl}</span>"
        if geo.bbl else ""
    )
    st.markdown(
        f"<div style='font-size:0.95rem;color:{branding.NEAR_BLACK};margin-bottom:0.75rem;'>"
        f"<strong>Resolved:</strong> {geo.address}"
        f" <span style='font-family:Geist Mono,monospace;color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
        f"({geo.lat:.5f}, {geo.lon:.5f})</span>"
        f"{bbl_html}</div>",
        unsafe_allow_html=True,
    )
    st.link_button(
        "Open in Google Maps — cross-check schools and houses of worship",
        f"https://www.google.com/maps/search/?api=1&query={geo.lat},{geo.lon}",
    )

    # ---------- Compliance ----------
    branding.section("Compliance")
    st.caption(
        "Gates from NY Cannabis Law § 72: 1,000 / 2,000 ft from another dispensary, "
        "500 ft + same street from pre-K – HS schools, 200 ft from buildings exclusively used as houses of worship."
    )
    for f in result.findings:
        with st.container(border=True):
            branding.finding_header(f.rule, f.status, f.summary)
            for d in f.details:
                st.caption(d)
            if f.evidence:
                with st.expander(f"Evidence ({len(f.evidence)})"):
                    st.dataframe(f.evidence, use_container_width=True, hide_index=True)

    # ---------- Commercial snapshot ----------
    branding.section("Commercial snapshot")

    col_a, col_b = st.columns(2)
    with col_a:
        nearest = result.nearest_stations[0] if result.nearest_stations else None
        if nearest:
            has_ridership = bool(nearest.ridership and nearest.ridership.weekday_2023)
            if has_ridership:
                big = f"{int(nearest.ridership.weekday_2023):,}"
                rk = (
                    f" · #{nearest.ridership.rank_2023} citywide"
                    if nearest.ridership.rank_2023 else ""
                )
                sub = (
                    f"{nearest.stop_name} ({_routes(nearest.routes)}) · "
                    f"{nearest.distance_ft:,.0f} ft · avg weekday ridership 2023{rk}"
                )
            else:
                big = f"{nearest.distance_ft:,.0f} ft"
                sub = f"{nearest.stop_name} ({_routes(nearest.routes)})"
            branding.stat_band("Nearest subway", big, sub)
        else:
            st.caption("Nearest subway unavailable.")
        # Show 2nd and 3rd nearest as supporting bullets (not full bands)
        for s in result.nearest_stations[1:3]:
            wd = (
                f" · {int(s.ridership.weekday_2023):,}/weekday"
                if s.ridership and s.ridership.weekday_2023 else ""
            )
            st.markdown(
                f"<div style='font-size:0.875rem;color:{branding.TEXT_SECONDARY};margin:0.25rem 0 0 0.25rem;'>"
                f"• {s.stop_name} ({_routes(s.routes)}) · {s.distance_ft:,.0f} ft{wd}</div>",
                unsafe_allow_html=True,
            )

    with col_b:
        d = result.demographics
        if d and d.mhhi:
            branding.stat_band(
                "Census tract MHHI",
                f"${d.mhhi:,}",
                f"Tract {d.tract_fips} · population {d.total_population:,}"
                if d.total_population else f"Tract {d.tract_fips}",
            )
        else:
            st.caption("Census demographics unavailable.")

        if d and d.median_gross_rent:
            burden = d.rent_burden_pct
            if burden is not None:
                # HUD threshold: >30% rent-burdened, >50% severely rent-burdened.
                # Surface the threshold so the number is interpretable on its own.
                if burden >= 50:
                    burden_note = f"{burden:.0f}% of median income — severely rent burdened (HUD)"
                elif burden >= 30:
                    burden_note = f"{burden:.0f}% of median income — rent burdened (HUD >30%)"
                else:
                    burden_note = f"{burden:.0f}% of median income — below HUD burden threshold"
            else:
                burden_note = "Median gross rent (incl. utilities), ACS 5-year"
            branding.stat_band(
                "Median gross rent",
                f"${d.median_gross_rent:,}/mo",
                burden_note,
            )

    # ---------- Co-tenants, coffee, attractions ----------
    comm = result.commercial
    if comm and comm.has_places_key:
        branding.section("Operator economy")

        col_x, col_y = st.columns(2)
        with col_x:
            st.markdown(
                f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                f"margin-bottom:0.5rem;'>COFFEE INDEX · 1,000 FT</div>",
                unsafe_allow_html=True,
            )
            cs = comm.coffee_summary or {}
            if not comm.coffee:
                st.caption("No coffee shops within 1,000 ft.")
            else:
                lo, hi = cs.get("dollar_low"), cs.get("dollar_high")
                if lo and hi:
                    range_str = f"~${lo}–${hi} per cup"
                elif lo:
                    range_str = f"~${lo}+ per cup"
                else:
                    range_str = "price not reported"
                bean = cs.get("bean_rating")
                avg_rating = cs.get("avg_rating")
                bean_str = (
                    f"{'🫘' * bean}{'·' * (5 - bean)} · {bean}/5 (avg Google rating {avg_rating})"
                    if bean else "—"
                )
                branding.stat_band(
                    f"{cs['count']} coffee shops",
                    range_str,
                    f"Quality {bean_str}"
                    + (f" · brands: {', '.join(cs['brands'])}" if cs.get("brands") else ""),
                )
                if cs.get("nearest"):
                    n = cs["nearest"]
                    pr = n.price_range
                    if pr and pr.get("start") and pr.get("end"):
                        npr = f"${pr['start']}–${pr['end']}"
                    else:
                        npr = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}.get(
                            PRICE_LEVEL_NUM.get(n.price_level), "—"
                        )
                    st.markdown(
                        f"<div style='font-size:0.875rem;color:{branding.TEXT_SECONDARY};margin-top:-0.25rem;'>"
                        f"Nearest: <strong style='color:{branding.NEAR_BLACK};'>{n.name}</strong> · "
                        f"{n.distance_ft:,.0f} ft · {npr}</div>",
                        unsafe_allow_html=True,
                    )
                with st.expander(f"All coffee ({len(comm.coffee)})"):
                    rows = [
                        {
                            "Name": p.name,
                            "Rating": p.rating,
                            "Reviews": p.rating_count,
                            "Price": (
                                f"${p.price_range['start']}–${p.price_range['end']}"
                                if p.price_range and p.price_range.get("start") and p.price_range.get("end")
                                else {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}.get(
                                    PRICE_LEVEL_NUM.get(p.price_level), "—"
                                )
                            ),
                            "Distance (ft)": round(p.distance_ft),
                            "Address": p.address,
                        }
                        for p in comm.coffee
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        with col_y:
            st.markdown(
                f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                f"margin-bottom:0.5rem;'>CO-TENANTS · 500 FT</div>",
                unsafe_allow_html=True,
            )
            if not comm.cotenants:
                st.caption("No co-tenants found in radius.")
            else:
                type_mix = Counter([p.primary_type or "other" for p in comm.cotenants])
                top_types = ", ".join(f"{_humanize(t)} ({n})" for t, n in type_mix.most_common(5))
                top1 = _humanize(type_mix.most_common(1)[0][0]) if type_mix else "—"
                branding.stat_band(
                    f"{len(comm.cotenants)} businesses",
                    top1,
                    f"Top types: {top_types}",
                )
                with st.expander(f"All co-tenants ({len(comm.cotenants)})"):
                    rows = [
                        {
                            "Name": p.name,
                            "Type": _humanize(p.primary_type),
                            "Rating": p.rating,
                            "Reviews": p.rating_count,
                            "Distance (ft)": round(p.distance_ft),
                            "Address": p.address,
                        }
                        for p in comm.cotenants
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        # Attractions — full-width row beneath the two-column grid
        st.markdown(
            f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
            f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
            f"margin:1.25rem 0 0.5rem 0;'>MAJOR ATTRACTIONS · ¼ MILE</div>",
            unsafe_allow_html=True,
        )
        if not comm.attractions:
            st.caption("No major attractions in radius (filter: 100+ reviews or 4.4★ with 20+ reviews).")
        else:
            st.markdown(
                f"<div style='font-size:0.9rem;color:{branding.NEAR_BLACK};margin-bottom:0.5rem;'>"
                f"<strong>{len(comm.attractions)}</strong> attractions ranked by popularity.</div>",
                unsafe_allow_html=True,
            )
            for a in comm.attractions[:5]:
                stars = f"★ {a.rating}" if a.rating else "—"
                reviews = f"{a.rating_count:,} reviews" if a.rating_count else ""
                st.markdown(
                    f"<div style='font-size:0.9rem;margin:0.2rem 0;'>"
                    f"• <strong>{a.name}</strong> "
                    f"<span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                    f"({_humanize(a.primary_type) or 'Attraction'}) · {a.distance_ft:,.0f} ft · {stars} · {reviews}"
                    f"</span></div>",
                    unsafe_allow_html=True,
                )
            with st.expander(f"All attractions ({len(comm.attractions)})"):
                rows = [
                    {
                        "Name": p.name,
                        "Type": _humanize(p.primary_type),
                        "Rating": p.rating,
                        "Reviews": p.rating_count,
                        "Distance (ft)": round(p.distance_ft),
                        "Address": p.address,
                    }
                    for p in comm.attractions
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)
    elif comm is not None and not comm.has_places_key:
        st.info("Add a `GOOGLE_MAPS_API_KEY` to unlock co-tenants, coffee, and attractions.")

    with st.expander("Roadmap — not yet automated"):
        st.markdown(
            "- Corner-lot detection via NYC PLUTO geometry\n"
            "- Population density via TIGER land area\n"
            "- Alerting on new listings (Phase 2)\n"
            "- Multi-state rulesets (Phase 3)"
        )
else:
    # Empty state — preview what an evaluation will return so the page has
    # substance before the user runs anything.
    branding.checks_panel()


# ---------- Footer: discrete log access + brand sign-off ----------

_n = search_log.count()
_log_label = f"Search log ({_n})" if _n else "Search log"
_log_l, _log_pad = st.columns([1, 4])
with _log_l:
    if st.button(_log_label, use_container_width=True, type="secondary"):
        _show_log_dialog()

branding.signoff()
