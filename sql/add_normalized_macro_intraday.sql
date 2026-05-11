-- 1단계: 신규 SQL 추가
-- public.normalized_macro_intraday 테이블 생성

CREATE TABLE IF NOT EXISTS public.normalized_macro_intraday (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    base_date DATE NOT NULL,
    series_id TEXT NOT NULL,
    value NUMERIC,
    change_rate NUMERIC,
    source TEXT,
    source_symbol TEXT,
    market TEXT,
    quality_flag TEXT DEFAULT 'OK',
    quality_detail JSONB,
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(observed_at, series_id, source)
);

CREATE INDEX IF NOT EXISTS idx_macro_intraday_base_series_time 
ON public.normalized_macro_intraday (base_date, series_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_macro_intraday_series_time 
ON public.normalized_macro_intraday (series_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_macro_intraday_quality 
ON public.normalized_macro_intraday (quality_flag);
