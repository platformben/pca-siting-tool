"""Subway station ridership from the 2023 MTA tables workbook.

The workbook's 'Avg Weekday' sheet has station name + borough + per-year
ridership. We lazy-load it and offer a nearest-station lookup once the
station is geocoded.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from ..geo import haversine_feet

DEFAULT_XLSX = Path(__file__).parents[2] / "2023 Subway Tables.xlsx"
MTA_STATIONS_URL = "https://data.ny.gov/resource/39hk-dx4f.json"


@dataclass
class StationRidership:
    station: str
    borough: str
    weekday_2023: float | None
    annual_2023: float | None
    rank_2023: int | None


@lru_cache(maxsize=1)
def load_weekday() -> pd.DataFrame:
    path = Path(os.getenv("SUBWAY_XLSX", DEFAULT_XLSX))
    df = pd.read_excel(path, sheet_name="Avg Weekday", header=1)
    df = df.rename(columns={
        df.columns[0]: "station",
        df.columns[1]: "_",
        df.columns[2]: "boro",
        df.columns[-4]: "weekday_2023",
        df.columns[-1]: "rank_2023",
    })
    df = df.dropna(subset=["station"]).copy()
    boro_headers = {"The Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"}
    df = df[~df["station"].isin(boro_headers)]
    df = df[df["station"].astype(str).str.strip() != ""]
    return df[["station", "boro", "weekday_2023", "rank_2023"]].reset_index(drop=True)


@lru_cache(maxsize=1)
def load_annual() -> pd.DataFrame:
    path = Path(os.getenv("SUBWAY_XLSX", DEFAULT_XLSX))
    df = pd.read_excel(path, sheet_name="Annual Total", header=1)
    df = df.rename(columns={df.columns[0]: "station", df.columns[-4]: "annual_2023"})
    df = df.dropna(subset=["station"]).copy()
    boro_headers = {"The Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"}
    df = df[~df["station"].isin(boro_headers)]
    return df[["station", "annual_2023"]].reset_index(drop=True)


def _strip_lines(name: str) -> str:
    return re.sub(r"\s*\([^)]+\)\s*$", "", name).strip()


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
def load_mta_stations() -> pd.DataFrame:
    try:
        r = requests.get(MTA_STATIONS_URL, params={"$limit": "5000"}, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return pd.DataFrame()
    rows = r.json()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["lat"] = pd.to_numeric(df["gtfs_latitude"], errors="coerce")
    df["lon"] = pd.to_numeric(df["gtfs_longitude"], errors="coerce")
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def nearest_stations(lat: float, lon: float, n: int = 3) -> list[NearestStation]:
    stations = load_mta_stations()
    if stations.empty:
        return []
    dists = [
        (i, haversine_feet(lat, lon, row.lat, row.lon))
        for i, row in stations.iterrows()
    ]
    dists.sort(key=lambda x: x[1])
    out = []
    seen: set[str] = set()
    for i, d in dists:
        row = stations.iloc[i]
        key = row["stop_name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            NearestStation(
                stop_name=row["stop_name"],
                borough=row.get("borough", ""),
                routes=row.get("daytime_routes", ""),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                distance_ft=d,
                ridership=lookup_station(row["stop_name"]),
            )
        )
        if len(out) >= n:
            break
    return out


def lookup_station(name: str) -> StationRidership | None:
    weekday = load_weekday()
    annual = load_annual()
    needle = _strip_lines(name).lower()
    for _, row in weekday.iterrows():
        if _strip_lines(str(row["station"])).lower() == needle:
            ann = annual[annual["station"] == row["station"]]
            annual_val = float(ann["annual_2023"].iloc[0]) if not ann.empty else None
            try:
                wd = float(row["weekday_2023"])
            except (TypeError, ValueError):
                wd = None
            try:
                rk = int(row["rank_2023"])
            except (TypeError, ValueError):
                rk = None
            return StationRidership(
                station=str(row["station"]),
                borough=str(row["boro"]),
                weekday_2023=wd,
                annual_2023=annual_val,
                rank_2023=rk,
            )
    return None
