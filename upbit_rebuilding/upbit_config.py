import pyupbit
import os

# ① 키 파일 절대 경로 지정
key_file_path = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"

# ② 파일 열어서 ACCESS / SECRET 읽기
with open(key_file_path, "r", encoding="utf-8") as file:
    ACCESS_KEY  = file.readline().strip()
    SECRET_KEY  = file.readline().strip()
# ------------------------------------------------------------
# upbit_config.py
#  - API키 / 환경 변수 / 파라미터 / 사용자 설정 모음
# ------------------------------------------------------------
# 업비트 API 키 (파일에서 읽어오거나 하드코딩)
# ACCESS_KEY = "여기에_접근키를_적어주세요"
# SECRET_KEY = "여기에_비밀키를_적어주세요"

# 매수 허용 종목 (처음엔 BTC만)
ALLOWED_TICKERS = ["KRW-BTC"]
# 동시에 보유할 수 있는 최대 종목 개수
MAX_COINS = 2

# 매매 전략 파라미터
RSI_PERIOD = 14
RSI_THRESHOLD = 10  # 처음 진입(매수)할 때의 RSI 기준
RSI_THRESHOLD_ADDITIONAL = 55  # 추매할 때의 RSI 기준
INITIAL_INVEST_RATIO = 0.05    # 매수비중 (보유 KRW * 이 값)
TARGET_PROFIT_RATE = 0.0030    # 지정가 매도 목표 수익률
STOP_LOSS_RATE = -0.6          # 손절
MAINTAIN_PROFIT_RATE = -0.0055 # 추가매수 트리거가 되는 손익률
MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 60  # 추매 사이 최소 간격(초)
MAX_ADDITIONAL_BUYS = 1000     # 최대 추매 횟수

# 매매 로직 실행 주기 (초)
LOOP_INTERVAL = 5

# RSI 재계산 주기 (초) - 너무 자주 get_ohlcv() 호출하면 API 제한 우려
RSI_CALC_INTERVAL = 60
