# ------------------------------------------------------------
# upbit_exception.py
#  - 네트워크 오류나 주문 실패 등에 대한 예외처리 예시
# ------------------------------------------------------------

import logging
import time

def handle_network_exception(e):
    """네트워크 예외 처리 함수"""
    logging.error(f"네트워크 예외 발생: {e}", exc_info=True)
    # 일시 대기 후 재시도할 수 있도록 구성
    time.sleep(5)

def handle_order_exception(e):
    """주문 관련 예외 처리 함수"""
    logging.error(f"주문 처리 예외 발생: {e}", exc_info=True)
    time.sleep(2)

def handle_general_exception(e):
    """기타 모든 예외 처리 함수"""
    logging.error(f"알 수 없는 예외 발생: {e}", exc_info=True)
    time.sleep(2)
