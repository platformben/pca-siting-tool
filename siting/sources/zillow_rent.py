"""Zillow ZORI — current asking-rent index by ZIP.

Public CSV from Zillow Research, refreshed monthly. We pull the
"All Homes Plus Multifamily, Smoothed, Seasonally Adjusted" series —
Zillow's headline rent index, comparable across ZIPs and months and
less noisy than the raw asking-rent stream.

Intended as a current-market companion to the ACS tract-level median
rent: ACS tells you what people *paid* a year or two ago, ZORI tells
you what's *being asked* this month. The gap between them is a clean
gentrification / market-shift signal.

The full CSV is ~5 MB and ~30K ZIPs. We download once per process,
parse out only the latest non-empty value per ZIP, and cache the
resulting compact dict via lru_cache. Streamlit Cloud restarts
processes regularly, so freshness is naturally bounded — we don't
need a TTL.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import requests

# Zillow Research's stable CDN path. The filename encodes the series:
#   Zip   = ZIP-code geography
#   zori  = Zillow Observed Rent Index
#   uc    = "unconditioned" (i.e. all observations, not type-restricted)
#   sfrcondomfr = single-family + condo + multi-family rentals
#   sm    = smoothed
#   month = monthly cadence
# Override via env var if Zillow renames the file.
ZORI_URL = os.getenv(
    "ZILLOW_ZORI_URL",
    "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv",
)

log = logging.getLogger(__name__)


@dataclass
class ZoriObservation:
    zip_code: str
    asking_rent: float
    month: str  # ISO date string of the column header (e.g. "2024-09-30")


@lru_cache(maxsize=1)
def _index() -> dict[str, ZoriObservation]:
    """Download the ZORI CSV and reduce it to ZIP -> latest observation.

    Errors (network, HTTP, malformed CSV) yield an empty dict, which makes
    every subsequent lookup() return None — the UI handles that as "data
    unavailable" rather than crashing the evaluation.
    """
    try:
        r = requests.get(ZORI_URL, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Zillow ZORI fetch failed: %s", e)
        return {}

    reader = csv.reader(io.StringIO(r.text))
    try:
        header = next(reader)
    except StopIteration:
        return {}

    try:
        zip_col = header.index("RegionName")
    except ValueError:
        log.warning("Zillow ZORI CSV missing 'RegionName' column")
        return {}

    # Month columns are ISO-date-shaped headers ("2024-09-30") at the right
    # end of the row. Walk the header to find them so we don't depend on the
    # exact metadata-column count, which Zillow has shifted in past releases.
    month_cols: list[tuple[int, str]] = []
    for i, h in enumerate(header):
        if len(h) == 10 and h[4] == "-" and h[7] == "-":
            try:
                datetime.strptime(h, "%Y-%m-%d")
                month_cols.append((i, h))
            except ValueError:
                pass

    if not month_cols:
        log.warning("Zillow ZORI CSV has no recognizable month columns")
        return {}

    # We want the latest non-empty value per ZIP — walk right-to-left.
    month_cols_rev = list(reversed(month_cols))

    out: dict[str, ZoriObservation] = {}
    for row in reader:
        if len(row) <= zip_col:
            continue
        zip_code = (row[zip_col] or "").strip()
        if len(zip_code) != 5 or not zip_code.isdigit():
            continue
        for idx, month in month_cols_rev:
            if idx >= len(row):
                continue
            v = (row[idx] or "").strip()
            if not v:
                continue
            try:
                value = float(v)
            except ValueError:
                continue
            out[zip_code] = ZoriObservation(
                zip_code=zip_code, asking_rent=value, month=month
            )
            break

    return out


def lookup(zip_code: str | None) -> ZoriObservation | None:
    """Return the latest ZORI observation for a 5-digit US ZIP, or None.

    Tolerates ZIP+4 ("11211-1234") by truncating to the leading 5 digits.
    """
    if not zip_code:
        return None
    z = zip_code.strip()[:5]
    if len(z) != 5 or not z.isdigit():
        return None
    return _index().get(z)
