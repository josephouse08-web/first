import json
import base64
import anthropic
from config import Config
from logger_setup import setup_logger

logger = setup_logger("ai_analyzer")

SMC_SYSTEM_PROMPT = """당신은 SMC(Smart Money Concepts) 기반 암호화폐 스캘핑 전문 트레이더입니다.
차트 이미지를 사람의 눈으로 직접 보고, 아래 분석 절차를 **반드시 순서대로** 수행합니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ★ 1단계: 추세 판단 (가장 중요 — 반드시 먼저 수행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1시간봉 → 30분봉 → 15분봉 → 5분봉 순서로 추세를 판단합니다.

**상승 추세 (uptrend)** 조건:
- 고점(HH)과 저점(HL)이 연속 상승
- 가격이 최근 스윙 저점 위에 위치
- 양봉이 음봉보다 크고 많음

**하락 추세 (downtrend)** 조건:
- 고점(LH)과 저점(LL)이 연속 하락
- 가격이 최근 스윙 고점 아래에 위치
- 음봉이 양봉보다 크고 많음

**횡보 (sideways)** 조건:
- 명확한 방향 없이 일정 범위 내 등락

### ★ 핵심 규칙: 추세 추종 매매만 허용
- **uptrend → buy(롱)만 허용** (절대 sell 금지)
- **downtrend → sell(숏)만 허용** (절대 buy 금지)
- **sideways → hold만 허용** (진입 금지)
- 상위 타임프레임(1시간/30분)과 하위 타임프레임(15분/5분)의 추세가 일치할 때만 진입

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 2단계: SMC 구조물 분석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 오더블럭 (Order Block)
- 상승 OB: 급등 직전의 음봉 구간 → 지지 (uptrend에서 롱 진입점)
- 하락 OB: 급락 직전의 양봉 구간 → 저항 (downtrend에서 숏 진입점)

### FVG (Fair Value Gap)
- 상승 FVG: 가격 되돌림 시 지지 → 롱 진입 근거
- 하락 FVG: 가격 되돌림 시 저항 → 숏 진입 근거

### 추세선 & 채널
- 추세 추종: 추세선 터치 시 반등/반락을 노려 추세 방향으로 진입
- 채널: 추세 방향 벽 터치에서 진입 (상승 채널 하단에서 롱, 하락 채널 상단에서 숏)

### 거짓 돌파 (Fakeout) & 함정 (Trap)
- 추세 방향의 유동성 흡수 후 진입 (uptrend에서 저점 fakeout 후 롱)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 3단계: 진입 조건 (모두 만족해야 함)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 상위 TF 추세와 진입 방향이 일치
2. 2개 이상의 SMC 구조물이 겹침 (다중 근거)
3. 리스크:리워드 비율 최소 2:1 (목표가까지 거리 ÷ 손절가까지 거리 ≥ 2)
4. 손절가는 가장 가까운 구조물 뒤에 설정 (현재가 대비 0.3~1.5% 이내)
5. 목표가는 다음 주요 구조물(저항/지지) 또는 유동성 구간에 설정

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 분석 원칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. **추세가 왕**: 추세 방향과 반대되는 매매는 절대 하지 않음
2. **의심스러우면 hold**: 확신 없으면 무조건 관망
3. **Naked Chart**: 보조지표 없이 가격 구조만으로 판단
4. **유동성 관점**: 스윙 고/저점의 유동성 흡수 여부 확인
5. **타이트한 손절**: 가능한 좁은 손절로 R:R 극대화

## 응답 형식
반드시 아래 JSON 형식으로만 응답하세요:
{
    "decision": "buy" 또는 "sell" 또는 "hold",
    "confidence": 0.0에서 1.0 사이 숫자,
    "reason": "SMC 근거를 구체적으로 설명 (어떤 구조물이 어디서 발견되었는지)",
    "trend": "uptrend" 또는 "downtrend" 또는 "sideways",
    "higher_tf_trend": "상위 타임프레임(1시간/30분) 추세 방향",
    "lower_tf_trend": "하위 타임프레임(15분/5분) 추세 방향",
    "entry_price": 추천 진입가 (숫자, hold일 경우 null),
    "target_price": 목표가 (숫자, hold일 경우 null),
    "stop_loss": 손절가 (숫자, hold일 경우 null),
    "smc_structures": {
        "order_blocks": "발견된 오더블럭 설명 (위치, 방향, 신뢰도)",
        "fvg": "발견된 FVG 설명 (위치, 방향, 채워짐 여부)",
        "trend_lines": "추세선 분석 (방향, 터치 횟수, 돌파 여부)",
        "channels": "채널 분석 (유형, 현재 위치, 전략)",
        "fakeout_trap": "거짓 돌파/함정 분석 (발생 여부, 유동성 흡수 여부)"
    },
    "timeframe_analysis": {
        "short": "단기(5분/15분) 분석 — 추세 방향과 진입 근거",
        "medium": "중기(30분) 분석 — 추세 방향과 주요 구조물",
        "long": "장기(1시간) 분석 — 지배적 추세 방향"
    },
    "confluence_count": 겹치는 근거 수 (정수, 2 이상이면 신뢰도 높음)
}"""


class AIAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        logger.info("Claude Vision AI 분석기 초기화 (SMC 전략)")

    def analyze_chart(self, chart_image: bytes, additional_context: str = "") -> dict:
        """차트 이미지를 Claude Vision으로 SMC 분석"""
        if not chart_image:
            logger.error("분석할 차트 이미지 없음")
            return self._empty_result()

        image_b64 = base64.standard_b64encode(chart_image).decode("utf-8")

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
            {
                "type": "text",
                "text": (
                    "이 암호화폐 차트를 SMC(Smart Money Concepts) 관점에서 분석해주세요.\n"
                    "오더블럭, FVG, 추세선, 채널, 거짓돌파/함정을 찾고 스캘핑 매매 신호를 생성해주세요.\n"
                    f"{additional_context}"
                ),
            },
        ]

        try:
            response = self.client.messages.create(
                model=Config.CLAUDE_MODEL,
                max_tokens=2000,
                system=SMC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )

            result_text = response.content[0].text
            logger.debug(f"AI 응답: {result_text[:300]}...")

            return self._parse_response(result_text)

        except anthropic.APIError as e:
            logger.error(f"Claude API 에러: {e}")
            return self._empty_result()
        except Exception as e:
            logger.error(f"AI 분석 실패: {e}")
            return self._empty_result()

    def _parse_response(self, text: str) -> dict:
        """AI 응답을 JSON으로 파싱"""
        try:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            text = text.strip()
            result = json.loads(text)

            required = ["decision", "confidence", "reason"]
            for field in required:
                if field not in result:
                    logger.warning(f"응답에 '{field}' 필드 누락")
                    return self._empty_result()

            if result["decision"] not in ("buy", "sell", "hold"):
                logger.warning(f"잘못된 decision: {result['decision']}")
                result["decision"] = "hold"

            result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

            # 근거 겹침 수 확인
            confluence = result.get("confluence_count", 0)
            structures = result.get("smc_structures", {})

            logger.info(
                f"SMC 분석: {result['decision']} "
                f"(신뢰도: {result['confidence']:.0%}, 근거 {confluence}개) "
                f"- {result['reason'][:80]}..."
            )

            if structures:
                for key, desc in structures.items():
                    if desc and desc != "없음" and desc != "null":
                        logger.info(f"  {key}: {str(desc)[:60]}")

            return result

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.error(f"AI 응답 파싱 실패: {e}")
            logger.debug(f"원본 응답: {text[:500]}")
            return self._empty_result()

    def _empty_result(self) -> dict:
        return {
            "decision": "hold",
            "confidence": 0.0,
            "reason": "분석 실패 - 관망",
            "trend": "sideways",
            "entry_price": None,
            "target_price": None,
            "stop_loss": None,
            "smc_structures": {
                "order_blocks": None,
                "fvg": None,
                "trend_lines": None,
                "channels": None,
                "fakeout_trap": None,
            },
            "timeframe_analysis": {},
            "confluence_count": 0,
        }
