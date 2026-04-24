import json
import argparse
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.collectors.fred_collector import FREDCollector
from src.normalizers.macro_normalizer import MacroNormalizer
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config

logger = get_logger(__name__)

try:
    from src.collectors.tradingeconomics_collector import TradingEconomicsCollector
except ImportError:
    TradingEconomicsCollector = None


def load_macro_series():
    with open("config/macro_series.json", "r", encoding="utf-8") as file:
        return json.load(file)


def compute_market_breadth_from_prices(loader: SupabaseLoader, target_date: date):
    start_date = (target_date - timedelta(days=10)).strftime("%Y-%m-%d")
    end_date = target_date.strftime("%Y-%m-%d")
    rows = loader.fetch_all(
        table_name="normalized_stock_prices_daily",
        date_col="base_date",
        start_date=start_date,
        end_date=end_date,
        order_col="base_date",
        desc=False,
    )
    if not rows:
        return None

    df = pd.DataFrame(rows)
    if df.empty or not {"symbol", "base_date", "close_price"}.issubset(df.columns):
        return None

    df["base_date"] = pd.to_datetime(df["base_date"]).dt.strftime("%Y-%m-%d")
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    latest_date = df["base_date"].max()

    advances = declines = unchanged = 0
    advancing_volume = declining_volume = 0
    for _, group in df.dropna(subset=["close_price"]).sort_values("base_date").groupby("symbol"):
        latest_rows = group[group["base_date"] == latest_date]
        previous_rows = group[group["base_date"] < latest_date]
        if latest_rows.empty or previous_rows.empty:
            continue

        latest = latest_rows.iloc[-1]
        previous = previous_rows.iloc[-1]
        diff = float(latest["close_price"]) - float(previous["close_price"])
        volume = int(float(latest.get("volume") or 0))
        if diff > 0:
            advances += 1
            advancing_volume += volume
        elif diff < 0:
            declines += 1
            declining_volume += volume
        else:
            unchanged += 1

    if advances + declines + unchanged == 0:
        return None

    return {
        "base_date": latest_date,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advancing_volume": advancing_volume,
        "declining_volume": declining_volume,
    }


def run_pipeline(target_date: date):
    logger.info(f"Starting Daily Macro Pipeline up to {target_date}...")

    config = load_config()
    series_list = load_macro_series()

    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    fred = FREDCollector(api_key=config.get("fred", {}).get("api_key", ""))
    te = None
    if TradingEconomicsCollector is not None:
        te = TradingEconomicsCollector(client_key=config.get("tradingeconomics", {}).get("client_key", ""))
    else:
        logger.warning("TradingEconomics collector is unavailable; TradingEconomics series will be skipped.")

    logger.info("Syncing macro master metadata...")
    master_records = [
        {
            "series_id": series.get("series_id") or series.get("endpoint_key"),
            "source": series.get("source"),
            "name": series.get("name"),
            "frequency": series.get("frequency"),
        }
        for series in series_list
    ]
    loader.upsert_records("macro_series_master", master_records)

    available_at = generate_available_at_for_eod(target_date)
    timestamp_now = get_current_kst()
    total_processed = 0
    encountered_error = False

    for series in series_list:
        if not series.get("enabled", False):
            continue

        source = series.get("source")

        if source == "FRED":
            series_id = series.get("series_id")
            obs_start = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")

            logger.info(f"Fetching FRED series: {series_id} since {obs_start}")
            raw_data = fred.fetch_series(series_id=series_id, observation_start=obs_start, sort_order="desc", limit=100)
            observations = raw_data.get("observations", []) if raw_data else []

            if observations:
                raw_record = {
                    "source": "FRED",
                    "series_id": series_id,
                    "base_date": target_date.strftime("%Y-%m-%d"),
                    "raw_data": json.dumps(raw_data),
                    "collected_at": timestamp_now.isoformat(),
                    "available_at": available_at.isoformat(),
                }
                loader.upsert_records("raw_macro_series", [raw_record])

                norm_records = MacroNormalizer.normalize_fred(series_id, observations, available_at)
                for record in norm_records:
                    extra_keys = [key for key in list(record.keys()) if key not in ["series_id", "base_date", "value"]]
                    for key in extra_keys:
                        record.pop(key, None)
                    if record.get("value") is not None:
                        record["value"] = float(record["value"])
                loader.upsert_records("normalized_macro_series", norm_records)
                total_processed += len(norm_records)

        elif source == "YAHOO":
            series_id = series.get("series_id")
            if not series_id:
                continue

            try:
                start_dt = target_date - timedelta(days=7)
                end_dt = target_date + timedelta(days=1)
                yf_df = yf.download(series_id, start=start_dt, end=end_dt, progress=False)
                if not yf_df.empty:
                    close_series = yf_df["Close"]
                    if hasattr(close_series, "columns"):
                        close_series = close_series.iloc[:, 0]
                    close_val = float(close_series.iloc[-1])
                    base_date = yf_df.index[-1].strftime("%Y-%m-%d")
                    raw_record = {
                        "source": "YAHOO",
                        "series_id": series_id,
                        "base_date": base_date,
                        "raw_data": json.dumps({"ticker": series_id, "close": close_val}),
                        "collected_at": timestamp_now.isoformat(),
                        "available_at": available_at.isoformat(),
                    }
                    norm_record = {
                        "series_id": series_id,
                        "base_date": base_date,
                        "value": close_val,
                        "available_at": available_at.isoformat(),
                    }
                    loader.upsert_records("raw_macro_series", [raw_record])
                    loader.upsert_records("normalized_macro_series", [norm_record])
                    total_processed += 1
            except Exception as exc:
                logger.warning(f"Failed to fetch YAHOO series {series_id}: {exc}")

        elif source == "TradingEconomics":
            if te is None:
                logger.warning("Skipping TradingEconomics series because the collector is unavailable.")
                continue

            endpoint_key = series.get("endpoint_key", "")
            if endpoint_key == "south_korea_interest_rate":
                te_raw = te.fetch_indicator("south korea", "interest rate")
                if te_raw:
                    latest = te_raw[0] if isinstance(te_raw, list) else te_raw
                    value = latest.get("Last") or latest.get("Value")
                    date_str = latest.get("DateTime") or latest.get("Date")
                    if value is not None:
                        base_date = str(date_str)[:10] if date_str else target_date.strftime("%Y-%m-%d")
                        raw_record = {
                            "source": "TradingEconomics",
                            "series_id": endpoint_key,
                            "base_date": base_date,
                            "raw_data": json.dumps(te_raw),
                            "collected_at": timestamp_now.isoformat(),
                            "available_at": available_at.isoformat(),
                        }
                        norm_record = {
                            "series_id": endpoint_key,
                            "base_date": base_date,
                            "value": float(value),
                            "available_at": available_at.isoformat(),
                        }
                        loader.upsert_records("raw_macro_series", [raw_record])
                        loader.upsert_records("normalized_macro_series", [norm_record])
                        total_processed += 1

    from src.collectors.krx_collector import KRXCollector

    krx = KRXCollector(auth_key=config.get("krx", {}).get("auth_key", ""))
    breadth_data = krx.fetch_market_breadth(target_date)
    if not breadth_data:
        breadth_data = compute_market_breadth_from_prices(loader, target_date)

    from src.collectors.global_index_collector import GlobalIndexCollector

    global_index = GlobalIndexCollector(config)
    global_data = global_index.fetch_daily_indices(target_date)

    hy_spread = None
    raw_hy = fred.fetch_series(series_id="BAMLH0A0HYM2", observation_start=target_date.strftime("%Y-%m-%d"), limit=1)
    if raw_hy and raw_hy.get("observations"):
        try:
            value = raw_hy["observations"][0]["value"]
            if value != ".":
                hy_spread = float(value)
        except (ValueError, TypeError):
            pass

    if global_data:
        global_record = {
            "base_date": global_data.get("base_date"),
            "usdkrw": global_data.get("usdkrw"),
            "dxy": global_data.get("dxy"),
            "us10y": global_data.get("us10y"),
            "kr10y": global_data.get("kr10y"),
            "kospi": global_data.get("kospi"),
            "kospi_change_rate": global_data.get("kospi_change_rate"),
            "kosdaq": global_data.get("kosdaq"),
            "kosdaq_change_rate": global_data.get("kosdaq_change_rate"),
            "wti": global_data.get("wti"),
            "brent": global_data.get("brent"),
            "nasdaq": global_data.get("nasdaq"),
            "nasdaq_change_rate": global_data.get("nasdaq_change_rate"),
            "sp500": global_data.get("sp500"),
            "sp500_change_rate": global_data.get("sp500_change_rate"),
            "sox": global_data.get("sox"),
            "vix": global_data.get("vix"),
            "gold": global_data.get("gold"),
            "copper": global_data.get("copper"),
            "bdry": global_data.get("bdry"),
            "hy_spread": hy_spread,
            "kospi_individual_net_buy": global_data.get("kospi_individual_net_buy"),
            "kospi_foreign_net_buy": global_data.get("kospi_foreign_net_buy"),
            "kospi_institutional_net_buy": global_data.get("kospi_institutional_net_buy"),
            "kosdaq_individual_net_buy": global_data.get("kosdaq_individual_net_buy"),
            "kosdaq_foreign_net_buy": global_data.get("kosdaq_foreign_net_buy"),
            "kosdaq_institutional_net_buy": global_data.get("kosdaq_institutional_net_buy"),
            "available_at": available_at.isoformat(),
        }

        if breadth_data:
            breadth_record = {
                "base_date": breadth_data.get("base_date") or target_date.strftime("%Y-%m-%d"),
                **breadth_data,
                "available_at": available_at.isoformat(),
            }
            if loader.upsert_records("market_breadth_daily", [breadth_record]):
                total_processed += 1
            else:
                encountered_error = True

        if loader.upsert_records("normalized_global_macro_daily", [global_record]):
            total_processed += 1
        else:
            encountered_error = True

    status = "SUCCESS" if total_processed > 0 and not encountered_error else "WARN"
    if status != "SUCCESS":
        logger.warning(f"Macro Pipeline finished with warnings for {target_date}. processed={total_processed}")
    loader.insert_log("daily_macro_pipeline", target_date.strftime("%Y-%m-%d"), status, total_processed)
    logger.info(f"Macro Pipeline Finished. status={status}, processed={total_processed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()

    target_dt = get_current_kst().date()
    if args.date:
        from src.utils.time_utils import parse_date_string

        target_dt = parse_date_string(args.date)

    run_pipeline(target_dt)
