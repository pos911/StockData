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

### 2. Full market coverage and active report universe are different

The daily stock pipeline now has two stock scopes:

- Full KOSPI/KOSDAQ price coverage: sourced from KRX KIND listed-company
  universe and loaded into `stocks_master` plus `normalized_stock_prices_daily`
- Active report / detailed universe: symbols with `stocks_master.is_active = true`
  and the static watchlist from `static_stock_universe`

Important read rule:

- Use `stocks_master.market IN ('KOSPI', 'KOSDAQ')` to check full market coverage
- Use `stocks_master.is_active = true` only when the report intentionally wants
  the curated active universe
- Do not use `is_active = true` to validate full market price coverage

### 3. Configured watchlist and active universe are different

The configured watchlist is stored in `static_stock_universe`.

The broader active pipeline universe is stored in `stocks_master` with
`is_active = true`.

`config/stock_universe.json` is a source input file, but it is also synchronized
into `static_stock_universe` as a separate traceable table.

Read rules:

- Use `static_stock_universe` to inspect the exact configured watchlist
- Use `stocks_master` to inspect the broader active report / pipeline universe

### 4. Read by layer

- Raw: source response tracking and audit
- Normalized: report-ready structured data
- Feature: derived indicators for model, signal, or report usage

## Current Automation Contract

`StockData` is a data-ingestion repository. It should not publish stock reports.

The active GitHub Actions workflow is:

- `.github/workflows/daily_sync.yml`
- Schedule: hourly, `0 * * * *`
- Manual run: `workflow_dispatch`
- Execution order:
  1. schema migration best effort
  2. ECOS macro collection
  3. global macro pipeline
  4. derivatives pipeline
  5. stock pipeline
  6. feature pipeline
  7. verification script

There is intentionally no report-generation workflow in this repository.

## Report Core Tables

The daily report should primarily read these tables.

### 1. `normalized_global_macro_daily`

Use for:

- KOSPI / KOSDAQ / NASDAQ / S&P500 level snapshots
- market-wide investor flows
- FX and yield summary

Key report columns:

- `base_date`: report market date
- `kospi`, `kosdaq`, `nasdaq`, `sp500`: latest index levels
- `kospi_change_rate`, `kosdaq_change_rate`, `nasdaq_change_rate`, `sp500_change_rate`: daily return in percent
- `usdkrw`: KRW per USD exchange rate
- `us10y`: US 10Y Treasury yield
- `kr10y`: Korea 10Y government bond yield, sourced from ECOS `KR_GOVT_10Y`
- `kospi_individual_net_buy`, `kospi_foreign_net_buy`, `kospi_institutional_net_buy`: KOSPI market-wide net flows
- `kosdaq_individual_net_buy`, `kosdaq_foreign_net_buy`, `kosdaq_institutional_net_buy`: KOSDAQ market-wide net flows

### 2. `normalized_stock_prices_daily`

Use for:

- stock close, volume, value, market cap snapshot

Key report columns:

- `symbol`
- `base_date`
- `close_price`
- `volume`
- `trading_value`
- `market_cap`
- `outstanding_shares`

### 3. `normalized_stock_supply_daily`

Use for:

- stock-level investor flow summary

Key report columns:

- `symbol`
- `base_date`
- `individual_net_buy`
- `foreign_net_buy`
- `institutional_net_buy`
- `foreign_holding_ratio`

### 4. `normalized_stock_fundamentals_ratios`

Use for:

- valuation and quality ratios in stock summaries

Key report columns:

- `symbol`
- `base_date`
- `per`
- `pbr`
- `roe`
- `debt_ratio`

### 5. `market_breadth_daily`

Use for:

- advance / decline market breadth summary

Key report columns:

- `base_date`
- `advances`
- `declines`
- `unchanged`
- `advancing_volume`
- `declining_volume`

### 6. `normalized_stock_events_daily`

Use for:

- disclosure-derived stock event summary

Key report columns:

- `symbol`
- `base_date`
- `event_type`
- `event_score`
- `sentiment_score`

## Table Inventory

## 1. Master Tables

### `static_stock_universe`

Use:

- Exact synchronized copy of `config/stock_universe.json`
- Audit table for configured watchlist additions and removals
- Primary table to confirm whether a symbol is explicitly configured by file

Important columns:

- `symbol`
- `name`
- `market`
- `enabled`
- `source_file`
- `updated_at`

### `stocks_master`

Use:

- Master list of tracked symbols
- Base universe for reports and feature generation
- Broader active universe that may include dynamic discoveries

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

### `raw_stock_short_selling`

Use:

- Raw short-selling payload tracking by source, symbol, and date
- Diagnostic store when KIS short-selling rows are empty, delayed, or missing expected fields

Important columns:

- `source`: `KIS`, `PYKRX`, or another fallback source
- `symbol`
- `base_date`
- `raw_data`: sanitized API payload/row context
- `collected_at`
- `available_at`

Important clarification:

- This table is an audit/diagnostic layer. Report consumers should read report-ready values from `normalized_stock_short_selling`.
- Rows with missing `symbol` or `base_date` are blocked before DB upsert.

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

- OpenDart disclosures are actively ingested
- Naver News ingestion is currently disabled by configuration
- If Naver News is re-enabled later, it is stored only in `raw_disclosures`
- Naver News is not currently normalized into `normalized_stock_events_daily`

## 3. Normalized Tables

### `normalized_stock_prices_daily`

Use:

- Daily stock price snapshot
- Primary table for full KOSPI/KOSDAQ price coverage verification
- Row grain: one row per `symbol`, `base_date`

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

Coverage expectation:

- For a completed full-market run, the latest `base_date` should usually have
  more than 2,000 distinct KOSPI/KOSDAQ symbols when joined to
  `stocks_master.market IN ('KOSPI', 'KOSDAQ')`
- If the count is much lower, the stock pipeline was probably run with
  `--limit`, interrupted, or rate-limited before completion

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
- `daily_stock_full_price_pipeline`
- `daily_feature_generator`

## Actual Current Data Flow

### OpenDart

- Raw payload -> `raw_disclosures`
- Parsed event rows -> `normalized_stock_events_daily`

### Naver News

- Ingestion is currently disabled by configuration
- If re-enabled later, raw payloads go to `raw_disclosures`
- No dedicated normalized news table yet

### KIS stock data

- Full KOSPI/KOSDAQ listed universe -> `stocks_master`
- Price raw -> `raw_stock_prices_daily`
- Price normalized -> `normalized_stock_prices_daily`
- Supply raw -> `raw_stock_supply_daily`
- Supply normalized -> `normalized_stock_supply_daily`
- Short-selling raw/diagnostic -> `raw_stock_short_selling`
- Short-selling normalized -> `normalized_stock_short_selling`
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

### Latest full KOSPI/KOSDAQ market price coverage

- Filter `stocks_master.market IN ('KOSPI', 'KOSDAQ')`
- Join to latest `normalized_stock_prices_daily.base_date`
- Expect `COUNT(DISTINCT normalized_stock_prices_daily.symbol) > 2000`

### Latest configured static watchlist

- Filter `static_stock_universe.enabled = true`
- Join to latest stock-level tables by `symbol`

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

4. Static watchlist sync

- `static_stock_universe` should match `config/stock_universe.json`
- Added symbols should appear there with `enabled = true`
- Removed symbols should no longer be present there
- `stocks_master` may contain more symbols than the static watchlist

5. Event/news raw ingestion

- `raw_disclosures` should at minimum show `OpenDart`
- `NaverNews` appears only if the news toggle is enabled again

6. Pipeline health

- Check recent `pipeline_run_logs` for `WARN` or `ERROR`

7. Full market stock price coverage

- `stocks_master` should contain the full KOSPI/KOSDAQ universe
- Latest `normalized_stock_prices_daily` should cover more than 2,000 symbols
- `daily_stock_full_price_pipeline.records_processed` should be more than 2,000
  on a completed full run

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

## Consumer Verification SQL

Run this in Supabase SQL Editor after a full `daily_sync` run. The query returns
one JSON object. A report repository should use it to separate two decisions:

1. Whether a report can be generated at all
2. How much confidence to assign to full-market analysis

Readiness levels:

- `minimum_report_ready = true`: enough data exists to generate a watchlist /
  curated-universe report
- `full_market_coverage_pass = true`: latest stock prices cover enough
  KOSPI/KOSDAQ symbols to describe full-market breadth, rankings, and market-wide
  stock coverage confidently

Important:

- `full_market_coverage_pass = false` is not automatically a report-blocking
  error
- It means the report should label market-wide stock conclusions as partial or
  unavailable
- A completed full-market stock run should usually show
  `daily_stock_full_price_pipeline.records_processed > 2000`

```sql
WITH latest_price AS (
    SELECT MAX(base_date) AS base_date
    FROM normalized_stock_prices_daily
),
market_universe AS (
    SELECT
        symbol,
        name,
        market,
        is_active
    FROM stocks_master
    WHERE market IN ('KOSPI', 'KOSDAQ')
),
price_coverage AS (
    SELECT
        lp.base_date,
        COUNT(DISTINCT p.symbol) AS covered_symbols,
        COUNT(DISTINCT p.symbol) FILTER (WHERE mu.market = 'KOSPI') AS kospi_covered,
        COUNT(DISTINCT p.symbol) FILTER (WHERE mu.market = 'KOSDAQ') AS kosdaq_covered,
        COUNT(*) FILTER (WHERE p.close_price IS NULL) AS null_close_rows
    FROM latest_price lp
    LEFT JOIN normalized_stock_prices_daily p
        ON p.base_date = lp.base_date
    LEFT JOIN market_universe mu
        ON mu.symbol = p.symbol
    WHERE mu.symbol IS NOT NULL
    GROUP BY lp.base_date
),
static_watchlist AS (
    SELECT
        symbol,
        name,
        market
    FROM static_stock_universe
    WHERE enabled = TRUE
),
watchlist_price_coverage AS (
    SELECT
        lp.base_date,
        COUNT(sw.symbol) AS watchlist_symbols,
        COUNT(p.symbol) AS watchlist_symbols_with_price
    FROM latest_price lp
    CROSS JOIN static_watchlist sw
    LEFT JOIN normalized_stock_prices_daily p
        ON p.symbol = sw.symbol
       AND p.base_date = lp.base_date
    GROUP BY lp.base_date
),
universe_summary AS (
    SELECT
        COUNT(*) AS total_market_universe,
        COUNT(*) FILTER (WHERE market = 'KOSPI') AS kospi_universe,
        COUNT(*) FILTER (WHERE market = 'KOSDAQ') AS kosdaq_universe,
        COUNT(*) FILTER (WHERE is_active = TRUE) AS active_symbols
    FROM market_universe
),
pipeline_status AS (
    SELECT jsonb_agg(
        jsonb_build_object(
            'job_name', job_name,
            'target_date', target_date,
            'status', status,
            'records_processed', records_processed,
            'error_message', error_message
        )
        ORDER BY target_date DESC
    ) AS recent_logs
    FROM (
        SELECT *
        FROM pipeline_run_logs
        WHERE job_name IN ('daily_stock_pipeline', 'daily_stock_full_price_pipeline')
        ORDER BY target_date DESC
        LIMIT 10
    ) x
),
coverage_gaps AS (
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.symbol) AS rows
    FROM (
        SELECT
            mu.symbol,
            mu.name,
            mu.market
        FROM market_universe mu
        LEFT JOIN normalized_stock_prices_daily p
            ON p.symbol = mu.symbol
           AND p.base_date = (SELECT base_date FROM latest_price)
        WHERE p.symbol IS NULL
        ORDER BY mu.symbol
        LIMIT 30
    ) x
),
sample_prices AS (
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.symbol) AS rows
    FROM (
        SELECT
            p.symbol,
            mu.name,
            mu.market,
            p.base_date,
            p.close_price,
            p.volume,
            p.trading_value,
            p.market_cap,
            p.outstanding_shares
        FROM normalized_stock_prices_daily p
        JOIN market_universe mu
            ON mu.symbol = p.symbol
        WHERE p.base_date = (SELECT base_date FROM latest_price)
        ORDER BY p.symbol
        LIMIT 20
    ) x
)
SELECT jsonb_pretty(
    jsonb_build_object(
        'latest_price_date', (SELECT base_date FROM latest_price),
        'minimum_report_ready',
            (SELECT base_date FROM latest_price) IS NOT NULL
            AND COALESCE((SELECT watchlist_symbols FROM watchlist_price_coverage), 0) > 0
            AND COALESCE((SELECT watchlist_symbols_with_price FROM watchlist_price_coverage), 0) > 0,
        'full_market_coverage_pass', COALESCE((SELECT covered_symbols FROM price_coverage), 0) > 2000,
        'price_coverage', (SELECT to_jsonb(price_coverage) FROM price_coverage),
        'watchlist_price_coverage', (SELECT to_jsonb(watchlist_price_coverage) FROM watchlist_price_coverage),
        'universe_summary', (SELECT to_jsonb(universe_summary) FROM universe_summary),
        'recent_pipeline_logs', COALESCE((SELECT recent_logs FROM pipeline_status), '[]'::jsonb),
        'sample_missing_price_symbols', COALESCE((SELECT rows FROM coverage_gaps), '[]'::jsonb),
        'sample_latest_prices', COALESCE((SELECT rows FROM sample_prices), '[]'::jsonb)
    )
) AS verification_report;
```

## Minimal Report Repository Read Model

A separate report repository should usually read:

1. Market snapshot:
   `normalized_global_macro_daily`, latest `base_date`
2. Full price coverage:
   `normalized_stock_prices_daily` joined to `stocks_master`
3. Curated watchlist:
   `static_stock_universe` joined to latest stock tables
4. Stock-level flows:
   `normalized_stock_supply_daily`
5. Valuation ratios:
   `normalized_stock_fundamentals_ratios`
6. Market breadth:
   `market_breadth_daily`
7. Derivatives:
   `normalized_derivatives_daily`
8. Features:
   `feature_store_daily`

Example stock snapshot join:

```sql
WITH latest_price AS (
    SELECT MAX(base_date) AS base_date
    FROM normalized_stock_prices_daily
)
SELECT
    m.symbol,
    m.name,
    m.market,
    p.base_date,
    p.close_price,
    p.volume,
    p.trading_value,
    p.market_cap,
    p.outstanding_shares,
    s.individual_net_buy,
    s.foreign_net_buy,
    s.institutional_net_buy,
    s.foreign_holding_ratio,
    r.per,
    r.pbr,
    r.roe,
    r.debt_ratio
FROM stocks_master m
JOIN normalized_stock_prices_daily p
    ON p.symbol = m.symbol
   AND p.base_date = (SELECT base_date FROM latest_price)
LEFT JOIN normalized_stock_supply_daily s
    ON s.symbol = m.symbol
   AND s.base_date = p.base_date
LEFT JOIN normalized_stock_fundamentals_ratios r
    ON r.symbol = m.symbol
   AND r.base_date = p.base_date
WHERE m.market IN ('KOSPI', 'KOSDAQ')
ORDER BY m.symbol;
```

## Report Repository Guardrail Policy

Use two independent gates.

### 1. Minimum report readiness

This decides whether a report can be generated.

Recommended conditions:

- latest `normalized_global_macro_daily` row exists
- latest `normalized_stock_prices_daily.base_date` exists
- `static_stock_universe.enabled = true` has at least one symbol
- at least one enabled static watchlist symbol has latest price data

This gate can pass even when full-market coverage is partial.

### 2. Full-market coverage quality

This decides how strongly the report may describe the whole KOSPI/KOSDAQ market.

Recommended conditions:

- `stocks_master.market IN ('KOSPI', 'KOSDAQ')` has more than 2,000 symbols
- latest `normalized_stock_prices_daily` covers more than 2,000 distinct
  KOSPI/KOSDAQ symbols
- recent `daily_stock_full_price_pipeline.records_processed` is more than 2,000

If this gate fails:

- do not block the whole report by default
- label market-wide stock coverage as `PARTIAL`
- avoid strong claims such as "entire market top volume stocks" unless the query
  explicitly limits itself to covered symbols

## Current Stock Pipeline Scope

The stock pipeline is now designed as a hybrid:

- Full price coverage:
  KRX KIND KOSPI/KOSDAQ listed universe -> KIS OHLCV ->
  `normalized_stock_prices_daily`
- Detailed stock enrichment:
  static / active / dynamic universe -> KIS supply, short selling, fundamentals,
  OpenDart events, feature generation

Therefore:

- `normalized_stock_prices_daily` should become full-market after a successful
  no-limit stock pipeline run
- `normalized_stock_supply_daily`, `normalized_stock_fundamentals_ratios`, and
  event tables are not expected to cover every listed KOSPI/KOSDAQ symbol every
  hour
- `stocks_master.is_active = true` is a curated/detailed-report flag, not the
  full market universe flag
