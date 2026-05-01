CREATE TABLE IF NOT EXISTS public.normalized_stock_snapshots_daily (
    symbol VARCHAR(20),
    base_date DATE,
    market_cap NUMERIC,
    outstanding_shares NUMERIC,
    foreign_holding_ratio NUMERIC,
    per NUMERIC,
    pbr NUMERIC,
    w52_high NUMERIC,
    w52_low NUMERIC,
    source VARCHAR(50),
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS public.raw_market_rankings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50) NOT NULL,
    base_date DATE NOT NULL,
    market VARCHAR(30) NOT NULL,
    rank_type VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    raw_rank INT,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, base_date, market, rank_type, symbol)
);

CREATE TABLE IF NOT EXISTS public.normalized_market_rankings_daily (
    base_date DATE NOT NULL,
    market VARCHAR(30) NOT NULL,
    rank_type VARCHAR(50) NOT NULL,
    rank INT NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    volume NUMERIC,
    trading_value NUMERIC,
    market_cap NUMERIC,
    change_rate NUMERIC,
    metric_value NUMERIC,
    source VARCHAR(50),
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (base_date, market, rank_type, rank, symbol)
);

CREATE OR REPLACE VIEW public.vw_latest_valid_stock_prices AS
SELECT *
FROM public.normalized_stock_prices_daily
WHERE base_date = (
    SELECT MAX(base_date)
    FROM public.normalized_stock_prices_daily
)
AND close_price IS NOT NULL
AND volume IS NOT NULL
AND trading_value IS NOT NULL;

CREATE OR REPLACE VIEW public.vw_valid_stock_prices_daily AS
SELECT *
FROM public.normalized_stock_prices_daily
WHERE close_price IS NOT NULL
AND volume IS NOT NULL
AND trading_value IS NOT NULL;

NOTIFY pgrst, 'reload schema';
