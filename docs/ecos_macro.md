# ECOS Macro Ingestion Guide

## 목적

ECOS 수집은 한국은행 경제통계시스템을 이용해 국내 금리와 환율을 일별 기준으로 적재하기 위한 경로입니다.

기존에는 `normalized_global_macro_daily.kr10y`를 FRED 월간 시리즈 `IRLTLT01KRM156N`으로 채웠지만, 이제는 ECOS 일별 `KR_GOVT_10Y`를 우선 사용합니다.

## API 키 설정

로컬:

- `config/api_keys.json`
- 섹션: `ecos.api_key`

예시:

```json
{
  "ecos": {
    "api_key": "YOUR_ECOS_API_KEY_HERE"
  }
}
```

GitHub Actions:

- `API_KEYS_JSON` secret 안에 같은 `ecos` 섹션을 포함하면 됩니다.

## 수집 지표 목록

정의 파일:

- `config/ecos_series.json`

현재 수집 대상:

- `KR_CALL_RATE`
- `KR_GOVT_1Y`
- `KR_GOVT_3Y`
- `KR_GOVT_5Y`
- `KR_GOVT_10Y`
- `KR_GOVT_20Y`
- `KR_GOVT_30Y`
- `KR_CORP_AA_3Y`
- `KR_CORP_BBB_3Y`
- `KR_CD_91D`
- `KR_CP_91D`
- `USDKRW`
- `JPYKRW`
- `CNYKRW`

## ECOS URL 구조

StatisticSearch 기본 형식:

```text
https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/{start}/{end}/{stat_code}/{cycle}/{start_date}/{end_date}/{item_code1}/?/?/?
```

예시:

```text
https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/5/817Y002/D/20240401/20240410/010210000/?/?/?
```

주요 응답 위치:

- `StatisticSearch.list_total_count`
- `StatisticSearch.row`
- `row[].TIME`
- `row[].DATA_VALUE`
- `row[].UNIT_NAME`
- `row[].ITEM_NAME1`

## 실제 운영 코드 기준 교정 사항

요청 초안에는 금리 표가 `060Y001`, cycle이 `DD`로 적혀 있었지만, ECOS 실응답 검증 결과 운영 가능한 값은 아래와 같습니다.

- 금리 표: `817Y002`
- 금리 cycle: `D`
- 환율 표: `731Y001`
- 환율 cycle: `D`

즉 `config/ecos_series.json`은 문서 초안보다 실제 응답 기준으로 교정되어 있습니다.

## 적재 레이어

Raw:

- `raw_ecos_macro_daily`

Normalized:

- `normalized_macro_series`

Master:

- `macro_series_master`

Global daily snapshot:

- `normalized_global_macro_daily.kr10y`
- `normalized_global_macro_daily.usdkrw`는 ECOS `USDKRW`를 우선 사용

## KR10Y가 FRED에서 ECOS로 바뀐 이유

기존 FRED `IRLTLT01KRM156N`은 월간 OECD 계열 시리즈라서 당일 채권시장 금리 변화 추적에는 맞지 않았습니다.

이제는:

- `KR_GOVT_10Y` from ECOS = canonical
- `IRLTLT01KRM156N` from FRED = backup only

로 해석하면 됩니다.

## 파생 지표

`feature_store_daily`에 추가되는 주요 ECOS 기반 피처:

- `KR_YIELD_SPREAD_10Y_3Y`
- `KR_CREDIT_SPREAD_AA_3Y`
- `KR_CREDIT_SPREAD_BBB_3Y`
- `USDKRW_1D_CHG_PCT`
- `KR10Y_1D_CHG_BP`
- `KR10Y_20D_CHG_BP`

의미:

- `KR_YIELD_SPREAD_10Y_3Y`: 장단기 금리차
- `KR_CREDIT_SPREAD_AA_3Y`: 회사채 AA-와 국고채 3년 스프레드
- `KR_CREDIT_SPREAD_BBB_3Y`: 회사채 BBB-와 국고채 3년 스프레드
- `USDKRW_1D_CHG_PCT`: 원달러 환율 일간 변화율
- `KR10Y_1D_CHG_BP`: 국고채 10년 금리의 하루 변화폭
- `KR10Y_20D_CHG_BP`: 국고채 10년 금리의 20거래일 변화폭

## 실행 방법

단일 시리즈:

```bash
python -m src.pipelines.collect_ecos_macro --series KR_GOVT_10Y --start 20240101 --end 20240131
```

전체 시리즈 최근 30일:

```bash
python -m src.pipelines.collect_ecos_macro --all --days 30
```

기본 동작:

- 인자를 주지 않으면 각 series별로 마지막 적재일 다음 날짜부터 오늘까지 증분 수집합니다.

## 장애 시 확인 포인트

로그에서 먼저 볼 것:

- `daily_ecos_macro_pipeline`
- 실패한 `series_id`
- ECOS `RESULT.CODE`

재실행 예시:

```bash
python -m src.pipelines.collect_ecos_macro --series KR_GOVT_10Y --days 30
python src/jobs/run_daily_macro_pipeline.py --date 20260424
python src/jobs/run_daily_feature_pipeline.py --date 20260424
```
