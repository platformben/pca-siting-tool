"""PCA brand application for the Streamlit UI.

Implements the brand's signature visual moves:
- Geist (sans) + Noto Serif (italic) + Geist Mono — loaded from Google Fonts
- Italic serif accent word inside Geist headlines
- Small-caps indigo section labels with hairline rules
- Status badges using the utility-color palette (this is internal product UI,
  not client-facing material — utility colors are explicitly allowed)
- Lavender stat bands for commercial metrics
- Editorial sign-off footer

All canon: see ~/.claude/skills/pca-brand/design-system/.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

# ---------- Color tokens ----------
# Brand canon (do not deviate)
INDIGO = "#36286f"
LAVENDER = "#c7bbdc"
NEAR_BLACK = "#1a1816"
WHITE = "#ffffff"

# Semantic surfaces / borders derived in tokens/colors.json
SURFACE_SUBTLE = "#f5f2fa"       # lavender tinted ~10%
BORDER_SUBTLE = "#e8e4f0"        # lavender tinted ~30%
TEXT_SECONDARY = "#5a5458"       # near-black at 70%
INDIGO_HOVER = "#2a1f58"         # indigo darkened ~15%

# Utility colors — internal product UI ONLY (utility-colors.json)
STATUS_COLORS = {
    "pass": {"fg": "#2f7a4f", "bg": "#e8f3ed", "border": "#a8d0b8", "label": "PASS"},
    "fail": {"fg": "#a23838", "bg": "#f5e3e3", "border": "#d4a3a3", "label": "FAIL"},
    "warn": {"fg": "#b8861a", "bg": "#faf2e0", "border": "#e0c58a", "label": "REVIEW"},
    "info": {"fg": INDIGO, "bg": SURFACE_SUBTLE, "border": LAVENDER, "label": "NOTE"},
}

OVERALL_COLORS = {
    "PASS": "#2f7a4f",
    "REVIEW": "#b8861a",
    "FAIL": "#a23838",
}


def _css() -> str:
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&family=Noto+Serif:ital@1&display=swap');

    html, body, [class*="css"], .stApp, .stMarkdown, .stText, .stButton button,
    .stTextInput input, .stSelectbox, .stCaption, [data-testid="stMetric"] {{
      font-family: Geist, Inter, -apple-system, BlinkMacSystemFont, sans-serif !important;
    }}
    code, pre, kbd, [data-testid="stCodeBlock"], .stDataFrame * {{
      font-family: 'Geist Mono', 'SF Mono', Menlo, monospace !important;
    }}

    .stApp {{ color: {NEAR_BLACK}; background: {WHITE}; }}

    /* Tighter, more editorial top padding */
    .block-container {{
      padding-top: 2.5rem !important;
      padding-bottom: 4rem;
      max-width: 1180px;
    }}

    /* Hero mark + italic-serif accent headline */
    .pca-hero {{
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      margin-bottom: 1rem;
    }}
    .pca-hero__lockup {{
      display: flex;
      align-items: center;
      gap: 1.1rem;
    }}
    .pca-hero__mark {{
      width: 64px;
      height: 64px;
      flex-shrink: 0;
      object-fit: contain;
      display: block;
    }}
    .pca-hero__title {{
      font-size: 2.75rem;
      font-weight: 700;
      line-height: 1.05;
      letter-spacing: -0.015em;
      color: {NEAR_BLACK};
      margin: 0;
    }}
    .pca-hero__title em {{
      font-family: 'Noto Serif', Charter, Georgia, serif !important;
      font-style: italic;
      font-weight: 400;
      color: {INDIGO};
    }}
    .pca-hero__sub {{
      font-size: 1rem;
      color: {TEXT_SECONDARY};
      max-width: 56ch;
      margin: 0.5rem 0 0.25rem 0;
    }}
    .pca-hero__sub em {{
      font-family: 'Noto Serif', Charter, Georgia, serif !important;
      font-style: italic;
    }}
    .pca-hero__meta {{
      font-family: 'Geist Mono', 'SF Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.6875rem;
      color: {TEXT_SECONDARY};
    }}

    /* Empty-state "What Scout checks" panel */
    .pca-checks {{
      margin: 2.5rem 0 1.5rem 0;
      padding: 1.75rem 2rem 2rem 2rem;
      background: {SURFACE_SUBTLE};
      border-left: 3px solid {INDIGO};
    }}
    .pca-checks__label {{
      font-family: 'Geist Mono', 'SF Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 0.6875rem;
      color: {TEXT_SECONDARY};
      margin-bottom: 1.25rem;
    }}
    .pca-checks__row {{
      display: flex;
      gap: 1rem;
      padding: 0.85rem 0;
      border-top: 1px solid {BORDER_SUBTLE};
    }}
    .pca-checks__row:first-of-type {{
      border-top: none;
      padding-top: 0;
    }}
    .pca-checks__num {{
      font-family: 'Geist Mono', 'SF Mono', monospace;
      font-size: 0.75rem;
      color: {INDIGO};
      letter-spacing: 0.12em;
      flex-shrink: 0;
      padding-top: 0.15rem;
    }}
    .pca-checks__title {{
      font-weight: 600;
      font-size: 0.95rem;
      color: {NEAR_BLACK};
      margin-bottom: 0.2rem;
    }}
    .pca-checks__detail {{
      font-size: 0.875rem;
      color: {TEXT_SECONDARY};
      line-height: 1.5;
    }}

    /* Small-caps indigo section labels with hairline rule beneath */
    h2, .stSubheader {{
      text-transform: uppercase !important;
      letter-spacing: 0.16em !important;
      font-size: 0.8125rem !important;
      font-weight: 600 !important;
      color: {INDIGO} !important;
      padding-bottom: 0.5rem !important;
      border-bottom: 1px solid {BORDER_SUBTLE} !important;
      margin-bottom: 1.25rem !important;
      margin-top: 2rem !important;
    }}

    /* Card-style finding container */
    [data-testid="stVerticalBlockBorderWrapper"] {{
      border: 1px solid {BORDER_SUBTLE} !important;
      border-radius: 0 !important;
      background: {WHITE};
    }}

    /* Inputs and buttons */
    .stTextInput input, .stTextArea textarea {{
      border-radius: 0 !important;
      border: 1px solid {BORDER_SUBTLE} !important;
    }}
    .stTextInput input:focus, .stTextArea textarea:focus {{
      border-color: {INDIGO} !important;
      box-shadow: 0 0 0 1px {INDIGO} !important;
    }}
    .stButton > button[kind="primary"], button[kind="primary"] {{
      background: {INDIGO} !important;
      color: {WHITE} !important;
      border: none !important;
      border-radius: 0.5rem !important;
      font-weight: 500 !important;
      letter-spacing: 0.02em;
    }}
    .stButton > button[kind="primary"]:hover {{
      background: {INDIGO_HOVER} !important;
    }}
    .stButton > button[kind="secondary"] {{
      background: {WHITE} !important;
      color: {INDIGO} !important;
      border: 1px solid {LAVENDER} !important;
      border-radius: 0.5rem !important;
      font-weight: 500 !important;
    }}
    .stButton > button[kind="secondary"]:hover {{
      background: {SURFACE_SUBTLE} !important;
      border-color: {INDIGO} !important;
    }}
    .stDownloadButton > button {{
      background: {INDIGO} !important;
      color: {WHITE} !important;
      border-radius: 0.5rem !important;
    }}

    /* Searchbox tweaks */
    [data-testid="stSearchbox"] input,
    div[data-testid="stForm"] input {{
      border-radius: 0 !important;
    }}

    /* Status badge — used inline in finding cards */
    .pca-badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.2rem 0.6rem;
      border-radius: 9999px;
      font-family: 'Geist Mono', 'SF Mono', monospace;
      font-size: 0.6875rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      vertical-align: middle;
    }}
    .pca-badge--pass  {{ background: #e8f3ed; color: #2f7a4f; border: 1px solid #a8d0b8; }}
    .pca-badge--fail  {{ background: #f5e3e3; color: #a23838; border: 1px solid #d4a3a3; }}
    .pca-badge--warn  {{ background: #faf2e0; color: #b8861a; border: 1px solid #e0c58a; }}
    .pca-badge--info  {{ background: {SURFACE_SUBTLE}; color: {INDIGO}; border: 1px solid {LAVENDER}; }}

    /* Overall verdict block */
    .pca-verdict {{
      display: flex;
      align-items: baseline;
      gap: 1rem;
      padding: 1rem 1.25rem;
      border-left: 4px solid {INDIGO};
      background: {SURFACE_SUBTLE};
      margin-bottom: 1rem;
    }}
    .pca-verdict__label {{
      font-family: 'Geist Mono', 'SF Mono', monospace;
      font-size: 0.6875rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: {TEXT_SECONDARY};
    }}
    .pca-verdict__value {{
      font-size: 2rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }}
    .pca-verdict--pass {{ border-left-color: #2f7a4f; }}
    .pca-verdict--pass .pca-verdict__value {{ color: #2f7a4f; }}
    .pca-verdict--review {{ border-left-color: #b8861a; }}
    .pca-verdict--review .pca-verdict__value {{ color: #b8861a; }}
    .pca-verdict--fail {{ border-left-color: #a23838; }}
    .pca-verdict--fail .pca-verdict__value {{ color: #a23838; }}

    /* Lavender stat band — for commercial metrics */
    .pca-stat-band {{
      background: {SURFACE_SUBTLE};
      padding: 1.25rem 1.5rem;
      border-left: 3px solid {INDIGO};
      margin-bottom: 0.75rem;
    }}
    .pca-stat-band__label {{
      font-family: 'Geist Mono', 'SF Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.6875rem;
      color: {TEXT_SECONDARY};
      margin-bottom: 0.25rem;
    }}
    .pca-stat-band__value {{
      font-size: 1.75rem;
      font-weight: 700;
      color: {INDIGO};
      line-height: 1.05;
    }}
    .pca-stat-band__detail {{
      font-size: 0.875rem;
      color: {TEXT_SECONDARY};
      margin-top: 0.35rem;
    }}

    /* Sign-off footer */
    .pca-signoff {{
      margin-top: 4rem;
      padding-top: 2rem;
      border-top: 1px solid {BORDER_SUBTLE};
      text-align: center;
      font-family: 'Noto Serif', Charter, Georgia, serif;
      font-style: italic;
      color: {TEXT_SECONDARY};
      font-size: 1rem;
    }}
    .pca-signoff__meta {{
      margin-top: 0.5rem;
      font-family: 'Geist Mono', 'SF Mono', monospace;
      font-style: normal;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 0.6875rem;
      color: {TEXT_SECONDARY};
    }}

    /* Caption styling */
    [data-testid="stCaptionContainer"], .stCaption {{
      color: {TEXT_SECONDARY} !important;
      font-size: 0.8125rem;
    }}

    /* Tighter dividers */
    hr {{
      border-color: {BORDER_SUBTLE} !important;
      margin: 2rem 0 !important;
    }}
    </style>
    """


def apply() -> None:
    """Inject the PCA stylesheet. Call once at app startup."""
    st.markdown(_css(), unsafe_allow_html=True)


# ---------- Reusable rendered blocks ----------

ASSETS = Path(__file__).parent / "assets"


@lru_cache(maxsize=4)
def _mark_data_url(name: str = "pca-mark-purple.png") -> str:
    """Base64-encode the brand mark for inline embedding.

    Streamlit Cloud occasionally lags serving static assets via st.image(),
    which leaves the hero looking like the mark "didn't ship." Inlining
    the bytes as a data: URL eliminates that path — the mark renders the
    instant the page parses.
    """
    p = ASSETS / "brand" / name
    if not p.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def hero(title_html: str, sub_html: str, ref: str | None = None) -> None:
    """Inline mark + italic-serif headline lockup, sub, and mono ref line."""
    src = _mark_data_url()
    mark_img = (
        f"<img class='pca-hero__mark' src='{src}' alt='PCA' />" if src else ""
    )
    meta = f"<div class='pca-hero__meta'>REF. PCA · SCOUT · {ref}</div>" if ref else ""
    st.markdown(
        f"""
        <div class="pca-hero">
          <div class="pca-hero__lockup">
            {mark_img}
            <h1 class="pca-hero__title">{title_html}</h1>
          </div>
          <p class="pca-hero__sub">{sub_html}</p>
          {meta}
        </div>
        """,
        unsafe_allow_html=True,
    )


def checks_panel() -> None:
    """Empty-state panel — what Scout will check, shown before any search runs.

    Fills the dead space between the form and the footer when the page first
    loads, and previews what the user will get back from an evaluation.
    """
    items = [
        (
            "01",
            "Compliance gates",
            "1,000 / 2,000 ft from another dispensary, 500 ft + same-street "
            "from pre-K – HS schools, 200 ft from houses of worship.",
        ),
        (
            "02",
            "Commercial snapshot",
            "Nearest subway with 2023 weekday ridership and citywide rank, "
            "census-tract median household income, total population.",
        ),
        (
            "03",
            "Operator economy",
            "Co-tenants within 500 ft, coffee index within 1,000 ft "
            "(price band, quality, brands), major attractions within ¼ mile.",
        ),
    ]
    rows = "".join(
        f"""<div class="pca-checks__row">
              <span class="pca-checks__num">{n}</span>
              <div>
                <div class="pca-checks__title">{title}</div>
                <div class="pca-checks__detail">{detail}</div>
              </div>
            </div>"""
        for n, title, detail in items
    )
    st.markdown(
        f"""
        <div class="pca-checks">
          <div class="pca-checks__label">What Scout checks</div>
          {rows}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(label: str) -> None:
    """Render a small-caps indigo section header with hairline rule.

    (We can't fully restyle st.subheader without breaking everything, so
    this is a thin wrapper that emits the canonical pattern directly.)
    """
    st.markdown(
        f"""<h2 style="text-transform:uppercase;letter-spacing:0.16em;font-size:0.8125rem;
        font-weight:600;color:{INDIGO};padding-bottom:0.5rem;
        border-bottom:1px solid {BORDER_SUBTLE};margin:2rem 0 1.25rem 0;">{label}</h2>""",
        unsafe_allow_html=True,
    )


def overall_verdict(overall: str) -> None:
    """Editorial verdict block — small label, oversized status word, indigo rule."""
    klass = {"PASS": "pass", "REVIEW": "review", "FAIL": "fail"}.get(overall, "review")
    st.markdown(
        f"""
        <div class="pca-verdict pca-verdict--{klass}">
          <span class="pca-verdict__label">Overall</span>
          <span class="pca-verdict__value">{overall}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_badge(status: str) -> str:
    """Return inline HTML for a status pill (pass/fail/warn/info)."""
    s = STATUS_COLORS.get(status, STATUS_COLORS["info"])
    return f"<span class='pca-badge pca-badge--{status}'>{s['label']}</span>"


def finding_header(rule: str, status: str, summary: str) -> None:
    """Header line inside a finding card: badge + rule + summary."""
    s = STATUS_COLORS.get(status, STATUS_COLORS["info"])
    st.markdown(
        f"""
        <div style="display:flex;align-items:flex-start;gap:0.75rem;margin-bottom:0.5rem;">
          {status_badge(status)}
          <div style="flex:1;">
            <div style="font-weight:600;font-size:0.95rem;color:{NEAR_BLACK};">{rule}</div>
            <div style="font-size:0.875rem;color:{s['fg']};margin-top:0.15rem;">{summary}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def stat_band(label: str, value: str, detail: str = "") -> None:
    """Lavender stat band — label / oversized indigo number / detail."""
    detail_html = f"<div class='pca-stat-band__detail'>{detail}</div>" if detail else ""
    st.markdown(
        f"""
        <div class="pca-stat-band">
          <div class="pca-stat-band__label">{label}</div>
          <div class="pca-stat-band__value">{value}</div>
          {detail_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def signoff() -> None:
    """Editorial italic-serif sign-off + reference metadata."""
    st.markdown(
        """
        <div class="pca-signoff">
          Open stronger. Scale faster. <em>Stay independent.</em>
          <div class="pca-signoff__meta">PLATFORM CANNABIS ADVISORS · SCOUT · MMXXVI</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_icon_path() -> str:
    """Absolute path to the PCA monogram for use as Streamlit page icon.

    Uses the circular badge variant — the bare monogram's hairlines disappear
    at favicon size, the inscribed circle keeps the mark legible at 16-32px.
    """
    return str(ASSETS / "brand" / "pca-mark-purple-badge.png")
