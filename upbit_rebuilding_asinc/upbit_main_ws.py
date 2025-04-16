# upbit_rebuilding/upbit_main_ws.py
"""
웹소켓 시세 + 2단계 추매 로직
  ① 소량 추매  : RSI < RSI_THRESHOLD_ADDITIONAL
  ② 정밀 추매  : RSI < RSI_CUSTOM_TRIGGER  &  손익률 <= MAINTAIN_PROFIT_RATE
                 → 목표 손익률(-0.55 %)에 맞춰 평단을 끌어올릴 수량만큼 일괄 매수
"""
import logging, time, pyupbit
from .upbit_config  import *
from .upbit_utils   import get_current_price, get_rsi
from .upbit_buy     import place_buy_order
from .upbit_sell    import place_limit_sell_order, place_market_sell_order, cancel_order
from .upbit_stream  import PriceStreamer, price_cache
from .upbit_exception import handle_general_exception

# ---------- 전역 상태 ----------
in_position, avg_buy = {}, {}
add_cnt, sell_uuid, last_add = {}, {}, {}
# ------------------------------

def q_add_needed(q_prev, c_prev, p_now, tgt)->float:
    """목표 손익률(tgt) 기준 새 평단으로 맞추기 위한 추가 수량"""
    c_new = p_now / (1 + tgt)           # 목표 평단
    denom = c_new - p_now
    if denom <= 0: 
        return 0.0
    return (q_prev * (c_prev - c_new)) / denom

def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
    streamer = PriceStreamer(ALLOWED_TICKERS); streamer.start()

    # ----- 기존 보유 잔고 반영 -----
    for b in upbit.get_balances():
        if b['currency'] == 'KRW': 
            continue
        tkr = f"KRW-{b['currency']}"
        qty = float(b['balance']) + float(b['locked'])
        if qty > 0:
            in_position[tkr] = True
            avg_buy[tkr]     = float(b['avg_buy_price'])
            add_cnt[tkr]     = 0
            sell_uuid[tkr]   = ""
            last_add[tkr]    = 0

    try:
        while True:
            streamer.update_tickers(
                ALLOWED_TICKERS + [t for t, v in in_position.items() if v]
            )

            balances = upbit.get_balances()
            krw_bal  = float(
                next((b for b in balances if b['currency'] == 'KRW'), 
                     {"balance": 0})['balance']
            )

            for tkr in ALLOWED_TICKERS:
                p_now = price_cache.get(tkr) or get_current_price(tkr)
                if p_now is None:
                    continue

                bal = next((b for b in balances 
                            if b['currency'] == tkr.split('-')[1]), None)
                q_prev = float(bal['balance']) + float(bal['locked']) if bal else 0.0

                # --------- A. 미보유 진입 ---------
                if q_prev == 0:
                    if len([t for t, v in in_position.items() if v]) >= MAX_COINS:
                        continue
                    if get_rsi(tkr) < RSI_THRESHOLD:
                        invest = krw_bal * INITIAL_INVEST_RATIO
                        if invest >= 5_000 and place_buy_order(upbit, tkr, invest):
                            time.sleep(1)
                            balances = upbit.get_balances()
                            bal = next(x for x in balances 
                                       if x['currency'] == tkr.split('-')[1])
                            q_prev = float(bal['balance']) + float(bal['locked'])
                            avg = float(bal['avg_buy_price'])
                            in_position[tkr] = True
                            avg_buy[tkr]     = avg
                            sell_uuid[tkr]   = place_limit_sell_order(
                                upbit, tkr, q_prev, avg * (1 + TARGET_PROFIT_RATE + 0.001)
                            )
                            add_cnt[tkr]  = 0
                            last_add[tkr] = time.time()
                    continue

                # --------- B. 보유 상태 ---------
                avg = avg_buy.get(tkr, float(bal['avg_buy_price']))
                pnl = (p_now - avg) / avg

                # (1) 손절
                if pnl <= STOP_LOSS_RATE:
                    if sell_uuid.get(tkr):
                        cancel_order(upbit, sell_uuid[tkr])
                    place_market_sell_order(upbit, tkr, q_prev)
                    in_position[tkr] = False
                    sell_uuid[tkr]   = ""
                    continue

                # (2) 추매 후보
                if pnl <= MAINTAIN_PROFIT_RATE:
                    now = time.time()
                    if now - last_add.get(tkr, 0) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS \
                       and add_cnt.get(tkr, 0) < MAX_ADDITIONAL_BUYS:

                        rsi_val = get_rsi(tkr)

                        # 2‑a) 소량 추매
                        if rsi_val < RSI_THRESHOLD_ADDITIONAL:
                            invest = krw_bal * INITIAL_INVEST_RATIO
                            if invest >= 5_000:
                                if sell_uuid.get(tkr):
                                    cancel_order(upbit, sell_uuid[tkr])
                                if place_buy_order(upbit, tkr, invest):
                                    add_cnt[tkr]  += 1
                                    last_add[tkr]  = now

                        # 2‑b) 정밀 추매 (Precision AD)
                        if RSI_CUSTOM_TRIGGER and rsi_val < RSI_CUSTOM_TRIGGER:
                            q_add = q_add_needed(q_prev, avg, p_now, MAINTAIN_PROFIT_RATE)
                            invest_needed = q_add * p_now
                            if invest_needed >= 5_000 and krw_bal >= invest_needed:
                                if sell_uuid.get(tkr):
                                    cancel_order(upbit, sell_uuid[tkr])
                                if place_buy_order(upbit, tkr, invest_needed):
                                    add_cnt[tkr]  += 1
                                    last_add[tkr]  = now
                                    logging.info(
                                        f"[Precision AD] {tkr} +{invest_needed:.0f} KRW"
                                    )

                        # 추매 후 상태 갱신
                        if last_add[tkr] == now:
                            time.sleep(1)
                            balances = upbit.get_balances()
                            bal = next(x for x in balances 
                                       if x['currency'] == tkr.split('-')[1])
                            q_prev = float(bal['balance']) + float(bal['locked'])
                            avg    = float(bal['avg_buy_price'])
                            avg_buy[tkr] = avg
                            sell_uuid[tkr] = place_limit_sell_order(
                                upbit, tkr, q_prev, avg * (1 + TARGET_PROFIT_RATE + 0.001)
                            )

                # (3) 매도 체결 확인
                if sell_uuid.get(tkr):
                    od = upbit.get_order(sell_uuid[tkr])
                    if od and od['state'] == 'done':
                        in_position[tkr] = False
                        sell_uuid[tkr]   = ""
                        logging.info(f"[SELL DONE] {tkr}")

            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("사용자 종료")
    except Exception as e:
        handle_general_exception(e)
    finally:
        streamer.stop()

if __name__ == "__main__":
    main()
