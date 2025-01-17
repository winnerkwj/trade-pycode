import asyncio
import time
import pyupbit
import json
import pandas as pd
import websockets
import aiohttp
import logging
from collections import defaultdict
import concurrent.futures
import requests  # requests.exceptions 처리를 위함

# ------------------------------------------------------------
# 업비트 API 키 파일 경로 설정
# ------------------------------------------------------------
key_file_path = r'C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt'

# ------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------
# 제외할 종목 목록
# ------------------------------------------------------------
excluded_tickers = ['KRW-BTC', 'KRW-USDT', 'KRW-BTG','KRW-MOCA']

# ------------------------------------------------------------
# RateLimiter 클래스
# ------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_calls, period=1.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            self.calls = [call for call in self.calls if call > now - self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.period - now
                logging.debug(f"RateLimiter: 요청 초과. {sleep_time:.2f}초 대기합니다.")
                await asyncio.sleep(sleep_time)
            self.calls.append(now)
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        pass

# ------------------------------------------------------------
# 전역 실행 환경
# ------------------------------------------------------------
order_lock = asyncio.Lock()
executor = concurrent.futures.ThreadPoolExecutor()

# ------------------------------------------------------------
# 업비트 API 키 로딩
# ------------------------------------------------------------
with open(key_file_path, 'r') as file:
    access = file.readline().strip()
    secret = file.readline().strip()

upbit = pyupbit.Upbit(access, secret)

# ------------------------------------------------------------
# API 요청 제한기 생성
# ------------------------------------------------------------
order_request_limiter = RateLimiter(max_calls=7, period=1.0)       
non_order_request_limiter = RateLimiter(max_calls=25, period=1.0)
public_api_limiters = defaultdict(lambda: RateLimiter(max_calls=10, period=1.0))

# ------------------------------------------------------------
# balances 캐싱 관련 변수
# ------------------------------------------------------------
balances_cache = {}
balances_last_update = 0
balances_update_interval = 2.0

# ------------------------------------------------------------
# 잔고 정보 업데이트 함수
# ------------------------------------------------------------
async def update_balances_cache():
    global balances_cache, balances_last_update
    if time.time() - balances_last_update >= balances_update_interval:
        try:
            async with non_order_request_limiter:
                raw_balances = await upbit_get_balances_async()
            new_cache = {}
            for b in raw_balances:
                ticker = f"KRW-{b['currency']}" if b['currency'] != 'KRW' else 'KRW-KRW'
                balance = float(b['balance']) + float(b['locked'])
                new_cache[ticker] = {
                    'balance': balance,
                    'avg_buy_price': float(b['avg_buy_price'])
                }
            balances_cache = new_cache
            balances_last_update = time.time()
            logging.debug("balances_cache 갱신 완료")
        except Exception as e:
            logging.error(f"balances 갱신 중 오류: {e}", exc_info=True)

# ------------------------------------------------------------
# 상위 거래량 종목 조회 함수
# ------------------------------------------------------------
async def get_top_volume_tickers(limit=60):
    logging.debug("상위 거래량 종목 조회 시작")
    loop = asyncio.get_event_loop()
    tickers = await loop.run_in_executor(executor, pyupbit.get_tickers, "KRW")

    url = "https://api.upbit.com/v1/ticker"
    params = {'markets': ','.join(tickers)}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            data = await response.json()

    data.sort(key=lambda x: x['acc_trade_price_24h'], reverse=True)
    top_tickers = [x['market'] for x in data if x['market'] not in excluded_tickers]
    top_tickers = top_tickers[:limit]
    logging.debug("상위 거래량 종목 조회 완료")
    return top_tickers

# ------------------------------------------------------------
# 매매 전략에 필요한 파라미터
# ------------------------------------------------------------
rsi_period = 14
rsi_threshold = 11
rsi_threshold_additional = 55
initial_invest_ratio = 0.05
target_profit_rate = 0.0030
stop_loss_rate = -0.6
maintain_profit_rate = -0.0055
rsi_calculation_interval = 60
min_hold_time_for_additional_buy = 120
min_interval_between_additional_buys = 120

# ------------------------------------------------------------
# 종목별 상태 관리용 딕셔너리들
# ------------------------------------------------------------
last_additional_buy_time = defaultdict(lambda: 0)
hold_start_time = {}
additional_buy_count = defaultdict(int)
max_additional_buys = 1000
holding_tickers = {}
sell_order_uuid = defaultdict(lambda: None)
sell_order_time = defaultdict(lambda: None)
avg_buy_price_holdings = {}
in_position = defaultdict(bool)

# ------------------------------------------------------------
# get_tick_size 함수
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
# RSI 계산 관련 변수
# ------------------------------------------------------------
rsi_cache = {}
rsi_timestamp = {}

# ------------------------------------------------------------
# get_rsi 함수
# ------------------------------------------------------------
async def get_rsi(ticker):
    now = time.time()
    if ticker in rsi_cache and now - rsi_timestamp.get(ticker, 0) < rsi_calculation_interval:
        return rsi_cache[ticker]
    try:
        endpoint = 'ohlcv'
        async with public_api_limiters[endpoint]:
            df = await get_ohlcv_async(ticker, interval="minute1", count=rsi_period * 2)
        if df is None or df.empty:
            logging.warning(f"{ticker} 데이터 없음")
            return None
        close = df['close']
        delta = close.diff().dropna()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
        avg_loss = loss.ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))
        rsi_value = rsi_val.iloc[-1]
        rsi_cache[ticker] = rsi_value
        rsi_timestamp[ticker] = now
        return rsi_value
    except Exception as e:
        logging.error(f"{ticker} RSI 계산 오류: {e}", exc_info=True)
        return None

# ------------------------------------------------------------
# get_ohlcv_async 함수
# ------------------------------------------------------------
async def get_ohlcv_async(ticker, interval="minute1", count=200):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, pyupbit.get_ohlcv, ticker, interval, count)

# ------------------------------------------------------------
# upbit_get_balance_async 함수
# ------------------------------------------------------------
async def upbit_get_balance_async(ticker):
    await update_balances_cache()
    return balances_cache.get(ticker, {}).get('balance', 0.0)

# ------------------------------------------------------------
# upbit_get_avg_buy_price_from_cache 함수
# ------------------------------------------------------------
async def upbit_get_avg_buy_price_from_cache(ticker):
    await update_balances_cache()
    return balances_cache.get(ticker, {}).get('avg_buy_price', 0.0)

# ------------------------------------------------------------
# 업비트 API 래퍼 함수들
# ------------------------------------------------------------
async def upbit_get_order_async(uuid):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.get_order, uuid)

async def upbit_buy_limit_order_async(ticker, price, volume):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.buy_limit_order, ticker, price, volume)

async def upbit_sell_limit_order_async(ticker, price, volume):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.sell_limit_order, ticker, price, volume)

async def upbit_sell_market_order_async(ticker, volume):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.sell_market_order, ticker, volume)

async def upbit_cancel_order_async(uuid):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.cancel_order, uuid)

async def upbit_get_balances_async():
    loop = asyncio.get_event_loop()
    # 네트워크 오류 / ConnectTimeout 대비
    try:
        return await loop.run_in_executor(executor, upbit.get_balances)
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        logging.error(f"balances 조회 Timeout 오류: {e}", exc_info=True)
        await asyncio.sleep(5)  # 잠시 대기 후 재시도
        return await loop.run_in_executor(executor, upbit.get_balances)
    except Exception as e:
        logging.error(f"balances 조회 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return await loop.run_in_executor(executor, upbit.get_balances)

async def upbit_get_order_list_async(state='wait'):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.get_order, "", state)

async def get_avg_buy_price_from_balances(ticker):
    return await upbit_get_avg_buy_price_from_cache(ticker)

# ------------------------------------------------------------
# place_buy_order 함수
# ------------------------------------------------------------
async def place_buy_order(ticker, krw_balance, invest_amount):
    logging.debug(f"{ticker} 매수 주문 시작. 투자액: {invest_amount}")
    max_attempts = 1
    for attempt in range(1, max_attempts + 1):
        endpoint = 'current_price'
        async with public_api_limiters[endpoint]:
            current_price = await get_current_price_async(ticker)
        try:
            async with order_request_limiter:
                order = await upbit_buy_limit_order_async(ticker, current_price, invest_amount / current_price)
            logging.info(f"{ticker} 매수 주문 시도 {attempt}회 - 가격: {current_price}, 금액: {invest_amount}")
            await asyncio.sleep(1)
            async with non_order_request_limiter:
                order_info = await upbit_get_order_async(order['uuid'])
            if order_info and order_info.get('state') == 'done':
                logging.info(f"[매수 체결 완료] {ticker} 가격: {current_price}, 매수 금액: {invest_amount}")
                for _ in range(5):
                    await asyncio.sleep(1)
                    balance = await upbit_get_balance_async(ticker)
                    if balance > 0:
                        logging.info(f"{ticker} 잔고 업데이트 완료: {balance}")
                        break
                else:
                    logging.warning(f"{ticker} 잔고 업데이트 지연")
                in_position[ticker] = True
                holding_tickers[ticker] = balance
                hold_start_time[ticker] = time.time()
                avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                if avg_buy_price is not None:
                    avg_buy_price_holdings[ticker] = avg_buy_price
                await place_limit_sell_order(ticker)
                return
            else:
                logging.info(f"{ticker} 매수 미체결 - 주문 취소 후 재시도")
                async with non_order_request_limiter:
                    await upbit_cancel_order_async(order['uuid'])
                await asyncio.sleep(0.5)
        except Exception as e:
            logging.error(f"{ticker} 매수 주문 실패: {e}", exc_info=True)
            await asyncio.sleep(0.5)
    logging.error(f"{ticker} 매수 실패 - 최대 시도 횟수 초과")

# ------------------------------------------------------------
# place_limit_sell_order 함수
# ------------------------------------------------------------
async def place_limit_sell_order(ticker):
    logging.debug(f"{ticker} 지정가 매도 주문 시작")
    async with non_order_request_limiter:
        total_balance = await upbit_get_balance_async(ticker)
        avg_buy_price = await get_avg_buy_price_from_balances(ticker)

    if total_balance <= 0:
        logging.info(f"{ticker} 보유 수량 없음. 매도 불가.")
        return

    target_price = float(avg_buy_price) * (1 + target_profit_rate + 0.001)
    tick_size = get_tick_size(target_price)
    target_price = (target_price // tick_size) * tick_size

    try:
        async with order_request_limiter:
            if sell_order_uuid[ticker]:
                logging.info(f"{ticker} 기존 매도 주문 취소 시도")
                try:
                    await upbit_cancel_order_async(sell_order_uuid[ticker])
                except Exception as e:
                    logging.error(f"{ticker} 매도 주문 취소 실패: {e}", exc_info=True)
                sell_order_uuid[ticker] = None
                sell_order_time[ticker] = None

            order = await upbit_sell_limit_order_async(ticker, target_price, total_balance)
            if not isinstance(order, dict):
                # 매도 응답이 dict가 아닐 수 있으므로 예외 처리
                logging.error(f"{ticker} 매도 응답이 dict가 아님: {order}")
                return

            # 여기서 'uuid' 키가 없을 경우 KeyError 발생할 수 있음
            sell_order_uuid[ticker] = order['uuid']
            sell_order_time[ticker] = time.time()
            logging.info(f"{ticker} 지정가 매도 주문 실행 - 가격: {target_price}, 수량: {total_balance}")
    except Exception as e:
        logging.error(f"{ticker} 지정가 매도 주문 실패: {e}", exc_info=True)

# ------------------------------------------------------------
# place_market_sell_order 함수
# ------------------------------------------------------------
async def place_market_sell_order(ticker):
    logging.debug(f"{ticker} 시장가 매도 주문 시작")
    async with non_order_request_limiter:
        total_balance = await upbit_get_balance_async(ticker)

    if total_balance <= 0:
        logging.info(f"{ticker} 보유 수량 없음. 시장가 매도 불가")
        return

    try:
        async with order_request_limiter:
            order = await upbit_sell_market_order_async(ticker, total_balance)
        if not isinstance(order, dict):
            logging.error(f"{ticker} 시장가 매도 응답이 dict가 아님: {order}")
            return
        logging.info(f"[매도 체결 완료] {ticker} 시장가 매도 - 수량: {total_balance}")
    except Exception as e:
        logging.error(f"{ticker} 시장가 매도 주문 실패: {e}", exc_info=True)

# ------------------------------------------------------------
# cancel_existing_sell_orders 함수
# ------------------------------------------------------------
async def cancel_existing_sell_orders():
    logging.info("기존 지정가 매도 주문 취소 진행...")
    async with non_order_request_limiter:
        orders = await upbit_get_order_list_async(state='wait')

    if isinstance(orders, list):
        for order in orders:
            # 주문 정보가 str로 떨어지는 경우 방어
            if not isinstance(order, dict):
                logging.warning(f"주문 정보가 dict가 아님: {order}")
                continue
            if order.get('side') == 'ask' and order.get('ord_type') == 'limit':
                uuid = order.get('uuid')
                market = order.get('market')
                if not uuid or not market:
                    continue
                logging.info(f"{market} 매도 주문 취소 진행")
                try:
                    async with order_request_limiter:
                        await upbit_cancel_order_async(uuid)
                    logging.info(f"{market} 매도 주문 취소 완료")
                except Exception as e:
                    logging.error(f"{market} 매도 주문 취소 실패: {e}", exc_info=True)
    else:
        logging.warning("미체결 주문이 없거나 조회 실패")

# ------------------------------------------------------------
# watch_price 함수
# ------------------------------------------------------------
async def watch_price():
    url = "wss://api.upbit.com/websocket/v1"
    previous_prices = {}
    previous_profit_rates = {}
    last_update = 0
    update_interval = 7200
    last_log_time = time.time()

    while True:
        # 모든 예외를 잡아 무한루프 안에서 재시도 → 네트워크 불안 시 종료되지 않도록
        try:
            if time.time() - last_update >= update_interval:
                logging.info("상위 거래량 종목 리스트 갱신 중...")
                tickers = await get_top_volume_tickers()
                last_update = time.time()
            else:
                # 이전 루프에서 이미 tickers를 가져왔다면 재사용
                if 'tickers' not in locals():
                    tickers = await get_top_volume_tickers()

            all_tickers = list(set(tickers + list(holding_tickers.keys())))
            all_tickers = [ticker for ticker in all_tickers if ticker not in excluded_tickers]

            logging.debug(f"웹소켓 연결 시도 - 종목 수: {len(all_tickers)}")

            # 웹소켓 연결
            async with websockets.connect(url, ping_interval=60, ping_timeout=10) as websocket:
                subscribe_data = [
                    {"ticket": "test"},
                    {"type": "ticker", "codes": all_tickers, "isOnlyRealtime": True},
                    {"format": "SIMPLE"}
                ]
                await websocket.send(json.dumps(subscribe_data))
                logging.info("웹소켓 구독 요청 완료")

                while True:
                    try:
                        data = await asyncio.wait_for(websocket.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        logging.warning("30초간 데이터 수신 없음, 재연결 시도")
                        break

                    if time.time() - last_log_time > 30:
                        logging.debug("watch_price 루프 정상 동작 중...")
                        last_log_time = time.time()

                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        logging.error("웹소켓 데이터 파싱 오류", exc_info=True)
                        continue

                    if not isinstance(data, dict):
                        logging.debug(f"웹소켓 데이터가 dict 아님: {data}")
                        continue

                    if 'cd' not in data or 'tp' not in data:
                        logging.debug("웹소켓 데이터에 cd나 tp 키가 없음")
                        continue

                    ticker = data['cd']
                    current_price = data['tp']

                    # RSI 계산
                    rsi = await get_rsi(ticker)
                    rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"

                    # 실시간 가격 로그
                    if ticker not in previous_prices or previous_prices[ticker] != current_price:
                        logging.info(f"{ticker} 실시간 가격: {current_price}, RSI: {rsi_str}")
                        previous_prices[ticker] = current_price

                    # 잔고 조회
                    total_balance = await upbit_get_balance_async(ticker)

                    # 보유 종목 로직
                    if total_balance > 0:
                        in_position[ticker] = True
                        if ticker not in holding_tickers:
                            holding_tickers[ticker] = total_balance
                            hold_start_time[ticker] = time.time()
                            additional_buy_count[ticker] = 0
                            sell_order_uuid[ticker] = None
                            sell_order_time[ticker] = None
                            avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                            if avg_buy_price is not None:
                                avg_buy_price_holdings[ticker] = avg_buy_price
                            else:
                                logging.warning(f"{ticker} 평균 매수가 알 수 없음")
                                continue
                            await place_limit_sell_order(ticker)
                        else:
                            # 기존 보유
                            avg_buy_price = avg_buy_price_holdings.get(ticker)
                            if avg_buy_price is None:
                                logging.warning(f"{ticker} 평균 매수가를 알 수 없어 수익률 계산 불가")
                                continue

                            profit_rate = (current_price - avg_buy_price) / avg_buy_price
                            if ticker not in previous_profit_rates or abs(previous_profit_rates[ticker] - profit_rate) >= 0.0001:
                                logging.info(f"{ticker} 보유: {total_balance}, 수익률: {profit_rate*100:.2f}%")
                                previous_profit_rates[ticker] = profit_rate

                            # 손절 조건
                            if profit_rate <= stop_loss_rate:
                                logging.info(f"{ticker} 손절 조건 충족")
                                if sell_order_uuid[ticker]:
                                    logging.info(f"{ticker} 매도 주문 취소 진행")
                                    try:
                                        async with non_order_request_limiter:
                                            await upbit_cancel_order_async(sell_order_uuid[ticker])
                                        sell_order_uuid[ticker] = None
                                        sell_order_time[ticker] = None
                                    except Exception as e:
                                        logging.error(f"{ticker} 매도 주문 취소 실패: {e}", exc_info=True)
                                await place_market_sell_order(ticker)
                                in_position[ticker] = False
                                holding_tickers.pop(ticker, None)
                                avg_buy_price_holdings.pop(ticker, None)
                                additional_buy_count.pop(ticker, None)
                                hold_start_time.pop(ticker, None)
                                sell_order_uuid.pop(ticker, None)
                                sell_order_time.pop(ticker, None)
                                continue

                            # 추가매수 조건
                            elif profit_rate <= maintain_profit_rate:
                                logging.info(f"{ticker} 수익률 추가매수 기준 이하")
                                current_time = time.time()
                                elapsed_time = current_time - last_additional_buy_time.get(ticker, 0)
                                if elapsed_time >= min_interval_between_additional_buys:
                                    if additional_buy_count[ticker] < max_additional_buys:
                                        if rsi is not None and rsi < rsi_threshold_additional:
                                            logging.info(f"{ticker} 추가 매수 진행")
                                            krw_balance = await upbit_get_balance_async("KRW-KRW")
                                            invest_amount = krw_balance * initial_invest_ratio
                                            fee = invest_amount * 0.0005
                                            total_invest_amount = invest_amount + fee
                                            if total_invest_amount > 5000 and krw_balance >= total_invest_amount:
                                                await place_buy_order(ticker, krw_balance, invest_amount)
                                                additional_buy_count[ticker] += 1
                                                last_additional_buy_time[ticker] = time.time()
                                                hold_start_time[ticker] = time.time()
                                                avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                                                if avg_buy_price is not None:
                                                    avg_buy_price_holdings[ticker] = avg_buy_price
                                                await place_limit_sell_order(ticker)
                                            else:
                                                logging.info(f"{ticker} 추가 매수 실패 - 잔고 부족")
                                        else:
                                            logging.info(f"{ticker} RSI 추가매수 기준 미충족")
                                    else:
                                        logging.info(f"{ticker} 최대 추가매수 횟수 초과")
                                else:
                                    remaining_interval = min_interval_between_additional_buys - elapsed_time
                                    logging.info(f"{ticker} 추가 매수 대기 중: {remaining_interval:.2f}초 남음")

                            # 매도 주문 상태 체크
                            if sell_order_uuid[ticker]:
                                async with non_order_request_limiter:
                                    try:
                                        order_info = await upbit_get_order_async(sell_order_uuid[ticker])
                                    except Exception as e:
                                        logging.error(f"{ticker} 매도 주문 조회 실패: {e}", exc_info=True)
                                        order_info = None
                                if order_info and order_info.get('state') == 'done':
                                    logging.info(f"[매도 체결 완료] {ticker} 지정가 매도")
                                    in_position[ticker] = False
                                    holding_tickers.pop(ticker, None)
                                    avg_buy_price_holdings.pop(ticker, None)
                                    additional_buy_count.pop(ticker, None)
                                    hold_start_time.pop(ticker, None)
                                    sell_order_uuid[ticker] = None
                                    sell_order_time[ticker] = None
                                    continue
                                else:
                                    if sell_order_time[ticker] and time.time() - sell_order_time[ticker] > 600:
                                        logging.info(f"{ticker} 매도 주문 장기 미체결, 재주문 시도")
                                        try:
                                            async with non_order_request_limiter:
                                                await upbit_cancel_order_async(sell_order_uuid[ticker])
                                            sell_order_uuid[ticker] = None
                                            sell_order_time[ticker] = None
                                            await place_limit_sell_order(ticker)
                                        except Exception as e:
                                            logging.error(f"{ticker} 매도 재주문 실패: {e}", exc_info=True)
                            else:
                                logging.info(f"{ticker} 보유 중이나 매도 주문 없음, 매도 주문 실행")
                                await place_limit_sell_order(ticker)

                    else:
                        # 미보유 종목 → 매수 조건 체크
                        in_position[ticker] = False
                        if ticker in excluded_tickers:
                            logging.debug(f"{ticker} 제외 종목. 매수 안함")
                            continue
                        if rsi is not None and rsi < rsi_threshold:
                            logging.debug(f"{ticker} 매수 조건 충족. Lock 획득 시도")
                            async with order_lock:
                                logging.debug(f"{ticker} order_lock 획득")
                                balance = await upbit_get_balance_async(ticker)
                                if balance == 0:
                                    logging.info(f"{ticker} RSI: {rsi:.2f}")
                                    krw_balance = await upbit_get_balance_async("KRW-KRW")
                                    invest_amount = krw_balance * initial_invest_ratio
                                    if invest_amount > 5000:
                                        await place_buy_order(ticker, krw_balance, invest_amount)
                                    else:
                                        logging.info(f"{ticker} 매수 실패 - 잔고 부족")
                                logging.debug(f"{ticker} order_lock 해제")
                        else:
                            if rsi is not None and abs(rsi - rsi_threshold) <= 5:
                                logging.info(f"{ticker} RSI {rsi:.2f}, 임계값 근접하나 불충족")
                            else:
                                logging.debug(f"{ticker} RSI 조건 불충족, 매수 안함")

                    # 정해진 시간 경과 시 웹소켓 종료 후 종목 리스트 갱신
                    if time.time() - last_update >= update_interval:
                        logging.info("종목 리스트 갱신 위해 웹소켓 종료")
                        break

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.InvalidStatusCode,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout) as net_err:
            logging.warning(f"네트워크/웹소켓 오류 발생. 재시도: {net_err}", exc_info=True)
            await asyncio.sleep(5)
            continue
        except Exception as e:
            logging.error(f"watch_price 예기치 못한 오류: {e}", exc_info=True)
            await asyncio.sleep(5)
            continue

        # 여기까지 문제가 없었으면, 다음 루프로(웹소켓 끊고 다시 연결)
        await asyncio.sleep(1)

# ------------------------------------------------------------
# get_current_price_async 함수
# ------------------------------------------------------------
async def get_current_price_async(ticker):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(executor, pyupbit.get_current_price, ticker)
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        logging.error(f"{ticker} 현재가 조회 Timeout: {e}", exc_info=True)
        await asyncio.sleep(5)
        return None
    except Exception as e:
        logging.error(f"{ticker} 현재가 조회 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return None

# ------------------------------------------------------------
# get_all_valid_tickers 함수
# ------------------------------------------------------------
async def get_all_valid_tickers():
    loop = asyncio.get_event_loop()
    try:
        valid_tickers = await loop.run_in_executor(executor, pyupbit.get_tickers, "KRW")
        return valid_tickers
    except Exception as e:
        logging.error(f"전체 티커 조회 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return []

# ------------------------------------------------------------
# main 함수
# ------------------------------------------------------------
async def main():
    logging.info("메인 함수 시작")
    await cancel_existing_sell_orders()

    valid_tickers = await get_all_valid_tickers()
    logging.info(f"유효한 종목 목록: {valid_tickers}")

    async with non_order_request_limiter:
        raw_balances = await upbit_get_balances_async()

    logging.info(f"잔고 정보: {raw_balances}")

    await update_balances_cache()

    for balance_info in raw_balances:
        currency = balance_info['currency']
        if currency == 'KRW':
            continue
        amount = float(balance_info['balance'])
        locked = float(balance_info['locked'])
        total_amount = amount + locked
        if total_amount <= 0:
            continue

        ticker = f"KRW-{currency}"
        if ticker not in valid_tickers:
            logging.warning(f"{ticker} 유효하지 않은 종목")
            continue

        in_position[ticker] = True
        holding_tickers[ticker] = total_amount
        hold_start_time[ticker] = time.time()
        additional_buy_count[ticker] = 0
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None
        avg_buy_price = float(balance_info['avg_buy_price'])
        avg_buy_price_holdings[ticker] = avg_buy_price
        logging.info(f"기존 보유 종목: {ticker}, 수량: {total_amount}, 평균가: {avg_buy_price}")

        if locked > 0:
            async with non_order_request_limiter:
                orders = await upbit_get_order_list_async(state='wait')
            if isinstance(orders, list):
                for order in orders:
                    if not isinstance(order, dict):
                        continue
                    if order.get('market') == ticker and order.get('side') == 'ask':
                        sell_order_uuid[ticker] = order.get('uuid')
                        sell_order_time[ticker] = time.time()
                        logging.info(f"{ticker} 기존 매도 주문 발견 - UUID: {sell_order_uuid[ticker]}")
                        break

        endpoint = 'current_price'
        try:
            async with public_api_limiters[endpoint]:
                current_price = await get_current_price_async(ticker)
        except Exception as e:
            logging.error(f"{ticker} 현재가격 조회 오류: {e}", exc_info=True)
            continue

        if current_price is None:
            logging.warning(f"{ticker} 현재 가격 불러오기 실패")
            continue

        profit_rate = (current_price - avg_buy_price) / avg_buy_price
        logging.info(f"{ticker} 초기 수익률: {profit_rate*100:.2f}%")

        if profit_rate <= maintain_profit_rate:
            logging.info(f"{ticker} 수익률 추가 매수 기준 이하")
            rsi = await get_rsi(ticker)
            if rsi is not None and rsi < rsi_threshold_additional:
                logging.info(f"{ticker} RSI {rsi:.2f}, 추가 매수 진행")
                krw_balance = await upbit_get_balance_async("KRW-KRW")
                invest_amount = krw_balance * initial_invest_ratio
                fee = invest_amount * 0.0005
                total_invest_amount = invest_amount + fee
                if total_invest_amount > 5000 and krw_balance >= total_invest_amount:
                    await place_buy_order(ticker, krw_balance, invest_amount)
                    additional_buy_count[ticker] += 1
                    last_additional_buy_time[ticker] = time.time()
                    hold_start_time[ticker] = time.time()
                    avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                    if avg_buy_price is not None:
                        avg_buy_price_holdings[ticker] = avg_buy_price
                    await place_limit_sell_order(ticker)
                else:
                    logging.info(f"{ticker} 추가 매수 실패 - 잔고 부족")
            else:
                logging.info(f"{ticker} RSI 기준 미충족, 추가 매수 안함")
        else:
            logging.info(f"{ticker} 수익률 추가 매수 기준 아님, 매도 주문 실행")
            await place_limit_sell_order(ticker)

    # 웹소켓 감시 시작
    await watch_price()

# ------------------------------------------------------------
# 프로그램 실행 시작점
#  - 네트워크 불안으로 main()이 종료되더라도 다시 실행하도록 전체 루프
# ------------------------------------------------------------
if __name__ == '__main__':
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("프로그램 종료 요청")
            break
        except Exception as e:
            # 심각한 예외 발생 시에도 계속 재시작
            logging.error(f"메인 함수 예외 발생: {e}", exc_info=True)
            logging.info("5초 후 재시작 시도...")
            time.sleep(5)
            continue
