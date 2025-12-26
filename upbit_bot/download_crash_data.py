"""
Download historical BTC crash data for backtesting
Target periods:
- 2018-11 (worst crash ~-37%)
- 2020-03 (COVID crash ~-25%)
- 2022-06 (LUNA/3AC crash ~-38%)
"""
import pandas as pd
import pyupbit
from datetime import datetime, timedelta
import time
import os

def download_upbit_data(ticker="KRW-BTC", interval="minute5", start_date=None, end_date=None, filename=None):
    """
    Download historical OHLCV data from Upbit.
    Note: Upbit API has limits on how far back data is available.
    """
    print(f"Downloading {ticker} data from {start_date} to {end_date}...")
    
    all_data = []
    current_to = end_date
    
    while True:
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, to=current_to, count=200)
            if df is None or len(df) == 0:
                print(f"No more data available before {current_to}")
                break
            
            all_data.append(df)
            oldest_date = df.index[0]
            
            print(f"Fetched {len(df)} candles, oldest: {oldest_date}")
            
            if oldest_date <= pd.Timestamp(start_date):
                break
            
            current_to = oldest_date - timedelta(minutes=1)
            time.sleep(0.2)  # Rate limit
            
        except Exception as e:
            print(f"Error: {e}")
            break
    
    if not all_data:
        print("No data fetched!")
        return None
    
    # Combine and sort
    combined = pd.concat(all_data)
    combined = combined[~combined.index.duplicated(keep='first')]
    combined = combined.sort_index()
    
    # Filter to target range
    combined = combined[combined.index >= pd.Timestamp(start_date)]
    combined = combined[combined.index <= pd.Timestamp(end_date)]
    
    # Save
    if filename:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        combined.reset_index(inplace=True)
        # pyupbit returns: index(datetime), open, high, low, close, volume, value
        # Rename index column to 'date' and drop 'value' if present
        combined.rename(columns={'index': 'date'}, inplace=True)
        if 'value' in combined.columns:
            combined = combined.drop(columns=['value'])
        combined.to_csv(filename, index=False)
        print(f"Saved {len(combined)} candles to {filename}")
    
    return combined

if __name__ == "__main__":
    output_dir = "upbit_bot/data/candles"
    
    # Try to download crash periods
    crash_periods = [
        # Most recent first (more likely to be available)
        ("2022-06-01", "2022-06-30", "btc-5m-202206-luna-crash.csv"),
        ("2020-03-01", "2020-03-31", "btc-5m-202003-covid-crash.csv"),
        ("2018-11-01", "2018-11-30", "btc-5m-201811-crash.csv"),
    ]
    
    for start, end, fname in crash_periods:
        filepath = os.path.join(output_dir, fname)
        print(f"\n{'='*50}")
        print(f"Attempting: {fname}")
        print(f"{'='*50}")
        
        result = download_upbit_data(
            ticker="KRW-BTC",
            interval="minute5",
            start_date=start,
            end_date=end,
            filename=filepath
        )
        
        if result is not None and len(result) > 0:
            print(f"✅ Success: {len(result)} candles")
        else:
            print(f"❌ Failed or no data available for {start} to {end}")
        
        time.sleep(1)
    
    print("\n\nDone! Check upbit_bot/data/candles/ for available data.")
