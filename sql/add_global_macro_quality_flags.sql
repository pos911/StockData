-- 2단계: daily macro 품질 컬럼 SQL 추가
-- normalized_global_macro_daily에 품질 추적 컬럼 추가

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kospi_source') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kospi_source TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kospi_source_date') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kospi_source_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kospi_quality_flag') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kospi_quality_flag TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kosdaq_source') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kosdaq_source TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kosdaq_source_date') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kosdaq_source_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'kosdaq_quality_flag') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN kosdaq_quality_flag TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'usdkrw_source') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN usdkrw_source TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'usdkrw_source_date') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN usdkrw_source_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'usdkrw_quality_flag') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN usdkrw_quality_flag TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'macro_quality_flag') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN macro_quality_flag TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'normalized_global_macro_daily' AND column_name = 'macro_quality_detail') THEN
        ALTER TABLE public.normalized_global_macro_daily ADD COLUMN macro_quality_detail JSONB;
    END IF;
END $$;
