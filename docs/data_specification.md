# 데이터 수집 항목 및 테이블 명세서 (Data Dictionary)

본 프로젝트에서 수집하는 데이터 항목과 Supabase DB 테이블 구조에 대한 상세 명세입니다. 특히 분석의 정확도를 결정하는 메타 칼럼과 룩어헤드 방지 메커니즘에 대해 상술합니다.

## 1. 데이터 레이어 개요 (Data Architecture Layers)
데이터는 수집 및 분석 단계에 따라 3단계 레이어로 관리됩니다.

- **Raw Layer**: 외부 API로부터 받은 순수 JSON 데이터를 수정 없이 보존합니다. (JSONB 타입 사용)
  - 용도: 데이터 누락 확인, 수집 장애 디버깅, 추후 새로운 정규화 로직 적용 시 재처리 데이터원.
- **Normalized Layer**: 다양한 소스의 데이터를 단일 스키마로 통합하여 분석에 용이한 형태로 정규화합니다.
  - 용도: 백테스팅, 모델 개발, 대시보드 시각화의 주 원천.
- **Feature Layer**: 정규화된 데이터를 기반으로 계산된 기술적/기본적 분석 지표(Feature)를 저장합니다.
  - 용도: 머신러닝 모델의 입력값, 전략 신호 생성.

---

## 2. 공통 메타 칼럼 (Common Meta Columns)
모든 테이블은 데이터의 신뢰성과 재현성을 위해 아래의 공통 메타 칼럼을 포함합니다.

| 칼럼명 | 타입 | 설명 |
| :--- | :--- | :--- |
| **`source`** | VARCHAR | 데이터의 출처 (예: `KIS`, `FRED`, `OpenDart`, `NaverNews`) |
| **`symbol`** | VARCHAR | 주식 종목 코드 (예: `005930`). 매크로 테이블에서는 생략 가능. |
| **`base_date`** | DATE | 데이터가 가리키는 실제 경제적/시계열적 기준 일자 (YYYY-MM-DD) |
| **`collected_at`** | TIMESTAMP | 시스템이 외부 API를 호출하여 데이터를 DB에 처음 생성한 시각 (KST 기준) |
| **`available_at`** | TIMESTAMP | **[핵심]** 분석가가 이 데이터를 실제 '알 수 있었던' 시각. 룩어헤드 방지의 핵심. |
| **`raw_data`** | JSONB | 외부 소스에서 받은 원본 데이터 전체를 보관. |
| **`updated_at`** | TIMESTAMP | 해당 레코드가 마지막으로 수정(Upsert)된 시각. |

---

## 3. 테이블 상세 명세 (Table Specifications)

### 3.1 마스터 테이블 (Master Data)
- **`stocks_master`**: 수집 대상 종목의 메타 정보.
  - `market`: KOSPI, KOSDAQ, ETF 등으로 구분.
  - `is_active`: 현재 수집 대상 여부.
- **`macro_series_master`**: 매크로 지표의 메타 정보.
  - `frequency`: 데이터의 빈도 (Daily, Monthly, Quarterly 등).

### 3.2 정규화 레이어 (Normalized Data)
- **`normalized_stock_prices_daily`**: 일별 주가 및 거래 데이터.
  - `open_price`, `high_price`, `low_price`, `close_price`: 당일 시/고/저/종가.
  - `volume`: 당일 총 거래량 (주).
  - `trading_value`: 당일 총 거래대금 (원).
  - `market_cap`: 당일 시가총액 (수집 시점 기준).
- **`normalized_macro_series`**: 경제 지표 시계열 데이터.
  - `value`: 해당 일자의 지표 값 (NUMERIC).

### 3.3 Feature 레이어 (Feature Store)
- **`feature_store_daily`**: 계산된 분석 지표.
  - `feature_name`: 지표 구분 (예: `MA5`, `MA20`, `RSI`).
  - `feature_value`: 지표의 수치값.

---

## 4. 데이터 원칙 및 필수 해설 (Core Concepts)

### 4.1 룩어헤드 방지 (Look-ahead Bias Prevention)
백테스팅 시 '미래의 데이터'를 사용하는 오류를 방지하기 위해 `available_at` 칼럼을 사용합니다.
- **예시**: 오늘 장이 마감된 후 오후 4시에 수집된 주가 데이터의 `base_date`는 오늘 날짜지만, `available_at`은 오늘 오후 4시입니다. 전략 시뮬레이션 시 '오늘 오전 9시' 시점에는 이 데이터를 사용할 수 없도록 `where available_at < [시뮬레이션 시점]` 조건을 반드시 걸어야 합니다.

### 4.2 재실행 안전성 (Idempotency)
모든 데이터는 `Unique Key (source + symbol + base_date)`를 기반으로 **Upsert** 처리됩니다.
- 동일한 날짜의 데이터를 여러 번 수집해도 데이터가 중복 생성되지 않고 기존 레코드가 갱신되므로, 가상 환경 장애나 재실행 시 데이터 정합성을 유지합니다.

### 4.3 데이터 정규화 전략
Raw 데이터는 원본 그대로 보존하되, 분석 단계에서는 반드시 `Normalized` 테이블을 사용합니다. 이는 추후 데이터 소스(예: KIS에서 KRX로)가 변경되더라도 분석 쿼리나 모델 코드를 수정하지 않아도 되게 하기 위함(Abstraction)입니다.

---

## 5. 파이프라인 모니터링
- **`pipeline_run_logs`**: 각 배치 잡의 실행 상태를 기록합니다. `records_processed` 칼럼을 통해 매 실행 시 수집된 데이터의 양을 상시 모니터링할 수 있습니다.
