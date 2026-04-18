# Supabase Consumer Spec (투자정보 레포 연동용)

이 문서는 **다른 레포지토리(투자정보 전달 서비스)**에서 동일 Supabase DB를 읽을 때 필요한 최소 계약(Contract)을 정의합니다.

---

## 1) 목적과 범위

- 목적: 읽기 전용 소비자(리포트/알림/대시보드)가 데이터 정합성을 잃지 않고 일관된 기준으로 조회하도록 규칙을 표준화.
- 범위: `stocks_master`, `normalized_*`, `feature_store_daily`, `pipeline_run_logs` 중심.
- 비범위: 쓰기(INSERT/UPDATE), API 키/권한 발급 절차.

---

## 2) 소비자 공통 규칙

1. **기준일은 `base_date`**
   - 지표가 의미하는 시계열 날짜.
2. **실사용 가능 시점은 `available_at`**
   - 백테스트/시뮬레이션은 반드시 `available_at <= 시점` 조건 사용.
3. **최신일 조회는 테이블별로 분리**
   - `normalized_global_macro_daily`의 최신일과 `normalized_stock_prices_daily` 최신일은 다를 수 있음.
4. **비거래일 처리**
   - 주말/휴일에는 주식 테이블 최신일이 1일 이상 지연될 수 있으며 정상.

---

## 3) 핵심 테이블 계약 (읽기 관점)

## 3.1 Universe / Master

### `stocks_master`
- PK: `symbol`
- 핵심 컬럼: `symbol`, `name`, `market`, `is_active`, `updated_at`
- 소비자 권장 필터: `is_active = true`

### `macro_series_master`
- PK: `series_id`
- 핵심 컬럼: `series_id`, `source`, `name`, `frequency`

## 3.2 Market Data

### `normalized_stock_prices_daily`
- PK: `(symbol, base_date)`
- 핵심 컬럼: `open_price`, `high_price`, `low_price`, `close_price`, `volume`, `trading_value`, `market_cap`, `available_at`
- 주의: 동일 날짜 재실행 시 값이 갱신될 수 있으므로, 소비자는 최신 스냅샷을 그대로 신뢰.

### `normalized_stock_supply_daily`
- PK: `(symbol, base_date)`
- 핵심 컬럼: `foreign_net_buy`, `institutional_net_buy`, `individual_net_buy`, `pension_net_buy`, `corporate_net_buy`, `available_at`

### `normalized_stock_fundamentals_ratios`
- PK: `(symbol, base_date)`
- 핵심 컬럼: `per`, `pbr`, `roe`, `debt_ratio`, `available_at`
- 주의: ETF/ETN/우선주 등 비대상 자산은 결측 가능성이 높음.

### `normalized_stock_short_selling`
- PK: `(symbol, base_date)`
- 핵심 컬럼: `short_volume`, `short_value`, `short_ratio`, `available_at`

## 3.3 Macro / Breadth / Derivatives

### `normalized_macro_series`
- PK: `(series_id, base_date)`
- 핵심 컬럼: `value`, `available_at`

### `normalized_global_macro_daily`
- PK: `base_date`
- 핵심 컬럼: `usdkrw`, `dxy`, `us10y`, `kr10y`, `wti`, `brent`, `nasdaq`, `sp500`, `sox`, `vix`, `gold`, `copper`, `bdry`, `hy_spread`, `available_at`

### `market_breadth_daily`
- PK: `base_date`
- 핵심 컬럼: `advances`, `declines`, `unchanged`, `advancing_volume`, `declining_volume`, `available_at`
- 주의: 데이터 소스 승인/가용성 이슈 시 빈 날짜 가능.

### `normalized_derivatives_daily`
- PK: `base_date`
- 핵심 컬럼: `kospi200_futures`, `futures_basis`, `open_interest`, `night_futures_return`, `expiration_flag`, `available_at`

## 3.4 Feature / Logs

### `feature_store_daily`
- PK: `(symbol, base_date, feature_name)`
- 핵심 컬럼: `feature_name`, `feature_value`, `available_at`
- 주의: feature 생성 배치는 비거래일 target 요청 시 최근 거래일로 fallback된 기준일을 사용.

### `pipeline_run_logs`
- PK: `run_id`
- 핵심 컬럼: `job_name`, `target_date`, `status`, `records_processed`, `error_message`
- 상태 해석:
  - `SUCCESS`: 처리 건수 > 0
  - `WARN`: 실행은 되었으나 처리 건수 0 또는 비어있는 실행

---

## 4) 소비자 레포 권장 조회 패턴

## 4.1 활성 종목 최신 종가 스냅샷

```sql
WITH latest AS (
  SELECT max(base_date)::date AS d
  FROM normalized_stock_prices_daily
)
SELECT p.symbol, m.name, p.base_date, p.close_price, p.volume, p.trading_value
FROM normalized_stock_prices_daily p
JOIN stocks_master m ON m.symbol = p.symbol
JOIN latest l ON p.base_date = l.d
WHERE m.is_active = true;
```

## 4.2 주가 + 수급 + 밸류에이션 조인 (동일 기준일)

```sql
WITH latest AS (
  SELECT max(base_date)::date AS d
  FROM normalized_stock_prices_daily
)
SELECT
  p.symbol,
  m.name,
  p.base_date,
  p.close_price,
  s.foreign_net_buy,
  r.per,
  r.pbr,
  r.roe,
  r.debt_ratio
FROM normalized_stock_prices_daily p
LEFT JOIN normalized_stock_supply_daily s
  ON s.symbol = p.symbol AND s.base_date = p.base_date
LEFT JOIN normalized_stock_fundamentals_ratios r
  ON r.symbol = p.symbol AND r.base_date = p.base_date
JOIN stocks_master m ON m.symbol = p.symbol
JOIN latest l ON p.base_date = l.d
WHERE m.is_active = true;
```

## 4.3 최근 실행 상태 모니터링

```sql
SELECT job_name, target_date, status, records_processed
FROM pipeline_run_logs
WHERE target_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - INTERVAL '3 day'
ORDER BY target_date DESC, job_name;
```

---

## 5) 데이터 품질 가드레일 (소비자 측)

소비자 레포에서 최소한 아래 3개는 점검 권장:

1. `zero_volume_pct` 급증 감지 (최근 영업일 대비)
2. 최신일 지연(`lag_days`) 경보
3. `pipeline_run_logs`에서 `WARN`/`FAIL` 탐지

---

## 6) 변경 관리 규칙 (호환성)

- 본 문서의 테이블/컬럼 계약을 변경할 때는 `docs/`에 변경 로그를 남기고 소비자 레포와 동기화합니다.
- 파생 피처명(`feature_name`) 신규 추가는 **하위 호환**으로 간주하되, 기존 피처명 변경/삭제는 breaking change로 취급합니다.

