# upbit_rebuilding/upbit_utils.py
import time, logging, pyupbit, pandas as pd

from .upbit_config import RSI_PERIOD, RSI_CALC_INTERVAL
from .upbit_stream import price_cache, price_cache_ts

# ------------------- 호가 단위 -------------------
def get_tick_size(price: float) -> float:
    if price >= 2_000_000: return 1000
    if price >= 1_000_000: return 500
    if price >=   500_000: return 100
    if price >=   100_000: return 50
    if price >=    10_000: return 10
    if price >=     1_000: return 1
    if price >=       100: return 0.1
    if price >=        10: return 0.01
    if price >=         1: return 0.001
    if price >=       0.1: return 0.0001
    if price >=      0.01: return 0.00001
    if price >=     0.001: return 0.000001
    if price >=    0.0001: return 0.0000001
    return 0.00000001

# ------------------- 현재가 ----------------------
def get_current_price(ticker: str) -> float | None:
    now = time.time()
    if ticker in price_cache and now - price_cache_ts.get(ticker, 0) < 3:
        return price_cache[ticker]
    try:
        return pyupbit.get_current_price(ticker)
    except Exception as e:
        logging.error(f"{ticker} REST 현재가 조회 실패: {e}", exc_info=True)
        return None

# ------------------- RSI -------------------------
_rsi_cache, _rsi_ts = {}, {}
def get_rsi(ticker: str) -> float:
    now = time.time()
    if ticker in _rsi_cache and now - _rsi_ts.get(ticker, 0) < RSI_CALC_INTERVAL:
        return _rsi_cache[ticker]
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute1", count=RSI_PERIOD * 2)
        if df is None or df.empty:
            return 50.0
        delta = df['close'].diff().dropna()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
        avg_loss = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
        rs  = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))
        _rsi_cache[ticker], _rsi_ts[ticker] = rsi, now
        return rsi
    except Exception as e:
        logging.error(f"{ticker} RSI 계산 실패: {e}", exc_info=True)
        return 50.0
