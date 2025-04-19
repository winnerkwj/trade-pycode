import logging, time

__all__ = ["net_err", "order_err", "generic_err",
           "handle_network_exception", "handle_order_exception"]

def net_err(e):
    logging.error(f"[NET] {e}", exc_info=True)
    time.sleep(5)

def order_err(e):
    logging.error(f"[ORDER] {e}", exc_info=True)
    time.sleep(2)

def generic_err(e):
    logging.error(f"[UNKNOWN] {e}", exc_info=True)
    time.sleep(2)

# ────── ★ 기존 코드와 호환되는 별칭 추가 ──────
handle_network_exception = net_err
handle_order_exception   = order_err
# ────────────────────────────────────────────
