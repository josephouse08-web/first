#!/usr/bin/env python3
"""
CryptoBot - 암호화폐 자동매매 시스템 메인 실행 스크립트

사용법:
    python run_bot.py                    # 기본 실행 (config.yaml 사용)
    python run_bot.py --config my.yaml   # 커스텀 설정 파일
    python run_bot.py --backtest         # 백테스트 모드
    python run_bot.py --paper            # 페이퍼 트레이딩 모드 (기본값)
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from crypto_bot.core.engine import TradingEngine
from crypto_bot.core.risk_manager import RiskManager
from crypto_bot.exchange.paper import PaperExchange
from crypto_bot.exchange.binance import BinanceExchange
from crypto_bot.exchange.upbit import UpbitExchange
from crypto_bot.strategies.rsi_strategy import RSIStrategy
from crypto_bot.strategies.macd_strategy import MACDStrategy
from crypto_bot.strategies.bollinger_strategy import BollingerStrategy
from crypto_bot.strategies.multi_strategy import MultiIndicatorStrategy
from crypto_bot.strategies.orderblock_strategy import OrderBlockStrategy
from crypto_bot.strategies.fvg_strategy import FVGStrategy
from crypto_bot.strategies.trendline_channel_strategy import TrendlineChannelStrategy
from crypto_bot.strategies.fakeout_strategy import FakeoutStrategy
from crypto_bot.strategies.ict_combined_strategy import ICTCombinedStrategy
from crypto_bot.notifications.notifier import ConsoleNotifier, TelegramNotifier, DiscordNotifier
from crypto_bot.dashboard.app import Dashboard
from crypto_bot.backtest.engine import BacktestEngine


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"설정 파일을 찾을 수 없습니다: {path}")
        print("기본 설정으로 실행합니다...")
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def create_exchange(config: dict):
    exchange_config = config.get("exchange", {})
    exchange_type = exchange_config.get("type", "paper")

    if exchange_type == "binance":
        return BinanceExchange(exchange_config)
    elif exchange_type == "upbit":
        return UpbitExchange(exchange_config)
    else:
        trading = config.get("trading", {})
        exchange_config["initial_balance"] = trading.get("initial_balance", 10000)
        exchange_config["use_live_data"] = exchange_config.get("use_live_data", True)
        return PaperExchange(exchange_config)


def create_strategies(config: dict) -> list:
    strategies_config = config.get("strategies", {})
    strategies = []

    if strategies_config.get("rsi", {}).get("enabled", True):
        strategies.append(RSIStrategy(strategies_config.get("rsi", {})))

    if strategies_config.get("macd", {}).get("enabled", True):
        strategies.append(MACDStrategy(strategies_config.get("macd", {})))

    if strategies_config.get("bollinger", {}).get("enabled", True):
        strategies.append(BollingerStrategy(strategies_config.get("bollinger", {})))

    if strategies_config.get("multi_indicator", {}).get("enabled", True):
        strategies.append(MultiIndicatorStrategy(strategies_config.get("multi_indicator", {})))

    # ICT 전략
    if strategies_config.get("orderblock", {}).get("enabled", False):
        strategies.append(OrderBlockStrategy(strategies_config.get("orderblock", {})))

    if strategies_config.get("fvg", {}).get("enabled", False):
        strategies.append(FVGStrategy(strategies_config.get("fvg", {})))

    if strategies_config.get("trendline_channel", {}).get("enabled", False):
        strategies.append(TrendlineChannelStrategy(strategies_config.get("trendline_channel", {})))

    if strategies_config.get("fakeout", {}).get("enabled", False):
        strategies.append(FakeoutStrategy(strategies_config.get("fakeout", {})))

    if strategies_config.get("ict_combined", {}).get("enabled", False):
        strategies.append(ICTCombinedStrategy(strategies_config.get("ict_combined", {})))

    return strategies


def create_notifiers(config: dict) -> list:
    notif_config = config.get("notifications", {})
    notifiers = []

    if notif_config.get("console", True):
        notifiers.append(ConsoleNotifier())

    telegram = notif_config.get("telegram", {})
    if telegram.get("enabled", False):
        notifiers.append(TelegramNotifier(telegram))

    discord = notif_config.get("discord", {})
    if discord.get("enabled", False):
        notifiers.append(DiscordNotifier(discord))

    return notifiers


async def run_live(config: dict):
    """실시간 트레이딩 실행"""
    trading_config = config.get("trading", {})
    risk_config = config.get("risk", {})

    exchange = create_exchange(config)
    risk_manager = RiskManager(risk_config)
    engine = TradingEngine(exchange, risk_manager, trading_config)

    # 전략 추가
    for strategy in create_strategies(config):
        engine.add_strategy(strategy)

    # 알림 추가
    for notifier in create_notifiers(config):
        engine.add_notifier(notifier)

    # 대시보드 시작
    dashboard_config = config.get("dashboard", {})
    dashboard = None
    if dashboard_config.get("enabled", True):
        dashboard = Dashboard(engine, dashboard_config)
        dashboard.start()

    # 종료 핸들러
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        print("\n종료 신호 수신, 안전하게 중지합니다...")
        asyncio.ensure_future(engine.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    # 실행
    try:
        await engine.start()
    finally:
        await exchange.close()
        if dashboard:
            dashboard.stop()


def run_backtest(config: dict):
    """백테스트 실행"""
    import asyncio

    backtest_config = config.get("backtest", {})
    trading_config = config.get("trading", {})
    risk_config = config.get("risk", {})

    # 데이터 수집을 위한 임시 거래소 연결
    exchange = PaperExchange({"initial_balance": 10000, "use_live_data": True})

    async def fetch_data():
        symbols = trading_config.get("symbols", ["BTC/USDT"])
        interval = trading_config.get("interval", "1h")
        candles = await exchange.fetch_candles(symbols[0], interval, limit=200)
        await exchange.close()
        return candles

    print("📊 과거 데이터 수집 중...")
    candles = asyncio.run(fetch_data())

    if not candles:
        print("데이터를 가져올 수 없습니다.")
        return

    print(f"✅ {len(candles)}개 캔들 데이터 수집 완료")

    # 전략 생성
    strategies = create_strategies(config)

    # 백테스트 실행
    backtest_config["initial_balance"] = trading_config.get("initial_balance", 10000)
    bt_engine = BacktestEngine(backtest_config)
    result = bt_engine.run(candles, strategies, risk_config)

    # 결과 출력
    print(result.summary())


def main():
    parser = argparse.ArgumentParser(description="CryptoBot - 암호화폐 자동매매 시스템")
    parser.add_argument("--config", "-c", default="config.yaml", help="설정 파일 경로")
    parser.add_argument("--backtest", "-b", action="store_true", help="백테스트 모드")
    parser.add_argument("--paper", "-p", action="store_true", help="페이퍼 트레이딩 모드")
    parser.add_argument("--log-level", "-l", default="INFO", help="로깅 레벨")
    args = parser.parse_args()

    setup_logging(args.log_level)
    config = load_config(args.config)

    # 페이퍼 모드 강제
    if args.paper:
        config.setdefault("exchange", {})["type"] = "paper"

    print(r"""
   ____                  _        ____        _
  / ___|_ __ _   _ _ __ | |_ ___ | __ )  ___ | |_
 | |   | '__| | | | '_ \| __/ _ \|  _ \ / _ \| __|
 | |___| |  | |_| | |_) | || (_) | |_) | (_) | |_
  \____|_|   \__, | .__/ \__\___/|____/ \___/ \__|
             |___/|_|          v1.0.0
    """)

    if args.backtest:
        print("📊 백테스트 모드로 시작합니다...")
        run_backtest(config)
    else:
        exchange_type = config.get("exchange", {}).get("type", "paper")
        mode = "페이퍼 트레이딩" if exchange_type == "paper" else f"실거래 ({exchange_type})"
        print(f"🚀 {mode} 모드로 시작합니다...")
        print(f"💰 초기 자본: ${config.get('trading', {}).get('initial_balance', 10000):,.2f}")
        print(f"📈 심볼: {', '.join(config.get('trading', {}).get('symbols', ['BTC/USDT']))}")
        print()

        try:
            asyncio.run(run_live(config))
        except KeyboardInterrupt:
            print("\n프로그램 종료")


if __name__ == "__main__":
    main()
