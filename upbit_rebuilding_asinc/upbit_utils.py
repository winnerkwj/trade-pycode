# upbit_rebuilding_asinc/upbit_utils.py
"""
· 실시간 가격 :  웹소켓 캐시 → REST fallback
· RSI 계산     :  캐싱
· 호가 단위    :  tick_size()
"""
import time, logging, pyupbit
from .upbit_config   import RSI_PERIOD, RSI_CACHE_SEC
from .upbit_stream   import price_cache, price_cache_ts
from .upbit_exception import net_err           # ← net_err 이름 확정

# ---------- 호가 단위 ----------
def tick_size(p: float) -> float:
    table = [(2_000_000,1000),(1_000_000,500),(500_000,100),(100_000,50),
             (10_000,10),(1_000,1),(100,0.1),(10,0.01),(1,0.001),
             (0.1,0.0001),(0.01,0.00001),(0.001,0.000001),(0.0001,0.0000001)]
    for limit, tick in table:
        if p >= limit:
            return tick
    return 0.00000001

# ---------- 현재가 ----------
def get_current_price(tkr: str):
    """웹소켓 캐시 우선, 없으면 REST 호출"""
    now = time.time()
    if tkr in price_cache and now - price_cache_ts.get(tkr, 0) < 3:
        return price_cache[tkr]
    try:
        return pyupbit.get_current_price(tkr)
    except Exception as e:
        net_err(e); return None

# 별칭 – main 코드 호환
current_price = get_current_price

# ---------- RSI ----------
_rsi_cache, _ts = {}, {}
def rsi(tkr: str) -> float:
    now=time.time()
    if tkr in _rsi_cache and now - _ts.get(tkr,0) < RSI_CACHE_SEC:
        return _rsi_cache[tkr]
    try:
        df=pyupbit.get_ohlcv(tkr,"minute1",RSI_PERIOD*2)
        if df is None or df.empty: return 50.0
        delta=df['close'].diff().dropna()
        gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
        ag=gain.ewm(alpha=1/RSI_PERIOD,min_periods=RSI_PERIOD).mean()
        al=loss.ewm(alpha=1/RSI_PERIOD,min_periods=RSI_PERIOD).mean()
        value=100-100/(1+(ag/al).iloc[-1])
        _rsi_cache[tkr],_ts[tkr]=value,now
        return value
    except Exception as e:
        net_err(e); return 50.0
get_tick_size = tick_size 
