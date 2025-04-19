# upbit_rebuilding_asinc/upbit_main_ws.py
"""
웹소켓 실시간 시세 + 정밀 추매(Precision AD)
──────────────────────────────────────────
추가 기능
  1. 장애 복구      : 재시작 시 미체결 매도 주문 UUID‑복구
  2. 자동 종목 필터 : 15 분마다 거래중지/상폐 코인 제외
  3. WS 1 분 OHLC   : RSI 계산 시 우선 사용 (REST Fallback)
  4. 잔고 부족 시 매수 스킵(로그만) · 소량 추매 제거
"""

import logging, time, pyupbit
from .upbit_config   import *                     # 모든 전략 상수
from .upbit_utils    import current_price, rsi    # 가격·RSI 유틸
from .upbit_buy      import place_buy             # 지정가 매수
from .upbit_sell     import sell_limit, sell_market, cancel
from .upbit_stream   import PriceStreamer, price_cache
from .market_filter  import fetch_filtered_tickers
from .upbit_exception import generic_err

# ─────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────
in_pos, avg_buy      = {}, {}
sell_uuid, last_add  = {}, {}

# ─────────────────────────────────────────
# 보조 함수
# ─────────────────────────────────────────
def q_add_needed(q_prev, c_prev, p_now, tgt):
    """
    목표 손익률(tgt)에 맞추기 위한 추가 수량 계산
    q_prev : 기존 수량
    c_prev : 기존 평단
    p_now  : 현재가
    tgt    : -0.0055 (MAINTAIN_PROFIT_RATE)
    """
    c_new = p_now / (1 + tgt)
    denom = c_new - p_now
    return max((q_prev * (c_prev - c_new)) / denom, 0) if denom > 0 else 0

def restore_open_orders(up):
    """
    재시작 시 미체결 매도 주문(UUID) 복구
    Upbit.get_order('', state='wait') → 리스트
    """
    try:
        orders = up.get_order("", state="wait")
        for od in orders:
            if not isinstance(od, dict):
                continue
            if od.get("side") == "ask" and od.get("ord_type") == "limit":
                sell_uuid[od["market"]] = od["uuid"]
                logging.info(f"[RESTORE] {od['market']} UUID={od['uuid']}")
    except Exception as e:
        generic_err(e)

# ─────────────────────────────────────────
# 메인 함수
# ─────────────────────────────────────────
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    up = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

    # 웹소켓 스트리머 시작 (초기 리스트는 ALLOWED_TICKERS)
    streamer = PriceStreamer(ALLOWED_TICKERS)
    streamer.start()

    # 장애 복구: 미체결 매도 주문 가져오기
    restore_open_orders(up)

    # 시작 시 보유 코인(잔고) 반영
    for b in up.get_balances():
        if b["currency"] == "KRW":
            continue
        tkr = f"KRW-{b['currency']}"
        qty = float(b["balance"]) + float(b["locked"])
        if qty > 0:
            in_pos[tkr]   = True
            avg_buy[tkr]  = float(b["avg_buy_price"])
            last_add[tkr] = 0
            sell_uuid.setdefault(tkr, "")  # 복구된 UUID가 없으면 빈 문자열

    last_filter_ts = 0
    allowed = ALLOWED_TICKERS.copy()

    try:
        while True:

            # ─── 1. 15 분마다 종목 필터 갱신 ─────────────────────
            if time.time() - last_filter_ts > TICKER_FILTER_INTERVAL:
                allowed = fetch_filtered_tickers(ALLOWED_TICKERS, TICKER_FILTER_INTERVAL)
                streamer.update(allowed + [t for t, v in in_pos.items() if v])
                last_filter_ts = time.time()

            # 잔고 조회
            balances = up.get_balances()
            krw = float(next((b for b in balances if b["currency"] == "KRW"),
                             {"balance": 0})["balance"])

            # ─── 2. 각 티커별 처리 ───────────────────────────
            for tkr in allowed:

                # 2‑A) 현재가
                p_now = price_cache.get(tkr) or current_price(tkr)
                if p_now is None:
                    continue

                # 2‑B) 잔고·수량 확인
                bal = next((b for b in balances if b["currency"] == tkr.split("-")[1]), None)
                q_prev = float(bal["balance"]) + float(bal["locked"]) if bal else 0.0

                # ─── 3. 포지션 진입 ────────────────────────
                if q_prev == 0:
                    if len([t for t, v in in_pos.items() if v]) >= MAX_COINS:
                        continue
                    if rsi(tkr) < RSI_THRESHOLD:
                        invest = krw * INITIAL_INVEST_RATIO
                        if invest >= 5_000 and place_buy(up, tkr, invest):
                            time.sleep(1)
                            bal = next(x for x in up.get_balances()
                                       if x["currency"] == tkr.split("-")[1])
                            q_prev = float(bal["balance"]) + float(bal["locked"])
                            avg_buy[tkr] = float(bal["avg_buy_price"])
                            in_pos[tkr]  = True
                            sell_uuid[tkr] = sell_limit(
                                up, tkr, q_prev,
                                avg_buy[tkr] * (1 + TARGET_PROFIT_RATE + 0.001)
                            )
                            last_add[tkr] = time.time()
                    continue  # 진입 처리 끝

                # ─── 4. 포지션 관리 ───────────────────────
                avg  = avg_buy[tkr]
                pnl  = (p_now - avg) / avg

                # 4‑A) 손절
                if pnl <= STOP_LOSS_RATE:
                    if sell_uuid.get(tkr):
                        cancel(up, sell_uuid[tkr])
                    sell_market(up, tkr, q_prev)
                    in_pos[tkr] = False
                    sell_uuid.pop(tkr, None)
                    continue

                # 4‑B) 정밀 추매
                if pnl <= MAINTAIN_PROFIT_RATE:
                    now = time.time()
                    if now - last_add.get(tkr, 0) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS:
                        if RSI_CUSTOM_TRIGGER and rsi(tkr) < RSI_CUSTOM_TRIGGER:

                            q_add  = q_add_needed(q_prev, avg, p_now, MAINTAIN_PROFIT_RATE)
                            invest = q_add * p_now

                            if invest < 5_000:
                                logging.debug(f"[PrecisionAD] {tkr} <5k skip")
                            elif krw < invest:
                                logging.info(
                                    f"[PrecisionAD] {tkr} need {invest:.0f}₩ > bal {krw:.0f}₩ → skip"
                                )
                            else:
                                if sell_uuid.get(tkr):
                                    cancel(up, sell_uuid[tkr])
                                if place_buy(up, tkr, invest):
                                    last_add[tkr] = now
                                    logging.info(f"[PrecisionAD] {tkr} +{invest:.0f}₩")

                                    # 새 평단·매도 주문
                                    bal = next(x for x in up.get_balances()
                                               if x["currency"] == tkr.split("-")[1])
                                    q_prev = float(bal["balance"]) + float(bal["locked"])
                                    avg    = float(bal["avg_buy_price"])
                                    avg_buy[tkr] = avg
                                    sell_uuid[tkr] = sell_limit(
                                        up, tkr, q_prev,
                                        avg * (1 + TARGET_PROFIT_RATE + 0.001)
                                    )

                # 4‑C) 매도 체결 확인
                if sell_uuid.get(tkr):
                    od = up.get_order(sell_uuid[tkr])
                    if od and od["state"] == "done":
                        in_pos[tkr] = False
                        sell_uuid.pop(tkr, None)
                        logging.info(f"[SELL DONE] {tkr}")

            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("사용자 종료")
    except Exception as e:
        generic_err(e)
    finally:
        streamer.stop()

# ─────────────────────────────────────────
if __name__ == "__main__":
    main()
