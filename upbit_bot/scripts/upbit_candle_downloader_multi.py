import argparse
import csv
import time
import os
from datetime import datetime, timedelta
import requests

# Simple standalone downloader to avoid complex imports in scripts
BASE_URL = "https://api.upbit.com/v1"

def get_candles(market, unit, to_datetime=None, count=200):
    url = f"{BASE_URL}/candles/minutes/{unit}"
    params = {"market": market, "count": count}
    if to_datetime:
        params["to"] = to_datetime
    
    headers = {"accept": "application/json"}
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}, {response.text}")
        return []
    
    return response.json()

def download_candles(market, unit, start_date, end_date, outdir):
    os.makedirs(outdir, exist_ok=True)
    filename = f"{market.replace('KRW-', '').lower()}-{unit}m-{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}.csv"
    filepath = os.path.join(outdir, filename)
    
    print(f"Downloading {market} ({unit}m) from {start_date} to {end_date} -> {filepath}")
    
    all_candles = []
    current_to = end_date
    
    while True:
        to_str = current_to.strftime("%Y-%m-%d %H:%M:%S")
        candles = get_candles(market, unit, to_str, count=200)
        
        if not candles:
            break
            
        # Filter candles before start_date
        valid_candles = []
        for c in candles:
            # candle_date_time_kst: "2024-01-01T09:00:00"
            c_dt = datetime.strptime(c["candle_date_time_kst"], "%Y-%m-%dT%H:%M:%S")
            if c_dt >= start_date and c_dt <= end_date:
                valid_candles.append({
                    "timestamp": c["timestamp"],
                    "date_kst": c["candle_date_time_kst"],
                    "open": c["opening_price"],
                    "high": c["high_price"],
                    "low": c["low_price"],
                    "close": c["trade_price"],
                    "volume": c["candle_acc_trade_volume"]
                })
        
        all_candles.extend(valid_candles)
        
        # Check last candle time
        last_candle_dt = datetime.strptime(candles[-1]["candle_date_time_kst"], "%Y-%m-%dT%H:%M:%S")
        if last_candle_dt < start_date:
            break
            
        current_to = last_candle_dt
        time.sleep(0.1) # Rate limit
        print(f"Collected {len(all_candles)} candles... (Last: {last_candle_dt})")

    # Sort by date ascending
    all_candles.sort(key=lambda x: x["date_kst"])
    
    # Remove duplicates
    unique_candles = []
    seen_timestamps = set()
    for c in all_candles:
        if c["timestamp"] not in seen_timestamps:
            unique_candles.append(c)
            seen_timestamps.add(c["timestamp"])
            
    print(f"Total unique candles: {len(unique_candles)}")
    
    if unique_candles:
        keys = unique_candles[0].keys()
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            dict_writer = csv.DictWriter(f, keys)
            dict_writer.writeheader()
            dict_writer.writerows(unique_candles)
        print("Done.")
    else:
        print("No candles found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upbit Candle Downloader (Multi)")
    parser.add_argument("--markets", type=str, required=True, help="Comma separated markets (e.g. KRW-BTC,KRW-ETH)")
    parser.add_argument("--unit", type=int, default=5, help="Minute unit (1, 3, 5, 10, 15, 30, 60, 240)")
    parser.add_argument("--from_date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to_date", type=str, required=True, help="End date (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--outdir", type=str, default="data/candles", help="Output directory")
    
    args = parser.parse_args()
    
    markets = args.markets.split(",")
    start_dt = datetime.strptime(args.from_date, "%Y-%m-%d")
    end_dt = datetime.strptime(args.to_date, "%Y-%m-%d %H:%M:%S")
    
    for m in markets:
        download_candles(m.strip(), args.unit, start_dt, end_dt, args.outdir)
