"""Small geo helpers — distance and street-name normalization.

All distance math uses a local equirectangular projection accurate to
well under a foot at the scales we care about (<= a few km).
"""
from __future__ import annotations

import math
import re

FEET_PER_METER = 3.28084
EARTH_R_M = 6_371_000.0


def haversine_feet(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    meters = 2 * EARTH_R_M * math.asin(math.sqrt(a))
    return meters * FEET_PER_METER


_STREET_SUFFIX = {
    "street": "st", "st": "st",
    "avenue": "ave", "ave": "ave", "av": "ave",
    "boulevard": "blvd", "blvd": "blvd",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "place": "pl", "pl": "pl",
    "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct",
    "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy",
    "square": "sq", "sq": "sq",
    "terrace": "ter", "ter": "ter",
    "way": "way",
    "broadway": "broadway",
}

_DIRECTION = {
    "north": "n", "n": "n",
    "south": "s", "s": "s",
    "east": "e", "e": "e",
    "west": "w", "w": "w",
    "northeast": "ne", "ne": "ne",
    "northwest": "nw", "nw": "nw",
    "southeast": "se", "se": "se",
    "southwest": "sw", "sw": "sw",
}


def normalize_street(raw: str | None) -> str:
    """Canonicalize a street name for loose equality checks.

    "East 115th Street" and "E 115 St" both become "e 115 st".
    Drops numeric-ordinal suffixes (1st -> 1) so renumbering is stable.
    """
    if not raw:
        return ""
    s = raw.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = []
    for tok in s.split():
        tok = _DIRECTION.get(tok, tok)
        tok = _STREET_SUFFIX.get(tok, tok)
        tok = re.sub(r"(\d+)(st|nd|rd|th)$", r"\1", tok)
        tokens.append(tok)
    return " ".join(tokens)


def extract_street_from_address(addr: str | None) -> str:
    """Pull the street-name portion out of a full street address.

    Assumes the format '<house number> <street name>'. Drops the leading
    number, unit/suite tokens, and everything after a comma.
    """
    if not addr:
        return ""
    head = addr.split(",")[0].strip()
    tokens = head.split()
    while tokens and re.match(r"^\d+[-\d]*[a-zA-Z]?$", tokens[0]):
        tokens = tokens[1:]
    out = []
    for tok in tokens:
        if tok.lower() in {"apt", "unit", "suite", "ste", "#", "fl", "floor"}:
            break
        out.append(tok)
    return " ".join(out)
