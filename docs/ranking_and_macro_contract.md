# Ranking And Macro Contract Update

## Volume Ranking Contract

- Production volume ranking uses KIS `volume-rank` with `market_code='J'` only.
- KOSPI, KOSDAQ, ETF, and ETN buckets are derived from `stocks_master.market` and `asset_type`.
- KIS `K` and `Q` market codes are reserved for diagnosis only and are not used in the production ranking pipeline.
- If KIS volume rows are sparse for a market, the pipeline replaces that market's volume ranking with a valid-price fallback.

## Valid Price Date Contract

- Latest price date for ranking fallback must not use simple `max(base_date)`.
- The selected date must satisfy:
  - `base_date <= target_date`
  - `close_price is not null`
  - `volume is not null`
  - `trading_value is not null`
  - valid row count threshold is met

## US 3Y Macro Contract

- `normalized_global_macro_daily.us3y`
- Meaning: U.S. 3-Year Treasury Constant Maturity rate
- Source: FRED `DGS3`
- Unit: percent
- Fallback: the latest normalized `DGS3` value at or before the target date
- Report consumers can use `us10y` and `us3y` together for yield-curve analysis
