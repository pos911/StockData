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
        statements.append(RAW_ECOS_MACRO_DAILY_TABLE_SQL)
        statements.append(RAW_STOCK_SHORT_SELLING_TABLE_SQL)
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
