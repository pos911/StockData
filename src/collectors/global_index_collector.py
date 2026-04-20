import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GlobalIndexCollector:
    """Collect global and Korean market index data via yfinance/pykrx."""

    def __init__(self):
        self.ticker_map = {
            "usdkrw": "KRW=X",
            "dxy": "DX-Y.NYB",
            "us10y": "^TNX",
            "kospi": "^KS11",
            "kosdaq": "^KQ11",
            "nasdaq": "^IXIC",
            "sp500": "^GSPC",
            "sox": "^SOX",
            "vix": "^VIX",
            "wti": "CL=F",
            "brent": "BZ=F",
            "gold": "GC=F",
            "copper": "HG=F",
            "bdry": "BDRY",
        }

    def fetch_daily_indices(self, target_date: date) -> Optional[Dict[str, Any]]:
        try:
            import yfinance as yf

            start_dt = target_date - timedelta(days=7)
            end_dt = target_date + timedelta(days=1)
            result = {"base_date": target_date.strftime("%Y-%m-%d")}

            for key, ticker in self.ticker_map.items():
                data = yf.download(ticker, start=start_dt, end=end_dt, progress=False)
                close = self._close_series(data)
                if close is None or close.empty:
                    result[key] = None
                    result[f"{key}_change_rate"] = None
                    continue

                position = self._target_position(close, target_date)
                current = float(close.iloc[position])
                result[key] = current

                if position > 0:
                    previous = float(close.iloc[position - 1])
                    result[f"{key}_change_rate"] = ((current / previous) - 1) * 100 if previous else None
                else:
                    result[f"{key}_change_rate"] = None

            result.update(self._fetch_korean_investor_trends(target_date))
            return result
        except Exception as exc:
            logger.error(f"Error fetching Global Indices: {exc}")
            return None

    @staticmethod
    def _close_series(data):
        if data is None or data.empty or "Close" not in data:
            return None
        close = data["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        return close.dropna()

    @staticmethod
    def _target_position(close, target_date: date) -> int:
        target_str = target_date.strftime("%Y-%m-%d")
        index_dates = list(close.index.strftime("%Y-%m-%d"))
        if target_str in index_dates:
            return index_dates.index(target_str)
        return len(close) - 1

    def _fetch_korean_investor_trends(self, target_date: date) -> Dict[str, Any]:
        empty = {
            "kospi_individual_net_buy": None,
            "kospi_foreign_net_buy": None,
            "kospi_institutional_net_buy": None,
            "kosdaq_individual_net_buy": None,
            "kosdaq_foreign_net_buy": None,
            "kosdaq_institutional_net_buy": None,
        }
        try:
            from pykrx import stock

            target = target_date.strftime("%Y%m%d")
            trends = {}
            for market in ("KOSPI", "KOSDAQ"):
                prefix = market.lower()
                df = stock.get_market_trading_value_by_date(target, target, market)
                if df is None or df.empty:
                    trends[f"{prefix}_individual_net_buy"] = None
                    trends[f"{prefix}_foreign_net_buy"] = None
                    trends[f"{prefix}_institutional_net_buy"] = None
                    continue

                row = df.iloc[-1]
                trends[f"{prefix}_individual_net_buy"] = self._row_number(row, ["개인"])
                trends[f"{prefix}_foreign_net_buy"] = self._row_number(row, ["외국인합계", "외국인"])
                trends[f"{prefix}_institutional_net_buy"] = self._row_number(row, ["기관합계", "기관"])
            return trends
        except Exception as exc:
            logger.warning(f"Failed to fetch Korean market investor trends: {exc}")
            return empty

    @staticmethod
    def _row_number(row, column_names):
        for column in column_names:
            if column in row.index:
                try:
                    return float(row[column])
                except (TypeError, ValueError):
                    return None
        return None
