-- Supabase 스키마 

-- 1. 마스터 테이블 (종목/매크로 메타 관리)
CREATE TABLE IF NOT EXISTS stocks_master (
    symbol VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    market VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS static_stock_universe (
    symbol VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    market VARCHAR(20),
    enabled BOOLEAN DEFAULT TRUE,
    source_file TEXT DEFAULT 'config/stock_universe.json',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS macro_series_master (
    series_id VARCHAR(50) PRIMARY KEY,
    source VARCHAR(50),
    name VARCHAR(200),
    name_ko VARCHAR(200),
    stat_code VARCHAR(50),
    item_code VARCHAR(50),
    category VARCHAR(50),
    unit VARCHAR(50),
    frequency VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Raw 레이어 (가공 전 순수 수집 값)
CREATE TABLE IF NOT EXISTS raw_stock_prices_daily (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    symbol VARCHAR(20),
    base_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, symbol, base_date)
);

CREATE TABLE IF NOT EXISTS raw_stock_supply_daily (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    symbol VARCHAR(20),
    base_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, symbol, base_date)
);

CREATE TABLE IF NOT EXISTS raw_macro_series (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    series_id VARCHAR(50),
    base_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, series_id, base_date)
);

CREATE TABLE IF NOT EXISTS raw_ecos_macro_daily (
    source TEXT NOT NULL DEFAULT 'ECOS',
    series_id TEXT NOT NULL,
    stat_code TEXT NOT NULL,
    item_code TEXT NOT NULL,
    item_name TEXT,
    date DATE NOT NULL,
    time_raw TEXT NOT NULL,
    value NUMERIC,
    unit TEXT,
    cycle TEXT,
    collected_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw JSONB,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS raw_disclosures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    symbol VARCHAR(20),
    base_date DATE,   -- 공시일
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, symbol, id) -- 공시 고유번호가 있으면 그 번호 활용
);

-- 3. Normalized 레이어
CREATE TABLE IF NOT EXISTS normalized_stock_prices_daily (
    symbol VARCHAR(20),
    base_date DATE,
    open_price NUMERIC,
    high_price NUMERIC,
    low_price NUMERIC,
    close_price NUMERIC,
    volume NUMERIC,
    trading_value NUMERIC,
    market_cap NUMERIC,
    outstanding_shares NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS normalized_macro_series (
    series_id VARCHAR(50),
    base_date DATE,
    value NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (series_id, base_date)
);

-- 4. Feature Store 의 기초 모델
CREATE TABLE IF NOT EXISTS feature_store_daily (
    symbol VARCHAR(20),
    base_date DATE,
    feature_name VARCHAR(100),
    feature_value NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(symbol, base_date, feature_name)
);

-- 5. 파이프라인 관리
CREATE TABLE IF NOT EXISTS pipeline_run_logs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name VARCHAR(100),
    target_date DATE,
    status VARCHAR(20),
    start_time TIMESTAMP WITH TIME ZONE,
    end_time TIMESTAMP WITH TIME ZONE,
    records_processed INT DEFAULT 0,
    error_message TEXT
);

-- 6. Algorithmic Trading Extension (Supply, Macro, Derivatives, Events)
CREATE TABLE IF NOT EXISTS normalized_stock_supply_daily (
    symbol VARCHAR(20),
    base_date DATE,
    foreign_net_buy NUMERIC,
    institutional_net_buy NUMERIC,
    individual_net_buy NUMERIC,
    pension_net_buy NUMERIC,
    corporate_net_buy NUMERIC,
    foreign_holding_ratio NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS normalized_stock_short_selling (
    symbol VARCHAR(20),
    base_date DATE,
    short_volume NUMERIC,
    short_value NUMERIC,
    short_ratio NUMERIC,
    source VARCHAR(50),
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS normalized_stock_fundamentals (
    symbol VARCHAR(20),
    base_date DATE,
    revenue NUMERIC,
    operating_income NUMERIC,
    net_income NUMERIC,
    total_assets NUMERIC,
    total_liabilities NUMERIC,
    total_equity NUMERIC,
    source VARCHAR(50),
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS normalized_stock_fundamentals_ratios (
    symbol VARCHAR(20),
    base_date DATE,
    per NUMERIC,
    pbr NUMERIC,
    roe NUMERIC,
    debt_ratio NUMERIC,
    source VARCHAR(50),
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date)
);

CREATE TABLE IF NOT EXISTS market_breadth_daily (
    base_date DATE PRIMARY KEY,
    advances INT,
    declines INT,
    unchanged INT,
    advancing_volume NUMERIC,
    declining_volume NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS normalized_global_macro_daily (
    base_date DATE PRIMARY KEY,
    usdkrw NUMERIC,
    dxy NUMERIC,
    us10y NUMERIC,
    kr10y NUMERIC,
    kospi NUMERIC,
    kospi_change_rate NUMERIC,
    kosdaq NUMERIC,
    kosdaq_change_rate NUMERIC,
    wti NUMERIC,
    brent NUMERIC,
    nasdaq NUMERIC,
    nasdaq_change_rate NUMERIC,
    sp500 NUMERIC,
    sp500_change_rate NUMERIC,
    sox NUMERIC,
    vix NUMERIC,
    gold NUMERIC,
    copper NUMERIC,
    bdry NUMERIC,
    hy_spread NUMERIC,
    kospi_individual_net_buy NUMERIC,
    kospi_foreign_net_buy NUMERIC,
    kospi_institutional_net_buy NUMERIC,
    kosdaq_individual_net_buy NUMERIC,
    kosdaq_foreign_net_buy NUMERIC,
    kosdaq_institutional_net_buy NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS normalized_derivatives_daily (
    base_date DATE PRIMARY KEY,
    kospi200_futures NUMERIC,
    futures_basis NUMERIC,
    open_interest NUMERIC,
    night_futures_return NUMERIC,
    expiration_flag BOOLEAN,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS normalized_stock_events_daily (
    symbol VARCHAR(20),
    base_date DATE,
    event_type VARCHAR(50),
    event_score NUMERIC,
    sentiment_score NUMERIC,
    available_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, base_date, event_type)
);
