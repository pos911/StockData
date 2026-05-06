ALTER TABLE public.normalized_market_rankings_daily
ADD COLUMN IF NOT EXISTS source_base_date DATE;

COMMENT ON COLUMN public.normalized_market_rankings_daily.source_base_date
IS 'Actual source data date used to calculate the ranking. For VALID_PRICE_FALLBACK this may differ from base_date.';

NOTIFY pgrst, 'reload schema';
