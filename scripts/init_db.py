import json
import psycopg2
from src.utils.logger import get_logger

logger = get_logger(__name__)

def init_db():
    try:
        with open("config/api_keys.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        
        conn_string = config["supabase"]["connection_string"]
        
        logger.info("Connecting to Supabase Database...")
        with psycopg2.connect(conn_string) as conn:
            with conn.cursor() as cur:
                logger.info("Reading SQL schema file...")
                with open("sql/supabase_schema.sql", "r", encoding="utf-8") as sql_file:
                    sql_script = sql_file.read()
                
                logger.info("Executing SQL script...")
                cur.execute(sql_script)
                conn.commit()
                logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

if __name__ == "__main__":
    init_db()
