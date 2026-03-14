import json
import os
from datetime import datetime
from config import Config
from upbit_api import UpbitAPI
from strategy import TradeAction
from logger_setup import setup_logger

logger = setup_logger("trader")


class Trader:
    def __init__(self, upbit_api: UpbitAPI):
        self.api = upbit_api
        self.position = {
            "has_position": False,
            "coin": Config.COIN,
            "volume": 0.0,
            "avg_price": 0.0,
            "current_price": 0.0,
            "entry_time": None,
        }
        self.trade_history = []
        self._load_position()

    def _load_position(self):
        """현재 포지션 확인 (API에서 잔고 조회)"""
        ticker = Config.COIN.split("-")[1] if "-" in Config.COIN else Config.COIN
        volume = self.api.get_balance(ticker)
        if volume > 0:
            current_price = self.api.get_current_price()
            self.position.update({
                "has_position": True,
                "volume": volume,
                "current_price": current_price,
            })
            logger.info(
                f"기존 포지션 감지: {ticker} {volume} "
                f"(현재가: {current_price:,.0f}원)"
            )

    def update_position(self):
        """포지션 현재가 업데이트"""
        if self.position["has_position"]:
            self.position["current_price"] = self.api.get_current_price()

    def get_position(self) -> dict:
        """현재 포지션 반환"""
        self.update_position()
        return self.position.copy()

    def execute(self, action: TradeAction) -> bool:
        """매매 실행"""
        if action.action == "hold":
            logger.info(f"관망: {action.reason}")
            return True

        if Config.DRY_RUN:
            return self._dry_run_execute(action)

        if action.action == "buy":
            return self._execute_buy(action)
        elif action.action == "sell":
            return self._execute_sell(action)

        return False

    def _execute_buy(self, action: TradeAction) -> bool:
        """실제 매수 실행"""
        krw_balance = self.api.get_balance("KRW")
        amount = min(action.amount, krw_balance * 0.9995)  # 수수료 고려

        if amount < 5000:  # 업비트 최소 주문 금액
            logger.warning(f"매수 금액 부족: {amount:,.0f}원 (최소 5,000원)")
            return False

        result = self.api.buy_market(amount=amount)
        if result:
            current_price = self.api.get_current_price()
            self.position.update({
                "has_position": True,
                "volume": amount / current_price if current_price else 0,
                "avg_price": current_price,
                "current_price": current_price,
                "entry_time": datetime.now().isoformat(),
            })
            self._record_trade("buy", amount, current_price, action.reason)
            logger.info(
                f"매수 완료: {Config.COIN} {amount:,.0f}원 "
                f"@ {current_price:,.0f}원"
            )
            return True

        logger.error("매수 주문 실패")
        return False

    def _execute_sell(self, action: TradeAction) -> bool:
        """실제 매도 실행"""
        if not self.position["has_position"]:
            logger.warning("매도할 포지션 없음")
            return False

        result = self.api.sell_market()
        if result:
            current_price = self.api.get_current_price()
            avg_price = self.position.get("avg_price", 0)
            pnl_pct = 0.0
            if avg_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price * 100

            self._record_trade(
                "sell", self.position["volume"], current_price,
                action.reason, pnl_pct,
            )
            logger.info(
                f"매도 완료: {Config.COIN} "
                f"@ {current_price:,.0f}원 (수익률: {pnl_pct:+.2f}%)"
            )

            self.position.update({
                "has_position": False,
                "volume": 0.0,
                "avg_price": 0.0,
                "current_price": 0.0,
                "entry_time": None,
            })
            return True

        logger.error("매도 주문 실패")
        return False

    def _dry_run_execute(self, action: TradeAction) -> bool:
        """DRY RUN 모드 - 실제 거래 없이 시뮬레이션"""
        current_price = self.api.get_current_price()
        prefix = "[DRY RUN]"

        if action.action == "buy":
            self.position.update({
                "has_position": True,
                "volume": action.amount / current_price if current_price else 0,
                "avg_price": current_price,
                "current_price": current_price,
                "entry_time": datetime.now().isoformat(),
            })
            logger.info(
                f"{prefix} 매수: {Config.COIN} {action.amount:,.0f}원 "
                f"@ {current_price:,.0f}원"
            )
            logger.info(f"{prefix} 사유: {action.reason}")
            if action.target_price:
                logger.info(f"{prefix} 목표가: {action.target_price:,.0f}원")
            if action.stop_loss:
                logger.info(f"{prefix} 손절가: {action.stop_loss:,.0f}원")

        elif action.action == "sell":
            avg_price = self.position.get("avg_price", 0)
            pnl_pct = 0.0
            if avg_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price * 100

            logger.info(
                f"{prefix} 매도: {Config.COIN} "
                f"@ {current_price:,.0f}원 (수익률: {pnl_pct:+.2f}%)"
            )
            logger.info(f"{prefix} 사유: {action.reason}")

            self.position.update({
                "has_position": False,
                "volume": 0.0,
                "avg_price": 0.0,
                "current_price": 0.0,
                "entry_time": None,
            })

        self._record_trade(
            action.action, action.amount, current_price,
            f"[DRY RUN] {action.reason}",
        )
        return True

    def _record_trade(self, side: str, amount: float, price: float,
                      reason: str, pnl_pct: float = 0.0):
        """거래 기록"""
        trade = {
            "timestamp": datetime.now().isoformat(),
            "side": side,
            "coin": Config.COIN,
            "amount": amount,
            "price": price,
            "reason": reason,
            "pnl_pct": pnl_pct,
            "dry_run": Config.DRY_RUN,
        }
        self.trade_history.append(trade)

        # JSON 파일에 저장
        os.makedirs("logs", exist_ok=True)
        history_file = "logs/trade_history.json"
        try:
            existing = []
            if os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing.append(trade)
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"거래 기록 저장 실패: {e}")

    def get_daily_summary(self) -> str:
        """일일 거래 요약"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [
            t for t in self.trade_history
            if t["timestamp"].startswith(today)
        ]

        if not today_trades:
            return "오늘 거래 없음"

        buys = [t for t in today_trades if t["side"] == "buy"]
        sells = [t for t in today_trades if t["side"] == "sell"]
        total_pnl = sum(t.get("pnl_pct", 0) for t in sells)

        return (
            f"오늘 거래 요약: 매수 {len(buys)}회, 매도 {len(sells)}회, "
            f"총 수익률: {total_pnl:+.2f}%"
        )
