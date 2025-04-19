"""
upbit_utils.py
──────────────
· 호가 단위 계산  tick_size()
· 실시간 가격     get_current_price()  (웹소켓 캐시 → REST fallback)
· RSI 계산        rsi()               (웹소켓 1분 OHLC 우선 사용)
"""
import time, logging, pyupbit, pandas as pd
from .upbit_config    import RSI_PERIOD, RSI_CACHE_SEC, WS_OHLC_USE
from .upbit_stream    import price_cache, price_cache_ts, minute_ohlc
from .upbit_exception import net_err

# ────────────────────────────────────────────────────────────────
# 1.  호가 단위
# ────────────────────────────────────────────────────────────────
def tick_size(price: float) -> float:
    table = [(2_000_000, 1000), (1_000_000, 500), (500_000, 100),
             (100_000, 50), (10_000, 10), (1_000, 1),
             (100, 0.1), (10, 0.01), (1, 0.001),
             (0.1, 0.0001), (0.01, 0.00001), (0.001, 0.000001),
             (0.0001, 0.0000001)]
    for limit, tick in table:
        if price >= limit:
            return tick
    return 0.00000001

# 레거시 별칭
get_tick_size = tick_size

# ────────────────────────────────────────────────────────────────
# 2.  현재가
# ────────────────────────────────────────────────────────────────
def get_current_price(ticker: str):
    now = time.time()
    if ticker in price_cache and now - price_cache_ts.get(ticker, 0) < 3:
        return price_cache[ticker]
    try:
        return pyupbit.get_current_price(ticker)
    except Exception as e:
        net_err(e)
        return None

# main 코드 호환용 별칭
current_price = get_current_price

# ────────────────────────────────────────────────────────────────
# 3.  RSI (14)
# ────────────────────────────────────────────────────────────────
_rsi_cache, _ts_cache = {}, {}

def _rsi_from_series(close: pd.Series) -> float:
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    al = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    return 100 - 100 / (1 + (ag / al).iloc[-1])

def rsi(ticker: str) -> float:
    now = time.time()
    if ticker in _rsi_cache and now - _ts_cache.get(ticker, 0) < RSI_CACHE_SEC:
        return _rsi_cache[ticker]

    close_series = None

    # 3‑A) 웹소켓 1분 OHLC 캐시 사용
    if WS_OHLC_USE and ticker in minute_ohlc:
        now_ts = int(now // 60) * 60
        closes = []
        for i in range(RSI_PERIOD * 2):
            ts = now_ts - 60 * i
            candle = minute_ohlc.get(ticker)
            if candle and candle['ts'] == ts:
                closes.append(candle['c'])
            else:
                break
        if len(closes) >= RSI_PERIOD * 2:
            closes.reverse()
            close_series = pd.Series(closes)

    # 3‑B) REST Fallback
    if close_series is None:
        try:
            df = pyupbit.get_ohlcv(ticker, "minute1", RSI_PERIOD * 2)
            close_series = df['close']
        except Exception as e:
            net_err(e)
            return 50.0

    value = _rsi_from_series(close_series)
    _rsi_cache[ticker], _ts_cache[ticker] = value, now
    return value
