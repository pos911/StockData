# 데이터 수집 항목 및 테이블 명세서 (Data Dictionary)

본 프로젝트에서 수집하는 데이터 항목과 Supabase DB 테이블 구조에 대한 상세 명세입니다.

## 1. 데이터 레이어 개요 (Data Architecture Layers)
데이터는 수집 단계에 따라 3단계 레이어로 관리됩니다.
- **Raw Layer**: 외부 API로부터 받은 순수 JSON 데이터를 보존합니다. (Troubleshooting 용도)
- **Normalized Layer**: 다양한 소스의 데이터를 단일 스키마로 통합하여 분석에 용이한 형태로 정규화합니다.
- **Feature Layer**: 정규화된 데이터를 기반으로 계산된 기술적/기본적 분석 지표(Feature)를 저장합니다.

---

## 2. 테이블 상세 명세 (Table Specifications)

### 2.1 마스터 테이블 (Master Data)
종목 및 지표의 기본 정보를 관리합니다.
- **`stocks_master`**: 수집 대상 주식 종목 리스트
  - `symbol`: 종목코드 (PK)
  - `name`: 종목명
  - `market`: 시장 구분 (KOSPI/KOSDAQ)
  - `is_active`: 활성화 여부
- **`macro_series_master`**: 수집 대상 매크로 시계열 리스트
  - `series_id`: 지표 코드 (FRED ID 등, PK)
  - `source`: 데이터 소스 (FRED 등)
  - `name`: 지표 이름
  - `frequency`: 데이터 주기 (Daily, Monthly 등)

### 2.2 정규화 레이어 (Normalized Data) - **분석 주사용 테이블**
- **`normalized_stock_prices_daily`**: 주식 일별 시세 데이터 (KIS/KRX 통합)
  - `open_price`, `high_price`, `low_price`, `close_price`: 시/고/저/종가
  - `volume`: 거래량
  - `trading_value`: 거래대금
  - `market_cap`: 시가총액
  - `outstanding_shares`: 상장주식수
  - `available_at`: **실제 분석 시 사용 가능한 시각 (룩어헤드 방지 핵심)**
- **`normalized_macro_series`**: 매크로 시계열 통합 데이터
  - `series_id`: 지표 식별자
  - `base_date`: 데이터 기준일
  - `value`: 지표 값

### 2.3 Raw 레이어 (Raw JSON Data)
- **`raw_stock_prices_daily`**: KIS/KRX에서 수집한 순수 JSON
- **`raw_macro_series`**: FRED에서 수집한 순수 JSON (Observation 객체 전체 보존)
- **`raw_disclosures`**: OpenDart 공시 전문 및 Naver 뉴스 검색 결과 (JSONB 타입으로 전체 보존)

### 2.4 Feature 레이어 (Feature Store)
- **`feature_store_daily`**: 파이프라인이 계산한 분석 지표
  - `feature_name`: 지표 명 (예: MA5, MA20)
  - `feature_value`: 계산된 값

---

## 3. 데이터 소스 및 수집 항목 (Data Sources)

| 소스 | 대상 테이블 | 주요 수집 항목 | 비고 |
| :--- | :--- | :--- | :--- |
| **KIS** | `normalized_stock_prices_daily` | 현재가, 시가, 고가, 저가, 거래량 | 실시간 시세 기반 |
| **OpenDart** | `raw_disclosures` | 당일 기업 공시 내역 (제목, 링크 등) | 기업 고유번호 매칭 수집 |
| **Naver** | `raw_disclosures` | 종목 관련 최신 뉴스 100건 | 실시간 필터링 수집 |
| **FRED** | `normalized_macro_series` | 미국 금리, 달러지수, 각종 경제 지표 | 과거 7일 증분 수집 최적화 |

---

## 4. 핵심 데이터 필드 가이드 (Core Fields)
- **`base_date`**: 데이터가 가리키는 실제 날짜 (예: 2026-04-08 시세)
- **`collected_at`**: 시스템이 실제 API를 호출한 시각
- **`available_at`**: **가장 중요한 필드.** 백테스팅이나 모델 학습 시 이 시각 이전의 데이터만 사용해야 룩어헤드 편향(Look-ahead bias)을 방지할 수 있습니다. (예: 장마감 후 오후 4시에 수집된 데이터는 오후 4시 이후 시점으로 표시)

---

## 5. 파이프라인 로그 (Monitoring)
- **`pipeline_run_logs`**: 각 잡(Job)의 성공/실패 여부, 처리 건수, 에러 메시지 등을 기록하여 GitHub Actions 실행 결과와 별개로 DB에서 모니터링 가능합니다.
