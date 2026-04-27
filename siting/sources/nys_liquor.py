"""NYS State Liquor Authority — off-premises license density by ZIP.

Closest-analogue retail to a cannabis dispensary. High off-premises density
suggests an existing "weekend run" pattern in the corridor; low density may
signal a regulatory cold spot or thin demand worth investigating.

Data source: NY Open Data, "Liquor Authority Quarterly List of Active
Licenses" dataset. Public, no auth required for moderate use. The full
statewide CSV is a few MB; we download once per process and reduce to
ZIP -> off-premises license count. Process restarts on Streamlit Cloud
bound staleness naturally.

Schema-flexibility: the SLA dataset has changed column casing and naming
conventions across releases. We resolve columns by case-insensitive
substring match against the header row rather than hard-coding exact names,
so a column rename ("Premises Zip" -> "premises_zip") doesn't break the
parser.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from collections import Counter
from functools import lru_cache

import requests

# NY Open Data download URL for the SLA active-licenses dataset. Override via
# env var if NYS rotates the dataset ID — the parser is column-name-tolerant
# but the URL itself isn't.
NYS_LIQUOR_URL = os.getenv(
    "NYS_LIQUOR_LICENSES_URL",
    "https://data.ny.gov/api/views/hrvs-fxs2/rows.csv?accessType=DOWNLOAD",
)

# License-type-name patterns that identify off-premises retail (sale of
# packaged product for consumption elsewhere). The "OP" prefix is SLA's
# canonical encoding; the substring matches are belt-and-suspenders for
# datasets that use plain-English descriptions instead.
OFF_PREMISES_MARKERS = (
    "OP ",            # NY SLA prefix: "OP Wine, Beer, Cider", "OP Liquor Store"
    "OFF PREMISES",
    "OFF-PREMISES",
    "LIQUOR STORE",
    "WINE STORE",
    "GROCERY BEER",
    "PACKAGE STORE",
)

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _index() -> dict[str, int]:
    """Download the SLA active-licenses CSV and bucket off-premises by ZIP.

    Returns dict keyed by 5-digit ZIP. An empty dict signals fetch/parse
    failure — callers distinguish "ZIP not in dataset" (count 0) from
    "data unavailable" (lookup_count returns None) by checking the dict.
    """
    try:
        r = requests.get(NYS_LIQUOR_URL, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("NYS SLA license fetch failed: %s", e)
        return {}

    reader = csv.reader(io.StringIO(r.text))
    try:
        header = next(reader)
    except StopIteration:
        return {}

    # Normalize headers to lowercased alphanumeric — collapses "License Type
    # Name", "license_type_name", "license-type-name" all to the same key, so
    # a Socrata format flip between Title Case and snake_case doesn't break us.
    def _norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())

    header_norm = [_norm(h) for h in header]

    def find(needles: tuple[str, ...]) -> int | None:
        for i, h in enumerate(header_norm):
            for n in needles:
                if _norm(n) in h:
                    return i
        return None

    zip_col = find(("zip",))
    type_col = find(("license type name", "license type", "license class"))
    if zip_col is None or type_col is None:
        log.warning(
            "NYS SLA CSV missing expected columns; got header=%s",
            header,
        )
        return {}

    counts: Counter[str] = Counter()
    for row in reader:
        if len(row) <= max(zip_col, type_col):
            continue
        zipv = (row[zip_col] or "").strip()[:5]
        if len(zipv) != 5 or not zipv.isdigit():
            continue
        type_name = (row[type_col] or "").strip().upper()
        if any(marker in type_name for marker in OFF_PREMISES_MARKERS):
            counts[zipv] += 1

    return dict(counts)


def lookup_count(zip_code: str | None) -> int | None:
    """Return off-premises license count for a 5-digit ZIP, or None.

    None vs 0 matters here: None means the SLA fetch failed (UI hides the
    field), 0 means the ZIP is in NY but has no off-premises licenses
    (UI surfaces it — that's an honest signal).
    """
    if not zip_code:
        return None
    z = zip_code.strip()[:5]
    if len(z) != 5 or not z.isdigit():
        return None
    idx = _index()
    if not idx:
        return None
    return idx.get(z, 0)
