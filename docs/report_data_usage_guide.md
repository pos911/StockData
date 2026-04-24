# 리포트 데이터 활용 가이드

## 1. 먼저 답부터

### `kr10y`는 원래 월간 데이터가 맞나?

맞습니다.

현재 `normalized_global_macro_daily.kr10y`는 한국은행의 일간 시장금리가 아니라, FRED에서 제공하는 OECD 계열 월간 장기국채 금리 시리즈를 사용합니다.

- 시리즈 ID: `IRLTLT01KRM156N`
- 의미: 한국 10년 국고채 수익률의 월간 거시지표

따라서 이 값은:

- 장중 실시간 금리로 해석하면 안 되고
- 월간 거시환경 참고값으로 해석해야 합니다

즉,

- `us10y`: 시장성 있는 일간 금리 시계열
- `kr10y`: 월간 매크로 금리 프록시

이렇게 이해하면 됩니다.

### SQL은 최신 데이터를 기준으로 확인해야 하나?

네. 반드시 최신 `base_date` 기준으로 확인하는 게 맞습니다.

이 시스템은 일별 적재 구조라서, 활용할 때 기본 원칙은:

1. 각 테이블의 최신 `base_date` 확인
2. 그 최신일 row를 기준으로 리포트 값을 읽기
3. 테이블별 최신일이 다를 수 있음을 전제로 보기

특히:

- `normalized_global_macro_daily`
- `market_breadth_daily`
- `normalized_stock_prices_daily`
- `normalized_stock_supply_daily`
- `feature_store_daily`

이 다섯 개는 최신일을 먼저 보는 습관이 중요합니다.

## 2. 리포트에서 바로 쓰는 테이블

### `normalized_global_macro_daily`

시장 전체 스냅샷 테이블입니다.

주요 컬럼:

- `base_date`: 기준일
- `kospi`: 코스피 지수
- `kospi_change_rate`: 코스피 등락률
- `kosdaq`: 코스닥 지수
- `kosdaq_change_rate`: 코스닥 등락률
- `nasdaq`: 나스닥 지수
- `nasdaq_change_rate`: 나스닥 등락률
- `sp500`: S&P500 지수
- `sp500_change_rate`: S&P500 등락률
- `kospi_individual_net_buy`: 코스피 시장 전체 개인 순매수
- `kospi_foreign_net_buy`: 코스피 시장 전체 외국인 순매수
- `kospi_institutional_net_buy`: 코스피 시장 전체 기관 순매수
- `kosdaq_individual_net_buy`: 코스닥 시장 전체 개인 순매수
- `kosdaq_foreign_net_buy`: 코스닥 시장 전체 외국인 순매수
- `kosdaq_institutional_net_buy`: 코스닥 시장 전체 기관 순매수
- `usdkrw`: 원달러 환율
- `dxy`: 달러 인덱스
- `us10y`: 미국 10년물 금리
- `kr10y`: 한국 10년물 금리, 단 월간 매크로 값
- `vix`: VIX
- `sox`: 필라델피아 반도체 지수
- `wti`: WTI
- `brent`: 브렌트유
- `gold`: 금
- `copper`: 구리
- `bdry`: 해운 관련 지표 대용
- `hy_spread`: 미국 하이일드 스프레드

이 테이블을 보면:

- 한국/미국 대표 지수
- 한국 시장 전체 수급
- 주요 거시 자산 가격

을 한 번에 볼 수 있습니다.

### `market_breadth_daily`

시장 폭 테이블입니다.

주요 컬럼:

- `base_date`
- `advances`: 상승 종목 수
- `declines`: 하락 종목 수
- `unchanged`: 보합 종목 수
- `advancing_volume`: 상승 종목 거래량 합
- `declining_volume`: 하락 종목 거래량 합

이 테이블은:

- 시장이 소수 대형주만 오르는지
- 시장 전반이 같이 오르는지

를 판단할 때 유용합니다.

### `normalized_stock_prices_daily`

개별 종목 가격 테이블입니다.

주요 컬럼:

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

컬럼 의미:

- `market_cap`: 시가총액
- `outstanding_shares`: 상장주식수

### `normalized_stock_supply_daily`

개별 종목 수급 테이블입니다.

주요 컬럼:

- `symbol`
- `base_date`
- `individual_net_buy`
- `foreign_net_buy`
- `institutional_net_buy`
- `pension_net_buy`
- `corporate_net_buy`
- `foreign_holding_ratio`

컬럼 의미:

- `individual_net_buy`: 개인 순매수
- `foreign_net_buy`: 외국인 순매수
- `institutional_net_buy`: 기관 순매수
- `foreign_holding_ratio`: 외국인 보유율

중요한 해석:

- `foreign_net_buy`는 흐름입니다
- `foreign_holding_ratio`는 보유 상태입니다

즉:

- 오늘 외국인이 샀는가
- 원래 외국인이 많이 들고 있는 종목인가

를 분리해서 볼 수 있습니다.

### `normalized_stock_fundamentals_ratios`

개별 종목 밸류/재무비율 테이블입니다.

주요 컬럼:

- `symbol`
- `base_date`
- `per`
- `pbr`
- `roe`
- `debt_ratio`

### `normalized_derivatives_daily`

파생 지표 테이블입니다.

주요 컬럼:

- `base_date`
- `kospi200_futures`
- `futures_basis`
- `open_interest`
- `night_futures_return`
- `expiration_flag`

### `feature_store_daily`

피처 엔지니어링 결과 테이블입니다.

주요 컬럼:

- `symbol`
- `base_date`
- `feature_name`
- `feature_value`

이 테이블은 모델/점수화/리포트 자동 생성용입니다.

## 3. 최신 데이터 확인 원칙

실제 활용할 때는 항상 아래 순서로 보는 게 안전합니다.

1. 최신 `base_date` 확인
2. 그 날짜 row를 읽기
3. 테이블끼리 최신일이 다르면 해석에 주의

예를 들어:

- `normalized_global_macro_daily` 최신일은 오늘
- `feature_store_daily` 최신일도 오늘
- 그런데 어떤 이유로 `normalized_stock_supply_daily` 최신일이 어제일 수 있음

이 경우에는 리포트 숫자가 안 맞는 게 아니라, 테이블 최신일이 다를 수 있다는 뜻입니다.

## 4. 바로 써먹는 SQL 예시

### 최신 글로벌 스냅샷

```sql
select *
from normalized_global_macro_daily
order by base_date desc
limit 1;
```

### 최신 breadth

```sql
select *
from market_breadth_daily
order by base_date desc
limit 1;
```

### 특정 종목 최신 가격/수급/비율

```sql
with latest_price as (
  select max(base_date) as base_date
  from normalized_stock_prices_daily
),
latest_supply as (
  select max(base_date) as base_date
  from normalized_stock_supply_daily
)
select
  p.symbol,
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
from normalized_stock_prices_daily p
left join normalized_stock_supply_daily s
  on s.symbol = p.symbol
 and s.base_date = p.base_date
left join normalized_stock_fundamentals_ratios r
  on r.symbol = p.symbol
 and r.base_date = p.base_date
where p.symbol = '005930'
  and p.base_date = (select base_date from latest_price);
```

### 최신일 기준 전체 상태 점검

아래 파일을 실행하면 됩니다.

- `sql/verify_api_ingestion_test_page.sql`

이 SQL은:

- 주요 테이블 freshness
- 글로벌 매크로 최신 row
- market breadth 최신 row
- 파생 최신 row
- 샘플 종목 5개
- raw 적재 건수
- 파이프라인 상태

를 한 번에 보여줍니다.

## 5. 해석할 때 주의할 점

### `kr10y`

- 일간 실시간 시장금리 아님
- 월간 매크로 값
- 방향성 판단용으로는 좋지만, 당일 채권시장 변동 해석용으로는 부적합

### `foreign_holding_ratio`

- 당일 순매수와 다른 개념
- 외국인이 누적으로 얼마나 들고 있는지 보는 값

### `outstanding_shares`

- 시가총액, 희석, 증자/소각 영향 해석에 유용
- 당일 가격보다 변화 빈도는 낮음

## 6. 지금 기준 활용 결론

현재는 아래처럼 활용하면 됩니다.

- 시장 리포트 요약:
  - `normalized_global_macro_daily`
  - `market_breadth_daily`
- 종목별 가격/수급:
  - `normalized_stock_prices_daily`
  - `normalized_stock_supply_daily`
- 종목별 밸류/재무:
  - `normalized_stock_fundamentals_ratios`
- 모델/점수 기반 활용:
  - `feature_store_daily`

즉, 지금은 이 문서와 `verify_api_ingestion_test_page.sql`만 봐도

- 어디서 데이터를 읽어야 하는지
- 각 컬럼이 무슨 의미인지
- 최신 데이터가 들어왔는지

를 바로 판단할 수 있습니다.
