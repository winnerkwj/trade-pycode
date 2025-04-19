# upbit_rebuilding_asinc/upbit_sell.py
"""
매도 주문 래퍼 모듈
"""
import logging


def sell_limit(upbit_client, market: str, volume: float, price: float) -> str:
    """
    지정가 매도 주문 실행

    :param upbit_client: pyupbit.Upbit 인스턴스
    :param market: 'KRW-BTC' 등 시장 티커
    :param volume: 매도할 코인 수량
    :param price: 매도 가격
    :return: 주문 UUID (실패 시 빈 문자열)
    """
    try:
        order = upbit_client.sell_limit_order(market, price, volume)
        if isinstance(order, dict) and 'uuid' in order:
            uuid = order['uuid']
        else:
            uuid = ''
        logging.info(f"[SELL LIMIT] {market}: price={price}, volume={volume}, uuid={uuid}")
        return uuid
    except Exception as e:
        logging.error(f"[SELL LIMIT] {market} 지정가 매도 실패: {e}", exc_info=True)
        return ""


def sell_market(upbit_client, market: str, volume: float) -> str:
    """
    시장가 매도 주문 실행

    :param upbit_client: pyupbit.Upbit 인스턴스
    :param market: 시장 티커
    :param volume: 매도할 코인 수량
    :return: 주문 UUID (실패 시 빈 문자열)
    """
    try:
        order = upbit_client.sell_market_order(market, volume)
        if isinstance(order, dict) and 'uuid' in order:
            uuid = order['uuid']
        else:
            uuid = ''
        logging.info(f"[SELL MARKET] {market}: volume={volume}, uuid={uuid}")
        return uuid
    except Exception as e:
        logging.error(f"[SELL MARKET] {market} 시장가 매도 실패: {e}", exc_info=True)
        return ""


def cancel(upbit_client, uuid: str) -> bool:
    """
    지정가 주문 취소

    :param upbit_client: pyupbit.Upbit 인스턴스
    :param uuid: 취소할 주문의 UUID
    :return: 취소 성공 여부
    """
    try:
        upbit_client.cancel_order(uuid)
        logging.info(f"[CANCEL] 주문 취소 성공: uuid={uuid}")
        return True
    except Exception as e:
        logging.error(f"[CANCEL] 주문 취소 실패: {e}", exc_info=True)
        return False