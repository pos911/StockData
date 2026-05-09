import os
import json
import argparse
import pandas as pd
import numpy as np
from datetime import date, timedelta, datetime
from typing import List, Dict, Any

from src.utils.logger import get_logger
from src.utils.time_utils import get_current_kst, generate_available_at_for_eod
from src.loaders.supabase_loader import SupabaseLoader
from src.utils.config_loader import load_config
from src.utils.market_data_quality import is_valid_price_row
from src.utils.symbols import normalize_symbol_value

logger = get_logger(__name__)

FEATURE_SOURCE_PRIORITY = {
    "KIS_DETAIL": 0,
    "KIS": 1,
    "KRX": 2,
    "VALID_PRICE_FALLBACK": 3,
    "UNKNOWN": 4,
}

RECOMPUTED_FEATURE_NAMES = [
    "close_price",
    "return_5d",
    "return_20d",
    "return_60d",
    "moving_avg_5",
    "moving_avg_20",
    "trading_value_ratio_20d",
    "volatility_20d",
    "foreign_flow_zscore",
    "volume",
]

class FeatureGenerator:
    """
    Normalized 데이터를 바탕으로 실제 Trading Algorithmic Feature들을 생성합니다.
    pandas를 사용하여 이동평균, 변동성, 수급 Z-Score 등을 계산합니다.
    """
    def __init__(self, loader: SupabaseLoader):
        self.loader = loader

    @staticmethod
    def _deduplicate_feature_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped = {}
        for record in records:
            normalized_symbol = normalize_symbol_value(record.get("symbol"))
            record["symbol"] = normalized_symbol
            key = (normalized_symbol, record.get("base_date"), record.get("feature_name"))
            if any(value in (None, "") for value in key):
                continue
            value = record.get("feature_value")
            if value is None or not np.isfinite(value):
                continue
            deduped[key] = record
        return list(deduped.values())

    def _feature_quality_columns_available(self) -> tuple[bool, bool]:
        cached = getattr(self, "_feature_quality_columns_cache", None)
        if cached is not None:
            return cached
        available = (False, False)
        try:
            self.loader.client.table("feature_store_daily").select(
                "data_quality_flag,source_consistency_status"
            ).limit(1).execute()
            available = (True, True)
        except Exception as exc:
            logger.warning(
                "feature_store_daily quality columns are unavailable. "
                f"Run sql/add_feature_data_quality_flags.sql to persist source-quality flags. note={exc}"
            )
        self._feature_quality_columns_cache = available
        return available

    @staticmethod
    def _window_source_status(group: pd.DataFrame, target_base_date: str, periods: int) -> Dict[str, Any]:
        window = group[group["base_date"] <= target_base_date].tail(periods + 1)
        if len(window) < periods + 1:
            return {"ready": False, "source_mixed": False, "sources": {}}
        sources = [str(value or "UNKNOWN") for value in window["source"].tolist()]
        counts = pd.Series(sources).value_counts().to_dict()
        return {"ready": True, "source_mixed": len(counts) > 1, "sources": counts}

    @staticmethod
    def _detect_price_scale_warnings(symbol: str, group: pd.DataFrame, target_base_date: str) -> list[str]:
        warnings: list[str] = []
        last_row = group[group["base_date"] == target_base_date]
        if last_row.empty:
            return warnings
        latest_close = float(last_row.iloc[0].get("close_price") or 0)
        bounds = {
            "005930": (50_000, 500_000),
            "000660": (200_000, 3_000_000),
            "071050": (50_000, 500_000),
            "278470": (100_000, 1_000_000),
            "058470": (50_000, 500_000),
        }
        if symbol in bounds:
            low, high = bounds[symbol]
            if latest_close and not (low <= latest_close <= high):
                warnings.append("WARN_PRICE_SCALE_ANOMALY")

        trailing = (
            group[group["base_date"] < target_base_date]["close_price"]
            .dropna()
            .astype(float)
            .tail(20)
            .tolist()
        )
        if len(trailing) >= 5:
            baseline = float(np.median(trailing))
            if baseline > 0:
                ratio = latest_close / baseline
                if ratio >= 1.8 or ratio <= 0.55:
                    warnings.append("WARN_PRICE_JUMP_ANOMALY")
        return sorted(set(warnings))

    @staticmethod
    def _build_feature_record(
        symbol: str,
        base_date: str,
        feature_name: str,
        feature_value: Any,
        available_at: str,
        include_quality_columns: tuple[bool, bool],
        data_quality_flag: str | None = None,
        source_consistency_status: str | None = None,
    ) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "symbol": symbol,
            "base_date": base_date,
            "feature_name": feature_name,
            "feature_value": float(feature_value),
            "available_at": available_at,
        }
        if include_quality_columns[0]:
            record["data_quality_flag"] = data_quality_flag
        if include_quality_columns[1]:
            record["source_consistency_status"] = source_consistency_status
        return record

    @staticmethod
    def _source_priority(value: Any) -> int:
        normalized = str(value or "UNKNOWN").strip().upper()
        return FEATURE_SOURCE_PRIORITY.get(normalized, FEATURE_SOURCE_PRIORITY["UNKNOWN"])

    def _apply_price_source_priority(self, prices_df: pd.DataFrame) -> pd.DataFrame:
        if prices_df.empty:
            return prices_df
        prioritized = prices_df.copy()
        if "source" not in prioritized.columns:
            prioritized["source"] = "UNKNOWN"
        prioritized["source"] = prioritized["source"].fillna("UNKNOWN")
        prioritized["source_priority"] = prioritized["source"].map(self._source_priority)
        sort_columns = ["symbol", "base_date", "source_priority"]
        ascending = [True, True, True]
        if "available_at" in prioritized.columns:
            sort_columns.append("available_at")
            ascending.append(False)
        prioritized = prioritized.sort_values(sort_columns, ascending=ascending)
        prioritized = prioritized.drop_duplicates(subset=["symbol", "base_date"], keep="first")
        prioritized = prioritized.drop(columns=["source_priority"], errors="ignore")
        return prioritized.reset_index(drop=True)

    def _delete_recomputed_feature_rows(self, base_date: str, symbols: list[str] | None = None) -> None:
        feature_names = RECOMPUTED_FEATURE_NAMES
        if not symbols:
            try:
                (
                    self.loader.client.table("feature_store_daily")
                    .delete()
                    .eq("base_date", base_date)
                    .neq("symbol", "GLOBAL")
                    .in_("feature_name", feature_names)
                    .execute()
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to delete stale feature rows before recompute. base_date={base_date}, note={exc}"
                )
            return
        for symbol in symbols:
            try:
                (
                    self.loader.client.table("feature_store_daily")
                    .delete()
                    .eq("base_date", base_date)
                    .eq("symbol", symbol)
                    .in_("feature_name", feature_names)
                    .execute()
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to delete stale feature rows before recompute. symbol={symbol}, base_date={base_date}, note={exc}"
                )

    def generate_features_for_date(self, target_date: date) -> int:
        # 1. 대상 종목 리스트 (Universe) 가져오기
        universe = self._load_universe()
        enabled_symbols = [normalize_symbol_value(s["symbol"]) for s in universe]
        
        if not enabled_symbols:
            logger.warning("No enabled symbols found in universe.")
            return 0

        # 2. 데이터 조회 기간 설정 (최근 60일치 확보)
        start_date = target_date - timedelta(days=90) # 주말/공휴일 고려하여 넉넉히 90일
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = target_date.strftime("%Y-%m-%d")
        
        logger.info(f"Fetching data from {start_date_str} to {end_date_str} for {len(enabled_symbols)} symbols...")

        # 3. 데이터 로드 (Price & Supply)
        # Note: 대량 조회를 위해 in_. 연산자 활용 추천되나, 여기선 심플하게 전체 조회 후 필터링하거나 루프 처리
        # 성능을 위해 전체를 한 번에 가져오는 방식으로 구현
        
        prices_df = self._fetch_table_data("normalized_stock_prices_daily", start_date_str, end_date_str)
        supply_df = self._fetch_table_data("normalized_stock_supply_daily", start_date_str, end_date_str)

        if prices_df.empty:
            logger.error("Required Price data is missing in Supabase.")
            return 0
        prices_df = prices_df[
            prices_df.apply(
                lambda row: is_valid_price_row(row.to_dict(), market_is_open=True),
                axis=1,
            )
        ].copy()
        prices_df = self._apply_price_source_priority(prices_df)
        if prices_df.empty:
            logger.error("No valid price rows remain after filtering zero-volume or zero-trading-value rows.")
            return 0

        if supply_df.empty:
            logger.warning("Supply data is empty. Proceeding with price data only.")
            df = prices_df.copy()
            df["foreign_net_buy"] = 0
        else:
            # 4. 데이터 병합 (Merge)
            df = pd.merge(prices_df, supply_df, on=["symbol", "base_date"], how="left")
            df["foreign_net_buy"] = df["foreign_net_buy"].fillna(0)
            if "source" not in df.columns:
                if "source_x" in df.columns:
                    df["source"] = df["source_x"]
                elif "source_y" in df.columns:
                    df["source"] = df["source_y"]

        if "source" not in df.columns:
            df["source"] = "UNKNOWN"

        # 날짜 통일 및 정렬 (요구사항 1: strftime 강제 변환)
        df["base_date"] = pd.to_datetime(df["base_date"]).dt.strftime('%Y-%m-%d')
        target_pd_date = end_date_str

        # 비거래일/휴장일 보정: 타겟 날짜 시세가 없으면 직전 영업일로 fallback
        available_dates = sorted(df["base_date"].dropna().unique().tolist())
        if target_pd_date not in available_dates:
            fallback_dates = [d for d in available_dates if d < target_pd_date]
            if not fallback_dates:
                logger.error(f"No tradable base_date found on or before {target_pd_date}. Skip feature generation.")
                return 0
            fallback_date = fallback_dates[-1]
            logger.warning(
                f"No stock price rows for target_date={target_pd_date}. "
                f"Falling back to latest tradable date={fallback_date}."
            )
            target_pd_date = fallback_date
            end_date_str = fallback_date

        # 로드된 전체 데이터의 날짜 범위를 로그로 출력
        min_date = df["base_date"].min()
        max_date = df["base_date"].max()
        logger.info(f"Data Range: {min_date} to {max_date}")

        # 요구사항 2: 전체 결과 볼륨 출력
        logger.info(f"DB Fetch & Merge Complete. Total rows: {df.shape[0]}")

        # 날짜 정렬 (계산을 위해 필수)
        df = df.sort_values(["symbol", "base_date"]).reset_index(drop=True)
        quality_columns = self._feature_quality_columns_available()

        # 5. 피처 계산 (Feature Engineering)
        logger.info("Calculating technical and supply features...")
        
        feature_records = []
        effective_base_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        available_at_str = generate_available_at_for_eod(effective_base_dt).isoformat()
        
        for symbol, group in df.groupby("symbol"):
            if symbol not in enabled_symbols:
                logger.debug(f"Skip symbol {symbol}: Not in enabled_symbols (stocks_master).")
                continue
                
            group = group.sort_values("base_date")
            n_rows = len(group)
            
            if symbol == "278470":
                logger.info(f"[APR_TARGETING] 에이피알(278470) 처리 시작. 총 데이터 수: {n_rows} 건. 최신 일자: {group['base_date'].max() if not group.empty else '없음'}")
            
            # 가격 지표
            close = group["close_price"]
            
            # 데이터 개수에 비례하여 return 계산 (최대 5일)
            trading_value = group["trading_value"].astype(float)
            group["return_5d"] = close.pct_change(5) if n_rows > 5 else np.nan
            group["return_20d"] = close.pct_change(20) if n_rows > 20 else np.nan
            group["return_60d"] = close.pct_change(60) if n_rows > 60 else np.nan

            group["moving_avg_5"] = close.rolling(5, min_periods=1).mean()
            group["moving_avg_20"] = close.rolling(20, min_periods=1).mean()
            group["trading_value_ratio_20d"] = trading_value / trading_value.rolling(20, min_periods=20).mean()
            
            # 20일 변동성 (수익률의 표준편차)
            group["volatility_20d"] = close.pct_change().rolling(20, min_periods=1).std() if n_rows > 1 else 0.0
            
            # 수급 지표 (외국인 순매수 Z-Score)
            foreign_buy = group["foreign_net_buy"]
            f_mean = foreign_buy.rolling(20, min_periods=1).mean()
            f_std = foreign_buy.rolling(20, min_periods=1).std()
            group["foreign_flow_zscore"] = (foreign_buy - f_mean) / f_std.replace(0, np.nan)
            
            # 마지막 날짜(target_date) 행 추출 (요구사항 1)
            last_row = group[group["base_date"] == target_pd_date]
            if last_row.empty:
                if symbol == "278470":
                    logger.error(f"[APR_TARGETING] 에이피알(278470) 타겟 일자({target_pd_date}) 시세 데이터 없음. 현재 그룹된 데이터 확인 요망.")
                else:
                    logger.debug(f"Symbol [{symbol}]: target_date [{target_pd_date}]에 해당하는 시세 데이터가 그룹 내에 존재하지 않음")
                continue
                
            row = last_row.iloc[0]
            return_5d_sources = self._window_source_status(group, target_pd_date, 5)
            return_20d_sources = self._window_source_status(group, target_pd_date, 20)
            return_60d_sources = self._window_source_status(group, target_pd_date, 60)
            price_scale_warnings = self._detect_price_scale_warnings(symbol, group, target_pd_date)
            if price_scale_warnings:
                logger.warning(
                    f"Price scale anomaly detected for {symbol} on {target_pd_date}: "
                    f"warnings={price_scale_warnings}, close_price={row.get('close_price')}, "
                    f"sources_5d={return_5d_sources['sources']}, sources_20d={return_20d_sources['sources']}"
                )

            targets = {
                "close_price": row.get("close_price"),
                "return_5d": None if return_5d_sources["source_mixed"] else row.get("return_5d"),
                "return_20d": None if return_20d_sources["source_mixed"] else row.get("return_20d"),
                "return_60d": None if return_60d_sources["source_mixed"] else row.get("return_60d"),
                "moving_avg_5": row.get("moving_avg_5"),
                "moving_avg_20": row.get("moving_avg_20"),
                "trading_value_ratio_20d": None if return_20d_sources["source_mixed"] else row.get("trading_value_ratio_20d"),
                "volatility_20d": row.get("volatility_20d", 0.0),
                "foreign_flow_zscore": row.get("foreign_flow_zscore"),
                "volume": row.get("volume", 0)
            }

            for f_name, f_val in targets.items():
                if pd.notnull(f_val):
                    data_quality_flag = None
                    source_consistency_status = None
                    if f_name == "return_5d" and return_5d_sources["source_mixed"]:
                        data_quality_flag = "SOURCE_MIXED"
                        source_consistency_status = "SOURCE_MIXED_5D"
                    elif f_name == "return_20d" and return_20d_sources["source_mixed"]:
                        data_quality_flag = "SOURCE_MIXED"
                        source_consistency_status = "SOURCE_MIXED_20D"
                    elif f_name == "return_60d" and return_60d_sources["source_mixed"]:
                        data_quality_flag = "SOURCE_MIXED"
                        source_consistency_status = "SOURCE_MIXED_60D"
                    elif f_name == "trading_value_ratio_20d" and return_20d_sources["source_mixed"]:
                        data_quality_flag = "SOURCE_MIXED"
                        source_consistency_status = "SOURCE_MIXED_20D"
                    elif price_scale_warnings and f_name in {"close_price", "return_5d", "return_20d", "return_60d"}:
                        data_quality_flag = price_scale_warnings[0]
                        source_consistency_status = "PRICE_SCALE_WARNING"
                    feature_records.append(
                        self._build_feature_record(
                            symbol=symbol,
                            base_date=end_date_str,
                            feature_name=f_name,
                            feature_value=f_val,
                            available_at=available_at_str,
                            include_quality_columns=quality_columns,
                            data_quality_flag=data_quality_flag,
                            source_consistency_status=source_consistency_status,
                        )
                    )
                else:
                    if symbol == "278470":
                        logger.warning(f"[APR_TARGETING] 에이피알(278470) 피처 누락: {f_name} 값이 NaN (총 데이터 수: {n_rows}건)")
                    logger.debug(f"Symbol [{symbol}]: 필요 데이터 부족(총 {n_rows}개 뿐)으로 스킵됨 (Feature {f_name} is NaN)")
        # 7. 글로벌 매크로 피처 계산 (비율 + 자동 모멘텀)
        logger.info("Calculating global macro features (ratios + momentum)...")
        macro_df = self._fetch_table_data("normalized_global_macro_daily", start_date_str, end_date_str)
        if not macro_df.empty:
            macro_df["base_date"] = pd.to_datetime(macro_df["base_date"]).dt.strftime('%Y-%m-%d')
            macro_df = macro_df.sort_values("base_date").reset_index(drop=True)
            # 한/미 휴장일 차이로 발생하는 Null 보정 (ffill → bfill 순차 적용)
            macro_df = macro_df.ffill().bfill()

            # 비율 지표 계산
            if "gold" in macro_df.columns and "copper" in macro_df.columns:
                macro_df["copper_gold_ratio"] = macro_df["copper"] / macro_df["gold"]
            if "gold" in macro_df.columns and "wti" in macro_df.columns:
                macro_df["gold_oil_ratio"] = macro_df["gold"] / macro_df["wti"]

            last_macro = macro_df[macro_df["base_date"] == target_pd_date]
            if not last_macro.empty:
                m_row = last_macro.iloc[0]
                # 기본 비율 피처
                for f_name in ["copper_gold_ratio", "gold_oil_ratio"]:
                    f_val = m_row.get(f_name)
                    if pd.notnull(f_val):
                        feature_records.append({
                            "symbol": "GLOBAL",
                            "base_date": end_date_str,
                            "feature_name": f_name,
                            "feature_value": float(f_val),
                            "available_at": available_at_str
                        })

                # 모든 수치 컬럼에 대해 1d/5d 변화율 자동 계산 (일반화)
                numeric_cols = macro_df.select_dtypes(include=[np.number]).columns.tolist()
                skip_cols = {"copper_gold_ratio", "gold_oil_ratio"}  # 파생 비율은 제외
                for col in numeric_cols:
                    if col in skip_cols:
                        continue
                    col_series = macro_df[macro_df["base_date"] <= target_pd_date][col]
                    if len(col_series) < 2:
                        continue
                    cur = col_series.iloc[-1]
                    # 1d 변화율
                    if len(col_series) >= 2:
                        prev1 = col_series.iloc[-2]
                        chg1d = (cur / pd.Series([prev1]).replace(0, np.nan).iloc[0]) - 1
                        if np.isfinite(chg1d):
                            feature_records.append({
                                "symbol": "GLOBAL",
                                "base_date": end_date_str,
                                "feature_name": f"macro_{col}_1d_chg",
                                "feature_value": float(chg1d),
                                "available_at": available_at_str
                            })
                    # 5d 변화율
                    if len(col_series) >= 6:
                        prev5 = col_series.iloc[-6]
                        chg5d = (cur / pd.Series([prev5]).replace(0, np.nan).iloc[0]) - 1
                        if np.isfinite(chg5d):
                            feature_records.append({
                                "symbol": "GLOBAL",
                                "base_date": end_date_str,
                                "feature_name": f"macro_{col}_5d_chg",
                                "feature_value": float(chg5d),
                                "available_at": available_at_str
                            })

        # 8. normalized_macro_series 모멘텀 피처 (ffill+bfill 보정)
        logger.info("Calculating macro_series momentum features...")
        try:
            _ms_start = (target_date - timedelta(days=45)).strftime("%Y-%m-%d")
            _ms_end = end_date_str
            _ms_data = self.loader.fetch_all(
                table_name="normalized_macro_series",
                date_col="base_date",
                start_date=_ms_start,
                end_date=_ms_end,
                order_col="base_date",
                desc=False
            )
            _ms_df = pd.DataFrame(_ms_data)
            if not _ms_df.empty and "series_id" in _ms_df.columns:
                _ms_df["base_date"] = pd.to_datetime(_ms_df["base_date"]).dt.strftime('%Y-%m-%d')
                _ms_df["value"] = pd.to_numeric(_ms_df["value"], errors="coerce")
                # 시리즈별로 ffill → bfill 순차 적용 (휴장 Null 보정)
                _ms_df = _ms_df.sort_values(["series_id", "base_date"]).reset_index(drop=True)
                _ms_df["value"] = _ms_df.groupby("series_id")["value"].transform(lambda x: x.ffill().bfill())

                # 모든 series_id에 대해 자동 변화율 계산 (하드코딩 제거)
                for series_id, _grp in _ms_df.groupby("series_id"):
                    _s = _grp.sort_values("base_date")
                    feat_prefix = f"macro_{series_id.lower().replace(' ', '_')}"
                    _last = _s[_s["base_date"] <= target_pd_date].tail(1)
                    if _last.empty:
                        continue
                    _cur_val = _last.iloc[0]["value"]
                    # 1일 전 변화율
                    _prev1 = _s[_s["base_date"] < _last.iloc[0]["base_date"]].tail(1)
                    if not _prev1.empty:
                        _p1 = _prev1.iloc[0]["value"]
                        chg1d = (_cur_val / _p1) - 1 if _p1 != 0 else np.nan
                        if np.isfinite(chg1d):
                            feature_records.append({
                                "symbol": "GLOBAL",
                                "base_date": end_date_str,
                                "feature_name": f"{feat_prefix}_1d_chg",
                                "feature_value": float(chg1d),
                                "available_at": available_at_str
                            })
                    # 5일 전 변화율
                    _prev5 = _s[_s["base_date"] < _last.iloc[0]["base_date"]].tail(5).head(1)
                    if not _prev5.empty:
                        _p5 = _prev5.iloc[0]["value"]
                        chg5d = (_cur_val / _p5) - 1 if _p5 != 0 else np.nan
                        if np.isfinite(chg5d):
                            feature_records.append({
                                "symbol": "GLOBAL",
                                "base_date": end_date_str,
                                "feature_name": f"{feat_prefix}_5d_chg",
                                "feature_value": float(chg5d),
                                "available_at": available_at_str
                            })

                def _series_slice(series_id: str):
                    return _ms_df[
                        (_ms_df["series_id"] == series_id) &
                        (_ms_df["base_date"] <= target_pd_date)
                    ].sort_values("base_date")

                def _append_global_feature(name: str, value):
                    if value is None or not np.isfinite(value):
                        return
                    feature_records.append({
                        "symbol": "GLOBAL",
                        "base_date": end_date_str,
                        "feature_name": name,
                        "feature_value": float(value),
                        "available_at": available_at_str,
                    })

                kr10y_series = _series_slice("KR_GOVT_10Y")
                kr3y_series = _series_slice("KR_GOVT_3Y")
                kraa_series = _series_slice("KR_CORP_AA_3Y")
                krbbb_series = _series_slice("KR_CORP_BBB_3Y")
                usdkrw_series = _series_slice("USDKRW")

                if not kr10y_series.empty and not kr3y_series.empty:
                    _append_global_feature(
                        "KR_YIELD_SPREAD_10Y_3Y",
                        kr10y_series.iloc[-1]["value"] - kr3y_series.iloc[-1]["value"],
                    )

                if not kraa_series.empty and not kr3y_series.empty:
                    _append_global_feature(
                        "KR_CREDIT_SPREAD_AA_3Y",
                        kraa_series.iloc[-1]["value"] - kr3y_series.iloc[-1]["value"],
                    )

                if not krbbb_series.empty and not kr3y_series.empty:
                    _append_global_feature(
                        "KR_CREDIT_SPREAD_BBB_3Y",
                        krbbb_series.iloc[-1]["value"] - kr3y_series.iloc[-1]["value"],
                    )

                if len(usdkrw_series) >= 2:
                    current = usdkrw_series.iloc[-1]["value"]
                    previous = usdkrw_series.iloc[-2]["value"]
                    if previous != 0:
                        _append_global_feature(
                            "USDKRW_1D_CHG_PCT",
                            ((current / previous) - 1) * 100,
                        )

                if len(kr10y_series) >= 2:
                    _append_global_feature(
                        "KR10Y_1D_CHG_BP",
                        (kr10y_series.iloc[-1]["value"] - kr10y_series.iloc[-2]["value"]) * 100,
                    )

                if len(kr10y_series) >= 21:
                    _append_global_feature(
                        "KR10Y_20D_CHG_BP",
                        (kr10y_series.iloc[-1]["value"] - kr10y_series.iloc[-21]["value"]) * 100,
                    )
        except Exception as _me:
            logger.warning(f"Macro momentum feature calculation failed (non-fatal): {_me}")

        # 6. Feature Store 저장
        # 요구사항 3: 강제 확인 로직
        original_count = len(feature_records)
        feature_records = self._deduplicate_feature_records(feature_records)
        record_count = len(feature_records)
        if record_count != original_count:
            logger.warning(f"Deduplicated feature records before upsert: {original_count} -> {record_count}")
        logger.info(f"Target count to upsert: {record_count}")
        
        if record_count > 0:
            self._delete_recomputed_feature_rows(end_date_str)
            success = self.loader.upsert_records("feature_store_daily", feature_records)
            if not success:
                logger.error(f"Feature upsert failed for {target_date}.")
                return 0
            logger.info(f"Successfully upserted {record_count} real features for {target_date}.")
        else:
            logger.error(f"ERROR: No features calculated for the given date. Check if input data exists for {target_date}")
        return record_count

    def _fetch_table_data(self, table_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Supabase에서 특정 기간의 데이터를 DataFrame으로 로드"""
        try:
            # 자동 페이징 기능이 포함된 fetch_all 메서드 호출
            data = self.loader.fetch_all(table_name=table_name, 
                                         date_col="base_date", 
                                         start_date=start_date, 
                                         end_date=end_date, 
                                         order_col="base_date", 
                                         desc=True)
            
            df = pd.DataFrame(data)
            if not df.empty and "symbol" in df.columns:
                df["symbol"] = df["symbol"].map(normalize_symbol_value)
            logger.info(f"Loaded {len(df)} rows from {table_name}")
            return df
        except Exception as e:
            logger.error(f"Error fetching {table_name}: {e}")
            return pd.DataFrame()

    def _load_universe(self):
        try:
            static_rows = (
                self.loader.client.table("static_stock_universe")
                .select("symbol, name")
                .eq("enabled", True)
                .execute()
                .data
                or []
            )
            latest_rank_res = (
                self.loader.client.table("normalized_market_rankings_daily")
                .select("base_date")
                .order("base_date", desc=True)
                .limit(1)
                .execute()
            )
            latest_rank_date = (latest_rank_res.data or [{}])[0].get("base_date")
            ranking_rows = []
            if latest_rank_date:
                ranking_rows = (
                    self.loader.client.table("normalized_market_rankings_daily")
                    .select("symbol, name")
                    .eq("base_date", latest_rank_date)
                    .eq("source", "KIS")
                    .execute()
                    .data
                    or []
                )
            combined = {}
            for row in static_rows + ranking_rows:
                symbol = normalize_symbol_value(row.get("symbol"))
                if not symbol:
                    continue
                combined[symbol] = {"symbol": symbol, "name": row.get("name") or symbol}
            if combined:
                return list(combined.values())
            logger.warning("No KIS detail universe symbols found in static_stock_universe or latest KIS ranking rows.")
            return []
        except Exception as e:
            logger.error(f"Error loading universe from DB: {e}")
            return []

def run_job(target_date: date) -> int:
    logger.info(f"Starting Real Feature Engineering Job for {target_date}...")
    config = load_config()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    
    generator = FeatureGenerator(loader)
    processed = int(generator.generate_features_for_date(target_date) or 0)
    status = "SUCCESS" if processed > 0 else "WARN"
    loader.insert_log("daily_feature_generator", target_date.strftime("%Y-%m-%d"), status, processed)
    logger.info(f"Feature Engineering Finished. status={status}, processed={processed}")
    return processed

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="YYYYMMDD format")
    args = parser.parse_args()
    
    target_dt = get_current_kst().date()
    if args.date:
         from src.utils.time_utils import parse_date_string
         target_dt = parse_date_string(args.date)
         
    run_job(target_dt)
