"""
SMC (Smart Money Concepts) 백테스터
- Claude Vision으로 차트를 실제 사람처럼 분석
- 롱/숏 둘 다 시뮬레이션
- 구조물 기반 진입/손절/익절
"""
import time
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import pyupbit
import pandas as pd

from chart_generator import generate_multi_timeframe_chart, generate_naked_chart
from ai_analyzer import AIAnalyzer
from config import Config
from logger_setup import setup_logger

logger = setup_logger("backtester")


@dataclass
class BacktestTrade:
    """백테스트 거래 기록"""
    trade_id: int = 0
    direction: str = ""        # "long" or "short"
    entry_time: str = ""
    exit_time: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    target_price: float = 0.0
    stop_loss: float = 0.0
    pnl_pct: float = 0.0
    pnl_krw: float = 0.0
    exit_reason: str = ""
    confidence: float = 0.0
    confluence: int = 0
    smc_reason: str = ""


@dataclass
class BacktestResult:
    """백테스트 결과 요약"""
    total_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    total_pnl_krw: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_pnl_per_trade: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_like: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    trades: list = field(default_factory=list)


class SMCBacktester:
    """SMC 전략 백테스터"""

    def __init__(self, coin: str = None, initial_balance: float = 1_000_000):
        self.coin = coin or Config.COIN
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.analyzer = AIAnalyzer()
        self.trades: list[BacktestTrade] = []
        self.trade_counter = 0
        self.position = None  # {"direction", "entry_price", "target", "stop_loss", "entry_time"}
        self.balance_history = [initial_balance]
        self.api_call_delay = 3  # Claude API 호출 간격 (초)

    def run(self, days_back: int = 7, analysis_interval_candles: int = 10) -> BacktestResult:
        """백테스트 실행

        Args:
            days_back: 며칠 전 데이터부터 테스트할지
            analysis_interval_candles: 몇 캔들마다 AI 분석할지 (비용 절약)
        """
        logger.info("=" * 60)
        logger.info("SMC 백테스트 시작")
        logger.info(f"코인: {self.coin}")
        logger.info(f"초기 자본: {self.initial_balance:,.0f}원")
        logger.info(f"기간: 최근 {days_back}일")
        logger.info(f"분석 간격: {analysis_interval_candles}캔들마다")
        logger.info("=" * 60)

        # 1. 히스토리 데이터 수집 (5분봉 기준 스캘핑)
        logger.info("히스토리 데이터 수집 중...")
        all_data = self._fetch_historical_data(days_back)
        if all_data is None or all_data.empty:
            logger.error("데이터 수집 실패")
            return self._build_result()

        logger.info(f"총 {len(all_data)}개 캔들 수집 완료")
        logger.info(f"기간: {all_data.index[0]} ~ {all_data.index[-1]}")

        # 2. 슬라이딩 윈도우로 분석
        window_size = Config.CANDLE_COUNT  # 80캔들 윈도우
        total_windows = (len(all_data) - window_size) // analysis_interval_candles
        logger.info(f"총 {total_windows}개 분석 포인트")

        for i in range(0, len(all_data) - window_size, analysis_interval_candles):
            window = all_data.iloc[i:i + window_size]
            current_idx = i + window_size - 1
            current_candle = all_data.iloc[current_idx]
            current_price = current_candle["close"]
            current_time = all_data.index[current_idx]

            step = (i // analysis_interval_candles) + 1
            logger.info(
                f"\n--- 분석 {step}/{total_windows} "
                f"({current_time.strftime('%m/%d %H:%M')}) "
                f"가격: {current_price:,.0f}원 ---"
            )

            # 포지션 보유 중이면 손절/익절 체크
            if self.position:
                exit_reason = self._check_position_exit(current_price, current_time)
                if exit_reason:
                    self._close_position(current_price, current_time, exit_reason)

            # 멀티 타임프레임 차트 생성
            tf_data = self._build_multi_timeframe(all_data, current_idx)
            if not tf_data:
                continue

            chart_image = generate_multi_timeframe_chart(tf_data)
            if not chart_image:
                continue

            # Claude Vision SMC 분석
            context = (
                f"현재가: {current_price:,.0f}원, 코인: {self.coin}, "
                f"시각: {current_time.strftime('%Y-%m-%d %H:%M')}, "
                f"타임프레임: {', '.join(tf_data.keys())}, "
                f"백테스트 모드: 롱/숏 모두 가능"
            )

            try:
                analysis = self.analyzer.analyze_chart(chart_image, context)
            except Exception as e:
                logger.error(f"AI 분석 실패: {e}")
                time.sleep(self.api_call_delay)
                continue

            decision = analysis.get("decision", "hold")
            confidence = analysis.get("confidence", 0.0)
            confluence = analysis.get("confluence_count", 0)

            logger.info(
                f"AI: {decision} (신뢰도: {confidence:.0%}, 근거: {confluence}개)"
            )
            if analysis.get("reason"):
                logger.info(f"  사유: {analysis['reason'][:100]}")

            # 포지션 없을 때만 신규 진입 판단
            if not self.position:
                self._evaluate_entry(analysis, current_price, current_time)

            # API 속도 제한
            time.sleep(self.api_call_delay)

        # 미청산 포지션 강제 종료
        if self.position:
            last_price = all_data.iloc[-1]["close"]
            last_time = all_data.index[-1]
            self._close_position(last_price, last_time, "백테스트 종료 - 강제 청산")

        result = self._build_result()
        self._print_result(result)
        self._save_result(result)
        return result

    def _fetch_historical_data(self, days_back: int) -> pd.DataFrame:
        """히스토리 5분봉 데이터 수집"""
        all_frames = []
        # pyupbit은 한 번에 최대 200개 캔들 반환
        candles_needed = days_back * 24 * 12  # 5분봉 = 하루 288개
        fetched = 0

        to = None
        while fetched < candles_needed:
            try:
                count = min(200, candles_needed - fetched)
                df = pyupbit.get_ohlcv(self.coin, interval="minute5", count=count, to=to)
                if df is None or df.empty:
                    break
                all_frames.append(df)
                to = df.index[0]
                fetched += len(df)
                time.sleep(0.15)  # Upbit API 속도 제한
            except Exception as e:
                logger.error(f"데이터 수집 에러: {e}")
                break

        if not all_frames:
            return pd.DataFrame()

        result = pd.concat(all_frames)
        result = result[~result.index.duplicated(keep="first")]
        result = result.sort_index()
        return result

    def _build_multi_timeframe(self, all_data: pd.DataFrame, current_idx: int) -> dict:
        """현재 시점 기준 멀티 타임프레임 데이터 구성"""
        tf_data = {}

        # 5분봉 (기본)
        start = max(0, current_idx - Config.CANDLE_COUNT + 1)
        df_5m = all_data.iloc[start:current_idx + 1].copy()
        if len(df_5m) >= 20:
            tf_data["minute5"] = df_5m

        # 15분봉 (5분봉 3개 묶기)
        df_15m = self._resample(df_5m, "15min")
        if len(df_15m) >= 15:
            tf_data["minute15"] = df_15m

        # 30분봉
        df_30m = self._resample(df_5m, "30min")
        if len(df_30m) >= 10:
            tf_data["minute30"] = df_30m

        # 1시간봉
        df_1h = self._resample(df_5m, "1h")
        if len(df_1h) >= 5:
            tf_data["minute60"] = df_1h

        return tf_data

    def _resample(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        """OHLCV 리샘플링"""
        resampled = df.resample(freq).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        return resampled

    def _evaluate_entry(self, analysis: dict, price: float, timestamp):
        """신규 진입 평가"""
        decision = analysis.get("decision", "hold")
        confidence = analysis.get("confidence", 0.0)
        confluence = analysis.get("confluence_count", 0)

        if decision == "hold":
            return

        # SMC 필터: 신뢰도 + 다중 근거
        if confidence < Config.MIN_CONFIDENCE:
            logger.info(f"  → 패스 (신뢰도 {confidence:.0%} < {Config.MIN_CONFIDENCE:.0%})")
            return

        if confluence < Config.MIN_CONFLUENCE:
            logger.info(f"  → 패스 (근거 {confluence}개 < {Config.MIN_CONFLUENCE}개)")
            return

        entry_price = analysis.get("entry_price") or price
        target_price = analysis.get("target_price")
        stop_loss = analysis.get("stop_loss")

        # AI가 축약 가격(예: 1.025 = 1억250만)을 반환하는 경우 보정
        target_price = self._fix_price(target_price, price)
        stop_loss = self._fix_price(stop_loss, price)

        if not target_price or not stop_loss:
            logger.info("  → 패스 (유효하지 않은 목표가/손절가)")
            return

        # 방향 결정
        if decision == "buy":
            direction = "long"
        elif decision == "sell":
            direction = "short"
        else:
            return

        # R:R 비율 체크
        if entry_price and target_price and stop_loss:
            if direction == "long" and entry_price > stop_loss:
                risk = entry_price - stop_loss
                reward = target_price - entry_price
            elif direction == "short" and stop_loss > entry_price:
                risk = stop_loss - entry_price
                reward = entry_price - target_price
            else:
                risk = 0
                reward = 0

            if risk > 0:
                rr = reward / risk
                if rr < 1.5:
                    logger.info(f"  → 패스 (R:R {rr:.1f}:1 < 1.5:1)")
                    return
                logger.info(f"  R:R 비율: {rr:.1f}:1")

        # 진입
        self.position = {
            "direction": direction,
            "entry_price": price,  # 현재가로 진입 (시장가 시뮬레이션)
            "target_price": target_price,
            "stop_loss": stop_loss,
            "entry_time": timestamp,
            "confidence": confidence,
            "confluence": confluence,
            "reason": analysis.get("reason", ""),
        }

        logger.info(
            f"  ★ {direction.upper()} 진입 @ {price:,.0f}원 "
            f"(목표: {target_price:,.0f}원, 손절: {stop_loss:,.0f}원)"
        )

    def _fix_price(self, raw_price, current_price: float):
        """AI가 축약 가격(1.025 = 1억250만)을 반환하는 경우 실제 가격으로 변환"""
        if raw_price is None:
            return None
        try:
            raw_price = float(raw_price)
        except (ValueError, TypeError):
            return None

        # 현재가 대비 너무 작으면 축약 형태로 판단
        if current_price > 1_000_000 and raw_price < 10_000:
            # 예: BTC 1억대에서 1.025 → 102,500,000
            magnitude = 10 ** len(str(int(current_price)))
            converted = raw_price * magnitude
            # 현재가 대비 ±20% 범위 내인지 확인
            if abs(converted - current_price) / current_price < 0.2:
                return converted
            # 다른 스케일 시도 (예: 102500 → 102,500,000)
            for scale in [1000, 100, 10]:
                converted = raw_price * scale
                if abs(converted - current_price) / current_price < 0.2:
                    return converted
            return None

        # 현재가 대비 ±20% 이내면 유효
        if abs(raw_price - current_price) / current_price < 0.2:
            return raw_price

        return None

    def _check_position_exit(self, current_price: float, current_time) -> str:
        """포지션 손절/익절 체크"""
        if not self.position:
            return ""

        direction = self.position["direction"]
        target = self.position.get("target_price")
        stop = self.position.get("stop_loss")

        if direction == "long":
            if target and current_price >= target:
                return "목표가 도달 (익절)"
            if stop and current_price <= stop:
                return "손절가 도달 (손절)"
        elif direction == "short":
            if target and current_price <= target:
                return "목표가 도달 (익절)"
            if stop and current_price >= stop:
                return "손절가 도달 (손절)"

        return ""

    def _close_position(self, exit_price: float, exit_time, reason: str):
        """포지션 청산"""
        if not self.position:
            return

        self.trade_counter += 1
        direction = self.position["direction"]
        entry_price = self.position["entry_price"]

        # 수익률 계산
        if direction == "long":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:  # short
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        # 수수료 반영 (업비트 0.05% x 2 = 0.1%)
        pnl_pct -= 0.1

        trade_amount = min(Config.TRADE_AMOUNT, self.balance)
        pnl_krw = trade_amount * (pnl_pct / 100)
        self.balance += pnl_krw
        self.balance_history.append(self.balance)

        trade = BacktestTrade(
            trade_id=self.trade_counter,
            direction=direction,
            entry_time=str(self.position["entry_time"]),
            exit_time=str(exit_time),
            entry_price=entry_price,
            exit_price=exit_price,
            target_price=self.position.get("target_price") or 0,
            stop_loss=self.position.get("stop_loss") or 0,
            pnl_pct=pnl_pct,
            pnl_krw=pnl_krw,
            exit_reason=reason,
            confidence=self.position.get("confidence", 0),
            confluence=self.position.get("confluence", 0),
            smc_reason=self.position.get("reason", "")[:100],
        )
        self.trades.append(trade)

        emoji = "+" if pnl_pct > 0 else ""
        logger.info(
            f"  ■ {direction.upper()} 청산 @ {exit_price:,.0f}원 "
            f"({emoji}{pnl_pct:.2f}%, {emoji}{pnl_krw:,.0f}원) "
            f"- {reason}"
        )
        logger.info(f"  잔고: {self.balance:,.0f}원")

        self.position = None

    def _build_result(self) -> BacktestResult:
        """백테스트 결과 집계"""
        result = BacktestResult(
            initial_balance=self.initial_balance,
            final_balance=self.balance,
            trades=[t.__dict__ for t in self.trades],
        )

        if not self.trades:
            return result

        result.total_trades = len(self.trades)
        result.long_trades = sum(1 for t in self.trades if t.direction == "long")
        result.short_trades = sum(1 for t in self.trades if t.direction == "short")
        result.winning_trades = sum(1 for t in self.trades if t.pnl_pct > 0)
        result.losing_trades = sum(1 for t in self.trades if t.pnl_pct <= 0)
        result.win_rate = result.winning_trades / result.total_trades * 100

        pnls = [t.pnl_pct for t in self.trades]
        result.total_pnl_pct = (self.balance - self.initial_balance) / self.initial_balance * 100
        result.total_pnl_krw = self.balance - self.initial_balance
        result.avg_pnl_per_trade = sum(pnls) / len(pnls)
        result.best_trade_pct = max(pnls)
        result.worst_trade_pct = min(pnls)

        wins = [t.pnl_pct for t in self.trades if t.pnl_pct > 0]
        losses = [t.pnl_pct for t in self.trades if t.pnl_pct <= 0]
        result.avg_win_pct = sum(wins) / len(wins) if wins else 0
        result.avg_loss_pct = sum(losses) / len(losses) if losses else 0

        # Profit Factor
        total_wins = sum(t.pnl_krw for t in self.trades if t.pnl_krw > 0)
        total_losses = abs(sum(t.pnl_krw for t in self.trades if t.pnl_krw < 0))
        result.profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # Max Drawdown
        peak = self.balance_history[0]
        max_dd = 0
        for bal in self.balance_history:
            if bal > peak:
                peak = bal
            dd = (peak - bal) / peak * 100
            max_dd = max(max_dd, dd)
        result.max_drawdown_pct = max_dd

        # Sharpe-like ratio (평균 수익 / 수익 표준편차)
        if len(pnls) > 1:
            import numpy as np
            mean_pnl = np.mean(pnls)
            std_pnl = np.std(pnls)
            result.sharpe_like = mean_pnl / std_pnl if std_pnl > 0 else 0
        else:
            result.sharpe_like = 0

        return result

    def _print_result(self, result: BacktestResult):
        """결과 출력"""
        logger.info("\n" + "=" * 60)
        logger.info("  SMC 백테스트 결과")
        logger.info("=" * 60)
        logger.info(f"  초기 자본:        {result.initial_balance:>15,.0f}원")
        logger.info(f"  최종 자본:        {result.final_balance:>15,.0f}원")
        logger.info(f"  총 수익률:        {result.total_pnl_pct:>14.2f}%")
        logger.info(f"  총 수익금:        {result.total_pnl_krw:>15,.0f}원")
        logger.info("-" * 60)
        logger.info(f"  총 거래:          {result.total_trades:>10}회")
        logger.info(f"    롱:             {result.long_trades:>10}회")
        logger.info(f"    숏:             {result.short_trades:>10}회")
        logger.info(f"  승리:             {result.winning_trades:>10}회")
        logger.info(f"  패배:             {result.losing_trades:>10}회")
        logger.info(f"  승률:             {result.win_rate:>10.1f}%")
        logger.info("-" * 60)
        logger.info(f"  평균 수익/거래:   {result.avg_pnl_per_trade:>10.2f}%")
        logger.info(f"  평균 승리:        {result.avg_win_pct:>10.2f}%")
        logger.info(f"  평균 패배:        {result.avg_loss_pct:>10.2f}%")
        logger.info(f"  최고 거래:        {result.best_trade_pct:>10.2f}%")
        logger.info(f"  최악 거래:        {result.worst_trade_pct:>10.2f}%")
        logger.info("-" * 60)
        logger.info(f"  Profit Factor:    {result.profit_factor:>10.2f}")
        logger.info(f"  최대 낙폭(MDD):   {result.max_drawdown_pct:>10.2f}%")
        logger.info(f"  Sharpe-like:      {result.sharpe_like:>10.2f}")
        logger.info("=" * 60)

        # 개별 거래 상세
        if self.trades:
            logger.info("\n  거래 상세:")
            logger.info("-" * 100)
            logger.info(
                f"  {'#':>3} {'방향':>4} {'진입가':>12} {'청산가':>12} "
                f"{'수익률':>8} {'수익금':>12} {'사유':>20}"
            )
            logger.info("-" * 100)
            for t in self.trades:
                emoji = "+" if t.pnl_pct > 0 else ""
                logger.info(
                    f"  {t.trade_id:>3} {t.direction:>5} "
                    f"{t.entry_price:>12,.0f} {t.exit_price:>12,.0f} "
                    f"{emoji}{t.pnl_pct:>7.2f}% {emoji}{t.pnl_krw:>11,.0f}원 "
                    f"{t.exit_reason[:20]:>20}"
                )

    def _save_result(self, result: BacktestResult):
        """결과를 JSON 파일로 저장"""
        os.makedirs("logs", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"logs/backtest_{timestamp}.json"

        data = {
            "timestamp": timestamp,
            "coin": self.coin,
            "initial_balance": result.initial_balance,
            "final_balance": result.final_balance,
            "total_pnl_pct": result.total_pnl_pct,
            "total_trades": result.total_trades,
            "long_trades": result.long_trades,
            "short_trades": result.short_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "max_drawdown_pct": result.max_drawdown_pct,
            "trades": result.trades,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"\n결과 저장: {filepath}")


def main():
    """백테스트 실행"""
    import argparse
    parser = argparse.ArgumentParser(description="SMC 백테스터")
    parser.add_argument("--coin", default=Config.COIN, help="코인 (예: KRW-BTC)")
    parser.add_argument("--days", type=int, default=3, help="백테스트 기간 (일)")
    parser.add_argument("--interval", type=int, default=12,
                        help="분석 간격 (캔들 수, 12=1시간마다)")
    parser.add_argument("--balance", type=float, default=1_000_000,
                        help="초기 자본 (원)")
    args = parser.parse_args()

    tester = SMCBacktester(coin=args.coin, initial_balance=args.balance)
    tester.run(days_back=args.days, analysis_interval_candles=args.interval)


if __name__ == "__main__":
    main()
