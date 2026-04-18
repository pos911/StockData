# StockData 수집/적재 수행 점검 보고서 (2026-04-13)

## 결론 요약
- 프로젝트 목표(다양한 종목/매크로 수집 및 적재)는 **부분 달성** 상태입니다.
- 코드상 수집기는 다양하게 구현되어 있으나, 실제 일일 실행 경로(job/workflow)에서 일부 데이터는 적재되지 않거나 주기 실행에서 제외되어 있습니다.

## 목표 대비 수행 현황

### 1) 종목 데이터
- `run_daily_stock_pipeline.py`는 동적 유니버스 순회, KIS OHLCV/수급, OpenDart 공시, Naver 뉴스, KRX 가격 수집 흐름을 가집니다.
- 그러나 실제 DB 적재는 다음 이슈가 있습니다.

#### [이슈 S-1] KIS OHLCV/수급 데이터가 운영 스키마 테이블로 적재되지 않음
- 파이프라인은 `fetch_ohlcv`, `fetch_investor_trend`를 호출하지만 반환값을 정규화/적재에 사용하지 않습니다.
- 내부적으로 `KISDomesticStockCollector`는 `stock_prices_daily`, `stock_supply_demand`에 upsert 시도하는데, 현재 스키마에는 해당 테이블이 없습니다.
- 결과적으로 KIS 기반 종목가격/수급이 목표 테이블(`normalized_stock_prices_daily`, `normalized_stock_supply_daily`)로 이어지지 않습니다.

#### [이슈 S-2] KRX 일봉 적재 조건이 과도하게 제한됨
- KRX auth_key 미설정 시 `skip_krx=True`로 되어 KRX 가격 수집 전체를 건너뜁니다.
- 하지만 실제 `fetch_daily_ohlcv`는 FinanceDataReader 기반이며 auth_key 없이도 동작 가능한 경로입니다.
- 따라서 설정에 따라 종목 가격이 통째로 비어버릴 수 있습니다.

### 2) 매크로 데이터
- `run_daily_macro_pipeline.py`는 `config/macro_series.json`를 읽지만 루프 내 실제 처리 분기는 `source == "FRED"`만 구현돼 있습니다.
- `TradingEconomics`, `YAHOO` 항목은 enabled 상태라도 `normalized_macro_series` 적재 경로가 없습니다.
- 별도로 글로벌 지표는 `normalized_global_macro_daily`에 적재되므로 일부 YAHOO 성격 데이터(VIX/원자재 등)는 이 테이블에서 보완되지만, 구성 파일 의도(시리즈별 적재)와는 불일치입니다.

### 3) 파생/이벤트/피처
- 파생은 `run_daily_derivatives_pipeline.py`가 별도 구현되어 `normalized_derivatives_daily` 적재가 가능합니다.
- 이벤트는 주식 파이프라인 내 OpenDart 이벤트 파싱을 통해 `normalized_stock_events_daily` 적재 시도가 있습니다.
- 피처는 `normalized_stock_prices_daily`가 있어야 생성되는데, 상기 종목 적재 이슈(S-1, S-2)가 있으면 피처 공백 가능성이 높습니다.

## 주기 실행(자동화) 점검
- GitHub Actions `daily_sync.yml`는 3시간 주기 크론으로 매크로/주식/피처 파이프라인을 실행합니다.
- 그러나 **파생 파이프라인은 워크플로우에서 호출되지 않아 정기 실행 누락** 상태입니다.

## 적재 누락/미흡 데이터 목록
1. KIS 기반 종목 가격/수급의 운영 스키마 적재 (실질 누락)
2. `normalized_stock_supply_daily` (실행 경로상 미적재)
3. `macro_series.json`의 TradingEconomics 소스 (미구현)
4. `macro_series.json`의 YAHOO 소스 시리즈별 적재 (미구현)
5. `normalized_derivatives_daily`의 주기 자동 실행 (워크플로우 누락)

## 권고사항 (우선순위)
1. **높음**: 주식 파이프라인에서 KIS 결과를 `normalized_stock_prices_daily`, `normalized_stock_supply_daily`에 직접 upsert하도록 수정.
2. **높음**: `skip_krx` 조건을 "KRX breadth"에만 적용하고, FDR 기반 일봉은 auth_key와 분리.
3. **중간**: 매크로 파이프라인에 `TradingEconomics`, `YAHOO` 분기 구현 또는 config에서 미사용 소스 비활성화.
4. **중간**: `daily_sync.yml`에 `run_daily_derivatives_pipeline.py` 추가.
5. **중간**: 실데이터 검증 스크립트(테이블별 최근 base_date/건수) CI 단계 추가.

## 현재 점검의 한계
- 로컬 환경의 Supabase URL/키 미설정으로 실제 원격 DB 건수 검증은 수행하지 못했습니다.
- 본 보고는 코드/워크플로우 정적 분석 기준입니다.
