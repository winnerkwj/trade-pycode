"""
전략 · 환경 설정
"""

# 1) API 키 ----------------------------------------------------------
KEY_FILE = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"
with open(KEY_FILE, "r", encoding="utf-8") as f:
    ACCESS_KEY, SECRET_KEY = [line.strip() for line in f.readlines()[:2]]

# 2) 최대 동시 보유 종목 수 ---------------------------------------------
MAX_COINS       = 2

# 3) 전략 파라미터 ---------------------------------------------------
RSI_PERIOD            = 14
RSI_THRESHOLD         = 10
RSI_CUSTOM_TRIGGER    = 35

TARGET_PROFIT_RATE    = 0.0030
MAINTAIN_PROFIT_RATE  = -0.0055
STOP_LOSS_RATE        = -0.6
INITIAL_INVEST_RATIO  = 0.05

MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 60

# 4) 주기 · 캐시 -----------------------------------------------------
RSI_CACHE_SEC          = 60
LOOP_INTERVAL          = 1

TICKER_ROTATE_INTERVAL = 600    # Top N 재계산 주기 (초)
TOP_N_TICKERS          = 60      # 24 h 거래대금 상위 N 종목
TICKER_FILTER_INTERVAL = 900     # Upbit 필터 주기 (초)
WS_OHLC_USE            = True    # WS 1 분 OHLC 캐시 사용 여부
