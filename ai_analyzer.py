import json
import base64
import anthropic
from config import Config
from logger_setup import setup_logger

logger = setup_logger("ai_analyzer")

SYSTEM_PROMPT = """당신은 암호화폐 스캘핑 전문 트레이더입니다.
차트 이미지를 분석하여 매매 신호를 생성합니다.

분석 시 다음 항목들을 확인하세요:
1. 캔들 패턴 (망치형, 도지, 장악형, 십자형 등)
2. 이동평균선 배열 (정배열/역배열, 골든크로스/데드크로스)
3. 볼린저 밴드 (밴드 이탈, 스퀴즈, 확장)
4. RSI (과매수 70 이상, 과매도 30 이하, 다이버전스)
5. MACD (크로스, 히스토그램 방향)
6. 거래량 변화
7. 지지/저항선
8. 각 타임프레임 간 추세 일치 여부

스캘핑에 집중하세요:
- 짧은 시간(5분~1시간) 내 빠른 진입/청산
- 작은 수익을 여러 번 쌓는 전략
- 리스크 대비 보상 비율 최소 1:1.5

반드시 아래 JSON 형식으로만 응답하세요:
{
    "decision": "buy" 또는 "sell" 또는 "hold",
    "confidence": 0.0에서 1.0 사이 숫자,
    "reason": "분석 근거를 한국어로 상세히 설명",
    "trend": "uptrend" 또는 "downtrend" 또는 "sideways",
    "entry_price": 추천 진입가 (숫자, hold일 경우 null),
    "target_price": 목표가 (숫자, hold일 경우 null),
    "stop_loss": 손절가 (숫자, hold일 경우 null),
    "timeframe_analysis": {
        "5min": "5분봉 분석 요약",
        "10min": "10분봉 분석 요약",
        "30min": "30분봉 분석 요약",
        "1hour": "1시간봉 분석 요약"
    },
    "patterns": ["발견된 패턴1", "발견된 패턴2"]
}"""


class AIAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        logger.info("Claude Vision AI 분석기 초기화 완료")

    def analyze_chart(self, chart_image: bytes, additional_context: str = "") -> dict:
        """차트 이미지를 Claude Vision으로 분석"""
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
                "text": f"이 암호화폐 차트를 분석하여 스캘핑 매매 신호를 생성해주세요.{chr(10)}{additional_context}",
            },
        ]

        try:
            response = self.client.messages.create(
                model=Config.CLAUDE_MODEL,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )

            result_text = response.content[0].text
            logger.debug(f"AI 응답: {result_text[:200]}...")

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
            # JSON 블록 추출 (```json ... ``` 형태 처리)
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            # 앞뒤 공백 제거 후 JSON 파싱
            text = text.strip()
            result = json.loads(text)

            # 필수 필드 검증
            required = ["decision", "confidence", "reason"]
            for field in required:
                if field not in result:
                    logger.warning(f"응답에 '{field}' 필드 누락")
                    return self._empty_result()

            if result["decision"] not in ("buy", "sell", "hold"):
                logger.warning(f"잘못된 decision: {result['decision']}")
                result["decision"] = "hold"

            result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

            logger.info(
                f"AI 분석 결과: {result['decision']} "
                f"(신뢰도: {result['confidence']:.1%}) - {result['reason'][:50]}..."
            )
            return result

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.error(f"AI 응답 파싱 실패: {e}")
            logger.debug(f"원본 응답: {text[:300]}")
            return self._empty_result()

    def _empty_result(self) -> dict:
        """빈 분석 결과 반환"""
        return {
            "decision": "hold",
            "confidence": 0.0,
            "reason": "분석 실패 - 관망",
            "trend": "sideways",
            "entry_price": None,
            "target_price": None,
            "stop_loss": None,
            "timeframe_analysis": {},
            "patterns": [],
        }
