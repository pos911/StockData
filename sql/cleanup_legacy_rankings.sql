-- Remove legacy or invalid ranking rows after the KRX master / KIS volume split.
-- Run manually in Supabase SQL Editor if old ranking rows must be cleaned up.

BEGIN;

DELETE FROM public.raw_market_rankings
WHERE symbol ~ '^Q[0-9]{6}$';

DELETE FROM public.normalized_market_rankings_daily
WHERE symbol ~ '^Q[0-9]{6}$';

DELETE FROM public.raw_market_rankings
WHERE source = 'KIS'
  AND rank_type IN ('trading_value', 'market_cap');

DELETE FROM public.normalized_market_rankings_daily
WHERE source = 'KIS'
  AND rank_type IN ('trading_value', 'market_cap');

DELETE FROM public.raw_market_rankings r
USING public.stocks_master m
WHERE r.symbol = m.symbol
  AND r.market <> 'KOSPI200'
  AND r.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND r.market <> m.market;

DELETE FROM public.normalized_market_rankings_daily r
USING public.stocks_master m
WHERE r.symbol = m.symbol
  AND r.market <> 'KOSPI200'
  AND r.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND r.market <> m.market;

COMMIT;
