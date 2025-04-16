# ------------------------------------------------------------
# upbit_utils.py
#  - RSI 계산, 호가단위 계산, 보조적인 함수들
# ------------------------------------------------------------
import pyupbit
import logging
import pandas as pd
import time

from .upbit_config import (
    RSI_PERIOD,
    RSI_CALC_INTERVAL
)

# RSI 캐시 저장용 전역변수
last_rsi_time = {}
rsi_cache = {}

def get_tick_size(price: float) -> float:
    """업비트 호가 단위 계산"""
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

def get_current_price(ticker: str) -> float:
    """단일 종목 현재가"""
    price = None
    try:
        price = pyupbit.get_current_price(ticker)
    except Exception as e:
        logging.error(f"{ticker} 현재가 조회 오류: {e}", exc_info=True)
    return price

def get_rsi(ticker: str) -> float:
    """
    단일 종목 RSI 계산
    - 너무 자주 호출하면 API 제한 발생할 수 있으므로, 
      RSI_CALC_INTERVAL 초 이상 지난 뒤 재계산
    """
    now = time.time()
    if ticker in rsi_cache and (now - last_rsi_time.get(ticker, 0) < RSI_CALC_INTERVAL):
        # 아직 캐시 유효
        return rsi_cache[ticker]

    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute1", count=RSI_PERIOD * 2)
        if df is None or df.empty:
            logging.warning(f"{ticker} RSI용 데이터가 비어있음")
            return 50.0  # 데이터가 없으면 중립값 가정

        close = df['close']
        delta = close.diff().dropna()
        gain = delta.copy()
        loss = delta.copy()

        gain[gain < 0] = 0
        loss[loss > 0] = 0
        loss = loss.abs()

        # 지수이동평균(EWMA) 사용
        avg_gain = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
        avg_loss = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))

        rsi_cache[ticker] = rsi
        last_rsi_time[ticker] = now

        return rsi
    except Exception as e:
        logging.error(f"{ticker} RSI 계산 오류: {e}", exc_info=True)
        return 50.0
