# StockData 데이터 적재 명세서

_Last updated: 2026-05-05_

이 문서는 `pos911/StockData`가 Supabase에 적재하는 주요 데이터의 목적, 원천, 테이블 구조, 컬럼 의미, 단위, 사용 시 주의사항을 정리한 데이터 사전이다.

현재 실제 DDL의 기준 파일은 `sql/supabase_schema.sql`이며, 이 문서는 해당 스키마와 2026-05-05 기준 최신 파이프라인 정책을 사람이 읽기 쉬운 형태로 설명한다.

---

## 1. 전체 데이터 계약 요약

### 1.1 원천별 역할

| 원천 | 주요 역할 | 비고 |
|---|---|---|
| KRX | 한국시장 종목 마스터, ETF/ETN 일별매매정보, ETP 가격·거래량·거래대금 | 한국시장 공식 기준 원천 |
| KIS Open API | 국내 주식 수급, 공매도, 스냅샷, 일부 실시간성 거래량 참고 | 거래량 ranking은 production path에서 `J` 전체시장만 사용 |
| FRED | 미국 금리 등 글로벌 매크로 시계열 | `DGS3`는 미국 3년물, `DGS10`은 미국 10년물 |
| ECOS | 한국 금리·환율 등 국내 매크로 | 한국 10년물 등 |
| yfinance 또는 외부 시세 | 글로벌 지수·상품·변동성 지표 보조 | S&P500, Nasdaq, VIX, DXY 등 |

### 1.2 최신 운영 원칙

1. `stocks_master`는 전체 시장 마스터다. 상세 수집 대상 전체를 의미하지 않는다.
2. 관심종목은 `static_stock_universe.enabled=true` 기준이다.
3. 시장별 랭킹의 공식 테이블은 `normalized_market_rankings_daily`다.
4. 거래량 랭킹은 KIS `J` 전체시장 응답을 `stocks_master.market` 기준으로 분류하되, 부족하면 `VALID_PRICE_FALLBACK`으로 보강한다.
5. 거래대금·시가총액 랭킹은 `normalized_stock_prices_daily + stocks_master` 기준으로 만든다.
6. `Q530134` 같은 Q-prefix 심볼은 저장하지 않는다. 항상 canonical 6자리 심볼로 정규화한다.
7. 최신 가격 기준일은 단순 `max(base_date)`가 아니라 `close_price`, `volume`, `trading_value`가 유효한 최신일을 사용한다.
8. `available_at`은 데이터가 리포트·소비자에게 사용 가능해지는 시각이다.
9. 신규 스키마 변경은 SQL 파일로 남기고 Supabase SQL Editor에서 수동 실행한다.

---

## 2. 파이프라인 실행 구조

### 2.1 권장 실행 순서

```text
1. run_daily_master_pipeline.py
   - KRX 기준 stocks_master 갱신
   - ETF/ETN KRX ETP 일별매매정보를 raw/normalized price에 적재

2. run_daily_ranking_pipeline.py
   - KIS J 전체시장 거래량 ranking 수집
   - KOSPI/KOSDAQ/ETF/ETN별 volume ranking 생성
   - 부족한 시장은 valid price fallback으로 보강
   - trading_value / market_cap ranking 생성

3. run_daily_stock_pipeline.py
   - static 관심종목 + ranking 종목 중심으로 상세 수집
   - KIS 수급, 공매도, 스냅샷, 종목별 보조 데이터 수집

4. run_daily_macro_pipeline.py
   - FRED/ECOS/외부지표 기반 macro 데이터 수집
```

### 2.2 상세 수집 제한

`run_daily_stock_pipeline.py`는 전체 종목을 대상으로 상세 호출하지 않는다. 기본 상세 universe는 다음으로 구성한다.

- `static_stock_universe.enabled=true`
- 최신 `normalized_market_rankings_daily` 종목
- 필요 시 제한된 수의 보강 대상

`stocks_master.is_active=true` 전체를 상세 수집 대상으로 확장하지 않는다.

---

## 3. 테이블별 데이터 사전

## 3.1 `stocks_master`

### 목적
한국시장 전체 종목 마스터. KRX 기준으로 KOSPI/KOSDAQ/ETF/ETN을 구분한다.

### 주요 원천
- KOSPI/KOSDAQ: KRX KIND, FDR, pykrx fallback
- ETF/ETN: KRX ETP API

### Primary Key
- `symbol`

### 컬럼 정의

| 컬럼 | 타입 | 의미 | 단위/예시 | 주의사항 |
|---|---|---|---|---|
| symbol | varchar | 종목코드 | `005930`, `278470`, `530134` | canonical 심볼. Q-prefix 금지 |
| name | varchar | 종목명 | 삼성전자, 에이피알 | KRX/KIS 기준명 |
| market | varchar | 시장 구분 | KOSPI, KOSDAQ, ETF, ETN | report·ranking의 시장 검증 기준 |
| asset_type | varchar | 자산 유형 | STOCK, ETF, ETN | 일반 주식과 ETP 구분 |
| is_active | boolean | 상세 수집 보호 또는 관심 대상 여부 | true/false | 전체 상장 여부가 아니다. 신규 KRX 종목은 기본 false |
| created_at | timestamptz | 최초 생성 시각 | UTC/KST 혼재 가능 | 시스템 생성 |
| updated_at | timestamptz | 최종 갱신 시각 | ISO timestamp | master refresh 시 갱신 |

### 소비자 주의사항
- `is_active=true`를 watchlist로 해석하지 않는다.
- report 관심종목은 `static_stock_universe.enabled=true`를 사용한다.
- market mismatch가 있으면 ranking 저장 또는 report 입력에서 제외한다.

---

## 3.2 `static_stock_universe`

### 목적
사용자 또는 리포트가 지속적으로 추적할 관심종목 목록.

### Primary Key
- `symbol`

| 컬럼 | 의미 | 예시 | 주의사항 |
|---|---|---|---|
| symbol | 관심종목 코드 | `071050` | canonical 6자리 |
| name | 관심종목명 | 한국금융지주 | 표시명 |
| market | 시장 | KOSPI | stocks_master와 일치해야 함 |
| asset_type | 자산 유형 | STOCK | ETF/ETN도 가능 |
| enabled | 사용 여부 | true | report watchlist 기준 |
| source_file | 원천 파일 | config/stock_universe.json | 관리 참고용 |
| created_at / updated_at | 생성/갱신 시각 | timestamp | 시스템 관리 |

### 소비자 주의사항
- report의 watchlist는 이 테이블의 `enabled=true`만 사용한다.

---

## 3.3 `macro_series_master`

### 목적
FRED/ECOS 등 macro 시계열의 메타데이터 관리.

| 컬럼 | 의미 | 예시 |
|---|---|---|
| series_id | 내부 시계열 ID | DGS3, DGS10, KR_GOVT_10Y |
| source | 원천 | FRED, ECOS |
| name | 영문명 | Market Yield on U.S. Treasury Securities at 3-Year Constant Maturity |
| name_ko | 한글명 | 미국 3년 국채금리 |
| stat_code | ECOS 통계코드 | ECOS 계열에서 사용 |
| item_code | ECOS 항목코드 | ECOS 계열에서 사용 |
| category | 분류 | rates, fx, commodities |
| unit | 단위 | %, index, KRW |
| frequency | 주기 | D, M, Q |
| is_active | 수집 여부 | true/false |
| updated_at / created_at | 관리 시각 | timestamp |

---

## 3.4 Raw 레이어 공통 원칙

Raw 테이블은 API 응답 원문을 보존한다. 데이터 품질 이슈가 발생했을 때 역추적용으로 사용한다.

### 공통 컬럼

| 컬럼 | 의미 |
|---|---|
| id | UUID PK |
| source | 원천 시스템. KRX, KIS, FRED, ECOS 등 |
| symbol / series_id | 종목코드 또는 시계열 ID |
| base_date | 데이터 기준일 |
| raw_data | 원본 API 응답 JSON |
| collected_at | 수집 시각 |
| available_at | 소비 가능 시각 |

---

## 3.5 `raw_stock_prices_daily`

### 목적
주가·ETP 가격성 데이터의 원본 응답 저장.

| 컬럼 | 의미 | 예시 |
|---|---|---|
| source | 가격 원천 | KRX, KIS |
| symbol | 종목코드 | 005930, 530134 |
| base_date | 가격 기준일 | 2026-05-04 |
| raw_data | 원본 응답 | KRX/KIS JSON |
| collected_at | 수집 시각 | timestamp |
| available_at | 사용 가능 시각 | timestamp |

### 사용처
- 원천 응답 확인
- ETF/ETN KRX ETP 응답 진단
- 가격 정규화 오류 추적

---

## 3.6 `normalized_stock_prices_daily`

### 목적
종목별 일별 가격·거래량·거래대금·시총 정규화 테이블.

### Primary Key
- `(symbol, base_date)`

| 컬럼 | 의미 | 단위 | 해석/주의사항 |
|---|---|---|---|
| symbol | 종목코드 | 문자열 | canonical 6자리 또는 ETP 코드 |
| base_date | 거래 기준일 | 날짜 | 휴일/비영업일 주의 |
| open_price | 시가 | 원 | ETF/ETN도 가격 단위 |
| high_price | 고가 | 원 |  |
| low_price | 저가 | 원 |  |
| close_price | 종가 | 원 | 유효 가격 row 필수 조건 |
| volume | 거래량 | 주/좌/증권수 | ETF는 좌수, ETN은 증권수 성격 |
| trading_value | 거래대금 | 원 | ranking의 trading_value 기준 |
| market_cap | 시가총액 | 원 | ETF/ETN은 상품 시가총액 또는 순자산과 차이 가능 |
| outstanding_shares | 상장주식수/상장좌수/상장증권수 | 수량 | 주식·ETF·ETN별 의미 다름 |
| available_at | 소비 가능 시각 | timestamp | report cutoff 기준 |
| updated_at | 갱신 시각 | timestamp | upsert 시 갱신 |

### 품질 기준
유효 가격 row는 아래 조건을 만족해야 한다.

```sql
close_price is not null
and volume is not null
and trading_value is not null
```

### 소비자 주의사항
- Top ranking 1차 소스로 직접 정렬하지 않는다.
- `normalized_market_rankings_daily`가 없을 때 fallback으로만 사용한다.
- `source` 컬럼은 없다. 원천 확인은 `raw_stock_prices_daily`를 본다.

---

## 3.7 `normalized_stock_snapshots_daily`

### 목적
KIS 등에서 받는 종목 스냅샷성 지표 저장. 가격 테이블에 넣기 어려운 시총, 상장주식수, 외국인 보유율, PER/PBR 등을 분리한다.

| 컬럼 | 의미 | 단위/예시 | 주의사항 |
|---|---|---|---|
| symbol | 종목코드 | 005930 | canonical |
| base_date | 기준일 | 2026-05-04 |  |
| market_cap | 시가총액 | 원 | snapshot 값 |
| outstanding_shares | 상장주식수 | 주 |  |
| foreign_holding_ratio | 외국인 보유율 | % | 외국인 누적 보유 비율 |
| per | PER | 배 | 업종 비교 필요 |
| pbr | PBR | 배 | 업종 비교 필요 |
| w52_high | 52주 최고가 | 원 |  |
| w52_low | 52주 최저가 | 원 |  |
| source | 원천 | KIS |  |
| available_at / updated_at | 시각 | timestamp |  |

---

## 3.8 `raw_market_rankings`

### 목적
랭킹 생성 원천 row 저장. 랭킹 품질 이슈를 역추적하기 위한 raw 테이블.

| 컬럼 | 의미 | 예시 |
|---|---|---|
| source | 랭킹 원천 | KIS, KRX, VALID_PRICE_FALLBACK |
| base_date | 랭킹 기준일 | 2026-05-04 |
| market | 시장 | KOSPI, KOSDAQ, ETF, ETN |
| rank_type | 랭킹 유형 | volume, trading_value, market_cap |
| symbol | 종목코드 | 005930 |
| name | 종목명 | 삼성전자 |
| raw_rank | 원천 또는 재부여 순위 | 1 |
| raw_data | 원본 또는 fallback 설명 JSON | price_base_date, fallback_reason 등 포함 |
| collected_at / available_at | 시각 | timestamp |

### 주의사항
- report는 보통 이 테이블을 직접 사용하지 않는다.
- 진단·감사·원인분석용이다.

---

## 3.9 `normalized_market_rankings_daily`

### 목적
시장별 Top 종목 랭킹의 공식 소비 테이블. report는 이 테이블을 1차 기준으로 사용한다.

### Primary Key
- `(base_date, market, rank_type, rank, symbol)`

| 컬럼 | 의미 | 단위/예시 | 주의사항 |
|---|---|---|---|
| base_date | 랭킹 기준일 | 2026-05-04 | report ranking_base_date |
| market | 시장 | KOSPI, KOSDAQ, ETF, ETN | stocks_master.market과 일치해야 함 |
| rank_type | 랭킹 유형 | volume, trading_value, market_cap |  |
| rank | 순위 | 1~30 | 시장/유형별 재부여 |
| symbol | 종목코드 | 005930 | Q-prefix 금지 |
| name | 종목명 | 삼성전자 | master명 우선 |
| volume | 거래량 | 주/좌/증권수 | volume ranking의 주요 수치 |
| trading_value | 거래대금 | 원 | trading_value ranking의 주요 수치 |
| market_cap | 시가총액 | 원 | market_cap ranking의 주요 수치 |
| change_rate | 등락률 | % | 원천에 없으면 null |
| metric_value | 랭킹 기준값 | rank_type별 다름 | volume/trading_value/market_cap 중 하나 |
| source | 생성 원천 | KIS, KRX, VALID_PRICE_FALLBACK | report 진단에 사용 |
| available_at / updated_at | 시각 | timestamp |  |

### source 의미

| source | 의미 | 사용 예 |
|---|---|---|
| KIS | KIS volume-rank `J` 전체시장 응답에서 market 검증 후 저장 | 실시간성 거래량 참고 |
| KRX | KRX ETP API 등 공식 원천에서 직접 생성 | ETF/ETN 가격 기반 랭킹 |
| VALID_PRICE_FALLBACK | 최신 유효 가격 테이블에서 재생성 | KIS sparse 또는 KRX 비영업일 보완 |

### 품질 기준
- `market mismatch rows = 0`
- `q_prefix rows = 0`
- KOSPI/KOSDAQ volume count > 0
- trading_value/market_cap ranking 존재

---

## 3.10 `normalized_macro_series`

### 목적
개별 macro 시계열의 normalized long-format 저장.

### Primary Key
- `(series_id, base_date)`

| 컬럼 | 의미 | 예시 |
|---|---|---|
| series_id | 시계열 ID | DGS3, DGS10 |
| base_date | 기준일 | 2026-04-30 |
| value | 값 | 3.91 |
| available_at | 사용 가능 시각 | timestamp |
| updated_at | 갱신 시각 | timestamp |

### 주요 series_id

| series_id | 의미 | 단위 | 원천 |
|---|---|---|---|
| DGS3 | 미국 3년 국채금리 | % | FRED |
| DGS10 | 미국 10년 국채금리 | % | FRED |
| KR_GOVT_10Y | 한국 10년 국채금리 | % | ECOS 등 |
| USDKRW | 원/달러 환율 | 원 | ECOS/외부 |

---

## 3.11 `feature_store_daily`

### 목적
퀀트 피처 저장소. 가격·수급·변동성·모멘텀 등 파생 지표를 long format으로 저장한다.

### Primary Key
- `(symbol, base_date, feature_name)`

| 컬럼 | 의미 | 예시 |
|---|---|---|
| symbol | 종목코드 또는 GLOBAL | 005930, GLOBAL |
| base_date | 기준일 | 2026-05-04 |
| feature_name | 피처명 | volume, return_5d, rsi_14 |
| feature_value | 피처값 | 숫자 |
| available_at / updated_at | 시각 | timestamp |

### 사용 예
- `return_5d`: 5일 수익률
- `moving_avg_20`: 20일 이동평균
- `foreign_flow_zscore`: 외국인 수급 강도 z-score
- `volume_ratio`: 거래량 상대 강도
- `volatility_20d`: 20일 변동성
- `rsi_14`: 14일 RSI

### 주의사항
- 피처별 단위와 산식은 별도 문서화가 필요하다.
- 아직 일부 피처는 미적재 가능성이 있다.

---

## 3.12 `pipeline_run_logs`

### 목적
파이프라인 실행 이력 관리.

| 컬럼 | 의미 |
|---|---|
| run_id | 실행 UUID |
| job_name | 파이프라인명 |
| target_date | 대상 기준일 |
| status | SUCCESS, WARN, FAIL 등 |
| start_time | 시작 시각 |
| end_time | 종료 시각 |
| records_processed | 처리 건수 |
| error_message | 오류 메시지 |

### 사용처
- report data quality guardrail
- 최근 실패 job 확인
- 특정 날짜 재실행 판단

---

## 3.13 `normalized_stock_supply_daily`

### 목적
종목별 투자자 수급 데이터 저장.

### Primary Key
- `(symbol, base_date)`

| 컬럼 | 의미 | 단위 | 주의사항 |
|---|---|---|---|
| symbol | 종목코드 | 문자열 | canonical |
| base_date | 기준일 | 날짜 |  |
| foreign_net_buy | 외국인 순매수 | 주 | KIS `frgn_ntby_qty`, 원화 아님 |
| institutional_net_buy | 기관 순매수 | 주 | KIS `orgn_ntby_qty`, 원화 아님 |
| individual_net_buy | 개인 순매수 | 주 | KIS `prsn_ntby_qty`, 원화 아님 |
| pension_net_buy | 연기금 순매수 | 주 | KIS `pnsn_ntby_qty`, 원화 아님 |
| corporate_net_buy | 기타법인 순매수 | 주 | KIS `etc_corp_ntby_qty`, 원화 아님 |
| foreign_holding_ratio | 외국인 보유율 | % | 누적 보유 비율 |
| available_at / updated_at | 시각 | timestamp |  |

### 중요
수급 net_buy 컬럼은 금액이 아니라 **수량(주)** 기준이다. 금액처럼 표시하지 않는다.

---

## 3.14 `normalized_stock_short_selling`

### 목적
종목별 공매도 데이터 저장.

### Primary Key
- `(symbol, base_date)`

| 컬럼 | 의미 | 단위 | 주의사항 |
|---|---|---|---|
| symbol | 종목코드 | 문자열 |  |
| base_date | 기준일 | 날짜 |  |
| short_volume | 공매도 수량 | 주 | KIS/KRX 원천별 차이 가능 |
| short_value | 공매도 금액 | 원 |  |
| short_ratio | 공매도 비중 | % | 없거나 0이면 해석 제한 |
| source | 원천 | KIS/KRX |  |
| available_at / updated_at | 시각 | timestamp |  |

---

## 3.15 `normalized_stock_fundamentals`

### 목적
종목별 재무제표성 기초값 저장.

| 컬럼 | 의미 | 단위 |
|---|---|---|
| symbol | 종목코드 | 문자열 |
| base_date | 기준일 | 날짜 |
| revenue | 매출액 | 원 |
| operating_income | 영업이익 | 원 |
| net_income | 순이익 | 원 |
| total_assets | 총자산 | 원 |
| total_liabilities | 총부채 | 원 |
| total_equity | 총자본 | 원 |
| source | 원천 | API/계산 |
| available_at / updated_at | 시각 | timestamp |

### 주의사항
- 0은 실제 0인지 수집 실패인지 구분 필요.
- 수집 실패 시 0보다 null이 바람직하다.

---

## 3.16 `normalized_stock_fundamentals_ratios`

### 목적
PER/PBR/ROE/부채비율 등 밸류에이션·재무비율 저장.

| 컬럼 | 의미 | 단위 | 해석 주의사항 |
|---|---|---|---|
| symbol | 종목코드 | 문자열 |  |
| base_date | 기준일 | 날짜 |  |
| per | 주가수익비율 | 배 | 업종·이익 사이클 고려 필요 |
| pbr | 주가순자산비율 | 배 | 금융/제조/성장주별 해석 다름 |
| roe | 자기자본이익률 | % | 0값 대량 발생 시 수집 오류 가능 |
| debt_ratio | 부채비율 | % | 업종별 기준 다름 |
| source | 원천 | API/계산 |  |
| available_at / updated_at | 시각 | timestamp |  |

---

## 3.17 `market_breadth_daily`

### 목적
시장 폭 지표 저장. 상승/하락 종목 수와 거래량 기반 시장 내부 체력을 본다.

| 컬럼 | 의미 | 해석 |
|---|---|---|
| base_date | 기준일 | 날짜 |
| advances | 상승 종목 수 | 시장 확산 강도 |
| declines | 하락 종목 수 | 약세 확산 강도 |
| unchanged | 보합 종목 수 |  |
| advancing_volume | 상승 종목 거래량 합 | 상승 참여 강도 |
| declining_volume | 하락 종목 거래량 합 | 하락 참여 강도 |
| available_at / updated_at | 시각 | timestamp |

---

## 3.18 `normalized_global_macro_daily`

### 목적
리포트가 바로 소비할 수 있는 글로벌/국내 매크로 wide-format 스냅샷.

### Primary Key
- `base_date`

| 컬럼 | 의미 | 단위 | 해석 |
|---|---|---|---|
| base_date | 기준일 | 날짜 | macro 기준일 |
| usdkrw | 원/달러 환율 | 원 | 높을수록 외국인 수급 부담 가능 |
| dxy | 달러인덱스 | index | 글로벌 달러 강도 |
| us10y | 미국 10년 국채금리 | % | 장기금리·할인율 기준 |
| us3y | 미국 3년 국채금리 | % | 중단기 정책금리 기대 반영 |
| kr10y | 한국 10년 국채금리 | % | 국내 장기금리 |
| kospi | KOSPI 지수 | index | 국내 대형주 시장 |
| kospi_change_rate | KOSPI 등락률 | % |  |
| kosdaq | KOSDAQ 지수 | index | 성장주/중소형 시장 |
| kosdaq_change_rate | KOSDAQ 등락률 | % |  |
| wti | WTI 유가 | USD/bbl | 인플레·에너지 민감도 |
| brent | Brent 유가 | USD/bbl | 글로벌 유가 기준 |
| nasdaq | Nasdaq 지수 | index | 성장주/기술주 심리 |
| nasdaq_change_rate | Nasdaq 등락률 | % |  |
| sp500 | S&P500 지수 | index | 미국 대형주 대표지수 |
| sp500_change_rate | S&P500 등락률 | % |  |
| sox | 필라델피아 반도체 지수 | index | 반도체 섹터 심리 |
| vix | 변동성지수 | index | 위험회피 심리 |
| gold | 금 가격 | USD/oz | 안전자산/실질금리 민감 |
| copper | 구리 가격 | USD | 경기민감 원자재 |
| bdry | 벌크선 운임 관련 지표 | index | 글로벌 물동량 참고 |
| hy_spread | 하이일드 스프레드 | bp/% | 신용위험 지표 |
| kospi_individual_net_buy | KOSPI 개인 순매수 | 원 | 시장별 수급 |
| kospi_foreign_net_buy | KOSPI 외국인 순매수 | 원 | 외국인 자금 방향 |
| kospi_institutional_net_buy | KOSPI 기관 순매수 | 원 | 기관 자금 방향 |
| kosdaq_individual_net_buy | KOSDAQ 개인 순매수 | 원 |  |
| kosdaq_foreign_net_buy | KOSDAQ 외국인 순매수 | 원 |  |
| kosdaq_institutional_net_buy | KOSDAQ 기관 순매수 | 원 |  |
| available_at / updated_at | 시각 | timestamp |  |

### 파생 지표: 미국 10Y-3Y 스프레드

테이블에 직접 저장하지는 않더라도 report 소비 단계에서 계산한다.

```text
us10y_us3y_spread = us10y - us3y
us10y_us3y_spread_bp = (us10y - us3y) * 100
```

해석:
- 양수: 장기금리가 3년물보다 높음. 정상적인 우상향 곡선.
- 0 부근: 경기·정책금리 기대가 엇갈리는 flat 구간.
- 음수: 3년물이 10년물보다 높은 역전 구간. 경기둔화·정책금리 인하 기대 가능성.

주의:
- 장단기 금리차만으로 경기침체를 단정하지 않는다.
- 금리 레벨, 달러, VIX, 주가지수 흐름과 함께 해석한다.

---

## 3.19 `normalized_derivatives_daily`

### 목적
파생시장 지표 저장.

| 컬럼 | 의미 | 해석 |
|---|---|---|
| base_date | 기준일 | 날짜 |
| kospi200_futures | KOSPI200 선물 | 지수 수준 |
| futures_basis | 선물 베이시스 | 현·선물 괴리 |
| open_interest | 미결제약정 | 포지션 누적 강도 |
| night_futures_return | 야간선물 수익률 | 익일 시초 영향 참고 |
| expiration_flag | 만기일 여부 | true/false |
| available_at / updated_at | 시각 | timestamp |

---

## 3.20 `normalized_stock_events_daily`

### 목적
종목별 이벤트·뉴스·공시성 신호 저장.

| 컬럼 | 의미 | 해석 |
|---|---|---|
| symbol | 종목코드 |  |
| base_date | 이벤트 기준일 |  |
| event_type | 이벤트 유형 | earnings, disclosure 등 |
| event_score | 이벤트 강도 | 높을수록 중요 |
| sentiment_score | 감성 점수 | 양수 긍정, 음수 부정 |
| available_at / updated_at | 시각 |  |

---

## 3.21 `market_trading_calendar`

### 목적
한국거래소와 미국 주식시장 거래일/휴장일 캘린더를 일자 단위로 저장한다. 이 테이블은 latest valid price date 선택, ranking fallback, report 기준일 보정, backtest 거래일 계산, 휴장일 수집 skip guardrail에 공통으로 사용한다.

### Primary Key
- `(calendar_date, exchange_code)`

| 컬럼 | 의미 | 예시 | 주의사항 |
|---|---|---|---|
| calendar_date | 거래소 로컬 기준 날짜 | 2026-05-04 | KST 기준 거래일 |
| exchange_code | 거래소 캘린더 코드 | XKRX, XNYS | pandas-market-calendars에서 선택된 calendar name |
| market | 논리 시장명 | KRX, US | XKRX는 한국시장, XNYS/XNAS는 미국시장 |
| is_open | 거래일 여부 | true / false | 거래일이면 true, 주말/휴장일이면 false |
| open_time | 개장 시각 | 2026-05-04T09:00:00+09:00 | 거래일에만 값 존재 가능 |
| close_time | 폐장 시각 | 2026-05-04T15:30:00+09:00 | 거래일에만 값 존재 가능 |
| timezone | 시간대 | Asia/Seoul | 기본값 Asia/Seoul |
| holiday_name | 휴장 명칭 | 어린이날 등 | 패키지가 명칭을 주지 않으면 null |
| reason | 분류 사유 | trading_day, weekend, holiday, missing_schedule | report/backtest에서 휴장 구분에 사용 |
| source | 캘린더 원천 | pandas_market_calendars | 현재 기본 구현 |
| calendar_version | 수집 시 사용한 패키지 버전 | 5.3.2 | holiday 반영 범위 추적용 |
| collected_at | 수집 시각 | timestamp | 파이프라인 실행 시각 |
| updated_at | 갱신 시각 | timestamp | upsert 시 갱신 |

### 갱신 정책
- 월 1회 `monthly_market_calendar_sync.yml`에서 갱신한다.
- 기본 적재 거래소는 `XKRX`, `XNYS`다. `XNAS`는 옵션으로 적재할 수 있다.
- 기본 적재 범위는 실행 연도 1월 1일 ~ 다음 연도 12월 31일이다.
- 수동 실행도 가능하다.

```bash
python -m src.jobs.run_monthly_market_calendar_pipeline --all-exchanges --start-date 2026-01-01 --end-date 2027-12-31
```

### 사용 방식
- `src/utils/trading_calendar.py`가 공통 접근 레이어다.
- `get_previous_trading_day`, `get_next_trading_day`, `get_latest_trading_day_on_or_before` 등으로 report/backtest에서 재사용한다.
- `src/utils/market_data_quality.py`는 latest valid price date 후보를 고를 때 이 테이블 기준으로 비거래일을 우선 제외한다.
- `XKRX`는 한국 주식/ETF/ETN/파생 데이터 수집 여부 판단에 사용한다.
- `XNYS`는 미국 주식시장성 지표(S&P500, Nasdaq, SOX, VIX) 수집 여부 판단에 사용한다.
- FRED/ECOS는 각자 공표 주기가 다르므로 `XNYS` 휴장일이어도 전체 macro 수집을 막지 않는다.
- 휴장일 skip은 오류가 아니라 정상 종료이며 `SKIPPED_MARKET_CLOSED`로 로그를 남긴다.

### 한계
- `pandas-market-calendars`와 내부 `exchange_calendars` 버전에 따라 임시공휴일, 특별휴장 반영 시차가 있을 수 있다.
- 미국시장 특별휴장도 패키지 반영 시점에 따라 지연될 수 있다.
- 테이블이 아직 생성되지 않았거나 비어 있으면 weekday fallback을 사용하며 warning을 남긴다.
- 특별휴장 보정이 필요하면 별도 수동 override 정책이 필요하다.

## 4. 주요 품질 검증 규칙

### 4.1 Symbol 품질

- `symbol ~ '^Q[0-9]{6}$'`이면 실패.
- 모든 한국 종목은 canonical 6자리 코드로 저장한다.
- ETF/ETN도 Q-prefix를 제거한 canonical 코드 사용.

### 4.2 Market 품질

`normalized_market_rankings_daily.market`은 `stocks_master.market`과 일치해야 한다.

예외:
- `KOSPI200`은 별도 ranking market으로 허용 가능.

### 4.3 Price 품질

유효 가격 row 조건:

```sql
close_price is not null
and volume is not null
and trading_value is not null
```

### 4.4 Ranking 품질

- KOSPI volume count > 0
- KOSDAQ volume count > 0
- KOSPI/KOSDAQ trading_value count > 0
- KOSPI/KOSDAQ market_cap count > 0
- ETF/ETN ranking은 KRX ETP API 또는 valid price fallback 기준으로 생성
- q_prefix rows = 0
- market mismatch rows = 0

### 4.5 Macro 품질

- `normalized_global_macro_daily.us10y` 존재
- `normalized_global_macro_daily.us3y` 존재
- `normalized_macro_series.DGS3` 존재
- `MACRO QUALITY = SUCCESS`

---

## 5. 소비자별 사용 가이드

### 5.1 report_stock_daily

| 목적 | 우선 사용 테이블 |
|---|---|
| Top 종목 | normalized_market_rankings_daily |
| 관심종목 | static_stock_universe + normalized_stock_prices_daily |
| 매크로 | normalized_global_macro_daily |
| 장단기 금리차 | normalized_global_macro_daily.us10y/us3y 계산 |
| 수급 | normalized_stock_supply_daily |
| 공매도 | normalized_stock_short_selling |
| 밸류 | normalized_stock_fundamentals_ratios |
| 데이터 품질 | pipeline_run_logs, verify_data.py 결과 |

### 5.2 직접 쿼리 예시

#### 최신 시장별 랭킹

```sql
select
  base_date,
  market,
  rank_type,
  source,
  rank,
  symbol,
  name,
  volume,
  trading_value,
  market_cap
from public.normalized_market_rankings_daily
where base_date = (
  select max(base_date) from public.normalized_market_rankings_daily
)
order by market, rank_type, rank;
```

#### 시장 불일치 점검

```sql
select
  r.market as ranking_market,
  r.rank_type,
  r.source,
  m.market as master_market,
  m.asset_type,
  count(*) as cnt
from public.normalized_market_rankings_daily r
left join public.stocks_master m
  on r.symbol = m.symbol
where r.base_date = (
  select max(base_date) from public.normalized_market_rankings_daily
)
group by r.market, r.rank_type, r.source, m.market, m.asset_type
order by r.market, r.rank_type, r.source, cnt desc;
```

#### 미국 10Y-3Y 스프레드

```sql
select
  base_date,
  us10y,
  us3y,
  (us10y - us3y) * 100 as us10y_us3y_spread_bp
from public.normalized_global_macro_daily
where us10y is not null
  and us3y is not null
order by base_date desc
limit 10;
```

---

## 6. 최근 변경 이력

### 2026-05-05

- KIS volume-rank production path를 `J` only로 고정.
- K/Q market code는 diagnose script에서만 사용.
- KOSPI/KOSDAQ volume 부족 시 `VALID_PRICE_FALLBACK`으로 보강.
- ETF/ETN KRX ETP 데이터 lookback 및 market별 valid price date 적용.
- `us3y` 추가: FRED `DGS3` 기반 미국 3년물.
- `normalized_global_macro_daily.us3y` 컬럼 추가.
- ETF/ETN ranking quality SUCCESS 확인.

---

## 7. 남은 문서화 과제

1. `feature_store_daily.feature_name`별 산식 상세화.
2. 각 collector별 API endpoint와 원본 필드 매핑표 보강.
3. `normalized_stock_fundamentals` 원천과 0/null 처리 정책 명확화.
4. 백테스트용 signal score 산식이 도입되면 별도 문서로 분리.
