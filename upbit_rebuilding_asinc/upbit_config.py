# upbit_rebuilding_asinc/upbit_config.py
"""
전략 · 환경 · 일정 파라미터를 한곳에 모은 파일
────────────────────────────────────────────
코인/파라미터를 변경하고 싶을 때 이 파일만 수정하면 됩니다.
"""

# ─────────────────────────────────────────
# 1)  Upbit API 키
# ─────────────────────────────────────────
KEY_FILE = r"C:\Users\winne\OneDrive\바탕 화면\upbit_key.txt"   # ← 키 파일 경로
with open(KEY_FILE, "r", encoding="utf-8") as f:
    ACCESS_KEY, SECRET_KEY = [line.strip() for line in f.readlines()[:2]]

# ─────────────────────────────────────────
# 2)  매매 대상 및 보유 한도
# ─────────────────────────────────────────
ALLOWED_TICKERS = [
    "KRW-BTC",
    "KRW-ETH",
    "KRW-SOL",
    "KRW-XRP",
]                       # 리스트에 원하는 코인 심볼 추가
MAX_COINS       = 2     # 동시에 보유할 최대 종목 수

# ─────────────────────────────────────────
# 3)  전략 파라미터
# ─────────────────────────────────────────
# ── RSI / 진입 · 추매 ────────────────────
RSI_PERIOD            = 14
RSI_THRESHOLD         = 10      # 최초 진입 RSI
RSI_CUSTOM_TRIGGER    = 35      # 정밀 추매 RSI (0 → OFF)

# ── 손익률 · 평단 ───────────────────────
TARGET_PROFIT_RATE    = 0.0030  # +0.30 % 매도 목표
MAINTAIN_PROFIT_RATE  = -0.0055 # -0.55 % 정밀 추매 트리거
STOP_LOSS_RATE        = -0.6    # -60 % 손절
INITIAL_INVEST_RATIO  = 0.05    # 잔고 5 % 첫 매수

# ── 추매 제약 ───────────────────────────
MIN_INTERVAL_BETWEEN_ADDITIONAL_BUYS = 60   # 초 (정밀 추매 최소 간격)

# ─────────────────────────────────────────
# 4)  주기 · 캐시
# ─────────────────────────────────────────
RSI_CACHE_SEC         = 60      # RSI 계산 결과 캐시(초)
LOOP_INTERVAL         = 1       # 메인 루프 주기(초)
TICKER_FILTER_INTERVAL = 900    # 15 분(초)마다 거래정지·상폐 종목 필터 갱신
WS_OHLC_USE           = True    # 웹소켓 1분 OHLC 캐시 사용 여부 (False → REST만)
