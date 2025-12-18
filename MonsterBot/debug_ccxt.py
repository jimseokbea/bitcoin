
import ccxt
print("CCXT version:", ccxt.__version__)
ex = ccxt.binance()
print("Time:", ex.milliseconds())
try:
    data = ex.fetch_ohlcv('DOGE/USDT', '3m', limit=10)
    print("Fetched:", len(data))
except Exception as e:
    print("Fetch Error:", e)
