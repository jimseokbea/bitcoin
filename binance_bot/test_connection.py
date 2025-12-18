import os
import sys
from dotenv import load_dotenv
import ccxt
import pandas as pd

# Load Env
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
IS_TESTNET = os.getenv("BINANCE_TESTNET", "False").lower() == "true"

def test_system():
    print("🤖 Binance Bot System Verification 🤖")
    print(f"Mode: {'⚠️ TESTNET ⚠️' if IS_TESTNET else 'REAL MONEY'}")
    print("---------------------------------------")
    
    # 1. Check API Keys
    if not API_KEY or not SECRET_KEY:
        print("❌ Error: Missing API Keys in .env file.")
        print("Please copy .env.example to .env and fill in your keys.")
        return
    print("✅ Environment Variables Loaded")

    # 2. Connect
    try:
        options = {'defaultType': 'future'}
        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': SECRET_KEY,
            'enableRateLimit': True,
            'options': options
        })
        if IS_TESTNET:
            exchange.set_sandbox_mode(True) 
        
        exchange.load_markets()
        print("✅ Connected to Binance Futures API")
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    # 3. Data Check (BTC/USDT)
    try:
        symbol = "BTC/USDT"
        ohlcv = exchange.fetch_ohlcv(symbol, '15m', limit=5)
        if len(ohlcv) > 0:
            last_price = ohlcv[-1][4]
            print(f"✅ Market Data OK: {symbol} Price = {last_price}")
        else:
            print("⚠️ Market Data warning: No data returned.")
    except Exception as e:
        print(f"❌ Market Data Failed: {e}")

    # 4. Permissions & Balance Check
    try:
        balance = exchange.fetch_balance()
        usdt_bal = balance['info']['totalWalletBalance'] # Futures specific field
        print(f"✅ Account Permission OK. Balance: {float(usdt_bal):.2f} USDT")
    except Exception as e:
        print(f"❌ Account Check Failed (Check API Permissions): {e}")

    # 5. Position Check
    try:
        positions = exchange.fetch_positions([symbol])
        print(f"✅ Position Endpoint OK. Open Positions: {len(positions)}")
    except Exception as e:
        print(f"⚠️ Position Check Warning: {e}")

    print("---------------------------------------")
    print("🎉 System Ready! You can now run 'run_bot.bat'")

if __name__ == "__main__":
    test_system()
