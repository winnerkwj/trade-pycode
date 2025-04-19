import logging
import time
import pyupbit
from threading import Lock
from .upbit_utils import get_current_price, get_tick_size
from .upbit_exception import net_err

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = Lock()

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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

order_limiter = RateLimiter(8, 1.0)

def place_buy(upbit: pyupbit.Upbit, ticker: str, invest_krw: float) -> bool:
    for attempt in range(1, 4):
        price = get_current_price(ticker)
        if price is None:
            time.sleep(1)
            continue
        vol = round(invest_krw / price, 8)
        price = round(price / get_tick_size(price)) * get_tick_size(price)
        try:
            with order_limiter:
                od = upbit.buy_limit_order(ticker, price, vol)
            logging.info(f"[BUY {attempt}] {ticker} vol={vol:.8f}@{price}")
            time.sleep(1)
            order_info = upbit.get_order(od['uuid'])
            if order_info.get('state') == 'done':
                return True
            with order_limiter:
                upbit.cancel_order(od['uuid'])
        except Exception as e:
            net_err(e)
    return False
