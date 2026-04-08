# Data Source Catalog

현재 프로젝트에서 대상하는 데이터 소스에 대한 목록과 활용처 정보입니다.

## 1. 주식 (국내)
- **한국투자증권 (KIS OpenAPI)**: 
  - 인증: `app_key`, `app_secret` 등
  - 용도: 종목의 기본 및 마스터 정보 조회. (추후 실시간 호가/체결 데이터 수집용으로 확장)
- **한국거래소 (KRX Info Data System / 공공 API 연결 구조)**:
  - 용도: 일별 주가(OHLCV), 거래대금, 특정 일자의 상장주식수, 투자자별 수급
- **OpenDart (금융감독원)**:
  - 인증: `api_key`
  - 용도: `corp_code` 매핑 테이블, 공시 리스트, 주요 재무제표 현황 수집
- **네이버 뉴스 (Naver Developers API)**:
  - 인증: `client_id`, `client_secret`
  - 용도: 종목명 뉴스 검색, 주요 이벤트성 기사 스크래핑 보조

## 2. 매크로 (글로벌경제/금리)
- **FRED (Federal Reserve Economic Data)**:
  - 인증: `api_key`
  - 용도: 각종 미국 기준 금리 (예: 10년물 국채 금리), 환율, 경기선행지수 등
- **TradingEconomics**:
  - 인증: `client_key`
  - 용도: 국가별 지표 (예: 한국 기준금리 등 FRED에서 제공이 제한되는 지표 보완)
