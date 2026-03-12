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

    # 트레일링 스탑 설정
    TRAILING_ACTIVATE_PCT = 0.4   # 목표가까지 40% 도달 시 트레일링 활성화
    TRAILING_STEP_PCT = 0.25      # 수익의 25% 지점에 손절선 이동 (75% 수익 보호)
    CONSECUTIVE_LOSS_COOLDOWN = 2  # N연패 후 1사이클 쿨다운
    MAX_STOP_LOSS_PCT = 1.5       # 1건당 최대 손절폭 % (레버리지 적용 전 기준)
    MIN_RR_RATIO = 2.0            # 최소 R:R 비율
    TREND_EMA_SHORT = 10          # 단기 EMA (추세 판단용)
    TREND_EMA_LONG = 30           # 장기 EMA (추세 판단용)

    def __init__(self, coin: str = None, initial_balance: float = 1_000_000,
                 leverage: int = 1):
        self.coin = coin or Config.COIN
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        # 선물 수수료: 메이커 0.02%, 테이커 0.04% (바이낸스 기준)
        # 현물 수수료: 업비트 0.05%
        self.fee_pct = 0.04 if leverage > 1 else 0.05  # 편도 수수료(%)
        self.analyzer = AIAnalyzer()
        self.trades: list[BacktestTrade] = []
        self.trade_counter = 0
        self.position = None
        self.balance_history = [initial_balance]
        self.api_call_delay = 3  # Claude API 호출 간격 (초)
        self.consecutive_losses = 0  # 연속 손실 카운터
        self.cooldown_remaining = 0  # 쿨다운 남은 사이클

    def run(self, days_back: int = 7, analysis_interval_candles: int = 10,
            offset_days: int = 0) -> BacktestResult:
        """백테스트 실행

        Args:
            days_back: 며칠간의 데이터로 테스트할지
            analysis_interval_candles: 몇 캔들마다 AI 분석할지 (비용 절약)
            offset_days: 오늘 기준 며칠 전부터 시작 (0=오늘까지, 14=2주 전까지)
        """
        mode = "선물" if self.leverage > 1 else "현물"
        period_desc = f"최근 {days_back}일" if offset_days == 0 else f"{offset_days+days_back}~{offset_days}일 전"
        logger.info("=" * 60)
        logger.info("SMC 백테스트 시작")
        logger.info(f"코인: {self.coin} ({mode} {self.leverage}x)")
        logger.info(f"초기 자본: {self.initial_balance:,.0f}원")
        logger.info(f"수수료: {self.fee_pct}% (편도)")
        logger.info(f"기간: {period_desc}")
        logger.info(f"최대 손절폭: {self.MAX_STOP_LOSS_PCT}% (레버리지 전)")
        logger.info(f"AI 분석 간격: {analysis_interval_candles}캔들마다")
        logger.info(f"손절/익절 체크: 매 캔들(5분)마다")
        logger.info("=" * 60)

        # 1. 히스토리 데이터 수집 (5분봉 기준 스캘핑)
        logger.info("히스토리 데이터 수집 중...")
        all_data = self._fetch_historical_data(days_back, offset_days)
        if all_data is None or all_data.empty:
            logger.error("데이터 수집 실패")
            return self._build_result()

        logger.info(f"총 {len(all_data)}개 캔들 수집 완료")
        logger.info(f"기간: {all_data.index[0]} ~ {all_data.index[-1]}")

        # 2. 매 캔들마다 손절/익절 체크, N캔들마다 AI 분석
        window_size = Config.CANDLE_COUNT  # 80캔들 윈도우
        total_analyses = (len(all_data) - window_size) // analysis_interval_candles
        analysis_count = 0
        candles_since_analysis = analysis_interval_candles  # 첫 캔들에서 바로 분석

        for current_idx in range(window_size, len(all_data)):
            current_candle = all_data.iloc[current_idx]
            current_price = current_candle["close"]
            current_high = current_candle["high"]
            current_low = current_candle["low"]
            current_time = all_data.index[current_idx]

            # ── 매 캔들: 포지션 손절/익절 체크 (고가/저가로 정밀 체크) ──
            if self.position:
                # 캔들 내 고가/저가로 체크 (실제 가격 움직임 반영)
                exit_reason = self._check_position_exit_candle(
                    current_high, current_low, current_price, current_time
                )
                if exit_reason:
                    self._close_position(
                        self._get_exit_price(exit_reason, current_high, current_low, current_price),
                        current_time, exit_reason
                    )

            # 청산된 포지션 체크 (레버리지 청산)
            if self.position and self.leverage > 1:
                self._check_liquidation(current_low if self.position["direction"] == "long" else current_high, current_time)

            # ── N캔들마다: AI 분석 ──
            candles_since_analysis += 1
            if candles_since_analysis < analysis_interval_candles:
                continue
            candles_since_analysis = 0
            analysis_count += 1

            logger.info(
                f"\n--- AI 분석 {analysis_count}/{total_analyses} "
                f"({current_time.strftime('%m/%d %H:%M')}) "
                f"가격: {current_price:,.0f}원 ---"
            )

            # 멀티 타임프레임 차트 생성
            tf_data = self._build_multi_timeframe(all_data, current_idx)
            if not tf_data:
                continue

            chart_image = generate_multi_timeframe_chart(tf_data)
            if not chart_image:
                continue

            # 추세 컨텍스트 계산
            trend_context = self._calc_trend_context(all_data, current_idx)
            ema_trend = self._get_ema_trend(all_data, current_idx)

            # Claude Vision SMC 분석
            context = (
                f"현재가: {current_price:,.0f}원, 코인: {self.coin}, "
                f"시각: {current_time.strftime('%Y-%m-%d %H:%M')}, "
                f"타임프레임: {', '.join(tf_data.keys())}, "
                f"모드: {mode} {self.leverage}x 레버리지, 롱/숏 모두 가능\n"
                f"{trend_context}\n"
                f"★ 중요: 추세 방향과 일치하는 매매만 하세요. "
                f"손절은 현재가 대비 0.3~1.5% 이내로 타이트하게 설정하세요."
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
                if self.cooldown_remaining > 0:
                    self.cooldown_remaining -= 1
                    logger.info(f"  → 쿨다운 중 ({self.cooldown_remaining}사이클 남음)")
                else:
                    self._evaluate_entry(analysis, current_price, current_time, ema_trend)

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

    def _fetch_historical_data(self, days_back: int, offset_days: int = 0) -> pd.DataFrame:
        """히스토리 5분봉 데이터 수집

        Args:
            days_back: 수집할 기간 (일)
            offset_days: 오늘 기준 며칠 전부터 끝나는지 (0=현재, 14=2주 전)
        """
        all_frames = []
        # pyupbit은 한 번에 최대 200개 캔들 반환
        candles_needed = days_back * 24 * 12  # 5분봉 = 하루 288개
        fetched = 0

        # offset_days > 0이면 과거 시점부터 시작
        to = None
        if offset_days > 0:
            to = datetime.now() - timedelta(days=offset_days)
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

    def _calc_trend_context(self, all_data: pd.DataFrame, current_idx: int) -> str:
        """EMA 기반 추세 컨텍스트 계산 (AI에 참고 정보로 전달)"""
        # 5분봉 데이터에서 EMA 계산
        lookback = max(self.TREND_EMA_LONG * 3, 200)
        start = max(0, current_idx - lookback)
        df = all_data.iloc[start:current_idx + 1].copy()

        if len(df) < self.TREND_EMA_LONG:
            return "추세 데이터 부족"

        close = df["close"]
        ema_short = close.ewm(span=self.TREND_EMA_SHORT, adjust=False).mean()
        ema_long = close.ewm(span=self.TREND_EMA_LONG, adjust=False).mean()

        current_price = close.iloc[-1]
        ema_s = ema_short.iloc[-1]
        ema_l = ema_long.iloc[-1]

        # 5분봉 추세
        if ema_s > ema_l and current_price > ema_s:
            trend_5m = "상승"
        elif ema_s < ema_l and current_price < ema_s:
            trend_5m = "하락"
        else:
            trend_5m = "횡보"

        # 1시간봉 추세 (리샘플링)
        df_1h = self._resample(df, "1h")
        if len(df_1h) >= self.TREND_EMA_LONG:
            close_1h = df_1h["close"]
            ema_s_1h = close_1h.ewm(span=self.TREND_EMA_SHORT, adjust=False).mean()
            ema_l_1h = close_1h.ewm(span=self.TREND_EMA_LONG, adjust=False).mean()
            price_1h = close_1h.iloc[-1]
            if ema_s_1h.iloc[-1] > ema_l_1h.iloc[-1] and price_1h > ema_s_1h.iloc[-1]:
                trend_1h = "상승"
            elif ema_s_1h.iloc[-1] < ema_l_1h.iloc[-1] and price_1h < ema_s_1h.iloc[-1]:
                trend_1h = "하락"
            else:
                trend_1h = "횡보"
        else:
            trend_1h = "판단불가"

        # 최근 가격 변화율
        if len(close) >= 12:
            change_1h = (current_price / close.iloc[-12] - 1) * 100
        else:
            change_1h = 0
        if len(close) >= 36:
            change_3h = (current_price / close.iloc[-36] - 1) * 100
        else:
            change_3h = 0

        return (
            f"[EMA 추세 참고] 5분봉: {trend_5m}, 1시간봉: {trend_1h} | "
            f"EMA{self.TREND_EMA_SHORT}: {ema_s:,.0f}, EMA{self.TREND_EMA_LONG}: {ema_l:,.0f} | "
            f"최근1시간 변동: {change_1h:+.2f}%, 최근3시간 변동: {change_3h:+.2f}%"
        )

    def _get_ema_trend(self, all_data: pd.DataFrame, current_idx: int) -> str:
        """30분봉 EMA 기반 추세 방향 반환 (안정적인 trend filter)"""
        lookback = max(self.TREND_EMA_LONG * 18, 400)  # 30분봉으로 충분한 데이터
        start = max(0, current_idx - lookback)
        df = all_data.iloc[start:current_idx + 1]

        # 30분봉으로 리샘플링하여 노이즈 제거
        df_30m = self._resample(df, "30min")
        if len(df_30m) < self.TREND_EMA_LONG:
            return "sideways"

        close = df_30m["close"]
        ema_short = close.ewm(span=self.TREND_EMA_SHORT, adjust=False).mean()
        ema_long = close.ewm(span=self.TREND_EMA_LONG, adjust=False).mean()
        price = close.iloc[-1]

        ema_s = ema_short.iloc[-1]
        ema_l = ema_long.iloc[-1]

        # EMA 간격이 충분해야 추세로 인정
        ema_gap_pct = abs(ema_s - ema_l) / ema_l * 100

        if ema_s > ema_l and price > ema_l and ema_gap_pct > 0.03:
            return "uptrend"
        elif ema_s < ema_l and price < ema_l and ema_gap_pct > 0.03:
            return "downtrend"
        return "sideways"

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

    def _evaluate_entry(self, analysis: dict, price: float, timestamp,
                         ema_trend: str = "sideways"):
        """신규 진입 평가"""
        decision = analysis.get("decision", "hold")
        confidence = analysis.get("confidence", 0.0)
        confluence = analysis.get("confluence_count", 0)

        if decision == "hold":
            return

        # ★ 추세 필터: EMA 추세와 반대 방향 진입 차단
        ai_trend = analysis.get("trend", "sideways")
        if decision == "buy" and ema_trend == "downtrend":
            logger.info(f"  → 패스 (추세 필터: EMA 하락 추세에서 롱 차단)")
            return
        if decision == "sell" and ema_trend == "uptrend":
            logger.info(f"  → 패스 (추세 필터: EMA 상승 추세에서 숏 차단)")
            return
        if ema_trend == "sideways":
            # 횡보 시 신뢰도 기준 강화
            if confidence < 0.80:
                logger.info(f"  → 패스 (횡보 구간: 신뢰도 {confidence:.0%} < 80% 필요)")
                return

        # ★ AI 추세 판단과 진입 방향 교차 검증
        if decision == "buy" and ai_trend == "downtrend":
            logger.info(f"  → 패스 (AI 추세 교차검증: AI가 하락 추세 판단했으나 롱 추천)")
            return
        if decision == "sell" and ai_trend == "uptrend":
            logger.info(f"  → 패스 (AI 추세 교차검증: AI가 상승 추세 판단했으나 숏 추천)")
            return
        if ai_trend == "sideways":
            logger.info(f"  → 패스 (AI가 횡보 추세 판단 — 진입 안함)")
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

        # 최대 손절폭 제한 적용
        if decision == "buy":
            raw_sl_pct = abs(price - stop_loss) / price * 100
        elif decision == "sell":
            raw_sl_pct = abs(stop_loss - price) / price * 100
        else:
            raw_sl_pct = 0

        if raw_sl_pct > self.MAX_STOP_LOSS_PCT:
            # 손절폭을 MAX_STOP_LOSS_PCT로 강제 축소
            old_sl = stop_loss
            if decision == "buy":
                stop_loss = price * (1 - self.MAX_STOP_LOSS_PCT / 100)
            elif decision == "sell":
                stop_loss = price * (1 + self.MAX_STOP_LOSS_PCT / 100)
            logger.info(
                f"  ✂ 손절폭 제한: {raw_sl_pct:.1f}% → {self.MAX_STOP_LOSS_PCT:.1f}% "
                f"(손절가 {old_sl:,.0f} → {stop_loss:,.0f}원)"
            )

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
                if rr < self.MIN_RR_RATIO:
                    logger.info(f"  → 패스 (R:R {rr:.1f}:1 < {self.MIN_RR_RATIO:.1f}:1)")
                    return
                logger.info(f"  R:R 비율: {rr:.1f}:1")

        # 진입
        self.position = {
            "direction": direction,
            "entry_price": price,
            "target_price": target_price,
            "stop_loss": stop_loss,
            "entry_time": timestamp,
            "confidence": confidence,
            "confluence": confluence,
            "reason": analysis.get("reason", ""),
            "best_price": price,         # 트레일링용 최고/최저가
            "trailing_active": False,    # 트레일링 활성화 여부
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

    def _check_position_exit_candle(self, high: float, low: float,
                                     close: float, current_time) -> str:
        """캔들의 고가/저가로 정밀 손절/익절 체크 (매 5분봉마다 호출)"""
        if not self.position:
            return ""

        direction = self.position["direction"]
        entry = self.position["entry_price"]
        target = self.position.get("target_price")
        stop = self.position.get("stop_loss")
        trailing_active = self.position.get("trailing_active", False)
        best_price = self.position.get("best_price", entry)

        if direction == "long":
            # 캔들 고가로 최고가 갱신
            if high > best_price:
                self.position["best_price"] = high
                best_price = high

            # 트레일링 활성화 체크
            if not trailing_active and target:
                activate_price = entry + (target - entry) * self.TRAILING_ACTIVATE_PCT
                if high >= activate_price:
                    trailing_active = True
                    self.position["trailing_active"] = True
                    self.position["stop_loss"] = entry
                    stop = entry
                    logger.info(
                        f"  ↑ 트레일링 활성화 (고가 {high:,.0f}원) "
                        f"(손절 → {stop:,.0f}원 본전)"
                    )

            # 트레일링 손절선 갱신
            if trailing_active:
                profit_from_entry = best_price - entry
                new_stop = entry + profit_from_entry * (1 - self.TRAILING_STEP_PCT)
                if new_stop > stop:
                    self.position["stop_loss"] = new_stop
                    stop = new_stop

            # 익절 체크 (고가가 목표 도달)
            if target and high >= target:
                return "목표가 도달 (익절)"
            # 손절 체크 (저가가 손절 도달)
            if stop and low <= stop:
                if trailing_active:
                    return "트레일링 스탑 (수익 확보)"
                return "손절가 도달 (손절)"

        elif direction == "short":
            # 캔들 저가로 최저가 갱신
            if low < best_price:
                self.position["best_price"] = low
                best_price = low

            # 트레일링 활성화
            if not trailing_active and target:
                activate_price = entry - (entry - target) * self.TRAILING_ACTIVATE_PCT
                if low <= activate_price:
                    trailing_active = True
                    self.position["trailing_active"] = True
                    self.position["stop_loss"] = entry
                    stop = entry
                    logger.info(
                        f"  ↓ 트레일링 활성화 (저가 {low:,.0f}원) "
                        f"(손절 → {stop:,.0f}원 본전)"
                    )

            # 트레일링 손절선 갱신
            if trailing_active:
                profit_from_entry = entry - best_price
                new_stop = entry - profit_from_entry * (1 - self.TRAILING_STEP_PCT)
                if new_stop < stop:
                    self.position["stop_loss"] = new_stop
                    stop = new_stop

            # 익절 (저가가 목표 도달)
            if target and low <= target:
                return "목표가 도달 (익절)"
            # 손절 (고가가 손절 도달)
            if stop and high >= stop:
                if trailing_active:
                    return "트레일링 스탑 (수익 확보)"
                return "손절가 도달 (손절)"

        return ""

    def _get_exit_price(self, reason: str, high: float, low: float, close: float) -> float:
        """청산 사유에 따른 실제 청산가 결정"""
        direction = self.position["direction"]
        target = self.position.get("target_price")
        stop = self.position.get("stop_loss")

        if "익절" in reason and target:
            return target  # 목표가 정확히 체결
        if "손절" in reason or "트레일링" in reason:
            if stop:
                return stop  # 손절가 정확히 체결
        return close

    def _check_liquidation(self, worst_price: float, current_time):
        """레버리지 강제 청산 체크"""
        if not self.position or self.leverage <= 1:
            return
        direction = self.position["direction"]
        entry = self.position["entry_price"]

        if direction == "long":
            pnl_pct = (worst_price - entry) / entry * self.leverage * 100
        else:
            pnl_pct = (entry - worst_price) / entry * self.leverage * 100

        # -90% 이상 손실 시 강제 청산 (마진 부족)
        if pnl_pct <= -90:
            logger.info(f"  !! 강제 청산 (레버리지 {self.leverage}x 마진콜)")
            self._close_position(worst_price, current_time, f"강제 청산 ({self.leverage}x 마진콜)")

    def _check_position_exit(self, current_price: float, current_time) -> str:
        """포지션 손절/익절 + 트레일링 스탑 체크"""
        if not self.position:
            return ""

        direction = self.position["direction"]
        entry = self.position["entry_price"]
        target = self.position.get("target_price")
        stop = self.position.get("stop_loss")
        trailing_active = self.position.get("trailing_active", False)
        best_price = self.position.get("best_price", entry)

        if direction == "long":
            # 최고가 갱신
            if current_price > best_price:
                best_price = current_price
                self.position["best_price"] = best_price

            # 트레일링 활성화 체크: 목표가까지 50% 도달
            if not trailing_active and target:
                activate_price = entry + (target - entry) * self.TRAILING_ACTIVATE_PCT
                if current_price >= activate_price:
                    trailing_active = True
                    self.position["trailing_active"] = True
                    # 손절선을 진입가로 이동 (본전 보장)
                    self.position["stop_loss"] = entry
                    stop = entry
                    logger.info(
                        f"  ↑ 트레일링 활성화 @ {current_price:,.0f}원 "
                        f"(손절 → {stop:,.0f}원 본전)"
                    )

            # 트레일링 중: 최고가 기준으로 손절선 끌어올림
            if trailing_active:
                profit_from_entry = best_price - entry
                new_stop = entry + profit_from_entry * (1 - self.TRAILING_STEP_PCT)
                if new_stop > stop:
                    self.position["stop_loss"] = new_stop
                    stop = new_stop

            # 완전 익절 (목표가 도달)
            if target and current_price >= target:
                return "목표가 도달 (익절)"
            # 손절 (트레일링 포함)
            if stop and current_price <= stop:
                if trailing_active:
                    return f"트레일링 스탑 (수익 확보)"
                return "손절가 도달 (손절)"

        elif direction == "short":
            # 최저가 갱신
            if current_price < best_price:
                best_price = current_price
                self.position["best_price"] = best_price

            # 트레일링 활성화 체크
            if not trailing_active and target:
                activate_price = entry - (entry - target) * self.TRAILING_ACTIVATE_PCT
                if current_price <= activate_price:
                    trailing_active = True
                    self.position["trailing_active"] = True
                    self.position["stop_loss"] = entry
                    stop = entry
                    logger.info(
                        f"  ↓ 트레일링 활성화 @ {current_price:,.0f}원 "
                        f"(손절 → {stop:,.0f}원 본전)"
                    )

            # 트레일링 중: 최저가 기준으로 손절선 끌어내림
            if trailing_active:
                profit_from_entry = entry - best_price
                new_stop = entry - profit_from_entry * (1 - self.TRAILING_STEP_PCT)
                if new_stop < stop:
                    self.position["stop_loss"] = new_stop
                    stop = new_stop

            # 완전 익절
            if target and current_price <= target:
                return "목표가 도달 (익절)"
            # 손절
            if stop and current_price >= stop:
                if trailing_active:
                    return f"트레일링 스탑 (수익 확보)"
                return "손절가 도달 (손절)"

        return ""

    def _close_position(self, exit_price: float, exit_time, reason: str):
        """포지션 청산"""
        if not self.position:
            return

        self.trade_counter += 1
        direction = self.position["direction"]
        entry_price = self.position["entry_price"]

        # 수익률 계산 (레버리지 적용)
        if direction == "long":
            pnl_pct = (exit_price - entry_price) / entry_price * 100 * self.leverage
        else:  # short
            pnl_pct = (entry_price - exit_price) / entry_price * 100 * self.leverage

        # 수수료 반영 (편도 × 2 × 레버리지)
        fee_total = self.fee_pct * 2 * self.leverage
        pnl_pct -= fee_total

        trade_amount = self.balance  # 자본금 전액 투자
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

        # 연속 손실 추적 & 쿨다운
        if pnl_pct <= 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.CONSECUTIVE_LOSS_COOLDOWN:
                self.cooldown_remaining = 1  # 1사이클 쉼
                logger.info(f"  ⚠ {self.consecutive_losses}연패 → 1사이클 쿨다운")
        else:
            self.consecutive_losses = 0

        trailing_tag = " [트레일링]" if self.position.get("trailing_active") else ""
        emoji = "+" if pnl_pct > 0 else ""
        logger.info(
            f"  ■ {direction.upper()} 청산 @ {exit_price:,.0f}원 "
            f"({emoji}{pnl_pct:.2f}%, {emoji}{pnl_krw:,.0f}원) "
            f"- {reason}{trailing_tag}"
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
    parser.add_argument("--leverage", type=int, default=1,
                        help="레버리지 배수 (1=현물, 10=선물10x 등)")
    parser.add_argument("--offset", type=int, default=0,
                        help="기간 오프셋 (일, 0=현재부터, 14=2주 전부터)")
    parser.add_argument("--max-sl", type=float, default=None,
                        help="최대 손절폭 %% (레버리지 전 기준)")
    args = parser.parse_args()

    tester = SMCBacktester(coin=args.coin, initial_balance=args.balance,
                           leverage=args.leverage)
    if args.max_sl is not None:
        tester.MAX_STOP_LOSS_PCT = args.max_sl
    tester.run(days_back=args.days, analysis_interval_candles=args.interval,
               offset_days=args.offset)


if __name__ == "__main__":
    main()
