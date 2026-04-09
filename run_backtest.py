#!/usr/bin/env python3
"""
대규모 백테스트 스위트
- 5,000개 캔들 × 다양한 시장 조건 (상승장/하락장/횡보장/급등락)
- 몬테카를로 시뮬레이션 100회
- 전략별/조합별/파라미터별 종합 비교
"""

import sys
import random
import math
from pathlib import Path
from datetime import datetime, timedelta
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).parent))

from crypto_bot.core.models import Candle
from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.strategies.rsi_strategy import RSIStrategy
from crypto_bot.strategies.macd_strategy import MACDStrategy
from crypto_bot.strategies.bollinger_strategy import BollingerStrategy
from crypto_bot.strategies.multi_strategy import MultiIndicatorStrategy
from crypto_bot.strategies.orderblock_strategy import OrderBlockStrategy
from crypto_bot.strategies.fvg_strategy import FVGStrategy
from crypto_bot.strategies.trendline_channel_strategy import TrendlineChannelStrategy
from crypto_bot.strategies.fakeout_strategy import FakeoutStrategy
from crypto_bot.strategies.ict_combined_strategy import ICTCombinedStrategy


# ───────────────────────────────────────────────────────────
# 시장 시뮬레이터: 다양한 시장 환경 생성
# ───────────────────────────────────────────────────────────

def generate_market(regime: str, num_candles: int, base_price: float = 65000,
                    seed: int = None) -> list[Candle]:
    """
    시장 환경별 캔들 데이터 생성

    regime:
        "bull"       - 강한 상승장 (연 +80% 추세)
        "bear"       - 강한 하락장 (연 -60% 추세)
        "sideways"   - 횡보장 (추세 없음, 레인지)
        "volatile"   - 급등락장 (높은 변동성)
        "crash"      - 폭락 후 반등
        "pump_dump"  - 급등 후 급락
        "trending"   - 완만한 상승 후 조정 반복
        "random"     - 순수 랜덤 워크
    """
    if seed is not None:
        random.seed(seed)

    candles = []
    price = base_price

    # 시장 환경별 파라미터
    params = {
        "bull":      {"drift": 0.0003, "vol": 0.015, "desc": "강세장 📈"},
        "bear":      {"drift": -0.00025, "vol": 0.018, "desc": "약세장 📉"},
        "sideways":  {"drift": 0.0, "vol": 0.008, "desc": "횡보장 ➡️"},
        "volatile":  {"drift": 0.00005, "vol": 0.035, "desc": "고변동성 🌊"},
        "crash":     {"drift": -0.001, "vol": 0.04, "desc": "폭락장 💥"},
        "pump_dump": {"drift": 0.0008, "vol": 0.03, "desc": "펌프앤덤프 🎢"},
        "trending":  {"drift": 0.00015, "vol": 0.012, "desc": "추세장 📊"},
        "random":    {"drift": 0.0, "vol": 0.02, "desc": "랜덤 🎲"},
    }

    p = params.get(regime, params["random"])
    drift = p["drift"]
    vol = p["vol"]

    for i in range(num_candles):
        # 특수 이벤트 시뮬레이션
        event_drift = drift
        event_vol = vol

        if regime == "crash" and num_candles * 0.3 < i < num_candles * 0.4:
            event_drift = -0.005   # 폭락 구간
            event_vol = 0.06
        elif regime == "crash" and i > num_candles * 0.5:
            event_drift = 0.0004   # 회복 구간
            event_vol = 0.02

        if regime == "pump_dump":
            if i < num_candles * 0.35:
                event_drift = 0.001   # 펌핑
            elif i < num_candles * 0.5:
                event_drift = -0.002  # 덤핑
                event_vol = 0.04
            else:
                event_drift = 0.0     # 안정화

        if regime == "trending":
            # 사이클: 상승 → 조정 → 상승 → 조정
            cycle_pos = (i % 200) / 200
            if cycle_pos < 0.6:
                event_drift = 0.0003
            else:
                event_drift = -0.0002
                event_vol = vol * 1.5

        # GBM (Geometric Brownian Motion)
        ret = event_drift + event_vol * random.gauss(0, 1)

        # 간헐적 블랙스완 이벤트 (0.5% 확률)
        if random.random() < 0.005:
            ret += random.choice([-1, 1]) * random.uniform(0.03, 0.08)

        open_price = price
        close_price = price * math.exp(ret)

        # 캔들 내 움직임
        intra_vol = abs(ret) + event_vol * 0.5
        high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, intra_vol * 0.5)))
        low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, intra_vol * 0.5)))

        # 거래량 (변동성 클수록 거래량 증가)
        base_vol = random.uniform(500, 5000)
        volume = base_vol * (1 + abs(ret) * 50)

        candles.append(Candle(
            timestamp=datetime(2025, 1, 1) + timedelta(hours=i),
            open=open_price,
            high=high_price,
            low=max(low_price, 0.01),
            close=max(close_price, 0.01),
            volume=volume,
        ))
        price = max(close_price, 0.01)

    return candles


def run_single_backtest(strategies, candles, risk_config=None, bt_config=None):
    """단일 백테스트 실행 (출력 없이)"""
    default_risk = {
        "max_risk_per_trade": 0.02,
        "max_positions": 5,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "max_daily_loss": 0.50,      # 백테스트용 완화
        "max_drawdown": 0.50,        # 백테스트용 완화
        "max_daily_trades": 9999,
        "min_signal_strength": 0.2,
    }
    if risk_config:
        default_risk.update(risk_config)

    default_bt = {"initial_balance": 10000, "commission_rate": 0.001, "lookback": 50}
    if bt_config:
        default_bt.update(bt_config)

    engine = BacktestEngine(default_bt)
    return engine.run(candles, strategies, default_risk, allow_short=True)


def print_result_row(name, r):
    trades = r.total_trades
    if trades == 0:
        print(f"  {name:<28} {'거래 없음':>10}")
        return
    print(f"  {name:<28} {r.total_return:>+8.2f}%  {r.win_rate:>6.1f}%  "
          f"{trades:>5}  {r.max_drawdown:>7.2f}%  {r.sharpe_ratio:>7.2f}  "
          f"{r.profit_factor:>7.2f}  {r.avg_win:>9.2f}  {r.avg_loss:>9.2f}")


# ───────────────────────────────────────────────────────────
# 전략 팩토리
# ───────────────────────────────────────────────────────────

STRATEGY_SETS = {
    # === ICT 전략 (쉽알남 매매법) ===
    "오더블럭(OB)": lambda: [OrderBlockStrategy()],
    "FVG": lambda: [FVGStrategy()],
    "추세선/채널": lambda: [TrendlineChannelStrategy()],
    "거짓돌파(Fakeout)": lambda: [FakeoutStrategy()],
    "ICT 종합": lambda: [ICTCombinedStrategy({"min_confluence": 2})],
    "OB+FVG": lambda: [OrderBlockStrategy(), FVGStrategy()],
    "OB+추세선": lambda: [OrderBlockStrategy(), TrendlineChannelStrategy()],
    "FVG+거짓돌파": lambda: [FVGStrategy(), FakeoutStrategy()],
    "ICT 올인 (4전략)": lambda: [OrderBlockStrategy(), FVGStrategy(),
                                TrendlineChannelStrategy(), FakeoutStrategy()],
    # === 기존 전략 (비교용) ===
    "RSI (기존)": lambda: [RSIStrategy({"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70})],
    "MACD (기존)": lambda: [MACDStrategy()],
    "Bollinger (기존)": lambda: [BollingerStrategy()],
}


def main():
    print(r"""
   ____                  _        ____        _
  / ___|_ __ _   _ _ __ | |_ ___ | __ )  ___ | |_
 | |   | '__| | | | '_ \| __/ _ \|  _ \ / _ \| __|
 | |___| |  | |_| | |_) | || (_) | |_) | (_) | |_
  \____|_|   \__, | .__/ \__\___/|____/ \___/ \__|
             |___/|_|    🔥 ICT 백테스트 v3.0 (쉽알남 매매법)
    """)

    NUM_CANDLES = 5000       # 캔들 수 (약 7개월 1시간봉)
    MONTE_CARLO_RUNS = 100   # 몬테카를로 시뮬레이션 횟수
    REGIMES = ["bull", "bear", "sideways", "volatile", "crash", "pump_dump", "trending", "random"]

    # ══════════════════════════════════════════════════════════
    # PART 1: 시장 환경별 × 전략별 대규모 백테스트
    # ══════════════════════════════════════════════════════════
    print("=" * 120)
    print(f"  PART 1: 시장 환경별 전략 성과 (5,000 캔들 × 8 시장 × {len(STRATEGY_SETS)} 전략 = {8*len(STRATEGY_SETS)} 백테스트)")
    print("=" * 120)

    regime_labels = {
        "bull": "📈 강세장", "bear": "📉 약세장", "sideways": "➡️ 횡보장",
        "volatile": "🌊 고변동", "crash": "💥 폭락장", "pump_dump": "🎢 펌덤",
        "trending": "📊 추세장", "random": "🎲 랜덤",
    }

    all_results = {}  # {strategy_name: {regime: result}}

    for regime in REGIMES:
        label = regime_labels[regime]
        candles = generate_market(regime, NUM_CANDLES, seed=42)

        start_p = candles[0].open
        end_p = candles[-1].close
        market_return = ((end_p - start_p) / start_p) * 100

        print(f"\n{'─'*120}")
        print(f"  {label} | {NUM_CANDLES}캔들 | 시장수익률: {market_return:+.2f}% "
              f"| ${start_p:,.0f} → ${end_p:,.0f}")
        print(f"{'─'*120}")
        print(f"  {'전략':<28} {'수익률':>8}  {'승률':>6}  {'거래':>5}  "
              f"{'MDD':>7}  {'샤프':>7}  {'PF':>7}  {'평균수익':>9}  {'평균손실':>9}")
        print(f"  {'-'*108}")

        for strat_name, strat_factory in STRATEGY_SETS.items():
            result = run_single_backtest(strat_factory(), candles)
            all_results.setdefault(strat_name, {})[regime] = result
            print_result_row(strat_name, result)

    # ── 시장 환경별 종합 점수 ──
    print(f"\n\n{'='*120}")
    print("  📊 전략별 시장 환경 종합 성적표")
    print("=" * 120)
    print(f"  {'전략':<28}", end="")
    for regime in REGIMES:
        print(f" {regime_labels[regime]:>8}", end="")
    print(f"  {'평균':>8}  {'최악':>8}")
    print(f"  {'-'*116}")

    strategy_avg = {}
    for strat_name in STRATEGY_SETS:
        print(f"  {strat_name:<28}", end="")
        returns = []
        for regime in REGIMES:
            r = all_results[strat_name][regime]
            ret = r.total_return
            returns.append(ret)
            color = "" if ret == 0 else ""
            print(f"  {ret:>+7.1f}%", end="")
        avg_ret = mean(returns)
        worst = min(returns)
        strategy_avg[strat_name] = avg_ret
        print(f"  {avg_ret:>+7.1f}%  {worst:>+7.1f}%")
    print(f"  {'-'*116}")

    # ══════════════════════════════════════════════════════════
    # PART 2: 몬테카를로 시뮬레이션 (100회)
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'='*120}")
    print(f"  PART 2: 몬테카를로 시뮬레이션 ({MONTE_CARLO_RUNS}회 × 2,000캔들 × 랜덤 시장)")
    print("=" * 120)

    mc_results = {}  # {strategy: [returns]}

    for strat_name, strat_factory in STRATEGY_SETS.items():
        returns = []
        wins = []
        mdds = []
        trade_counts = []
        sharpes = []

        for i in range(MONTE_CARLO_RUNS):
            # 매번 다른 랜덤 시장
            regime = random.choice(REGIMES)
            candles = generate_market(regime, 2000, seed=i * 137 + 7)
            result = run_single_backtest(strat_factory(), candles)

            returns.append(result.total_return)
            if result.total_trades > 0:
                wins.append(result.win_rate)
                mdds.append(result.max_drawdown)
                sharpes.append(result.sharpe_ratio)
            trade_counts.append(result.total_trades)

        mc_results[strat_name] = returns

        profitable = sum(1 for r in returns if r > 0)
        breakeven = sum(1 for r in returns if r == 0)

        print(f"\n  📌 {strat_name}")
        print(f"     수익률  → 평균: {mean(returns):>+7.2f}%  |  중앙값: {sorted(returns)[len(returns)//2]:>+7.2f}%  "
              f"|  최고: {max(returns):>+7.2f}%  |  최악: {min(returns):>+7.2f}%  "
              f"|  표준편차: {stdev(returns):>6.2f}%")
        print(f"     수익 확률: {profitable}/{MONTE_CARLO_RUNS} ({profitable/MONTE_CARLO_RUNS*100:.0f}%)  "
              f"|  손실: {MONTE_CARLO_RUNS - profitable - breakeven}/{MONTE_CARLO_RUNS}  "
              f"|  무거래: {breakeven}/{MONTE_CARLO_RUNS}")
        if wins:
            print(f"     승률   → 평균: {mean(wins):>5.1f}%  |  MDD 평균: {mean(mdds):>5.2f}%  "
                  f"|  MDD 최악: {max(mdds):>5.2f}%  |  샤프 평균: {mean(sharpes):>+5.2f}")
        print(f"     거래수  → 평균: {mean(trade_counts):>5.1f}  |  총 시뮬: {MONTE_CARLO_RUNS}회")

    # ══════════════════════════════════════════════════════════
    # PART 3: 파라미터 민감도 분석
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'='*120}")
    print("  PART 3: 리스크 파라미터 민감도 분석 (3,000캔들 × 다양한 파라미터)")
    print("=" * 120)

    # 추세장 + 고변동 혼합 시장
    candles_mixed = generate_market("trending", 1500, seed=99) + \
                    generate_market("volatile", 1500, base_price=generate_market("trending", 1500, seed=99)[-1].close, seed=88)

    strategies_3 = lambda: [RSIStrategy(), MACDStrategy(), BollingerStrategy()]

    # SL/TP 조합
    print(f"\n  ── 스탑로스 / 테이크프로핏 조합 ──")
    print(f"  {'SL':>5} {'TP':>5} {'수익률':>9} {'승률':>7} {'거래':>5} {'MDD':>8} {'PF':>8}")
    print(f"  {'-'*52}")

    for sl in [0.01, 0.02, 0.03, 0.05, 0.07, 0.10]:
        for tp in [0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]:
            if tp <= sl:
                continue
            r = run_single_backtest(
                strategies_3(), candles_mixed,
                risk_config={"stop_loss_pct": sl, "take_profit_pct": tp}
            )
            if r.total_trades > 0:
                print(f"  {sl*100:>4.0f}% {tp*100:>4.0f}% {r.total_return:>+8.2f}% "
                      f"{r.win_rate:>6.1f}% {r.total_trades:>5} {r.max_drawdown:>7.2f}% "
                      f"{r.profit_factor:>7.2f}")

    # 포지션 사이징
    print(f"\n  ── 포지션 사이징 (거래당 리스크 비율) ──")
    print(f"  {'리스크':>6} {'수익률':>9} {'승률':>7} {'거래':>5} {'MDD':>8} {'샤프':>8}")
    print(f"  {'-'*48}")

    for risk_pct in [0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]:
        r = run_single_backtest(
            strategies_3(), candles_mixed,
            risk_config={"max_risk_per_trade": risk_pct}
        )
        if r.total_trades > 0:
            print(f"  {risk_pct*100:>5.1f}% {r.total_return:>+8.2f}% "
                  f"{r.win_rate:>6.1f}% {r.total_trades:>5} {r.max_drawdown:>7.2f}% "
                  f"{r.sharpe_ratio:>7.2f}")

    # RSI 파라미터
    print(f"\n  ── RSI 과매수/과매도 기준값 ──")
    print(f"  {'과매도':>6} {'과매수':>6} {'수익률':>9} {'승률':>7} {'거래':>5} {'MDD':>8}")
    print(f"  {'-'*48}")

    for oversold in [20, 25, 30, 35, 40]:
        for overbought in [60, 65, 70, 75, 80]:
            r = run_single_backtest(
                [RSIStrategy({"rsi_oversold": oversold, "rsi_overbought": overbought})],
                candles_mixed,
            )
            if r.total_trades > 0:
                print(f"  {oversold:>5}  {overbought:>5}  {r.total_return:>+8.2f}% "
                      f"{r.win_rate:>6.1f}% {r.total_trades:>5} {r.max_drawdown:>7.2f}%")

    # ══════════════════════════════════════════════════════════
    # 최종 리포트
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'█'*120}")
    print(f"  🏆 최종 종합 리포트")
    print(f"{'█'*120}")

    print(f"\n  {'전략':<28} {'환경평균':>8} {'MC평균':>8} {'MC중앙':>8} "
          f"{'수익확률':>8} {'MC최악':>8} {'종합점수':>8}")
    print(f"  {'─'*88}")

    for strat_name in STRATEGY_SETS:
        env_avg = strategy_avg[strat_name]
        mc_rets = mc_results[strat_name]
        mc_avg = mean(mc_rets)
        mc_median = sorted(mc_rets)[len(mc_rets) // 2]
        mc_worst = min(mc_rets)
        profit_prob = sum(1 for r in mc_rets if r > 0) / len(mc_rets) * 100

        # 종합점수 = 환경평균*0.3 + MC평균*0.3 + 수익확률*0.2 + (1/MC최악스케일)*0.2
        score = env_avg * 0.3 + mc_avg * 0.3 + profit_prob * 0.2 + max(-20, mc_worst) * 0.2
        print(f"  {strat_name:<28} {env_avg:>+7.2f}% {mc_avg:>+7.2f}% {mc_median:>+7.2f}% "
              f"{profit_prob:>6.0f}%  {mc_worst:>+7.2f}% {score:>+7.1f}")

    print(f"\n  총 백테스트 실행 횟수: "
          f"{len(REGIMES) * len(STRATEGY_SETS)} (환경별) + "
          f"{MONTE_CARLO_RUNS * len(STRATEGY_SETS)} (몬테카를로) + "
          f"파라미터 스윕 수백 회")
    print(f"  = 약 {len(REGIMES)*len(STRATEGY_SETS) + MONTE_CARLO_RUNS*len(STRATEGY_SETS) + 200}+ 회 백테스트 완료\n")


if __name__ == "__main__":
    main()
