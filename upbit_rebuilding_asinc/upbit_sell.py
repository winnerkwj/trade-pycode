# upbit_rebuilding_asinc/upbit_utils.py
"""
호가 단위 · 실시간 가격 · RSI (WS OHLC 우선)
공개 REST API 요청 제한: 30 req/sec
"""
import time
import logging
import pyupbit
import pandas as pd
from threading import Lock
from .upbit_config import RSI_PERIOD, RSI_CACHE_SEC, WS_OHLC_USE
from .upbit_stream import price_cache, price_cache_ts, minute_ohlc
from .upbit_exception import net_err

class RestRateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period    = period
        self.calls     = []    # type: list[float]
        self.lock      = Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            # 기간 지난 호출 기록 제거
            self.calls = [t for t in self.calls if t > now - self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self.calls.append(time.time())

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# 공개 REST API rate limiter (30 calls/sec)
public_api_limiter = RestRateLimiter(max_calls=30, period=1.0)

# ───── 호가 단위 ─────
def tick_size(price: float) -> float:
    table = [
        (2_000_000,1000),(1_000_000,500),(500_000,100),(100_000,50),
        (10_000,10),(1_000,1),(100,0.1),(10,0.01),(1,0.001),
        (0.1,0.0001),(0.01,0.00001),(0.001,0.000001),(0.0001,0.0000001)
    ]
    for limit, tick in table:
        if price >= limit:
            return tick
    return 0.00000001
get_tick_size = tick_size  # legacy alias

# ───── 현재가 조회 ─────
def get_current_price(tkr: str) -> float | None:
    now = time.time()
    if tkr in price_cache and now - price_cache_ts.get(tkr,0) < 3:
        return price_cache[tkr]
    try:
        return pyupbit.get_current_price(tkr)
    except Exception as e:
        net_err(e)
        return None
current_price = get_current_price

# ───── RSI 계산 ─────
_rsi_cache, _ts_cache = {}, {}

def _calc_rsi(close: pd.Series) -> float:
    delta   = close.diff().dropna()
    gain    = delta.clip(lower=0)
    loss    = -delta.clip(upper=0)
    ag      = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    al      = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    rs      = ag.iloc[-1] / al.iloc[-1] if al.iloc[-1] != 0 else 0
    return 100 - (100 / (1 + rs))

def rsi(tkr: str) -> float:
    now = time.time()
    if tkr in _rsi_cache and now - _ts_cache.get(tkr,0) < RSI_CACHE_SEC:
        return _rsi_cache[tkr]

    close = None
    # WS OHLC 우선
    if WS_OHLC_USE and tkr in minute_ohlc:
        base_ts = int(now//60)*60
        closes = []
        for i in range(RSI_PERIOD*2):
            ts = base_ts - 60*i
            rec = minute_ohlc.get(tkr, {})
            if rec.get('ts') != ts:
                break
            closes.append(rec['c'])
        if len(closes) >= RSI_PERIOD*2:
            closes.reverse()
            close = pd.Series(closes)

    # REST fallback
    if close is None:
        try:
            with public_api_limiter:
                df = pyupbit.get_ohlcv(tkr, "minute1", RSI_PERIOD*2)
            close = df['close']
        except Exception as e:
            net_err(e)
            close = pd.Series([50.0]*RSI_PERIOD*2)

    value = _calc_rsi(close)
    _rsi_cache[tkr], _ts_cache[tkr] = value, now
    return value
