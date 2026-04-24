# PCA Siting Tool

Evaluate a candidate dispensary address against New York OCM siting rules and pull a basic commercial snapshot. Phase 1 = single-address evaluator. Roadmap: listing-alert feed (Phase 2), multi-state rulesets (Phase 3).

## What it checks

**OCM compliance gates** (all sourced from public data, no keys needed):

| Rule | Source | Output |
|---|---|---|
| Dispensary proximity (1,000 / 2,000 ft) | OCM ArcGIS `ActiveLicensesV1` + `PendingLicensesV1` | PASS / FAIL with nearest competitor |
| School proximity (500 ft + same street, pre-K – HS) | NYC DCP Facilities DB (`ji82-xba5`) + OpenStreetMap | PASS / FAIL / REVIEW with same-street flag |
| House of worship (200 ft, exclusive use) | OpenStreetMap with mixed-use tag heuristics | PASS / FAIL / REVIEW with exclusive-use hint |
| Certificate of Occupancy | NYC DOB C of O (`bs8b-p36w`) by BBL | Retail/commercial keyword scan |

**Commercial snapshot:**
- Nearest 3 subway stations + 2023 ridership (from `2023 Subway Tables.xlsx`)
- Census tract MHHI + population (ACS 5-year 2022)

**Not yet automated:** co-tenants, foot traffic, coffee-price proxy, corner-lot auto-detection, density. These need a Google Maps API key (Places + Geocoding) and NYC PLUTO parcel shapes.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in optional keys
streamlit run app.py
```

Open http://localhost:8501 and paste an address.

### API keys (all optional)

- `GOOGLE_MAPS_API_KEY` — unlocks Places lookups for co-tenants / coffee / private-school backup. Free tier is ~10k calls/month per SKU.
- `CENSUS_API_KEY` — unlocks higher ACS rate limits ([signup](https://api.census.gov/data/key_signup.html)).
- `NYC_APP_TOKEN` — lifts Socrata rate limits on DOB / Facilities DB queries.

## Regulatory nuances the tool can't decide for you

These trigger a REVIEW status and require your eyes:

- **Corner-lot schools** — a school on a corner lot is "on both streets." The tool flags any school <500 ft on a different street so you can verify corner status.
- **Exclusive-use churches** — a ground-floor church with apartments above is NOT exclusively used and does NOT trigger the 200 ft rule. The tool uses OSM tags (`building:levels`, `building=apartments`, etc.) as a hint but can be wrong in both directions.
- **Parks next to DOE-operated schools** — flagged as adjacent in the notes, but you still need to confirm DOE operation.
- **OCM map gaps** — the tool always nudges you to the Google Maps cross-check link for pre-K / private school / small-congregation cases.

## Layout

```
app.py                       Streamlit UI
siting/
  evaluator.py               Orchestrates all checks for one address
  geo.py                     Distance + street-name normalization
  rules/
    ny.py                    NY OCM rules (future: nj.py, ma.py, ...)
  sources/
    geocode.py               NYC Geosearch + Census fallback
    ocm.py                   OCM ArcGIS feature service client
    nyc_opendata.py          NYC DCP Facilities DB + DOB C of O
    osm.py                   Overpass for schools / worship / parks
    subway.py                MTA station locations + 2023 ridership xlsx
    census_acs.py            MHHI + population by tract
    population.py            >20k threshold for OCM buffer rule
2023 Subway Tables.xlsx      MTA ridership (not committed separately)
```

## Adding another state

Create `siting/rules/<state>.py` with the same `Finding` shape and `check_*` functions, then dispatch from `evaluator.py` based on the geocode result's `state`. Data sources can be mixed: OSM and Census work nationwide; state-specific regulator feeds (NJ CRC, MA CCC, etc.) go into new `siting/sources/` modules.
