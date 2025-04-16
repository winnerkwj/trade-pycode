# upbit_rebuilding/upbit_exception.py
import logging, time

def handle_network_exception(e):
    logging.error(f"[NET] {e}", exc_info=True)
    time.sleep(5)

def handle_order_exception(e):
    logging.error(f"[ORDER] {e}", exc_info=True)
    time.sleep(2)

def handle_general_exception(e):
    logging.error(f"[UNKNOWN] {e}", exc_info=True)
    time.sleep(2)
