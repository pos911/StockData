-- 10단계: SQL 정리 파일 추가
-- 매크로 데이터 검증용 쿼리

-- A. normalized_global_macro_daily 확인:
SELECT
  base_date,
  kospi,
  kospi_change_rate,
  kosdaq,
  kosdaq_change_rate,
  usdkrw,
  dxy,
  sp500,
  nasdaq,
  sox,
  vix,
  wti,
  brent,
  available_at
FROM public.normalized_global_macro_daily
WHERE base_date BETWEEN current_date - interval '7 days' AND current_date
ORDER BY base_date DESC;

-- B. normalized_macro_series USDKRW 확인:
SELECT
  series_id,
  base_date,
  value,
  available_at
FROM public.normalized_macro_series
WHERE series_id IN ('USDKRW', 'DEXKOUS', 'KRW=X')
ORDER BY base_date DESC
LIMIT 30;

-- C. normalized_macro_intraday 확인:
SELECT
  base_date,
  observed_at,
  series_id,
  value,
  change_rate,
  source,
  source_symbol,
  quality_flag,
  quality_detail,
  collected_at
FROM public.normalized_macro_intraday
WHERE base_date BETWEEN current_date - interval '3 days' AND current_date
ORDER BY observed_at DESC, series_id;

-- D. pipeline_run_logs 확인:
SELECT *
FROM public.pipeline_run_logs
WHERE job_name ilike '%macro%'
ORDER BY coalesce(end_time, start_time, target_date::timestamp) DESC
LIMIT 30;
