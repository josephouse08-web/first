#!/usr/bin/env python3
"""백테스트 전용 실행 스크립트 - 다양한 조건으로 수익률 분석"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from crypto_bot.exchange.paper import PaperExchange
from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.strategies.rsi_strategy import RSIStrategy
from crypto_bot.strategies.macd_strategy import MACDStrategy
from crypto_bot.strategies.bollinger_strategy import BollingerStrategy
from crypto_bot.strategies.multi_strategy import MultiIndicatorStrategy

import asyncio


def run_test(name, strategies, candles, risk_config=None, bt_config=None):
    """단일 백테스트 실행"""
    default_risk = {
        "max_risk_per_trade": 0.02,
        "max_positions": 5,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "max_daily_loss": 0.10,
        "max_drawdown": 0.20,
        "max_daily_trades": 999,      # 백테스트에서는 제한 해제
        "min_signal_strength": 0.2,
    }
    if risk_config:
        default_risk.update(risk_config)

    default_bt = {"initial_balance": 10000, "commission_rate": 0.001, "lookback": 50}
    if bt_config:
        default_bt.update(bt_config)

    engine = BacktestEngine(default_bt)
    result = engine.run(candles, strategies, default_risk)

    print(f"\n{'='*60}")
    print(f"  전략: {name}")
    print(result.summary())
    return result


async def fetch_candles(symbol="BTC/USDT", interval="1h", limit=500):
    exchange = PaperExchange({"initial_balance": 10000, "use_live_data": True})
    candles = await exchange.fetch_candles(symbol, interval, limit)
    await exchange.close()
    return candles


def main():
    print(r"""
   ____                  _        ____        _
  / ___|_ __ _   _ _ __ | |_ ___ | __ )  ___ | |_
 | |   | '__| | | | '_ \| __/ _ \|  _ \ / _ \| __|
 | |___| |  | |_| | |_) | || (_) | |_) | (_) | |_
  \____|_|   \__, | .__/ \__\___/|____/ \___/ \__|
             |___/|_|    📊 Backtest Suite v1.0
    """)

    # 데이터 수집
    print("📊 시세 데이터 수집 중... (Binance Public API)")
    candles = asyncio.run(fetch_candles("BTC/USDT", "1h", 500))
    print(f"✅ {len(candles)}개 캔들 수집 완료")
    if candles:
        print(f"   기간: {candles[0].timestamp} → {candles[-1].timestamp}")
        print(f"   시작가: ${candles[0].open:,.2f} → 종가: ${candles[-1].close:,.2f}")

    # ── 1) 개별 전략 백테스트 ──
    print("\n" + "▶"*30 + " 개별 전략 백테스트 " + "◀"*30)

    results = {}

    results["RSI"] = run_test(
        "RSI 단독",
        [RSIStrategy({"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70})],
        candles,
    )

    results["MACD"] = run_test(
        "MACD 단독",
        [MACDStrategy()],
        candles,
    )

    results["Bollinger"] = run_test(
        "볼린저밴드 단독",
        [BollingerStrategy()],
        candles,
    )

    results["Multi"] = run_test(
        "복합지표 (6개 통합)",
        [MultiIndicatorStrategy({"min_confirmations": 3})],
        candles,
    )

    # ── 2) 전략 조합 백테스트 ──
    print("\n" + "▶"*30 + " 전략 조합 백테스트 " + "◀"*30)

    results["RSI+MACD"] = run_test(
        "RSI + MACD 조합",
        [RSIStrategy(), MACDStrategy()],
        candles,
    )

    results["ALL"] = run_test(
        "전체 전략 통합 (RSI+MACD+BB+Multi)",
        [RSIStrategy(), MACDStrategy(), BollingerStrategy(),
         MultiIndicatorStrategy({"min_confirmations": 3})],
        candles,
    )

    # ── 3) 리스크 파라미터 튜닝 ──
    print("\n" + "▶"*30 + " 리스크 파라미터 비교 " + "◀"*30)

    results["공격적"] = run_test(
        "공격적 (리스크 5%, SL 2%, TP 10%)",
        [RSIStrategy(), MACDStrategy(), BollingerStrategy()],
        candles,
        risk_config={"max_risk_per_trade": 0.05, "stop_loss_pct": 0.02, "take_profit_pct": 0.10},
    )

    results["보수적"] = run_test(
        "보수적 (리스크 1%, SL 5%, TP 4%)",
        [RSIStrategy(), MACDStrategy(), BollingerStrategy()],
        candles,
        risk_config={"max_risk_per_trade": 0.01, "stop_loss_pct": 0.05, "take_profit_pct": 0.04},
    )

    # ── 최종 비교 ──
    print("\n" + "="*70)
    print("  📊 전략별 성과 비교 요약")
    print("="*70)
    print(f"  {'전략':<25} {'수익률':>10} {'승률':>8} {'거래수':>6} {'MDD':>8} {'샤프':>8}")
    print("-"*70)
    for name, r in results.items():
        print(f"  {name:<25} {r.total_return:>+9.2f}% {r.win_rate:>7.1f}% "
              f"{r.total_trades:>6} {r.max_drawdown:>7.2f}% {r.sharpe_ratio:>8.2f}")
    print("="*70)

    # 최고 수익률 전략
    best = max(results.items(), key=lambda x: x[1].total_return)
    print(f"\n  🏆 최고 수익 전략: {best[0]} ({best[1].total_return:+.2f}%)")

    best_sharpe = max(results.items(), key=lambda x: x[1].sharpe_ratio)
    print(f"  📈 최고 샤프비율:  {best_sharpe[0]} ({best_sharpe[1].sharpe_ratio:.2f})")

    lowest_mdd = min(results.items(), key=lambda x: x[1].max_drawdown if x[1].total_trades > 0 else 999)
    print(f"  🛡️  최저 MDD:      {lowest_mdd[0]} ({lowest_mdd[1].max_drawdown:.2f}%)")
    print()


if __name__ == "__main__":
    main()
