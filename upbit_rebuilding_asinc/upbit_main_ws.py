# upbit_rebuilding_asinc/upbit_main_ws.py
"""
실시간 웹소켓 시세 + 정밀 추매 + 장애 복구
Top N(active) 교체 + Upbit 필터 (REST 30 req/sec)
"""
import logging
import time
import pyupbit
import requests
from itertools import islice

from .upbit_config    import *
from .upbit_utils     import current_price, rsi, public_api_limiter
from .upbit_buy       import place_buy
from .upbit_sell      import sell_limit, sell_market, cancel
from .upbit_stream    import PriceStreamer, price_cache
from .market_filter   import fetch_filtered_tickers
from .upbit_exception import generic_err

# ───────── 전역 상태 ─────────
in_pos, avg_buy     = {}, {}
sell_uuid, last_add = {}, {}

_UPBIT_ALL_URL    = "https://api.upbit.com/v1/market/all?isDetails=true"
_UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"

# ───────── Top N(active) 추출 ─────────
def _chunk(seq, size=100):
    it = iter(seq)
    while True:
        batch = list(islice(it, size))
        if not batch:
            break
        yield batch


def get_top_active_tickers(n: int) -> list[str]:
    """24h 거래대금 상위 N개 active 마켓 리턴."""
    rows = []
    try:
        # 1) pyupbit로 KRW 마켓 목록 조회 (active만 반환)
        with public_api_limiter:
            markets = pyupbit.get_tickers(fiat="KRW")
        logging.info(f"[DEBUG] Active markets count via pyupbit: {len(markets)}; sample: {markets[:n]}")

        # 2) 시세 조회 (100개씩 배치)
        for batch in _chunk(markets, 100):
            with public_api_limiter:
                resp = requests.get(
                    _UPBIT_TICKER_URL,
                    params={"markets": ",".join(batch)},
                    timeout=5
                ).json()
            if isinstance(resp, list):
                rows.extend(resp)

        logging.info(f"[DEBUG] Ticker rows count: {len(rows)}; candidate markets: {[r['market'] for r in rows[:n]]}")

        # 3) 거래대금 기준 정렬
        if rows:
            rows.sort(key=lambda x: x["acc_trade_price_24h"], reverse=True)
            return [r["market"] for r in rows[:n]]
    except Exception as e:
        generic_err(e)

    # fallback
    return markets[:n] if 'markets' in locals() and markets else ["KRW-BTC"]

# ───────── 보조 함수 ─────────
def q_add_needed(q_prev, c_prev, p_now, tgt):
    c_new = p_now / (1 + tgt)
    denom = c_new - p_now
    return max((q_prev * (c_prev - c_new)) / denom, 0) if denom > 0 else 0

# ───────── 장애 복구 ─────────
def restore_open_orders(up):
    try:
        for od in up.get_order("", state="wait"):
            if isinstance(od, dict) and od.get("side") == "ask" and od.get("ord_type") == "limit":
                sell_uuid[od["market"]] = od["uuid"]
                logging.info(f"[RESTORE] {od['market']} UUID={od['uuid']}")
    except Exception as e:
        generic_err(e)

# ───────── 메인 ─────────
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    up = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

    # 초기 Top N + 필터
    base_list = get_top_active_tickers(TOP_N_TICKERS)
    allowed   = fetch_filtered_tickers(base_list, TICKER_FILTER_INTERVAL)
    logging.info(f"[INIT] 매매 대상: {allowed}")

    streamer = PriceStreamer(allowed)
    streamer.start()

    # 장애 복구
    restore_open_orders(up)

    # 기존 잔고 반영
    for b in up.get_balances():
        if b['currency'] == 'KRW':
            continue
        tkr = f"KRW-{b['currency']}"
        qty = float(b['balance']) + float(b['locked'])
        if qty > 0:
            in_pos[tkr]   = True
            avg_buy[tkr]  = float(b['avg_buy_price'])
            last_add[tkr] = 0
            sell_uuid.setdefault(tkr, "")

    last_rotate = 0
    last_filter = 0

    try:
        while True:
            # Top N 갱신 (1시간)
            if time.time() - last_rotate > TICKER_ROTATE_INTERVAL:
                base_list = get_top_active_tickers(TOP_N_TICKERS)
                last_rotate = time.time()

            # 필터 갱신 (15분)
            if time.time() - last_filter > TICKER_FILTER_INTERVAL:
                new_allowed = fetch_filtered_tickers(base_list, TICKER_FILTER_INTERVAL)
                if new_allowed:
                    allowed = new_allowed
                    streamer.update(allowed + [t for t,v in in_pos.items() if v])
                else:
                    logging.warning("[FILTER] empty → 유지")
                last_filter = time.time()

            balances = up.get_balances()
            krw      = float(next((b for b in balances if b['currency']=='KRW'), {'balance':0})['balance'])

            for tkr in allowed:
                p_now = price_cache.get(tkr) or current_price(tkr)
                if p_now is None:
                    continue

                bal    = next((b for b in balances if b['currency']==tkr.split('-')[1]), None)
                q_prev = float(bal['balance']) + float(bal['locked']) if bal else 0.0

                # A) 진입
                if q_prev == 0:
                    if len([t for t,v in in_pos.items() if v]) >= MAX_COINS:
                        continue
                    if rsi(tkr) < RSI_THRESHOLD:
                        invest = krw * INITIAL_INVEST_RATIO
                        if invest >= 5_000 and place_buy(up, tkr, invest):
                            time.sleep(1)
                            bal    = next(x for x in up.get_balances() if x['currency']==tkr.split('-')[1])
                            q_prev = float(bal['balance']) + float(bal['locked'])
                            avg_buy[tkr] = float(bal['avg_buy_price'])
                            in_pos[tkr]  = True
                            sell_uuid[tkr] = sell_limit(
                                up, tkr, q_prev,
                                avg_buy[tkr] * (1 + TARGET_PROFIT_RATE + 0.001)
                            )
                            last_add[tkr] = time.time()
                    continue

                # B) 손절 & 정밀 추매 & 매도 체결 확인 (동일 구조 유지)
                avg = avg_buy[tkr]
                pnl = (p_now - avg) / avg
                if pnl <= STOP_LOSS_RATE:
                    if sell_uuid.get(tkr): cancel(up, sell_uuid[tkr])
                    sell_market(up, tkr, q_prev)
                    in_pos[tkr]=False; sell_uuid.pop(tkr,None)
                    continue
                if pnl <= MAINTAIN_PROFIT_RATE:
                    now = time.time()
                    if (now - last_add.get(tkr,0) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS
                        and RSI_CUSTOM_TRIGGER and rsi(tkr) < RSI_CUSTOM_TRIGGER):
                        q_add  = q_add_needed(q_prev, avg, p_now, MAINTAIN_PROFIT_RATE)
                        invest = q_add * p_now
                        if invest >= 5_000 and krw >= invest:
                            if sell_uuid.get(tkr): cancel(up, sell_uuid[tkr])
                            if place_buy(up, tkr, invest):
                                last_add[tkr]=now
                                bal = next(x for x in up.get_balances() if x['currency']==tkr.split('-')[1])
                                q_prev = float(bal['balance'])+float(bal['locked'])
                                avg    = float(bal['avg_buy_price'])
                                avg_buy[tkr]=avg
                                sell_uuid[tkr]=sell_limit(
                                    up, tkr, q_prev,
                                    avg * (1 + TARGET_PROFIT_RATE + 0.001)
                                )
                if sell_uuid.get(tkr):
                    od = up.get_order(sell_uuid[tkr])
                    if od and od['state']=='done':
                        in_pos[tkr]=False; sell_uuid.pop(tkr,None)
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
