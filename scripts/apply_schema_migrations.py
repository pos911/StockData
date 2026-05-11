import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.utils.config_loader import load_config
from src.utils.logger import get_logger

logger = get_logger(__name__)


GLOBAL_MACRO_COLUMNS = {
    "kospi": "NUMERIC",
    "kospi_change_rate": "NUMERIC",
    "kosdaq": "NUMERIC",
    "kosdaq_change_rate": "NUMERIC",
    "nasdaq_change_rate": "NUMERIC",
    "sp500_change_rate": "NUMERIC",
    "gold": "NUMERIC",
    "copper": "NUMERIC",
    "bdry": "NUMERIC",
    "hy_spread": "NUMERIC",
    "kospi_individual_net_buy": "NUMERIC",
    "kospi_foreign_net_buy": "NUMERIC",
    "kospi_institutional_net_buy": "NUMERIC",
    "kosdaq_individual_net_buy": "NUMERIC",
    "kosdaq_foreign_net_buy": "NUMERIC",
    "kosdaq_institutional_net_buy": "NUMERIC",
}

MACRO_SERIES_MASTER_COLUMNS = {
    "name_ko": "VARCHAR(200)",
    "stat_code": "VARCHAR(50)",
    "item_code": "VARCHAR(50)",
    "category": "VARCHAR(50)",
    "unit": "VARCHAR(50)",
    "is_active": "BOOLEAN DEFAULT TRUE",
    "updated_at": "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
}

MASTER_ASSET_COLUMNS = {
    "asset_type": "VARCHAR(20) DEFAULT 'STOCK'",
}

RAW_ECOS_MACRO_DAILY_TABLE_SQL = """
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
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw JSONB,
    PRIMARY KEY (series_id, date)
);
"""

RAW_STOCK_SHORT_SELLING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_stock_short_selling (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50),
    symbol VARCHAR(20),
    base_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, symbol, base_date)
);
"""

SNAPSHOT_AND_RANKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS normalized_stock_snapshots_daily (
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

CREATE TABLE IF NOT EXISTS raw_market_rankings (
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

CREATE TABLE IF NOT EXISTS normalized_market_rankings_daily (
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

CREATE OR REPLACE VIEW vw_latest_valid_stock_prices AS
SELECT *
FROM public.normalized_stock_prices_daily
WHERE base_date = (
    SELECT MAX(base_date)
    FROM public.normalized_stock_prices_daily
)
AND close_price IS NOT NULL
AND volume IS NOT NULL
AND trading_value IS NOT NULL;

CREATE OR REPLACE VIEW vw_valid_stock_prices_daily AS
SELECT *
FROM public.normalized_stock_prices_daily
WHERE close_price IS NOT NULL
AND volume IS NOT NULL
AND trading_value IS NOT NULL;
"""


def apply_schema_migrations() -> None:
    config = load_config()
    connection_string = config.get("supabase", {}).get("connection_string")
    if not connection_string:
        logger.warning("SUPABASE connection_string is missing. Skipping schema migrations.")
        return

    try:
        import psycopg2

        statements = [
            f"ALTER TABLE normalized_global_macro_daily ADD COLUMN IF NOT EXISTS {name} {column_type};"
            for name, column_type in GLOBAL_MACRO_COLUMNS.items()
        ]
        statements.extend(
            f"ALTER TABLE macro_series_master ADD COLUMN IF NOT EXISTS {name} {column_type};"
            for name, column_type in MACRO_SERIES_MASTER_COLUMNS.items()
        )
        for table_name in ("stocks_master", "static_stock_universe"):
            statements.extend(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {name} {column_type};"
                for name, column_type in MASTER_ASSET_COLUMNS.items()
            )
        statements.extend(
            [
                "COMMENT ON COLUMN normalized_stock_supply_daily.foreign_net_buy IS 'Net buy quantity in shares from KIS frgn_ntby_qty, not KRW.';",
                "COMMENT ON COLUMN normalized_stock_supply_daily.institutional_net_buy IS 'Net buy quantity in shares from KIS orgn_ntby_qty, not KRW.';",
                "COMMENT ON COLUMN normalized_stock_supply_daily.individual_net_buy IS 'Net buy quantity in shares from KIS prsn_ntby_qty, not KRW.';",
                "COMMENT ON COLUMN normalized_stock_supply_daily.pension_net_buy IS 'Net buy quantity in shares from KIS pnsn_ntby_qty, not KRW.';",
                "COMMENT ON COLUMN normalized_stock_supply_daily.corporate_net_buy IS 'Net buy quantity in shares from KIS etc_corp_ntby_qty, not KRW.';",
            ]
        )
        statements.append(RAW_ECOS_MACRO_DAILY_TABLE_SQL)
        statements.append(RAW_STOCK_SHORT_SELLING_TABLE_SQL)
        statements.append(SNAPSHOT_AND_RANKING_TABLE_SQL)
        
        # New intraday macro SQL
        statements.append((Path(__file__).resolve().parents[1] / "sql" / "add_normalized_macro_intraday.sql").read_text(encoding="utf-8"))
        statements.append((Path(__file__).resolve().parents[1] / "sql" / "add_global_macro_quality_flags.sql").read_text(encoding="utf-8"))

        statements.append("NOTIFY pgrst, 'reload schema';")

        with psycopg2.connect(connection_string, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            conn.commit()
        logger.info("Schema migrations applied successfully.")
    except Exception as exc:
        logger.warning(
            "Schema migrations skipped. This is non-fatal for the data sync; "
            "use the Supabase pooler connection string or run sql/supabase_schema.sql manually "
            f"to persist new columns. Error: {exc}"
        )
        return


if __name__ == "__main__":
    apply_schema_migrations()
