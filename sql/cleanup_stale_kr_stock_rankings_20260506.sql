-- Diagnose stale 2026-05-06 KOSPI/KOSDAQ fallback rankings before cleanup.
SELECT
  r.base_date,
  r.market,
  r.rank_type,
  r.source,
  COALESCE(r.source_base_date::text, r.raw_data->>'price_base_date') AS effective_source_base_date,
  COUNT(*) AS row_count
FROM (
  SELECT
    n.base_date,
    n.source_base_date,
    n.market,
    n.rank_type,
    n.source,
    NULL::jsonb AS raw_data
  FROM public.normalized_market_rankings_daily n
  WHERE n.base_date = DATE '2026-05-06'
    AND n.market IN ('KOSPI', 'KOSDAQ')
    AND n.source = 'VALID_PRICE_FALLBACK'
  UNION ALL
  SELECT
    r.base_date,
    NULL::date AS source_base_date,
    r.market,
    r.rank_type,
    r.source,
    r.raw_data
  FROM public.raw_market_rankings r
  WHERE r.base_date = DATE '2026-05-06'
    AND r.market IN ('KOSPI', 'KOSDAQ')
    AND r.source = 'VALID_PRICE_FALLBACK'
) r
GROUP BY
  r.base_date,
  r.market,
  r.rank_type,
  r.source,
  COALESCE(r.source_base_date::text, r.raw_data->>'price_base_date')
ORDER BY r.market, r.rank_type, r.source;

-- Delete stale Korean stock fallback rankings for 2026-05-06.
DELETE FROM public.normalized_market_rankings_daily
WHERE base_date = DATE '2026-05-06'
  AND market IN ('KOSPI', 'KOSDAQ')
  AND source = 'VALID_PRICE_FALLBACK';

DELETE FROM public.raw_market_rankings
WHERE base_date = DATE '2026-05-06'
  AND market IN ('KOSPI', 'KOSDAQ')
  AND source = 'VALID_PRICE_FALLBACK';
