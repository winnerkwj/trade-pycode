import time
import pyupbit

# ------------------------------------------------------------
# get_tick_size 함수
# ------------------------------------------------------------
def get_tick_size(price):
    if price >= 2000000:
        return 1000
    elif price >= 1000000:
        return 500
    elif price >= 500000:
        return 100
    elif price >= 100000:
        return 50
    elif price >= 10000:
        return 10
    elif price >= 1000:
        return 1
    elif price >= 100:
        return 0.1
    elif price >= 10:
        return 0.01
    elif price >= 1:
        return 0.001
    elif price >= 0.1:
        return 0.0001
    elif price >= 0.01:
        return 0.00001
    elif price >= 0.001:
        return 0.000001
    elif price >= 0.0001:
        return 0.0000001
    else:
        return 0.00000001

# ------------------------------------------------------------
# 동기식 RateLimiter 
# ------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_calls, period=1.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    def acquire(self):
        now = time.time()
        # period 초 안에 일어난 호출만 유지
        self.calls = [t for t in self.calls if t > now - self.period]
        if len(self.calls) >= self.max_calls:
            sleep_time = self.calls[0] + self.period - now
            time.sleep(sleep_time)
        self.calls.append(time.time())
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# 원하는 만큼 RateLimiter 생성
non_order_request_limiter = RateLimiter(max_calls=25, period=1.0)   

def get_ohlcv_sync(ticker, interval="minute1", count=200):
    with non_order_request_limiter:
        try:
            return pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        except Exception as e:
            time.sleep(3)
            return None
# ------------------------------------------------------------
# get_rsi 함수
# ------------------------------------------------------------
rsi_cache = {}
rsi_timestamp = {}

def get_rsi_sync(ticker, period=14):
    now = time.time()
    if ticker in rsi_cache and now - rsi_timestamp.get(ticker, 0) < 60:
        return rsi_cache[ticker]

    df = get_ohlcv_sync(ticker, interval="minute1", count=period*2)
    if df is None or df.empty:
        return None

    close = df['close']
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    rsi_value = rsi_val.iloc[-1]

    rsi_cache[ticker] = rsi_value
    rsi_timestamp[ticker] = now
    return rsi_value



