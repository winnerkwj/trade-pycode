# upbit_rebuilding_asinc/upbit_utils.py
"""
호가 단위 · 실시간 가격 · RSI (WS OHLC 우선)
공개 REST API 요청 제한: 30 req/sec, 티커 전용 10 req/sec
"""
import time
import logging
import pyupbit
import pandas as pd
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
        self.calls     = []  # type: list[float]
        self.lock      = Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
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

# 공개 REST API 및 티커 전용 리미터
public_api_limiter = RestRateLimiter(max_calls=30, period=1.0)
ticker_limiter     = RestRateLimiter(max_calls=10, period=1.0)

# ───── 호가 단위 ─────
def tick_size(price: float) -> float:
    table = [
        (2_000_000,1000),(1_000_000,500),(500_000,100),(100_000,50),
        (10_000,10),(1_000,1),(100,0.1),(10,0.01),(1,0.001),
        (0.1,0.0001),(0.01,0.00001),(0.001,0.000001),(0.0001,0.0000001)
    ]
    for limit, ts in table:
        if price >= limit:
            return ts
    return 0.00000001

get_tick_size = tick_size  # legacy alias

# ───── 현재가 조회 ─────
_TICKER_URL            = "https://api.upbit.com/v1/ticker"
_TICKER_COOLDOWN       = 1.0   # too_many_requests 후 심볼별 재시도 금지
_MAX_FALLBACKS_PER_SEC = 5     # 루프당 REST 폴백 최대 횟수

_last_ticker_err_ts    = 0.0
_last_rest_ts_per_tkr  = {}    # 각 티커별 마지막 REST 호출 시각
_fallback_reset_ts     = 0.0
_fallback_count        = 0

def get_current_price(tkr: str) -> float | None:
    """
    1) WS price_cache 우선 (최근 3초)
    2) too_many_requests 발생 시 1초간 해당 티커 REST 재시도 금지
    3) 한 루프당 최대 5회 REST 폴백 제한
    4) REST ticker API 호출 (ticker_limiter 적용)
    """
    global _last_ticker_err_ts, _fallback_reset_ts, _fallback_count

    now = time.time()
    # 1) WS 캐시 우선
    if tkr in price_cache and now - price_cache_ts.get(tkr, 0) < 3:
        return price_cache[tkr]

    # 루프당 폴백 카운터 리셋 (1초 단위)
    if now - _fallback_reset_ts > 1.0:
        _fallback_reset_ts = now
        _fallback_count    = 0

    # 폴백 제한 체크
    if _fallback_count >= _MAX_FALLBACKS_PER_SEC:
        return None

    # 티커별 too_many_requests 쿨다운 체크
    last_rest = _last_rest_ts_per_tkr.get(tkr, 0.0)
    if now - last_rest < _TICKER_COOLDOWN:
        return None

    # 2) REST 호출 준비
    try:
        with ticker_limiter:
            resp = requests.get(
                _TICKER_URL,
                params={"markets": tkr},
                timeout=5
            ).json()

        # 3) too_many_requests 처리
        if isinstance(resp, dict) and resp.get("name") == "too_many_requests":
            _last_ticker_err_ts           = now
            _last_rest_ts_per_tkr[tkr]    = now
            logging.warning(f"[NET] too_many_requests for {tkr}, cooling down {_TICKER_COOLDOWN}s")
            _fallback_count += 1
            return None

        # 4) 정상 리스트 응답
        if isinstance(resp, list) and resp and "trade_price" in resp[0]:
            _last_rest_ts_per_tkr[tkr] = now
            _fallback_count += 1
            return float(resp[0]["trade_price"])

        # 5) 예상치 못한 포맷
        logging.error(f"[NET] unexpected ticker response for {tkr}: {resp}")
        _last_rest_ts_per_tkr[tkr] = now
        _fallback_count += 1
        return None

    except Exception as e:
        net_err(e)
        _last_rest_ts_per_tkr[tkr] = now
        _fallback_count += 1
        return None

current_price = get_current_price

# ───── RSI 계산 ─────
_rsi_cache, _ts_cache = {}, {}

def _calc_rsi(close: pd.Series) -> float:
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    al    = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    rs    = ag.iloc[-1] / al.iloc[-1] if al.iloc[-1] != 0 else 0
    return 100 - (100 / (1 + rs))

def rsi(tkr: str) -> float:
    """
    WS minute_ohlc 우선으로 RSI 계산, 부족 시 REST get_ohlcv 호출
    """
    now = time.time()
    if tkr in _rsi_cache and now - _ts_cache.get(tkr, 0) < RSI_CACHE_SEC:
        return _rsi_cache[tkr]

    close = None
    if WS_OHLC_USE and tkr in minute_ohlc:
        base_ts = int(now // 60) * 60
        closes  = []
        for i in range(RSI_PERIOD * 2):
            ts  = base_ts - 60 * i
            rec = minute_ohlc.get(tkr, {})
            if rec.get("ts") != ts:
                break
            closes.append(rec["c"])
        if len(closes) >= RSI_PERIOD * 2:
            closes.reverse()
            close = pd.Series(closes)

    if close is None:
        try:
            with public_api_limiter:
                df = pyupbit.get_ohlcv(tkr, "minute1", RSI_PERIOD * 2)
            close = df["close"]
        except Exception as e:
            net_err(e)
            close = pd.Series([50.0] * (RSI_PERIOD * 2))

    value = _calc_rsi(close)
    _rsi_cache[tkr] = value
    _ts_cache[tkr]  = now
    return value
