# StockData Ingestion Spec

## Scope

This document describes the data that is currently ingested and stored for the daily report path as of 2026-04-24.

The report-critical data is now available in these areas:

- KOSPI / KOSDAQ index level and daily change
- KOSPI / KOSDAQ market-wide investor flow
- NASDAQ / S&P500 index level and daily change
- Individual stock daily prices
- Individual stock investor flow
- Market breadth
- Macro series and feature store

## Daily Workflow Order

The daily sync workflow runs in this order:

1. `scripts/apply_schema_migrations.py`
2. `python -m src.pipelines.collect_ecos_macro --all`
3. `src/jobs/run_daily_macro_pipeline.py`
4. `src/jobs/run_daily_derivatives_pipeline.py`
5. `src/jobs/run_daily_stock_pipeline.py`
6. `src/jobs/run_daily_feature_pipeline.py`
7. `scripts/verify_data.py`

## Report-Critical Tables

### `normalized_global_macro_daily`

This is the main market snapshot table used by the report.

Primary write path:

- `src/jobs/run_daily_macro_pipeline.py`
- `src/collectors/global_index_collector.py`

Sources:

- `Yahoo Finance`
  - `KRW=X` -> `usdkrw`
  - `DX-Y.NYB` -> `dxy`
  - `^TNX` -> `us10y`
  - `^IXIC` -> `nasdaq`
  - `^GSPC` -> `sp500`
  - `^SOX` -> `sox`
  - `^VIX` -> `vix`
  - `CL=F` -> `wti`
  - `BZ=F` -> `brent`
  - `GC=F` -> `gold`
  - `HG=F` -> `copper`
  - `BDRY` -> `bdry`
- `KIS Open API`
  - `/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market`
  - TR ID: `FHPTJ04040000`
  - Used for:
    - `kospi`
    - `kospi_change_rate`
    - `kospi_individual_net_buy`
    - `kospi_foreign_net_buy`
    - `kospi_institutional_net_buy`
    - `kosdaq`
    - `kosdaq_change_rate`
    - `kosdaq_individual_net_buy`
    - `kosdaq_foreign_net_buy`
    - `kosdaq_institutional_net_buy`
- `FRED`
  - `BAMLH0A0HYM2` -> `hy_spread`
- `ECOS`
  - `KR_GOVT_10Y` -> `kr10y` (preferred)
  - `USDKRW` -> `usdkrw` (preferred)
- `FRED`
  - `IRLTLT01KRM156N` -> `kr10y` backup only if ECOS is unavailable

Current columns used by reports:

- `base_date`
- `usdkrw`
- `dxy`
- `us10y`
- `kospi`
- `kospi_change_rate`
- `kosdaq`
- `kosdaq_change_rate`
- `nasdaq`
- `nasdaq_change_rate`
- `sp500`
- `sp500_change_rate`
- `sox`
- `vix`
- `wti`
- `brent`
- `gold`
- `copper`
- `bdry`
- `hy_spread`
- `kospi_individual_net_buy`
- `kospi_foreign_net_buy`
- `kospi_institutional_net_buy`
- `kosdaq_individual_net_buy`
- `kosdaq_foreign_net_buy`
- `kosdaq_institutional_net_buy`
- `available_at`

Current behavior:

- `kr10y` prefers ECOS daily series `KR_GOVT_10Y`
- FRED monthly `IRLTLT01KRM156N` remains as fallback only

### `market_breadth_daily`

This table stores daily breadth used for quality checks and market diagnostics.

Primary write path:

- `python -m src.pipelines.collect_ecos_macro --all`
- `src/jobs/run_daily_macro_pipeline.py` for downstream consumption

Current source priority:

1. `normalized_stock_prices_daily` based breadth calculation
2. `KRXCollector.fetch_market_breadth()` fallback only if price-based calculation fails

Stored columns:

- `base_date`
- `advances`
- `declines`
- `unchanged`
- `advancing_volume`
- `declining_volume`
- `available_at`

### `normalized_stock_prices_daily`

Primary write path:

- `src/jobs/run_daily_stock_pipeline.py`

Sources:

- `KIS Open API`
  - `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`
  - TR ID: `FHKST03010100`
- `FinanceDataReader` fallback

Stored columns used by reports:

- `symbol`
- `base_date`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `trading_value`
- `market_cap`

Additional populated fields:

- `outstanding_shares`

### `normalized_stock_supply_daily`

Primary write path:

- `src/jobs/run_daily_stock_pipeline.py`
- `src/collectors/kis/domestic.py`

Source:

- `KIS Open API`
  - `/uapi/domestic-stock/v1/quotations/inquire-investor`
  - TR ID: `FHKST01010900`

Stored columns used by reports:

- `symbol`
- `base_date`
- `individual_net_buy`
- `foreign_net_buy`
- `institutional_net_buy`
- `pension_net_buy`
- `corporate_net_buy`

Additional populated fields:

- `foreign_holding_ratio`

### `normalized_stock_fundamentals_ratios`

Source:

- `KIS Open API`
  - `stability-ratio`
  - `financial-ratio`
  - `profit-ratio`
  - `growth-ratio`

Stored columns commonly used:

- `symbol`
- `base_date`
- `per`
- `pbr`
- `roe`
- `debt_ratio`

### `normalized_derivatives_daily`

Primary write path:

- `src/jobs/run_daily_derivatives_pipeline.py`

Sources:

- `Yahoo Finance`
- `pykrx` fallback

Stored columns:

- `base_date`
- `kospi200_futures`
- `futures_basis`
- `open_interest`
- `night_futures_return`
- `expiration_flag`

### `normalized_macro_series`

Primary write path:

- `src/jobs/run_daily_macro_pipeline.py`

Sources:

- `FRED`
- selected `Yahoo Finance` macro tickers
- `ECOS`
- optional `TradingEconomics` path if configured

Stored columns:

- `series_id`
- `base_date`
- `value`
- `available_at`

### `feature_store_daily`

Primary write path:

- `src/jobs/run_daily_feature_pipeline.py`
- `src/features/generate_features.py`

Input tables:

- `normalized_stock_prices_daily`
- `normalized_stock_supply_daily`
- `normalized_global_macro_daily`
- `normalized_macro_series`

Stored key columns:

- `symbol`
- `base_date`
- `feature_name`
- `feature_value`

## Raw Tables

The raw tables are used for traceability and debugging.

- `raw_stock_prices_daily`
- `raw_stock_supply_daily`
- `raw_macro_series`
- `raw_ecos_macro_daily`
- `raw_disclosures`

## Report Retrieval Reference

### Global report snapshot

Read from:

- `normalized_global_macro_daily`

Columns:

- `kospi`, `kospi_change_rate`
- `kosdaq`, `kosdaq_change_rate`
- `nasdaq`, `nasdaq_change_rate`
- `sp500`, `sp500_change_rate`
- `kospi_individual_net_buy`, `kospi_foreign_net_buy`, `kospi_institutional_net_buy`
- `kosdaq_individual_net_buy`, `kosdaq_foreign_net_buy`, `kosdaq_institutional_net_buy`

### Individual stock snapshot

Price table:

- `normalized_stock_prices_daily`

Supply table:

- `normalized_stock_supply_daily`

Ratio table:

- `normalized_stock_fundamentals_ratios`

## Current Status Summary

Working now:

- KOSPI / KOSDAQ market-wide investor flow
- KOSPI / KOSDAQ index level and daily change
- NASDAQ / S&P500 level and daily change
- Daily stock price ingestion
- Daily stock investor flow ingestion
- Market breadth ingestion
- Feature store generation

Special handling:

- `normalized_global_macro_daily.kr10y` uses ECOS daily `KR_GOVT_10Y` first and falls back to FRED monthly only when necessary.

## Verification SQL

Use this file for one-shot validation:

- `sql/verify_api_ingestion_test_page.sql`
