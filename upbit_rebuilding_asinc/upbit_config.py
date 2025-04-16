# upbit_rebuilding/upbit_config.py
"""
API 키 및 전략 파라미터를 한곳에 모아둡니다.
"""

# ------------------------------------------------------------------
# 1) 업비트 API 키 (텍스트파일에서 읽는 방식을 추천)
# ------------------------------------------------------------------
import os
key_file_path = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"

with open(key_file_path, "r", encoding="utf-8") as f:
    ACCESS_KEY = f.readline().strip()
    SECRET_KEY = f.readline().strip()

# ------------------------------------------------------------------
# 2) 매매 대상 및 제한
# ------------------------------------------------------------------
ALLOWED_TICKERS = ["KRW-BTC"]   # 초기엔 BTC만
MAX_COINS       = 2             # 동시에 최대 2종목까지만 보유

# ------------------------------------------------------------------
# 3) 전략 파라미터
# ------------------------------------------------------------------
RSI_PERIOD                    = 14
RSI_THRESHOLD                 = 10          # 최초 매수 RSI
RSI_THRESHOLD_ADDITIONAL      = 55          # 추매 RSI
INITIAL_INVEST_RATIO          = 0.05        # 보유 KRW * 비율
TARGET_PROFIT_RATE            = 0.0030      # +0.3 %
STOP_LOSS_RATE                = -0.6        # –60 %
MAINTAIN_PROFIT_RATE          = -0.0055     # –0.55 %
MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 60   # 초
MAX_ADDITIONAL_BUYS           = 1000
RSI_CALC_INTERVAL             = 60          # 초

# ------------------------------------------------------------------
# 4) 루프 주기 (웹소켓 버전은 1 초로 낮춤)
# ------------------------------------------------------------------
LOOP_INTERVAL = 1
