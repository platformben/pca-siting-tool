"""Subway station ridership from the 2023 MTA tables.

The xlsx workbook ships in the repo for reference; the runtime reads
two pre-extracted CSVs in ``data/`` produced by
``scripts/convert_subway_xlsx.py``. We don't load pandas/openpyxl on
the request path — Render's free tier doesn't have the headroom.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import requests

from ..geo import haversine_feet

DATA_DIR = Path(__file__).parents[2] / "data"
DEFAULT_WEEKDAY = DATA_DIR / "subway_weekday.csv"
DEFAULT_ANNUAL = DATA_DIR / "subway_annual.csv"

# NY State Open Data deprecated 39hk-dx4f (the dataset went down to 1 row,
# which made every NYC address falsely match "137 St-City College" in
# Manhattan). 5f5g-n3cz ("MTA Subway Stations and Complexes") replaces it
# with 445 station+complex records and uses plain latitude/longitude.
# Override via env var if NYS rotates the dataset ID again.
MTA_STATIONS_URL = os.getenv(
    "MTA_STATIONS_URL",
    "https://data.ny.gov/resource/5f5g-n3cz.json",
)


@dataclass
class StationRidership:
    station: str
    borough: str
    weekday_2023: float | None
    annual_2023: float | None
    rank_2023: int | None


def _parse_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_int(s: str) -> int | None:
    f = _parse_float(s)
    return int(f) if f is not None else None


@lru_cache(maxsize=1)
def _weekday_index() -> dict[str, dict]:
    """station name -> {boro, weekday_2023, rank_2023}."""
    path = Path(os.getenv("SUBWAY_WEEKDAY_CSV", DEFAULT_WEEKDAY))
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            station = (row.get("station") or "").strip()
            if not station:
                continue
            out[station] = {
                "boro": (row.get("boro") or "").strip(),
                "weekday_2023": _parse_float(row.get("weekday_2023") or ""),
                "rank_2023": _parse_int(row.get("rank_2023") or ""),
            }
    return out


@lru_cache(maxsize=1)
def _annual_index() -> dict[str, float]:
    """station name -> annual_2023 ridership."""
    path = Path(os.getenv("SUBWAY_ANNUAL_CSV", DEFAULT_ANNUAL))
    out: dict[str, float] = {}
    if not path.exists():
        return out
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            station = (row.get("station") or "").strip()
            if not station:
                continue
            v = _parse_float(row.get("annual_2023") or "")
            if v is not None:
                out[station] = v
    return out


def _strip_lines(name: str) -> str:
    """Strip the trailing line list, e.g. '138 St-Grand Concourse (4,5)' -> '138 St-Grand Concourse'."""
    import re
    return re.sub(r"\s*\([^)]+\)\s*$", "", name).strip()


@lru_cache(maxsize=1)
def _weekday_by_stripped() -> dict[str, str]:
    """Lowercased line-stripped name -> the original station key in the weekday index."""
    return {_strip_lines(k).lower(): k for k in _weekday_index()}


@dataclass
class NearestStation:
    stop_name: str
    borough: str
    routes: str
    lat: float
    lon: float
    distance_ft: float
    ridership: StationRidership | None


@lru_cache(maxsize=1)
def load_mta_stations() -> list[dict]:
    """Pull MTA station list from the NY Open Data SODA API.

    Returns a list of dicts. Each dict has ``stop_name``, ``borough``,
    ``daytime_routes``, ``lat``, ``lon``. Tolerates schema variants:
    the old `39hk-dx4f` dataset used `gtfs_latitude`/`gtfs_longitude`
    while the replacement `5f5g-n3cz` uses `latitude`/`longitude`. We
    try both so a future schema flip doesn't silently break the lookup
    (which previously caused every Brooklyn address to match a Harlem
    station).
    """
    try:
        r = requests.get(MTA_STATIONS_URL, params={"$limit": "5000"}, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        # Prefer plain lat/lon (5f5g-n3cz), fall back to gtfs_* (legacy).
        lat = _parse_float(row.get("latitude") or row.get("gtfs_latitude"))
        lon = _parse_float(row.get("longitude") or row.get("gtfs_longitude"))
        if lat is None or lon is None:
            continue
        out.append({
            "stop_name": row.get("stop_name") or row.get("display_name") or "",
            "borough": row.get("borough") or "",
            "daytime_routes": row.get("daytime_routes") or "",
            "lat": lat,
            "lon": lon,
        })
    return out


def nearest_stations(lat: float, lon: float, n: int = 3) -> list[NearestStation]:
    stations = load_mta_stations()
    if not stations:
        return []
    ranked = sorted(
        ((s, haversine_feet(lat, lon, s["lat"], s["lon"])) for s in stations),
        key=lambda x: x[1],
    )
    out: list[NearestStation] = []
    seen: set[str] = set()
    for s, d in ranked:
        key = s["stop_name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            NearestStation(
                stop_name=s["stop_name"],
                borough=s["borough"],
                routes=s["daytime_routes"],
                lat=s["lat"],
                lon=s["lon"],
                distance_ft=d,
                ridership=lookup_station(s["stop_name"]),
            )
        )
        if len(out) >= n:
            break
    return out


def lookup_station(name: str) -> StationRidership | None:
    weekday = _weekday_index()
    annual = _annual_index()
    needle = _strip_lines(name).lower()
    station_key = _weekday_by_stripped().get(needle)
    if not station_key:
        return None
    rec = weekday[station_key]
    return StationRidership(
        station=station_key,
        borough=rec["boro"],
        weekday_2023=rec["weekday_2023"],
        annual_2023=annual.get(station_key),
        rank_2023=rec["rank_2023"],
    )
