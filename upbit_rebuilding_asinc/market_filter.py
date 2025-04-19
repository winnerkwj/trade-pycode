"""
Upbit 경고(CAUTION)·거래정지(STOP/SUSPENDED) 필터
공개 REST API 요청 제한: 30 req/sec
"""
import time
import logging
import requests
from collections import defaultdict
from .upbit_exception import net_err
from .upbit_utils     import public_api_limiter

_UPBIT_URL = "https://api.upbit.com/v1/market/all?isDetails=true"
__all__   = ["fetch_filtered_tickers"]

_last_fetch : float     = 0.0
_cache_list : list[str] = []

def _upbit_banned() -> dict[str,str]:
    reasons = {}
    try:
        with public_api_limiter:
            data = requests.get(_UPBIT_URL, timeout=5).json()
        for item in data:
            market = item.get("market")
            if not market:
                continue
            warn  = item.get("market_warning")
            state = item.get("state")
            if warn == "CAUTION":
                reasons[market] = "CAUTION"
            elif state and state.lower() != "active":
                reasons[market] = state.upper()
    except Exception as e:
        net_err(e)
    return reasons

def fetch_filtered_tickers(origin_list: list[str],
                           cache_sec: int = 900) -> list[str]:
    global _last_fetch, _cache_list
    now = time.time()
    if now - _last_fetch < cache_sec and _cache_list:
        return _cache_list

    banned_map = _upbit_banned()
    banned     = set(banned_map) & set(origin_list)
    filtered   = [t for t in origin_list if t not in banned]

    if banned:
        grp   = defaultdict(list)
        for m in banned:
            grp[banned_map[m]].append(m)
        parts = [f"{k}:{len(v)}" for k,v in grp.items()]
        logging.info(f"[FILTER] 제외 {len(banned)}개 ({', '.join(parts)}) / 사용 {filtered}")
    else:
        logging.info("[FILTER] 제외 종목 없음")

    _cache_list, _last_fetch = filtered, now
    return filtered
