import signal
import sys
import time
from datetime import datetime

from config import Config
from upbit_api import UpbitAPI
from chart_generator import generate_multi_timeframe_chart, generate_single_chart
from ai_analyzer import AIAnalyzer
from strategy import ScalpingStrategy
from trader import Trader
from logger_setup import setup_logger

logger = setup_logger("main")

# Graceful shutdown
shutdown = False


def signal_handler(sig, frame):
    global shutdown
    logger.info("종료 신호 수신... 안전하게 종료합니다.")
    shutdown = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def print_banner():
    banner = """
╔══════════════════════════════════════════════╗
║     코인 스캘핑 자동 매매 AI 봇              ║
║     Crypto Scalping AI Trading Bot           ║
╠══════════════════════════════════════════════╣
║  거래소: 업비트 (Upbit)                      ║
║  AI: Claude Vision API                       ║
║  전략: 멀티 타임프레임 스캘핑                ║
╚══════════════════════════════════════════════╝
"""
    print(banner)


def print_config():
    mode = "시뮬레이션 (DRY RUN)" if Config.DRY_RUN else "실거래 모드"
    logger.info(f"모드: {mode}")
    logger.info(f"코인: {Config.COIN}")
    logger.info(f"1회 거래 금액: {Config.TRADE_AMOUNT:,.0f}원")
    logger.info(f"일일 목표: +{Config.DAILY_TARGET}%")
    logger.info(f"일일 손실 한도: {Config.MAX_LOSS}%")
    logger.info(f"분석 주기: {Config.ANALYSIS_INTERVAL}초")
    logger.info(f"타임프레임: {', '.join(Config.TIMEFRAMES)}")
    logger.info(f"최소 신뢰도: {Config.MIN_CONFIDENCE:.0%}")


def run():
    global shutdown

    print_banner()
    print_config()

    # Initialize components
    logger.info("시스템 초기화 중...")
    upbit = UpbitAPI()
    analyzer = AIAnalyzer()
    strategy = ScalpingStrategy()
    trader = Trader(upbit)

    # Initialize daily stats
    krw_balance = upbit.get_balance("KRW")
    position = trader.get_position()
    if position["has_position"]:
        total_value = krw_balance + (position["volume"] * position["current_price"])
    else:
        total_value = krw_balance
    strategy.reset_daily(total_value)

    current_price = upbit.get_current_price()
    logger.info(f"현재가: {current_price:,.0f}원")
    logger.info(f"KRW 잔고: {krw_balance:,.0f}원")
    logger.info(f"총 자산 가치: {total_value:,.0f}원")
    logger.info("=" * 50)
    logger.info("스캘핑 봇 시작!")
    logger.info("=" * 50)

    cycle = 0

    while not shutdown:
        cycle += 1
        logger.info(f"\n--- 분석 사이클 #{cycle} ({datetime.now().strftime('%H:%M:%S')}) ---")

        try:
            # Step 1: Fetch multi-timeframe OHLCV data
            logger.info("멀티 타임프레임 데이터 조회 중...")
            ohlcv_data = upbit.get_multi_timeframe_ohlcv()

            valid_data = {k: v for k, v in ohlcv_data.items() if not v.empty}
            if not valid_data:
                logger.warning("유효한 OHLCV 데이터 없음 - 재시도 대기")
                _wait(Config.ANALYSIS_INTERVAL)
                continue

            # Step 2: Generate chart image
            logger.info("차트 이미지 생성 중...")
            chart_image = generate_multi_timeframe_chart(valid_data)
            if not chart_image:
                logger.warning("차트 생성 실패 - 재시도 대기")
                _wait(Config.ANALYSIS_INTERVAL)
                continue

            # Step 3: AI analysis
            logger.info("Claude Vision AI 분석 중...")
            current_price = upbit.get_current_price()
            context = (
                f"현재가: {current_price:,.0f}원, "
                f"코인: {Config.COIN}, "
                f"분석 타임프레임: {', '.join(valid_data.keys())}"
            )
            analysis = analyzer.analyze_chart(chart_image, context)

            logger.info(
                f"AI 판단: {analysis['decision']} "
                f"(신뢰도: {analysis.get('confidence', 0):.1%})"
            )
            if analysis.get("reason"):
                logger.info(f"분석: {analysis['reason'][:100]}")

            # Step 4: Strategy evaluation
            position = trader.get_position()
            position["current_price"] = current_price
            action = strategy.evaluate(analysis, strategy.daily_stats, position)

            logger.info(f"전략 판단: {action.action} - {action.reason}")

            # Step 5: Execute trade
            if action.action != "hold":
                success = trader.execute(action)
                if success and action.action == "sell":
                    avg_price = position.get("avg_price", 0)
                    is_profit = current_price > avg_price if avg_price else False
                    strategy.record_trade(is_profit)

                # Update daily stats
                krw_balance = upbit.get_balance("KRW")
                new_position = trader.get_position()
                if new_position["has_position"]:
                    total_value = krw_balance + (
                        new_position["volume"] * new_position["current_price"]
                    )
                else:
                    total_value = krw_balance
                strategy.update_balance(total_value)

            # Daily stats
            stats = strategy.daily_stats
            logger.info(
                f"일일 수익률: {stats.profit_pct:+.2f}% "
                f"(목표: +{Config.DAILY_TARGET}%)"
            )
            logger.info(f"오늘 거래: {stats.total_trades}회")

            # Check if should stop
            if stats.target_reached:
                logger.info("일일 목표 수익률 달성! 거래를 중단합니다.")
                logger.info(trader.get_daily_summary())
                break

            if stats.max_loss_reached:
                logger.warning("일일 최대 손실 도달! 거래를 중단합니다.")
                logger.info(trader.get_daily_summary())
                break

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"분석 사이클 에러: {e}", exc_info=True)

        # Wait for next cycle
        _wait(Config.ANALYSIS_INTERVAL)

    # Shutdown
    logger.info("\n" + "=" * 50)
    logger.info("봇 종료")
    logger.info(trader.get_daily_summary())
    logger.info("=" * 50)


def _wait(seconds: int):
    """인터럽트 가능한 대기"""
    global shutdown
    for _ in range(seconds):
        if shutdown:
            break
        time.sleep(1)


if __name__ == "__main__":
    run()
