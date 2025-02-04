import asyncio
import time
import math
import pyupbit
import json
import pandas as pd
import websockets
import aiohttp
import logging
from collections import defaultdict
import concurrent.futures
import requests

# ------------------------------------------------------------
# 업비트 API 키 파일 경로 설정
# ------------------------------------------------------------
key_file_path = r'C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt'

# ------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ------------------------------------------------------------
# 제외할 종목 목록
# ------------------------------------------------------------
excluded_tickers = [
    'KRW-USDT', 'KRW-BTG', 'KRW-MOCA', 'KRW-VTHO', 'KRW-SBD'
]

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
                logging.debug(f"[RateLimiter] 요청 초과. {sleep_time:.2f}초 대기합니다.")
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
order_request_limiter = RateLimiter(max_calls=6, period=1.0)       # 주문 관련
non_order_request_limiter = RateLimiter(max_calls=25, period=1.0)  # balances 등 일반 요청
public_api_limiters = defaultdict(lambda: RateLimiter(max_calls=9, period=1.0))  # 티커 조회 등

# ------------------------------------------------------------
# 잔고 캐싱 관련 변수
# ------------------------------------------------------------
balances_cache = {}
balances_last_update = 0
balances_update_interval = 5.0

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
rsi_threshold_additional = 11

initial_invest_ratio = 0.05
target_profit_rate = 0.0030      # +0.3%
stop_loss_rate = -0.6           # -60%
maintain_profit_rate = -0.0055  # -0.55%
rsi_calculation_interval = 60
min_hold_time_for_additional_buy = 600
min_interval_between_additional_buys = 600
max_additional_buys = 1000

# ------------------------------------------------------------
# 종목별 상태 관리용
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
previous_prices = {}
previous_profit_rates = {}

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
# 잔고 정보 업데이트 (캐시)
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
            logging.debug("[update_balances_cache] balances_cache 갱신 완료")
        except Exception as e:
            logging.error(f"[update_balances_cache] balances 갱신 중 오류: {e}", exc_info=True)

# ------------------------------------------------------------
# 실시간 잔고 새로고침 함수
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
        logging.error(f"[get_fresh_balance] {ticker} 잔고 새로고침 오류: {e}", exc_info=True)
        return 0.0

# ------------------------------------------------------------
# 상위 거래량 종목 조회
# ------------------------------------------------------------
async def get_top_volume_tickers(limit=60):
    logging.debug("[get_top_volume_tickers] 상위 거래량 종목 조회 시작")
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
    logging.debug("[get_top_volume_tickers] 상위 거래량 종목 조회 완료")
    return top_tickers

# ------------------------------------------------------------
# OHLCV (캔들) 비동기 조회
# ------------------------------------------------------------
async def get_ohlcv_async(ticker, interval="minute1", count=200):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, pyupbit.get_ohlcv, ticker, interval, count)

# ------------------------------------------------------------
# RSI 계산
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
            logging.warning(f"[get_rsi] {ticker} 데이터 없음")
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
        logging.error(f"[get_rsi] {ticker} RSI 계산 오류: {e}", exc_info=True)
        return None

# ------------------------------------------------------------
# 다중 종목 현재가 조회
# ------------------------------------------------------------
async def get_current_prices_async(tickers: list[str]) -> dict:
    if not tickers:
        return {}
    url = "https://api.upbit.com/v1/ticker"
    params = {"markets": ",".join(tickers)}

    results = {}
    async with public_api_limiters['ticker']:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=5) as resp:
                    if resp.status == 429:
                        logging.warning("[get_current_prices_async] 429 오류. 잠시 후 재시도 권장.")
                        return {}
                    if resp.status != 200:
                        logging.error(f"[get_current_prices_async] 현재가 조회 HTTP 오류 상태: {resp.status}")
                        return {}
                    data = await resp.json()
                    if not isinstance(data, list):
                        return {}
                    for item in data:
                        market = item.get("market")
                        trade_price = item.get("trade_price", 0)
                        if market and trade_price:
                            results[market] = trade_price
            except asyncio.TimeoutError:
                logging.error(f"[get_current_prices_async] 현재가 조회 타임아웃: {tickers}")
            except Exception as e:
                logging.error(f"[get_current_prices_async] 현재가 조회 예외: {e}", exc_info=True)
    return results

# ------------------------------------------------------------
# 웹소켓 없이도 현재가를 가져올 수 있도록 fallback
# ------------------------------------------------------------
async def get_price_from_cache_or_api(ticker):
    price = previous_prices.get(ticker)
    if price is not None:
        return price

    fallback_result = await get_current_prices_async([ticker])
    if ticker in fallback_result:
        previous_prices[ticker] = fallback_result[ticker]
        return fallback_result[ticker]

    return None

# ------------------------------------------------------------
# 업비트 API 래퍼 함수들
# ------------------------------------------------------------
async def upbit_get_balances_async():
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(executor, upbit.get_balances)
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        logging.error(f"[upbit_get_balances_async] Timeout 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return await loop.run_in_executor(executor, upbit.get_balances)
    except Exception as e:
        logging.error(f"[upbit_get_balances_async] balances 조회 오류: {e}", exc_info=True)
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
# 기존 지정가 매도 주문 모두 취소
# ------------------------------------------------------------
async def cancel_existing_sell_orders():
    logging.info("[cancel_existing_sell_orders] 기존 지정가 매도 주문 취소 진행...")
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
                logging.info(f"[cancel_existing_sell_orders] {market} 매도 주문 취소 진행")
                try:
                    async with order_request_limiter:
                        await upbit_cancel_order_async(uuid)
                    logging.info(f"[cancel_existing_sell_orders] {market} 매도 주문 취소 완료")
                except Exception as e:
                    logging.error(f"[cancel_existing_sell_orders] {market} 매도 주문 취소 실패: {e}", exc_info=True)
    else:
        logging.warning("[cancel_existing_sell_orders] 미체결 주문이 없거나 조회 실패")

# ------------------------------------------------------------
# 매수 주문
# ------------------------------------------------------------
async def place_buy_order(ticker, krw_balance, invest_amount):
    logging.debug(f"[place_buy_order] {ticker} 매수 주문 시작. 투자액: {invest_amount}")
    max_attempts = 1
    for attempt in range(1, max_attempts + 1):
        current_price = await get_price_from_cache_or_api(ticker)
        if current_price is None:
            logging.error(f"[place_buy_order] {ticker} 현재가가 None, 매수 불가")
            return

        try:
            async with order_request_limiter:
                order = await upbit_buy_limit_order_async(
                    ticker,
                    current_price,
                    invest_amount / current_price
                )
            logging.info(f"[place_buy_order] {ticker} 매수 주문 시도 {attempt}회 - 가격: {current_price}, 금액: {invest_amount}")
            await asyncio.sleep(1)
            async with non_order_request_limiter:
                order_info = await upbit_get_order_async(order['uuid'])

            if order_info and order_info.get('state') == 'done':
                logging.info(f"[매수 체결 완료] {ticker} 가격: {current_price}, 매수 금액: {invest_amount}")
                for _ in range(5):
                    await asyncio.sleep(1)
                    balance = await upbit_get_balance_async(ticker)
                    if balance > 0:
                        logging.info(f"[place_buy_order] {ticker} 잔고 업데이트 완료: {balance}")
                        break
                else:
                    logging.warning(f"[place_buy_order] {ticker} 잔고 업데이트 지연")

                in_position[ticker] = True
                holding_tickers[ticker] = balance
                hold_start_time[ticker] = time.time()

                avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                if avg_buy_price is not None:
                    avg_buy_price_holdings[ticker] = avg_buy_price

                # 바로 매도 주문 걸어둠
                await place_limit_sell_order(ticker)
                return
            else:
                logging.info(f"[place_buy_order] {ticker} 매수 미체결 -> 주문 취소 후 재시도")
                async with non_order_request_limiter:
                    await upbit_cancel_order_async(order['uuid'])
                await asyncio.sleep(0.5)

        except Exception as e:
            logging.error(f"[place_buy_order] {ticker} 매수 주문 실패: {e}", exc_info=True)
            await asyncio.sleep(0.5)

    logging.error(f"[place_buy_order] {ticker} 매수 실패 - 최대 시도 횟수 초과")

# ------------------------------------------------------------
# 지정가 매도 주문
# ------------------------------------------------------------
async def place_limit_sell_order(ticker):
    logging.debug(f"[place_limit_sell_order] {ticker} 지정가 매도 주문 시작")

    avg_buy_price = await get_avg_buy_price_from_balances(ticker)
    if avg_buy_price is None:
        logging.warning(f"[place_limit_sell_order] {ticker} 평균 매수가 정보 없음. 매도 중단")
        return

    target_price = float(avg_buy_price) * (1 + target_profit_rate + 0.001)
    tick_size = get_tick_size(target_price)
    target_price = (target_price // tick_size) * tick_size

    current_balance = await get_fresh_balance(ticker)
    if current_balance <= 0:
        logging.info(f"[place_limit_sell_order] {ticker} 실시간 잔고 0, 매도 중단")
        return

    total_order_value = target_price * current_balance
    if total_order_value < MIN_SELL_ORDER_KRW:
        logging.warning(f"[place_limit_sell_order] {ticker} 매도 주문 총액 {total_order_value:.2f}원 미달")
        return

    try:
        # 기존 매도 주문 있으면 취소 후 locked 해제 대기
        if sell_order_uuid[ticker]:
            old_uuid = sell_order_uuid[ticker]
            logging.info(f"[place_limit_sell_order] {ticker} 기존 매도 주문 취소 시도. uuid={old_uuid}")
            try:
                async with order_request_limiter:
                    cancel_result = await upbit_cancel_order_async(old_uuid)
                logging.info(f"[place_limit_sell_order] {ticker} 기존 매도 주문 취소 요청 결과: {cancel_result}")
            except Exception as e:
                logging.error(f"[place_limit_sell_order] {ticker} 기존 매도 주문 취소 실패: {e}", exc_info=True)
            
            sell_order_uuid[ticker] = None
            sell_order_time[ticker] = None

            cancel_start = time.time()
            original_balance = current_balance
            while time.time() - cancel_start < 5:
                await asyncio.sleep(0.5)
                new_balance = await get_fresh_balance(ticker)
                if new_balance <= 0:
                    logging.info(f"[place_limit_sell_order] {ticker} 잔고 0이 됨 -> 이미 전량 매도되었을 가능성. 스킵.")
                    return
                if abs(new_balance - original_balance) < 1e-7:
                    logging.info(f"[place_limit_sell_order] {ticker} locked 해제 감지 -> 재주문 진행")
                    break
            else:
                logging.warning(f"[place_limit_sell_order] {ticker} 5초 내 locked 해제 안됨. 그래도 재시도")

        attempt = 0
        while attempt < MAX_SELL_ATTEMPTS:
            attempt += 1

            current_balance = await get_fresh_balance(ticker)
            if current_balance <= 0:
                logging.info(f"[place_limit_sell_order] {ticker} 재확인: 잔고 0 -> 매도 중단")
                return

            order_value = target_price * current_balance
            if order_value < MIN_SELL_ORDER_KRW:
                logging.warning(f"[place_limit_sell_order] {ticker} 매도 주문 총액 {order_value:.2f}원 미달")
                return

            # 안전하게 0.995 곱
            safe_balance = math.floor(current_balance)
            if safe_balance <= 0:
                logging.info(f"[place_limit_sell_order] {ticker} safe_balance=0 -> 매도 중단")
                return

            try:
                async with order_request_limiter:
                    order = await upbit_sell_limit_order_async(ticker, target_price, safe_balance)

                if not order or not isinstance(order, dict):
                    err_msg = order if order else "None"
                    logging.error(f"[place_limit_sell_order] {ticker} 매도 응답이 dict 아님: {err_msg} (시도 {attempt}/{MAX_SELL_ATTEMPTS})")
                    # InsufficientFundsAsk 체크
                    if order and "InsufficientFundsAsk" in str(order):
                        logging.error(f"[place_limit_sell_order] {ticker} 잔고 부족 오류 -> 즉시 스킵")
                        return
                    await asyncio.sleep(1)
                    continue

                if "InsufficientFundsAsk" in str(order):
                    logging.error(f"[place_limit_sell_order] {ticker} 매도 잔고 부족 -> 즉시 스킵")
                    return

                # 매도 주문 정상 응답
                sell_order_uuid[ticker] = order.get('uuid')
                sell_order_time[ticker] = time.time()
                logging.info(f"[place_limit_sell_order] {ticker} 지정가 매도 주문 완료 - 가격: {target_price}, 수량: {safe_balance}")
                return

            except Exception as e:
                # 예외에 InsufficientFundsAsk 가 있다면 즉시 스킵
                if "InsufficientFundsAsk" in str(e):
                    logging.error(f"[place_limit_sell_order] {ticker} 잔고 부족 예외 -> 즉시 스킵: {e}", exc_info=True)
                    return

                logging.error(f"[place_limit_sell_order] {ticker} 지정가 매도 주문 실패 (시도 {attempt}/{MAX_SELL_ATTEMPTS}): {e}", exc_info=True)
                await asyncio.sleep(1)

        logging.error(f"[place_limit_sell_order] {ticker} 매도 주문 실패 - 최대 시도 횟수 초과")

    except Exception as e:
        logging.error(f"[place_limit_sell_order] {ticker} 예외 발생: {e}", exc_info=True)

# ------------------------------------------------------------
# 신규 매수 제한 조건
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
        logging.info(f"[should_skip_new_buy] 보유 중인 고액 코인 수 {count}개 -> 신규 매수 스킵")
        return True
    return False

# ------------------------------------------------------------
# 실시간 시세 처리 (웹소켓)
# ------------------------------------------------------------
async def watch_price():
    """
    웹소켓 실시간 시세 처리
    - update_interval=60초 동안 실시간 처리 후, 리스트 갱신 위해 웹소켓 종료
    - InsufficientFundsAsk 발생 시 즉시 스킵
    - 웹소켓 종료 시 if websocket: ... close()
    """
    url = "wss://api.upbit.com/websocket/v1"
    global previous_prices, previous_profit_rates

    update_interval = 3600
    start_time = time.time()

    websocket = None
    try:
        logging.info("[watch_price] 웹소켓 연결 시도")
        websocket = await websockets.connect(url, ping_interval=60, ping_timeout=10)
        logging.info("[watch_price] 웹소켓 연결 완료")

        tickers = await get_top_volume_tickers(limit=5)
        all_tickers = list(set(tickers + list(holding_tickers.keys())))
        all_tickers = [t for t in all_tickers if t not in excluded_tickers]

        subscribe_data = [
            {"ticket": "test"},
            {"type": "ticker", "codes": all_tickers, "isOnlyRealtime": True},
            {"format": "SIMPLE"}
        ]
        await websocket.send(json.dumps(subscribe_data))
        logging.info(f"[watch_price] 웹소켓 구독 요청 완료. 대상 종목 수: {len(all_tickers)}")

        while True:
            elapsed = time.time() - start_time
            if elapsed >= update_interval:
                logging.info("[watch_price] 종목 리스트 갱신 위해 웹소켓 종료 요청")
                return

            try:
                data = await asyncio.wait_for(websocket.recv(), timeout=30)
            except asyncio.TimeoutError:
                logging.warning("[watch_price] 30초간 데이터 수신 없어 재연결 필요. return")
                return

            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                logging.error("[watch_price] 웹소켓 데이터 파싱 오류", exc_info=True)
                continue

            if not isinstance(data, dict):
                continue
            if 'cd' not in data or 'tp' not in data:
                continue

            ticker = data['cd']
            current_price = data['tp']
            previous_prices[ticker] = current_price

            rsi = await get_rsi(ticker)
            rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
            logging.info(f"[watch_price] {ticker} 실시간 가격: {current_price}, RSI: {rsi_str}")

            # 보유 잔고 확인
            total_balance = await upbit_get_balance_async(ticker)
            if total_balance > 0:
                in_position[ticker] = True
                if ticker not in holding_tickers:
                    # 새로 편입
                    holding_tickers[ticker] = total_balance
                    hold_start_time[ticker] = time.time()
                    additional_buy_count[ticker] = 0
                    sell_order_uuid[ticker] = None
                    sell_order_time[ticker] = None
                    avg_price = await get_avg_buy_price_from_balances(ticker)
                    avg_buy_price_holdings[ticker] = avg_price if avg_price else 0.0
                    await place_limit_sell_order(ticker)
                else:
                    # 기존 보유 종목
                    avg_buy_price = avg_buy_price_holdings.get(ticker, 0.0)
                    if avg_buy_price <= 0:
                        continue
                    profit_rate = (current_price - avg_buy_price) / avg_buy_price

                    # 로그(수익률)
                    if (ticker not in previous_profit_rates or
                        abs(previous_profit_rates[ticker] - profit_rate) >= 0.0001):
                        logging.info(f"[watch_price] {ticker} 수익률: {profit_rate*100:.2f}%")
                        previous_profit_rates[ticker] = profit_rate

                    # 손절
                    if profit_rate <= stop_loss_rate:
                        logging.info(f"[watch_price] {ticker} 손절 조건 충족 -> 시장가 매도")
                        if sell_order_uuid[ticker]:
                            logging.info(f"[watch_price] {ticker} 기존 매도 주문 취소 시도")
                            try:
                                async with non_order_request_limiter:
                                    await upbit_cancel_order_async(sell_order_uuid[ticker])
                                sell_order_uuid[ticker] = None
                                sell_order_time[ticker] = None
                            except Exception as e:
                                logging.error(f"[watch_price] {ticker} 매도 주문 취소 실패: {e}", exc_info=True)
                        await place_market_sell_order(ticker)
                        in_position[ticker] = False
                        holding_tickers.pop(ticker, None)
                        avg_buy_price_holdings.pop(ticker, None)
                        additional_buy_count.pop(ticker, None)
                        hold_start_time.pop(ticker, None)
                        sell_order_uuid.pop(ticker, None)
                        sell_order_time.pop(ticker, None)

                    # 추가 매수
                    elif profit_rate <= maintain_profit_rate:
                        logging.info(f"[watch_price] {ticker} 추가매수 조건 이하")
                        elapsed_buy = time.time() - last_additional_buy_time[ticker]
                        if elapsed_buy >= min_interval_between_additional_buys:
                            if additional_buy_count[ticker] < max_additional_buys:
                                if rsi and rsi < rsi_threshold_additional:
                                    Q0 = await get_fresh_balance(ticker)
                                    P0 = avg_buy_price_holdings.get(ticker, 0.0)
                                    if P0 <= 0:
                                        continue
                                    # 추가매수 로직(예시)
                                    X = - Q0 * ((current_price / 0.995) - P0) / (0.00502513 * current_price)
                                    if X <= 0:
                                        continue

                                    additional_invest_amount = X * current_price
                                    fee = additional_invest_amount * 0.0005
                                    total_needed = additional_invest_amount + fee

                                    krw_balance = await upbit_get_balance_async("KRW-KRW")
                                    if total_needed > krw_balance:
                                        logging.info(f"[watch_price] {ticker} 추가매수 잔고 부족")
                                        continue

                                    logging.info(f"[watch_price] {ticker} 추가매수 진행 -> 수량={X:.4f}, 금액={additional_invest_amount:.2f}")
                                    await place_buy_order(ticker, krw_balance, additional_invest_amount)
                                    additional_buy_count[ticker] += 1
                                    last_additional_buy_time[ticker] = time.time()
                                    hold_start_time[ticker] = time.time()

                                    new_avg = await get_avg_buy_price_from_balances(ticker)
                                    if new_avg:
                                        avg_buy_price_holdings[ticker] = new_avg

                                    await place_limit_sell_order(ticker)
                                else:
                                    logging.info(f"[watch_price] {ticker} RSI 추가매수 기준 미충족")
                            else:
                                logging.info(f"[watch_price] {ticker} 추가매수 횟수({max_additional_buys}) 초과")
                        else:
                            remain_buy = min_interval_between_additional_buys - elapsed_buy
                            logging.info(f"[watch_price] {ticker} 추가매수 대기중: {remain_buy:.2f}초 남음")

                    # 기존 매도 주문 상태
                    if sell_order_uuid[ticker]:
                        async with non_order_request_limiter:
                            try:
                                order_info = await upbit_get_order_async(sell_order_uuid[ticker])
                            except Exception as e:
                                logging.error(f"[watch_price] {ticker} 매도 주문 조회 실패: {e}", exc_info=True)
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
                            # 장기 미체결(10분)
                            if sell_order_time[ticker] and time.time() - sell_order_time[ticker] > 600:
                                logging.info(f"[watch_price] {ticker} 매도 주문 장기 미체결 -> 재주문")
                                try:
                                    async with non_order_request_limiter:
                                        await upbit_cancel_order_async(sell_order_uuid[ticker])
                                    sell_order_uuid[ticker] = None
                                    sell_order_time[ticker] = None
                                    await place_limit_sell_order(ticker)
                                except Exception as e:
                                    logging.error(f"[watch_price] {ticker} 매도 재주문 실패: {e}", exc_info=True)
                    else:
                        # 매도 주문이 없으면 새로 지정가 매도 주문
                        await place_limit_sell_order(ticker)

            else:
                # 잔고 0 -> 신규 매수 판단
                in_position[ticker] = False
                if ticker in excluded_tickers:
                    continue

                skip_buy = await should_skip_new_buy()
                if skip_buy:
                    continue

                if rsi and rsi < rsi_threshold:
                    async with order_lock:
                        balance = await upbit_get_balance_async(ticker)
                        if balance == 0:
                            logging.info(f"[watch_price] {ticker} RSI={rsi:.2f}, 매수 시도")
                            krw_balance = await upbit_get_balance_async("KRW-KRW")
                            invest_amount = krw_balance * initial_invest_ratio
                            if invest_amount > 5000:
                                await place_buy_order(ticker, krw_balance, invest_amount)
                            else:
                                logging.info(f"[watch_price] {ticker} 매수 실패 - 잔고 부족")
                else:
                    pass

    except (websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.InvalidStatusCode,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout) as net_err:
        logging.warning(f"[watch_price] 네트워크/웹소켓 오류 발생: {net_err}", exc_info=True)
        return
    except Exception as e:
        logging.error(f"[watch_price] 예기치 못한 오류 발생: {e}", exc_info=True)
        return
    finally:
        # 여기서 websocket.closed 대신 if websocket:
        if websocket:
            logging.info("[watch_price] finally: websocket.close() 실행")
            try:
                await asyncio.wait_for(websocket.close(), timeout=5)
            except asyncio.TimeoutError:
                logging.warning("[watch_price] websocket.close()가 5초 내 완료되지 않아 강제 종료 시도")

        logging.info("[watch_price] finally 블록 종료 -> watch_price 함수 완전 종료")

# ------------------------------------------------------------
# 전체 유효 티커 조회
# ------------------------------------------------------------
async def get_all_valid_tickers():
    loop = asyncio.get_event_loop()
    try:
        valid_tickers = await loop.run_in_executor(executor, pyupbit.get_tickers, "KRW")
        return valid_tickers
    except Exception as e:
        logging.error(f"[get_all_valid_tickers] 전체 티커 조회 오류: {e}", exc_info=True)
        await asyncio.sleep(5)
        return []

# ------------------------------------------------------------
# 메인 함수
# ------------------------------------------------------------
async def main():
    logging.info("[main] 메인 함수 시작")

    # 기존 지정가 매도 주문 취소
    await cancel_existing_sell_orders()

    # 전체 유효 티커 조회
    valid_tickers = await get_all_valid_tickers()
    logging.info(f"[main] 유효한 종목 개수: {len(valid_tickers)}")

    # 잔고 조회 및 캐시 갱신
    async with non_order_request_limiter:
        raw_balances = await upbit_get_balances_async()
    await update_balances_cache()

    # 기존 보유 종목 상태 설정
    for b in raw_balances:
        currency = b['currency']
        if currency == 'KRW':
            continue
        amount = float(b['balance'])
        locked = float(b['locked'])
        total_amount = amount + locked
        if total_amount <= 0:
            continue

        ticker = f"KRW-{currency}"
        if ticker not in valid_tickers:
            logging.warning(f"[main] {ticker} 유효하지 않은 종목(상장X)")
            continue

        in_position[ticker] = True
        holding_tickers[ticker] = total_amount
        hold_start_time[ticker] = time.time()
        additional_buy_count[ticker] = 0
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None
        avg_price = float(b['avg_buy_price'])
        avg_buy_price_holdings[ticker] = avg_price
        logging.info(f"[main] 기존 보유 종목: {ticker}, 수량: {total_amount}, 평균가: {avg_price}")

        # locked가 있으면 기존 주문이 있을 수도
        if locked > 0:
            async with non_order_request_limiter:
                orders = await upbit_get_order_list_async(state='wait')
            if isinstance(orders, list):
                for order in orders:
                    if order.get('market') == ticker and order.get('side') == 'ask':
                        sell_order_uuid[ticker] = order.get('uuid')
                        sell_order_time[ticker] = time.time()
                        logging.info(f"[main] {ticker} 기존 매도 주문 발견 - UUID: {sell_order_uuid[ticker]}")
                        break

        # 현재가 조회(웹소켓 전이므로 fallback)
        prices_dict = await get_current_prices_async([ticker])
        current_price = prices_dict.get(ticker)
        if current_price is None:
            logging.warning(f"[main] {ticker} 현재 가격 불러오기 실패(None)")
            continue

        profit_rate = (current_price - avg_price) / avg_price
        logging.info(f"[main] {ticker} 초기 수익률: {profit_rate*100:.2f}%")

        if profit_rate <= maintain_profit_rate:
            logging.info(f"[main] {ticker} 추가매수 로직은 watch_price에서")
        else:
            logging.info(f"[main] {ticker} 초기 지정가 매도 주문 실행")
            await place_limit_sell_order(ticker)

    # 실시간 시세 감시
    logging.info("[main] watch_price() 호출 시작")
    await watch_price()
    logging.info("[main] watch_price() 종료됨 -> main() 함수도 종료 (상위 루프에서 재실행 예정)")
    return

# ------------------------------------------------------------
# 프로그램 실행 구간
# ------------------------------------------------------------
if __name__ == '__main__':
    while True:
        try:
            logging.info("[__main__] ========== 메인 루프 시작 ==========")
            asyncio.run(main())
            logging.info("[__main__] main() 정상 종료됨. 3초 후 재시작.")
            time.sleep(3)

        except KeyboardInterrupt:
            logging.info("[__main__] 프로그램 종료 요청 (KeyboardInterrupt)")
            break

        except Exception as e:
            logging.error(f"[__main__] 메인 함수 예외 발생: {e}", exc_info=True)
            logging.info("[__main__] 5초 후 재시작 시도...")
            time.sleep(5)
            continue
