import time
import pyupbit
import requests
import logging
import json
import pandas as pd
from collections import defaultdict
import concurrent.futures

# ------------------------------------------------------------
# 업비트 API 키 파일 경로 설정 (사용자 환경에 맞게 수정)
# ------------------------------------------------------------
key_file_path = r'C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt'

# ------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------
# 제외할 종목 목록 & 소수점 제외 목록
# ------------------------------------------------------------
excluded_tickers = ['KRW-BTC','KRW-USDT','KRW-BTG','KRW-MOCA','KRW-SAND','KRW-GRT']
drop_point = ['KRW-BLUR','KRW-SAND','KRW-SEI','KRW-ALGO','KRW-POL','KRW-GRT']

# ------------------------------------------------------------
# 매매 전략에 필요한 파라미터
# ------------------------------------------------------------
rsi_period = 14
rsi_threshold = 10
rsi_threshold_additional = 55
initial_invest_ratio = 0.05
target_profit_rate = 0.0030
stop_loss_rate = -0.6
maintain_profit_rate = -0.0055
rsi_calculation_interval = 60
min_interval_between_additional_buys = 60
max_additional_buys = 1000

# ------------------------------------------------------------
# 보유상태 및 매매 관리용 딕셔너리
# ------------------------------------------------------------
last_additional_buy_time = defaultdict(lambda: 0)
holding_tickers = {}           # 티커별 보유 수량
avg_buy_price_holdings = {}    # 티커별 평균 매수가
in_position = defaultdict(bool)
additional_buy_count = defaultdict(int)

sell_order_uuid = defaultdict(lambda: None)
sell_order_time = defaultdict(lambda: None)

# 매수 시간 기록용
hold_start_time = {}

# ------------------------------------------------------------
# PyUpbit 객체 생성 (동기)
# ------------------------------------------------------------
with open(key_file_path, 'r') as f:
    access = f.readline().strip()
    secret = f.readline().strip()

upbit = pyupbit.Upbit(access, secret)

# ------------------------------------------------------------
# 동기식 RateLimiter (필요 시 사용)
#  - 완벽히 동일한 기능을 원하시면, 아래처럼 단순화
# ------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_calls, period=1.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    def acquire(self):
        now = time.time()
        # period 초 안에 일어난 호출만 유지
        self.calls = [t for t in self.calls if t > now - self.period]
        if len(self.calls) >= self.max_calls:
            sleep_time = self.calls[0] + self.period - now
            logging.debug(f"RateLimiter: 요청 초과. {sleep_time:.2f}초 대기.")
            time.sleep(sleep_time)
        self.calls.append(time.time())
    def __enter__(self):
        self.acquire()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# 원하는 만큼 RateLimiter 생성 (동기)
order_request_limiter = RateLimiter(max_calls=7, period=1.0)
non_order_request_limiter = RateLimiter(max_calls=25, period=1.0)

# ------------------------------------------------------------
# 간단 동기 래퍼: 잔고, 주문 등
# ------------------------------------------------------------
def get_balances():
    """동기식 잔고 조회"""
    with non_order_request_limiter:
        try:
            return upbit.get_balances()
        except requests.exceptions.RequestException as e:
            logging.error(f"get_balances() 오류: {e}")
            time.sleep(5)
            return []
        except Exception as e:
            logging.error(f"get_balances() 알 수 없는 오류: {e}")
            time.sleep(5)
            return []

def get_balance(ticker):
    """특정 ticker 잔고 조회 (동기)"""
    balances = get_balances()
    if ticker == "KRW-KRW":
        for b in balances:
            if b['currency'] == 'KRW':
                return float(b['balance'])
        return 0.0
    else:
        currency = ticker.split('-')[-1]
        for b in balances:
            if b['currency'] == currency:
                return float(b['balance'])
        return 0.0

def get_avg_buy_price(ticker):
    balances = get_balances()
    currency = ticker.split('-')[-1]
    for b in balances:
        if b['currency'] == currency:
            return float(b['avg_buy_price'])
    return 0.0

def buy_limit_order(ticker, price, volume):
    with order_request_limiter:
        return upbit.buy_limit_order(ticker, price, volume)

def sell_limit_order(ticker, price, volume):
    with order_request_limiter:
        return upbit.sell_limit_order(ticker, price, volume)

def sell_market_order(ticker, volume):
    with order_request_limiter:
        return upbit.sell_market_order(ticker, volume)

def cancel_order(uuid):
    with order_request_limiter:
        return upbit.cancel_order(uuid)

def get_waiting_orders():
    with non_order_request_limiter:
        return upbit.get_order("", state="wait")

def get_order(uuid):
    with non_order_request_limiter:
        return upbit.get_order(uuid)

# ------------------------------------------------------------
# 현재가/시세 관련 (동기)
# ------------------------------------------------------------
def get_current_price_sync(ticker):
    with non_order_request_limiter:
        try:
            return pyupbit.get_current_price(ticker)
        except requests.exceptions.RequestException as e:
            logging.error(f"현재가 조회 오류: {e}")
            time.sleep(3)
            return None
        except Exception as e:
            logging.error(f"{ticker} 현재가 조회 오류: {e}")
            time.sleep(3)
            return None

def get_ohlcv_sync(ticker, interval="minute1", count=200):
    with non_order_request_limiter:
        try:
            return pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        except Exception as e:
            logging.error(f"{ticker} OHLCV 조회 오류: {e}")
            time.sleep(3)
            return None

# ------------------------------------------------------------
# get_top_volume_tickers (동기)
#  - 24시간 거래대금 기준 상위 limit 종목 조회
# ------------------------------------------------------------
def get_top_volume_tickers(limit=60):
    logging.debug("상위 거래량 종목 조회 시작")
    with non_order_request_limiter:
        krw_tickers = pyupbit.get_tickers("KRW")
    url = "https://api.upbit.com/v1/ticker"
    params = {"markets": ",".join(krw_tickers)}
    try:
        with non_order_request_limiter:
            resp = requests.get(url, params=params)
        data = resp.json()
        data.sort(key=lambda x: x['acc_trade_price_24h'], reverse=True)
        result = []
        for x in data:
            if x['market'] not in excluded_tickers:
                result.append(x['market'])
            if len(result) >= limit:
                break
        logging.debug("상위 거래량 종목 조회 완료")
        return result
    except Exception as e:
        logging.error(f"get_top_volume_tickers 오류: {e}")
        return []

# ------------------------------------------------------------
# 호가단위 함수 (동기 그대로)
# ------------------------------------------------------------
def get_tick_size(price):
    if price >= 2000000:
        return 1000
    elif price >= 1000000:
        return 500
    elif price >= 500000:
        return 100
    elif price >= 100000:
        return 50
    elif price >= 10000:
        return 10
    elif price >= 1000:
        return 1
    elif price >= 100:
        return 0.1
    elif price >= 10:
        return 0.01
    elif price >= 1:
        return 0.001
    elif price >= 0.1:
        return 0.0001
    elif price >= 0.01:
        return 0.00001
    elif price >= 0.001:
        return 0.000001
    elif price >= 0.0001:
        return 0.0000001
    else:
        return 0.00000001

# ------------------------------------------------------------
# RSI 계산 (동기)
#  - rsi_cache / rsi_timestamp 로 캐싱해도 되고, 단순 호출 가능
# ------------------------------------------------------------
rsi_cache = {}
rsi_timestamp = {}

def get_rsi_sync(ticker, period=14):
    now = time.time()
    # 캐싱 로직 (원한다면 사용)
    if ticker in rsi_cache and now - rsi_timestamp.get(ticker, 0) < rsi_calculation_interval:
        return rsi_cache[ticker]

    df = get_ohlcv_sync(ticker, interval="minute1", count=period*2)
    if df is None or df.empty:
        logging.warning(f"{ticker} OHLCV 데이터 없음")
        return None

    close = df['close']
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    rsi_value = rsi_val.iloc[-1]

    rsi_cache[ticker] = rsi_value
    rsi_timestamp[ticker] = now
    return rsi_value

# ------------------------------------------------------------
# 주문 로직 (동기)
# ------------------------------------------------------------
def place_buy_order(ticker, krw_balance, invest_amount):
    """단순화: 1회 지정가 매수 시도 후 미체결 시 취소"""
    current_price = get_current_price_sync(ticker)
    if not current_price:
        logging.warning(f"{ticker} 매수가격 조회 실패")
        return

    volume = invest_amount / current_price
    logging.info(f"[매수 시도] {ticker}, 가격: {current_price}, 금액: {invest_amount}")
    order = buy_limit_order(ticker, current_price, volume)
    if not order or 'uuid' not in order:
        logging.warning(f"[매수 주문 실패] {ticker}")
        return

    time.sleep(1.0)
    # 체결확인
    order_info = get_order(order['uuid'])
    if order_info and order_info.get('state') == 'done':
        logging.info(f"[매수 체결 완료] {ticker}")
    else:
        logging.info(f"[매수 미체결 → 취소] {ticker}")
        cancel_order(order['uuid'])
        return

    # 보유상태 갱신
    in_position[ticker] = True
    bal = get_balance(ticker)
    holding_tickers[ticker] = bal
    avg_buy_price_holdings[ticker] = get_avg_buy_price(ticker)
    hold_start_time[ticker] = time.time()

    # 매수 후 지정가 매도 주문
    place_limit_sell_order(ticker)

def place_limit_sell_order(ticker):
    """목표 수익률 지정가 매도"""
    total_balance = get_balance(ticker)
    if total_balance <= 0:
        logging.info(f"[지정가 매도 실패] {ticker} 보유수량 없음")
        return

    avg_buy_price = get_avg_buy_price(ticker)
    target_price = avg_buy_price * (1 + target_profit_rate + 0.001)
    tick_size = get_tick_size(target_price)

    if ticker in drop_point:
        target_price = round((target_price // tick_size) * tick_size)
    else:
        target_price = (target_price // tick_size) * tick_size

    logging.info(f"[지정가 매도 주문] {ticker}, 가격={target_price}, 수량={total_balance}")
    # 기존 주문 취소(있다면)
    if sell_order_uuid[ticker]:
        cancel_order(sell_order_uuid[ticker])
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None

    order = sell_limit_order(ticker, target_price, total_balance)
    if not order or 'uuid' not in order:
        logging.warning(f"[지정가 매도 주문 실패] {ticker}")
        return
    sell_order_uuid[ticker] = order['uuid']
    sell_order_time[ticker] = time.time()

def place_market_sell_order(ticker):
    """시장가 매도 (손절 시 등)"""
    total_balance = get_balance(ticker)
    if total_balance <= 0:
        logging.info(f"[시장가 매도 실패] {ticker}, 보유수량 없음")
        return

    # 기존 매도 주문 있으면 취소
    if sell_order_uuid[ticker]:
        cancel_order(sell_order_uuid[ticker])
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None

    order = sell_market_order(ticker, total_balance)
    if not order or 'uuid' not in order:
        logging.warning(f"[시장가 매도 실패] {ticker}")
        return

    logging.info(f"[시장가 매도 체결] {ticker}, 수량={total_balance}")
    in_position[ticker] = False
    holding_tickers.pop(ticker, None)
    avg_buy_price_holdings.pop(ticker, None)
    additional_buy_count.pop(ticker, None)
    hold_start_time.pop(ticker, None)

# ------------------------------------------------------------
# 기존 대기 매도 주문 취소
# ------------------------------------------------------------
def cancel_existing_sell_orders():
    logging.info("기존 매도 주문 취소 진행...")
    orders = get_waiting_orders()
    if isinstance(orders, list):
        for od in orders:
            if not isinstance(od, dict):
                continue
            if od.get('side') == 'ask' and od.get('ord_type') == 'limit':
                uuid = od.get('uuid')
                market = od.get('market')
                if uuid:
                    logging.info(f"[매도취소] {market}, uuid={uuid}")
                    cancel_order(uuid)

# ------------------------------------------------------------
# 메인 루프 (동기)
#  - 1) 상위 거래량 종목 + 보유 종목 합쳐서 반복 조회
#  - 2) RSI, 현재가, 수익률 체크해 매수/매도
#  - 3) 일정 주기로 반복 (time.sleep)
# ------------------------------------------------------------
def main():
    logging.info("[메인 시작] 동기 방식 자동매매")

    # 1) 기존 매도 주문 취소
    cancel_existing_sell_orders()

    # 2) 보유 종목 초기화
    raw_balances = get_balances()
    for b in raw_balances:
        currency = b['currency']
        if currency == 'KRW':
            continue
        total_amt = float(b['balance']) + float(b['locked'])
        if total_amt <= 0:
            continue
        ticker = f"KRW-{currency}"
        in_position[ticker] = True
        holding_tickers[ticker] = total_amt
        abp = float(b['avg_buy_price'])
        avg_buy_price_holdings[ticker] = abp
        additional_buy_count[ticker] = 0
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None
        hold_start_time[ticker] = time.time()
        logging.info(f"[보유중] {ticker}, 수량={total_amt}, avg={abp}")

    # 메인 반복문
    last_ticker_update = 0
    ticker_update_interval = 300  # 5분마다 상위 거래량 갱신

    while True:
        try:
            now = time.time()
            if now - last_ticker_update > ticker_update_interval:
                top_tickers = get_top_volume_tickers(limit=60)
                last_ticker_update = now
            else:
                if 'top_tickers' not in locals():
                    top_tickers = get_top_volume_tickers(limit=60)

            # 모니터링 대상: 보유중인 티커 + 상위 거래량 티커
            all_tickers = set(top_tickers + list(holding_tickers.keys()))
            all_tickers = [t for t in all_tickers if t not in excluded_tickers]

            for ticker in all_tickers:
                # 현재가/RSI 조회
                current_price = get_current_price_sync(ticker)
                if current_price is None:
                    continue
                rsi_val = get_rsi_sync(ticker, rsi_period)

                balance = get_balance(ticker)
                if balance > 0:
                    in_position[ticker] = True
                    if ticker not in holding_tickers:
                        # 새로 보유된 경우(다른 곳에서 매수됐을 때?)
                        holding_tickers[ticker] = balance
                        avg_buy_price_holdings[ticker] = get_avg_buy_price(ticker)
                        additional_buy_count[ticker] = 0
                        hold_start_time[ticker] = time.time()
                        # 바로 지정가 매도 주문
                        place_limit_sell_order(ticker)
                    else:
                        # 기존 보유
                        abp = avg_buy_price_holdings.get(ticker, 0)
                        if abp <= 0:
                            continue
                        profit_rate = (current_price - abp) / abp

                        # 손절
                        if profit_rate <= stop_loss_rate:
                            logging.info(f"[손절] {ticker}, 수익률={profit_rate*100:.2f}%")
                            place_market_sell_order(ticker)
                            continue

                        # 추가매수
                        if profit_rate <= maintain_profit_rate:
                            logging.info(f"{ticker} 추가매수 라인 이하, 수익률={profit_rate*100:.2f}%")
                            if rsi_val is not None and rsi_val < rsi_threshold_additional:
                                elapsed = time.time() - last_additional_buy_time[ticker]
                                if elapsed >= min_interval_between_additional_buys and additional_buy_count[ticker] < max_additional_buys:
                                    krw_bal = get_balance("KRW-KRW")
                                    # 일반 매수금
                                    invest_amount = krw_bal * initial_invest_ratio
                                    if krw_bal < 55000:
                                        invest_amount = krw_bal * (initial_invest_ratio*2)

                                    if invest_amount > 5000 and krw_bal >= invest_amount:
                                        place_buy_order(ticker, krw_bal, invest_amount)
                                        additional_buy_count[ticker] += 1
                                        last_additional_buy_time[ticker] = time.time()
                                        hold_start_time[ticker] = time.time()
                                        # 평균단가 갱신 후 지정가 매도
                                        avg_buy_price_holdings[ticker] = get_avg_buy_price(ticker)
                                        place_limit_sell_order(ticker)
                                    else:
                                        logging.info(f"[추가매수 실패] 잔고 부족")
                                else:
                                    logging.debug(f"{ticker} 추가매수 대기중 or 최대횟수 초과")
                            else:
                                logging.debug(f"{ticker} RSI={rsi_val} 추가매수 불가")

                        # 매도주문 상태 확인
                        if sell_order_uuid[ticker]:
                            odinfo = get_order(sell_order_uuid[ticker])
                            if odinfo and odinfo.get('state') == 'done':
                                # 매도체결
                                logging.info(f"[매도체결] {ticker}")
                                in_position[ticker] = False
                                holding_tickers.pop(ticker, None)
                                avg_buy_price_holdings.pop(ticker, None)
                                additional_buy_count.pop(ticker, None)
                                hold_start_time.pop(ticker, None)
                                sell_order_uuid[ticker] = None
                                sell_order_time[ticker] = None
                            else:
                                # 10분 넘게 미체결 시 재주문 예시
                                if sell_order_time[ticker] and (time.time() - sell_order_time[ticker] > 600):
                                    logging.info(f"[매도 재주문] {ticker} 10분 이상 미체결")
                                    cancel_order(sell_order_uuid[ticker])
                                    sell_order_uuid[ticker] = None
                                    sell_order_time[ticker] = None
                                    place_limit_sell_order(ticker)
                        else:
                            # 보유중이나 매도주문 없으면 새로 지정가 주문
                            place_limit_sell_order(ticker)

                else:
                    # 미보유 종목 → 매수 판단
                    in_position[ticker] = False
                    if rsi_val is not None and rsi_val < rsi_threshold:
                        logging.info(f"[매수조건] {ticker}, RSI={rsi_val:.2f}")
                        bal_krw = get_balance("KRW-KRW")
                        invest_amount = bal_krw * initial_invest_ratio
                        if bal_krw < 55000:
                            invest_amount = bal_krw * (initial_invest_ratio*2)

                        if invest_amount > 5000:
                            place_buy_order(ticker, bal_krw, invest_amount)
                        else:
                            logging.info(f"[매수불가] 잔고 부족")

            time.sleep(1)  # 루프 간격 (1초 등 원하는 대로 설정)

        except KeyboardInterrupt:
            logging.info("[사용자 종료]")
            break
        except Exception as e:
            logging.error(f"[메인루프 오류] {e}", exc_info=True)
            time.sleep(5)
            continue

# ------------------------------------------------------------
# 실행부
# ------------------------------------------------------------
if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            logging.info("사용자 종료 요청")
            break
        except Exception as e:
            logging.error(f"[전체 예외발생] {e}", exc_info=True)
            logging.info("5초 후 재시작합니다.")
            time.sleep(5)
            continue
