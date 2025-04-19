# upbit_rebuilding_asinc/upbit_stream.py
import asyncio, json, threading, time, logging, websockets
UPBIT_WS = "wss://api.upbit.com/websocket/v1"

price_cache, price_cache_ts = {}, {}

class PriceStreamer(threading.Thread):
    """웹소켓 → price_cache"""
    def __init__(self, tickers, reconnect=5):
        super().__init__(daemon=True)
        self._tickers = list(tickers)
        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._reconnect = reconnect

    # ───────── 메서드 이름 두 가지 모두 지원 ─────────
    def update_tickers(self, tickers):
        with self._lock:
            self._tickers = list(set(tickers))

    update = update_tickers          # ★ alias 1줄 추가
    # ────────────────────────────────────────────────

minute_ohlc = {}        # {ticker: {'o','h','l','c','ts'}}
def _update_ohlc(tkr:str, price:float):
    now = time.time()
    ts = int(now//60)*60
    candle = minute_ohlc.get(tkr)
    if candle and candle['ts']==ts:
        candle['h'] = max(candle['h'], price)
        candle['l'] = min(candle['l'], price)
        candle['c'] = price
    else:
        minute_ohlc[tkr]={'o':price,'h':price,'l':price,'c':price,'ts':ts}



    def stop(self):
        self._stop.set()

    async def _loop(self):
        while not self._stop.is_set():
            try:
                async with websockets.connect(UPBIT_WS, ping_interval=60) as ws:
                    with self._lock:
                        codes = self._tickers.copy()
                    await ws.send(json.dumps([
                        {"ticket":"price-stream"},
                        {"type":"ticker","codes":codes,"isOnlyRealtime":True},
                        {"format":"SIMPLE"}
                    ]))
                    logging.info(f"[WS] subscribe {codes}")
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        if 'cd' in msg and 'tp' in msg:
                            price_cache[msg['cd']]   = msg['tp']
                            price_cache_ts[msg['cd']] = time.time()
                            _update_ohlc(msg['cd'], msg['tp'])
            except Exception as e:
                logging.warning(f"[WS] reconnect: {e}")
                await asyncio.sleep(self._reconnect)

    def run(self):
        asyncio.run(self._loop())
