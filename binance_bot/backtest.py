import os
import sys
import time
import pandas as pd
import ccxt
from dotenv import load_dotenv
from core.strategy_modules import SignalEngine

# Load Env
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

class Backtester:
    def __init__(self, symbol="SOL/USDT", days=7):
        self.symbol = symbol
        self.days = days
        self.engine = SignalEngine()
        self.exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': SECRET_KEY,
            'options': {'defaultType': 'future'}
        })
        
        # Config (Same as Main)
        self.leverage = 7
        self.risk_per_trade = 0.013
        self.init_equity = 1000.0
        
        # Trailing
        self.tp1_trigger = 0.010
        self.trail_trigger = 0.015
        self.trail_callback = 0.005
        self.fee_rate = 0.0005 # 0.05%

    def fetch_data(self):
        print(f"⏳ 데이터 다운로드 중 ({self.days}일)...")
        # Calc limit
        # 3m candles per day = 480. 7 days = 3360.
        # CCXT default limit 500 or 1000. Need loop.
        # Simplify: fetch last 1000 (approx 2 days of 3m) for quick test, 
        # or implement since param.
        
        since = self.exchange.milliseconds() - (self.days * 24 * 60 * 60 * 1000)
        
        all_ohlcv = []
        while since < self.exchange.milliseconds():
            ohlcv = self.exchange.fetch_ohlcv(self.symbol, '3m', since, limit=1000)
            if not ohlcv: break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 180000 # +3m
            time.sleep(0.5)
            
            if len(all_ohlcv) > (self.days * 480): break # Safety break
            
        df_3m = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_3m['datetime'] = pd.to_datetime(df_3m['timestamp'], unit='ms')
        
        # 1H Data (just fetch simple)
        since_1h = self.exchange.milliseconds() - (self.days * 24 * 60 * 60 * 1000)
        ohlcv_1h = self.exchange.fetch_ohlcv(self.symbol, '1h', since_1h, limit=500)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        return df_3m, df_1h

    def run(self):
        df_3m, df_1h = self.fetch_data()
        print(f"✅ 데이터 준비 완료: 3m({len(df_3m)}), 1h({len(df_1h)})")
        
        equity = self.init_equity
        position = None # {type, amount, entry, sl, tp1_done, highest}
        trades_log = []
        
        # Iterate
        # Need window for SignalEngine (needs ~200 candles)
        start_idx = 200
        
        for i in range(start_idx, len(df_3m)):
            # Slice Data
            curr_3m = df_3m.iloc[i-200:i+1] # Pass full window ending at i
            curr_1h = df_1h # Passing full 1h is risky if lookahead, but engine uses iloc[-2]. 
                            # Need to slice 1h to match time? 
                            # Complex. Approximate: Pass full 1h, engine checks iloc[-2] of *Provided DF*.
                            # Correct way: Filter 1h where time <= current 3m time.
            curr_time = df_3m.iloc[i]['timestamp']
            curr_1h_slice = df_1h[df_1h['timestamp'] <= curr_time]
            
            if len(curr_1h_slice) < 50: continue

            # Current Candle (Simulating Close of i)
            price = df_3m.iloc[i]['close']
            high = df_3m.iloc[i]['high']
            low = df_3m.iloc[i]['low']
            
            # 1. Manage Position
            if position:
                side = position['type']
                entry = position['entry']
                amt = position['amount']
                
                # Check SL (Low for Long, High for Short)
                if side == 'long':
                    if low <= position['sl']:
                        # Stopped Out
                        loss = (position['sl'] - entry) * amt
                        equity += loss
                        equity -= (price * amt * self.fee_rate) # Fee
                        trades_log.append({'res': 'loss', 'pnl': loss})
                        position = None
                        continue
                        
                elif side == 'short':
                    if high >= position['sl']:
                        loss = (entry - position['sl']) * amt
                        equity += loss
                        equity -= (price * amt * self.fee_rate)
                        trades_log.append({'res': 'loss', 'pnl': loss})
                        position = None
                        continue

                # Check TP1
                roi = (price - entry) / entry if side == 'long' else (entry - price) / entry
                
                if not position['tp1_done'] and roi >= self.tp1_trigger:
                    # Sell 50%
                    pnl = (entry * roi) * (amt * 0.5)
                    equity += pnl
                    position['amount'] *= 0.5
                    position['tp1_done'] = True
                    position['sl'] = entry # Break Even
                    trades_log.append({'res': 'win_tp1', 'pnl': pnl})
                
                # Check Trail
                if roi >= self.trail_trigger:
                    if side == 'long':
                        position['highest'] = max(position['highest'], high)
                        new_sl = position['highest'] * (1 - self.trail_callback)
                        if new_sl > position['sl']: position['sl'] = new_sl
                    else:
                        position['highest'] = min(position['highest'], low)
                        new_sl = position['highest'] * (1 + self.trail_callback)
                        if new_sl < position['sl']: position['sl'] = new_sl
                    
                    # Check Trailing Stop Hit (same candle wick?)
                    if side == 'long' and low <= position['sl']:
                         pnl = (position['sl'] - entry) * position['amount']
                         equity += pnl
                         trades_log.append({'res': 'win_trail', 'pnl': pnl})
                         position = None
                    elif side == 'short' and high >= position['sl']:
                         pnl = (entry - position['sl']) * position['amount']
                         equity += pnl
                         trades_log.append({'res': 'win_trail', 'pnl': pnl})
                         position = None

            # 2. Open Position
            if not position:
                action, sl, tp, _ = self.engine.analyze(curr_3m, curr_1h_slice)
                
                if action:
                    # Calc Size
                    risk_amt = equity * self.risk_per_trade
                    dist = abs(price - sl)
                    if dist == 0: continue
                    qty = risk_amt / dist
                    
                    position = {
                        'type': action,
                        'amount': qty,
                        'entry': price,
                        'sl': sl,
                        'tp1_done': False,
                        'highest': price
                    }
                    equity -= (price * qty * self.fee_rate) # Entry Fee

        # Report
        idx_win = len([t for t in trades_log if t['pnl'] > 0])
        idx_loss = len([t for t in trades_log if t['pnl'] <= 0])
        total_pnl = equity - self.init_equity
        win_rate = (idx_win / (idx_win + idx_loss)) * 100 if (idx_win+idx_loss) > 0 else 0
        
        print(f"\n🧪 [백테스팅 결과] {self.symbol} ({self.days}일)")
        print(f"----------------------------------------")
        print(f"초기 자본: ${self.init_equity:.2f}")
        print(f"최종 자본: ${equity:.2f} ({((equity/self.init_equity)-1)*100:.2f}%)")
        print(f"총 거래수: {len(trades_log)}회")
        print(f"승률 (Win Rate): {win_rate:.2f}%")
        print(f"총 수익 (PnL): ${total_pnl:.2f}")
        print(f"----------------------------------------")

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SOL/USDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        
    bt = Backtester(sym, days=days)
    bt.run()
