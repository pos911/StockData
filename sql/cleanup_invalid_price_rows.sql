BEGIN;

-- 1. Before count
SELECT
  COUNT(*) FILTER (WHERE p.close_price IS NULL OR p.close_price <= 0) AS zero_or_null_close_rows,
  COUNT(*) FILTER (WHERE p.source IS NULL OR p.source = '') AS null_source_rows,
  COUNT(*) FILTER (
    WHERE COALESCE(c.is_open, false) = false
  ) AS non_trading_day_rows
FROM public.normalized_stock_prices_daily p
JOIN public.stocks_master m
  ON p.symbol = m.symbol
LEFT JOIN public.market_trading_calendar c
  ON c.calendar_date = p.base_date
 AND c.exchange_code = 'XKRX'
WHERE m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN');

-- 2. Delete invalid normalized rows
DELETE FROM public.normalized_stock_prices_daily p
USING public.stocks_master m
WHERE p.symbol = m.symbol
  AND m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND (
    p.close_price IS NULL
    OR p.close_price <= 0
    OR p.source IS NULL
    OR p.source = ''
  );

-- 3. Delete non-trading day rows
WITH closed_rows AS (
  SELECT p.symbol, p.base_date
  FROM public.normalized_stock_prices_daily p
  JOIN public.stocks_master m
    ON p.symbol = m.symbol
  LEFT JOIN public.market_trading_calendar c
    ON c.calendar_date = p.base_date
   AND c.exchange_code = 'XKRX'
  WHERE m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
    AND COALESCE(c.is_open, false) = false
)
DELETE FROM public.normalized_stock_prices_daily p
USING closed_rows x
WHERE p.symbol = x.symbol
  AND p.base_date = x.base_date;

-- 4. After count
SELECT
  COUNT(*) FILTER (WHERE p.close_price IS NULL OR p.close_price <= 0) AS zero_or_null_close_rows,
  COUNT(*) FILTER (WHERE p.source IS NULL OR p.source = '') AS null_source_rows,
  COUNT(*) FILTER (
    WHERE COALESCE(c.is_open, false) = false
  ) AS non_trading_day_rows
FROM public.normalized_stock_prices_daily p
JOIN public.stocks_master m
  ON p.symbol = m.symbol
LEFT JOIN public.market_trading_calendar c
  ON c.calendar_date = p.base_date
 AND c.exchange_code = 'XKRX'
WHERE m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN');

COMMIT;
NOTIFY pgrst, 'reload schema';
