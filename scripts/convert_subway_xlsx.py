"""One-time converter: 2023 Subway Tables.xlsx -> two CSVs.

Run this whenever the MTA publishes a refreshed workbook. The runtime
parses the CSVs with the stdlib so we don't ship pandas/openpyxl in
production. Requires `pip install openpyxl` locally.

Usage:
    python scripts/convert_subway_xlsx.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "2023 Subway Tables.xlsx"
WEEKDAY_CSV = ROOT / "data" / "subway_weekday.csv"
ANNUAL_CSV = ROOT / "data" / "subway_annual.csv"

BORO_HEADERS = {"The Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"}

# Column indices in the workbook (header row is row 2).
COL_STATION = 0
COL_BORO = 2
COL_2023 = 8
COL_RANK_2023 = 11


def _data_rows(sheet):
    """Yield data rows, skipping title row, header row, borough headers,
    and trailing blanks. Each row is the raw tuple from openpyxl."""
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i < 2:
            continue
        station = row[COL_STATION]
        if not station or str(station).strip() == "":
            continue
        if station in BORO_HEADERS:
            continue
        yield row


def main() -> None:
    if not XLSX.exists():
        raise SystemExit(f"Workbook not found: {XLSX}")
    wb = openpyxl.load_workbook(XLSX, data_only=True, read_only=True)

    WEEKDAY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with WEEKDAY_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station", "boro", "weekday_2023", "rank_2023"])
        for row in _data_rows(wb["Avg Weekday"]):
            w.writerow([
                row[COL_STATION],
                row[COL_BORO] or "",
                row[COL_2023] if row[COL_2023] is not None else "",
                row[COL_RANK_2023] if row[COL_RANK_2023] is not None else "",
            ])

    with ANNUAL_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station", "annual_2023"])
        for row in _data_rows(wb["Annual Total"]):
            w.writerow([
                row[COL_STATION],
                row[COL_2023] if row[COL_2023] is not None else "",
            ])

    print(f"Wrote {WEEKDAY_CSV.relative_to(ROOT)}")
    print(f"Wrote {ANNUAL_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
