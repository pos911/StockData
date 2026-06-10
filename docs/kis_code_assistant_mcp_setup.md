# KIS Code Assistant MCP 연동 및 장중 수급·옵션 브리핑 적용 계획

이 문서는 StockData 레포에서 한국투자증권 KIS Open API 기반 장중 브리핑 시스템을 확장하기 위한 MCP 연결 방식과 API 탐색 체크리스트를 정리한다.

## 1. 결론

ChatGPT 웹 대화창 안에 외부 MCP 서버를 직접 붙이는 방식은 사용할 수 없다. MCP는 Cursor, Claude Desktop 같은 MCP 클라이언트 앱에서 로컬 서버를 실행하거나 stdio로 실행해 연결하는 구조다.

따라서 이 레포에서는 다음 방식으로 운용한다.

1. Cursor 또는 Claude Desktop에 `KIS Code Assistant MCP`를 연결한다.
2. MCP로 필요한 KIS Open API 명세와 예제 호출을 찾는다.
3. 확인된 endpoint, TR ID, parameter, response field를 StockData의 collector 코드에 고정 구현한다.
4. GitHub Actions 또는 로컬 스케줄러가 09:30, 10:30, 12:00, 14:00, 15:20 장중 스냅샷을 저장한다.
5. 저장된 스냅샷으로 장중 수급·선물·옵션·프로그램·환율 기반 브리핑과 백테스트를 수행한다.

즉, MCP는 런타임 데이터 수집기가 아니라 **API 탐색 및 코드 생성 보조 도구**로 쓴다. 실제 수집은 Python collector가 담당한다.

## 2. 현재 StockData의 KIS 기반 구조

이미 존재하는 핵심 구조는 다음과 같다.

- `config/api_keys.template.json`
  - KIS `app_key`, `app_secret`, `account_no`, `product_code` 템플릿 존재
- `src/collectors/kis/base.py`
  - KIS 인증 헤더, domain, rate limit, retry, Supabase upsert 공통 처리
- `src/collectors/kis/domestic.py`
  - 국내 주식·수급 계열 collector
- `src/collectors/kis/derivatives.py`
  - 국내 선물/옵션 가격 collector 기초 구현
- `docs/api_ingestion_spec.md`
  - 현재 적재 테이블, 수집 경로, KIS endpoint/TR ID 일부 정리

현재 일별 파이프라인은 가능하나, 장중 옵션 브리핑용으로는 다음 데이터가 추가 필요하다.

## 3. 장중 브리핑에 필요한 데이터와 우선순위

### 3.1 최우선 수집 대상

| 우선순위 | 데이터 | 목적 | 필요 주기 |
|---:|---|---|---|
| 1 | KOSPI/KOSDAQ/KOSPI200/KOSDAQ150 현재 지수 | 시장 방향성 | 09:30/10:30/12:00/14:00/15:20 |
| 2 | 시장별 개인/외국인/기관 순매수 | 현물 수급 판단 | 동일 |
| 3 | KOSPI200 선물 외국인/기관/개인 순매수 | 지수 방향 핵심 | 동일 |
| 4 | 프로그램 매매 전체/차익/비차익 | 대형주 바스켓 압력 | 동일 |
| 5 | KOSPI200 콜/풋 옵션 투자자별 순매수 | 하방/상방 헤지 판단 | 동일 |
| 6 | 위클리 옵션 콜/풋 투자자별 순매수 | 단기 변동성·꼬리위험 | 동일 |
| 7 | 삼성전자·SK하이닉스 외국계 순매수/회원사 동향 | 대형 반도체 압력 | 동일 |
| 8 | USD/KRW 환율 | 외국인 자금·원화 리스크 | 동일 |
| 9 | KSVKOSPI/VKOSPI | 공포·변동성 regime | 동일 |
| 10 | 옵션 체인, 행사가별 거래량·미결제약정 | 감마/만기 방어선 추정 | 12:00/15:20 우선 |

### 3.2 백테스트용 라벨

각 스냅샷 시점마다 다음 outcome을 저장한다.

| 라벨 | 설명 |
|---|---|
| `kospi_return_to_close` | 스냅샷 시점 대비 코스피 종가 수익률 |
| `kosdaq_return_to_close` | 스냅샷 시점 대비 코스닥 종가 수익률 |
| `kospi200_return_to_close` | KOSPI200 종가 방향 |
| `samsung_return_to_close` | 삼성전자 종가 방향 |
| `next_day_kospi_return` | 익일 코스피 종가 수익률 |
| `intraday_max_drawdown_after_snapshot` | 스냅샷 후 최대 하락폭 |
| `intraday_rebound_after_snapshot` | 스냅샷 후 종가까지 반등폭 |
| `kosdaq_outperform_kospi` | 코스닥 상대강도 |

## 4. KIS Code Assistant MCP 설치

### 4.1 공통 준비

```bash
git clone https://github.com/koreainvestment/open-trading-api.git
cd "open-trading-api/MCP/KIS Code Assistant MCP"
uv sync
```

Windows에서는 `where uv`로 uv 경로를 확인한다.

### 4.2 Cursor 연결

터미널에서 서버 실행:

```bash
cd "open-trading-api/MCP/KIS Code Assistant MCP"
uv run server.py
```

Cursor 설정 예시:

```json
{
  "mcpServers": {
    "kis-code-assistant-mcp": {
      "url": "http://localhost:8081/mcp"
    }
  }
}
```

Docker 사용 시:

```bash
cd "open-trading-api/MCP/KIS Code Assistant MCP"
docker build -t kis-code-assistant-mcp .
docker run -d -p 8081:8081 --name kis-code-assistant-mcp kis-code-assistant-mcp
curl http://localhost:8081/health
```

### 4.3 Claude Desktop 연결

`claude_desktop_config.json` 예시:

```json
{
  "mcpServers": {
    "kis-code-assistant-mcp": {
      "command": "{uv 실행 경로}",
      "args": [
        "--directory",
        "{프로젝트 폴더 경로}/MCP/KIS Code Assistant MCP",
        "run",
        "server.py",
        "--stdio"
      ]
    }
  }
}
```

## 5. MCP에 던질 API 탐색 프롬프트

아래 프롬프트를 순서대로 실행해서 endpoint, TR ID, parameter, response field를 확정한다.

### 5.1 시장 지수·현물 수급

```text
KOSPI, KOSDAQ, KOSPI200, KOSDAQ150 현재 지수와 등락률을 조회하는 KIS Open API를 찾아줘. Python 호출 예제와 응답 필드도 알려줘.
```

```text
시장별 투자자매매동향 시세 API 찾아줘. KOSPI, KOSDAQ의 개인, 외국인, 기관 순매수 금액을 장중에 가져오고 싶어. endpoint, TR ID, 필수 파라미터, 응답 필드를 알려줘.
```

### 5.2 프로그램 매매

```text
프로그램매매 종합현황 시간 API 찾아줘. 전체, 차익, 비차익 순매수 금액을 장중 스냅샷으로 가져오고 싶어. Python 예제까지 만들어줘.
```

```text
프로그램매매 투자자매매동향 당일 API 찾아줘. 외국인/기관/개인별 프로그램 매매를 조회할 수 있는지 확인해줘.
```

### 5.3 선물·옵션 수급

```text
KOSPI200 선물 현재가, 분봉, 거래량, 미결제약정을 조회하는 API를 찾아줘.
```

```text
KOSPI200 선물 투자자별 매매동향 또는 순매수 조회 API가 있는지 찾아줘. 개인, 외국인, 기관별 장중 순매수를 얻고 싶어.
```

```text
KOSPI200 콜옵션/풋옵션 투자자별 순매수 조회 API가 있는지 찾아줘. 위클리 옵션도 가능한지 확인해줘.
```

```text
국내옵션전광판 콜풋 API 찾아줘. 행사가별 콜/풋 가격, 거래량, 미결제약정을 가져오고 싶어.
```

### 5.4 삼성전자·대형주 수급

```text
삼성전자 외국계 순매수 추이 API 찾아줘. 종목코드 005930 기준으로 장중 외국계합을 가져오고 싶어.
```

```text
회원사 실시간 매매동향 API 예제 만들어줘. 삼성전자 매수/매도 상위 회원사와 수량을 가져오고 싶어.
```

### 5.5 환율·변동성

```text
원달러 환율 현재가 또는 USD/KRW 장중 시세 API 찾아줘. KIS Open API에서 가능한지 확인하고 Python 예제를 만들어줘.
```

```text
VKOSPI 또는 KOSPI 변동성 지수 현재가 조회가 KIS Open API로 가능한지 찾아줘. 가능하면 지수 코드와 호출 예제를 알려줘.
```

## 6. API 확정 후 구현할 파일 구조

권장 추가 파일:

```text
src/collectors/kis/intraday_market.py
src/collectors/kis/intraday_program.py
src/collectors/kis/intraday_derivatives_flow.py
src/collectors/kis/intraday_options_chain.py
src/collectors/kis/intraday_member_flow.py
src/jobs/run_intraday_market_snapshot.py
src/features/intraday_market_features.py
src/reports/intraday_market_briefing.py
sql/create_intraday_market_snapshot.sql
```

## 7. 저장 테이블 초안

```sql
create table if not exists intraday_market_snapshot (
  id bigserial primary key,
  snapshot_at timestamptz not null,
  trade_date date not null,
  slot text not null,

  kospi numeric,
  kospi_change_rate numeric,
  kosdaq numeric,
  kosdaq_change_rate numeric,
  kospi200 numeric,
  kospi200_change_rate numeric,
  kosdaq150 numeric,
  kosdaq150_change_rate numeric,

  kospi_individual_net_buy numeric,
  kospi_foreign_net_buy numeric,
  kospi_institution_net_buy numeric,
  kosdaq_individual_net_buy numeric,
  kosdaq_foreign_net_buy numeric,
  kosdaq_institution_net_buy numeric,

  futures_foreign_net_buy numeric,
  futures_institution_net_buy numeric,
  futures_individual_net_buy numeric,

  call_foreign_net_buy numeric,
  call_institution_net_buy numeric,
  call_individual_net_buy numeric,
  put_foreign_net_buy numeric,
  put_institution_net_buy numeric,
  put_individual_net_buy numeric,

  program_total_net_buy numeric,
  program_arbitrage_net_buy numeric,
  program_non_arbitrage_net_buy numeric,

  samsung_price numeric,
  samsung_change_rate numeric,
  samsung_foreign_broker_net_buy numeric,

  usdkrw numeric,
  ksvkospi numeric,

  raw jsonb,
  created_at timestamptz default now(),

  unique (trade_date, slot)
);
```

## 8. 브리핑 점수 초안

| 조건 | 점수 |
|---|---:|
| 외국인 코스피 현물 순매수 강함 | +2 |
| 외국인 코스피 현물 순매도 강함 | -2 |
| 외국인 KOSPI200 선물 순매수 강함 | +3 |
| 외국인 KOSPI200 선물 순매도 강함 | -3 |
| 비차익 프로그램 순매수 강함 | +2 |
| 비차익 프로그램 순매도 강함 | -2 |
| 외국인 풋 매수 + 콜 매도 | -2 |
| 외국인 콜 매수 + 풋 매도 | +2 |
| 환율 상승 | -1 |
| 환율 급등 | -2 |
| KSVKOSPI 하락 | +1 |
| KSVKOSPI 상승 | -1 |
| 코스닥 외국인+기관 동반 매수 | +1 |
| 개인만 코스피 대량 순매수 | -1 |

점수 분류:

| 점수 | 장세판단 |
|---:|---|
| +5 이상 | 상방 우위 |
| +2 ~ +4 | 반등 가능 |
| -1 ~ +1 | 중립/혼조 |
| -2 ~ -4 | 하방 경계 |
| -5 이하 | 급락 위험 |

## 9. 실행 우선순위

1. MCP로 API 존재 여부 확인
2. 시장별 현물 수급 + 프로그램 매매부터 구현
3. 선물 수급 구현
4. 옵션 전광판 및 옵션 투자자별 수급 구현
5. 삼성전자 외국계/회원사 동향 구현
6. 환율·KSVKOSPI 보완
7. 장중 스냅샷 저장
8. 장중 브리핑 markdown 생성
9. 1개월 데이터 축적 후 rule score 백테스트
10. 3~6개월 데이터 축적 후 통계 모델 검증

## 10. 주의사항

- MCP 검색 결과는 개발 보조용이다. 실제 운영 코드는 endpoint/TR ID/field를 고정하고 테스트해야 한다.
- KIS API rate limit, 장 운영시간, 휴장일, 실전/모의 domain 차이를 반드시 반영한다.
- 장중 특정 시각 과거 데이터는 나중에 복원하기 어렵다. 따라서 스냅샷 저장을 먼저 시작하는 것이 중요하다.
- 옵션 데이터는 방향성 베팅, 헤지, 변동성 매매가 섞여 있으므로 단독 예측 지표로 쓰지 않는다.
- 최종 판단은 외국인 선물, 비차익 프로그램, 환율, 변동성, 대형주 외국계 수급을 결합해 산출한다.
