import json
import pandas as pd
from datetime import date
import argparse
from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst
from src.loaders.supabase_loader import SupabaseLoader

logger = get_logger(__name__)

from src.utils.config_loader import load_config

def run_feature_pipeline(target_date: date):
    logger.info(f"Starting Feature Pipeline wrapper for {target_date}...")
    from src.features.generate_features import run_job as generate_features_job
    generate_features_job(target_date)
    logger.info("Feature Pipeline wrapper finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_feature_pipeline(target_dt)
