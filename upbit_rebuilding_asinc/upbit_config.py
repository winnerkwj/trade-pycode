"""
전략 · 환경 설정
"""

# 1) API 키 ----------------------------------------------------------
KEY_FILE = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"
with open(KEY_FILE, "r", encoding="utf-8") as f:
    ACCESS_KEY, SECRET_KEY = [line.strip() for line in f.readlines()[:2]]

# ─── Top N + 필터/회전 주기 ───
TOP_N_TICKERS           = 60             # Top N 개 종목
TICKER_FILTER_INTERVAL  = 15 * 60        # 필터 재적용 주기 (초)
TICKER_ROTATE_INTERVAL  = 60 * 60        # Top N 갱신 주기 (초)

# ─── 포지션 제한 ───
MAX_COINS               = 6              # 동시에 보유 가능한 종목 수

# ─── RSI 설정 ───
RSI_PERIOD              = 14             # RSI 연산 기간
RSI_CACHE_SEC           = 60             # RSI 캐시 유효 시간 (초)
WS_OHLC_USE             = True           # WS로 받은 OHLC 캐시 사용 여부

RSI_THRESHOLD           = 20             # 1차 매수 RSI 기준
RSI_CUSTOM_TRIGGER      = 15             # 정밀추매 RSI 기준

# ─── 수익/손절/추매 설정 ───
TARGET_PROFIT_RATE      = 0.003          # 목표 수익률 (0.3%)
MAINTAIN_PROFIT_RATE    = -0.0055        # 추가매수 진입 수익률 기준 (-0.55%)
STOP_LOSS_RATE          = -0.6           # 손절 수익률 기준 (-60%)

# ─── 투자비율·쿨다운·루프 인터벌 ───
INITIAL_INVEST_RATIO                = 0.05    # 전체 KRW 대비 1차 매수 비율
MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 600    # 추가매수 최소 간격 (초)
LOOP_INTERVAL                        = 1      # 메인 루프 인터벌 (초)
