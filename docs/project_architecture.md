# Project Architecture

본 데이터 파이프라인의 아키텍처는 데이터 엔지니어링의 표준 계층 모델(Medallion Architecture와 유사)을 차용하여 구축되었습니다.

## 1. 아키텍처 계층

- **Raw Layer (`raw_*` tables)**: 다양한 API와 소스에서 얻은 데이터를 가공 없이 그대로 담아둡니다. 데이터 유실을 방지하고 추후 정제 로직이 변경되었을 때 원본 시점에서 재처리(Re-play) 가능하게 합니다.
- **Normalized Layer (`normalized_*` tables)**: 각기 다른 형식의 날짜 체계, 통화 단위, 종목 코드 포맷 등을 표준화하는 계층입니다. 비즈니스 로직에 맞춰 사용할 수 있도록 정제된 형태를 가집니다.
- **Feature Layer (`feature_store_daily`)**: ML 모델 또는 분석의 입력으로 쓰기 위한 파생 변수 및 결합 지표 계층입니다. 현재는 초기 구조로 설계되었으며 향후 파생 데이터 계산 로직이 추가됩니다.

## 2. 룩어헤드 방지(Look-ahead Bias Prevention)
모든 데이터 테이블은 `base_date` (해당 지표가 가리키는 기준 시점)와 함께 반드시 **`available_at`** 필드를 저장합니다.
- `available_at`는 해당 데이터가 실제로 시장참여자(파이프라인)에게 알려진(Publish) 시점입니다. 이를 기준으로 백테스트 시점에서 실제 활용 가능한 데이터만 쿼리하도록 강제합니다.

## 3. 모듈 구성 매핑
- **Config**: 데이터 소스와 종목 및 API의 메타 정보 관리
- **Collectors (`src/collectors`)**: 외부 API 호출, Rate limit 처리, Raw 데이터 반환
- **Normalizers (`src/normalizers`)**: Raw 데이터를 읽어 표준 스키마 형태로 매핑/변환
- **Loaders (`src/loaders`)**: Supabase로의 IO (Insert/Upsert 로직, 네트워크 에러 Retry 공통화)
- **Jobs (`src/jobs`)**: 전체 데이터 흐름을 오케스트레이션하여 실행하는 메인 엔트리

이 모듈들은 서로 독립적이며 확장 가능하도록 설계되어 이후 추가 데이터 소스가 생길 경우 `collectors`에 새 모듈을 추가하기만 하면 됩니다.
