# upbit_rebuilding_asinc/upbit_buy.py
"""
upbit_buy.py
──────────
주문 요청 API(POST /v1/orders) 제한: 초당 8회
RateLimiter 클래스를 활용해 동기 코드에 스로틀 적용
"""
import logging
import time
import time
import pyupbit
from threading import Lock
from .upbit_utils import get_current_price
from .upbit_exception import net_err, order_err

__all__ = ["place_buy"]

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: list[float] = []
        self.lock = Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.time()
            # Remove calls older than period
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

# 주문 생성·취소(POST /v1/orders 등) rate limiter: 8 calls/sec
order_limiter = RateLimiter(max_calls=8, period=1.0)


def place_buy(upbit: pyupbit.Upbit, ticker: str, invest_krw: float) -> bool:
    """
    지정가 매수 시도: 최대 3회
    1) 현재가 조회
    2) buy_limit_order (RateLimiter 적용)
    3) 체결 확인, 미체결 시 cancel_order (RateLimiter 적용)
    """
    for attempt in range(1, 4):
        price = get_current_price(ticker)
        if price is None:
            time.sleep(1)
            continue
        vol = round(invest_krw / price, 8)
        try:
            # 주문 요청
            with order_limiter:
                od = upbit.buy_limit_order(ticker, price, vol)
            logging.info(f"[BUY {attempt}] {ticker} vol={vol:.8f}@{price:.0f}")
            time.sleep(1)
            # 체결 확인
            order_info = upbit.get_order(od['uuid'])
            if order_info.get('state') == 'done':
                return True
            # 미체결 시 주문 취소
            with order_limiter:
                upbit.cancel_order(od['uuid'])
        except pyupbit.exceptions.UpbitError as e:
            order_err(e)
        except Exception as e:
            net_err(e)
    return False
