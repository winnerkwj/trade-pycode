import asyncio
import time
import json
import pyupbit
import pandas as pd
import websockets
import aiohttp
import logging
from collections import defaultdict
import concurrent.futures

# 업비트 API 키 파일 경로 설정
key_file_path = r'C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt'

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

excluded_tickers = ['KRW-BTC', 'KRW-USDT']

class RateLimiter:
    def __init__(self, max_calls, period=1.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = asyncio.get_running_loop().time()
            self.calls = [call for call in self.calls if call > now - self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.period - now
                await asyncio.sleep(sleep_time)
            self.calls.append(now)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

order_lock = asyncio.Lock()
executor = concurrent.futures.ThreadPoolExecutor()

with open(key_file_path, 'r') as file:
    access = file.readline().strip()
    secret = file.readline().strip()

upbit = pyupbit.Upbit(access, secret)

order_request_limiter = RateLimiter(max_calls=8, period=1.0)
non_order_request_limiter = RateLimiter(max_calls=30, period=1.0)
public_api_limiters = defaultdict(lambda: RateLimiter(max_calls=10, period=1.0))

async def get_top_volume_tickers(limit=60):
    loop = asyncio.get_running_loop()
    tickers = await loop.run_in_executor(executor, pyupbit.get_tickers, "KRW")

    url = "https://api.upbit.com/v1/ticker"
    params = {'markets': ','.join(tickers)}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
    except aiohttp.ClientError as e:
        logging.error(f"API 요청 중 오류 발생: {e}")
        return []

    data.sort(key=lambda x: x['acc_trade_price_24h'], reverse=True)
    top_tickers = [x['market'] for x in data if x['market'] not in excluded_tickers]
    top_tickers = top_tickers[:limit]
    return top_tickers

rsi_period = 14
rsi_threshold = 10
rsi_threshold_additional = 55
initial_invest_ratio = 0.005
target_profit_rate = 0.0030
stop_loss_rate = -0.60
maintain_profit_rate = -0.0055

rsi_calculation_interval = 5
min_hold_time_for_additional_buy = 80
min_interval_between_additional_buys = 80

last_additional_buy_time = defaultdict(lambda: 0)
hold_start_time = {}
additional_buy_count = defaultdict(int)
max_additional_buys = 1000

holding_tickers = {}
sell_order_uuid = defaultdict(lambda: None)
sell_order_time = defaultdict(lambda: None)
avg_buy_price_holdings = {}
in_position = defaultdict(bool)

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

rsi_cache = {}
rsi_timestamp = {}

async def get_rsi(ticker):
    now = time.time()
    if ticker in rsi_cache and now - rsi_timestamp.get(ticker, 0) < rsi_calculation_interval:
        return rsi_cache[ticker]
    try:
        endpoint = 'ohlcv'
        async with public_api_limiters[endpoint]:
            df = await get_ohlcv_async(ticker, interval="minute1", count=rsi_period * 2)
        if df is None or df.empty:
            logging.warning(f"{ticker} 데이터 수집 실패 또는 데이터 없음")
            return None
        close = df['close']
        delta = close.diff().dropna()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
        avg_loss = loss.ewm(alpha=1/rsi_period, min_periods=rsi_period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi_value = rsi.iloc[-1]
        rsi_cache[ticker] = rsi_value
        rsi_timestamp[ticker] = now
        return rsi_value
    except Exception as e:
        logging.error(f"{ticker} RSI 계산 중 오류 발생: {e}")
        return None

async def get_ohlcv_async(ticker, interval="minute1", count=200):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, pyupbit.get_ohlcv, ticker, interval, count)

async def get_current_price_async(ticker):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, pyupbit.get_current_price, ticker)

async def upbit_get_balance_async(ticker):
    currency = ticker.split('-')[1]
    loop = asyncio.get_running_loop()
    balances = await loop.run_in_executor(executor, upbit.get_balances)
    for item in balances:
        if item['currency'] == currency:
            balance = float(item['balance'])
            locked = float(item['locked'])
            return balance + locked
    return 0.0

async def upbit_get_order_async(uuid):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.get_order, uuid)

async def upbit_buy_limit_order_async(ticker, price, volume):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.buy_limit_order, ticker, price, volume)

async def upbit_sell_limit_order_async(ticker, price, volume):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.sell_limit_order, ticker, price, volume)

async def upbit_sell_market_order_async(ticker, volume):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.sell_market_order, ticker, volume)

async def upbit_cancel_order_async(uuid):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.cancel_order, uuid)

async def upbit_get_balances_async():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.get_balances)

async def upbit_get_order_list_async(state='wait'):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, upbit.get_order, "", state)

async def get_avg_buy_price_from_balances(ticker):
    async with non_order_request_limiter:
        balances = await upbit_get_balances_async()
    currency = ticker.split('-')[1]
    for balance in balances:
        if balance['currency'] == currency:
            return float(balance['avg_buy_price'])
    return None

async def cancel_existing_sell_orders():
    logging.info("기존 지정가 매도 주문을 조회하고 취소합니다.")
    try:
        async with non_order_request_limiter:
            orders = await upbit_get_order_list_async(state='wait')
    except Exception as e:
        logging.error(f"미체결 주문 조회 실패: {e}")
        return

    if isinstance(orders, list):
        for order in orders:
            if order['side'] == 'ask' and order['ord_type'] == 'limit':
                uuid = order['uuid']
                market = order['market']
                logging.info(f"{market} 지정가 매도 주문 취소 진행 중...")
                try:
                    async with order_request_limiter:
                        await upbit_cancel_order_async(uuid)
                    logging.info(f"{market} 지정가 매도 주문 취소 완료")
                except Exception as e:
                    logging.error(f"{market} 지정가 매도 주문 취소 실패: {e}")
    else:
        logging.warning("미체결 주문이 없거나 주문 목록을 가져오지 못했습니다.")

async def place_buy_order(ticker, krw_balance, invest_amount):
    # (이전 코드에서 구현했던 매수 로직 복원)
    # 최대 1회 시도
    max_attempts = 1
    for attempt in range(1, max_attempts + 1):
        endpoint = 'current_price'
        try:
            async with public_api_limiters[endpoint]:
                current_price = await get_current_price_async(ticker)
        except Exception as e:
            logging.error(f"{ticker} 현재 가격 조회 실패: {e}")
            await asyncio.sleep(0.5)
            continue
        try:
            async with order_request_limiter:
                order = await upbit_buy_limit_order_async(ticker, current_price, invest_amount / current_price)
            logging.info(f"{ticker} 매수 주문 시도 {attempt}회차 - 가격: {current_price}, 금액: {invest_amount} KRW")
            await asyncio.sleep(1)
            try:
                async with non_order_request_limiter:
                    order_info = await upbit_get_order_async(order['uuid'])
            except Exception as e:
                logging.error(f"{ticker} 주문 정보 조회 실패: {e}")
                continue
            if order_info and order_info.get('state') == 'done':
                logging.info(f"{ticker} 매수 주문 체결 완료")
                for _ in range(5):
                    await asyncio.sleep(1)
                    try:
                        balance = await upbit_get_balance_async(ticker)
                    except Exception as e:
                        logging.error(f"{ticker} 잔고 조회 실패: {e}")
                        continue
                    if balance > 0:
                        logging.info(f"{ticker} 잔고 업데이트 완료 - 잔고: {balance}")
                        break
                else:
                    logging.warning(f"{ticker} 잔고 업데이트 지연")
                in_position[ticker] = True
                holding_tickers[ticker] = balance
                hold_start_time[ticker] = time.time()
                try:
                    avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                except Exception as e:
                    logging.error(f"{ticker} 평균 매수가 조회 실패: {e}")
                    avg_buy_price = None
                if avg_buy_price is not None:
                    avg_buy_price_holdings[ticker] = avg_buy_price
                await place_limit_sell_order(ticker)
                return
            else:
                logging.info(f"{ticker} 매수 주문 미체결 - 주문 취소 후 재시도")
                try:
                    async with non_order_request_limiter:
                        await upbit_cancel_order_async(order['uuid'])
                except Exception as e:
                    logging.error(f"{ticker} 주문 취소 실패: {e}")
                await asyncio.sleep(0.5)
        except Exception as e:
            logging.error(f"{ticker} 매수 주문 실패: {e}")
            await asyncio.sleep(0.5)
    logging.error(f"{ticker} 매수 주문 실패 - 최대 시도 횟수 초과")

async def place_limit_sell_order(ticker):
    async with non_order_request_limiter:
        total_balance = await upbit_get_balance_async(ticker)
        avg_buy_price = await get_avg_buy_price_from_balances(ticker)

    if total_balance <= 0:
        logging.info(f"{ticker} 보유 수량이 없어 매도 주문을 진행하지 않습니다.")
        return

    target_price = float(avg_buy_price) * (1 + target_profit_rate + 0.001)
    tick_size = get_tick_size(target_price)
    target_price = (target_price // tick_size) * tick_size

    try:
        async with order_request_limiter:
            if sell_order_uuid[ticker]:
                logging.info(f"{ticker} 기존 매도 주문 취소 중...")
                try:
                    await upbit_cancel_order_async(sell_order_uuid[ticker])
                except Exception as e:
                    logging.error(f"{ticker} 매도 주문 취소 실패: {e}")
                sell_order_uuid[ticker] = None
                sell_order_time[ticker] = None

            order = await upbit_sell_limit_order_async(ticker, target_price, total_balance)
            sell_order_uuid[ticker] = order['uuid']
            sell_order_time[ticker] = time.time()
            logging.info(f"{ticker} 지정가 매도 주문 실행 - 가격: {target_price}, 수량: {total_balance}")
    except Exception as e:
        logging.error(f"{ticker} 지정가 매도 주문 실패: {e}")

async def place_market_sell_order(ticker):
    async with non_order_request_limiter:
        total_balance = await upbit_get_balance_async(ticker)

    if total_balance <= 0:
        logging.info(f"{ticker} 보유 수량이 없어 시장가 매도 주문을 진행하지 않습니다.")
        return

    try:
        async with order_request_limiter:
            await upbit_sell_market_order_async(ticker, total_balance)
            logging.info(f"{ticker} 시장가 매도 주문 실행 - 수량: {total_balance}")
    except Exception as e:
        logging.error(f"{ticker} 시장가 매도 주문 실패: {e}")

async def watch_price():
    url = "wss://api.upbit.com/websocket/v1"
    previous_prices = {}
    previous_profit_rates = {}
    last_update = 0
    update_interval = 7200

    while True:
        if time.time() - last_update >= update_interval:
            tickers = await get_top_volume_tickers()
            last_update = time.time()
            logging.info("상위 거래량 종목 리스트 갱신")

        all_tickers = list(set(tickers + list(holding_tickers.keys())))
        all_tickers = [ticker for ticker in all_tickers if ticker not in excluded_tickers]

        try:
            async with websockets.connect(url, ping_interval=60, ping_timeout=10) as websocket:
                subscribe_data = [
                    {"ticket": "test"},
                    {"type": "ticker", "codes": all_tickers, "isOnlyRealtime": True},
                    {"format": "SIMPLE"}
                ]
                await websocket.send(json.dumps(subscribe_data))

                while True:
                    data = await websocket.recv()
                    data = json.loads(data)
                    if 'cd' in data and 'tp' in data:
                        ticker = data['cd']
                        current_price = data['tp']

                        rsi = await get_rsi(ticker)
                        rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"

                        # 가격 변동이 있을 때만 출력
                        if ticker not in previous_prices or previous_prices[ticker] != current_price:
                            logging.info(f"{ticker} 실시간 가격: {current_price}, RSI: {rsi_str}")
                            previous_prices[ticker] = current_price

                        # 여기서 보유 종목 관리 및 매매 로직 수행
                        async with non_order_request_limiter:
                            total_balance = await upbit_get_balance_async(ticker)

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
                                    logging.warning(f"{ticker} 평균 매수가를 찾을 수 없습니다.")
                                    continue
                                await place_limit_sell_order(ticker)
                            else:
                                avg_buy_price = avg_buy_price_holdings.get(ticker)
                                if avg_buy_price is None:
                                    logging.warning(f"{ticker} 평균 매수가를 알 수 없어 수익률 계산을 건너뜹니다.")
                                    continue

                                profit_rate = (current_price - avg_buy_price) / avg_buy_price

                                if ticker not in previous_profit_rates or abs(previous_profit_rates[ticker] - profit_rate) >= 0.0001:
                                    logging.info(f"{ticker} 보유 수량: {total_balance}, 수익률: {profit_rate*100:.2f}%")
                                    previous_profit_rates[ticker] = profit_rate

                                if profit_rate <= stop_loss_rate:
                                    logging.info(f"{ticker} 손절매 조건 충족 - 수익률: {profit_rate*100:.2f}%")
                                    if sell_order_uuid[ticker]:
                                        logging.info(f"{ticker} 매도 주문 취소 진행 중...")
                                        try:
                                            async with non_order_request_limiter:
                                                await upbit_cancel_order_async(sell_order_uuid[ticker])
                                            sell_order_uuid[ticker] = None
                                            sell_order_time[ticker] = None
                                        except Exception as e:
                                            logging.error(f"{ticker} 매도 주문 취소 실패: {e}")
                                    await place_market_sell_order(ticker)
                                    in_position[ticker] = False
                                    holding_tickers.pop(ticker, None)
                                    avg_buy_price_holdings.pop(ticker, None)
                                    additional_buy_count.pop(ticker, None)
                                    hold_start_time.pop(ticker, None)
                                    sell_order_uuid.pop(ticker, None)
                                    sell_order_time.pop(ticker, None)
                                    continue

                                elif profit_rate <= maintain_profit_rate:
                                    logging.info(f"{ticker} 수익률이 maintain_profit_rate 이하로 떨어짐 - 수익률: {profit_rate*100:.2f}%")
                                    logging.info(f"{ticker} 추가 매수 횟수: {additional_buy_count[ticker]}, 최대 추가 매수 횟수: {max_additional_buys}")
                                    logging.info(f"{ticker} 현재 RSI 값: {rsi}")

                                    current_time = time.time()
                                    elapsed_time = current_time - last_additional_buy_time.get(ticker, 0)
                                    if elapsed_time >= min_interval_between_additional_buys:
                                        if additional_buy_count[ticker] < max_additional_buys:
                                            if rsi is not None and rsi < rsi_threshold_additional:
                                                logging.info(f"{ticker} 추가 매수 진행 중...")
                                                async with non_order_request_limiter:
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
                                                    logging.info(f"{ticker} 추가 매수 실패 - 잔고 부족 또는 최소 금액 미만")
                                            else:
                                                logging.info(f"{ticker} RSI 값이 추가 매수 기준을 충족하지 않습니다.")
                                        else:
                                            logging.info(f"{ticker} 최대 추가 매수 횟수 초과")
                                    else:
                                        remaining_interval = min_interval_between_additional_buys - elapsed_time
                                        logging.info(f"{ticker} 다음 추가 매수까지 남은 시간: {remaining_interval:.2f}초")

                                if sell_order_uuid[ticker]:
                                    async with non_order_request_limiter:
                                        order_info = await upbit_get_order_async(sell_order_uuid[ticker])
                                    if order_info and order_info.get('state') == 'done':
                                        logging.info(f"{ticker} 매도 주문 체결 완료")
                                        in_position[ticker] = False
                                        holding_tickers.pop(ticker, None)
                                        avg_buy_price_holdings.pop(ticker, None)
                                        additional_buy_count.pop(ticker, None)
                                        hold_start_time.pop(ticker, None)
                                        sell_order_uuid[ticker] = None
                                        sell_order_time[ticker] = None
                                        continue
                                    else:
                                        if time.time() - sell_order_time[ticker] > 600:
                                            logging.info(f"{ticker} 매도 주문 미체결로 재주문 진행")
                                            try:
                                                async with non_order_request_limiter:
                                                    await upbit_cancel_order_async(sell_order_uuid[ticker])
                                                sell_order_uuid[ticker] = None
                                                sell_order_time[ticker] = None
                                                await place_limit_sell_order(ticker)
                                            except Exception as e:
                                                logging.error(f"{ticker} 매도 주문 재주문 실패: {e}")
                                else:
                                    logging.info(f"{ticker} 보유 중이지만 매도 주문이 없습니다. 매도 주문을 실행합니다.")
                                    await place_limit_sell_order(ticker)

                        else:
                            in_position[ticker] = False
                            if ticker in excluded_tickers:
                                logging.debug(f"{ticker}는 제외할 암호화폐이므로 매수를 진행하지 않습니다.")
                                continue
                            if in_position[ticker]:
                                logging.debug(f"{ticker} 이미 포지션을 보유하고 있으므로 매수를 진행하지 않습니다.")
                                continue
                            if rsi is not None and rsi < rsi_threshold:
                                async with order_lock:
                                    async with non_order_request_limiter:
                                        balance = await upbit_get_balance_async(ticker)
                                    if balance == 0:
                                        logging.info(f"{ticker} RSI: {rsi:.2f}")
                                        async with non_order_request_limiter:
                                            krw_balance = await upbit_get_balance_async("KRW-KRW")
                                        invest_amount = krw_balance * initial_invest_ratio
                                        if invest_amount > 5000:
                                            await place_buy_order(ticker, krw_balance, invest_amount)
                                        else:
                                            logging.info(f"{ticker} 매수 실패 - 잔고 부족")
                            else:
                                if rsi is not None and abs(rsi - rsi_threshold) <= 5:
                                    logging.info(f"{ticker} RSI 값이 {rsi:.2f}로 임계값을 충족하지 않으므로 매수를 진행하지 않습니다.")
                                else:
                                    logging.debug(f"{ticker} RSI 값이 조건을 충족하지 않으므로 매수를 진행하지 않습니다.")

                    if time.time() - last_update >= update_interval:
                        logging.info("종목 리스트 갱신을 위해 웹소켓 연결을 종료합니다.")
                        break

                # 내부 루프 break 후 상위 루프 재시작
                continue

        except websockets.exceptions.ConnectionClosedError as e:
            logging.warning(f"웹소켓 연결 끊김, 재연결 시도 중: {e}")
            await asyncio.sleep(1)
            continue
        except Exception as e:
            logging.error(f"예기치 못한 오류 발생: {e}")
            await asyncio.sleep(1)
            continue

async def main():
    await cancel_existing_sell_orders()
    valid_tickers = await get_top_volume_tickers()
    logging.info(f"유효한 종목 목록: {valid_tickers}")

    async with non_order_request_limiter:
        balances = await upbit_get_balances_async()

    logging.info(f"잔고 정보: {balances}")

    for balance_info in balances:
        currency = balance_info['currency']
        if currency == 'KRW':
            continue  # 원화는 제외
        amount = float(balance_info['balance'])
        locked = float(balance_info['locked'])
        total_amount = amount + locked
        if total_amount <= 0:
            continue  # 잔고가 없으면 다음 종목으로

        ticker = f"KRW-{currency}"

        # 종목 코드가 유효한지 확인
        if ticker not in valid_tickers:
            logging.warning(f"{ticker}는 유효한 종목이 아닙니다. 처리하지 않습니다.")
            continue

        in_position[ticker] = True  # 포지션 보유 상태로 변경
        holding_tickers[ticker] = total_amount
        hold_start_time[ticker] = time.time()
        additional_buy_count[ticker] = 0  # 추가 매수 횟수 초기화
        sell_order_uuid[ticker] = None
        sell_order_time[ticker] = None
        # 평균 매수가 저장
        avg_buy_price = float(balance_info['avg_buy_price'])
        avg_buy_price_holdings[ticker] = avg_buy_price
        logging.info(f"기존 보유 종목 추가: {ticker}, 수량: {total_amount}, 평균 매수가: {avg_buy_price}")

        # locked 수량이 있는 경우 기존 주문 UUID 저장
        if locked > 0:
            async with non_order_request_limiter:
                orders = await upbit_get_order_list_async(state='wait')
            for order in orders:
                if order['market'] == ticker and order['side'] == 'ask':
                    # 기존 매도 주문이 있으면 UUID 저장
                    sell_order_uuid[ticker] = order['uuid']
                    sell_order_time[ticker] = time.time()
                    logging.info(f"{ticker} 기존 매도 주문 발견 - UUID: {sell_order_uuid[ticker]}")
                    break

        # 현재 가격 조회
        endpoint = 'current_price'
        try:
            async with public_api_limiters[endpoint]:
                current_price = await get_current_price_async(ticker)
        except pyupbit.errors.UpbitError as e:
            logging.error(f"{ticker} 현재 가격을 가져오는 중 에러 발생: {e}")
            continue  # 다음 종목으로 넘어감

        if current_price is None:
            logging.warning(f"{ticker} 현재 가격을 가져올 수 없습니다.")
            continue

        # 수익률 계산
        profit_rate = (current_price - avg_buy_price) / avg_buy_price
        logging.info(f"{ticker} 수익률: {profit_rate*100:.2f}%")

        # 추가 매수 조건 확인
        if profit_rate <= maintain_profit_rate:
            logging.info(f"{ticker} 수익률이 추가 매수 기준 이하입니다. 추가 매수 진행을 검토합니다.")
            # RSI 값 조회
            rsi = await get_rsi(ticker)
            if rsi is not None and rsi < rsi_threshold_additional:
                logging.info(f"{ticker} RSI 값이 {rsi:.2f}로 추가 매수 기준을 충족합니다.")
                # 추가 매수 진행
                async with non_order_request_limiter:
                    krw_balance = await upbit_get_balance_async("KRW-KRW")
                invest_amount = krw_balance * initial_invest_ratio
                fee = invest_amount * 0.0005
                total_invest_amount = invest_amount + fee
                if total_invest_amount > 5000 and krw_balance >= total_invest_amount:
                    await place_buy_order(ticker, krw_balance, invest_amount)
                    additional_buy_count[ticker] += 1  # 추가 매수 횟수 증가
                    last_additional_buy_time[ticker] = time.time()  # 마지막 추가 매수 시간 업데이트
                    hold_start_time[ticker] = time.time()  # 보유 시작 시간 업데이트
                    # 평균 매수가 업데이트
                    avg_buy_price = await get_avg_buy_price_from_balances(ticker)
                    if avg_buy_price is not None:
                        avg_buy_price_holdings[ticker] = avg_buy_price
                    # 추가 매수 후 지정가 매도 주문 진행
                    await place_limit_sell_order(ticker)
                else:
                    logging.info(f"{ticker} 추가 매수 실패 - 잔고 부족 또는 최소 금액 미만")
            else:
                logging.info(f"{ticker} RSI 값이 추가 매수 기준을 충족하지 않습니다.")
        else:
            logging.info(f"{ticker} 수익률이 추가 매수 기준을 충족하지 않습니다.")
            # 기존 보유 종목에 대한 지정가 매도 주문 실행
            await place_limit_sell_order(ticker)
    await watch_price()

# 프로그램 시작
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("프로그램이 사용자에 의해 중단되었습니다.")
