#!/usr/bin/env python3
"""
실제 바이낸스 데이터 백테스트 v2
- 멀티 코인: BTC, ETH, SOL, BNB, XRP, DOGE, ADA
- 멀티 타임프레임: 15m(진입) → 1h(POI) → 4h(구조) 합성
- ICT 전략 vs 기존 전략 비교
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
from crypto_bot.strategies.ict_mtf_strategy import ICTMultiTFStrategy


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
                    time.sleep(wait)
                else:
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

        current_start = int(data[-1][0]) + 1
        if len(data) < limit:
            break
        time.sleep(0.2)

    return all_candles


def run_single_backtest(strategies, candles, risk_config=None, bt_config=None,
                        allow_short=True):
    default_risk = {
        "max_risk_per_trade": 0.02, "max_positions": 5,
        "stop_loss_pct": 0.03, "take_profit_pct": 0.06,
        "max_daily_loss": 0.50, "max_drawdown": 0.50,
        "max_daily_trades": 9999, "min_signal_strength": 0.2,
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
        print(f"  {name:<30} {'거래없음':>10}")
        return
    alpha = f" (α:{r.total_return - market_return:+.1f}%)" if market_return is not None else ""
    ls = r.long_stats
    ss = r.short_stats
    long_info = f"L:{ls['count']}({ls['pnl_pct']:+.1f}%)" if ls['count'] > 0 else "L:0"
    short_info = f"S:{ss['count']}({ss['pnl_pct']:+.1f}%)" if ss['count'] > 0 else "S:0"
    win_loss = f"{r.winning_trades}W/{r.losing_trades}L"
    print(f"  {name:<30} {r.total_return:>+7.1f}%  {win_loss:>10}({r.win_rate:.0f}%)  "
          f"{r.max_drawdown:>5.1f}%  {r.sharpe_ratio:>5.1f}  "
          f"{r.profit_factor:>4.1f}  {long_info:>14}  {short_info:>14}{alpha}")


# ══════════════════════════════════════════════════════════
# 전략 셋 (1h 봉 기반)
# ══════════════════════════════════════════════════════════
STRATEGY_SETS_1H = {
    "ICT 올인 (4전략)": lambda: [OrderBlockStrategy(), FVGStrategy(),
                                TrendlineChannelStrategy(), FakeoutStrategy()],
    "ICT 컨텍스트": lambda: [ICTContextStrategy()],
    "ICT 종합": lambda: [ICTCombinedStrategy({"min_confluence": 2})],
    "OB+FVG": lambda: [OrderBlockStrategy(), FVGStrategy()],
    "Bollinger (기존)": lambda: [BollingerStrategy()],
}

# 15m 봉 기반 전략
STRATEGY_SETS_15M = {
    "ICT 멀티TF (쉽알남)": lambda: [ICTMultiTFStrategy()],
}

# 전체 코인 리스트
COINS = [
    ("BTCUSDT", "BTC"),
    ("ETHUSDT", "ETH"),
    ("SOLUSDT", "SOL"),
    ("BNBUSDT", "BNB"),
    ("XRPUSDT", "XRP"),
    ("DOGEUSDT", "DOGE"),
    ("ADAUSDT", "ADA"),
]


def main():
    print(r"""
   ____                  _        ____        _
  / ___|_ __ _   _ _ __ | |_ ___ | __ )  ___ | |_
 | |   | '__| | | | '_ \| __/ _ \|  _ \ / _ \| __|
 | |___| |  | |_| | |_) | || (_) | |_) | (_) | |_
  \____|_|   \__, | .__/ \__\___/|____/ \___/ \__|
             |___/|_|    📊 백테스트 v2 (멀티TF + 멀티코인)
    """)

    # ══════════════════════════════════════════════════════════
    # 1. 데이터 수집
    # ══════════════════════════════════════════════════════════
    print("=" * 130)
    print("  바이낸스 실제 데이터 수집 중... (7코인 × 2타임프레임)")
    print("=" * 130)

    end_time = int(datetime.now().timestamp() * 1000)
    start_3m = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    start_1m = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)

    datasets = {}

    for symbol, label in COINS:
        print(f"\n  📥 {label}/USDT 데이터 수집 중...")

        # 1h 봉 (3개월)
        candles_1h = fetch_binance_klines(symbol, "1h", start_3m, end_time)
        if not candles_1h or len(candles_1h) < 100:
            print(f"     ❌ {label} 1h 데이터 수집 실패")
            continue

        # 15m 봉 (1개월 - MTF 전략용)
        candles_15m = fetch_binance_klines(symbol, "15m", start_1m, end_time)

        start_p = candles_1h[0].open
        end_p = candles_1h[-1].close
        market_ret_3m = ((end_p - start_p) / start_p) * 100

        candles_1m_period = [c for c in candles_1h if c.timestamp >= datetime.fromtimestamp(start_1m / 1000)]
        if candles_1m_period:
            start_p_1m = candles_1m_period[0].open
            market_ret_1m = ((end_p - start_p_1m) / start_p_1m) * 100
        else:
            market_ret_1m = 0

        print(f"     ✅ 1h: {len(candles_1h)}개 | 15m: {len(candles_15m) if candles_15m else 0}개")
        print(f"     3개월 시장수익률: {market_ret_3m:+.1f}% | ${start_p:,.1f} → ${end_p:,.1f}")

        datasets[label] = {
            "1h_3m": candles_1h,
            "1h_1m": candles_1m_period,
            "15m_1m": candles_15m if candles_15m else [],
            "market_ret_3m": market_ret_3m,
            "market_ret_1m": market_ret_1m,
        }

    if not datasets:
        print("\n데이터를 가져올 수 없습니다.")
        return

    # ══════════════════════════════════════════════════════════
    # 2. 1h 전략 백테스트 (3개월)
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'='*130}")
    print("  PART 1: 1h 전략 비교 (7코인 × 3개월)")
    print("=" * 130)

    all_results_1h = {}  # {strat_name: {coin: result}}

    for coin, data in datasets.items():
        candles = data["1h_3m"]
        if len(candles) < 100:
            continue

        start_p = candles[0].open
        end_p = candles[-1].close
        mkt_ret = ((end_p - start_p) / start_p) * 100

        print(f"\n{'─'*130}")
        print(f"  {coin}/USDT | 3개월 | {len(candles)}캔들 | 시장: {mkt_ret:+.1f}%")
        print(f"{'─'*130}")
        print(f"  {'전략':<30} {'수익률':>7}  {'승/패(승률)':>14}  "
              f"{'MDD':>5}  {'샤프':>5}  {'PF':>4}  {'롱':>14}  {'숏':>14}  알파")
        print(f"  {'-'*120}")

        for strat_name, strat_factory in STRATEGY_SETS_1H.items():
            result = run_single_backtest(strat_factory(), candles)
            all_results_1h.setdefault(strat_name, {})[coin] = result
            print_result_row(strat_name, result, mkt_ret)

    # ══════════════════════════════════════════════════════════
    # 3. 15m 멀티TF 전략 백테스트 (1개월)
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'='*130}")
    print("  PART 2: 멀티TF 전략 (15m→1h→4h 합성) × 7코인 × 1개월")
    print("=" * 130)

    all_results_mtf = {}  # {coin: result}

    # 비교용: 1h 전략도 1개월로 다시 측정
    all_results_1h_1m = {}

    for coin, data in datasets.items():
        candles_15m = data["15m_1m"]
        candles_1h = data["1h_1m"]

        if not candles_15m or len(candles_15m) < 300:
            print(f"\n  ⏭️  {coin}: 15m 데이터 부족 ({len(candles_15m) if candles_15m else 0}개)")
            continue

        mkt_ret = data["market_ret_1m"]
        start_p = candles_15m[0].open
        end_p = candles_15m[-1].close

        print(f"\n{'─'*130}")
        print(f"  {coin}/USDT | 1개월 | 15m:{len(candles_15m)}캔들 / 1h:{len(candles_1h)}캔들 | 시장: {mkt_ret:+.1f}%")
        print(f"{'─'*130}")
        print(f"  {'전략':<30} {'수익률':>7}  {'승/패(승률)':>14}  "
              f"{'MDD':>5}  {'샤프':>5}  {'PF':>4}  {'롱':>14}  {'숏':>14}  알파")
        print(f"  {'-'*120}")

        # 멀티TF 전략 (15m 기반)
        for strat_name, strat_factory in STRATEGY_SETS_15M.items():
            result = run_single_backtest(
                strat_factory(), candles_15m,
                bt_config={"lookback": 200}
            )
            all_results_mtf[coin] = result
            print_result_row(strat_name, result, mkt_ret)

        # 1h 전략 비교 (같은 1개월)
        if len(candles_1h) >= 100:
            for strat_name in ["ICT 올인 (4전략)", "ICT 컨텍스트"]:
                strat_factory = STRATEGY_SETS_1H[strat_name]
                result = run_single_backtest(strat_factory(), candles_1h)
                all_results_1h_1m.setdefault(strat_name, {})[coin] = result
                print_result_row(f"  ↳ {strat_name} (1h비교)", result, mkt_ret)

    # ══════════════════════════════════════════════════════════
    # 4. 종합 리포트
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'█'*130}")
    print(f"  🏆 종합 리포트: 1h 전략 × 7코인 (3개월)")
    print(f"{'█'*130}")

    coins_list = [c for c in datasets]
    header = f"  {'전략':<30}"
    for coin in coins_list:
        header += f" {coin:>8}"
    header += f"  {'평균':>7}  {'승률':>5}"
    print(header)
    print(f"  {'-'*130}")

    for strat_name in STRATEGY_SETS_1H:
        row = f"  {strat_name:<30}"
        returns = []
        win_rates = []
        for coin in coins_list:
            r = all_results_1h.get(strat_name, {}).get(coin)
            if r and r.total_trades > 0:
                returns.append(r.total_return)
                win_rates.append(r.win_rate)
                row += f" {r.total_return:>+7.1f}%"
            else:
                row += f" {'N/A':>8}"
        if returns:
            row += f"  {mean(returns):>+6.1f}%  {mean(win_rates):>4.0f}%"
        print(row)

    # 멀티TF 종합
    print(f"\n{'█'*130}")
    print(f"  🏆 멀티TF vs 1h 비교 (1개월)")
    print(f"{'█'*130}")

    header = f"  {'전략':<30}"
    for coin in coins_list:
        header += f" {coin:>8}"
    header += f"  {'평균':>7}  {'승률':>5}"
    print(header)
    print(f"  {'-'*130}")

    # MTF 결과
    row = f"  {'ICT 멀티TF (쉽알남)':<30}"
    mtf_returns = []
    mtf_wrs = []
    for coin in coins_list:
        r = all_results_mtf.get(coin)
        if r and r.total_trades > 0:
            mtf_returns.append(r.total_return)
            mtf_wrs.append(r.win_rate)
            row += f" {r.total_return:>+7.1f}%"
        else:
            row += f" {'N/A':>8}"
    if mtf_returns:
        row += f"  {mean(mtf_returns):>+6.1f}%  {mean(mtf_wrs):>4.0f}%"
    print(row)

    # 1h 비교
    for strat_name in ["ICT 올인 (4전략)", "ICT 컨텍스트"]:
        row = f"  {strat_name:<30}"
        returns = []
        wrs = []
        for coin in coins_list:
            r = all_results_1h_1m.get(strat_name, {}).get(coin)
            if r and r.total_trades > 0:
                returns.append(r.total_return)
                wrs.append(r.win_rate)
                row += f" {r.total_return:>+7.1f}%"
            else:
                row += f" {'N/A':>8}"
        if returns:
            row += f"  {mean(returns):>+6.1f}%  {mean(wrs):>4.0f}%"
        print(row)

    # 시장 수익률
    row = f"  {'📊 시장 수익률':<30}"
    for coin in coins_list:
        mkt = datasets[coin]["market_ret_1m"]
        row += f" {mkt:>+7.1f}%"
    all_mkt = [datasets[c]["market_ret_1m"] for c in coins_list]
    row += f"  {mean(all_mkt):>+6.1f}%"
    print(row)

    # 거래 횟수 비교
    print(f"\n  📊 거래 횟수 비교 (1개월):")
    for coin in coins_list:
        r_mtf = all_results_mtf.get(coin)
        r_1h = all_results_1h_1m.get("ICT 올인 (4전략)", {}).get(coin)
        mtf_t = r_mtf.total_trades if r_mtf else 0
        h1_t = r_1h.total_trades if r_1h else 0
        print(f"     {coin}: 멀티TF={mtf_t}회 | ICT올인(1h)={h1_t}회")

    print(f"\n  총 코인: {len(datasets)}개 | 전략: {len(STRATEGY_SETS_1H) + len(STRATEGY_SETS_15M)}개")
    print()


if __name__ == "__main__":
    main()
