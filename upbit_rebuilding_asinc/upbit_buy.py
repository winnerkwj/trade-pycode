# upbit_rebuilding_asinc/upbit_buy.py
"""
지정가 매수 유틸 (동기 함수)
  · 3회 재시도
  · REST 실패 / 주문 실패 시 wait & retry
"""
import logging, time, pyupbit
from .upbit_utils      import get_current_price
from .upbit_exception  import handle_network_exception as net_err
from .upbit_exception  import handle_order_exception   as order_err

__all__ = ["place_buy"]   # 외부에서 import 가능한 심볼

def place_buy(upbit, ticker: str, invest_krw: float) -> bool:
    """
    지정가 매수 후 바로 체결 확인
      upbit   : pyupbit.Upbit 객체
      ticker  : 'KRW-BTC' 형식
      invest_krw : 원화 금액
    return True  → 체결 완료
           False → 실패
    """
    for attempt in range(1, 4):
        price = get_current_price(ticker)
        if price is None:
            time.sleep(1); continue

        volume = round(invest_krw / price, 8)
        try:
            order = upbit.buy_limit_order(ticker, price, volume)
            logging.info(f"[BUY {attempt}] {ticker} {volume=:.8f}@{price:.0f}")
            time.sleep(1)

            if upbit.get_order(order['uuid']).get('state') == 'done':
                logging.info(f"[BUY DONE] {ticker}")
                return True

            upbit.cancel_order(order['uuid'])   # 미체결 취소 후 재시도
        except pyupbit.exceptions.UpbitError as e:
            order_err(e)
        except Exception as e:
            net_err(e)

    logging.warning(f"[BUY FAIL] {ticker}")
    return False
