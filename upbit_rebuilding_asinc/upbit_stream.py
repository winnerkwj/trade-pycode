import asyncio
import json
import threading
import time
import logging
import websockets

UPBIT_WS = "wss://api.upbit.com/websocket/v1"

price_cache, price_cache_ts = {}, {}
minute_ohlc = {}

def _update_ohlc(tkr, price):
    now_ts = int(time.time() // 60) * 60
    candle = minute_ohlc.get(tkr)
    if candle and candle['ts'] == now_ts:
        candle['h'] = max(candle['h'], price)
        candle['l'] = min(candle['l'], price)
        candle['c'] = price
    else:
        minute_ohlc[tkr] = {'o': price, 'h': price, 'l': price, 'c': price, 'ts': now_ts}

class PriceStreamer(threading.Thread):
    def __init__(self, tickers, reconnect=5):
        super().__init__(daemon=True)
        self._tickers = list(tickers)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reconnect = reconnect

    def update_tickers(self, tickers):
        with self._lock:
            self._tickers = list(set(tickers))

    def stop(self):
        self._stop.set()

    async def _loop(self):
        while not self._stop.is_set():
            try:
                async with websockets.connect(UPBIT_WS, ping_interval=60) as ws:
                    with self._lock:
                        codes = self._tickers.copy()
                    await ws.send(json.dumps([
                        {"ticket": "price-stream"},
                        {"type": "ticker", "codes": codes, "isOnlyRealtime": True},
                        {"format": "SIMPLE"}
                    ]))
                    logging.info(f"[WS] subscribe {codes}")
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        if 'cd' in msg and 'tp' in msg:
                            price_cache[msg['cd']] = msg['tp']
                            price_cache_ts[msg['cd']] = time.time()
                            _update_ohlc(msg['cd'], msg['tp'])
            except Exception as e:
                logging.warning(f"[WS] reconnect: {e}")
                await asyncio.sleep(self._reconnect)

    def run(self):
        asyncio.run(self._loop())
