-- Fix normalized_macro_intraday upsert key
--
-- Problem:
--   The hourly intraday macro job upserts KOSPI, KOSDAQ, USDKRW, DXY, SP500,
--   NASDAQ, SOX, VIX, WTI, and BRENT snapshots into the same table.
--   Multiple series can legitimately share the same observed_at timestamp, so
--   observed_at alone must never be unique.
--
-- Required conflict target used by SupabaseLoader:
--   (observed_at, series_id, source)

ALTER TABLE IF EXISTS public.normalized_macro_intraday
    DROP CONSTRAINT IF EXISTS normalized_macro_intraday_observed_at_key;

ALTER TABLE IF EXISTS public.normalized_macro_intraday
    DROP CONSTRAINT IF EXISTS normalized_macro_intraday_observed_at_series_id_source_key;

DROP INDEX IF EXISTS public.normalized_macro_intraday_observed_at_key;
DROP INDEX IF EXISTS public.normalized_macro_intraday_observed_at_series_id_source_key;

ALTER TABLE IF EXISTS public.normalized_macro_intraday
    ADD CONSTRAINT normalized_macro_intraday_observed_at_series_id_source_key
    UNIQUE (observed_at, series_id, source);

CREATE INDEX IF NOT EXISTS idx_macro_intraday_base_series_time
    ON public.normalized_macro_intraday (base_date, series_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_macro_intraday_series_time
    ON public.normalized_macro_intraday (series_id, observed_at DESC);
