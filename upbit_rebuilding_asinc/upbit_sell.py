# upbit_rebuilding/upbit_sell.py
import logging, time, pyupbit

from .upbit_utils     import get_tick_size
from .upbit_exception import handle_network_exception, handle_order_exception

def place_limit_sell_order(upbit, ticker: str, volume: float, target_price: float) -> str:
    try:
        tick = get_tick_size(target_price)
        target_price = (target_price // tick) * tick
        order = upbit.sell_limit_order(ticker, target_price, volume)
        logging.info(f"[ASK LIMIT] {ticker} {target_price=:.0f}")
        return order['uuid']
    except pyupbit.exceptions.UpbitError as e:
        handle_order_exception(e)
    except Exception as e:
        handle_network_exception(e)
    return ""

def place_market_sell_order(upbit, ticker: str, volume: float) -> bool:
    try:
        upbit.sell_market_order(ticker, volume)
        logging.info(f"[ASK MKT] {ticker} {volume=}")
        return True
    except pyupbit.exceptions.UpbitError as e:
        handle_order_exception(e)
    except Exception as e:
        handle_network_exception(e)
    return False

def cancel_order(upbit, uuid: str):
    try:
        upbit.cancel_order(uuid)
    except Exception as e:
        handle_network_exception(e)
