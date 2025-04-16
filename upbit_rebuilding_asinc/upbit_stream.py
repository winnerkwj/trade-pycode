# upbit_rebuilding/upbit_stream.py
import asyncio, json, threading, time, logging, websockets

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

price_cache: dict[str, float] = {}
price_cache_ts: dict[str, float] = {}

class PriceStreamer(threading.Thread):
    def __init__(self, tickers, reconnect_interval=5):
        super().__init__(daemon=True)
        self._tickers = tickers
        self._tick_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reconnect_interval = reconnect_interval

    # --- 외부 API ---------------------------------------------------
    def update_tickers(self, tickers):
        with self._tick_lock:
            self._tickers = list(set(tickers))

    def stop(self):
        self._stop_evt.set()
    # ---------------------------------------------------------------

    async def _stream_loop(self):
        while not self._stop_evt.is_set():
            try:
                async with websockets.connect(UPBIT_WS_URL, ping_interval=60, ping_timeout=10) as ws:
                    with self._tick_lock:
                        codes = self._tickers.copy()
                    sub_msg = [
                        {"ticket": "price-stream"},
                        {"type": "ticker", "codes": codes, "isOnlyRealtime": True},
                        {"format": "SIMPLE"}
                    ]
                    await ws.send(json.dumps(sub_msg))
                    logging.info(f"[WS] subscribe {codes}")

                    while not self._stop_evt.is_set():
                        data = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(data)
                        if isinstance(msg, dict) and 'cd' in msg and 'tp' in msg:
                            tkr = msg['cd']
                            price_cache[tkr] = msg['tp']
                            price_cache_ts[tkr] = time.time()
            except Exception as e:
                logging.warning(f"[WS] reconnect due to {e}")
                await asyncio.sleep(self._reconnect_interval)

    def run(self):
        asyncio.run(self._stream_loop())
