# upbit_rebuilding_asinc/upbit_utils.py
"""
호가 단위·실시간 가격·RSI 유틸
"""
import time
import logging
import pandas as pd
import pyupbit
import requests
from threading import Lock

from .upbit_config    import RSI_PERIOD, RSI_CACHE_SEC, WS_OHLC_USE
from .upbit_stream    import price_cache, price_cache_ts, minute_ohlc
from .upbit_exception import net_err

# ───── RateLimiter ─────
class RestRateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period    = period
        self.calls     = []
        self.lock      = Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            # 기간 내 기록된 호출만 남김
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

# REST API 제한기
public_api_limiter = RestRateLimiter(max_calls=30, period=1.0)
ticker_limiter     = RestRateLimiter(max_calls=10, period=1.0)

# ───── 호가 단위 계산 ─────
def tick_size(price: float) -> float:
    table = [
        (2_000_000, 1000), (1_000_000, 500), (500_000, 100), (100_000, 50),
        (10_000, 10), (1_000, 1), (100, 0.1), (10, 0.01), (1, 0.001),
        (0.1, 0.0001), (0.01, 0.00001), (0.001, 0.000001), (0.0001, 0.0000001)
    ]
    for limit, ts in table:
        if price >= limit:
            return ts
    return 0.00000001

get_tick_size = tick_size

# ───── 현재가 조회 (WS 캐시 우선, REST 폴백) ─────
_TICKER_URL            = "https://api.upbit.com/v1/ticker"
_TICKER_COOLDOWN       = 1.0
_MAX_FALLBACKS_PER_SEC = 5

_last_rest_ts_per_tkr  = {}
_fallback_reset_ts     = 0.0
_fallback_count        = 0

def get_current_price(tkr: str) -> float | None:
    now = time.time()
    # WS 캐시 우선
    if tkr in price_cache and now - price_cache_ts.get(tkr, 0) < 3:
        return price_cache[tkr]

    global _fallback_reset_ts, _fallback_count
    if now - _fallback_reset_ts > 1.0:
        _fallback_reset_ts = now
        _fallback_count    = 0
    if _fallback_count >= _MAX_FALLBACKS_PER_SEC:
        return None

    last = _last_rest_ts_per_tkr.get(tkr, 0)
    if now - last < _TICKER_COOLDOWN:
        return None

    try:
        with ticker_limiter:
            resp = requests.get(
                _TICKER_URL,
                params={"markets": tkr},
                timeout=5
            ).json()
        if isinstance(resp, dict) and resp.get("name")=="too_many_requests":
            _last_rest_ts_per_tkr[tkr] = now
            logging.warning(f"[NET] too_many_requests for {tkr}, cooling down {_TICKER_COOLDOWN}s")
            _fallback_count += 1
            return None
        if isinstance(resp, list) and resp and "trade_price" in resp[0]:
            _last_rest_ts_per_tkr[tkr] = now
            _fallback_count += 1
            return float(resp[0]["trade_price"])
        logging.error(f"[NET] unexpected ticker response for {tkr}: {resp}")
        _last_rest_ts_per_tkr[tkr] = now
        _fallback_count += 1
        return None
    except Exception as e:
        net_err(e)
        _last_rest_ts_per_tkr[tkr] = now
        _fallback_count += 1
        return None

# alias for import
current_price = get_current_price

# ───── RSI 계산 ─────
_rsi_cache, _ts_cache = {}, {}

def _calc_rsi(close: pd.Series) -> float:
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    avg_l = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    rs    = (avg_g.iloc[-1] / avg_l.iloc[-1]) if avg_l.iloc[-1] != 0 else 0
    return 100 - (100 / (1 + rs))

def rsi(tkr: str) -> float:
    now = time.time()
    if tkr in _rsi_cache and now - _ts_cache.get(tkr, 0) < RSI_CACHE_SEC:
        return _rsi_cache[tkr]

    close = None
    if WS_OHLC_USE and tkr in minute_ohlc:
        base_ts = int(now // 60) * 60
        vals = []
        for i in range(RSI_PERIOD*2):
            ts = base_ts - 60*i
            rec = minute_ohlc.get(tkr, {})
            if rec.get("ts") != ts:
                break
            vals.append(rec["c"])
        if len(vals) >= RSI_PERIOD*2:
            vals.reverse()
            close = pd.Series(vals)

    if close is None:
        try:
            with public_api_limiter:
                df = pyupbit.get_ohlcv(tkr, "minute1", RSI_PERIOD*2)
            close = df["close"]
        except Exception as e:
            net_err(e)
            close = pd.Series([50.0] * (RSI_PERIOD*2))

    val = _calc_rsi(close)
    _rsi_cache[tkr] = val
    _ts_cache[tkr]  = now
    return val