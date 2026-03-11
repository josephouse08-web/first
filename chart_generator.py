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


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 계산"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 계산"""
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_bollinger_bands(series: pd.Series, period: int = 20, std_dev: int = 2):
    """볼린저 밴드 계산"""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower


def generate_single_chart(df: pd.DataFrame, timeframe: str) -> bytes:
    """단일 타임프레임 차트 이미지 생성"""
    if df.empty:
        logger.warning(f"빈 데이터: {timeframe}")
        return b""

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # Ensure proper index
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Moving averages
    ma5 = df["close"].rolling(5).mean()
    ma20 = df["close"].rolling(20).mean()

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower = calc_bollinger_bands(df["close"])

    # RSI
    rsi = calc_rsi(df["close"])

    # MACD
    macd_line, signal_line, macd_hist = calc_macd(df["close"])

    # Additional plots
    add_plots = [
        mpf.make_addplot(ma5, color="orange", width=0.8, label="MA5"),
        mpf.make_addplot(ma20, color="blue", width=0.8, label="MA20"),
        mpf.make_addplot(bb_upper, color="gray", linestyle="--", width=0.5),
        mpf.make_addplot(bb_lower, color="gray", linestyle="--", width=0.5),
        mpf.make_addplot(rsi, panel=2, color="purple", width=0.8, ylabel="RSI"),
        mpf.make_addplot(
            pd.Series(70, index=df.index), panel=2, color="red",
            linestyle="--", width=0.5,
        ),
        mpf.make_addplot(
            pd.Series(30, index=df.index), panel=2, color="green",
            linestyle="--", width=0.5,
        ),
        mpf.make_addplot(macd_line, panel=3, color="blue", width=0.8, ylabel="MACD"),
        mpf.make_addplot(signal_line, panel=3, color="red", width=0.8),
        mpf.make_addplot(
            macd_hist, panel=3, type="bar",
            color=["green" if v >= 0 else "red" for v in macd_hist.fillna(0)],
        ),
    ]

    label = TIMEFRAME_LABELS.get(timeframe, timeframe)

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 8},
    )

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        volume=True,
        addplot=add_plots,
        style=style,
        title=f"\n{label}",
        figsize=(10, 8),
        panel_ratios=(4, 1, 1, 1),
        savefig=dict(fname=buf, dpi=150, bbox_inches="tight"),
    )
    buf.seek(0)
    image_bytes = buf.read()
    buf.close()
    plt.close("all")

    logger.debug(f"차트 생성 완료: {label} ({len(image_bytes)} bytes)")
    return image_bytes


def generate_multi_timeframe_chart(ohlcv_dict: dict) -> bytes:
    """멀티 타임프레임 4분할 차트 이미지 생성"""
    timeframes = list(ohlcv_dict.keys())
    n = len(timeframes)

    if n == 0:
        logger.error("차트 데이터 없음")
        return b""

    cols = 2
    rows = (n + 1) // 2

    fig = plt.figure(figsize=(20, 10 * rows))

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

        # Simple candlestick with matplotlib
        _draw_candlestick_subplot(ax, df, label)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    image_bytes = buf.read()
    buf.close()
    plt.close(fig)

    logger.info(f"멀티 타임프레임 차트 생성 완료 ({len(image_bytes)} bytes)")
    return image_bytes


def _draw_candlestick_subplot(ax, df: pd.DataFrame, title: str):
    """서브플롯에 캔들스틱 그리기"""
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    x = np.arange(len(df))

    # Candles
    colors = ["green" if c >= o else "red" for c, o in zip(closes, opens)]
    ax.bar(x, abs(closes - opens), bottom=np.minimum(closes, opens),
           color=colors, width=0.6, alpha=0.8)
    ax.vlines(x, lows, highs, color=colors, linewidth=0.5)

    # Moving averages
    ma5 = pd.Series(closes).rolling(5).mean()
    ma20 = pd.Series(closes).rolling(20).mean()
    ax.plot(x, ma5, color="orange", linewidth=0.8, label="MA5")
    ax.plot(x, ma20, color="blue", linewidth=0.8, label="MA20")

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower = calc_bollinger_bands(pd.Series(closes))
    ax.plot(x, bb_upper, color="gray", linewidth=0.5, linestyle="--")
    ax.plot(x, bb_lower, color="gray", linewidth=0.5, linestyle="--")
    ax.fill_between(x, bb_upper, bb_lower, alpha=0.05, color="gray")

    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # Show last few x-axis labels
    n_labels = 6
    step = max(len(df) // n_labels, 1)
    tick_positions = x[::step]
    tick_labels = [df.index[i].strftime("%m/%d %H:%M") for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=7, rotation=30)
