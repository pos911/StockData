import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class MiniBacktester:
    """
    모의 백테스트 엔진 (Risk Management 포함)
    """
    def __init__(self, initial_capital: float = 100000000.0):
        self.capital = initial_capital
        self.positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position info
        
        # 리스크 설정
        self.max_positions = 5
        self.stop_loss_pct = -0.05
        self.take_profit_pct = 0.15
        
    def step(self, current_date: str, signals: Dict[str, Dict[str, Any]], prices: Dict[str, float]):
        """
        매일(Day)의 시점에서 실행
        """
        logger.info(f"--- Backtest Step: {current_date} ---")
        
        # 1. 포지션 청산 조건 확인 (Risk Management)
        symbols_to_close = []
        for sym, pos in self.positions.items():
            if sym in prices:
                current_price = prices[sym]
                avg_price = pos["avg_price"]
                pnl_pct = (current_price - avg_price) / avg_price
                
                # Stop Loss / Take Profit
                if pnl_pct <= self.stop_loss_pct:
                    logger.info(f"STOP LOSS hit for {sym}: {pnl_pct*100:.2f}%")
                    symbols_to_close.append(sym)
                elif pnl_pct >= self.take_profit_pct:
                    logger.info(f"TAKE PROFIT hit for {sym}: {pnl_pct*100:.2f}%")
                    symbols_to_close.append(sym)
                elif signals.get(sym, {}).get("signal") == "SELL":
                    logger.info(f"SELL signal for {sym}")
                    symbols_to_close.append(sym)
                    
        for sym in symbols_to_close:
            self._close_position(sym, prices[sym])

        # 2. 신규 진입 (Risk Management: Position Sizing)
        for sym, sig in signals.items():
            if sig.get("signal") == "BUY" and sym not in self.positions:
                if len(self.positions) < self.max_positions:
                    # position_size = min(10%, vol_adjusted_sizing) -> mock 10%
                    alloc = self.capital * 0.10
                    price = prices.get(sym)
                    if price:
                        qty = int(alloc // price)
                        if qty > 0:
                            self._open_position(sym, price, qty)

        # 3. 평가 잔고
        eval_cap = self.capital
        for sym, pos in self.positions.items():
            eval_cap += pos["qty"] * prices.get(sym, pos["avg_price"])
        logger.info(f"End of {current_date}: Capital={self.capital:,.0f}, Eval Cap={eval_cap:,.0f}")

    def _open_position(self, symbol: str, price: float, qty: int):
        cost = price * qty
        self.capital -= cost
        self.positions[symbol] = {
            "avg_price": price,
            "qty": qty
        }
        logger.info(f"Bought {qty} of {symbol} at {price:,.0f}")

    def _close_position(self, symbol: str, price: float):
        pos = self.positions.pop(symbol)
        revenue = pos["qty"] * price
        self.capital += revenue
        logger.info(f"Sold {pos['qty']} of {symbol} at {price:,.0f}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Initializing Backtest...")
    bt = MiniBacktester()
    
    # Mock data loop avoiding lookahead (available_at checked implicitly by passing day's exact data)
    days = ["2026-04-01", "2026-04-02", "2026-04-03"]
    for day in days:
        prices_mock = {"005930": 80000, "000660": 130000}
        signals_mock = {
            "005930": {"signal": "BUY" if day == "2026-04-01" else "HOLD"}
        }
        bt.step(day, signals_mock, prices_mock)
