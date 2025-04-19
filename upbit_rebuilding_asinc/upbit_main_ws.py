"""
웹소켓 실시간 시세 + ‘정밀 추매(Precision AD)’ 만 사용
  • 손익률 ≤ MAINTAIN_PROFIT_RATE (-0.55 %)
  • RSI < RSI_CUSTOM_TRIGGER (예 15)
  • 필요한 수량 Q_add 만큼 한 번에 매수
     – 최소 5 000원 미만이면 무시
     – 잔고 부족이면 스킵(프로그램 계속 동작)
"""
import logging, time, pyupbit
from .upbit_config  import *
from .upbit_utils   import get_current_price as current_price, rsi, tick_size
from .upbit_buy     import place_buy
from .upbit_sell    import sell_limit, sell_market, cancel
from .upbit_stream  import PriceStreamer, price_cache
from .upbit_exception import generic_err

# ---------- 전역 상태 ----------
in_pos, avg_buy      = {}, {}
sell_uuid, last_add  = {}, {}
# ------------------------------

def q_add_needed(q_prev, c_prev, p_now, tgt):
    """목표 손익률(tgt) 평단으로 맞추기 위한 추가 수량"""
    c_new = p_now / (1 + tgt)
    denom = c_new - p_now
    return max((q_prev * (c_prev - c_new)) / denom, 0) if denom > 0 else 0

def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    up = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
    streamer = PriceStreamer(ALLOWED_TICKERS); streamer.start()

    # ---- 초기 잔고 반영 ----
    for b in up.get_balances():
        if b['currency'] == 'KRW': continue
        t = f"KRW-{b['currency']}"
        q = float(b['balance']) + float(b['locked'])
        if q > 0:
            in_pos[t]   = True
            avg_buy[t]  = float(b['avg_buy_price'])
            sell_uuid[t] = ""
            last_add[t]  = 0

    try:
        while True:
            streamer.update(ALLOWED_TICKERS + [t for t,v in in_pos.items() if v])

            balances = up.get_balances()
            krw = float(next((b for b in balances if b['currency']=="KRW"),
                             {"balance":0})['balance'])

            for tkr in ALLOWED_TICKERS:
                p_now = price_cache.get(tkr) or current_price(tkr)
                if p_now is None:
                    continue

                bal = next((b for b in balances
                            if b['currency']==tkr.split('-')[1]), None)
                q_prev = float(bal['balance']) + float(bal['locked']) if bal else 0.0

                # ---------- A. 진입 ----------
                if q_prev == 0:
                    if len([t for t,v in in_pos.items() if v]) >= MAX_COINS:
                        continue
                    if rsi(tkr) < RSI_THRESHOLD:
                        invest = krw * INITIAL_INVEST_RATIO
                        if invest >= 5_000 and place_buy(up, tkr, invest):
                            time.sleep(1)
                            bal = next(x for x in up.get_balances()
                                       if x['currency']==tkr.split('-')[1])
                            q_prev = float(bal['balance']) + float(bal['locked'])
                            avg_buy[tkr] = float(bal['avg_buy_price'])
                            in_pos[tkr]  = True
                            sell_uuid[tkr] = sell_limit(
                                up, tkr, q_prev,
                                avg_buy[tkr]*(1+TARGET_PROFIT_RATE+0.001)
                            )
                            last_add[tkr] = time.time()
                    continue

                # ---------- B. 보유 ----------
                avg = avg_buy[tkr]
                pnl = (p_now - avg) / avg

                # 1) 손절
                if pnl <= STOP_LOSS_RATE:
                    if sell_uuid.get(tkr): cancel(up, sell_uuid[tkr])
                    sell_market(up, tkr, q_prev)
                    in_pos[tkr] = False; sell_uuid[tkr] = ""
                    continue

                # 2) 정밀 추매 조건
                if pnl <= MAINTAIN_PROFIT_RATE:
                    now = time.time()
                    if now - last_add.get(tkr,0) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS:
                        rsi_val = rsi(tkr)

                        if RSI_CUSTOM_TRIGGER and rsi_val < RSI_CUSTOM_TRIGGER:
                            q_add   = q_add_needed(q_prev, avg, p_now, MAINTAIN_PROFIT_RATE)
                            invest  = q_add * p_now

                            if invest < 5_000:
                                logging.info(f"[Precision AD] {tkr} 5천원 미만 → 패스")
                            elif krw < invest:
                                logging.info(
                                    f"[Precision AD] {tkr} 필요 {invest:.0f}₩ > 잔고 {krw:.0f}₩"
                                    " → 잔고 부족, 스킵"
                                )
                            else:
                                if sell_uuid.get(tkr): cancel(up, sell_uuid[tkr])
                                if place_buy(up, tkr, invest):
                                    last_add[tkr] = now
                                    logging.info(
                                        f"[Precision AD] {tkr} +{invest:.0f}₩ 매수 완료"
                                    )

                                    # 평단·매도 주문 갱신
                                    bal = next(x for x in up.get_balances()
                                               if x['currency']==tkr.split('-')[1])
                                    q_prev = float(bal['balance']) + float(bal['locked'])
                                    avg = float(bal['avg_buy_price'])
                                    avg_buy[tkr] = avg
                                    sell_uuid[tkr] = sell_limit(
                                        up, tkr, q_prev,
                                        avg*(1+TARGET_PROFIT_RATE+0.001)
                                    )

                # 3) 매도 체결 확인
                if sell_uuid.get(tkr):
                    od = up.get_order(sell_uuid[tkr])
                    if od and od['state'] == 'done':
                        in_pos[tkr] = False; sell_uuid[tkr] = ""
                        logging.info(f"[SELL DONE] {tkr}")

            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("사용자 종료")
    except Exception as e:
        generic_err(e)
    finally:
        streamer.stop()

if __name__ == "__main__":
    main()
