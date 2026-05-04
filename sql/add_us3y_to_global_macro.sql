ALTER TABLE public.normalized_global_macro_daily
ADD COLUMN IF NOT EXISTS us3y NUMERIC;

COMMENT ON COLUMN public.normalized_global_macro_daily.us3y
IS 'US 3-Year Treasury Constant Maturity Rate from FRED DGS3';

NOTIFY pgrst, 'reload schema';
