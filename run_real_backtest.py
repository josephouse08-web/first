#!/usr/bin/env python3
"""
실제 바이낸스 데이터 백테스트
- 공개 API로 BTC/ETH 과거 1시간봉 데이터 수집
- ICT 전략 vs 기존 전략 비교
- 실제 시장 데이터에서의 성과 검증
"""

import sys
import json
import math
from pathlib import Path
from datetime import datetime, timedelta
from statistics import mean, stdev
from urllib.request import urlopen, Request
from urllib.error import URLError
import time

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
from crypto_bot.strategies.ict_context_strategy import ICTContextStrategy


def fetch_binance_klines(symbol: str, interval: str, start_time: int,
                         end_time: int, limit: int = 1000) -> list[Candle]:
    """바이낸스 공개 API에서 캔들 데이터 가져오기"""
    all_candles = []
    current_start = start_time

    while current_start < end_time:
        url = (
            f"https://data-api.binance.vision/api/v3/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_time}&limit={limit}"
        )

        for attempt in range(4):
            try:
                req = Request(url, headers={"User-Agent": "CryptoBot/1.0"})
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except (URLError, Exception) as e:
                if attempt < 3:
                    wait = 2 ** (attempt + 1)
                    print(f"    API 재시도 ({attempt + 1}/4), {wait}초 대기... ({e})")
                    time.sleep(wait)
                else:
                    print(f"    API 실패: {e}")
                    return all_candles

        if not data:
            break

        for k in data:
            candle = Candle(
                timestamp=datetime.fromtimestamp(k[0] / 1000),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            all_candles.append(candle)

        # 다음 배치
        current_start = int(data[-1][0]) + 1

        if len(data) < limit:
            break

        time.sleep(0.2)  # API 레이트 리밋 방지

    return all_candles


def run_single_backtest(strategies, candles, risk_config=None, bt_config=None,
                        allow_short=True):
    """단일 백테스트 실행"""
    default_risk = {
        "max_risk_per_trade": 0.02,
        "max_positions": 5,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "max_daily_loss": 0.50,
        "max_drawdown": 0.50,
        "max_daily_trades": 9999,
        "min_signal_strength": 0.2,
    }
    if risk_config:
        default_risk.update(risk_config)

    default_bt = {"initial_balance": 10000, "commission_rate": 0.001, "lookback": 50}
    if bt_config:
        default_bt.update(bt_config)

    engine = BacktestEngine(default_bt)
    return engine.run(candles, strategies, default_risk, allow_short=allow_short)


def print_result_row(name, r, market_return=None):
    trades = r.total_trades
    if trades == 0:
        print(f"  {name:<28} {'거래 없음':>10}")
        return
    alpha = f" (α: {r.total_return - market_return:+.2f}%)" if market_return is not None else ""
    ls = r.long_stats
    ss = r.short_stats
    long_info = f"L:{ls['count']}({ls['pnl_pct']:+.1f}%)" if ls['count'] > 0 else "L:0"
    short_info = f"S:{ss['count']}({ss['pnl_pct']:+.1f}%)" if ss['count'] > 0 else "S:0"
    win_loss = f"{r.winning_trades}W/{r.losing_trades}L"
    print(f"  {name:<28} {r.total_return:>+8.2f}%  {win_loss:>10}({r.win_rate:.0f}%)  "
          f"{r.max_drawdown:>6.2f}%  {r.sharpe_ratio:>6.2f}  "
          f"{r.profit_factor:>5.2f}  {long_info:>16}  {short_info:>16}{alpha}")


# 전략 셋
STRATEGY_SETS = {
    # ICT 전략
    "오더블럭(OB)": lambda: [OrderBlockStrategy()],
    "FVG": lambda: [FVGStrategy()],
    "추세선/채널": lambda: [TrendlineChannelStrategy()],
    "거짓돌파(Fakeout)": lambda: [FakeoutStrategy()],
    "ICT 종합": lambda: [ICTCombinedStrategy({"min_confluence": 2})],
    "OB+FVG": lambda: [OrderBlockStrategy(), FVGStrategy()],
    "ICT 올인 (4전략)": lambda: [OrderBlockStrategy(), FVGStrategy(),
                                TrendlineChannelStrategy(), FakeoutStrategy()],
    # 신규: 컨텍스트 기반 종합 분석
    "ICT 컨텍스트": lambda: [ICTContextStrategy()],
    "ICT 컨텍스트+OB": lambda: [ICTContextStrategy(), OrderBlockStrategy()],
    # 기존 전략 (비교용)
    "RSI (기존)": lambda: [RSIStrategy()],
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
             |___/|_|    📊 실제 데이터 백테스트 v1.0
    """)

    # ══════════════════════════════════════════════════════════
    # 데이터 수집
    # ══════════════════════════════════════════════════════════
    print("=" * 120)
    print("  바이낸스 실제 데이터 수집 중...")
    print("=" * 120)

    # 기간 설정: 최근 6개월 (1시간봉)
    end_time = int(datetime.now().timestamp() * 1000)
    start_6m = int((datetime.now() - timedelta(days=180)).timestamp() * 1000)
    start_3m = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    start_1m = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)

    datasets = {}

    for symbol, label in [("BTCUSDT", "BTC/USDT"), ("ETHUSDT", "ETH/USDT")]:
        print(f"\n  📥 {label} 6개월 1시간봉 데이터 수집 중...")
        candles = fetch_binance_klines(symbol, "1h", start_6m, end_time)
        if candles:
            print(f"     ✅ {len(candles)}개 캔들 수집 완료")
            print(f"     기간: {candles[0].timestamp.strftime('%Y-%m-%d')} → {candles[-1].timestamp.strftime('%Y-%m-%d')}")
            start_p = candles[0].open
            end_p = candles[-1].close
            market_ret = ((end_p - start_p) / start_p) * 100
            print(f"     시장수익률: {market_ret:+.2f}% (${start_p:,.0f} → ${end_p:,.0f})")
            datasets[label] = {
                "6m": candles,
                "3m": [c for c in candles if c.timestamp >= datetime.fromtimestamp(start_3m / 1000)],
                "1m": [c for c in candles if c.timestamp >= datetime.fromtimestamp(start_1m / 1000)],
                "market_return": market_ret,
            }
        else:
            print(f"     ❌ 데이터 수집 실패")

    if not datasets:
        print("\n데이터를 가져올 수 없습니다. 네트워크 연결을 확인하세요.")
        return

    # ══════════════════════════════════════════════════════════
    # PART 1: 심볼별 × 기간별 × 전략별 백테스트
    # ══════════════════════════════════════════════════════════
    all_results = {}

    for symbol_label, data in datasets.items():
        for period_label, period_key in [("6개월", "6m"), ("3개월", "3m"), ("1개월", "1m")]:
            candles = data[period_key]
            if len(candles) < 100:
                continue

            start_p = candles[0].open
            end_p = candles[-1].close
            market_ret = ((end_p - start_p) / start_p) * 100

            print(f"\n{'─'*120}")
            print(f"  {symbol_label} | {period_label} | {len(candles)}캔들 | "
                  f"시장수익률: {market_ret:+.2f}% | "
                  f"${start_p:,.0f} → ${end_p:,.0f}")
            print(f"{'─'*120}")
            print(f"  {'전략':<28} {'수익률':>8}  {'승/패(승률)':>14}  "
                  f"{'MDD':>6}  {'샤프':>6}  {'PF':>5}  {'롱(수익률)':>16}  {'숏(수익률)':>16}  알파")
            print(f"  {'-'*115}")

            key = f"{symbol_label}_{period_key}"
            for strat_name, strat_factory in STRATEGY_SETS.items():
                result = run_single_backtest(strat_factory(), candles)
                all_results.setdefault(strat_name, {})[key] = result
                print_result_row(strat_name, result, market_ret)

    # ══════════════════════════════════════════════════════════
    # PART 2: SL/TP 최적화 (실제 BTC 6개월 데이터)
    # ══════════════════════════════════════════════════════════
    if "BTC/USDT" in datasets:
        btc_candles = datasets["BTC/USDT"]["6m"]
        print(f"\n\n{'='*120}")
        print("  PART 2: SL/TP 최적화 (BTC 6개월 실제 데이터)")
        print("=" * 120)

        # ICT 올인 전략으로 SL/TP 스윕
        print(f"\n  ── ICT 올인 전략: SL/TP 조합 ──")
        print(f"  {'SL':>5} {'TP':>5} {'수익률':>9} {'승률':>7} {'거래':>5} {'MDD':>8} {'PF':>8} {'샤프':>8}")
        print(f"  {'-'*60}")

        best_score = -999
        best_params = {}

        for sl in [0.015, 0.02, 0.03, 0.04, 0.05, 0.07]:
            for tp in [0.03, 0.04, 0.06, 0.08, 0.10, 0.15]:
                if tp <= sl:
                    continue
                strats = [OrderBlockStrategy(), FVGStrategy(),
                          TrendlineChannelStrategy(), FakeoutStrategy()]
                r = run_single_backtest(
                    strats, btc_candles,
                    risk_config={"stop_loss_pct": sl, "take_profit_pct": tp}
                )
                if r.total_trades > 0:
                    score = r.total_return * 0.3 + r.sharpe_ratio * 20 + r.profit_factor * 10 - r.max_drawdown * 0.5
                    if score > best_score:
                        best_score = score
                        best_params = {"sl": sl, "tp": tp, "result": r}
                    print(f"  {sl*100:>4.1f}% {tp*100:>4.1f}% {r.total_return:>+8.2f}% "
                          f"{r.win_rate:>6.1f}% {r.total_trades:>5} {r.max_drawdown:>7.2f}% "
                          f"{r.profit_factor:>7.2f} {r.sharpe_ratio:>7.2f}")

        if best_params:
            print(f"\n  🏆 최적 SL/TP: SL={best_params['sl']*100:.1f}%, TP={best_params['tp']*100:.1f}%")

    # ══════════════════════════════════════════════════════════
    # 최종 종합 리포트
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'█'*120}")
    print(f"  🏆 최종 종합 리포트 (실제 바이낸스 데이터)")
    print(f"{'█'*120}")

    print(f"\n  {'전략':<28}", end="")
    data_keys = []
    for symbol_label in datasets:
        for period_key in ["6m", "3m", "1m"]:
            key = f"{symbol_label}_{period_key}"
            if any(key in v for v in all_results.values()):
                data_keys.append(key)
                short = f"{symbol_label[:3]}_{period_key}"
                print(f" {short:>10}", end="")
    print(f"  {'평균':>8}  {'승률평균':>8}")
    print(f"  {'-'*120}")

    for strat_name in STRATEGY_SETS:
        print(f"  {strat_name:<28}", end="")
        returns = []
        win_rates = []
        for key in data_keys:
            r = all_results.get(strat_name, {}).get(key)
            if r and r.total_trades > 0:
                returns.append(r.total_return)
                win_rates.append(r.win_rate)
                print(f"  {r.total_return:>+8.1f}%", end="")
            else:
                print(f"  {'N/A':>9}", end="")

        if returns:
            avg_ret = mean(returns)
            avg_wr = mean(win_rates)
            print(f"  {avg_ret:>+7.1f}%  {avg_wr:>6.1f}%")
        else:
            print(f"  {'N/A':>8}  {'N/A':>8}")

    print(f"\n  총 백테스트 실행 횟수: {len(data_keys) * len(STRATEGY_SETS)} + SL/TP 최적화")
    print()


if __name__ == "__main__":
    main()
