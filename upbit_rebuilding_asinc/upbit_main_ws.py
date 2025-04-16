# upbit_rebuilding/upbit_main_ws.py
import logging, time, pyupbit

from .upbit_config   import *
from .upbit_utils    import get_current_price, get_rsi
from .upbit_buy      import place_buy_order
from .upbit_sell     import place_limit_sell_order, place_market_sell_order, cancel_order
from .upbit_exception import handle_general_exception
from .upbit_stream    import PriceStreamer, price_cache

# ------------- 전역 상태 ----------------
in_position               = {}
avg_buy_price_holdings    = {}
additional_buy_count      = {}
sell_order_uuid           = {}
last_additional_buy_time  = {}
# ----------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

    # 웹소켓 스트리머 시작
    streamer = PriceStreamer(ALLOWED_TICKERS)
    streamer.start()

    # 초기 잔고 → 보유 종목 인식
    for b in upbit.get_balances():
        if b['currency'] == 'KRW': continue
        tkr = f"KRW-{b['currency']}"
        qty = float(b['balance']) + float(b['locked'])
        if qty > 0:
            in_position[tkr] = True
            avg_buy_price_holdings[tkr] = float(b['avg_buy_price'])
            additional_buy_count[tkr] = 0
            sell_order_uuid[tkr] = ""
            last_additional_buy_time[tkr] = 0
            logging.info(f"[INIT HOLD] {tkr} {qty=} {avg_buy_price_holdings[tkr]=}")

    # ---------- 메인 루프 ----------
    try:
        while True:
            # 1) 구독 목록 최신화 (보유 + 관심)
            sub_codes = ALLOWED_TICKERS + [t for t, v in in_position.items() if v]
            streamer.update_tickers(sub_codes)

            # 2) 각 티커 의사결정
            balances = upbit.get_balances()
            krw_info = next((x for x in balances if x['currency'] == 'KRW'), None)
            krw_balance = float(krw_info['balance']) if krw_info else 0.0

            for ticker in ALLOWED_TICKERS:
                cur_price = price_cache.get(ticker) or get_current_price(ticker)
                if cur_price is None: continue

                bal_info = next((x for x in balances if x['currency'] == ticker.split('-')[1]), None)
                hold_amt = float(bal_info['balance']) + float(bal_info['locked']) if bal_info else 0.0

                # -------- 매수 진입 --------
                if hold_amt == 0:
                    in_position[ticker] = False
                    if len([t for t, v in in_position.items() if v]) >= MAX_COINS:
                        continue
                    if get_rsi(ticker) < RSI_THRESHOLD:
                        invest = krw_balance * INITIAL_INVEST_RATIO
                        if invest >= 5_000:
                            if place_buy_order(upbit, ticker, invest):
                                time.sleep(1)
                                balances = upbit.get_balances()  # 갱신
                                new_bal = next(x for x in balances if x['currency']==ticker.split('-')[1])
                                hold_amt = float(new_bal['balance']) + float(new_bal['locked'])
                                avg   = float(new_bal['avg_buy_price'])
                                in_position[ticker] = True
                                avg_buy_price_holdings[ticker] = avg
                                sell_price = avg * (1 + TARGET_PROFIT_RATE + 0.001)
                                sell_uuid  = place_limit_sell_order(upbit, ticker, hold_amt, sell_price)
                                sell_order_uuid[ticker] = sell_uuid
                                additional_buy_count[ticker] = 0
                                last_additional_buy_time[ticker] = time.time()
                    continue  # 매수 시도했으면 다음 티커

                # -------- 보유 상태 --------
                in_position[ticker] = True
                avg   = avg_buy_price_holdings.get(ticker, float(bal_info['avg_buy_price']))
                pnl   = (cur_price - avg) / avg

                # 2‑A) 손절
                if pnl <= STOP_LOSS_RATE:
                    if sell_order_uuid.get(ticker):
                        cancel_order(upbit, sell_order_uuid[ticker])
                    place_market_sell_order(upbit, ticker, hold_amt)
                    in_position[ticker] = False
                    sell_order_uuid[ticker] = ""
                    continue

                # 2‑B) 추매
                if pnl <= MAINTAIN_PROFIT_RATE:
                    now = time.time()
                    if (now - last_additional_buy_time.get(ticker, 0) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS
                        and additional_buy_count.get(ticker,0) < MAX_ADDITIONAL_BUYS
                        and get_rsi(ticker) < RSI_THRESHOLD_ADDITIONAL):
                        invest = krw_balance * INITIAL_INVEST_RATIO
                        if invest >= 5_000:
                            if sell_order_uuid.get(ticker):
                                cancel_order(upbit, sell_order_uuid[ticker])
                            if place_buy_order(upbit, ticker, invest):
                                additional_buy_count[ticker] += 1
                                last_additional_buy_time[ticker] = now
                                time.sleep(1)
                                balances = upbit.get_balances()
                                new_bal = next(x for x in balances if x['currency']==ticker.split('-')[1])
                                hold_amt = float(new_bal['balance']) + float(new_bal['locked'])
                                avg   = float(new_bal['avg_buy_price'])
                                avg_buy_price_holdings[ticker] = avg
                                sell_price = avg * (1 + TARGET_PROFIT_RATE + 0.001)
                                sell_order_uuid[ticker] = place_limit_sell_order(upbit, ticker, hold_amt, sell_price)

                # 2‑C) 매도 체결 확인
                if sell_order_uuid.get(ticker):
                    od = upbit.get_order(sell_order_uuid[ticker])
                    if od and od['state'] == 'done':
                        in_position[ticker] = False
                        sell_order_uuid[ticker] = ""
                        logging.info(f"[SELL DONE] {ticker}")

            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("사용자 종료")
    except Exception as e:
        handle_general_exception(e)
    finally:
        streamer.stop()

if __name__ == "__main__":
    main()
