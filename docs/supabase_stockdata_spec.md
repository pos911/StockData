# Supabase StockData Specification

## Purpose

This document explains how to use the StockData tables stored in Supabase.

It focuses on three things:

1. Which table should be read for each use case
2. What the important columns mean
3. How to verify that the latest load succeeded

## Core Rules

### 1. Read latest data by `base_date`

Most report and analysis logic should read the latest row by `base_date`.

- Stock prices: `normalized_stock_prices_daily`
- Stock investor flows: `normalized_stock_supply_daily`
- Market and macro snapshot: `normalized_global_macro_daily`
- Market breadth: `market_breadth_daily`
- Derivatives snapshot: `normalized_derivatives_daily`
- Features: `feature_store_daily`

### 2. Active universe is defined by `stocks_master`

The active report universe is based on `stocks_master.is_active = true`.

`config/stock_universe.json` is the input file, but the pipeline syncs it into
`stocks_master` and downstream logic should use the table.

### 3. Read data by layer

- Raw: source response tracking
- Normalized: report and analysis ready tables
- Feature: derived indicators for model, signal, and report usage

## Key Tables

## 1. Universe and Master

### `stocks_master`

Use:

- Master list of tracked symbols
- Base universe for report and feature generation

Important columns:

- `symbol`: stock code
- `name`: stock name
- `market`: `KOSPI`, `KOSDAQ`, `DYNAMIC`, etc.
- `is_active`: active flag
- `updated_at`: last update timestamp

Interpretation:

- Only rows with `is_active = true` are part of the current live universe.

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

Examples:

- `KR_GOVT_10Y`
- `USDKRW`
- `DGS10`

## 2. Raw Layer

### `raw_stock_prices_daily`

Use:

- Raw price payload tracking

Important columns:

- `source`
- `symbol`
- `base_date`
- `raw_data`
- `collected_at`
- `available_at`

### `raw_stock_supply_daily`

Use:

- Raw investor flow payload tracking

Important columns:

- `source`
- `symbol`
- `base_date`
- `raw_data`

### `raw_macro_series`

Use:

- Backup raw store for FRED, Yahoo, ECOS, and other macro sources

Important columns:

- `source`
- `series_id`
- `base_date`
- `raw_data`

### `raw_ecos_macro_daily`

Use:

- Dedicated raw store for ECOS series

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

## 3. Normalized Layer

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

- `foreign_net_buy`: same day foreign net buy
- `foreign_holding_ratio`: foreign ownership ratio

### `normalized_stock_fundamentals_ratios`

Use:

- Valuation, profitability, and balance sheet ratios

Important columns:

- `symbol`
- `base_date`
- `per`
- `pbr`
- `roe`
- `debt_ratio`

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
- Main source table for report-level market data

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

- `kr10y`: now uses ECOS `KR_GOVT_10Y` daily yield as primary
- FRED `IRLTLT01KRM156N` remains backup only
- `usdkrw`: now prefers ECOS `USDKRW`

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

## 4. Feature Layer

### `feature_store_daily`

Use:

- Derived features for stock-level and global signals

Key columns:

- `symbol`
- `base_date`
- `feature_name`
- `feature_value`

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

## 5. Pipeline Logs

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

## Common Read Patterns

### Latest market snapshot

- Read the latest row from `normalized_global_macro_daily`

### Latest active stock prices

- Filter `stocks_master.is_active = true`
- Join to latest `normalized_stock_prices_daily.base_date`

### Latest active stock flows

- Filter `stocks_master.is_active = true`
- Join to latest `normalized_stock_supply_daily.base_date`

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

3. Sample stock snapshot fields

- `outstanding_shares`
- `foreign_holding_ratio`

4. Pipeline health

- Check recent `pipeline_run_logs` for `WARN` or `ERROR`

## Verification SQL

Use the file below to check the latest data load in one shot:

- `sql/verify_stockdata_status.sql`

This SQL returns one JSON report that includes:

- freshness for key tables
- latest global macro snapshot
- latest breadth and derivatives snapshot
- ECOS raw and normalized load status
- sample stock rows for 5 symbols
- active universe summary
- recent pipeline health
