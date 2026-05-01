-- 1) Check snapshot-only price rows before cleanup.
SELECT
  base_date,
  COUNT(*) AS snapshot_only_rows
FROM public.normalized_stock_prices_daily
WHERE close_price IS NULL
  AND volume IS NULL
  AND trading_value IS NULL
  AND open_price IS NULL
  AND high_price IS NULL
  AND low_price IS NULL
GROUP BY base_date
ORDER BY base_date DESC;

-- 2) Delete snapshot-only price rows.
DELETE FROM public.normalized_stock_prices_daily
WHERE close_price IS NULL
  AND volume IS NULL
  AND trading_value IS NULL
  AND open_price IS NULL
  AND high_price IS NULL
  AND low_price IS NULL;

-- 3) Confirm cleanup result.
SELECT
  base_date,
  COUNT(*) AS remaining_snapshot_only_rows
FROM public.normalized_stock_prices_daily
WHERE close_price IS NULL
  AND volume IS NULL
  AND trading_value IS NULL
  AND open_price IS NULL
  AND high_price IS NULL
  AND low_price IS NULL
GROUP BY base_date
ORDER BY base_date DESC;
