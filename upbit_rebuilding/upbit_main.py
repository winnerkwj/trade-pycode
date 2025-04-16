# ------------------------------------------------------------
# upbit_main.py
#  - 전체 프로그램 실행 메인
# ------------------------------------------------------------
import logging
import time
import pyupbit

# 모듈 import
from upbit_config import (
    ACCESS_KEY, SECRET_KEY,
    ALLOWED_TICKERS, MAX_COINS,
    RSI_THRESHOLD, RSI_THRESHOLD_ADDITIONAL,
    INITIAL_INVEST_RATIO, TARGET_PROFIT_RATE,
    STOP_LOSS_RATE, MAINTAIN_PROFIT_RATE,
    MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS,
    MAX_ADDITIONAL_BUYS,
    LOOP_INTERVAL
)
from upbit_utils import (
    get_current_price, get_rsi
)
from upbit_buy import place_buy_order
from upbit_sell import (
    place_limit_sell_order,
    place_market_sell_order,
    cancel_order
)
from upbit_exception import handle_general_exception

# 전역 상태관리 딕셔너리
in_position = {}               # 티커별 보유중 여부
avg_buy_price_holdings = {}    # 티커별 평균매수가
additional_buy_count = {}      # 티커별 추매 횟수
sell_order_uuid = {}           # 티커별 현재 걸려있는 매도주문 uuid
last_additional_buy_time = {}  # 티커별 마지막 추매 시각

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    # Upbit 객체 생성
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
    logging.info("===== Upbit 자동매매 프로그램 (동기식) 시작 =====")

    # 초기 잔고 파악 -> 이미 보유중인 코인 있으면 in_position 세팅
    init_balances = upbit.get_balances()
    for b in init_balances:
        cur = b['currency']
        if cur == 'KRW':
            continue
        balance = float(b['balance']) + float(b['locked'])
        if balance > 0:
            ticker = f"KRW-{cur}"
            in_position[ticker] = True
            avg_buy_price_holdings[ticker] = float(b['avg_buy_price'])
            additional_buy_count[ticker] = 0
            sell_order_uuid[ticker] = ""  
            last_additional_buy_time[ticker] = 0
            logging.info(f"[보유중] 티커={ticker}, 수량={balance}, 평단={b['avg_buy_price']}")

    # 메인 루프
    while True:
        try:
            # 현재 보유중인 종목 수
            holding_tickers = [t for t, v in in_position.items() if v]
            num_holdings = len(holding_tickers)

            for ticker in ALLOWED_TICKERS:
                current_price = get_current_price(ticker)
                if not current_price:
                    # 현재가 없으면 다음 티커로
                    continue

                # 잔고 확인
                balances = upbit.get_balances()
                balance_info = next((x for x in balances if x['currency'] == ticker.split('-')[1]), None)
                if balance_info:
                    hold_amount = float(balance_info['balance']) + float(balance_info['locked'])
                else:
                    hold_amount = 0.0

                if hold_amount <= 0:
                    # 미보유 상태
                    in_position[ticker] = False

                    # 종목 제한(MAX_COINS) 초과하면 신규매수 안함
                    if num_holdings >= MAX_COINS:
                        continue

                    # RSI 체크
                    rsi_val = get_rsi(ticker)
                    if rsi_val < RSI_THRESHOLD:
                        # 매수 조건 충족
                        krw_info = next((x for x in balances if x['currency'] == 'KRW'), None)
                        if krw_info:
                            krw_balance = float(krw_info['balance'])
                            # 최소 투자금
                            invest_amount = krw_balance * INITIAL_INVEST_RATIO
                            if invest_amount >= 5000:
                                # 매수 시도
                                success = place_buy_order(upbit, ticker, invest_amount)
                                if success:
                                    # 매수 체결 성공 시 다시 잔고 갱신
                                    time.sleep(1)
                                    new_balances = upbit.get_balances()
                                    new_info = next((x for x in new_balances if x['currency'] == ticker.split('-')[1]), None)
                                    if new_info:
                                        new_hold_amount = float(new_info['balance']) + float(new_info['locked'])
                                        avg_buy_price = float(new_info['avg_buy_price'])
                                        if new_hold_amount > 0:
                                            in_position[ticker] = True
                                            num_holdings += 1
                                            avg_buy_price_holdings[ticker] = avg_buy_price
                                            additional_buy_count[ticker] = 0
                                            last_additional_buy_time[ticker] = time.time()
                                            sell_order_uuid[ticker] = ""

                                            logging.info(f"[매수완료] {ticker}, 평단={avg_buy_price}")
                                            # 매수 후 곧바로 지정가 매도 주문
                                            target_price = avg_buy_price * (1 + TARGET_PROFIT_RATE + 0.001)
                                            new_uuid = place_limit_sell_order(upbit, ticker, new_hold_amount, target_price)
                                            sell_order_uuid[ticker] = new_uuid
                            else:
                                logging.info(f"[{ticker}] 매수 불가 (투자금 5000원 이하)")
                else:
                    # 보유 상태
                    in_position[ticker] = True
                    avg_buy_price = avg_buy_price_holdings.get(ticker, 0)
                    if avg_buy_price == 0:
                        # 혹시 평단정보가 없으면 balances에서 갱신
                        avg_buy_price = float(balance_info['avg_buy_price'])
                        avg_buy_price_holdings[ticker] = avg_buy_price

                    profit_rate = (current_price - avg_buy_price) / avg_buy_price

                    # 손절 조건
                    if profit_rate <= STOP_LOSS_RATE:
                        logging.info(f"[손절] {ticker} 현재수익률={profit_rate*100:.2f}% → 시장가 매도")
                        # 기존 매도 주문 있으면 취소
                        if sell_order_uuid[ticker]:
                            cancel_order(upbit, sell_order_uuid[ticker])
                            sell_order_uuid[ticker] = ""

                        time.sleep(0.5)
                        place_market_sell_order(upbit, ticker, hold_amount)
                        time.sleep(1)

                        # 보유 마킹 해제
                        in_position[ticker] = False
                        avg_buy_price_holdings[ticker] = 0
                        additional_buy_count[ticker] = 0
                        last_additional_buy_time[ticker] = 0
                        sell_order_uuid[ticker] = ""
                        continue

                    # 추매 조건
                    if profit_rate <= MAINTAIN_PROFIT_RATE:
                        # RSI 기준
                        rsi_val = get_rsi(ticker)
                        if rsi_val < RSI_THRESHOLD_ADDITIONAL:
                            # 일정 시간 이후 가능한지 체크
                            now = time.time()
                            if (now - last_additional_buy_time.get(ticker, 0)) > MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS:
                                if additional_buy_count[ticker] < MAX_ADDITIONAL_BUYS:
                                    # 추가매수 시도
                                    krw_info = next((x for x in balances if x['currency'] == 'KRW'), None)
                                    if krw_info:
                                        krw_balance = float(krw_info['balance'])
                                        invest_amount = krw_balance * INITIAL_INVEST_RATIO
                                        if invest_amount >= 5000:
                                            # 매도 주문 있으면 취소 후 매수
                                            if sell_order_uuid[ticker]:
                                                cancel_order(upbit, sell_order_uuid[ticker])
                                                sell_order_uuid[ticker] = ""
                                            success = place_buy_order(upbit, ticker, invest_amount)
                                            if success:
                                                additional_buy_count[ticker] += 1
                                                last_additional_buy_time[ticker] = time.time()

                                                # 매수 후 다시 평균단가 갱신
                                                time.sleep(1)
                                                new_balances = upbit.get_balances()
                                                new_info = next((x for x in new_balances if x['currency'] == ticker.split('-')[1]), None)
                                                if new_info:
                                                    new_avg_buy = float(new_info['avg_buy_price'])
                                                    new_hold_amount = float(new_info['balance']) + float(new_info['locked'])
                                                    avg_buy_price_holdings[ticker] = new_avg_buy
                                                    # 추매 후 바로 새 지정가 매도 주문
                                                    target_price = new_avg_buy * (1 + TARGET_PROFIT_RATE + 0.001)
                                                    new_uuid = place_limit_sell_order(upbit, ticker, new_hold_amount, target_price)
                                                    sell_order_uuid[ticker] = new_uuid
                                        else:
                                            logging.info(f"[{ticker}] 추가매수 불가 (투자금 5000원 이하)")
                                else:
                                    logging.info(f"[{ticker}] 최대 추매 횟수 초과")
                            else:
                                remain = MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS - (now - last_additional_buy_time.get(ticker, 0))
                                logging.info(f"[{ticker}] 추매 대기중... {remain:.1f}초 남음")

                    # 매도 주문 체결 확인
                    if sell_order_uuid[ticker]:
                        order_info = upbit.get_order(sell_order_uuid[ticker])
                        if order_info and order_info.get('state') == 'done':
                            # 매도 체결
                            logging.info(f"[매도 체결완료] {ticker}")
                            in_position[ticker] = False
                            avg_buy_price_holdings[ticker] = 0
                            additional_buy_count[ticker] = 0
                            last_additional_buy_time[ticker] = 0
                            sell_order_uuid[ticker] = ""
                        else:
                            # 너무 오래 안 체결되면 재주문
                            # (원한다면 특정 시간 지나면 취소 → 시장가 매도 로직)
                            pass
                    else:
                        # 지정가 매도 주문이 없는 경우 → 새로 주문
                        target_price = avg_buy_price * (1 + TARGET_PROFIT_RATE + 0.001)
                        new_uuid = place_limit_sell_order(upbit, ticker, hold_amount, target_price)
                        sell_order_uuid[ticker] = new_uuid

            # 루프 간격
            time.sleep(LOOP_INTERVAL)

        except KeyboardInterrupt:
            logging.info("사용자 종료 요청")
            break
        except Exception as e:
            handle_general_exception(e)

if __name__ == "__main__":
    main()
