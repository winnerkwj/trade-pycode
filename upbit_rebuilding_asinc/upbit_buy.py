# upbit_rebuilding/upbit_buy.py
import logging, time, pyupbit

from .upbit_exception import handle_network_exception, handle_order_exception

def place_buy_order(upbit, ticker: str, invest_amount: float) -> bool:
    max_retry = 3
    for attempt in range(1, max_retry + 1):
        try:
            price = pyupbit.get_current_price(ticker)
            if price is None:
                time.sleep(1); continue
            volume = round(invest_amount / price, 8)
            order  = upbit.buy_limit_order(ticker, price, volume)
            logging.info(f"[BUY TRY {attempt}] {ticker} {price=:.0f}, {volume=}")
            time.sleep(1)
            detail = upbit.get_order(order['uuid'])
            if detail and detail.get('state') == 'done':
                logging.info(f"[BUY DONE] {ticker}")
                return True
            upbit.cancel_order(order['uuid'])
        except pyupbit.exceptions.UpbitError as e:
            handle_order_exception(e)
        except Exception as e:
            handle_network_exception(e)
    logging.error(f"[BUY FAIL] {ticker}")
    return False
