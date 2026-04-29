import os
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List

from src.utils.logger import get_logger
from src.utils.config_loader import load_config
from src.utils.universe_loader import load_universe
from src.loaders.supabase_loader import SupabaseLoader
from src.features.signal_generator import SignalGenerator

logger = get_logger(__name__)

def generate_report():
    logger.info("Starting Daily Quant Investment Report generation...")
    
    # 1. 라이브러리 및 DB 로드
    config = load_config()
    universe = load_universe()
    loader = SupabaseLoader(url=config["supabase"]["url"], key=config["supabase"]["service_role_key"])
    signal_gen = SignalGenerator()
    
    # 종목명 매핑
    symbol_to_name = {s["symbol"]: s["name"] for s in universe}
    
    # 최신 영업일(base_date) 찾기
    macro_latest_res = loader.client.table("normalized_global_macro_daily") \
        .select("*") \
        .order("base_date", desc=True) \
        .limit(1) \
        .execute()
        
    if not macro_latest_res.data:
        print("데이터가 존재하지 않습니다: normalized_global_macro_daily")
        return
        
    latest_macro = macro_latest_res.data[0]
    target_date = latest_macro["base_date"]
    us10y = latest_macro.get("us10y", 0)
    usdkrw = latest_macro.get("usdkrw", 0)
    
    # 2. 데이터 페칭 및 시그널 계산
    features_res = loader.client.table("feature_store_daily") \
        .select("*") \
        .eq("base_date", target_date) \
        .execute()
        
    df_features = pd.DataFrame(features_res.data)
    if df_features.empty:
        print(f"해당 날짜({target_date})의 피처 데이터가 존재하지 않습니다.")
        return
        
    # symbol별 피처 딕셔너리 변환
    symbol_features = {}
    for symbol, group in df_features.groupby("symbol"):
        symbol_features[symbol] = dict(zip(group["feature_name"], group["feature_value"]))
        
    # 시그널 계산
    all_signals = []
    for symbol, features in symbol_features.items():
        sig_info = signal_gen.generate_signal(features)
        all_signals.append({
            "symbol": symbol,
            "name": symbol_to_name.get(symbol, symbol),
            "total_score": sig_info["total_score"],
            "signal": sig_info["signal"],
            "features": features
        })
        
    df_results = pd.DataFrame(all_signals)
    
    # 3. 시황 분석 및 투자 적정 시기
    buy_count = len(df_results[df_results["signal"] == "BUY"])
    total_count = len(df_results)
    buy_ratio = (buy_count / total_count * 100) if total_count > 0 else 0
    
    conclusion = ""
    if buy_ratio >= 40:
        conclusion = "공격적 매수(Risk-On) 적기"
    elif buy_ratio >= 20:
        conclusion = "개별 종목 장세 및 보수적 접근"
    else:
        conclusion = "현금 관망(Risk-Off) 시기"
        
    # 4. 퀀트 기반 투자 추천 종목 (Top 5)
    top_5 = df_results.sort_values("total_score", ascending=False).head(5)
    
    # 5. 특정 3사 집중 검토
    target_symbols = {
        "222800": "심텍",
        "004020": "현대제철",
        "017670": "SK텔레콤"
    }

    # --- 리포트 내용 조립 ---
    report = []
    report.append(f"# 📊 Daily Quant Investment Report ({target_date})")
    report.append("\n## 1. 현재 시황 및 투자 적정 시기")
    report.append(f"- 매크로 지표: 원달러 환율 {usdkrw:,.2f}원, 미 국채 10년물 {us10y}%")
    report.append(f"- 시장 강도: 전체 추적 종목 중 {buy_ratio:.1f}%가 매수(BUY) 시그널 발생.")
    report.append(f"- [결론]: **{conclusion}**")
    
    report.append("\n## 2. 퀀트 기반 추천 종목 (Top 5)")
    for i, (idx, row) in enumerate(top_5.iterrows(), 1):
        zscore = row["features"].get("foreign_flow_zscore", 0)
        report.append(f"{i}위: {row['name']}({row['symbol']}) - 총점: {row['total_score']:.3f}점 (외인수급 Z-Score: {zscore:.2f})")
        
    report.append("\n## 3. 타겟 3사 집중 검토")
    for symbol, name in target_symbols.items():
        stock = df_results[df_results["symbol"] == symbol]
        if stock.empty:
            report.append(f"\n### [{name}] ({symbol}) - 데이터 부족")
            continue
            
        row = stock.iloc[0]
        f = row["features"]
        ma5 = f.get("moving_avg_5", 0)
        ma20 = f.get("moving_avg_20", 0)
        zscore = f.get("foreign_flow_zscore", 0)
        
        trend_msg = "단기 이평선 정배열 (긍정적)" if ma5 > ma20 else "역배열 상태 (보수적)"
        supply_msg = "외국인 매수 우위" if zscore > 0 else "외국인 매도 우위"
        comment = "공격적인 비중 확대 추천" if row["signal"] == "BUY" else "보수적인 관망 유지 추천"
        
        report.append(f"\n### [{name}] ({symbol})")
        report.append(f"- 현재 시그널: {row['signal']} (총점: {row['total_score']:.3f}점)")
        report.append(f"- 단기 추세: {trend_msg}")
        report.append(f"- 수급 동향: {supply_msg}")
        report.append(f"- 💡 최종 코멘트: **{comment}**")

    final_report = "\n".join(report)
    
    # 6. 리포트 파일 저장
    report_dir = "reports"
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"daily_report_{target_date}.md")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(final_report)
        
    print(f"\n✅ 리포트가 파일로 저장되었습니다: {os.path.abspath(report_path)}")
    print("-" * 50)
    print(final_report)

if __name__ == "__main__":
    generate_report()
