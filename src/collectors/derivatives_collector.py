import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DerivativesCollector:
    """Collect KOSPI200 proxy data for the derivatives daily table."""

    KOSPI200_INDEX_CODE = "1028"

    def fetch_daily_derivatives(self, target_date: date) -> Optional[Dict[str, Any]]:
        yf_record = self._fetch_kospi200_from_yfinance(target_date)
        if yf_record:
            return yf_record

        for offset in range(0, 7):
            query_date = target_date - timedelta(days=offset)
            record = self._fetch_kospi200_from_pykrx(query_date)
            if record:
                return record

        logger.warning(f"No KOSPI200 derivatives proxy data found on or before {target_date}.")
        return None

    def _fetch_kospi200_from_yfinance(self, target_date: date) -> Optional[Dict[str, Any]]:
        try:
            import yfinance as yf

            start_dt = target_date - timedelta(days=7)
            end_dt = target_date + timedelta(days=1)
            df = yf.download("^KS200", start=start_dt, end=end_dt, progress=False, auto_adjust=False)
            if df is None or df.empty or "Close" not in df:
                return None

            close = df["Close"]
            volume = df["Volume"] if "Volume" in df else None
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            if hasattr(volume, "columns"):
                volume = volume.iloc[:, 0]

            close = close.dropna()
            if close.empty:
                return None

            target_str = target_date.strftime("%Y-%m-%d")
            index_dates = list(close.index.strftime("%Y-%m-%d"))
            position = index_dates.index(target_str) if target_str in index_dates else len(close) - 1
            base_date = close.index[position].date()
            volume_value = 0
            if volume is not None and position < len(volume):
                try:
                    volume_value = int(float(volume.iloc[position]))
                except (TypeError, ValueError):
                    volume_value = 0

            return {
                "base_date": base_date.strftime("%Y-%m-%d"),
                "kospi200_futures": float(close.iloc[position]),
                "futures_basis": 0.0,
                "open_interest": volume_value,
                "night_futures_return": 0.0,
                "expiration_flag": self._is_expiration_date(base_date),
            }
        except Exception as exc:
            logger.warning(f"Failed to fetch KOSPI200 proxy via yfinance: {exc}")
            return None

    def _fetch_kospi200_from_pykrx(self, target_date: date) -> Optional[Dict[str, Any]]:
        try:
            from pykrx import stock

            target = target_date.strftime("%Y%m%d")
            df = stock.get_index_ohlcv_by_date(target, target, self.KOSPI200_INDEX_CODE)
            if df is None or df.empty:
                return None

            row = df.iloc[-1]
            close = self._row_number(row, ["종가", "현재가"])
            volume = self._row_number(row, ["거래량"])
            if close is None:
                logger.warning(f"KOSPI200 close column missing for {target}. Columns={list(df.columns)}")
                return None

            return {
                "base_date": target_date.strftime("%Y-%m-%d"),
                "kospi200_futures": close,
                "futures_basis": 0.0,
                "open_interest": int(volume or 0),
                "night_futures_return": 0.0,
                "expiration_flag": self._is_expiration_date(target_date),
            }
        except Exception as exc:
            logger.warning(f"Failed to fetch KOSPI200 proxy for {target_date}: {exc}")
            return None

    @staticmethod
    def _row_number(row, column_names):
        for column in column_names:
            if column in row.index:
                try:
                    return float(row[column])
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _is_expiration_date(target_date: date) -> bool:
        # Korean index futures/options usually expire on the second Thursday.
        return target_date.weekday() == 3 and 8 <= target_date.day <= 14
