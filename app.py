"""PCA Scout — Streamlit UI.

Run: streamlit run app.py
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime

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


def _bbl_links(bbl: str | None) -> tuple[str | None, str | None]:
    """Build ZoLa + DOB Property Profile deep-links from a 10-digit BBL.

    BBL format: 1-digit borough + 5-digit block + 4-digit lot. Returns
    (zola_url, dob_url) — both None if the BBL is malformed.
    """
    if not bbl or len(bbl) != 10 or not bbl.isdigit():
        return (None, None)
    boro = bbl[0]
    block = str(int(bbl[1:6]))   # strip leading zeros for the path segment
    lot = str(int(bbl[6:10]))
    zola = f"https://zola.planning.nyc.gov/lot/{boro}/{block}/{lot}"
    dob = (
        "https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet"
        f"?boro={boro}&block={block}&lot={lot}"
    )
    return (zola, dob)

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


# Session state for Compare mode. Full Evaluation objects live here keyed
# by resolved address so the sidebar can re-render any of them as a hero
# card without re-running the evaluation pipeline.
_EVALS_KEY = "_pca_session_evals"
_SELECTED_KEY = "_pca_compare_selected"
_COMPARE_MODE_KEY = "_pca_compare_mode"

def _evals() -> dict:
    if _EVALS_KEY not in st.session_state:
        st.session_state[_EVALS_KEY] = {}
    return st.session_state[_EVALS_KEY]


def _selected_for_compare() -> set:
    if _SELECTED_KEY not in st.session_state:
        st.session_state[_SELECTED_KEY] = set()
    return st.session_state[_SELECTED_KEY]


def _render_sidebar() -> bool:
    """Render the persistent sidebar with evaluated candidates + Compare button.

    Returns True if the main panel should render Compare mode, False for
    normal single-eval mode.
    """
    evals = _evals()
    selected = _selected_for_compare()

    with st.sidebar:
        # Brand mark + section label
        st.markdown(
            f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
            f"letter-spacing:0.16em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
            f"margin-bottom:0.75rem;'>EVALUATED THIS SESSION</div>",
            unsafe_allow_html=True,
        )

        if not evals:
            st.caption("No evaluations yet. Search an address to begin.")
            return False

        st.caption(
            f"{len(evals)} address{'es' if len(evals) != 1 else ''} evaluated. "
            f"Select 2+ to compare."
        )

        # One row per saved evaluation: checkbox + verdict chip + address +
        # revenue range (when overlay is active).
        new_selected = set()
        for addr, ev in evals.items():
            cset = ev.pca_comparables
            rev_str = ""
            if cset and not cset.is_empty():
                rng = cset.revenue_range_annualized()
                if rng:
                    rev_str = f" · ${rng[0]/1e6:.1f}–${rng[2]/1e6:.1f}M"
            verdict = ev.overall
            checked = st.checkbox(
                f"**{verdict}**{rev_str}  \n_{addr[:60]}_",
                value=addr in selected,
                key=f"_cmp_{addr}",
            )
            if checked:
                new_selected.add(addr)
        st.session_state[_SELECTED_KEY] = new_selected

        st.markdown("---")

        # Compare mode is sticky — clicking the button flips the flag in
        # session state, and the compare view shows an "Exit" button.
        in_compare = st.session_state.get(_COMPARE_MODE_KEY, False)
        compare_label = (
            f"Exit Compare" if in_compare
            else f"Compare selected ({len(new_selected)})"
        )
        if st.button(
            compare_label,
            disabled=(not in_compare and len(new_selected) < 2),
            use_container_width=True,
            type="primary",
        ):
            st.session_state[_COMPARE_MODE_KEY] = not in_compare
            st.rerun()

        # Utility buttons
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="Download .csv",
                data=search_log.to_csv_bytes(),
                file_name="pca-scout-log.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col2:
            if st.button("Clear all", use_container_width=True):
                st.session_state[_EVALS_KEY] = {}
                st.session_state[_SELECTED_KEY] = set()
                search_log.clear()
                st.rerun()

        st.caption(
            "Session-only — close the tab and data clears. Download the CSV "
            "to keep an audit trail."
        )

    return st.session_state.get(_COMPARE_MODE_KEY, False)


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


# ---------- Sidebar + Compare mode ----------
# Sidebar renders the per-session candidate list. Returns True when the user
# clicks "Compare selected" with 2+ checkboxes ticked.
_compare_mode = _render_sidebar()


def _render_compare_view(selected_addrs: set[str]) -> None:
    """Render hero cards for selected candidates side-by-side."""
    branding.section(f"Compare candidates ({len(selected_addrs)})")
    if not selected_addrs:
        st.caption(
            "No candidates selected. Use the sidebar checkboxes to pick 2+ "
            "addresses, then re-enter Compare mode."
        )
        return
    if len(selected_addrs) < 2:
        st.caption(
            "Compare mode needs 2+ candidates. Add another from the sidebar."
        )
    else:
        st.caption(
            "Side-by-side hero verdict + revenue range. Use the sidebar to "
            "add/remove candidates or exit Compare mode."
        )
    evals = _evals()
    cards = [evals[a] for a in selected_addrs if a in evals]
    # Streamlit columns top out at usable widths around 3; cap to keep cards
    # legible. Excess selections render in a second row.
    PER_ROW = 3
    for row_start in range(0, len(cards), PER_ROW):
        row = cards[row_start: row_start + PER_ROW]
        cols = st.columns(len(row))
        for col, ev in zip(cols, row):
            with col:
                cset = ev.pca_comparables
                rev_range = (
                    cset.revenue_range_annualized()
                    if cset and not cset.is_empty() else None
                )
                conf = cset.confidence() if cset and not cset.is_empty() else None
                zola, dob = _bbl_links(ev.geo.bbl) if ev.geo else (None, None)
                branding.hero_verdict(
                    overall=ev.overall,
                    findings=ev.findings,
                    address=ev.geo.address if ev.geo else ev.input_address,
                    bbl=ev.geo.bbl if ev.geo else None,
                    zola_url=zola,
                    dob_url=dob,
                    revenue_range=rev_range,
                    confidence=conf,
                )


if _compare_mode:
    _render_compare_view(_selected_for_compare())
    st.stop()


# ---------- Single-candidate result rendering ----------

if run_now and selected:
    with st.spinner("Geocoding and running compliance checks…"):
        # Lazy import — first click pulls in the data clients; subsequent
        # clicks reuse Python's import cache.
        from siting.evaluator import evaluate
        import gc

        result = evaluate(selected.strip())
        search_log.record(result)
        # Cache the full Evaluation in session state so Compare mode can
        # re-render it without re-running the pipeline.
        if result.geo:
            _evals()[result.geo.address] = result
        gc.collect()

    if not result.geo:
        st.error(result.findings[0].summary if result.findings else "Geocoding failed.")
        st.stop()

    geo = result.geo

    # Hero card: above-the-fold verdict + 5-gate compliance grid + (when
    # the PCA overlay is enabled) the estimated annual revenue range. Single
    # screen the C-suite reads to triage a candidate in/out before scrolling
    # into the detailed sections below.
    zola_url, dob_url = _bbl_links(geo.bbl)
    revenue_range = None
    confidence = None
    if result.pca_comparables and not result.pca_comparables.is_empty():
        revenue_range = result.pca_comparables.revenue_range_annualized()
        confidence = result.pca_comparables.confidence()
    branding.hero_verdict(
        overall=result.overall,
        findings=result.findings,
        address=geo.address,
        bbl=geo.bbl,
        zola_url=zola_url,
        dob_url=dob_url,
        revenue_range=revenue_range,
        confidence=confidence,
    )
    st.link_button(
        "Open in Google Maps — cross-check schools and houses of worship",
        f"https://www.google.com/maps/search/?api=1&query={geo.lat},{geo.lon}",
    )

    # ---------- Compliance — evidence-only ----------
    # Hero card above already shows every gate with its chip and one-liner.
    # This section is for the analyst who wants to dig into evidence + rule
    # text. Streamlit disallows nested st.expander, so the outer expander
    # renders all findings with their evidence tables inline (still gated
    # behind one click).
    with st.expander("Compliance — full rule text + evidence", expanded=False):
        st.caption(
            "Gates from NY Cannabis Law § 72: 1,000 / 2,000 ft from another dispensary, "
            "500 ft + same street from pre-K – HS schools, 200 ft from buildings "
            "exclusively used as houses of worship."
        )
        for f in result.findings:
            branding.finding_header(f.rule, f.status, f.summary)
            for d in f.details:
                st.caption(d)
            if f.evidence:
                st.caption(f"Evidence ({len(f.evidence)}):")
                st.dataframe(f.evidence, use_container_width=True, hide_index=True)
            st.markdown(
                f"<div style='height:1px;background:{branding.BORDER_SUBTLE};"
                f"margin:0.75rem 0;'></div>",
                unsafe_allow_html=True,
            )

    # ---------- The lot ----------
    # NYC-only parcel detail. Renders right after Compliance so the operator
    # gets the physical-site read before market context. Section is hidden
    # entirely outside NYC since PLUTO is a city-of-NY dataset.
    if result.pluto:
        lot = result.pluto
        branding.section("The lot")

        # Header line: address + BBL + owner. PLUTO's address column is often
        # cleaner than the geocoder's (matched to the actual tax lot, not the
        # nearest building entrance Google found).
        owner_part = (
            f" · Owner: <strong style='color:{branding.NEAR_BLACK};'>{lot.owner}</strong>"
            if lot.owner else ""
        )
        zola_url, dob_url = _bbl_links(lot.bbl)
        link_parts = []
        if zola_url:
            link_parts.append(
                f"<a href='{zola_url}' target='_blank' "
                f"style='color:{branding.INDIGO};text-decoration:none;'>ZoLa ↗</a>"
            )
        if dob_url:
            link_parts.append(
                f"<a href='{dob_url}' target='_blank' "
                f"style='color:{branding.INDIGO};text-decoration:none;'>DOB BIS ↗</a>"
            )
        links_html = (
            f" · <span style='font-size:0.8125rem;'>{' · '.join(link_parts)}</span>"
            if link_parts else ""
        )
        st.markdown(
            f"<div style='font-size:0.95rem;color:{branding.NEAR_BLACK};margin-bottom:1rem;'>"
            f"<strong>{lot.address or '—'}</strong>"
            f" <span style='font-family:Geist Mono,monospace;color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
            f"BBL {lot.bbl}</span>"
            f"{links_html}"
            f"<br/><span style='color:{branding.TEXT_SECONDARY};font-size:0.875rem;'>"
            f"Land use: {lot.landuse_code} — {lot.landuse_label} · "
            f"Building class: {lot.bldgclass or '—'}{owner_part}</span></div>",
            unsafe_allow_html=True,
        )

        col_lot, col_bldg = st.columns(2)
        with col_lot:
            # Lot dimensions — frontage is the storefront width, the most
            # operator-relevant single number on this whole section.
            if lot.lot_area or lot.lot_frontage_ft:
                area_str = f"{lot.lot_area:,} sqft" if lot.lot_area else "—"
                if lot.lot_frontage_ft and lot.lot_depth_ft:
                    detail = (
                        f"{lot.lot_frontage_ft} ft frontage × "
                        f"{lot.lot_depth_ft} ft depth"
                    )
                elif lot.lot_frontage_ft:
                    detail = f"{lot.lot_frontage_ft} ft frontage"
                else:
                    detail = "Dimensions partial"
                branding.stat_band("Lot", area_str, detail)
            else:
                st.caption("Lot dimensions unavailable.")

            # Zoning + overlay
            zone_part = lot.zonedist1 or "—"
            overlay_part = (
                f" + commercial overlay {lot.overlay1}"
                if lot.overlay1 and lot.overlay1.upper().startswith(("C1", "C2"))
                else (f" + overlay {lot.overlay1}" if lot.overlay1 else "")
            )
            zone_kind = (
                "commercial"
                if zone_part.upper().startswith(("C", "M"))
                else "residential" if zone_part.upper().startswith("R") else "—"
            )
            branding.stat_band(
                "Zoning",
                zone_part + overlay_part,
                f"Primary district kind: {zone_kind}",
            )

        with col_bldg:
            # Building summary — total area + floors + age + alteration.
            if lot.bldg_area or lot.year_built:
                area_str = f"{lot.bldg_area:,} sqft" if lot.bldg_area else "—"
                age_parts = []
                if lot.num_floors:
                    age_parts.append(
                        f"{int(lot.num_floors)} floor{'s' if int(lot.num_floors) != 1 else ''}"
                    )
                if lot.year_built:
                    yr = f"built {lot.year_built}"
                    if lot.year_altered and lot.year_altered > lot.year_built:
                        yr += f", last altered {lot.year_altered}"
                    age_parts.append(yr)
                if lot.num_buildings and lot.num_buildings > 1:
                    age_parts.append(f"{lot.num_buildings} buildings")
                detail = " · ".join(age_parts) if age_parts else "Year/floor data partial"
                branding.stat_band("Building", area_str, detail)
            else:
                st.caption("Building dimensions unavailable.")

            # FAR utilization + assessed value
            far_pct = lot.far_utilization_pct
            if far_pct is not None:
                if far_pct >= 95:
                    far_note = f"{far_pct:.0f}% of max — built out, no expansion headroom"
                elif far_pct >= 70:
                    far_note = f"{far_pct:.0f}% of max — modest expansion headroom"
                else:
                    far_note = f"{far_pct:.0f}% of max — significant unused FAR"
                branding.stat_band(
                    "Built FAR",
                    f"{lot.built_far:.2f} of {lot.max_commercial_far:.2f}",
                    far_note,
                )
            elif lot.assessed_total:
                branding.stat_band(
                    "Assessed total (DOF)",
                    f"${lot.assessed_total:,}",
                    "Land + improvements; assessed value, not market value",
                )

        # Assessed value as a small caption line if not already shown above
        if far_pct is not None and lot.assessed_total:
            st.markdown(
                f"<div style='font-size:0.875rem;color:{branding.TEXT_SECONDARY};"
                f"margin-top:0.5rem;'>"
                f"DOF assessed total: <strong style='color:{branding.NEAR_BLACK};'>"
                f"${lot.assessed_total:,}</strong> "
                f"<span style='font-size:0.8125rem;'>"
                f"(land + improvements; not market value)</span></div>",
                unsafe_allow_html=True,
            )

        # ACRIS deed history — last priced sale + count of prior transfers.
        # Read together with the assessed value above, this gives the operator
        # the landlord's basis: what they paid, when, and what the city now
        # values it at.
        if result.recent_deeds:
            from siting.sources.acris import last_priced_sale
            priced = last_priced_sale(result.recent_deeds)
            st.markdown(
                f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                f"margin:1.25rem 0 0.5rem 0;'>RECORDED DEEDS · NYC ACRIS</div>",
                unsafe_allow_html=True,
            )
            if priced:
                try:
                    d = datetime.strptime(priced.recorded_date, "%Y-%m-%d")
                    date_str = d.strftime("%b %Y")
                    years_ago = (datetime.now() - d).days / 365.25
                    age_str = (
                        f"{years_ago:.0f} years ago"
                        if years_ago >= 1 else "less than a year ago"
                    )
                except (ValueError, TypeError):
                    date_str = priced.recorded_date or "—"
                    age_str = ""
                age_part = f" · {age_str}" if age_str else ""
                st.markdown(
                    f"<div style='font-size:0.95rem;color:{branding.NEAR_BLACK};'>"
                    f"Last priced sale: "
                    f"<strong>${priced.sale_amount:,}</strong> "
                    f"<span style='color:{branding.TEXT_SECONDARY};font-size:0.875rem;'>"
                    f"recorded {date_str}{age_part}</span></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption(
                    "Deeds on file but no priced sale — likely intra-family "
                    "transfers, gifts, or $1 conveyances."
                )

            # Count of all recorded deeds (priced + unpriced) for context.
            n_deeds = len(result.recent_deeds)
            if n_deeds > 1 or (n_deeds == 1 and not priced):
                # Build a compact "prior years" list from non-most-recent records
                prior_years = []
                for d in result.recent_deeds[1:4]:
                    try:
                        prior_years.append(d.recorded_date[:4])
                    except (TypeError, AttributeError):
                        pass
                prior_part = (
                    f" — prior in {', '.join(prior_years)}" if prior_years else ""
                )
                st.markdown(
                    f"<div style='font-size:0.8125rem;color:{branding.TEXT_SECONDARY};"
                    f"margin-top:0.25rem;'>"
                    f"{n_deeds} recorded deed{'s' if n_deeds != 1 else ''} on file{prior_part}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ---------- Performance estimate (PCA overlay) ----------
    # Internal-only section. Renders only when both the portfolio data and the
    # PCA_OVERLAY_KEY secret are configured — without them, find_comparables
    # returns None and we skip rendering entirely. Public deploy stays public.
    if result.pca_comparables and not result.pca_comparables.is_empty():
        cset = result.pca_comparables
        branding.section("Performance estimate · PCA overlay")
        st.caption(
            "Internal use only. Annualized run-rate inferred from the most-similar "
            "mature stores in the PCA portfolio (≥12 months open). Comparable-set "
            "methodology, not regression — apply C-suite judgment."
        )

        rng = cset.revenue_range_annualized()
        if rng:
            lo, mid, hi = rng
            conf = cset.confidence()
            conf_label = {
                "high":   "High confidence",
                "medium": "Medium confidence",
                "low":    "Low confidence",
            }.get(conf, "Confidence unknown")
            conf_detail = {
                "high":   "3+ strong matches (similarity ≥80)",
                "medium": "3+ decent matches (similarity ≥60)",
                "low":    "few or weak matches — directional only",
            }.get(conf, "")
            branding.stat_band(
                "Estimated annualized revenue",
                f"${lo/1e6:.1f}M – ${hi/1e6:.1f}M",
                f"Median: ${mid/1e6:.2f}M · {conf_label} ({conf_detail})",
            )

        st.markdown(
            f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
            f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
            f"margin:1.25rem 0 0.5rem 0;'>{len(cset.matches)} CLOSEST COMPARABLES</div>",
            unsafe_allow_html=True,
        )
        for m in cset.matches:
            annualized = m.store.revenue_wk * 52
            st.markdown(
                f"<div style='font-size:0.95rem;margin:0.4rem 0;display:flex;"
                f"justify-content:space-between;align-items:baseline;gap:1rem;'>"
                f"<div>"
                f"<strong>{m.store.store}</strong> "
                f"<span style='font-family:Geist Mono,monospace;color:{branding.TEXT_SECONDARY};"
                f"font-size:0.75rem;letter-spacing:0.08em;'>SIM {m.similarity_pct:.0f}%</span>"
                f"<br/>"
                f"<span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                f"{m.store.address}</span></div>"
                f"<div style='text-align:right;white-space:nowrap;'>"
                f"<strong>${annualized/1e6:.2f}M</strong>"
                f"<br/><span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                f"${m.store.revenue_wk:,.0f}/wk · {m.store.margin_pct:.0f}% margin</span></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

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

        if d and d.adult_21_plus is not None:
            # Cannabis is age-21-restricted; this is the actual addressable
            # market in the tract, not the demographic-blind population count.
            pct = d.pct_21_plus
            pct_part = f"{pct:.0f}% of tract" if pct is not None else "tract share unavailable"
            cohort_part = (
                f" · 21–34 cohort: {d.adult_21_to_34:,}"
                if d.adult_21_to_34 is not None else ""
            )
            branding.stat_band(
                "Adult population (21+)",
                f"{d.adult_21_plus:,}",
                f"{pct_part}{cohort_part}",
            )

        # Demographic profile — five dimensions in one compact block, not five
        # more stat bands. Each line is muted, dense, scannable.
        if d and any(v is not None for v in (
            d.median_age, d.pct_bachelors_plus, d.pct_renter_occupied,
            d.pct_transit_commute, d.pct_below_poverty,
        )):
            def _stat(label: str, value: str | None) -> str:
                if value is None:
                    return ""
                return (
                    f"<span style='display:inline-block;margin-right:0.85rem;'>"
                    f"<span style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                    f"letter-spacing:0.1em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};'>"
                    f"{label}</span> "
                    f"<strong style='color:{branding.NEAR_BLACK};'>{value}</strong></span>"
                )

            parts = [
                _stat("Median age", f"{d.median_age:.0f}" if d.median_age is not None else None),
                _stat("Bachelor's+", f"{d.pct_bachelors_plus:.0f}%" if d.pct_bachelors_plus is not None else None),
                _stat("Renter", f"{d.pct_renter_occupied:.0f}%" if d.pct_renter_occupied is not None else None),
                _stat("Transit commute", f"{d.pct_transit_commute:.0f}%" if d.pct_transit_commute is not None else None),
                _stat("Below poverty", f"{d.pct_below_poverty:.0f}%" if d.pct_below_poverty is not None else None),
            ]
            st.markdown(
                f"<div style='margin:0.5rem 0 0.5rem 0;line-height:1.7;font-size:0.875rem;'>"
                f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                f"margin-bottom:0.4rem;'>DEMOGRAPHIC PROFILE · ACS 5-YEAR</div>"
                f"{''.join(parts)}</div>",
                unsafe_allow_html=True,
            )

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
                "Median gross rent (paid)",
                f"${d.median_gross_rent:,}/mo",
                burden_note,
            )

        if result.offpremises_zip_count is not None and result.geo and result.geo.zip:
            n = result.offpremises_zip_count
            if n == 0:
                detail = "No active off-premises licenses in this ZIP — regulatory cold spot or thin demand"
            elif n < 5:
                detail = f"Sparse off-premises retail · ZIP {result.geo.zip} · NYS SLA active"
            elif n < 20:
                detail = f"Moderate off-premises density · ZIP {result.geo.zip} · NYS SLA active"
            else:
                detail = f"Dense off-premises corridor · ZIP {result.geo.zip} · NYS SLA active"
            branding.stat_band(
                "Off-premises licenses",
                f"{n}",
                detail,
            )

        if result.zori:
            z = result.zori
            try:
                month_label = datetime.strptime(z.month, "%Y-%m-%d").strftime("%b %Y")
            except ValueError:
                month_label = z.month
            # Compare ZORI (current asking) against ACS (recent paid) — the gap
            # is the gentrification / market-shift signal.
            if d and d.median_gross_rent and d.median_gross_rent > 0:
                gap_pct = (z.asking_rent - d.median_gross_rent) / d.median_gross_rent * 100
                if gap_pct >= 25:
                    gap_note = f"+{gap_pct:.0f}% vs tract median paid — market shifting fast"
                elif gap_pct >= 10:
                    gap_note = f"+{gap_pct:.0f}% vs tract median paid"
                elif gap_pct >= -10:
                    gap_note = "In line with tract median paid"
                else:
                    gap_note = f"{gap_pct:.0f}% vs tract median paid"
                detail = f"{month_label} · {gap_note} · ZIP {z.zip_code}"
            else:
                detail = f"{month_label} · Zillow ZORI · ZIP {z.zip_code}"
            branding.stat_band(
                "ZIP asking rent (current)",
                f"${z.asking_rent:,.0f}/mo",
                detail,
            )

    # ---------- Neighborhood anchors ----------
    # Closest cannabis competitor (active, within 1.5 mi) plus nearest
    # daily-needs anchors (supermarket, pharmacy). Section renders when any
    # of the three has data — competitors come from OCM (no key required),
    # supermarket/pharmacy come from Places (require GOOGLE_MAPS_API_KEY).
    comm = result.commercial
    has_anchors = bool(
        result.nearest_dispensaries
        or (comm and (comm.nearest_supermarket or comm.nearest_pharmacy))
    )
    in_ny = (result.geo.state or "").upper() in {"NY", "NEW YORK"}
    if has_anchors or in_ny:
        branding.section("Neighborhood anchors")

        # Cannabis competitor — show the section even when none are nearby,
        # because "no competitor in 1.5 mi" is itself a meaningful signal.
        if in_ny:
            st.markdown(
                f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                f"margin-bottom:0.5rem;'>NEAREST ACTIVE DISPENSARIES · 1.5 MI</div>",
                unsafe_allow_html=True,
            )
            if not result.nearest_dispensaries:
                st.caption("No active dispensary licenses within 1.5 mi.")
            else:
                for d in result.nearest_dispensaries:
                    mi = d.distance_ft / 5280
                    label = d.dba or d.entity_name or "(unnamed license)"
                    lic = d.license_type or "Retail"
                    op = (d.operational_status or "").strip()
                    op_chip = f" · {op}" if op else ""
                    addr_parts = [p for p in (d.address, d.city) if p]
                    addr = ", ".join(addr_parts)
                    st.markdown(
                        f"<div style='font-size:0.95rem;margin:0.4rem 0;'>"
                        f"<strong>{label}</strong> "
                        f"<span style='color:{branding.TEXT_SECONDARY};font-size:0.875rem;'>"
                        f"· {mi:.2f} mi · {lic}{op_chip}</span>"
                        f"<br/>"
                        f"<span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                        f"{addr}</span></div>",
                        unsafe_allow_html=True,
                    )

        if comm and (comm.nearest_supermarket or comm.nearest_pharmacy):
            col_sm, col_rx = st.columns(2)
            with col_sm:
                st.markdown(
                    f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                    f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                    f"margin:1.25rem 0 0.5rem 0;'>NEAREST SUPERMARKET · ½ MI</div>",
                    unsafe_allow_html=True,
                )
                sm = comm.nearest_supermarket
                if not sm:
                    st.caption("None within ½ mi.")
                else:
                    rating = (
                        f"★ {sm.rating} ({sm.rating_count:,} reviews)"
                        if sm.rating and sm.rating_count else "—"
                    )
                    st.markdown(
                        f"<div style='font-size:0.95rem;'>"
                        f"<strong>{sm.name}</strong>"
                        f"<br/><span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                        f"{sm.distance_ft:,.0f} ft · {rating}</span></div>",
                        unsafe_allow_html=True,
                    )
            with col_rx:
                st.markdown(
                    f"<div style='font-family:Geist Mono,monospace;text-transform:uppercase;"
                    f"letter-spacing:0.12em;font-size:0.6875rem;color:{branding.TEXT_SECONDARY};"
                    f"margin:1.25rem 0 0.5rem 0;'>NEAREST PHARMACY · ½ MI</div>",
                    unsafe_allow_html=True,
                )
                rx = comm.nearest_pharmacy
                if not rx:
                    st.caption("None within ½ mi.")
                else:
                    rating = (
                        f"★ {rx.rating} ({rx.rating_count:,} reviews)"
                        if rx.rating and rx.rating_count else "—"
                    )
                    st.markdown(
                        f"<div style='font-size:0.95rem;'>"
                        f"<strong>{rx.name}</strong>"
                        f"<br/><span style='color:{branding.TEXT_SECONDARY};font-size:0.8125rem;'>"
                        f"{rx.distance_ft:,.0f} ft · {rating}</span></div>",
                        unsafe_allow_html=True,
                    )

    # ---------- Co-tenants, coffee, attractions ----------
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
                if comm.vacancy_count:
                    flag = (
                        "corridor distress flag (3+ in radius)"
                        if comm.vacancy_count >= 3
                        else "monitor for additional closures"
                    )
                    st.markdown(
                        f"<div style='font-size:0.875rem;color:{branding.TEXT_SECONDARY};"
                        f"margin:0.5rem 0 0.5rem 0;'>"
                        f"<strong style='color:{branding.NEAR_BLACK};'>{comm.vacancy_count}</strong> "
                        f"storefront{'s' if comm.vacancy_count != 1 else ''} marked permanently closed in radius "
                        f"— {flag}</div>",
                        unsafe_allow_html=True,
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


# ---------- Footer ----------
# Search log + download moved to the persistent sidebar. Footer is brand-only.

branding.signoff()
