import io
import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib
from logger_setup import setup_logger

matplotlib.use("Agg")

logger = setup_logger("chart_generator")

TIMEFRAME_LABELS = {
    "minute1": "1분봉",
    "minute3": "3분봉",
    "minute5": "5분봉",
    "minute10": "10분봉",
    "minute15": "15분봉",
    "minute30": "30분봉",
    "minute60": "1시간봉",
    "minute240": "4시간봉",
    "day": "일봉",
}


def generate_naked_chart(df: pd.DataFrame, timeframe: str) -> bytes:
    """SMC 분석용 네이키드 차트 생성 (캔들 + 볼륨만, 보조지표 없음)"""
    if df.empty:
        logger.warning(f"빈 데이터: {timeframe}")
        return b""

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    label = TIMEFRAME_LABELS.get(timeframe, timeframe)

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 9},
        gridstyle="--",
        gridcolor="#e0e0e0",
    )

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        volume=True,
        style=style,
        title=f"\n{label}",
        figsize=(12, 7),
        panel_ratios=(5, 1),
        savefig=dict(fname=buf, dpi=150, bbox_inches="tight"),
    )
    buf.seek(0)
    image_bytes = buf.read()
    buf.close()
    plt.close("all")

    logger.debug(f"네이키드 차트 생성 완료: {label} ({len(image_bytes)} bytes)")
    return image_bytes


def generate_multi_timeframe_chart(ohlcv_dict: dict) -> bytes:
    """SMC 분석용 멀티 타임프레임 네이키드 차트 (4분할)"""
    timeframes = list(ohlcv_dict.keys())
    n = len(timeframes)

    if n == 0:
        logger.error("차트 데이터 없음")
        return b""

    cols = 2
    rows = (n + 1) // 2

    fig = plt.figure(figsize=(22, 11 * rows))

    for i, tf in enumerate(timeframes):
        df = ohlcv_dict[tf]
        if df.empty:
            continue

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        ax = fig.add_subplot(rows, cols, i + 1)
        label = TIMEFRAME_LABELS.get(tf, tf)
        _draw_naked_candlestick(ax, df, label)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    image_bytes = buf.read()
    buf.close()
    plt.close(fig)

    logger.info(f"멀티 타임프레임 네이키드 차트 생성 완료 ({len(image_bytes)} bytes)")
    return image_bytes


def _draw_naked_candlestick(ax, df: pd.DataFrame, title: str):
    """네이키드 캔들스틱 (보조지표 없음 - SMC 분석에 최적)"""
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    x = np.arange(len(df))

    # 캔들
    colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    ax.bar(x, abs(closes - opens), bottom=np.minimum(closes, opens),
           color=colors, width=0.6, alpha=0.9)
    ax.vlines(x, lows, highs, color=colors, linewidth=0.6)

    # 최근 스윙 고/저점 표시 (SMC 구조물 식별 보조)
    swing_highs, swing_lows = _find_swing_points(highs, lows, window=5)
    for idx in swing_highs:
        ax.annotate("H", xy=(idx, highs[idx]), fontsize=7, color="#ef5350",
                     ha="center", va="bottom", fontweight="bold")
    for idx in swing_lows:
        ax.annotate("L", xy=(idx, lows[idx]), fontsize=7, color="#26a69a",
                     ha="center", va="top", fontweight="bold")

    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.grid(True, alpha=0.2)
    ax.set_facecolor("#fafafa")

    # X축 라벨
    n_labels = 6
    step = max(len(df) // n_labels, 1)
    tick_positions = x[::step]
    tick_labels = [df.index[i].strftime("%m/%d %H:%M") for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=7, rotation=30)


def _find_swing_points(highs, lows, window=5):
    """스윙 고점/저점 탐지 (SMC 구조물 식별용)"""
    swing_highs = []
    swing_lows = []
    n = len(highs)

    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            swing_highs.append(i)
        if lows[i] == min(lows[i - window:i + window + 1]):
            swing_lows.append(i)

    return swing_highs, swing_lows
