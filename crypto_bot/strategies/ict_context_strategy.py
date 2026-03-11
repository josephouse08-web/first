"""ICT 컨텍스트 전략 v3 - 품질 유지 + 거래 빈도 개선

v1: 15거래/6개월 (너무 적음) - 평균 +3.0%
v2: 249거래/6개월 (기준 낮춤 → 질 저하) - 평균 -12.5%
v3: 목표 ~150거래/6개월 (하루 ~0.8회)
  - 진입 기준은 v1 수준으로 유지
  - 셋업 감지 범위를 확대 (더 많은 POI 타입)
  - EMA 바운스, RSI 극단, 캔들 패턴을 추가 POI로 인정
  - 방향 필터는 유연하게, 진입 품질은 엄격하게
"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class ICTContextStrategy(BaseStrategy):

    def __init__(self, config: dict = None):
        super().__init__("ICT Context", config)
        self.htf_period = self.config.get("htf_period", 4)
        self.swing_lookback = self.config.get("swing_lookback", 3)
        self.structure_depth = self.config.get("structure_depth", 80)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.structure_depth:
            return None

        current_price = candles[-1].close
        prev_candle = candles[-2]

        # ═══════════════════════════════════════════════════
        # 1단계: 큰 그림 - HTF/LTF 구조
        # ═══════════════════════════════════════════════════
        htf_candles = self._build_htf_candles(candles)
        if len(htf_candles) < 20:
            return None

        htf_bias = self._analyze_market_structure(htf_candles)
        ltf_bias = self._analyze_market_structure(candles[-40:])

        # ═══════════════════════════════════════════════════
        # 2단계: 프리미엄/디스카운트
        # ═══════════════════════════════════════════════════
        swing_range = self._get_swing_range(htf_candles[-20:])
        zone = self._classify_zone(current_price, swing_range)

        # ═══════════════════════════════════════════════════
        # 방향 결정
        # ═══════════════════════════════════════════════════
        can_buy = False
        can_sell = False

        if htf_bias == "bullish":
            can_buy = True
            if zone == "premium" and ltf_bias != "bullish":
                can_sell = True
        elif htf_bias == "bearish":
            can_sell = True
            if zone == "discount" and ltf_bias != "bearish":
                can_buy = True
        else:  # ranging
            can_buy = True
            can_sell = True

        if not can_buy and not can_sell:
            return self._hold(current_price)

        # ═══════════════════════════════════════════════════
        # 3단계: 기술적 컨텍스트 수집
        # ═══════════════════════════════════════════════════
        recent = candles[-60:]
        atr_vals = self.atr(
            [c.high for c in recent], [c.low for c in recent],
            [c.close for c in recent], period=14
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        closes = [c.close for c in candles[-60:]]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, min(50, len(closes)))
        rsi_vals = self.rsi(closes, 14)

        # OB/FVG 탐색
        bullish_obs = self._find_order_blocks(recent, current_atr, "bullish") if can_buy else []
        bearish_obs = self._find_order_blocks(recent, current_atr, "bearish") if can_sell else []
        bullish_fvgs = self._find_fvgs(recent, current_atr, "bullish") if can_buy else []
        bearish_fvgs = self._find_fvgs(recent, current_atr, "bearish") if can_sell else []

        # 스윙 포인트
        swing_highs, swing_lows = self._find_swing_points(candles[-40:])

        # ═══════════════════════════════════════════════════
        # 4단계: 종합 판단
        # ═══════════════════════════════════════════════════
        buy_score, buy_meta = (0.0, {})
        sell_score, sell_meta = (0.0, {})

        ctx = {
            "ema20": ema20, "ema50": ema50, "rsi": rsi_vals,
            "atr": current_atr, "swing_highs": swing_highs, "swing_lows": swing_lows,
            "htf_bias": htf_bias, "ltf_bias": ltf_bias, "zone": zone,
        }

        if can_buy:
            buy_score, buy_meta = self._evaluate_entry(
                candles, current_price, "buy", bullish_obs, bullish_fvgs, ctx
            )
        if can_sell:
            sell_score, sell_meta = self._evaluate_entry(
                candles, current_price, "sell", bearish_obs, bearish_fvgs, ctx
            )

        min_score = 0.40

        if buy_score >= min_score and buy_score > sell_score:
            return Signal(
                type=SignalType.BUY, strength=min(buy_score, 1.0),
                strategy_name=self.name, price=current_price,
                timestamp=datetime.now(), metadata=buy_meta,
            )
        elif sell_score >= min_score and sell_score > buy_score:
            return Signal(
                type=SignalType.SELL, strength=min(sell_score, 1.0),
                strategy_name=self.name, price=current_price,
                timestamp=datetime.now(), metadata=sell_meta,
            )

        return self._hold(current_price)

    # ═══════════════════════════════════════════════════════════
    # 평가 엔진
    # ═══════════════════════════════════════════════════════════

    def _evaluate_entry(self, candles: list[Candle], price: float,
                        direction: str, obs: list, fvgs: list,
                        ctx: dict) -> tuple[float, dict]:
        score = 0.0
        reasons = []
        is_buy = direction == "buy"
        aligned_bias = "bullish" if is_buy else "bearish"

        htf_bias = ctx["htf_bias"]
        ltf_bias = ctx["ltf_bias"]
        zone = ctx["zone"]
        atr = ctx["atr"]
        ema20 = ctx["ema20"]
        ema50 = ctx["ema50"]
        rsi = ctx["rsi"]

        # ━━━ A. 컨텍스트 점수 (최대 ~0.25) ━━━

        # (A1) HTF 방향 일치
        if htf_bias == aligned_bias:
            score += 0.10
            reasons.append("HTF일치")

        # (A2) LTF 방향 일치
        if ltf_bias == aligned_bias:
            score += 0.07
            reasons.append("LTF일치")

        # (A3) 존 적합성
        if (is_buy and zone == "discount") or (not is_buy and zone == "premium"):
            score += 0.08
            reasons.append("유리한 존")
        elif zone == "equilibrium":
            score += 0.03

        # ━━━ B. POI 점수 - 여러 타입 (최대 ~0.30) ━━━
        poi_found = False

        # (B1) OB 진입
        for ob in obs:
            if ob["low"] <= price <= ob["high"]:
                poi_found = True
                freshness = max(0, (35 - ob["age"]) / 35)
                s = 0.12 + freshness * 0.06
                if ob.get("has_bos"):
                    s += 0.04
                score += s
                reasons.append(f"OB진입(age:{ob['age']})")
                break

        # (B2) FVG 진입
        for fvg in fvgs:
            if fvg["low"] <= price <= fvg["high"]:
                poi_found = True
                s = 0.10 + min(fvg["size"] * 0.04, 0.06)
                score += s
                reasons.append("FVG진입")
                break

        # (B3) EMA 바운스 (새 POI 타입)
        if len(ema20) >= 2:
            ema20_val = ema20[-1]
            ema_dist = abs(price - ema20_val) / atr
            if ema_dist < 0.3:  # EMA20에 근접
                if is_buy and price >= ema20_val and candles[-1].low <= ema20_val * 1.002:
                    poi_found = True
                    score += 0.10
                    reasons.append("EMA20 바운스")
                elif not is_buy and price <= ema20_val and candles[-1].high >= ema20_val * 0.998:
                    poi_found = True
                    score += 0.10
                    reasons.append("EMA20 바운스")

        # (B4) 지지/저항 반전
        swing_lows = ctx["swing_lows"]
        swing_highs = ctx["swing_highs"]
        if is_buy and swing_lows:
            for _, sl_price in swing_lows[-5:]:
                if abs(price - sl_price) < atr * 0.5:
                    poi_found = True
                    score += 0.08
                    reasons.append("지지선 반전")
                    break
        elif not is_buy and swing_highs:
            for _, sh_price in swing_highs[-5:]:
                if abs(price - sh_price) < atr * 0.5:
                    poi_found = True
                    score += 0.08
                    reasons.append("저항선 반전")
                    break

        # (B5) 유동성 스윕
        if is_buy and swing_lows:
            nearest_low = min(p for _, p in swing_lows[-3:]) if len(swing_lows) >= 1 else None
            if nearest_low and candles[-1].low < nearest_low and candles[-1].close > nearest_low:
                poi_found = True
                score += 0.12
                reasons.append("유동성 스윕")
        elif not is_buy and swing_highs:
            nearest_high = max(p for _, p in swing_highs[-3:]) if len(swing_highs) >= 1 else None
            if nearest_high and candles[-1].high > nearest_high and candles[-1].close < nearest_high:
                poi_found = True
                score += 0.12
                reasons.append("유동성 스윕")

        # ━━━ C. 확인 점수 (최대 ~0.25) ━━━

        # (C1) 캔들 패턴 확인
        if is_buy:
            # 양봉 전환
            if candles[-1].close > candles[-1].open:
                if candles[-2].close < candles[-2].open:
                    score += 0.06
                    reasons.append("양봉전환")
                # 양봉 크기가 ATR의 30% 이상
                body = candles[-1].close - candles[-1].open
                if body > atr * 0.3:
                    score += 0.03
            # 아래 긴 꼬리 (망치형)
            lower_wick = candles[-1].open - candles[-1].low if candles[-1].close > candles[-1].open else candles[-1].close - candles[-1].low
            full_range = candles[-1].high - candles[-1].low
            if full_range > 0 and lower_wick / full_range > 0.5:
                score += 0.05
                reasons.append("망치형")
        else:
            if candles[-1].close < candles[-1].open:
                if candles[-2].close > candles[-2].open:
                    score += 0.06
                    reasons.append("음봉전환")
                body = candles[-1].open - candles[-1].close
                if body > atr * 0.3:
                    score += 0.03
            upper_wick = candles[-1].high - candles[-1].open if candles[-1].close < candles[-1].open else candles[-1].high - candles[-1].close
            full_range = candles[-1].high - candles[-1].low
            if full_range > 0 and upper_wick / full_range > 0.5:
                score += 0.05
                reasons.append("유성형")

        # (C2) RSI 확인
        if len(rsi) > 0:
            current_rsi = rsi[-1]
            if is_buy and current_rsi < 40:
                score += 0.05
                reasons.append(f"RSI과매도({current_rsi:.0f})")
            elif not is_buy and current_rsi > 60:
                score += 0.05
                reasons.append(f"RSI과매수({current_rsi:.0f})")

        # (C3) EMA 정렬
        if is_buy and ema20[-1] > ema50[-1]:
            score += 0.04
            reasons.append("EMA정렬")
        elif not is_buy and ema20[-1] < ema50[-1]:
            score += 0.04
            reasons.append("EMA정렬")

        # (C4) 모멘텀 (최근 3봉)
        recent_3 = candles[-3:]
        if is_buy:
            if sum(1 for c in recent_3 if c.close > c.open) >= 2:
                score += 0.03
        else:
            if sum(1 for c in recent_3 if c.close < c.open) >= 2:
                score += 0.03

        # (C5) 볼륨
        if len(candles) >= 20:
            avg_vol = sum(c.volume for c in candles[-20:]) / 20
            if avg_vol > 0 and candles[-1].volume > avg_vol * 1.3:
                score += 0.04
                reasons.append("볼륨확인")

        # ━━━ POI 없으면 감점 ━━━
        if not poi_found:
            score *= 0.4

        meta = {
            "direction": direction, "htf_bias": htf_bias, "zone": zone,
            "reasons": reasons, "score": round(score, 3), "poi_found": poi_found,
        }
        return score, meta

    # ═══════════════════════════════════════════════════════════
    # HTF 캔들 합성
    # ═══════════════════════════════════════════════════════════

    def _build_htf_candles(self, candles: list[Candle]) -> list[Candle]:
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

    # ═══════════════════════════════════════════════════════════
    # 스윙 포인트 & 시장 구조
    # ═══════════════════════════════════════════════════════════

    def _find_swing_points(self, candles: list[Candle]) -> tuple[list, list]:
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
        swing_highs, swing_lows = self._find_swing_points(candles)
        bullish_score = 0.0
        bearish_score = 0.0

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            last_highs = [p for _, p in swing_highs[-3:]]
            last_lows = [p for _, p in swing_lows[-3:]]

            if all(last_highs[i] > last_highs[i - 1] for i in range(1, len(last_highs))):
                bullish_score += 1
            if all(last_lows[i] > last_lows[i - 1] for i in range(1, len(last_lows))):
                bullish_score += 1
            if all(last_highs[i] < last_highs[i - 1] for i in range(1, len(last_highs))):
                bearish_score += 1
            if all(last_lows[i] < last_lows[i - 1] for i in range(1, len(last_lows))):
                bearish_score += 1

        closes = [c.close for c in candles]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, min(50, len(closes)))
        if ema20[-1] > ema50[-1]:
            bullish_score += 0.5
        elif ema20[-1] < ema50[-1]:
            bearish_score += 0.5
        if closes[-1] > ema20[-1]:
            bullish_score += 0.3
        elif closes[-1] < ema20[-1]:
            bearish_score += 0.3

        if bullish_score >= 1.5 and bullish_score > bearish_score:
            return "bullish"
        elif bearish_score >= 1.5 and bearish_score > bullish_score:
            return "bearish"
        return "ranging"

    # ═══════════════════════════════════════════════════════════
    # 프리미엄/디스카운트
    # ═══════════════════════════════════════════════════════════

    def _get_swing_range(self, candles: list[Candle]) -> dict:
        swing_highs, swing_lows = self._find_swing_points(candles)
        if swing_highs and swing_lows:
            range_high = max(p for _, p in swing_highs)
            range_low = min(p for _, p in swing_lows)
        else:
            range_high = max(c.high for c in candles)
            range_low = min(c.low for c in candles)
        return {
            "high": range_high, "low": range_low,
            "equilibrium": (range_high + range_low) / 2,
            "range": range_high - range_low,
        }

    def _classify_zone(self, price: float, swing_range: dict) -> str:
        if swing_range["range"] == 0:
            return "equilibrium"
        position = (price - swing_range["low"]) / swing_range["range"]
        if position >= 0.65:
            return "premium"
        elif position <= 0.35:
            return "discount"
        return "equilibrium"

    # ═══════════════════════════════════════════════════════════
    # OB / FVG 탐색
    # ═══════════════════════════════════════════════════════════

    def _find_order_blocks(self, candles: list[Candle], atr: float, direction: str) -> list[dict]:
        obs = []
        for i in range(2, len(candles) - 1):
            if direction == "bullish":
                if candles[i].close < candles[i].open:
                    impulse = 0
                    impulse_high = candles[i].high
                    for j in range(1, min(4, len(candles) - i)):
                        impulse += candles[i + j].close - candles[i + j].open
                        impulse_high = max(impulse_high, candles[i + j].high)
                    if impulse > atr * 0.6:
                        prev_high = max(c.high for c in candles[max(0, i - 12):i])
                        has_bos = impulse_high > prev_high
                        ob_high = max(candles[i].open, candles[i].close)
                        ob_low = min(candles[i].open, candles[i].close)
                        # 위크 포함 확장
                        ob_high = ob_high + (candles[i].high - ob_high) * 0.3
                        ob_low = ob_low - (ob_low - candles[i].low) * 0.3
                        mitigated = any(c.low < ob_low for c in candles[i + 1:])
                        if not mitigated:
                            obs.append({
                                "high": ob_high, "low": ob_low,
                                "age": len(candles) - 1 - i,
                                "impulse": impulse / atr,
                                "has_bos": has_bos,
                            })
            else:
                if candles[i].close > candles[i].open:
                    impulse = 0
                    impulse_low = candles[i].low
                    for j in range(1, min(4, len(candles) - i)):
                        impulse += candles[i + j].open - candles[i + j].close
                        impulse_low = min(impulse_low, candles[i + j].low)
                    if impulse > atr * 0.6:
                        prev_low = min(c.low for c in candles[max(0, i - 12):i])
                        has_bos = impulse_low < prev_low
                        ob_high = max(candles[i].open, candles[i].close)
                        ob_low = min(candles[i].open, candles[i].close)
                        ob_high = ob_high + (candles[i].high - ob_high) * 0.3
                        ob_low = ob_low - (ob_low - candles[i].low) * 0.3
                        mitigated = any(c.high > ob_high for c in candles[i + 1:])
                        if not mitigated:
                            obs.append({
                                "high": ob_high, "low": ob_low,
                                "age": len(candles) - 1 - i,
                                "impulse": impulse / atr,
                                "has_bos": has_bos,
                            })
        return obs

    def _find_fvgs(self, candles: list[Candle], atr: float, direction: str) -> list[dict]:
        fvgs = []
        for i in range(2, len(candles)):
            if direction == "bullish":
                gap_low = candles[i - 2].high
                gap_high = candles[i].low
                if gap_high > gap_low and (gap_high - gap_low) > atr * 0.15:
                    if candles[i - 1].close > candles[i - 1].open:
                        ce = (gap_high + gap_low) / 2
                        filled = any(c.low <= ce for c in candles[i + 1:]) if i + 1 < len(candles) else False
                        if not filled:
                            fvgs.append({
                                "high": gap_high, "low": gap_low,
                                "ce": ce, "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr,
                            })
            else:
                gap_high = candles[i - 2].low
                gap_low = candles[i].high
                if gap_high > gap_low and (gap_high - gap_low) > atr * 0.15:
                    if candles[i - 1].close < candles[i - 1].open:
                        ce = (gap_high + gap_low) / 2
                        filled = any(c.high >= ce for c in candles[i + 1:]) if i + 1 < len(candles) else False
                        if not filled:
                            fvgs.append({
                                "high": gap_high, "low": gap_low,
                                "ce": ce, "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr,
                            })
        return fvgs

    def _hold(self, price: float) -> Signal:
        return Signal(
            type=SignalType.HOLD, strength=0,
            strategy_name=self.name, price=price,
            timestamp=datetime.now(),
        )
