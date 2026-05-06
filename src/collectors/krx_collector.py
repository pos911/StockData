from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional, List
from io import StringIO
import pandas as pd
import requests
import FinanceDataReader as fdr
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient
from src.utils.symbols import normalize_symbol_value
from src.utils.time_utils import generate_available_at_for_eod

logger = get_logger(__name__)
KOSDAQ_STOCK_DAILY_TRADING_IMPLEMENTED = False

class KRXCollector:
    """한국거래소 데이터 수집기 (FinanceDataReader 및 KRX OPEN API 연동)"""
    def __init__(self, auth_key: str = ""):
        self.auth_key = auth_key
        self.client = HttpClient()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    @staticmethod
    def _normalize_market_label(value: str) -> str:
        if not value:
            return ""
        label = str(value).strip().upper()
        if "KOSPI" in label:
            return "KOSPI"
        if "KOSDAQ" in label:
            return "KOSDAQ"
        if "ETF" in label:
            return "ETF"
        if "KONEX" in label:
            return "KONEX"
        return label

    @staticmethod
    def _parse_numeric(value):
        if value in (None, "", "-"):
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_base_date(value: str | None, target_date: date) -> str:
        text = str(value or "").strip()
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return target_date.strftime("%Y-%m-%d")

    def fetch_krx_api(self, endpoint: str, target_date: date) -> List[Dict[str, Any]]:
        if not self.auth_key:
            logger.warning(f"KRX auth_key missing; skipping endpoint={endpoint}")
            return []
        try:
            response = requests.get(
                endpoint,
                params={"basDd": target_date.strftime("%Y%m%d")},
                headers={"AUTH_KEY": self.auth_key},
                timeout=20,
            )
            if response.status_code in {403, 404}:
                logger.warning(f"KRX API unavailable endpoint={endpoint} status={response.status_code}")
                return []
            response.raise_for_status()
            data = response.json()
            rows = data.get("OutBlock_1") or []
            if not isinstance(rows, list):
                rows = []
            sample_columns = sorted(rows[0].keys()) if rows else []
            logger.info(
                f"KRX API fetched endpoint={endpoint} target_date={target_date:%Y-%m-%d} "
                f"row_count={len(rows)} columns={sample_columns}"
            )
            return rows
        except Exception as exc:
            logger.warning(f"KRX API request failed endpoint={endpoint}: {exc}")
            return []

    def fetch_etf_daily_trading(self, target_date: date) -> List[Dict[str, Any]]:
        return self.fetch_krx_api("https://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd", target_date)

    def fetch_etn_daily_trading(self, target_date: date) -> List[Dict[str, Any]]:
        return self.fetch_krx_api("https://data-dbg.krx.co.kr/svc/apis/etp/etn_bydd_trd", target_date)

    def fetch_stock_daily_trading(self, target_date: date) -> List[Dict[str, Any]]:
        rows = self.fetch_kospi_stock_daily_trading(target_date)
        kosdaq_rows = self.fetch_kosdaq_stock_daily_trading(target_date)
        if rows or kosdaq_rows:
            return rows + kosdaq_rows
        for endpoint in (
            "https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr",
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_clpr",
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        ):
            rows = self.fetch_krx_api(endpoint, target_date)
            if rows:
                return rows
        return self._fetch_stock_daily_trading_pykrx(target_date)

    def _request_krx_trading_api(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        method: str = "POST",
        content_type: str = "application/json",
    ) -> Dict[str, Any]:
        if not self.auth_key:
            logger.warning(f"KRX auth_key missing; skipping endpoint={endpoint}")
            return {}
        headers = {
            "AUTH_KEY": self.auth_key,
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": self.headers["User-Agent"],
        }
        if content_type == "application/json":
            response = requests.request(method, endpoint, json=payload, headers=headers, timeout=20)
        elif method.upper() == "POST":
            response = requests.request(method, endpoint, data=payload, headers=headers, timeout=20)
        else:
            response = requests.request(method, endpoint, params=payload, headers=headers, timeout=20)

        content_type_header = response.headers.get("content-type", "")
        body_sample = (response.text or "")[:500].replace("\n", " ").replace("\r", " ")
        if response.status_code in {403, 404}:
            logger.warning(
                f"KRX trading API unavailable endpoint={endpoint} method={method} "
                f"status={response.status_code} content_type={content_type_header} body_sample={body_sample}"
            )
            return {}
        if "LOGOUT" in body_sample or "로그인" in body_sample:
            logger.warning(
                f"KRX trading API requires authenticated session endpoint={endpoint} method={method} "
                f"content_type={content_type_header} body_sample={body_sample}"
            )
            return {}
        try:
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(
                f"KRX trading API request failed endpoint={endpoint} method={method} "
                f"content_type={content_type_header} body_sample={body_sample} error={exc}"
            )
            return {}

    def fetch_kospi_stock_daily_trading(self, target_date: date) -> List[Dict[str, Any]]:
        endpoint = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
        payload = {"basDd": target_date.strftime("%Y%m%d")}
        attempts = [
            ("POST", "application/json"),
            ("POST", "application/x-www-form-urlencoded"),
            ("GET", "application/json"),
        ]
        for method, content_type in attempts:
            response_data = self._request_krx_trading_api(
                endpoint=endpoint,
                payload=payload,
                method=method,
                content_type=content_type,
            )
            rows = response_data.get("OutBlock_1") or []
            if not isinstance(rows, list):
                rows = []
            sample_columns = sorted(rows[0].keys()) if rows else []
            logger.info(
                f"KRX KOSPI stock daily fetched target_date={target_date:%Y-%m-%d} "
                f"method={method} content_type={content_type} row_count={len(rows)} columns={sample_columns}"
            )
            if rows:
                if method != "POST" or content_type != "application/json":
                    logger.warning(
                        "KRX KOSPI daily trading succeeded with a request format different from the API sample. "
                        f"Using method={method} content_type={content_type} until the documented JSON request path is validated."
                    )
                return rows
        return []

    def fetch_kosdaq_stock_daily_trading(self, target_date: date) -> List[Dict[str, Any]]:
        logger.warning(
            f"KOSDAQ stock daily trading endpoint is not implemented yet for target_date={target_date:%Y-%m-%d}. "
            "Do not infer KOSDAQ full-market price coverage from the KOSPI endpoint."
        )
        return []

    def normalize_krx_stock_price_row(self, row: Dict[str, Any], target_date: date) -> Optional[Dict[str, Any]]:
        symbol = normalize_symbol_value(
            row.get("ISU_SRT_CD")
            or row.get("ISU_CD")
            or row.get("isu_srt_cd")
            or row.get("isu_cd")
        )
        market_value = str(row.get("MKT_ID") or row.get("MKT_NM") or row.get("mktId") or row.get("mktNm") or "").strip().upper()
        market = None
        if market_value in {"STK", "KOSPI"} or "KOSPI" in market_value or "유가증권" in str(row.get("MKT_NM") or ""):
            market = "KOSPI"
        elif market_value in {"KSQ", "KOSDAQ"} or "KOSDAQ" in market_value:
            market = "KOSDAQ"
        if not symbol or not market:
            return None

        return {
            "symbol": symbol,
            "name": row.get("ISU_ABBRV") or row.get("ISU_NM") or row.get("isu_abbrv") or row.get("isu_nm"),
            "market": market,
            "asset_type": "STOCK",
            "base_date": self._format_base_date(row.get("BAS_DD") or row.get("basDd"), target_date),
            "open_price": self._parse_numeric(row.get("TDD_OPNPRC") or row.get("tdd_opnprc")),
            "high_price": self._parse_numeric(row.get("TDD_HGPRC") or row.get("tdd_hgprc")),
            "low_price": self._parse_numeric(row.get("TDD_LWPRC") or row.get("tdd_lwprc")),
            "close_price": self._parse_numeric(row.get("TDD_CLSPRC") or row.get("tdd_clsprc")),
            "volume": self._parse_numeric(row.get("ACC_TRDVOL") or row.get("TDD_VLM") or row.get("acc_trdvol") or row.get("tdd_vlm")),
            "trading_value": self._parse_numeric(row.get("ACC_TRDVAL") or row.get("TDD_AMT") or row.get("acc_trdval") or row.get("tdd_amt")),
            "market_cap": self._parse_numeric(row.get("MKTCAP") or row.get("mktcap")),
            "outstanding_shares": self._parse_numeric(row.get("LIST_SHRS") or row.get("list_shrs")),
            "change_price": self._parse_numeric(row.get("CMPPREVDD_PRC") or row.get("cmpprevdd_prc")),
            "change_rate": self._parse_numeric(row.get("FLUC_RT") or row.get("fluc_rt")),
            "source": "KRX_API",
            "available_at": generate_available_at_for_eod(target_date).isoformat(),
        }

    def _fetch_stock_daily_trading_pykrx(self, target_date: date) -> List[Dict[str, Any]]:
        try:
            from pykrx import stock
        except Exception as exc:
            logger.warning(f"pykrx fallback unavailable for stock daily trading: {exc}")
            return []

        rows: List[Dict[str, Any]] = []
        for market_name, market_code in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
            try:
                ohlcv = stock.get_market_ohlcv_by_ticker(target_date.strftime("%Y%m%d"), market=market_code)
                cap = stock.get_market_cap_by_ticker(target_date.strftime("%Y%m%d"), market=market_code)
            except Exception as exc:
                logger.warning(f"pykrx stock daily trading fallback failed for {market_name}: {exc}")
                continue
            if ohlcv is None or ohlcv.empty:
                logger.warning(f"pykrx stock daily trading fallback returned no OHLCV rows for {market_name}.")
                continue
            cap = cap if cap is not None else pd.DataFrame()
            for ticker, row in ohlcv.iterrows():
                cap_row = cap.loc[ticker] if not cap.empty and ticker in cap.index else {}
                rows.append(
                    {
                        "BAS_DD": target_date.strftime("%Y%m%d"),
                        "ISU_SRT_CD": str(ticker),
                        "ISU_ABBRV": "",
                        "MKT_ID": "STK" if market_name == "KOSPI" else "KSQ",
                        "MKT_NM": market_name,
                        "TDD_OPNPRC": row.get("시가"),
                        "TDD_HGPRC": row.get("고가"),
                        "TDD_LWPRC": row.get("저가"),
                        "TDD_CLSPRC": row.get("종가"),
                        "ACC_TRDVOL": row.get("거래량"),
                        "ACC_TRDVAL": row.get("거래대금"),
                        "MKTCAP": cap_row.get("시가총액") if isinstance(cap_row, pd.Series) else None,
                        "LIST_SHRS": cap_row.get("상장주식수") if isinstance(cap_row, pd.Series) else None,
                        "FLUC_RT": row.get("등락률"),
                    }
                )
            logger.info(f"pykrx fallback stock daily trading loaded market={market_name} row_count={len(ohlcv)}")
        return rows

    def normalize_krx_etp_row(self, row: Dict[str, Any], market: str, asset_type: str, target_date: date) -> Dict[str, Any]:
        symbol = normalize_symbol_value(row.get("ISU_CD"))
        return {
            "symbol": symbol,
            "name": row.get("ISU_NM"),
            "base_date": self._format_base_date(row.get("BAS_DD"), target_date),
            "open_price": self._parse_numeric(row.get("TDD_OPNPRC")),
            "high_price": self._parse_numeric(row.get("TDD_HGPRC")),
            "low_price": self._parse_numeric(row.get("TDD_LWPRC")),
            "close_price": self._parse_numeric(row.get("TDD_CLSPRC")),
            "volume": self._parse_numeric(row.get("ACC_TRDVOL")),
            "trading_value": self._parse_numeric(row.get("ACC_TRDVAL")),
            "market_cap": self._parse_numeric(row.get("MKTCAP")),
            "outstanding_shares": self._parse_numeric(row.get("LIST_SHRS")),
            "market": market,
            "asset_type": asset_type,
            "source": "KRX",
            "available_at": generate_available_at_for_eod(target_date).isoformat(),
        }

    def fetch_market_classification_map(self) -> Dict[str, Dict[str, str]]:
        """Return symbol -> {name, market} using FinanceDataReader stock listings."""
        try:
            df = fdr.StockListing("KRX")
            if df is None or df.empty:
                return {}

            symbol_col = "Code" if "Code" in df.columns else "Symbol" if "Symbol" in df.columns else None
            name_col = "Name" if "Name" in df.columns else None
            market_col = "Market" if "Market" in df.columns else None
            if not symbol_col or not name_col or not market_col:
                logger.warning(f"FDR listing missing expected columns: {list(df.columns)}")
                return {}

            mapping = {}
            for _, row in df.iterrows():
                symbol = str(row.get(symbol_col, "")).strip()
                if not symbol:
                    continue
                mapping[symbol] = {
                    "name": str(row.get(name_col, "")).strip(),
                    "market": self._normalize_market_label(str(row.get(market_col, "")).strip()),
                }
            return mapping
        except Exception as exc:
            logger.warning(f"Failed to fetch market classification map from FDR: {exc}")

        try:
            from pykrx import stock

            mapping = {}
            for market in ("KOSPI", "KOSDAQ", "KONEX"):
                for ticker in stock.get_market_ticker_list(market=market):
                    mapping[ticker] = {
                        "name": stock.get_market_ticker_name(ticker),
                        "market": market,
                    }

            try:
                for ticker in stock.get_etf_ticker_list():
                    mapping[ticker] = {
                        "name": stock.get_etf_ticker_name(ticker),
                        "market": "ETF",
                    }
            except Exception as etf_exc:
                logger.warning(f"Failed to fetch ETF ticker list from pykrx: {etf_exc}")

            return mapping
        except Exception as exc:
            logger.warning(f"Failed to fetch market classification map from pykrx: {exc}")
            return {}

    def fetch_kind_listings(self) -> List[Dict[str, str]]:
        return self._fetch_full_universe_from_kind()

    def fetch_fdr_krx_listings(self) -> List[Dict[str, str]]:
        mapping = self.fetch_market_classification_map()
        rows = [
            {"code": symbol, "name": info.get("name", ""), "market": info.get("market")}
            for symbol, info in mapping.items()
            if info.get("market") in {"KOSPI", "KOSDAQ"}
        ]
        return sorted(rows, key=lambda row: row["code"])

    def fetch_pykrx_market_listings(self, target_date: Optional[date] = None) -> List[Dict[str, str]]:
        try:
            from pykrx import stock

            query_base = target_date or date.today()
            rows: Dict[str, Dict[str, str]] = {}
            for offset in range(0, 14):
                query_date = query_base - timedelta(days=offset)
                query_ymd = query_date.strftime("%Y%m%d")
                for market in ("KOSPI", "KOSDAQ"):
                    try:
                        tickers = stock.get_market_ticker_list(query_ymd, market=market)
                    except Exception as market_exc:
                        logger.warning(
                            f"pykrx listing failed for {market} on {query_ymd}: {market_exc}"
                        )
                        tickers = []
                    for ticker in tickers or []:
                        rows[ticker] = {
                            "code": ticker,
                            "name": stock.get_market_ticker_name(ticker),
                            "market": market,
                        }
                if rows:
                    break
            return sorted(rows.values(), key=lambda row: row["code"])
        except Exception as exc:
            logger.warning(f"Failed to fetch pykrx market listings: {exc}")
            return []

    def fetch_full_universe(self, target_date: Optional[date] = None) -> List[Dict[str, str]]:
        """Return the full KOSPI/KOSDAQ listed universe for price ingestion.

        KIS is still the source used for per-symbol OHLCV collection, but the
        complete listing universe is more reliably sourced from KRX/FDR/pykrx.
        """
        kind_rows = self._fetch_full_universe_from_kind()
        if len(kind_rows) >= 2000:
            return kind_rows

        mapping = self.fetch_market_classification_map()
        universe = [
            {"code": symbol, "name": info.get("name", ""), "market": info.get("market")}
            for symbol, info in mapping.items()
            if info.get("market") in {"KOSPI", "KOSDAQ"}
        ]
        if len(universe) >= 2000:
            return sorted(universe, key=lambda row: row["code"])

        try:
            from pykrx import stock

            query_base = target_date or date.today()
            pykrx_map: Dict[str, Dict[str, str]] = {}
            for offset in range(0, 14):
                query_date = query_base - timedelta(days=offset)
                query_ymd = query_date.strftime("%Y%m%d")
                for market in ("KOSPI", "KOSDAQ"):
                    try:
                        tickers = stock.get_market_ticker_list(query_ymd, market=market)
                    except Exception as market_exc:
                        logger.warning(
                            f"pykrx full universe failed for {market} on {query_ymd}: {market_exc}"
                        )
                        tickers = []

                    for ticker in tickers or []:
                        pykrx_map[ticker] = {
                            "code": ticker,
                            "name": stock.get_market_ticker_name(ticker),
                            "market": market,
                        }

                if len(pykrx_map) >= 2000:
                    return sorted(pykrx_map.values(), key=lambda row: row["code"])

            if pykrx_map:
                logger.warning(f"Full universe from pykrx is unexpectedly small: {len(pykrx_map)}")
                return sorted(pykrx_map.values(), key=lambda row: row["code"])
        except Exception as exc:
            logger.warning(f"Failed to fetch full universe from pykrx: {exc}")

        logger.warning(f"Full universe fallback is small: {len(universe)}")
        return sorted(universe, key=lambda row: row["code"])

    def _fetch_full_universe_from_kind(self) -> List[Dict[str, str]]:
        market_urls = {
            "KOSPI": "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType=stockMkt",
            "KOSDAQ": "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType=kosdaqMkt",
        }
        rows: List[Dict[str, str]] = []
        for market, url in market_urls.items():
            try:
                response = requests.get(url, headers=self.headers, timeout=20)
                response.raise_for_status()
                html = response.content.decode("euc-kr", errors="replace")
                tables = pd.read_html(StringIO(html), flavor="lxml")
                if not tables:
                    logger.warning(f"KRX KIND full universe returned no table for {market}")
                    continue
                df = tables[0]
                required = {"회사명", "종목코드"}
                if not required.issubset(set(df.columns)):
                    logger.warning(f"KRX KIND columns missing for {market}: {list(df.columns)}")
                    continue

                for _, row in df.iterrows():
                    code = str(row.get("종목코드", "")).strip()
                    name = str(row.get("회사명", "")).strip()
                    if not code or not name or code.lower() == "nan":
                        continue
                    rows.append(
                        {
                            "code": code.zfill(6) if code.isdigit() else code,
                            "name": name,
                            "market": market,
                        }
                    )
            except Exception as exc:
                logger.warning(f"Failed to fetch full universe from KRX KIND for {market}: {exc}")

        deduped = {row["code"]: row for row in rows}
        logger.info(f"KRX KIND full universe loaded: {len(deduped)} symbols")
        return sorted(deduped.values(), key=lambda row: row["code"])

    def fetch_daily_ohlcv(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """FinanceDataReader를 이용한 OHLCV 수집"""
        try:
            dt_str = target_date.strftime("%Y-%m-%d")
            df = fdr.DataReader(symbol, dt_str, dt_str)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            return {
                "symbol": symbol,
                "base_date": target_date.strftime("%Y%m%d"),
                "open": int(row.get("Open", 0)),
                "high": int(row.get("High", 0)),
                "low": int(row.get("Low", 0)),
                "close": int(row.get("Close", 0)),
                "volume": int(row.get("Volume", 0)),
                "trading_value": int(row.get("Amount", 0)) if "Amount" in row else 0,
                "market_cap": 0,
                "outstanding_shares": 0
            }
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} via FDR: {e}")
            return None

    def fetch_market_breadth(self, target_date: date) -> Optional[Dict[str, Any]]:
        """
        KRX OPEN API '전종목 시세' 엔드포인트를 호출하여 Market Breadth 수치를 산출합니다.
        승인 대기 중(404, 403 등)일 경우 에러 대신 경고 로그만 남기고 None을 리턴합니다.
        """
        fallback = self._fetch_market_breadth_pykrx(target_date)
        if fallback:
            return fallback

        if not self.auth_key:
            logger.warning("KRX auth_key is missing and pykrx fallback failed. Skipping market breadth.")
            return None

        try:
            # KRX OPEN API '주식 시세 -> 전종목 시세'
            # 엔드포인트: https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr
            url = "https://data.krx.co.kr/svc/apis/sto/stk_bydd_clpr"
            params = {
                "basDd": target_date.strftime("%Y%m%d")
            }
            headers = {"AUTH_KEY": self.auth_key}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            # 승인 전(404) 혹은 권한 부족(403) 시 중단 방지
            if response.status_code != 200:
                logger.warning(f"KRX API pending approval (Status: {response.status_code}). Skipping.")
                return None
            
            data = response.json()
            items = data.get("OutBlock_1", [])
            if not items:
                return None
            
            df = pd.DataFrame(items)
            
            # KOSPI 종목 필터링
            if "MKT_ID" in df.columns:
                df = df[df["MKT_ID"] == "STK"]
            
            if df.empty:
                return None
            
            # 필요 컬럼 숫자형 변환 (FLUC_RT: 등락률, TDD_VLM: 거래량)
            df["FLUC_RT"] = pd.to_numeric(df["FLUC_RT"], errors='coerce').fillna(0)
            df["TDD_VLM"] = pd.to_numeric(df["TDD_VLM"], errors='coerce').fillna(0)
            
            advances_df = df[df["FLUC_RT"] > 0]
            declines_df = df[df["FLUC_RT"] < 0]
            unchanged_df = df[df["FLUC_RT"] == 0]
            
            return {
                "base_date": target_date.strftime("%Y-%m-%d"),
                "advances": int(len(advances_df)),
                "declines": int(len(declines_df)),
                "unchanged": int(len(unchanged_df)),
                "advancing_volume": int(advances_df["TDD_VLM"].sum()),
                "declining_volume": int(declines_df["TDD_VLM"].sum())
            }
            
        except Exception as e:
            logger.warning(f"KRX API pending approval or error: {e}. Skipping.")
            return None

    def _fetch_market_breadth_pykrx(self, target_date: date) -> Optional[Dict[str, Any]]:
        try:
            from pykrx import stock

            for offset in range(0, 7):
                query_date = target_date - timedelta(days=offset)
                frames = []
                for market in ("KOSPI", "KOSDAQ"):
                    df = stock.get_market_ohlcv_by_ticker(query_date.strftime("%Y%m%d"), market=market)
                    if df is not None and not df.empty:
                        frames.append(df)

                if not frames:
                    continue

                df_all = pd.concat(frames)
                change_col = "등락률" if "등락률" in df_all.columns else None
                volume_col = "거래량" if "거래량" in df_all.columns else None
                if not change_col:
                    logger.warning(f"pykrx market breadth missing change-rate column: {list(df_all.columns)}")
                    return None

                change = pd.to_numeric(df_all[change_col], errors="coerce").fillna(0)
                volume = (
                    pd.to_numeric(df_all[volume_col], errors="coerce").fillna(0)
                    if volume_col
                    else pd.Series(0, index=df_all.index)
                )

                advances = change > 0
                declines = change < 0
                unchanged = change == 0
                return {
                    "base_date": query_date.strftime("%Y-%m-%d"),
                    "advances": int(advances.sum()),
                    "declines": int(declines.sum()),
                    "unchanged": int(unchanged.sum()),
                    "advancing_volume": int(volume[advances].sum()),
                    "declining_volume": int(volume[declines].sum()),
                }
            return None
        except Exception as exc:
            logger.warning(f"pykrx market breadth fallback failed: {exc}")
            return None
