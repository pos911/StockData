ALTER TABLE public.stocks_master
    ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) DEFAULT 'STOCK';

ALTER TABLE public.static_stock_universe
    ADD COLUMN IF NOT EXISTS asset_type VARCHAR(20) DEFAULT 'STOCK';

COMMENT ON COLUMN public.normalized_stock_supply_daily.foreign_net_buy IS
    'Net buy quantity in shares from KIS frgn_ntby_qty, not KRW.';
COMMENT ON COLUMN public.normalized_stock_supply_daily.institutional_net_buy IS
    'Net buy quantity in shares from KIS orgn_ntby_qty, not KRW.';
COMMENT ON COLUMN public.normalized_stock_supply_daily.individual_net_buy IS
    'Net buy quantity in shares from KIS prsn_ntby_qty, not KRW.';
COMMENT ON COLUMN public.normalized_stock_supply_daily.pension_net_buy IS
    'Net buy quantity in shares from KIS pnsn_ntby_qty, not KRW.';
COMMENT ON COLUMN public.normalized_stock_supply_daily.corporate_net_buy IS
    'Net buy quantity in shares from KIS etc_corp_ntby_qty, not KRW.';

UPDATE public.stocks_master
SET asset_type = CASE
    WHEN UPPER(COALESCE(market, '')) = 'ETF'
      OR UPPER(COALESCE(name, '')) LIKE '%ETF%'
      OR UPPER(COALESCE(name, '')) LIKE 'KODEX%'
      OR UPPER(COALESCE(name, '')) LIKE 'TIGER%'
      OR UPPER(COALESCE(name, '')) LIKE 'RISE%'
      OR UPPER(COALESCE(name, '')) LIKE 'ACE%'
      OR UPPER(COALESCE(name, '')) LIKE 'PLUS%'
      OR UPPER(COALESCE(name, '')) LIKE 'SOL%'
      OR UPPER(COALESCE(name, '')) LIKE 'HANARO%'
      OR UPPER(COALESCE(name, '')) LIKE 'KOSEF%'
      OR UPPER(COALESCE(name, '')) LIKE 'TIMEFOLIO%'
        THEN 'ETF'
    WHEN UPPER(COALESCE(market, '')) = 'ETN'
      OR UPPER(COALESCE(name, '')) LIKE '%ETN%'
        THEN 'ETN'
    ELSE 'STOCK'
END
WHERE asset_type IS NULL OR asset_type = 'STOCK';

UPDATE public.static_stock_universe
SET asset_type = CASE
    WHEN UPPER(COALESCE(market, '')) = 'ETF'
      OR UPPER(COALESCE(name, '')) LIKE '%ETF%'
        THEN 'ETF'
    WHEN UPPER(COALESCE(market, '')) = 'ETN'
      OR UPPER(COALESCE(name, '')) LIKE '%ETN%'
        THEN 'ETN'
    ELSE 'STOCK'
END
WHERE asset_type IS NULL OR asset_type = 'STOCK';

UPDATE public.stocks_master
SET market = asset_type
WHERE asset_type IN ('ETF', 'ETN');

UPDATE public.static_stock_universe
SET market = asset_type
WHERE asset_type IN ('ETF', 'ETN');
