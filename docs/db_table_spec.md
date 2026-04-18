# DB 테이블/칼럼 명세 (StockData)

기준: `sql/supabase_schema.sql`, `sql/setup_rls_and_tokens.sql`, 그리고 코드 내 실제 `upsert/query` 참조.

## 1) 공식 DDL 테이블 (`sql/supabase_schema.sql`)

### Master
- `stocks_master`
  - `symbol`, `name`, `market`, `is_active`, `created_at`, `updated_at`
- `macro_series_master`
  - `series_id`, `source`, `name`, `frequency`, `created_at`

### Raw
- `raw_stock_prices_daily`
  - `id`, `source`, `symbol`, `base_date`, `raw_data`, `collected_at`, `available_at`
- `raw_stock_supply_daily`
  - `id`, `source`, `symbol`, `base_date`, `raw_data`, `collected_at`, `available_at`
- `raw_macro_series`
  - `id`, `source`, `series_id`, `base_date`, `raw_data`, `collected_at`, `available_at`
- `raw_disclosures`
  - `id`, `source`, `symbol`, `base_date`, `raw_data`, `collected_at`, `available_at`

### Normalized
- `normalized_stock_prices_daily`
  - `symbol`, `base_date`, `open_price`, `high_price`, `low_price`, `close_price`, `volume`, `trading_value`, `market_cap`, `outstanding_shares`, `available_at`, `updated_at`
- `normalized_macro_series`
  - `series_id`, `base_date`, `value`, `available_at`, `updated_at`
- `normalized_stock_supply_daily`
  - `symbol`, `base_date`, `foreign_net_buy`, `institutional_net_buy`, `individual_net_buy`, `foreign_holding_ratio`, `short_volume`, `short_balance`, `lending_balance`, `available_at`, `updated_at`
- `normalized_global_macro_daily`
  - `base_date`, `usdkrw`, `dxy`, `us10y`, `kr10y`, `wti`, `brent`, `nasdaq`, `sp500`, `sox`, `vix`, `available_at`, `updated_at`
- `normalized_derivatives_daily`
  - `base_date`, `kospi200_futures`, `futures_basis`, `open_interest`, `night_futures_return`, `expiration_flag`, `available_at`, `updated_at`
- `normalized_stock_events_daily`
  - `symbol`, `base_date`, `event_type`, `event_score`, `sentiment_score`, `available_at`, `updated_at`

### Feature / Ops
- `feature_store_daily`
  - `symbol`, `base_date`, `feature_name`, `feature_value`, `available_at`, `updated_at`
- `pipeline_run_logs`
  - `run_id`, `job_name`, `target_date`, `status`, `start_time`, `end_time`, `records_processed`, `error_message`

## 2) 별도 SQL 파일 정의 테이블

- `api_tokens` (`sql/setup_rls_and_tokens.sql`)
  - `service_name`, `token_value`, `expires_at`, `updated_at`

## 3) 코드에서 사용하지만 메인 DDL(`supabase_schema.sql`)에 없는 테이블

아래는 코드에서 `upsert_records`/`table`로 사용되는 테이블이며, 현재 메인 스키마 파일에는 직접 정의가 없습니다.

- `market_breadth_daily`
  - 예상 칼럼: `base_date`, `advances`, `declines`, `unchanged`, `advancing_volume`, `declining_volume`, `available_at`
- `derivatives_prices`
  - 예상 칼럼: `symbol`, `base_date`, `open`, `high`, `low`, `close`, `volume`, `open_interest`
- `stock_short_selling`
  - 예상 칼럼: `symbol`, `base_date`, `short_volume`, `short_value`, `short_ratio`
- `stock_fundamentals`
  - 예상 칼럼: `symbol`, `base_date`, `revenue`, `operating_income`, `net_income`, `total_assets`, `total_liabilities`, `total_equity`
- `stock_fundamentals_ratios`
  - 예상 칼럼: `symbol`, `base_date`, `per`, `pbr`, `roe`, `debt_ratio`
- `stock_prices_daily` (해외 수집기 경로)
  - 예상 칼럼: `symbol`, `exchange`, `base_date`, `open`, `high`, `low`, `close`, `volume`, `trading_value`
- `bond_prices`
  - 예상 칼럼: `symbol`, `base_date`, `price`, `yield_rate`, `volume`

## 4) 참고
- 공통 컬럼 설명(`base_date`, `collected_at`, `available_at`)은 `docs/data_dictionary.md` 참조.
