# ------------------------------------------------------------
# upbit_buy.py
#  - 매수 주문 관련 함수들
# ------------------------------------------------------------
import logging
import pyupbit
import time

from .upbit_exception import (
    handle_network_exception,
    handle_order_exception
)

def place_buy_order(upbit, ticker: str, invest_amount: float) -> bool:
    """
    지정가 매수 주문 후, 바로 체결되도록 현재가 근처로 호가를 맞춤.
    체결될 때까지 몇 번 재시도.
    """
    max_retry = 3
    for attempt in range(1, max_retry+1):
        try:
            current_price = pyupbit.get_current_price(ticker)
            if current_price is None:
                logging.warning(f"{ticker} 현재가 불러오기 실패, 재시도합니다.")
                time.sleep(1)
                continue

            # 수수료 감안
            volume = invest_amount / current_price
            volume = round(volume, 8)  # 소수점 8자리 제한 예시

            order = upbit.buy_limit_order(ticker, current_price, volume)
            logging.info(f"[매수 시도] {attempt}회: {ticker} 가격={current_price}, 수량={volume}")

            time.sleep(1)
            # 주문 상태 확인
            order_detail = upbit.get_order(order['uuid'])
            if order_detail and order_detail.get('state') == 'done':
                # 체결 완료
                logging.info(f"[매수 체결완료] 티커={ticker}, 체결가격={current_price}, 매수금액={invest_amount}")
                return True
            else:
                # 미체결이면 주문 취소 후 재시도
                logging.info(f"[매수 미체결] {ticker}, 주문취소 후 재시도")
                upbit.cancel_order(order['uuid'])
                time.sleep(0.5)

        except pyupbit.exceptions.UpbitError as e:
            handle_order_exception(e)
        except Exception as e:
            handle_network_exception(e)

    logging.error(f"[매수 실패] {ticker} - 모든 재시도 불가")
    return False
