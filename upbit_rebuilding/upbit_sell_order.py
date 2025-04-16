# ------------------------------------------------------------
# upbit_sell.py
#  - 매도 주문 관련 함수들
# ------------------------------------------------------------
import logging
import pyupbit
import time

from .upbit_exception import (
    handle_network_exception,
    handle_order_exception
)
from .upbit_utils import get_tick_size

def place_limit_sell_order(upbit, ticker: str, volume: float, target_price: float) -> str:
    """
    지정가 매도 주문. 성공 시 UUID 반환. 실패 시 "" 반환.
    """
    try:
        # 호가단위 맞추기
        tick_size = get_tick_size(target_price)
        if tick_size > 0:
            # 호가단위에 맞춰서 내림 처리
            target_price = (target_price // tick_size) * tick_size

        order = upbit.sell_limit_order(ticker, target_price, volume)
        if not isinstance(order, dict):
            logging.error(f"{ticker} 매도 응답이 dict가 아님: {order}")
            return ""

        logging.info(f"[지정가 매도 주문] 티커={ticker}, 가격={target_price}, 수량={volume}")
        return order['uuid']
    except pyupbit.exceptions.UpbitError as e:
        handle_order_exception(e)
    except Exception as e:
        handle_network_exception(e)
    return ""

def place_market_sell_order(upbit, ticker: str, volume: float) -> bool:
    """시장가 매도"""
    try:
        order = upbit.sell_market_order(ticker, volume)
        if not isinstance(order, dict):
            logging.error(f"{ticker} 시장가 매도 응답이 dict가 아님: {order}")
            return False
        logging.info(f"[시장가 매도 체결] 티커={ticker}, 수량={volume}")
        return True
    except pyupbit.exceptions.UpbitError as e:
        handle_order_exception(e)
    except Exception as e:
        handle_network_exception(e)
    return False

def cancel_order(upbit, uuid: str):
    """주문 취소"""
    try:
        upbit.cancel_order(uuid)
    except pyupbit.exceptions.UpbitError as e:
        handle_order_exception(e)
    except Exception as e:
        handle_network_exception(e)
