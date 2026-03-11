import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Upbit API
    UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
    UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

    # Anthropic API
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Trading Settings
    COIN = os.getenv("COIN", "KRW-BTC")
    TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "50000"))
    DAILY_TARGET = float(os.getenv("DAILY_TARGET", "2.0"))
    MAX_LOSS = float(os.getenv("MAX_LOSS", "-1.0"))
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    ANALYSIS_INTERVAL = int(os.getenv("ANALYSIS_INTERVAL", "30"))  # 스캘핑: 30초

    # Timeframes for SMC scalping (작은 → 큰 순서)
    TIMEFRAMES = ["minute5", "minute15", "minute30", "minute60"]
    CANDLE_COUNT = 80  # 구조물 식별에 충분한 캔들 수

    # Risk Management
    MAX_DAILY_TRADES = 20
    MIN_CONFIDENCE = 0.7
    MIN_CONFLUENCE = 2      # SMC 다중 근거 최소 개수
    MIN_TRADE_INTERVAL = 90  # 스캘핑: 90초 간격

    # Claude model
    CLAUDE_MODEL = "claude-sonnet-4-20250514"
