"""ICT 컨텍스트 전략 - 실제 트레이더처럼 차트를 종합적으로 읽는 전략

기존 전략들의 한계:
- 각 전략이 독립적으로 yes/no를 내고 투표하는 방식
- 시장 맥락(context) 없이 기계적으로 시그널 생성

이 전략의 접근:
1단계: 큰 그림 파악 (HTF 구조 분석)
  - 시장이 상승인가 하락인가 횡보인가?
  - 주요 스윙 고/저점은 어디인가?
  - BOS(Break of Structure) / CHoCH(Change of Character) 발생했나?

2단계: 프리미엄/디스카운트 존 판단
  - 전체 스윙 범위의 어디에 가격이 있는가?
  - 프리미엄(상단 50%) → 매도만 고려
  - 디스카운트(하단 50%) → 매수만 고려

3단계: POI(Point of Interest) 컨텍스트 평가
  - OB/FVG가 있어도 HTF 방향에 맞지 않으면 무시
  - OB+FVG가 같은 가격대에 겹치면 강력한 POI

4단계: 진입 확인
  - MSS + 캔들 패턴 + 모멘텀 확인
  - "이 차트를 보니 여기서 사면/팔면 먹겠다" 종합 판단

5단계: 멀티 타임프레임
  - 상위 TF(4h 시뮬레이션)로 방향, 하위 TF(1h)로 진입
"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class ICTContextStrategy(BaseStrategy):
    """실제 트레이더처럼 차트를 종합적으로 읽는 ICT 전략"""

    def __init__(self, config: dict = None):
        super().__init__("ICT Context", config)
        self.htf_period = self.config.get("htf_period", 4)  # 4개 1h = 4h 시뮬레이션
        self.swing_lookback = self.config.get("swing_lookback", 5)
        self.structure_depth = self.config.get("structure_depth", 100)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.structure_depth:
            return None

        current_price = candles[-1].close

        # ═══════════════════════════════════════════════════
        # 1단계: 큰 그림 - HTF 구조 분석
        # ═══════════════════════════════════════════════════
        htf_candles = self._build_htf_candles(candles)
        if len(htf_candles) < 30:
            return None

        htf_bias = self._analyze_market_structure(htf_candles)
        # htf_bias: "bullish", "bearish", "ranging"

        # 현재 TF 구조도 분석
        ltf_bias = self._analyze_market_structure(candles[-60:])

        # ═══════════════════════════════════════════════════
        # 2단계: 프리미엄/디스카운트 존
        # ═══════════════════════════════════════════════════
        swing_range = self._get_swing_range(htf_candles[-30:])
        zone = self._classify_zone(current_price, swing_range)
        # zone: "premium", "discount", "equilibrium"

        # ═══════════════════════════════════════════════════
        # 방향 결정: HTF 방향 + 존 위치로 "무엇을 할 수 있는가" 판단
        # ═══════════════════════════════════════════════════
        can_buy = False
        can_sell = False

        if htf_bias == "bullish":
            # 상승장: 디스카운트 존에서만 매수
            if zone in ("discount", "equilibrium"):
                can_buy = True
            # 상승장에서도 프리미엄 극단이면 단기 매도 가능
            if zone == "premium" and ltf_bias == "bearish":
                can_sell = True
        elif htf_bias == "bearish":
            # 하락장: 프리미엄 존에서만 매도
            if zone in ("premium", "equilibrium"):
                can_sell = True
            # 하락장에서도 디스카운트 극단이면 단기 매수 가능
            if zone == "discount" and ltf_bias == "bullish":
                can_buy = True
        else:  # ranging
            # 횡보장: 존에 따라 양방향
            if zone == "discount":
                can_buy = True
            elif zone == "premium":
                can_sell = True
            else:
                # 이퀄리브리엄에서는 LTF 방향 따라감
                if ltf_bias == "bullish":
                    can_buy = True
                elif ltf_bias == "bearish":
                    can_sell = True

        if not can_buy and not can_sell:
            return self._hold(current_price)

        # ═══════════════════════════════════════════════════
        # 3단계: POI (Point of Interest) 탐색
        # ═══════════════════════════════════════════════════
        recent = candles[-80:]
        atr_vals = self.atr(
            [c.high for c in recent], [c.low for c in recent],
            [c.close for c in recent], period=14
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        # OB 탐색
        bullish_obs = self._find_order_blocks(recent, current_atr, "bullish") if can_buy else []
        bearish_obs = self._find_order_blocks(recent, current_atr, "bearish") if can_sell else []

        # FVG 탐색
        bullish_fvgs = self._find_fvgs(recent, current_atr, "bullish") if can_buy else []
        bearish_fvgs = self._find_fvgs(recent, current_atr, "bearish") if can_sell else []

        # ═══════════════════════════════════════════════════
        # 4단계: 종합 판단 - "이 차트 보니 여기서 먹겠다"
        # ═══════════════════════════════════════════════════
        buy_score = 0.0
        sell_score = 0.0
        buy_meta = {}
        sell_meta = {}

        if can_buy:
            buy_score, buy_meta = self._evaluate_entry(
                candles, current_price, current_atr, "buy",
                bullish_obs, bullish_fvgs, htf_bias, ltf_bias, zone, swing_range
            )

        if can_sell:
            sell_score, sell_meta = self._evaluate_entry(
                candles, current_price, current_atr, "sell",
                bearish_obs, bearish_fvgs, htf_bias, ltf_bias, zone, swing_range
            )

        # 최종 결정
        min_score = 0.45  # 최소 진입 기준

        if buy_score >= min_score and buy_score > sell_score:
            return Signal(
                type=SignalType.BUY,
                strength=min(buy_score, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata=buy_meta,
            )
        elif sell_score >= min_score and sell_score > buy_score:
            return Signal(
                type=SignalType.SELL,
                strength=min(sell_score, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata=sell_meta,
            )

        return self._hold(current_price)

    # ═══════════════════════════════════════════════════════════
    # 1단계 헬퍼: 시장 구조 분석
    # ═══════════════════════════════════════════════════════════

    def _build_htf_candles(self, candles: list[Candle]) -> list[Candle]:
        """1h 캔들을 4h 캔들로 합성 (멀티 타임프레임 시뮬레이션)"""
        htf = []
        n = self.htf_period
        for i in range(0, len(candles) - n + 1, n):
            group = candles[i:i + n]
            htf.append(Candle(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            ))
        return htf

    def _find_swing_points(self, candles: list[Candle]) -> tuple[list, list]:
        """스윙 고/저점 탐색. 반환: (swing_highs, swing_lows) 각각 (index, price) 리스트"""
        n = self.swing_lookback
        highs = []
        lows = []
        for i in range(n, len(candles) - n):
            is_high = all(candles[i].high >= candles[i + d].high for d in range(-n, n + 1) if d != 0)
            is_low = all(candles[i].low <= candles[i + d].low for d in range(-n, n + 1) if d != 0)
            if is_high:
                highs.append((i, candles[i].high))
            if is_low:
                lows.append((i, candles[i].low))
        return highs, lows

    def _analyze_market_structure(self, candles: list[Candle]) -> str:
        """시장 구조 분석: bullish / bearish / ranging"""
        swing_highs, swing_lows = self._find_swing_points(candles)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            # 스윙 부족 → EMA로 판단
            closes = [c.close for c in candles]
            ema20 = self.ema(closes, 20)
            ema50 = self.ema(closes, 50)
            if ema20[-1] > ema50[-1] and closes[-1] > ema20[-1]:
                return "bullish"
            elif ema20[-1] < ema50[-1] and closes[-1] < ema20[-1]:
                return "bearish"
            return "ranging"

        # HH/HL → bullish, LL/LH → bearish
        last_highs = [p for _, p in swing_highs[-3:]]
        last_lows = [p for _, p in swing_lows[-3:]]

        higher_highs = all(last_highs[i] > last_highs[i - 1] for i in range(1, len(last_highs)))
        higher_lows = all(last_lows[i] > last_lows[i - 1] for i in range(1, len(last_lows)))
        lower_highs = all(last_highs[i] < last_highs[i - 1] for i in range(1, len(last_highs)))
        lower_lows = all(last_lows[i] < last_lows[i - 1] for i in range(1, len(last_lows)))

        bullish_score = 0
        bearish_score = 0

        if higher_highs:
            bullish_score += 1
        if higher_lows:
            bullish_score += 1
        if lower_highs:
            bearish_score += 1
        if lower_lows:
            bearish_score += 1

        # CHoCH 감지: 하락 구조에서 갑자기 HH → 전환 신호
        if len(last_highs) >= 3:
            if last_highs[-3] > last_highs[-2] and last_highs[-1] > last_highs[-2]:
                bullish_score += 0.5  # CHoCH bullish
            if last_highs[-3] < last_highs[-2] and last_highs[-1] < last_highs[-2]:
                bearish_score += 0.5  # CHoCH bearish

        # EMA 방향도 반영
        closes = [c.close for c in candles]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, 50)
        if ema20[-1] > ema50[-1]:
            bullish_score += 0.5
        elif ema20[-1] < ema50[-1]:
            bearish_score += 0.5

        if bullish_score >= 2 and bullish_score > bearish_score:
            return "bullish"
        elif bearish_score >= 2 and bearish_score > bullish_score:
            return "bearish"
        return "ranging"

    # ═══════════════════════════════════════════════════════════
    # 2단계 헬퍼: 프리미엄/디스카운트 존
    # ═══════════════════════════════════════════════════════════

    def _get_swing_range(self, candles: list[Candle]) -> dict:
        """최근 주요 스윙 범위 파악"""
        swing_highs, swing_lows = self._find_swing_points(candles)

        if swing_highs and swing_lows:
            range_high = max(p for _, p in swing_highs)
            range_low = min(p for _, p in swing_lows)
        else:
            range_high = max(c.high for c in candles)
            range_low = min(c.low for c in candles)

        equilibrium = (range_high + range_low) / 2

        return {
            "high": range_high,
            "low": range_low,
            "equilibrium": equilibrium,
            "range": range_high - range_low,
        }

    def _classify_zone(self, price: float, swing_range: dict) -> str:
        """현재 가격이 프리미엄/디스카운트/이퀄리브리엄 중 어디인지"""
        if swing_range["range"] == 0:
            return "equilibrium"

        position = (price - swing_range["low"]) / swing_range["range"]

        if position >= 0.7:
            return "premium"
        elif position <= 0.3:
            return "discount"
        else:
            return "equilibrium"

    # ═══════════════════════════════════════════════════════════
    # 3단계 헬퍼: POI 탐색 (컨텍스트 기반)
    # ═══════════════════════════════════════════════════════════

    def _find_order_blocks(self, candles: list[Candle], atr: float, direction: str) -> list[dict]:
        """오더블럭 탐색 (컨텍스트 포함)"""
        obs = []
        for i in range(3, len(candles) - 1):
            if direction == "bullish":
                # 음봉 뒤에 강한 양봉 임펄스
                if candles[i].close < candles[i].open:  # 음봉
                    # 다음 1~3개 캔들의 임펄스 체크
                    impulse = 0
                    impulse_high = candles[i].high
                    for j in range(1, min(4, len(candles) - i)):
                        impulse += candles[i + j].close - candles[i + j].open
                        impulse_high = max(impulse_high, candles[i + j].high)

                    if impulse > atr * 0.8:
                        # BOS 체크: 이전 스윙 고점 돌파
                        prev_high = max(c.high for c in candles[max(0, i - 15):i])
                        if impulse_high > prev_high:
                            # 미티게이션 체크
                            ob_high = max(candles[i].open, candles[i].close)
                            ob_low = min(candles[i].open, candles[i].close)
                            mitigated = any(c.low < ob_low for c in candles[i + 1:])
                            if not mitigated:
                                obs.append({
                                    "high": ob_high, "low": ob_low,
                                    "age": len(candles) - 1 - i,
                                    "impulse": impulse / atr,
                                    "idx": i,
                                })
            else:  # bearish
                if candles[i].close > candles[i].open:  # 양봉
                    impulse = 0
                    impulse_low = candles[i].low
                    for j in range(1, min(4, len(candles) - i)):
                        impulse += candles[i + j].open - candles[i + j].close
                        impulse_low = min(impulse_low, candles[i + j].low)

                    if impulse > atr * 0.8:
                        prev_low = min(c.low for c in candles[max(0, i - 15):i])
                        if impulse_low < prev_low:
                            ob_high = max(candles[i].open, candles[i].close)
                            ob_low = min(candles[i].open, candles[i].close)
                            mitigated = any(c.high > ob_high for c in candles[i + 1:])
                            if not mitigated:
                                obs.append({
                                    "high": ob_high, "low": ob_low,
                                    "age": len(candles) - 1 - i,
                                    "impulse": impulse / atr,
                                    "idx": i,
                                })
        return obs

    def _find_fvgs(self, candles: list[Candle], atr: float, direction: str) -> list[dict]:
        """FVG 탐색 (컨텍스트 포함)"""
        fvgs = []
        for i in range(2, len(candles)):
            if direction == "bullish":
                gap_low = candles[i - 2].high
                gap_high = candles[i].low
                if gap_high > gap_low and (gap_high - gap_low) > atr * 0.2:
                    if candles[i - 1].close > candles[i - 1].open:  # 중간 양봉
                        # 아직 안 채워졌는지 체크
                        filled = any(c.low <= gap_low for c in candles[i:])
                        if not filled:
                            fvgs.append({
                                "high": gap_high, "low": gap_low,
                                "ce": (gap_high + gap_low) / 2,
                                "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr,
                            })
            else:  # bearish
                gap_high = candles[i - 2].low
                gap_low = candles[i].high
                if gap_high > gap_low and (gap_high - gap_low) > atr * 0.2:
                    if candles[i - 1].close < candles[i - 1].open:  # 중간 음봉
                        filled = any(c.high >= gap_high for c in candles[i:])
                        if not filled:
                            fvgs.append({
                                "high": gap_high, "low": gap_low,
                                "ce": (gap_high + gap_low) / 2,
                                "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr,
                            })
        return fvgs

    # ═══════════════════════════════════════════════════════════
    # 4단계: 종합 판단 - "이 차트 보니 여기서 먹겠다"
    # ═══════════════════════════════════════════════════════════

    def _evaluate_entry(self, candles: list[Candle], price: float, atr: float,
                        direction: str, obs: list, fvgs: list,
                        htf_bias: str, ltf_bias: str, zone: str,
                        swing_range: dict) -> tuple[float, dict]:
        """종합 진입 평가. '차트를 보고 판단하는' 로직"""
        score = 0.0
        meta = {"direction": direction, "htf_bias": htf_bias, "zone": zone, "reasons": []}

        is_buy = direction == "buy"
        aligned_bias = "bullish" if is_buy else "bearish"

        # ── (1) HTF 방향 일치 보너스 ──
        if htf_bias == aligned_bias:
            score += 0.15
            meta["reasons"].append("HTF 방향 일치")

        # ── (2) LTF 방향 일치 보너스 ──
        if ltf_bias == aligned_bias:
            score += 0.10
            meta["reasons"].append("LTF 방향 일치")

        # ── (3) 존 적합성 ──
        if is_buy and zone == "discount":
            score += 0.15
            meta["reasons"].append("디스카운트 존 매수")
        elif not is_buy and zone == "premium":
            score += 0.15
            meta["reasons"].append("프리미엄 존 매도")
        elif zone == "equilibrium":
            score += 0.05  # 약간의 보너스

        # ── (4) OB에 가격이 진입 ──
        ob_hit = False
        for ob in obs:
            if ob["low"] <= price <= ob["high"]:
                ob_hit = True
                freshness = max(0, (30 - ob["age"]) / 30)
                ob_score = 0.15 + freshness * 0.1 + min(ob["impulse"] * 0.05, 0.1)
                score += ob_score
                meta["reasons"].append(f"OB 진입 (age:{ob['age']}, imp:{ob['impulse']:.1f})")
                meta["ob"] = ob
                break

        # ── (5) FVG에 가격이 진입 ──
        fvg_hit = False
        for fvg in fvgs:
            if fvg["low"] <= price <= fvg["high"]:
                fvg_hit = True
                fvg_score = 0.12 + min(fvg["size"] * 0.05, 0.08)
                score += fvg_score
                meta["reasons"].append(f"FVG 진입 (size:{fvg['size']:.1f})")
                meta["fvg"] = fvg
                break

        # ── (6) OB+FVG 겹침 보너스 (같은 가격대에 둘 다 있으면 매우 강력) ──
        if ob_hit and fvg_hit:
            score += 0.15
            meta["reasons"].append("OB+FVG 컨플루언스")

        # ── (7) MSS (Market Structure Shift) 확인 ──
        if len(candles) >= 3:
            if is_buy:
                # 하락하다 양봉 전환
                if candles[-2].close < candles[-2].open and candles[-1].close > candles[-1].open:
                    if candles[-1].close > candles[-2].high:
                        score += 0.10
                        meta["reasons"].append("MSS (약→강 전환)")
            else:
                # 상승하다 음봉 전환
                if candles[-2].close > candles[-2].open and candles[-1].close < candles[-1].open:
                    if candles[-1].close < candles[-2].low:
                        score += 0.10
                        meta["reasons"].append("MSS (강→약 전환)")

        # ── (8) 모멘텀 확인 ──
        recent_5 = candles[-5:]
        if is_buy:
            bullish_count = sum(1 for c in recent_5 if c.close > c.open)
            if bullish_count >= 3:
                score += 0.05
                meta["reasons"].append("양봉 모멘텀")
        else:
            bearish_count = sum(1 for c in recent_5 if c.close < c.open)
            if bearish_count >= 3:
                score += 0.05
                meta["reasons"].append("음봉 모멘텀")

        # ── (9) EMA 정렬 확인 ──
        closes = [c.close for c in candles[-50:]]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, 50)
        if is_buy and ema20[-1] > ema50[-1]:
            score += 0.05
            meta["reasons"].append("EMA 정렬 (20>50)")
        elif not is_buy and ema20[-1] < ema50[-1]:
            score += 0.05
            meta["reasons"].append("EMA 정렬 (20<50)")

        # ── (10) 유동성 스윕 감지 (Fakeout) ──
        if len(candles) >= 3:
            swing_highs, swing_lows = self._find_swing_points(candles[-30:])
            if is_buy and swing_lows:
                nearest_low = min(p for _, p in swing_lows[-3:]) if swing_lows else None
                if nearest_low and candles[-1].low < nearest_low and candles[-1].close > nearest_low:
                    wick = candles[-1].close - candles[-1].low
                    body = abs(candles[-1].close - candles[-1].open)
                    if body > 0 and wick / (candles[-1].high - candles[-1].low + 1e-10) > 0.5:
                        score += 0.12
                        meta["reasons"].append("유동성 스윕 후 반전")
            elif not is_buy and swing_highs:
                nearest_high = max(p for _, p in swing_highs[-3:]) if swing_highs else None
                if nearest_high and candles[-1].high > nearest_high and candles[-1].close < nearest_high:
                    wick = candles[-1].high - candles[-1].close
                    if (candles[-1].high - candles[-1].low) > 0:
                        if wick / (candles[-1].high - candles[-1].low) > 0.5:
                            score += 0.12
                            meta["reasons"].append("유동성 스윕 후 반전")

        # ── (11) 볼륨 확인 ──
        if len(candles) >= 20:
            avg_vol = sum(c.volume for c in candles[-20:]) / 20
            if avg_vol > 0 and candles[-1].volume > avg_vol * 1.3:
                score += 0.05
                meta["reasons"].append("볼륨 증가")

        # POI에 하나도 안 걸리면 진입 자격 없음
        if not ob_hit and not fvg_hit:
            # OB/FVG 없이는 유동성 스윕이 있어야 최소 진입 가능
            if "유동성 스윕" not in str(meta.get("reasons", [])):
                score *= 0.3  # 대폭 감점

        meta["score"] = round(score, 3)
        return score, meta

    def _hold(self, price: float) -> Signal:
        return Signal(
            type=SignalType.HOLD, strength=0,
            strategy_name=self.name, price=price,
            timestamp=datetime.now(),
        )
