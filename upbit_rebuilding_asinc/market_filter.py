"""
market_filter.py
────────────────
Upbit `/v1/market/all?isDetails=true` 만 사용해
  • market_warning == 'CAUTION'
  • state          != 'ACTIVE'   (거래정지, 상장폐지 예정)
종목을 걸러 낸다.

함수
  fetch_filtered_tickers(origin_list, cache_sec=900)
    → origin_list 에서 제외 종목을 뺀 새로운 리스트 반환
      (제외된 심볼 · 사유를 INFO 로그)
"""
from __future__ import annotations
import time, logging, requests
from collections import defaultdict
from .upbit_exception import net_err

_UPBIT_URL = "https://api.upbit.com/v1/market/all?isDetails=true"

# 캐싱
_last_fetch  = 0.0
_cache_list  : list[str] = []

__all__ = ["fetch_filtered_tickers"]     # 외부 import 공개

def _upbit_banned() -> dict[str, str]:
    """
    return { 'KRW-BTC': 'CAUTION', 'KRW-XYZ': 'SUSPENDED', ... }
    """
    reasons = {}
    try:
        data = requests.get(_UPBIT_URL, timeout=5).json()
        for item in data:
            market = item["market"]
            if item.get("market_warning") == "CAUTION":
                reasons[market] = "CAUTION"
            elif item.get("state") != "ACTIVE":
                reasons[market] = item.get("state", "SUSPENDED")
    except Exception as e:
        net_err(e)
    return reasons

def fetch_filtered_tickers(origin_list: list[str],
                           cache_sec: int = 900) -> list[str]:
    """
    origin_list : ALLOWED_TICKERS (['KRW-BTC', …])
    cache_sec   : 캐시 주기(초)
    return      : 필터링 결과 리스트
    """
    global _last_fetch, _cache_list
    now = time.time()
    if now - _last_fetch < cache_sec and _cache_list:
        return _cache_list

    banned_map = _upbit_banned()              # 사유 딕셔너리
    banned     = set(banned_map) & set(origin_list)
    filtered   = [t for t in origin_list if t not in banned]

    if banned:
        # 사유별 집계
        reason_grp = defaultdict(list)
        for m in banned:
            reason_grp[banned_map[m]].append(m)
        # 로그 출력
        parts = [f"{k}:{len(v)}" for k, v in reason_grp.items()]
        logging.info(f"[FILTER] 제외 {len(banned)}개 ({', '.join(parts)}) / 사용 {filtered}")
    else:
        logging.info("[FILTER] 제외 종목 없음")

    _cache_list, _last_fetch = filtered, now
    return filtered
