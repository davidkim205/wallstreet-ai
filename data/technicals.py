import yfinance as yf

def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    return float((100 - 100 / (1 + gain / loss)).iloc[-1])

def calc_macd_histogram(close):
    ema12  = close.ewm(span=12).mean()
    ema26  = close.ewm(span=26).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    return float((macd - signal).iloc[-1])

def calc_bb_position(current, close):
    bb_mid   = close.rolling(20).mean().iloc[-1]
    bb_std   = close.rolling(20).std().iloc[-1]
    bb_upper = float(bb_mid + 2 * bb_std)
    bb_lower = float(bb_mid - 2 * bb_std)
    pos = (current - bb_lower) / (bb_upper - bb_lower) * 100 \
          if (bb_upper - bb_lower) > 0 else 50
    if pos > 80:
        label = f"상단 근접 ({pos:.0f}%)"
    elif pos < 20:
        label = f"하단 근접 ({pos:.0f}%)"
    else:
        label = f"중간 구간 ({pos:.0f}%)"
    return bb_upper, bb_lower, label

def fetch_technicals(ticker, period="6mo"):
    hist = yf.Ticker(ticker).history(period=period)
    if len(hist) < 30:
        return {}
    close   = hist["Close"]
    volume  = hist["Volume"]
    current = float(close.iloc[-1])
    ma20  = close.rolling(20).mean().iloc[-1]
    ma50  = close.rolling(50).mean().iloc[-1]  if len(close) >= 50  else None
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    rsi          = calc_rsi(close)
    macd_hist    = calc_macd_histogram(close)
    bb_upper, bb_lower, bb_pos = calc_bb_position(current, close)
    def rsi_signal(r):
        if r < 30: return "과매도 (반등 가능)"
        if r > 70: return "과매수 (조정 가능)"
        return "중립"
    return {
        "ma20":           round(float(ma20), 2),
        "ma50":           round(float(ma50), 2) if ma50 is not None else None,
        "ma200":          round(float(ma200), 2) if ma200 is not None else None,
        "price_vs_ma20":  "위" if current > float(ma20) else "아래",
        "rsi_14":         round(rsi, 1),
        "rsi_signal":     rsi_signal(rsi),
        "macd_histogram": round(macd_hist, 4),
        "macd_signal":    "상승 모멘텀" if macd_hist > 0 else "하락 모멘텀",
        "bb_upper":       round(bb_upper, 2),
        "bb_lower":       round(bb_lower, 2),
        "bb_position":    bb_pos,
        "volume_ratio":   round(float(volume.iloc[-1]) / float(volume.tail(20).mean()), 2),
    }
