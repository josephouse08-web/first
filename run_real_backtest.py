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


def _calc_market_return(candles):
    if not candles or len(candles) < 2:
        return 0
    return ((candles[-1].close - candles[0].open) / candles[0].open) * 100


def _filter_candles_after(candles, ts):
    return [c for c in candles if c.timestamp >= ts]


def main():
    print(r"""
   ____                  _        ____        _
  / ___|_ __ _   _ _ __ | |_ ___ | __ )  ___ | |_
 | |   | '__| | | | '_ \| __/ _ \|  _ \ / _ \| __|
 | |___| |  | |_| | |_) | || (_) | |_) | (_) | |_
  \____|_|   \__, | .__/ \__\___/|____/ \___/ \__|
             |___/|_|    📊 백테스트 v3 (2년 + 멀티TF + 멀티코인)
    """)

    # ══════════════════════════════════════════════════════════
    # 1. 데이터 수집 (2년 1h + 3개월 15m)
    # ══════════════════════════════════════════════════════════
    print("=" * 130)
    print("  바이낸스 실제 데이터 수집 중... (7코인 × 2년 1h + 2년 15m)")
    print("=" * 130)

    end_time = int(datetime.now().timestamp() * 1000)
    start_2y = int((datetime.now() - timedelta(days=730)).timestamp() * 1000)
    start_1y = int((datetime.now() - timedelta(days=365)).timestamp() * 1000)
    start_6m = int((datetime.now() - timedelta(days=180)).timestamp() * 1000)
    start_3m = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    start_1m = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)

    # 타임스탬프 → datetime 변환
    ts_1y = datetime.fromtimestamp(start_1y / 1000)
    ts_6m = datetime.fromtimestamp(start_6m / 1000)
    ts_3m = datetime.fromtimestamp(start_3m / 1000)
    ts_1m = datetime.fromtimestamp(start_1m / 1000)

    datasets = {}

    for symbol, label in COINS:
        print(f"\n  📥 {label}/USDT 데이터 수집 중...")

        # 1h 봉 (2년)
        candles_1h = fetch_binance_klines(symbol, "1h", start_2y, end_time)
        if not candles_1h or len(candles_1h) < 200:
            print(f"     ❌ {label} 1h 데이터 수집 실패")
            continue

        # 15m 봉 (2년 - MTF 전략용, 강세장 포함)
        print(f"     📥 15m 봉 수집 중 (2년, ~70,000개)...")
        candles_15m = fetch_binance_klines(symbol, "15m", start_2y, end_time)

        start_p = candles_1h[0].open
        end_p = candles_1h[-1].close
        mkt_2y = _calc_market_return(candles_1h)

        print(f"     ✅ 1h: {len(candles_1h)}개 | 15m: {len(candles_15m) if candles_15m else 0}개 (2년)")
        print(f"     2년 시장수익률: {mkt_2y:+.1f}% | ${start_p:,.1f} → ${end_p:,.1f}")

        # 기간별 슬라이스
        c_1y = _filter_candles_after(candles_1h, ts_1y)
        c_6m = _filter_candles_after(candles_1h, ts_6m)
        c_3m = _filter_candles_after(candles_1h, ts_3m)
        c_1m = _filter_candles_after(candles_1h, ts_1m)

        # 15m 기간별 슬라이스
        c15_1y = _filter_candles_after(candles_15m, ts_1y) if candles_15m else []
        c15_6m = _filter_candles_after(candles_15m, ts_6m) if candles_15m else []
        c15_3m = _filter_candles_after(candles_15m, ts_3m) if candles_15m else []

        datasets[label] = {
            "1h_2y": candles_1h,
            "1h_1y": c_1y,
            "1h_6m": c_6m,
            "1h_3m": c_3m,
            "1h_1m": c_1m,
            "15m_2y": candles_15m if candles_15m else [],
            "15m_1y": c15_1y,
            "15m_6m": c15_6m,
            "15m_3m": c15_3m,
        }

    if not datasets:
        print("\n데이터를 가져올 수 없습니다.")
        return

    # ══════════════════════════════════════════════════════════
    # 2. 1h 전략: 기간별 백테스트 (2년, 1년, 6개월, 3개월)
    # ══════════════════════════════════════════════════════════
    PERIODS = [
        ("2년", "1h_2y"),
        ("1년", "1h_1y"),
        ("6개월", "1h_6m"),
        ("3개월", "1h_3m"),
    ]

    # {strat_name: {period_key: {coin: result}}}
    all_results = {}

    for period_label, period_key in PERIODS:
        print(f"\n\n{'='*130}")
        print(f"  PART: 1h 전략 비교 × 7코인 × {period_label}")
        print("=" * 130)

        for coin, data in datasets.items():
            candles = data.get(period_key, [])
            if len(candles) < 100:
                continue

            mkt_ret = _calc_market_return(candles)

            print(f"\n{'─'*130}")
            print(f"  {coin}/USDT | {period_label} | {len(candles)}캔들 | 시장: {mkt_ret:+.1f}%")
            print(f"{'─'*130}")
            print(f"  {'전략':<30} {'수익률':>7}  {'승/패(승률)':>14}  "
                  f"{'MDD':>5}  {'샤프':>5}  {'PF':>4}  {'롱':>14}  {'숏':>14}  알파")
            print(f"  {'-'*120}")

            for strat_name, strat_factory in STRATEGY_SETS_1H.items():
                result = run_single_backtest(strat_factory(), candles)
                all_results.setdefault(strat_name, {}).setdefault(period_key, {})[coin] = result
                print_result_row(strat_name, result, mkt_ret)

    # ══════════════════════════════════════════════════════════
    # 3. 15m 멀티TF 전략: 기간별 (2년, 1년, 6개월, 3개월)
    # ══════════════════════════════════════════════════════════
    MTF_PERIODS = [
        ("2년", "15m_2y"),
        ("1년", "15m_1y"),
        ("6개월", "15m_6m"),
        ("3개월", "15m_3m"),
    ]

    # {period_key: {coin: result}}
    all_results_mtf = {}

    for period_label, period_key in MTF_PERIODS:
        print(f"\n\n{'='*130}")
        print(f"  멀티TF 전략 (15m→1h→4h) × 7코인 × {period_label}")
        print("=" * 130)

        for coin, data in datasets.items():
            candles_15m = data.get(period_key, [])
            if not candles_15m or len(candles_15m) < 500:
                print(f"\n  ⏭️  {coin}: 15m 데이터 부족 ({len(candles_15m) if candles_15m else 0}개)")
                continue

            mkt_ret = _calc_market_return(candles_15m)

            print(f"\n{'─'*130}")
            print(f"  {coin}/USDT | {period_label} 15m | {len(candles_15m)}캔들 | 시장: {mkt_ret:+.1f}%")
            print(f"{'─'*130}")
            print(f"  {'전략':<30} {'수익률':>7}  {'승/패(승률)':>14}  "
                  f"{'MDD':>5}  {'샤프':>5}  {'PF':>4}  {'롱':>14}  {'숏':>14}  알파")
            print(f"  {'-'*120}")

            for strat_name, strat_factory in STRATEGY_SETS_15M.items():
                result = run_single_backtest(
                    strat_factory(), candles_15m,
                    bt_config={"lookback": 300}
                )
                all_results_mtf.setdefault(period_key, {})[coin] = result
                print_result_row(strat_name, result, mkt_ret)

    # ══════════════════════════════════════════════════════════
    # 4. 기간별 종합 리포트
    # ══════════════════════════════════════════════════════════
    coins_list = list(datasets.keys())

    for period_label, period_key in PERIODS:
        print(f"\n{'█'*130}")
        print(f"  🏆 종합: 1h 전략 × 7코인 × {period_label}")
        print(f"{'█'*130}")

        header = f"  {'전략':<30}"
        for coin in coins_list:
            header += f" {coin:>8}"
        header += f"  {'평균':>7}  {'승률':>5}  {'MDD':>5}"
        print(header)
        print(f"  {'-'*130}")

        for strat_name in STRATEGY_SETS_1H:
            row = f"  {strat_name:<30}"
            returns, win_rates, mdds = [], [], []
            for coin in coins_list:
                r = all_results.get(strat_name, {}).get(period_key, {}).get(coin)
                if r and r.total_trades > 0:
                    returns.append(r.total_return)
                    win_rates.append(r.win_rate)
                    mdds.append(r.max_drawdown)
                    row += f" {r.total_return:>+7.1f}%"
                else:
                    row += f" {'N/A':>8}"
            if returns:
                row += f"  {mean(returns):>+6.1f}%  {mean(win_rates):>4.0f}%  {mean(mdds):>4.1f}%"
            print(row)

        # 시장 수익률
        row = f"  {'📊 시장 수익률':<30}"
        mkts = []
        for coin in coins_list:
            candles = datasets[coin].get(period_key, [])
            mkt = _calc_market_return(candles)
            mkts.append(mkt)
            row += f" {mkt:>+7.1f}%"
        row += f"  {mean(mkts):>+6.1f}%"
        print(row)

    # ══════════════════════════════════════════════════════════
    # 5. 최종 요약: 전 기간 전략 순위
    # ══════════════════════════════════════════════════════════
    print(f"\n\n{'█'*130}")
    print(f"  🏆 최종 요약: 전략 × 기간 평균 수익률")
    print(f"{'█'*130}")

    header = f"  {'전략':<30}"
    for pl, _ in PERIODS:
        header += f"  {pl:>8}"
    header += f"  {'전체평균':>8}"
    print(header)
    print(f"  {'-'*100}")

    rankings = []
    for strat_name in STRATEGY_SETS_1H:
        row = f"  {strat_name:<30}"
        period_avgs = []
        for _, period_key in PERIODS:
            rets = []
            for coin in coins_list:
                r = all_results.get(strat_name, {}).get(period_key, {}).get(coin)
                if r and r.total_trades > 0:
                    rets.append(r.total_return)
            if rets:
                avg = mean(rets)
                period_avgs.append(avg)
                row += f"  {avg:>+7.1f}%"
            else:
                row += f"  {'N/A':>8}"
        if period_avgs:
            overall = mean(period_avgs)
            row += f"  {overall:>+7.1f}%"
            rankings.append((strat_name, overall))
        print(row)

    # 시장
    row = f"  {'📊 시장':<30}"
    mkt_avgs = []
    for _, period_key in PERIODS:
        mkts = [_calc_market_return(datasets[c].get(period_key, [])) for c in coins_list]
        avg = mean(mkts) if mkts else 0
        mkt_avgs.append(avg)
        row += f"  {avg:>+7.1f}%"
    row += f"  {mean(mkt_avgs):>+7.1f}%"
    print(row)

    # 순위
    rankings.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  📊 전략 순위 (전 기간 평균):")
    for i, (name, avg) in enumerate(rankings, 1):
        mkt_avg = mean(mkt_avgs)
        alpha = avg - mkt_avg
        print(f"     {i}. {name:<28} {avg:>+7.1f}% (시장 대비 α: {alpha:>+6.1f}%)")

    # MTF 기간별 종합
    if all_results_mtf:
        print(f"\n{'█'*130}")
        print(f"  🏆 멀티TF (쉽알남) 기간별 종합")
        print(f"{'█'*130}")

        header = f"  {'기간':<12}"
        for coin in coins_list:
            header += f" {coin:>8}"
        header += f"  {'평균':>7}  {'승률':>5}"
        print(header)
        print(f"  {'-'*100}")

        for period_label, period_key in MTF_PERIODS:
            row = f"  {period_label:<12}"
            rets, wrs = [], []
            for coin in coins_list:
                r = all_results_mtf.get(period_key, {}).get(coin)
                if r and r.total_trades > 0:
                    rets.append(r.total_return)
                    wrs.append(r.win_rate)
                    row += f" {r.total_return:>+7.1f}%"
                else:
                    row += f" {'N/A':>8}"
            if rets:
                row += f"  {mean(rets):>+6.1f}%  {mean(wrs):>4.0f}%"
            print(row)

    print(f"\n  총 코인: {len(datasets)}개 | 기간: 4개 (2년/1년/6개월/3개월)")
    print(f"  총 백테스트 실행: ~{len(STRATEGY_SETS_1H) * 4 * len(datasets) + len(datasets)}회")
    print()


if __name__ == "__main__":
    main()
