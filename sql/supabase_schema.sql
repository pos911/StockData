# Supabase 스키마 

-- 1. 마스터 테이블 (종목/매크로 메타 관리)
CREATE TABLE IF NOT EXISTS stocks_master (
    symbol VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    market VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS macro_series_master (
    series_id VARCHAR(50) PRIMARY KEY,
    source VARCHAR(50),
    name VARCHAR(200),
    frequency VARCHAR(20),
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
