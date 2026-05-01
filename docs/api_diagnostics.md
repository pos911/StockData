# API Diagnostics

Use this guide when StockData has missing or stale source data. Diagnostics must
confirm whether the upstream API has a value before code treats the value as
missing.

## KIS Short-Selling

Run both KOSPI and KOSDAQ market-code checks through `auto`:

```bash
python scripts/diagnose_api_sources.py --source kis_short_selling --symbol 005930 --date 20260429 --market auto
python scripts/diagnose_api_sources.py --source kis_short_selling --symbol 000660 --date 20260429 --market auto
python scripts/diagnose_api_sources.py --source kis_short_selling --symbol 278470 --date 20260429 --market auto
python scripts/diagnose_api_sources.py --source kis_short_selling --symbol 058470 --date 20260429 --market auto
```

The output includes:

- endpoint and TR ID
- request params
- top-level response keys
- `output1` keys
- `output2` or `output` row count
- first-row keys and a redacted sample
- detected date fields
- detected numeric short-selling fields

If KIS has no rows:

- check whether the other market code has rows
- check whether the date is a non-business day or pre-publication date
- check whether KIS entitlement or endpoint support is missing
- check PYKRX/KRX fallback availability

If KIS has rows but no date field:

- inspect `output1` date fields
- use request-date fallback only when the request was for exactly one target date
- never upsert `base_date = ''`

## KIS Price And Investor Flow

```bash
python scripts/diagnose_api_sources.py --source kis_ohlcv --symbol 005930 --date 20260429 --market auto
python scripts/diagnose_api_sources.py --source kis_investor --symbol 058470 --date 20260429 --market auto
```

## KIS Market Rankings

Market ranking payloads are stored in `raw_market_rankings` and normalized into
`normalized_market_rankings_daily`.

```bash
python scripts/diagnose_api_sources.py --source kis_volume_rank --market J
python scripts/diagnose_api_sources.py --source kis_volume_rank --market Q
python scripts/diagnose_api_sources.py --source kis_volume_rank --market T
```

Check:

- `row_count`
- `first_row_keys`
- first row sample fields such as `mksc_shrn_iscd`, `hts_kor_isnm`, `acml_vol`, `acml_tr_pbmn`

If `--market T` returns fewer than 20 ETF rows, StockData falls back to ETF rows
from the latest valid price table when possible.

## ECOS

Check today, then recent fallback windows:

```bash
python scripts/diagnose_api_sources.py --source ecos --series KR_GOVT_10Y
python scripts/diagnose_api_sources.py --source ecos --series KR_GOVT_3Y
python scripts/diagnose_api_sources.py --source ecos --series USDKRW
python scripts/diagnose_api_sources.py --source ecos --series KR_CD_91D
```

Interpretation:

- today has no row but recent 14 days has rows: publication delay or holiday,
  report freshness may be `SUCCESS_WITH_STALE_DATA` or `WARN`
- recent 30 days has no rows: likely `stat_code` / `item_code` issue, API
  permission issue, or ECOS response format change

## Required Follow-up

When a diagnostic finds upstream rows:

- add or update parser field mappings
- keep the source row in a raw table
- normalize and upsert validated rows

When a diagnostic finds no upstream rows:

- log endpoint, TR ID, params, symbol, date, and market code
- classify the absence: non-business day, publication delay, market-code
  mismatch, entitlement issue, or unsupported endpoint
