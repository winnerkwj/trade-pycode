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
excluded_tickers = ['KRW-BTC', 'KRW-USDT', 'KRW-BTG', 'KRW-MOCA', 'KRW-VTHO', 'KRW-SBD']

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
            # 이전 호출 기록에서 (now - period) 이전 것은 제거
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
# (429를 많이 겪으면 숫자를 더 낮추세요.)
# ------------------------------------------------------------
order_request_limiter = RateLimiter(max_calls=5, period=1.0)      # 주문 관련
non_order_request_limiter = RateLimiter(max_calls=25, period=1.0) # balances 등 일반 요청
public_api_limiters = defaultdict(lambda: RateLimiter(max_calls=7, period=1.0))  # 티커 조회 등

# ------------------------------------------------------------
# 잔고 캐싱 관련 변수 (일부 주문에서는 실시간 잔고 확인을 위해 별도 함수 사용)
# ------------------------------------------------------------
balances_cache = {}
balances_last_update = 0
balances_update_interval = 2.0

# ------------------------------------------------------------
# 최소 매도 주문 기준 및 재시도 횟수
# ------------------------------------------------------------
MIN_SELL_ORDER_KRW = 5000
MAX_SELL_ATTEMPTS = 3

# ------------------------------------------------------------
# 매매 전략에 필요한 파라미터
# ------------------------------------------------------------
rsi_period = 14
rsi_threshold = 11
rsi_threshold_additional = 15
initial_invest_ratio = 0.1
target_profit_rate = 0.0030
stop_loss_rate = -0.6
maintain_profit_rate = -0.0055
rsi_calculation_interval = 60
min_hold_time_for_additional_buy = 600
min_interval_between_additional_buys = 600
max_additional_buys = 1000

# ------------------------------------------------------------
# 종목별 상태 관리용 딕셔너리들
# ------------------------------------------------------------
last_additional_buy_time = defaultdict(lambda: 0)
hold_start_time = {}
additional_buy_count = defaultdict(int)
holding_tickers = {}
sell_order_uuid = defaultdict(lambda: None)
sell_order_time = defaultdict(lambda: None)
avg_buy_price_holdings = {}
in_position = defaultdict(bool)

# ------------------------------------------------------------
# 웹소켓/시세 캐싱
# ------------------------------------------------------------
previous_prices = {}  # 웹소켓에서 받은 최신가 저장
previous_profit_rates = {}  # 최근 수익률 로그

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
# 잔고 정보 업데이트 함수 (캐시 업데이트)
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
# 실시간 잔고 새로고침 함수 (캐시 대신 API 직접 호출)
# ------------------------------------------------------------
async def get_fresh_balance(ticker):
    loop = asyncio.get_event_loop()
    try:
        raw_balances = await loop.run_in_executor(executor, upbit.get_balances)
        for b in raw_balances:
            t = f"KRW-{b['currency']}" if b['currency'] != 'KRW' else 'KRW-KRW'
            if t == ticker:
                return float(b['balance']) + float(b['locked'])
        return 0.0
    except Exception as e:
        logging.error(f"{ticker} 잔고 새로고침 오류: {e}", exc_info=True)
        return 0.0

# ------------------------------------------------------------
# get_top_volume_tickers 함수 (상위 거래량 종목)
# ------------------------------------------------------------
async def get_top_volume_tickers(limit=30):
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
# get_ohlcv_async 함수 (RSI 계산용)
# ------------------------------------------------------------
async def get_ohlcv_async(ticker, interval="minute1", count=200):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, pyupbit.get_ohlcv, ticker, interval, count)

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
# 다중 종목 시세 한 번에 조회 (HTTP 429 완화)
# ------------------------------------------------------------
async def get_current_prices_async(tickers: list[str]) -> dict:
    """
    tickers: ["KRW-BTC", "KRW-ETH", ...]
    반환 예: {"KRW-BTC": 41000000.0, "KRW-ETH": 3000000.0, ...}
    """
    if not tickers:
        return {}
    url = "https://api.upbit.com/v1/ticker"
    params = {"markets": ",".join(tickers)}

    results = {}
    async with public_api_limiters['ticker']:  # RateLimit
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=5) as resp:
                    if resp.status == 429:
                        # Too Many Requests
                        logging.warning(f"[get_current_prices_async] 429 오류. 잠시 후 재시도 권장.")
                        return {}
                    if resp.status != 200:
                        logging.error(f"현재가 조회 HTTP 오류 상태: {resp.status}, tickers={tickers}")
                        return {}
                    data = await resp.json()
                    # data는 보통 list
                    if not isinstance(data, list):
                        return {}
                    for item in data:
                        market = item.get("market")
                        trade_price = item.get("trade_price", 0)
                        if market and trade_price:
                            results[market] = trade_price
            except asyncio.TimeoutError:
                logging.error(f"현재가 조회 타임아웃: {tickers}")
            except Exception as e:
                logging.error(f"현재가 조회 예외: {e}", exc_info=True)
    return results

# ------------------------------------------------------------
# 웹소켓에서 받은 가격이 없는 경우 fallback
# ------------------------------------------------------------
async def get_price_from_cache_or_api(ticker):
    """
    1) 우선 previous_prices[ticker] (웹소켓 최신가) 사용
    2) 없다면 get_current_prices_async()로 한번에 가져오고 캐싱
    """
    # 1) 웹소켓 가격
    price = previous_prices.get(ticker)
    if price is not None:
        return price

    # 2) fallback - 단일 종목만이라도 조회
    fallback_result = await get_current_prices_async([ticker])
    if ticker in fallback_result:
        # fallback으로 가져온 가격도 캐싱(웹소켓 대용)
        previous_prices[ticker] = fallback_result[ticker]
        return fallback_result[ticker]

    return None  # 그래도 못 구하면 None

# ------------------------------------------------------------
# 업비트 API 래퍼 함수들
# ------------------------------------------------------------
async def upbit_get_balances_async():
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(executor, upbit.get_balances)
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        logging.error(f"balances 조회 Timeout 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return await loop.run_in_executor(executor, upbit.get_balances)
    except Exception as e:
        logging.error(f"balances 조회 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return await loop.run_in_executor(executor, upbit.get_balances)

async def upbit_get_order_list_async(state='wait'):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, upbit.get_order, "", state)

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

async def get_avg_buy_price_from_balances(ticker):
    await update_balances_cache()
    info = balances_cache.get(ticker)
    if info:
        return info.get('avg_buy_price', 0.0)
    return 0.0

async def upbit_get_balance_async(ticker):
    await update_balances_cache()
    info = balances_cache.get(ticker)
    if info:
        return info.get('balance', 0.0)
    return 0.0

# ------------------------------------------------------------
# cancel_existing_sell_orders (기존 지정가 매도 주문 모두 취소)
# ------------------------------------------------------------
async def cancel_existing_sell_orders():
    logging.info("기존 지정가 매도 주문 취소 진행...")
    async with non_order_request_limiter:
        orders = await upbit_get_order_list_async(state='wait')
    if isinstance(orders, list):
        for order in orders:
            if not isinstance(order, dict):
                logging.warning(f"주문 정보가 dict 아님: {order}")
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
# place_buy_order (매수 주문)
# ------------------------------------------------------------
async def place_buy_order(ticker, krw_balance, invest_amount):
    logging.debug(f"{ticker} 매수 주문 시작. 투자액: {invest_amount}")
    max_attempts = 1
    for attempt in range(1, max_attempts + 1):
        current_price = await get_price_from_cache_or_api(ticker)
        if current_price is None:
            logging.error(f"{ticker} 매수가 불가능한 현재가(None). 매수 스킵")
            return

        try:
            async with order_request_limiter:
                order = await upbit_buy_limit_order_async(
                    ticker,
                    current_price,
                    invest_amount / current_price
                )
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
# place_limit_sell_order (지정가 매도)
# ------------------------------------------------------------
async def place_limit_sell_order(ticker):
    logging.debug(f"{ticker} 지정가 매도 주문 시작")

    avg_buy_price = await get_avg_buy_price_from_balances(ticker)
    if avg_buy_price is None:
        logging.warning(f"{ticker} 평균 매수가 정보 없음. 매도 중단")
        return

    target_price = float(avg_buy_price) * (1 + target_profit_rate + 0.001)
    tick_size = get_tick_size(target_price)
    target_price = (target_price // tick_size) * tick_size

    current_balance = await get_fresh_balance(ticker)
    if current_balance <= 0:
        logging.info(f"{ticker} 실시간 잔고 부족. 매도 중단")
        return

    total_order_value = target_price * current_balance
    if total_order_value < MIN_SELL_ORDER_KRW:
        logging.warning(f"{ticker} 매도 주문 총액 {total_order_value:.2f}원이 최소 주문 기준 {MIN_SELL_ORDER_KRW}원 미달")
        return

    attempt = 0
    while attempt < MAX_SELL_ATTEMPTS:
        attempt += 1
        current_balance = await get_fresh_balance(ticker)
        if current_balance <= 0:
            logging.info(f"{ticker} 잔고 0. 매도 중단")
            return
        current_order_value = target_price * current_balance
        if current_order_value < MIN_SELL_ORDER_KRW:
            logging.warning(f"{ticker} 매도 주문 총액 {current_order_value:.2f}원 미달")
            return
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

            async with order_request_limiter:
                order = await upbit_sell_limit_order_async(ticker, target_price, current_balance)

            if order is None or not isinstance(order, dict):
                error_msg = order if order is not None else "None"
                logging.error(f"{ticker} 매도 응답이 dict 아님: {error_msg} (시도 {attempt}/{MAX_SELL_ATTEMPTS})")
                await asyncio.sleep(1)
                continue

            sell_order_uuid[ticker] = order.get('uuid')
            sell_order_time[ticker] = time.time()
            logging.info(f"{ticker} 지정가 매도 주문 실행 - 가격: {target_price}, 수량: {current_balance}")
            return
        except Exception as e:
            logging.error(f"{ticker} 지정가 매도 주문 실패 (시도 {attempt}/{MAX_SELL_ATTEMPTS}): {e}", exc_info=True)
            await asyncio.sleep(1)

    logging.error(f"{ticker} 매도 주문 실패 - 최대 시도 횟수 초과")

# ------------------------------------------------------------
# place_market_sell_order (시장가 매도)
# ------------------------------------------------------------
async def place_market_sell_order(ticker):
    logging.debug(f"{ticker} 시장가 매도 주문 시작")
    current_balance = await get_fresh_balance(ticker)
    if current_balance <= 0:
        logging.info(f"{ticker} 잔고 부족. 시장가 매도 중단")
        return

    current_price = previous_prices.get(ticker)
    if current_price is None:
        # fallback
        p = await get_current_prices_async([ticker])
        current_price = p.get(ticker)
    if current_price is None:
        logging.warning(f"{ticker} 현재가 조회 실패로 시장가 매도 취소")
        return

    total_order_value = current_price * current_balance
    if total_order_value < MIN_SELL_ORDER_KRW:
        logging.warning(f"{ticker} 시장가 매도 총액 {total_order_value:.2f}원이 최소 주문 기준 {MIN_SELL_ORDER_KRW}원 미달")
        return

    attempt = 0
    while attempt < MAX_SELL_ATTEMPTS:
        attempt += 1
        try:
            async with order_request_limiter:
                order = await upbit_sell_market_order_async(ticker, current_balance)
            if not isinstance(order, dict):
                error_msg = order if order is not None else "None"
                logging.error(f"{ticker} 시장가 매도 응답이 dict 아님: {error_msg} (시도 {attempt}/{MAX_SELL_ATTEMPTS})")
                await asyncio.sleep(1)
                continue
            logging.info(f"[매도 체결 완료] {ticker} 시장가 매도 - 수량: {current_balance}")
            return
        except Exception as e:
            logging.error(f"{ticker} 시장가 매도 주문 실패 (시도 {attempt}/{MAX_SELL_ATTEMPTS}): {e}", exc_info=True)
            await asyncio.sleep(1)
    logging.error(f"{ticker} 시장가 매도 주문 실패 - 최대 시도 횟수 초과")

# ------------------------------------------------------------
# should_skip_new_buy (신규 매수 제한 조건)
# ------------------------------------------------------------
async def should_skip_new_buy():
    count = 0
    for held in holding_tickers:
        price = previous_prices.get(held)
        if price is None:
            p = await get_current_prices_async([held])
            price = p.get(held)
        if price is not None and (price * holding_tickers[held]) >= 500000:
            count += 1
    if count >= 3:
        logging.info(f"보유 중인 고액 코인 수 {count}개 (>=3개). 신규 매수 스킵.")
        return True
    return False

# ------------------------------------------------------------
# watch_price 함수 (웹소켓 실시간 처리)
# ------------------------------------------------------------
async def watch_price():
    url = "wss://api.upbit.com/websocket/v1"
    global previous_prices, previous_profit_rates
    last_update = 0
    update_interval = 7200
    last_log_time = time.time()

    while True:
        try:
            # 주기적으로 상위 거래량 종목 리스트 업데이트
            if time.time() - last_update >= update_interval:
                logging.info("상위 거래량 종목 리스트 갱신 중...")
                tickers = await get_top_volume_tickers()
                last_update = time.time()
            else:
                if 'tickers' not in locals():
                    tickers = await get_top_volume_tickers()

            all_tickers = list(set(tickers + list(holding_tickers.keys())))
            all_tickers = [ticker for ticker in all_tickers if ticker not in excluded_tickers]

            logging.debug(f"웹소켓 연결 시도 - 종목 수: {len(all_tickers)}")

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
                    # 시세 캐시 저장
                    previous_prices[ticker] = current_price

                    # RSI
                    rsi = await get_rsi(ticker)
                    rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"

                    logging.info(f"{ticker} 실시간 가격: {current_price}, RSI: {rsi_str}")

                    # 보유 여부
                    total_balance = await upbit_get_balance_async(ticker)
                    if total_balance > 0:
                        in_position[ticker] = True
                        if ticker not in holding_tickers:
                            # 신규 편입
                            holding_tickers[ticker] = total_balance
                            hold_start_time[ticker] = time.time()
                            additional_buy_count[ticker] = 0
                            sell_order_uuid[ticker] = None
                            sell_order_time[ticker] = None
                            avg_price = await get_avg_buy_price_from_balances(ticker)
                            avg_buy_price_holdings[ticker] = avg_price if avg_price else 0.0
                            await place_limit_sell_order(ticker)
                        else:
                            # 기존 보유
                            avg_buy_price = avg_buy_price_holdings.get(ticker, 0.0)
                            if avg_buy_price <= 0:
                                logging.warning(f"{ticker} 평균 매수가 없어서 수익률 계산 불가")
                                continue
                            profit_rate = (current_price - avg_buy_price) / avg_buy_price
                            if (ticker not in previous_profit_rates or
                                abs(previous_profit_rates[ticker] - profit_rate) >= 0.0001):
                                logging.info(f"{ticker} 보유: {total_balance}, 수익률: {profit_rate*100:.2f}%")
                                previous_profit_rates[ticker] = profit_rate

                            # 손절
                            if profit_rate <= stop_loss_rate:
                                logging.info(f"{ticker} 손절 조건 충족")
                                if sell_order_uuid[ticker]:
                                    logging.info(f"{ticker} 매도 주문 취소")
                                    try:
                                        async with non_order_request_limiter:
                                            await upbit_cancel_order_async(sell_order_uuid[ticker])
                                        sell_order_uuid[ticker] = None
                                        sell_order_time[ticker] = None
                                    except Exception as e:
                                        logging.error(f"{ticker} 매도 주문 취소 실패: {e}", exc_info=True)
                                await place_market_sell_order(ticker)
                                # 보유 해제
                                in_position[ticker] = False
                                holding_tickers.pop(ticker, None)
                                avg_buy_price_holdings.pop(ticker, None)
                                additional_buy_count.pop(ticker, None)
                                hold_start_time.pop(ticker, None)
                                sell_order_uuid.pop(ticker, None)
                                sell_order_time.pop(ticker, None)

                            # 추가매수
                            elif profit_rate <= maintain_profit_rate:
                                logging.info(f"{ticker} 수익률 추가매수 기준 이하")
                                elapsed = time.time() - last_additional_buy_time[ticker]
                                if elapsed >= min_interval_between_additional_buys:
                                    if additional_buy_count[ticker] < max_additional_buys:
                                        if rsi and rsi < rsi_threshold_additional:
                                            # 추가매수 수량
                                            Q0 = await get_fresh_balance(ticker)
                                            P0 = avg_buy_price_holdings.get(ticker, 0.0)
                                            if P0 <= 0:
                                                logging.warning(f"{ticker} 평균 매수가 불명")
                                                continue
                                            X = - Q0 * ((current_price / 0.995) - P0) / (0.00502513 * current_price)
                                            if X <= 0:
                                                logging.info(f"{ticker} 추가매수 계산된 수량이 0 이하. 중단")
                                                continue
                                            additional_invest_amount = X * current_price
                                            fee = additional_invest_amount * 0.0005
                                            total_needed = additional_invest_amount + fee

                                            krw_balance = await upbit_get_balance_async("KRW-KRW")
                                            if total_needed > krw_balance:
                                                logging.info(f"{ticker} 추가매수 잔고 부족. 필요={total_needed:.2f}, 보유={krw_balance:.2f}")
                                                continue
                                            logging.info(f"{ticker} 추가매수 진행: {X:.4f}, 금액={additional_invest_amount:.2f}")
                                            await place_buy_order(ticker, krw_balance, additional_invest_amount)
                                            additional_buy_count[ticker] += 1
                                            last_additional_buy_time[ticker] = time.time()
                                            hold_start_time[ticker] = time.time()
                                            new_avg = await get_avg_buy_price_from_balances(ticker)
                                            if new_avg:
                                                avg_buy_price_holdings[ticker] = new_avg
                                            await place_limit_sell_order(ticker)
                                        else:
                                            logging.info(f"{ticker} RSI 추가매수 기준 미충족")
                                    else:
                                        logging.info(f"{ticker} 최대 추가매수 횟수 초과")
                                else:
                                    remain = min_interval_between_additional_buys - elapsed
                                    logging.info(f"{ticker} 추가매수 대기중: {remain:.2f}s 남음")

                            # 매도 주문 상태 확인
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
                                else:
                                    # 장기 미체결(10분) -> 재주문
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
                        # 잔고 0 -> 신규 매수 판단
                        in_position[ticker] = False
                        if ticker in excluded_tickers:
                            logging.debug(f"{ticker} 제외 종목. 매수 X")
                            continue

                        # 신규 종목 매수 제한
                        skip_buy = await should_skip_new_buy()
                        if skip_buy:
                            logging.info(f"{ticker} 신규 매수 제한 (고액 코인 3개 이상 보유)")
                            continue

                        if rsi and rsi < rsi_threshold:
                            logging.debug(f"{ticker} 매수 조건 충족. order_lock 획득")
                            async with order_lock:
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
                            if rsi and abs(rsi - rsi_threshold) <= 5:
                                logging.info(f"{ticker} RSI {rsi:.2f}, 임계값 근접하나 부족")
                            else:
                                logging.debug(f"{ticker} RSI 조건 불충족, 매수 X")

                    # 주기적 리스트 갱신 타이밍
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
        await asyncio.sleep(1)

# ------------------------------------------------------------
# get_all_valid_tickers
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

    # 기존 지정가 매도 주문 취소
    await cancel_existing_sell_orders()

    # 전체 유효 틱커 조회
    valid_tickers = await get_all_valid_tickers()
    logging.info(f"유효한 종목 목록: {valid_tickers}")

    # 잔고 조회
    async with non_order_request_limiter:
        raw_balances = await upbit_get_balances_async()
    logging.info(f"잔고 정보: {raw_balances}")
    await update_balances_cache()

    # 기존 보유 종목 상태 설정
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
            logging.warning(f"{ticker} 유효하지 않은 종목(상장X)")
            continue

        in_position[ticker] = True
        holding_tickers[ticker] = total_amount
        hold_start_time[ticker] = time.time()
        additional_buy_count[ticker] = 0
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None
        avg_price = float(balance_info['avg_buy_price'])
        avg_buy_price_holdings[ticker] = avg_price
        logging.info(f"기존 보유 종목: {ticker}, 수량: {total_amount}, 평균가: {avg_price}")

        if locked > 0:
            async with non_order_request_limiter:
                orders = await upbit_get_order_list_async(state='wait')
            if isinstance(orders, list):
                for order in orders:
                    if (order.get('market') == ticker and
                        order.get('side') == 'ask'):
                        sell_order_uuid[ticker] = order.get('uuid')
                        sell_order_time[ticker] = time.time()
                        logging.info(f"{ticker} 기존 매도 주문 발견 - UUID: {sell_order_uuid[ticker]}")
                        break

        # 현재가 조회(웹소켓 전) → fallback
        prices_dict = await get_current_prices_async([ticker])
        current_price = prices_dict.get(ticker)
        if current_price is None:
            logging.warning(f"{ticker} 현재 가격 불러오기 실패(None)")
            continue

        profit_rate = (current_price - avg_price) / avg_price
        logging.info(f"{ticker} 초기 수익률: {profit_rate*100:.2f}%")
        if profit_rate <= maintain_profit_rate:
            logging.info(f"{ticker} 수익률 추가 매수 기준 이하")
            rsi = await get_rsi(ticker)
            if rsi and rsi < rsi_threshold_additional:
                logging.info(f"{ticker} RSI {rsi:.2f}, 추가 매수 대상 (watch_price에서 실행)")
                # watch_price에서 실시간 가격 변동 시점에 추가매수 진행
            else:
                logging.info(f"{ticker} RSI 기준 미충족, 추가 매수 안함")
        else:
            logging.info(f"{ticker} 매도 주문 실행")
            await place_limit_sell_order(ticker)

    # 메인 루프 → 웹소켓 시세 확인
    await watch_price()

# ------------------------------------------------------------
# 프로그램 실행 시작점
# ------------------------------------------------------------
if __name__ == '__main__':
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("프로그램 종료 요청")
            break
        except Exception as e:
            logging.error(f"메인 함수 예외 발생: {e}", exc_info=True)
            logging.info("5초 후 재시작 시도...")
            time.sleep(5)
            continue
