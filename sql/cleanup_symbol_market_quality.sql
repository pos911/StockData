-- Canonicalize Q-prefixed numeric symbols without discarding stronger rows.
-- Run manually in Supabase SQL Editor.

BEGIN;

-- 1. normalized_stock_prices_daily: prefer rows with valid close/volume/trading_value,
-- then larger trading_value, then larger volume.
WITH q_rows AS (
    SELECT
        symbol,
        SUBSTRING(symbol FROM 2) AS canonical_symbol,
        base_date,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        trading_value,
        market_cap,
        outstanding_shares,
        available_at,
        updated_at,
        (
            CASE WHEN close_price IS NOT NULL AND volume IS NOT NULL AND trading_value IS NOT NULL THEN 1000000000 ELSE 0 END
            + COALESCE(trading_value, 0)
            + COALESCE(volume, 0) / 1000000.0
        ) AS quality_score
    FROM public.normalized_stock_prices_daily
    WHERE symbol ~ '^Q[0-9]{6}$'
),
better_q_rows AS (
    SELECT q.*
    FROM q_rows q
    JOIN public.normalized_stock_prices_daily c
      ON c.symbol = q.canonical_symbol
     AND c.base_date = q.base_date
    WHERE q.quality_score >
          (
              CASE WHEN c.close_price IS NOT NULL AND c.volume IS NOT NULL AND c.trading_value IS NOT NULL THEN 1000000000 ELSE 0 END
              + COALESCE(c.trading_value, 0)
              + COALESCE(c.volume, 0) / 1000000.0
          )
)
UPDATE public.normalized_stock_prices_daily c
SET open_price = q.open_price,
    high_price = q.high_price,
    low_price = q.low_price,
    close_price = q.close_price,
    volume = q.volume,
    trading_value = q.trading_value,
    market_cap = COALESCE(q.market_cap, c.market_cap),
    outstanding_shares = COALESCE(q.outstanding_shares, c.outstanding_shares),
    available_at = COALESCE(q.available_at, c.available_at),
    updated_at = CURRENT_TIMESTAMP
FROM better_q_rows q
WHERE c.symbol = q.canonical_symbol
  AND c.base_date = q.base_date;

INSERT INTO public.normalized_stock_prices_daily (
    symbol, base_date, open_price, high_price, low_price, close_price, volume,
    trading_value, market_cap, outstanding_shares, available_at, updated_at
)
SELECT
    canonical_symbol, base_date, open_price, high_price, low_price, close_price, volume,
    trading_value, market_cap, outstanding_shares, available_at, CURRENT_TIMESTAMP
FROM q_rows q
WHERE NOT EXISTS (
    SELECT 1
    FROM public.normalized_stock_prices_daily c
    WHERE c.symbol = q.canonical_symbol
      AND c.base_date = q.base_date
);

DELETE FROM public.normalized_stock_prices_daily
WHERE symbol ~ '^Q[0-9]{6}$';

-- 2. raw tables: keep canonical row on conflict, otherwise rewrite symbol.
UPDATE public.raw_stock_prices_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.raw_stock_prices_daily c
      WHERE c.source = q.source
        AND c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.raw_stock_prices_daily
WHERE symbol ~ '^Q[0-9]{6}$';

WITH q_rows AS (
    SELECT *
    FROM public.normalized_stock_supply_daily
    WHERE symbol ~ '^Q[0-9]{6}$'
)
UPDATE public.normalized_stock_supply_daily c
SET foreign_net_buy = COALESCE(c.foreign_net_buy, q.foreign_net_buy),
    institutional_net_buy = COALESCE(c.institutional_net_buy, q.institutional_net_buy),
    individual_net_buy = COALESCE(c.individual_net_buy, q.individual_net_buy),
    pension_net_buy = COALESCE(c.pension_net_buy, q.pension_net_buy),
    corporate_net_buy = COALESCE(c.corporate_net_buy, q.corporate_net_buy),
    foreign_holding_ratio = COALESCE(c.foreign_holding_ratio, q.foreign_holding_ratio),
    available_at = COALESCE(c.available_at, q.available_at),
    updated_at = CURRENT_TIMESTAMP
FROM q_rows q
WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
  AND c.base_date = q.base_date;

UPDATE public.normalized_stock_supply_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.normalized_stock_supply_daily c
      WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.normalized_stock_supply_daily
WHERE symbol ~ '^Q[0-9]{6}$';

UPDATE public.raw_stock_supply_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.raw_stock_supply_daily c
      WHERE c.source = q.source
        AND c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.raw_stock_supply_daily
WHERE symbol ~ '^Q[0-9]{6}$';

WITH q_rows AS (
    SELECT *
    FROM public.normalized_stock_short_selling
    WHERE symbol ~ '^Q[0-9]{6}$'
)
UPDATE public.normalized_stock_short_selling c
SET short_volume = COALESCE(c.short_volume, q.short_volume),
    short_value = COALESCE(c.short_value, q.short_value),
    short_ratio = COALESCE(c.short_ratio, q.short_ratio),
    source = COALESCE(c.source, q.source),
    available_at = COALESCE(c.available_at, q.available_at),
    updated_at = CURRENT_TIMESTAMP
FROM q_rows q
WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
  AND c.base_date = q.base_date;

UPDATE public.normalized_stock_short_selling q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.normalized_stock_short_selling c
      WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.normalized_stock_short_selling
WHERE symbol ~ '^Q[0-9]{6}$';

UPDATE public.raw_stock_short_selling q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.raw_stock_short_selling c
      WHERE c.source = q.source
        AND c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.raw_stock_short_selling
WHERE symbol ~ '^Q[0-9]{6}$';

WITH q_rows AS (
    SELECT *
    FROM public.normalized_stock_snapshots_daily
    WHERE symbol ~ '^Q[0-9]{6}$'
)
UPDATE public.normalized_stock_snapshots_daily c
SET market_cap = COALESCE(c.market_cap, q.market_cap),
    outstanding_shares = COALESCE(c.outstanding_shares, q.outstanding_shares),
    foreign_holding_ratio = COALESCE(c.foreign_holding_ratio, q.foreign_holding_ratio),
    per = COALESCE(c.per, q.per),
    pbr = COALESCE(c.pbr, q.pbr),
    w52_high = COALESCE(c.w52_high, q.w52_high),
    w52_low = COALESCE(c.w52_low, q.w52_low),
    source = COALESCE(c.source, q.source),
    available_at = COALESCE(c.available_at, q.available_at),
    updated_at = CURRENT_TIMESTAMP
FROM q_rows q
WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
  AND c.base_date = q.base_date;

UPDATE public.normalized_stock_snapshots_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.normalized_stock_snapshots_daily c
      WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
  );
DELETE FROM public.normalized_stock_snapshots_daily
WHERE symbol ~ '^Q[0-9]{6}$';

WITH q_rows AS (
    SELECT *
    FROM public.feature_store_daily
    WHERE symbol ~ '^Q[0-9]{6}$'
)
UPDATE public.feature_store_daily c
SET feature_value = COALESCE(c.feature_value, q.feature_value),
    available_at = COALESCE(c.available_at, q.available_at),
    updated_at = CURRENT_TIMESTAMP
FROM q_rows q
WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
  AND c.base_date = q.base_date
  AND c.feature_name = q.feature_name
  AND c.feature_value IS NULL
  AND q.feature_value IS NOT NULL;

UPDATE public.feature_store_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.feature_store_daily c
      WHERE c.symbol = SUBSTRING(q.symbol FROM 2)
        AND c.base_date = q.base_date
        AND c.feature_name = q.feature_name
  );
DELETE FROM public.feature_store_daily
WHERE symbol ~ '^Q[0-9]{6}$';

UPDATE public.raw_market_rankings q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.raw_market_rankings c
      WHERE c.source = q.source
        AND c.base_date = q.base_date
        AND c.market = q.market
        AND c.rank_type = q.rank_type
        AND c.symbol = SUBSTRING(q.symbol FROM 2)
  );
DELETE FROM public.raw_market_rankings
WHERE symbol ~ '^Q[0-9]{6}$';

UPDATE public.normalized_market_rankings_daily q
SET symbol = SUBSTRING(q.symbol FROM 2)
WHERE q.symbol ~ '^Q[0-9]{6}$'
  AND NOT EXISTS (
      SELECT 1
      FROM public.normalized_market_rankings_daily c
      WHERE c.base_date = q.base_date
        AND c.market = q.market
        AND c.rank_type = q.rank_type
        AND c.rank = q.rank
        AND c.symbol = SUBSTRING(q.symbol FROM 2)
  );
DELETE FROM public.normalized_market_rankings_daily
WHERE symbol ~ '^Q[0-9]{6}$';

-- 3. Reclassify existing ranking rows to actual master market and drop mismatches for market-specific ranks.
WITH master_map AS (
    SELECT symbol, market
    FROM public.stocks_master
),
reclassified AS (
    SELECT
        r.base_date,
        r.market AS old_market,
        r.rank_type,
        r.rank,
        r.symbol,
        CASE
            WHEN r.market = 'KOSPI200' THEN 'KOSPI200'
            WHEN m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN') THEN m.market
            ELSE r.market
        END AS new_market
    FROM public.normalized_market_rankings_daily r
    LEFT JOIN master_map m
      ON m.symbol = r.symbol
)
UPDATE public.normalized_market_rankings_daily r
SET market = x.new_market
FROM reclassified x
WHERE r.base_date = x.base_date
  AND r.market = x.old_market
  AND r.rank_type = x.rank_type
  AND r.rank = x.rank
  AND r.symbol = x.symbol
  AND x.new_market IS NOT NULL
  AND x.new_market <> x.old_market;

WITH master_map AS (
    SELECT symbol, market
    FROM public.stocks_master
),
reclassified AS (
    SELECT
        r.source,
        r.base_date,
        r.market AS old_market,
        r.rank_type,
        r.symbol,
        CASE
            WHEN r.market = 'KOSPI200' THEN 'KOSPI200'
            WHEN m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN') THEN m.market
            ELSE r.market
        END AS new_market
    FROM public.raw_market_rankings r
    LEFT JOIN master_map m
      ON m.symbol = r.symbol
)
UPDATE public.raw_market_rankings r
SET market = x.new_market
FROM reclassified x
WHERE r.source = x.source
  AND r.base_date = x.base_date
  AND r.market = x.old_market
  AND r.rank_type = x.rank_type
  AND r.symbol = x.symbol
  AND x.new_market IS NOT NULL
  AND x.new_market <> x.old_market;

DELETE FROM public.normalized_market_rankings_daily r
USING public.stocks_master m
WHERE r.symbol = m.symbol
  AND r.market <> 'KOSPI200'
  AND r.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND r.market <> m.market;

DELETE FROM public.raw_market_rankings r
USING public.stocks_master m
WHERE r.symbol = m.symbol
  AND r.market <> 'KOSPI200'
  AND r.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND m.market IN ('KOSPI', 'KOSDAQ', 'ETF', 'ETN')
  AND r.market <> m.market;

COMMIT;
