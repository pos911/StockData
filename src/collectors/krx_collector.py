from datetime import date, datetime
from typing import Dict, Any, Optional, List
import requests
from bs4 import BeautifulSoup
import FinanceDataReader as fdr
import time
from src.utils.logger import get_logger
from src.utils.http_client import HttpClient

logger = get_logger(__name__)

class KRXCollector:
    """한국거래소 및 네이버 금융 기반 수집기 (FinanceDataReader + Naver Fallback)"""
    def __init__(self, auth_key: str = ""):
        self.auth_key = auth_key
        self.client = HttpClient()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def fetch_daily_ohlcv(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """FinanceDataReader를 이용한 OHLCV 수집"""
        try:
            dt_str = target_date.strftime("%Y-%m-%d")
            # fdr.DataReader는 특정 기간의 데이터를 반환함
            df = fdr.DataReader(symbol, dt_str, dt_str)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            # fdr은 시가총액/상장주식수를 OHLCV와 함께 주지 않는 경우가 많으므로 보완 필요
            # 일단 기본 필드 위주로 반환
            return {
                "symbol": symbol,
                "base_date": target_date.strftime("%Y%m%d"),
                "open": int(row.get("Open", 0)),
                "high": int(row.get("High", 0)),
                "low": int(row.get("Low", 0)),
                "close": int(row.get("Close", 0)),
                "volume": int(row.get("Volume", 0)),
                "trading_value": 0, # fdr 기본에 없을 수 있음
                "market_cap": 0,
                "outstanding_shares": 0
            }
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} via FDR: {e}")
            return None

    def fetch_daily_investor_supply(self, symbol: str, target_date: date) -> Optional[Dict[str, Any]]:
        """네이버 금융 스크래핑을 이용한 투자자별 수급 수집"""
        # Naver Finance에서는 페이지별로 20일치씩 데이터를 제공함
        # 특정 날짜를 찾기 위해 페이지를 순회하거나, 최근 데이터 위주로 처리
        try:
            target_dt_str = target_date.strftime("%Y.%m.%d")
            
            # 1페이지부터 검색 (최대 40페이지까지 탐색 시 약 1년치 거래일 커버 가능)
            for page in range(1, 41):
                url = f"https://finance.naver.com/item/frgn.naver?code={symbol}&page={page}"
                # SSL 인증서 검증 건너뛰기 (현재 환경 이슈 해결용)
                res = requests.get(url, headers=self.headers, verify=False)
                soup = BeautifulSoup(res.text, "html.parser")
                
                table = soup.find("table", attrs={"summary": "외국인 기관 순매매량 상세 정보"})
                if not table:
                    break
                
                rows = table.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) < 9:
                        continue
                    
                    # Naver 날짜는 보통 span.tah.p10에 위치함
                    date_elem = row.select_one("span.tah.p10")
                    if not date_elem:
                        continue
                    
                    date_text = date_elem.text.strip()
                    if date_text == target_dt_str:
                        # 데이터 파싱: 기관(5), 외국인(6), 외국인보유율(8)
                        try:
                            inst_net = int(cols[5].text.strip().replace(",", ""))
                            for_net = int(cols[6].text.strip().replace(",", ""))
                            # 보유율은 8번 컬럼
                            for_ratio_text = cols[8].text.strip().replace("%", "")
                            for_ratio = float(for_ratio_text) if for_ratio_text else 0.0
                            
                            logger.info(f"Successfully scraped Naver supply for {symbol} on {date_text}")
                            return {
                                "symbol": symbol,
                                "base_date": target_date.strftime("%Y%m%d"),
                                "foreign_net_buy": for_net,
                                "institutional_net_buy": inst_net,
                                "individual_net_buy": 0,
                                "foreign_holding_ratio": for_ratio,
                                "short_volume": 0,
                                "short_balance": 0,
                                "lending_balance": 0
                            }
                        except (ValueError, IndexError) as e:
                            logger.error(f"Error parsing Naver row for {symbol}: {e}")
                            continue
            
            return None
        except Exception as e:
            logger.error(f"Error fetching supply for {symbol} via Naver: {e}")
            return None

    def fetch_supply_history(self, symbol: str, pages: int = 40) -> List[Dict[str, Any]]:
        """네이버 금융에서 다량의 수급 이력을 한 번에 스크래핑 (백필용)"""
        results = []
        try:
            for page in range(1, pages + 1):
                url = f"https://finance.naver.com/item/frgn.naver?code={symbol}&page={page}"
                res = requests.get(url, headers=self.headers, verify=False)
                soup = BeautifulSoup(res.text, "html.parser")
                
                table = soup.find("table", attrs={"summary": "외국인 기관 순매매량 상세 정보"})
                if not table:
                    break
                
                rows = table.find_all("tr")
                page_added = 0
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) < 9:
                        continue
                    
                    date_elem = row.select_one("span.tah.p10")
                    if not date_elem:
                        continue
                    
                    date_text = date_elem.text.strip()
                    try:
                        # 날짜 변환 (YYYY.MM.DD -> YYYYMMDD)
                        base_date_str = date_text.replace(".", "")
                        inst_net = int(cols[5].text.strip().replace(",", ""))
                        for_net = int(cols[6].text.strip().replace(",", ""))
                        for_ratio_text = cols[8].text.strip().replace("%", "")
                        for_ratio = float(for_ratio_text) if for_ratio_text else 0.0
                        
                        results.append({
                            "symbol": symbol,
                            "base_date": base_date_str,
                            "foreign_net_buy": for_net,
                            "institutional_net_buy": inst_net,
                            "individual_net_buy": 0,
                            "foreign_holding_ratio": for_ratio,
                            "short_volume": 0,
                            "short_balance": 0,
                            "lending_balance": 0
                        })
                        page_added += 1
                    except (ValueError, IndexError):
                        continue
                
                if page_added == 0:
                    break
                    
                time.sleep(0.1)
                
            return results
        except Exception as e:
            logger.error(f"Error fetching supply history for {symbol}: {e}")
            return results
