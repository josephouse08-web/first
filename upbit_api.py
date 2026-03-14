import pyupbit
import pandas as pd
from config import Config
from logger_setup import setup_logger

logger = setup_logger("upbit_api")


class UpbitAPI:
    def __init__(self):
        if Config.UPBIT_ACCESS_KEY and Config.UPBIT_SECRET_KEY:
            self.upbit = pyupbit.Upbit(
                Config.UPBIT_ACCESS_KEY, Config.UPBIT_SECRET_KEY
            )
            logger.info("업비트 API 인증 완료")
        else:
            self.upbit = None
            logger.warning("업비트 API 키 미설정 - 공개 API만 사용 가능")

    def get_ohlcv(self, coin: str = None, interval: str = "minute5", count: int = None) -> pd.DataFrame:
        """OHLCV 데이터 조회"""
        coin = coin or Config.COIN
        count = count or Config.CANDLE_COUNT
        try:
            df = pyupbit.get_ohlcv(coin, interval=interval, count=count)
            if df is not None and not df.empty:
                logger.debug(f"OHLCV 조회 성공: {coin} {interval} ({len(df)}개)")
                return df
            logger.warning(f"OHLCV 데이터 없음: {coin} {interval}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"OHLCV 조회 실패: {e}")
            return pd.DataFrame()

    def get_multi_timeframe_ohlcv(self, coin: str = None) -> dict:
        """멀티 타임프레임 OHLCV 데이터 조회"""
        coin = coin or Config.COIN
        result = {}
        for tf in Config.TIMEFRAMES:
            result[tf] = self.get_ohlcv(coin, interval=tf)
        return result

    def get_current_price(self, coin: str = None) -> float:
        """현재가 조회"""
        coin = coin or Config.COIN
        try:
            price = pyupbit.get_current_price(coin)
            return price or 0.0
        except Exception as e:
            logger.error(f"현재가 조회 실패: {e}")
            return 0.0

    def get_balance(self, ticker: str = "KRW") -> float:
        """잔고 조회"""
        if not self.upbit:
            logger.warning("API 키 미설정 - 잔고 조회 불가")
            return 0.0
        try:
            balance = self.upbit.get_balance(ticker)
            return balance or 0.0
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return 0.0

    def get_orderbook(self, coin: str = None) -> dict:
        """호가 조회"""
        coin = coin or Config.COIN
        try:
            orderbook = pyupbit.get_orderbook(coin)
            if orderbook:
                return orderbook[0] if isinstance(orderbook, list) else orderbook
            return {}
        except Exception as e:
            logger.error(f"호가 조회 실패: {e}")
            return {}

    def buy_market(self, coin: str = None, amount: float = None) -> dict:
        """시장가 매수"""
        coin = coin or Config.COIN
        amount = amount or Config.TRADE_AMOUNT
        if not self.upbit:
            logger.error("API 키 미설정 - 매수 불가")
            return {}
        try:
            result = self.upbit.buy_market_order(coin, amount)
            logger.info(f"매수 주문: {coin} {amount:,.0f}원 → {result}")
            return result if result else {}
        except Exception as e:
            logger.error(f"매수 실패: {e}")
            return {}

    def sell_market(self, coin: str = None, volume: float = None) -> dict:
        """시장가 매도"""
        coin = coin or Config.COIN
        if not self.upbit:
            logger.error("API 키 미설정 - 매도 불가")
            return {}
        if volume is None:
            # 전량 매도
            ticker = coin.split("-")[1] if "-" in coin else coin
            volume = self.get_balance(ticker)
        if volume <= 0:
            logger.warning("매도할 수량 없음")
            return {}
        try:
            result = self.upbit.sell_market_order(coin, volume)
            logger.info(f"매도 주문: {coin} {volume} → {result}")
            return result if result else {}
        except Exception as e:
            logger.error(f"매도 실패: {e}")
            return {}
