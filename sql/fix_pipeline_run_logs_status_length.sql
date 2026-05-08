ALTER TABLE public.pipeline_run_logs
ALTER COLUMN status TYPE VARCHAR(50);

COMMENT ON COLUMN public.pipeline_run_logs.status
IS 'Pipeline status such as SUCCESS, WARN, FAIL, SKIPPED_MARKET_CLOSED, SKIPPED_INSUFFICIENT_PRICE_DATA.';

NOTIFY pgrst, 'reload schema';
