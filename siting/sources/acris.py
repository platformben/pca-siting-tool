"""ACRIS — NYC Department of Finance deed records by BBL.

Closes the loop on the parcel picture: PLUTO tells us the current owner
and assessed value, ACRIS tells us what they paid and when. Together
those numbers give an operator the landlord's basis — the floor below
which a deal probably won't pencil.

Two-step query (ACRIS is normalized into separate Socrata datasets):

1. Real Property Legals (`8h5j-fqxa`) — links document IDs to BBLs via
   borough/block/lot triples. Pulls all document IDs that have ever been
   recorded against this lot.

2. Real Property Master (`bnx9-e6tj`) — the actual deed/transaction
   records. We filter to doc_type starting with DEED (sales of property,
   excluding mortgages, easements, and corrections) and sort newest-first.

Public Socrata endpoints, no auth required for moderate use. NYC_APP_TOKEN
env var increases rate limits if set.
"""
from __future__ import annotations

import os
import requests
from dataclasses import dataclass

ACRIS_LEGALS = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
ACRIS_MASTER = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"


@dataclass
class Deed:
    document_id: str
    doc_type: str            # raw ACRIS code (DEED, DEEDX, etc.)
    sale_amount: int | None  # document_amt — 0/None for gifts, foreclosures, $1 transfers
    recorded_date: str       # ISO date string (YYYY-MM-DD)
    document_date: str | None


def _headers() -> dict:
    tok = os.getenv("NYC_APP_TOKEN")
    return {"X-App-Token": tok} if tok else {}


def recent_deeds_for_bbl(bbl: str | None, limit: int = 5) -> list[Deed]:
    """Return recent recorded deeds for a BBL, newest-first.

    Empty list on any failure (network, malformed BBL, no records). The
    UI treats empty as "no sales history surfaced" and hides the line.
    """
    if not bbl or len(bbl) != 10 or not bbl.isdigit():
        return []

    borough = int(bbl[0])
    block = int(bbl[1:6])
    lot = int(bbl[6:10])

    # Step 1 — pull document IDs for this borough/block/lot triple.
    try:
        r = requests.get(
            ACRIS_LEGALS,
            params={
                "borough": borough,
                "block": block,
                "lot": lot,
                "$select": "document_id",
                "$limit": "200",
            },
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        legals_rows = r.json()
    except (requests.RequestException, ValueError):
        return []

    doc_ids: list[str] = []
    seen: set[str] = set()
    for row in legals_rows:
        did = row.get("document_id")
        if did and did not in seen:
            doc_ids.append(did)
            seen.add(did)
    if not doc_ids:
        return []

    # Step 2 — pull deed details. Cap the IN-clause at 50 to keep the URL
    # under typical query-string limits; ACRIS Legals can have hundreds of
    # entries on a long-lived lot but the caller only wants recent deeds.
    ids_str = ",".join(f"'{did}'" for did in doc_ids[:50])
    try:
        r = requests.get(
            ACRIS_MASTER,
            params={
                # doc_type LIKE 'DEED%' catches DEED, DEEDX, DEEDS, etc. and
                # excludes mortgages (MTGE*), assignments (ASST*), corrections
                # (CORD), agreements (AGMT), and other non-sale records.
                "$where": f"document_id IN ({ids_str}) AND starts_with(doc_type, 'DEED')",
                "$order": "recorded_filed DESC",
                "$limit": str(limit),
            },
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        master_rows = r.json()
    except (requests.RequestException, ValueError):
        return []

    out: list[Deed] = []
    for row in master_rows:
        amt_raw = row.get("document_amt")
        try:
            amt = int(float(amt_raw)) if amt_raw not in (None, "", "0") else None
        except (TypeError, ValueError):
            amt = None
        out.append(
            Deed(
                document_id=row.get("document_id", ""),
                doc_type=row.get("doc_type", "") or "",
                sale_amount=amt,
                recorded_date=(row.get("recorded_filed") or "")[:10],
                document_date=((row.get("document_date") or "")[:10]) or None,
            )
        )
    return out


def last_priced_sale(deeds: list[Deed]) -> Deed | None:
    """Most recent deed with a non-trivial sale amount.

    ACRIS records gifts, intra-family transfers, and $1 conveyances as
    deeds with amount 0 or null. Operators want the most recent deed
    with a real arms-length-looking number, so we walk the list and
    return the first deed with sale_amount > $1.
    """
    for d in deeds:
        if d.sale_amount and d.sale_amount > 1:
            return d
    return None
