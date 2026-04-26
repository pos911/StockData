# Supabase StockData Specification

## Purpose

This document is the canonical consumer-facing spec for the data loaded by
`pos911/StockData` into Supabase.

It answers three practical questions:

1. Which table should be used for each analysis or report use case
2. What the important columns mean
3. How to verify that the latest load actually succeeded

## Read Rules

### 1. Prefer latest rows by date

Most reads should use the latest row by date.

- Stock-level daily data: latest `base_date`
- Market and macro snapshot: latest `base_date`
- ECOS raw macro data: latest `date`
- Master tables: latest `updated_at`

### 2. Active stock universe is `stocks_master`

The active report universe is defined by `stocks_master.is_active = true`.

`config/stock_universe.json` is an input file only. The pipeline syncs it into
`stocks_master`, and downstream consumers should read the table.

### 3. Read by layer

- Raw: source response tracking and audit
- Normalized: report-ready structured data
- Feature: derived indicators for model, signal, or report usage

## Table Inventory

## 1. Master Tables

### `stocks_master`

Use:

- Master list of tracked symbols
- Base universe for reports and feature generation

Important columns:

- `symbol`
- `name`
- `market`
- `is_active`
- `updated_at`

### `macro_series_master`

Use:

- Metadata registry for macro series

Important columns:

- `series_id`
- `source`
- `name`
- `name_ko`
- `stat_code`
- `item_code`
- `category`
- `unit`
- `frequency`
- `is_active`
- `updated_at`

Examples:

- `KR_GOVT_10Y`
- `USDKRW`
- `DGS10`

## 2. Raw Tables

### `raw_stock_prices_daily`

Use:

- Raw price payload tracking by source, symbol, and date

Important columns:

- `source`
- `symbol`
- `base_date`
- `raw_data`
- `collected_at`
- `available_at`

### `raw_stock_supply_daily`

Use:

- Raw investor flow payload tracking by source, symbol, and date

Important columns:

- `source`
- `symbol`
- `base_date`
- `raw_data`
- `collected_at`
- `available_at`

### `raw_macro_series`

Use:

- Backup raw macro store for FRED, Yahoo, ECOS, and other macro providers

Important columns:

- `source`
- `series_id`
- `base_date`
- `raw_data`
- `collected_at`
- `available_at`

### `raw_ecos_macro_daily`

Use:

- Dedicated raw ECOS macro history

Important columns:

- `source`
- `series_id`
- `stat_code`
- `item_code`
- `item_name`
- `date`
- `time_raw`
- `value`
- `unit`
- `cycle`
- `collected_at`
- `raw`

### `raw_disclosures`

Use:

- Mixed raw event/news/disclosure store
- Holds both OpenDart disclosure payloads and Naver News payloads

Important columns:

- `source`
- `symbol`
- `base_date`
- `raw_data`
- `collected_at`
- `available_at`

Source meaning:

- `source = 'OpenDart'`: corporate disclosure raw payload
- `source = 'NaverNews'`: Naver News search result raw payload

Important clarification:

- Naver News is currently stored in `raw_disclosures`
- Naver News is not currently normalized into `normalized_stock_events_daily`
- Naver News retention is managed as a rolling 12-hour window per symbol

## 3. Normalized Tables

### `normalized_stock_prices_daily`

Use:

- Daily stock price snapshot

Important columns:

- `symbol`
- `base_date`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `trading_value`
- `market_cap`
- `outstanding_shares`
- `available_at`

Interpretation:

- `outstanding_shares`: total listed shares
- `market_cap`: market capitalization

### `normalized_stock_supply_daily`

Use:

- Daily investor flow snapshot by stock

Important columns:

- `symbol`
- `base_date`
- `individual_net_buy`
- `foreign_net_buy`
- `institutional_net_buy`
- `pension_net_buy`
- `corporate_net_buy`
- `foreign_holding_ratio`
- `available_at`

Interpretation:

- `foreign_net_buy`: same-day foreign net buy
- `foreign_holding_ratio`: foreign ownership ratio

### `normalized_stock_short_selling`

Use:

- Daily short-selling snapshot by stock

Important columns:

- `symbol`
- `base_date`
- `short_volume`
- `short_value`
- `short_ratio`
- `source`
- `available_at`

### `normalized_stock_fundamentals`

Use:

- Daily normalized financial statement values

Important columns:

- `symbol`
- `base_date`
- `revenue`
- `operating_income`
- `net_income`
- `total_assets`
- `total_liabilities`
- `total_equity`
- `source`
- `available_at`

### `normalized_stock_fundamentals_ratios`

Use:

- Daily valuation, profitability, and leverage ratios

Important columns:

- `symbol`
- `base_date`
- `per`
- `pbr`
- `roe`
- `debt_ratio`
- `source`
- `available_at`

### `normalized_stock_events_daily`

Use:

- Event-level normalized stock event table
- Currently populated from parsed OpenDart disclosures

Important columns:

- `symbol`
- `base_date`
- `event_type`
- `event_score`
- `sentiment_score`
- `available_at`

Important clarification:

- This is not currently a general news table
- It is currently closer to a normalized disclosure-event table

### `normalized_macro_series`

Use:

- Normalized macro time series store

Important columns:

- `series_id`
- `base_date`
- `value`
- `available_at`

Important series examples:

- `KR_GOVT_10Y`
- `KR_GOVT_3Y`
- `KR_CORP_AA_3Y`
- `KR_CORP_BBB_3Y`
- `USDKRW`
- `JPYKRW`
- `CNYKRW`
- `DGS10`
- `DTWEXBGS`

### `normalized_global_macro_daily`

Use:

- Daily market and macro summary snapshot
- Primary report-level market snapshot table

Important columns:

- `base_date`
- `usdkrw`
- `dxy`
- `us10y`
- `kr10y`
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

Interpretation:

- `kr10y`: uses ECOS `KR_GOVT_10Y` daily yield as primary
- FRED `IRLTLT01KRM156N` remains backup only
- `usdkrw`: prefers ECOS `USDKRW`

### `market_breadth_daily`

Use:

- Broad market strength snapshot

Important columns:

- `base_date`
- `advances`
- `declines`
- `unchanged`
- `advancing_volume`
- `declining_volume`
- `available_at`

### `normalized_derivatives_daily`

Use:

- Derivatives market summary

Important columns:

- `base_date`
- `kospi200_futures`
- `futures_basis`
- `open_interest`
- `night_futures_return`
- `expiration_flag`
- `available_at`

## 4. Feature Layer

### `feature_store_daily`

Use:

- Derived features for stock-level and global signals

Key columns:

- `symbol`
- `base_date`
- `feature_name`
- `feature_value`
- `available_at`

Stock feature examples:

- `return_5d`
- `moving_avg_5`
- `moving_avg_20`
- `volatility_20d`
- `foreign_flow_zscore`

Global feature examples:

- `KR_YIELD_SPREAD_10Y_3Y`
- `KR_CREDIT_SPREAD_AA_3Y`
- `KR_CREDIT_SPREAD_BBB_3Y`
- `USDKRW_1D_CHG_PCT`
- `KR10Y_1D_CHG_BP`
- `KR10Y_20D_CHG_BP`

## 5. Pipeline Log Table

### `pipeline_run_logs`

Use:

- Pipeline execution health tracking

Important columns:

- `job_name`
- `target_date`
- `status`
- `records_processed`
- `error_message`

Job examples:

- `daily_ecos_macro_pipeline`
- `daily_macro_pipeline`
- `daily_derivatives_pipeline`
- `daily_stock_pipeline`
- `daily_feature_generator`

## Actual Current Data Flow

### OpenDart

- Raw payload -> `raw_disclosures`
- Parsed event rows -> `normalized_stock_events_daily`

### Naver News

- Raw payload -> `raw_disclosures`
- Only articles published within the latest 12 hours are retained
- No dedicated normalized news table yet

### KIS stock data

- Price raw -> `raw_stock_prices_daily`
- Price normalized -> `normalized_stock_prices_daily`
- Supply raw -> `raw_stock_supply_daily`
- Supply normalized -> `normalized_stock_supply_daily`
- Short selling -> `normalized_stock_short_selling`
- Financial statements -> `normalized_stock_fundamentals`
- Ratios -> `normalized_stock_fundamentals_ratios`

### ECOS / macro data

- ECOS raw -> `raw_ecos_macro_daily`
- Backup raw -> `raw_macro_series`
- Normalized time series -> `normalized_macro_series`
- Daily market snapshot -> `normalized_global_macro_daily`

## Common Read Patterns

### Latest market snapshot

- Read the latest row from `normalized_global_macro_daily`

### Latest active stock prices

- Filter `stocks_master.is_active = true`
- Join to latest `normalized_stock_prices_daily.base_date`

### Latest active stock flows

- Filter `stocks_master.is_active = true`
- Join to latest `normalized_stock_supply_daily.base_date`

### Latest short-selling snapshot

- Read latest `normalized_stock_short_selling.base_date`

### Latest financial statements

- Read latest `normalized_stock_fundamentals.base_date`

### Report-level stock join

Use these tables together:

- `stocks_master`
- `normalized_stock_prices_daily`
- `normalized_stock_supply_daily`
- `normalized_stock_fundamentals_ratios`

## What To Check First

1. Freshness

- Main tables should usually have `lag_days = 0`

2. Market snapshot required fields

- `kr10y`
- `usdkrw`
- `kospi`
- `kosdaq`
- `nasdaq`
- `sp500`
- 6 market-wide investor flow fields for KOSPI and KOSDAQ

3. Stock snapshot fields

- `outstanding_shares`
- `foreign_holding_ratio`
- `market_cap`

4. Event/news raw ingestion

- `raw_disclosures` should show both `OpenDart` and `NaverNews` when available

5. Pipeline health

- Check recent `pipeline_run_logs` for `WARN` or `ERROR`

## Verification SQL

Use this file to check the latest load in one shot:

- `sql/verify_stockdata_status.sql`

This SQL should return one JSON report containing:

- freshness for all key master/raw/normalized/feature tables
- latest market and macro snapshot
- latest breadth and derivatives snapshot
- ECOS raw and normalized status
- disclosure/news raw status
- short selling / fundamentals / event table status
- sample stock rows for 5 symbols
- active universe summary
- recent pipeline health
