import json
import base64
import anthropic
from config import Config
from logger_setup import setup_logger

logger = setup_logger("ai_analyzer")

SMC_SYSTEM_PROMPT = """당신은 SMC(Smart Money Concepts) 기반 암호화폐 스캘핑 전문 트레이더입니다.
차트 이미지를 사람의 눈으로 직접 보고, 아래 5가지 SMC 구조물을 분석하여 매매 신호를 생성합니다.

## 분석 프레임워크 (쉽알남 SMC 전략)

### 1. 오더블럭 (Order Block)
- 가격이 급격히 방향 전환하기 직전의 캔들 구간 (스마트머니의 대량 주문 흔적)
- 상승 OB: 급등 직전의 음봉 몸통 구간 → 지지 역할
- 하락 OB: 급락 직전의 양봉 몸통 구간 → 저항 역할
- 신뢰도 높은 OB: 유동성 흡수(스윙 고/저점 갱신) 후 발생, 캔들 몸통 크기 차이가 2배 이상

### 2. FVG (Fair Value Gap)
- 연속 3개 캔들에서 중간 캔들이 크게 움직여 생기는 가격 불균형 구간
- 상승 FVG: 1번 캔들 고가 ~ 3번 캔들 저가 사이 갭 → 가격이 되돌아와 지지
- 하락 FVG: 1번 캔들 저가 ~ 3번 캔들 고가 사이 갭 → 가격이 되돌아와 저항
- 중간 캔들이 앞뒤 캔들보다 2~3배 이상 커야 유효

### 3. 추세선 (Trend Line)
- 상승 추세선: 스윙 저점들의 꼬리(wick)를 연결
- 하락 추세선: 스윙 고점들의 꼬리(wick)를 연결
- 추세 추종: 추세선 터치 시 반등을 노려 진입
- 추세 반전: 추세선 돌파 후 리테스트 시 반대 방향 진입

### 4. 채널 (Channel)
- 평행한 두 추세선 사이에서 가격이 왕복하는 구간
- 4포인트 매매: 채널 확인 후 4번째 터치에서 역추세 진입
- 리테스트 매매: 채널 이탈 후 재진입/리테스트 시 진입
- 돌파 매매: 채널을 강하게 뚫고 나갈 때 추세 추격 진입
- 중단선(50%)을 못 넘기면 추세 약화 신호

### 5. 거짓 돌파 (Fake out) & 함정 (Trap)
- Fake out: 구조물을 돌파하는 척했다가 긴 꼬리 남기고 급반전 (단일 고/저점)
- Trap: 구조물 돌파 후 잠시 유인했다가 다시 회귀 (쌍고점/쌍바닥 형태)
- 스마트머니가 개인 투자자의 손절 물량(유동성)을 흡수하기 위한 행위
- 긴 꼬리 = 강력한 유동성 흡수 = 높은 반전 신뢰도

## 분석 원칙
1. **Naked Chart 분석**: 보조지표 없이 캔들과 가격 구조만으로 판단
2. **다중 근거**: 여러 구조물이 겹치는 구간에서만 진입 (OB + FVG, 채널 + Fake out 등)
3. **멀티 타임프레임**: 상위 타임프레임의 추세 방향과 일치하는 하위 타임프레임 진입만 허용
4. **유동성 관점**: 스윙 고/저점 근처에 모인 손절 물량(유동성)의 흡수 여부 확인
5. **스캘핑 최적화**: 5분~1시간 내 빠른 진입/청산, 리스크:리워드 최소 1:1.5

## 매매 방향
- **buy (롱)**: 상승 오더블럭/FVG 지지, 상승 추세선 바운스, 채널 하단 반등, Fake out 후 급반등
- **sell (숏)**: 하락 오더블럭/FVG 저항, 하락 추세선 저항, 채널 상단 저항, Trap 후 급락
- **hold**: 근거 불충분, 구조물 미확인, 방향 불명확

## 응답 형식
반드시 아래 JSON 형식으로만 응답하세요:
{
    "decision": "buy" 또는 "sell" 또는 "hold",
    "confidence": 0.0에서 1.0 사이 숫자,
    "reason": "SMC 근거를 구체적으로 설명 (어떤 구조물이 어디서 발견되었는지)",
    "trend": "uptrend" 또는 "downtrend" 또는 "sideways",
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
        "short": "단기(5분/10분) 분석",
        "medium": "중기(30분) 분석",
        "long": "장기(1시간) 분석"
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
