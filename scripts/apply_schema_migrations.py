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
        statements.append("NOTIFY pgrst, 'reload schema';")

        with psycopg2.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            conn.commit()
        logger.info("Schema migrations applied successfully.")
    except Exception as exc:
        logger.error(f"Failed to apply schema migrations: {exc}")
        raise


if __name__ == "__main__":
    apply_schema_migrations()
