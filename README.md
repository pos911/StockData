# 주식 / 매크로 데이터 수집 파이프라인

## 소개
본 프로젝트는 한국 주식 종목별 데이터와 종목 무관 매크로 데이터를 분리 수집하여 통합 저장(Supabase Postgres)하는 데이터 파이프라인입니다. 

## 사전 준비 (Prerequisites)
- Python 3.10+
- Supabase 프로젝트(Postgres DB)
- 각 데이터 소스 API 키 (KIS, KRX, FRED 등)

## 설정 방법
1. 프로젝트 루트에 `requirements.txt`에 명시된 패키지 설치
   ```cmd
   pip install -r requirements.txt
   ```
2. `config/api_keys.template.json`을 복사하여 `config/api_keys.json` 생성 후 올바른 키값 기입
   (Git에 올라가지 않도록 이미 `.gitignore`에 등록되어 있습니다.)
3. 필요시 `.env` 파일도 활용 가능합니다. (`api_keys.json`이 우선시됩니다.)
4. `config/stock_universe.json`과 `config/macro_series.json`에서 `enabled: true`인 수집 대상을 관리합니다.

## 실행 순서 (Windows 기반 로컬 환경 예시)

**1) 데이터베이스 스키마 설정**
Supabase 대시보드나 SQL 편집기에서 `sql/supabase_schema.sql`을 실행해 테이블을 생성합니다.

**2) 주식 일간 데이터 파이프라인 실행**
```cmd
python src/jobs/run_daily_stock_pipeline.py
```
이 스크립트는 KIS, KRX, Opendart 등에서 일간 주봉(EOD 기준), 거래대금 등을 수집하여 `raw` 계층에 저장하고 `normalized` 계층으로 변환 후 적재합니다.

**3) 매크로 지표 일간 파이프라인 실행**
```cmd
python src/jobs/run_daily_macro_pipeline.py
```
FRED, Trading Economics에서 거시경제 지표를 수집합니다.

## 후속 확장 (GitHub Actions 자동화)
본 프로젝트는 GitHub Actions를 통해 3시간마다 자동 실행되도록 설정되어 있습니다 (`.github/workflows/daily_sync.yml`). 이를 활성화하려면 GitHub 레포지토리의 **Settings > Secrets and variables > Actions** 항목에 아래 값을 등록해야 합니다:

- `API_KEYS_JSON`: `config/api_keys.json` 파일의 내용을 **전체 복사하여 그대로 붙여넣기** 하시면 됩니다. (개별 키를 일일이 등록할 필요가 없습니다.)

## 성능 최적화 (증분 업데이트)
- **매크로 수집**: API 부하를 줄이기 위해 매 실행 시 최근 7일치 데이터만 수집합니다.
- **지표 계산**: DB 성능을 위해 최근 60일 데이터만 조회하여 이동평균선을 계산합니다.
- **캐싱**: GitHub Actions의 Cache 기능을 이용해 KIS 토큰과 수천 개의 기업 고유번호 데이터를 재사용함으로써 실행 속도를 극대화했습니다.
