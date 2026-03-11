"""
쉽알남식 멀티 타임프레임 ICT 전략

핵심 원칙:
1. 4h → 시장 구조 & 바이어스 (큰 그림, 주요 S/R)
2. 1h → POI 탐색 (중요 구간의 OB/FVG만 인정)
3. 15m → 진입 타이밍 (MSS, 캔들 패턴, 모멘텀)

쉽알남 법칙 적용:
- "중요 구간에서 형성된 OB/FVG만 진입" → 4h 레벨 근처의 1h OB/FVG
- "트랩 패턴 인식" → 유동성 스윕 후 반전
- "리스크 관리 최우선" → 확실한 셋업에서만 진입
- "큰 타임프레임 확인 필수" → 4h bias가 최우선

입력: 15분봉 캔들 → 내부에서 1h/4h 합성
"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class ICTMultiTFStrategy(BaseStrategy):
    """쉽알남식 멀티 타임프레임 ICT 전략"""

    def __init__(self, config: dict = None):
        super().__init__("ICT 멀티TF", config)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        """15분봉 캔들을 받아서 멀티 TF 분석"""
        if len(candles) < 300:  # 최소 ~3일치 15m 데이터
            return None

        current_price = candles[-1].close

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 타임프레임 합성
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        candles_1h = self._build_tf(candles, 4)   # 15m × 4 = 1h
        candles_4h = self._build_tf(candles, 16)  # 15m × 16 = 4h

        if len(candles_4h) < 20 or len(candles_1h) < 40:
            return None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 4h 분석: 큰 그림
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        htf_bias = self._analyze_structure(candles_4h)
        swing_range_4h = self._get_swing_range(candles_4h[-20:])
        zone = self._classify_zone(current_price, swing_range_4h)

        # 4h 주요 레벨 (지지/저항)
        htf_levels = self._find_key_levels(candles_4h[-30:])

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 방향 결정
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        can_buy = False
        can_sell = False

        if htf_bias == "bullish":
            can_buy = True
            if zone == "premium":
                can_sell = True
        elif htf_bias == "bearish":
            can_sell = True
            if zone == "discount":
                can_buy = True
        else:
            can_buy = True
            can_sell = True

        if not can_buy and not can_sell:
            return self._hold(current_price)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1h 분석: 중요 구간의 OB/FVG만
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        atr_1h = self._calc_atr(candles_1h[-30:])

        buy_pois = []
        sell_pois = []
        if can_buy:
            buy_pois = self._find_important_pois(
                candles_1h[-40:], atr_1h, htf_levels, "bullish", current_price
            )
        if can_sell:
            sell_pois = self._find_important_pois(
                candles_1h[-40:], atr_1h, htf_levels, "bearish", current_price
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 15m 분석: 진입 확인
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        recent_15m = candles[-20:]
        atr_15m = self._calc_atr(candles[-30:])

        buy_score = 0.0
        sell_score = 0.0
        buy_meta = {}
        sell_meta = {}

        if can_buy and buy_pois:
            buy_score, buy_meta = self._evaluate_entry(
                recent_15m, current_price, atr_15m, "buy",
                buy_pois, htf_bias, zone, htf_levels
            )

        if can_sell and sell_pois:
            sell_score, sell_meta = self._evaluate_entry(
                recent_15m, current_price, atr_15m, "sell",
                sell_pois, htf_bias, zone, htf_levels
            )

        # 4h 레벨 근처 + 트랩이 있으면 POI 없이도 진입 가능
        if can_buy and buy_score < 0.35:
            trap_score, trap_meta = self._check_trap_entry(
                candles[-30:], current_price, atr_15m, "buy", htf_levels
            )
            if trap_score > buy_score:
                buy_score, buy_meta = trap_score, trap_meta

        if can_sell and sell_score < 0.35:
            trap_score, trap_meta = self._check_trap_entry(
                candles[-30:], current_price, atr_15m, "sell", htf_levels
            )
            if trap_score > sell_score:
                sell_score, sell_meta = trap_score, trap_meta

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 최종 결정
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    # 타임프레임 합성
    # ═══════════════════════════════════════════════════════════

    def _build_tf(self, candles: list[Candle], multiplier: int) -> list[Candle]:
        """하위 TF 캔들을 상위 TF로 합성"""
        result = []
        for i in range(0, len(candles) - multiplier + 1, multiplier):
            group = candles[i:i + multiplier]
            result.append(Candle(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            ))
        return result

    # ═══════════════════════════════════════════════════════════
    # 4h 분석 도구
    # ═══════════════════════════════════════════════════════════

    def _find_swing_points(self, candles: list[Candle], lookback: int = 3) -> tuple[list, list]:
        highs, lows = [], []
        for i in range(lookback, len(candles) - lookback):
            is_high = all(candles[i].high >= candles[i + d].high
                         for d in range(-lookback, lookback + 1) if d != 0)
            is_low = all(candles[i].low <= candles[i + d].low
                         for d in range(-lookback, lookback + 1) if d != 0)
            if is_high:
                highs.append((i, candles[i].high))
            if is_low:
                lows.append((i, candles[i].low))
        return highs, lows

    def _analyze_structure(self, candles: list[Candle]) -> str:
        """4h 시장 구조 분석"""
        swing_highs, swing_lows = self._find_swing_points(candles, lookback=2)
        bull = 0.0
        bear = 0.0

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            highs = [p for _, p in swing_highs[-3:]]
            lows = [p for _, p in swing_lows[-3:]]
            if all(highs[i] > highs[i-1] for i in range(1, len(highs))):
                bull += 1
            if all(lows[i] > lows[i-1] for i in range(1, len(lows))):
                bull += 1
            if all(highs[i] < highs[i-1] for i in range(1, len(highs))):
                bear += 1
            if all(lows[i] < lows[i-1] for i in range(1, len(lows))):
                bear += 1

        closes = [c.close for c in candles]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, min(50, len(closes)))
        if ema20[-1] > ema50[-1]:
            bull += 0.5
        else:
            bear += 0.5

        if bull >= 1.5 and bull > bear:
            return "bullish"
        elif bear >= 1.5 and bear > bull:
            return "bearish"
        return "ranging"

    def _get_swing_range(self, candles: list[Candle]) -> dict:
        sh, sl = self._find_swing_points(candles, lookback=2)
        if sh and sl:
            h = max(p for _, p in sh)
            l = min(p for _, p in sl)
        else:
            h = max(c.high for c in candles)
            l = min(c.low for c in candles)
        return {"high": h, "low": l, "range": h - l, "eq": (h + l) / 2}

    def _classify_zone(self, price: float, sr: dict) -> str:
        if sr["range"] == 0:
            return "equilibrium"
        pos = (price - sr["low"]) / sr["range"]
        if pos >= 0.65:
            return "premium"
        elif pos <= 0.35:
            return "discount"
        return "equilibrium"

    def _find_key_levels(self, candles: list[Candle]) -> list[dict]:
        """4h 주요 지지/저항 레벨 탐색"""
        levels = []
        sh, sl = self._find_swing_points(candles, lookback=2)

        for idx, price in sh:
            levels.append({"price": price, "type": "resistance", "strength": 1.0})
        for idx, price in sl:
            levels.append({"price": price, "type": "support", "strength": 1.0})

        # 중복 레벨 병합 (가까운 레벨은 합치기)
        if not levels:
            return levels
        merged = []
        levels.sort(key=lambda x: x["price"])
        atr_approx = (candles[-1].high - candles[-1].low) * 2
        for lv in levels:
            if merged and abs(lv["price"] - merged[-1]["price"]) < atr_approx:
                merged[-1]["strength"] += 0.5  # 근접 레벨 = 더 강함
                merged[-1]["price"] = (merged[-1]["price"] + lv["price"]) / 2
            else:
                merged.append(lv.copy())
        return merged

    # ═══════════════════════════════════════════════════════════
    # 1h 분석: 중요 POI만 (쉽알남 원칙)
    # ═══════════════════════════════════════════════════════════

    def _find_important_pois(self, candles_1h: list[Candle], atr: float,
                              htf_levels: list[dict], direction: str,
                              current_price: float) -> list[dict]:
        """
        쉽알남 원칙: "중요 구간에서 형성된 OB/FVG만 인정"
        → 4h 지지/저항 근처에 있는 1h OB/FVG만 반환
        """
        pois = []

        # OB 탐색
        for i in range(2, len(candles_1h) - 1):
            ob = self._detect_ob(candles_1h, i, atr, direction)
            if ob:
                # 핵심: 이 OB가 4h 주요 레벨 근처인지 확인
                importance = self._check_level_proximity(ob, htf_levels, atr)
                if importance > 0:
                    ob["importance"] = importance
                    ob["type"] = "OB"
                    # 가격이 OB 근처인지
                    if abs(current_price - (ob["high"] + ob["low"]) / 2) < atr * 2:
                        pois.append(ob)

        # FVG 탐색
        for i in range(2, len(candles_1h)):
            fvg = self._detect_fvg(candles_1h, i, atr, direction)
            if fvg:
                importance = self._check_level_proximity(fvg, htf_levels, atr)
                if importance > 0:
                    fvg["importance"] = importance
                    fvg["type"] = "FVG"
                    if abs(current_price - (fvg["high"] + fvg["low"]) / 2) < atr * 2:
                        pois.append(fvg)

        return pois

    def _detect_ob(self, candles: list[Candle], i: int, atr: float,
                    direction: str) -> Optional[dict]:
        """1h OB 감지"""
        if direction == "bullish":
            if candles[i].close < candles[i].open:  # 음봉
                impulse = 0
                for j in range(1, min(4, len(candles) - i)):
                    impulse += candles[i + j].close - candles[i + j].open
                if impulse > atr * 0.5:
                    ob_h = max(candles[i].open, candles[i].close)
                    ob_l = min(candles[i].open, candles[i].close)
                    mitigated = any(c.low < ob_l for c in candles[i + 1:])
                    if not mitigated:
                        return {"high": ob_h, "low": ob_l,
                                "age": len(candles) - 1 - i,
                                "impulse": impulse / atr}
        else:
            if candles[i].close > candles[i].open:  # 양봉
                impulse = 0
                for j in range(1, min(4, len(candles) - i)):
                    impulse += candles[i + j].open - candles[i + j].close
                if impulse > atr * 0.5:
                    ob_h = max(candles[i].open, candles[i].close)
                    ob_l = min(candles[i].open, candles[i].close)
                    mitigated = any(c.high > ob_h for c in candles[i + 1:])
                    if not mitigated:
                        return {"high": ob_h, "low": ob_l,
                                "age": len(candles) - 1 - i,
                                "impulse": impulse / atr}
        return None

    def _detect_fvg(self, candles: list[Candle], i: int, atr: float,
                     direction: str) -> Optional[dict]:
        """1h FVG 감지"""
        if direction == "bullish":
            gap_low = candles[i - 2].high
            gap_high = candles[i].low
            if gap_high > gap_low and (gap_high - gap_low) > atr * 0.1:
                if candles[i - 1].close > candles[i - 1].open:
                    ce = (gap_high + gap_low) / 2
                    filled = any(c.low <= ce for c in candles[i + 1:]) if i + 1 < len(candles) else False
                    if not filled:
                        return {"high": gap_high, "low": gap_low,
                                "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr}
        else:
            gap_high = candles[i - 2].low
            gap_low = candles[i].high
            if gap_high > gap_low and (gap_high - gap_low) > atr * 0.1:
                if candles[i - 1].close < candles[i - 1].open:
                    ce = (gap_high + gap_low) / 2
                    filled = any(c.high >= ce for c in candles[i + 1:]) if i + 1 < len(candles) else False
                    if not filled:
                        return {"high": gap_high, "low": gap_low,
                                "age": len(candles) - 1 - i,
                                "size": (gap_high - gap_low) / atr}
        return None

    def _check_level_proximity(self, poi: dict, htf_levels: list[dict],
                                atr: float) -> float:
        """POI가 4h 주요 레벨 근처인지 확인. 근처면 importance > 0"""
        poi_mid = (poi["high"] + poi["low"]) / 2
        best = 0.0
        for level in htf_levels:
            dist = abs(poi_mid - level["price"])
            if dist < atr * 3:  # 3 ATR 이내
                proximity = 1.0 - (dist / (atr * 3))
                score = proximity * level["strength"]
                best = max(best, score)
        return best

    # ═══════════════════════════════════════════════════════════
    # 15m 분석: 진입 확인
    # ═══════════════════════════════════════════════════════════

    def _evaluate_entry(self, candles_15m: list[Candle], price: float,
                         atr: float, direction: str, pois: list[dict],
                         htf_bias: str, zone: str,
                         htf_levels: list[dict]) -> tuple[float, dict]:
        """15분봉에서 진입 확인"""
        score = 0.0
        reasons = []
        is_buy = direction == "buy"
        aligned = "bullish" if is_buy else "bearish"

        # (1) HTF 방향 일치
        if htf_bias == aligned:
            score += 0.10
            reasons.append("4h방향일치")
        elif htf_bias == "ranging":
            score += 0.03

        # (2) 존 적합성
        if (is_buy and zone == "discount") or (not is_buy and zone == "premium"):
            score += 0.08
            reasons.append("유리한존")

        # (3) 중요 POI 근처 (쉽알남 핵심)
        best_poi = None
        for poi in pois:
            if poi["low"] <= price <= poi["high"]:
                # POI 내부 진입 → 최고
                poi_score = 0.15 * poi.get("importance", 1.0)
                if poi_score > score * 0.3:
                    best_poi = poi
                    score += poi_score
                    reasons.append(f"{poi['type']}진입(4h레벨)")
                break
            else:
                dist = min(abs(price - poi["low"]), abs(price - poi["high"]))
                if dist < atr * 0.5:
                    poi_score = 0.10 * poi.get("importance", 1.0)
                    best_poi = poi
                    score += poi_score
                    reasons.append(f"{poi['type']}근접(4h레벨)")
                    break

        # (4) 15m MSS (Market Structure Shift)
        if len(candles_15m) >= 3:
            if is_buy:
                if (candles_15m[-2].close < candles_15m[-2].open and
                    candles_15m[-1].close > candles_15m[-1].open):
                    score += 0.06
                    reasons.append("15m양봉전환")
                    if candles_15m[-1].close > candles_15m[-2].high:
                        score += 0.04
                        reasons.append("15mMSS")
            else:
                if (candles_15m[-2].close > candles_15m[-2].open and
                    candles_15m[-1].close < candles_15m[-1].open):
                    score += 0.06
                    reasons.append("15m음봉전환")
                    if candles_15m[-1].close < candles_15m[-2].low:
                        score += 0.04
                        reasons.append("15mMSS")

        # (5) 캔들 패턴
        c = candles_15m[-1]
        full_range = c.high - c.low
        if full_range > 0:
            if is_buy:
                lower_wick = min(c.open, c.close) - c.low
                if lower_wick / full_range > 0.5:
                    score += 0.05
                    reasons.append("망치형")
            else:
                upper_wick = c.high - max(c.open, c.close)
                if upper_wick / full_range > 0.5:
                    score += 0.05
                    reasons.append("유성형")

        # (6) RSI 확인
        closes = [c.close for c in candles_15m]
        rsi = self.rsi(closes, 14)
        if rsi:
            if is_buy and rsi[-1] < 40:
                score += 0.04
                reasons.append(f"RSI{rsi[-1]:.0f}")
            elif not is_buy and rsi[-1] > 60:
                score += 0.04
                reasons.append(f"RSI{rsi[-1]:.0f}")

        # (7) 모멘텀
        recent_3 = candles_15m[-3:]
        if is_buy and sum(1 for c in recent_3 if c.close > c.open) >= 2:
            score += 0.03
        elif not is_buy and sum(1 for c in recent_3 if c.close < c.open) >= 2:
            score += 0.03

        # (8) 볼륨 확인
        if len(candles_15m) >= 10:
            avg_vol = sum(c.volume for c in candles_15m[-10:]) / 10
            if avg_vol > 0 and candles_15m[-1].volume > avg_vol * 1.3:
                score += 0.04
                reasons.append("볼륨확인")

        # POI 없으면 큰 감점
        if not best_poi:
            score *= 0.3

        meta = {
            "direction": direction, "htf_bias": htf_bias, "zone": zone,
            "reasons": reasons, "score": round(score, 3),
            "poi": best_poi.get("type") if best_poi else "없음",
        }
        return score, meta

    def _check_trap_entry(self, candles: list[Candle], price: float,
                           atr: float, direction: str,
                           htf_levels: list[dict]) -> tuple[float, dict]:
        """
        쉽알남 트랩 패턴: 유동성 스윕 후 반전
        4h 레벨을 뚫고 나갔다가 다시 들어오는 패턴
        """
        score = 0.0
        reasons = []
        is_buy = direction == "buy"

        sh, sl = self._find_swing_points(candles, lookback=2)

        if is_buy and sl:
            for level in htf_levels:
                if level["type"] != "support":
                    continue
                lv = level["price"]
                # 가격이 지지선 아래로 갔다가 다시 위로 복귀
                if candles[-1].low < lv and candles[-1].close > lv:
                    # 아래 꼬리가 긴 캔들 (트랩 확인)
                    lower_wick = candles[-1].close - candles[-1].low
                    full = candles[-1].high - candles[-1].low
                    if full > 0 and lower_wick / full > 0.4:
                        score = 0.35 + level["strength"] * 0.1
                        reasons.append(f"트랩(지지선 스윕)")
                        reasons.append("4h레벨 반전")
                        break

        elif not is_buy and sh:
            for level in htf_levels:
                if level["type"] != "resistance":
                    continue
                lv = level["price"]
                if candles[-1].high > lv and candles[-1].close < lv:
                    upper_wick = candles[-1].high - candles[-1].close
                    full = candles[-1].high - candles[-1].low
                    if full > 0 and upper_wick / full > 0.4:
                        score = 0.35 + level["strength"] * 0.1
                        reasons.append(f"트랩(저항선 스윕)")
                        reasons.append("4h레벨 반전")
                        break

        meta = {
            "direction": direction, "reasons": reasons,
            "score": round(score, 3), "poi": "트랩",
        }
        return score, meta

    # ═══════════════════════════════════════════════════════════
    # 유틸리티
    # ═══════════════════════════════════════════════════════════

    def _calc_atr(self, candles: list[Candle]) -> float:
        if len(candles) < 14:
            return candles[-1].close * 0.01
        atr_vals = self.atr(
            [c.high for c in candles], [c.low for c in candles],
            [c.close for c in candles], period=14
        )
        return atr_vals[-1] if atr_vals[-1] > 0 else candles[-1].close * 0.01

    def _hold(self, price: float) -> Signal:
        return Signal(
            type=SignalType.HOLD, strength=0,
            strategy_name=self.name, price=price,
            timestamp=datetime.now(),
        )
