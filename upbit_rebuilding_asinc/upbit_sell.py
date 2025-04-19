import logging
from .upbit_utils import get_tick_size

def sell_limit(upbit_client, market: str, volume: float, price: float) -> str:
    try:
        tick = get_tick_size(price)
        adj_price = round(price / tick) * tick
        order = upbit_client.sell_limit_order(market, adj_price, volume)
        uuid = order.get('uuid', '') if isinstance(order, dict) else ''
        if not uuid:
            logging.error(f"[SELL FAIL] uuid 없음: 응답={order}")
        logging.info(f"[SELL LIMIT] {market}: price={adj_price}, volume={volume}, uuid={uuid}")
        return uuid
    except Exception as e:
        logging.error(f"[SELL LIMIT] {market} 지정가 매도 실패: {e}", exc_info=True)
        return ""

def sell_market(upbit_client, market: str, volume: float) -> str:
    try:
        order = upbit_client.sell_market_order(market, volume)
        uuid = order.get('uuid', '') if isinstance(order, dict) else ''
        logging.info(f"[SELL MARKET] {market}: volume={volume}, uuid={uuid}")
        return uuid
    except Exception as e:
        logging.error(f"[SELL MARKET] {market} 시장가 매도 실패: {e}", exc_info=True)
        return ""

def cancel(upbit_client, uuid: str) -> bool:
    try:
        upbit_client.cancel_order(uuid)
        logging.info(f"[CANCEL] 주문 취소 성공: uuid={uuid}")
        return True
    except Exception as e:
        logging.error(f"[CANCEL] 주문 취소 실패: {e}", exc_info=True)
        return False
