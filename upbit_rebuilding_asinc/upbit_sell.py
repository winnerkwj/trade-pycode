# upbit_rebuilding_asinc/upbit_sell.py
"""
지정가·시장가 매도 + 주문 취소 유틸
  · sell_limit(...)   → 지정가 매도, UUID 반환
  · sell_market(...)  → 시장가 매도
  · cancel(...)       → 주문 취소
"""

import logging, pyupbit
from .upbit_utils      import tick_size, get_tick_size   # get_tick_size 는 레거시 별칭
from .upbit_exception  import order_err, net_err

__all__ = ["sell_limit", "sell_market", "cancel"]

def sell_limit(upbit, ticker: str, quantity: float, target_price: float) -> str:
    """
    지정가 매도 주문을 내고 UUID 반환.
    호가 단위에 맞춰 target_price 를 버림(round down) 처리한다.
    실패 시 빈 문자열 반환.
    """
    try:
        target_price = (target_price // tick_size(target_price)) * tick_size(target_price)
        order = upbit.sell_limit_order(ticker, target_price, quantity)
        logging.info(f"[ASK LIMIT] {ticker} @{target_price:.0f}")
        return order["uuid"]
    except pyupbit.exceptions.UpbitError as e:
        order_err(e); return ""
    except Exception as e:
        net_err(e); return ""

def sell_market(upbit, ticker: str, quantity: float) -> bool:
    """시장가 매도. 성공이면 True"""
    try:
        upbit.sell_market_order(ticker, quantity)
        logging.info(f"[ASK MKT] {ticker} {quantity=}")
        return True
    except pyupbit.exceptions.UpbitError as e:
        order_err(e); return False
    except Exception as e:
        net_err(e); return False

def cancel(upbit, uuid: str) -> None:
    """주문 취소 (예외만 로그)"""
    try:
        upbit.cancel_order(uuid)
    except Exception as e:
        net_err(e)
