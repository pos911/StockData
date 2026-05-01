CREATE TABLE IF NOT EXISTS public.raw_stock_short_selling (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    symbol VARCHAR(20),
    base_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, symbol, base_date)
);

NOTIFY pgrst, 'reload schema';
