# upbit_rebuilding/upbit_config.py
"""
전략·환경 설정
"""

import os

# ------------------------------------------------------------------ #
# 1) 업비트 API KEY (키 파일 두 줄: access / secret)
# ------------------------------------------------------------------ #
KEY_FILE = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"
with open(KEY_FILE, "r", encoding="utf-8") as f:
    ACCESS_KEY, SECRET_KEY = [l.strip() for l in f.readlines()[:2]]

# ------------------------------------------------------------------ #
# 2) 매매 대상 및 제한
# ------------------------------------------------------------------ #
ALLOWED_TICKERS = ALLOWED_TICKERS = [
    "KRW-BTC",
    "KRW-ETH",
    "KRW-SOL",
    "KRW-XRP",
]   # 원하는 종목 추가
MAX_COINS       = 2             # 동시에 보유할 최대 종목 수

# ------------------------------------------------------------------ #
# 3) 전략 파라미터
# ------------------------------------------------------------------ #
RSI_PERIOD               = 14
RSI_THRESHOLD            = 20      # 최초 진입 RSI
# RSI_THRESHOLD_ADDITIONAL = 0     # 소량 추매 RSI/0.0 ▶ 기능 미사용

# ★ 정밀 추매용 사용자 RSI 트리거  
#   값 = 0 ▶ 기능 미사용
RSI_CUSTOM_TRIGGER       = 15      

TARGET_PROFIT_RATE       = 0.0030  # +0.30 %
MAINTAIN_PROFIT_RATE     = -0.0055 # -0.55 %
STOP_LOSS_RATE           = -0.6    # -60 %
INITIAL_INVEST_RATIO     = 0.10    # 잔고의 5 % 매수

MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 600   # 초

RSI_CACHE_SEC = 60   
RSI_CALC_INTERVAL = 60   # 초
LOOP_INTERVAL     = 1    # 메인 루프 주기(초)

