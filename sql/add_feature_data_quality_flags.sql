ALTER TABLE public.feature_store_daily
ADD COLUMN IF NOT EXISTS data_quality_flag TEXT;

ALTER TABLE public.feature_store_daily
ADD COLUMN IF NOT EXISTS source_consistency_status TEXT;

COMMENT ON COLUMN public.feature_store_daily.data_quality_flag
IS 'Optional quality flag for a feature row, e.g. SOURCE_MIXED or WARN_PRICE_SCALE_ANOMALY.';

COMMENT ON COLUMN public.feature_store_daily.source_consistency_status
IS 'Describes whether the feature calculation used a consistent price source window, e.g. SOURCE_MIXED_5D.';

NOTIFY pgrst, 'reload schema';
