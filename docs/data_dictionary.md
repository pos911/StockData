# Data Dictionary

## 공통 컬럼
| 컬럼명 | 타입 | 설명 |
|---|---|---|
| `base_date` | `DATE` | 지표가 실제 가리키는 기준 날짜. 주식 데이터의 경우 거래일 |
| `collected_at` | `TIMESTAMP` | 크롤러 또는 API 연동 시스템이 해당 데이터를 DB에 `insert/upsert`한 시간 |
| `available_at` | `TIMESTAMP` | 백테스트 시점에서 데이터가 룩어헤드 편향(Look-ahead bias) 없이 사용 가능해지는 시점. 보통 장 마감 후 또는 공시 시간 |

## Normalized Tables
- **`normalized_stock_prices_daily`**
  - `open_price`, `high_price`, `low_price`, `close_price` (보정/수정 주가가 아닌 원시 체결가를 뜻하며, 별도 주가 액면분할 시 환산 로직은 Feature 계층에서 처리)
  - `volume`: 거래량 (주 수)
  - `trading_value`: 거래대금 (원)
  - `market_cap`: 시가총액 (원)
  - `outstanding_shares`: 상장주식수

- **`normalized_macro_series`**
  - `series_id`: 메타테이블과 매핑되는 고유 지표 ID (예: `DGS10`)
  - `value`: 숫자형 지표값 (예: % 수익률 또는 지수 값)
