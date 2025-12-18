
import os
import sys
import time
import pandas as pd
import numpy as np
import ccxt
from dotenv import load_dotenv
from core.strategy_modules import SignalEngine

# Load Env
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

class StressTestSignalEngine(SignalEngine):
    def analyze(self, df_3m, df_1h):
        """
        [STRESS TEST MODE]
        Relaxed constraints to force SIDE entries and Trend entries.
        """
        if df_3m is None or len(df_3m) < 200 or df_1h is None or len(df_1h) < 50:
            return None, 0, 0, {}

        # Indicators (Use parent logic)
        df_3m = self._calculate_3m_indicators(df_3m)
        df_1h = self._calculate_1h_indicators(df_1h)

        last_3m = df_3m.iloc[-2]
        last_1h = df_1h.iloc[-2]

        vwap = last_3m['vwap']
        vol = last_3m['volume']
        vol_ma = last_3m['vol_ma']
        rsi = last_3m['rsi']
        ema200 = last_3m['ema200']
        price = last_3m['close']
        rng_up = last_3m['rng_up']

        adx = last_1h['adx']
        atr_1h = last_1h['atr']

        # [MODIFIED] Relaxed Thresholds
        SIDE_ADX_THRESHOLD = 30 
        ENTRY_SCORE_THRESHOLD = 1.0 # Was 2.0 -> 1.0 (Almost any signal)
        RSI_OVERSOLD = 40 # Was 30 -> 40 (Easier entry)
        RSI_OVERBOUGHT = 60 # Was 70 -> 60 (Easier entry)

        # ==========================================
        # MODE 1: RANGE TRADING (Relaxed)
        # ==========================================
        if adx < SIDE_ADX_THRESHOLD:
            # Long: Oversold ONLY (Remove EMA200 safety)
            if rsi < RSI_OVERSOLD:
                sl = price - (atr_1h * 2.0)
                tp = price + (atr_1h * 2.5) 
                return 'buy', sl, tp, {'desc': f'Stress Side Buy (RSI {int(rsi)})', 'adx': adx}
            
            # Short: Overbought ONLY (Remove EMA200 safety)
            if rsi > RSI_OVERBOUGHT:
                sl = price + (atr_1h * 2.0)
                tp = price - (atr_1h * 2.5)
                return 'sell', sl, tp, {'desc': f'Stress Side Sell (RSI {int(rsi)})', 'adx': adx}
                
            return None, 0, 0, {}

        # ==========================================
        # MODE 2: TREND TRADING (Relaxed)
        # ==========================================
        score_long = 0
        score_short = 0
        
        # A. Volume
        if vol > (vol_ma * 1.3): 
            score_long += 3; score_short += 3
        # B. Trend
        if price > vwap: score_long += 3
        if price < vwap: score_short += 3
        # C. ADX
        if adx > 30: score_long += 2; score_short += 2

        # D. Breakout (Parent logic simplified fetch)
        try:
            current_price = df_3m['close'].iloc[-1]
            rng_curr = df_3m['rng_up'].iloc[-2]
            rng_prev = df_3m['rng_up'].iloc[-3]
            
            if rng_curr and not rng_prev and current_price > df_3m['high'].iloc[-2]:
                score_long += 5 
            elif not rng_curr and rng_prev and current_price < df_3m['low'].iloc[-2]:
                score_short += 5
        except: pass

        # Execution with Lower Threshold
        if score_long >= ENTRY_SCORE_THRESHOLD:
             sl = price - (atr_1h * 1.2)
             tp = price + (atr_1h * 3.0)
             return 'buy', sl, tp, {'desc': f'Stress Trend Buy ({score_long})', 'adx': adx}

        if score_short >= ENTRY_SCORE_THRESHOLD:
             sl = price + (atr_1h * 1.2)
             tp = price - (atr_1h * 3.0)
             return 'sell', sl, tp, {'desc': f'Stress Trend Sell ({score_short})', 'adx': adx}

        return None, 0, 0, {'desc': '', 'adx': adx}


class ComparativeBacktester:
    def __init__(self, symbol="DOGE/USDT", days=30):
        self.symbol = symbol
        self.days = days
        self.engine = StressTestSignalEngine() # Use Stress Engine
        self.exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': SECRET_KEY,
            'options': {'defaultType': 'future'}
        })
        self.fee_rate = 0.0005 

    def fetch_data(self):
        print(f"⏳ 데이터 다운로드 중 ({self.days}일)...")
        since = self.exchange.milliseconds() - (self.days * 24 * 60 * 60 * 1000)
        
        all_ohlcv = []
        while since < self.exchange.milliseconds():
            ohlcv = self.exchange.fetch_ohlcv(self.symbol, '3m', since, limit=1000)
            if not ohlcv: break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 180000 
            time.sleep(0.2)
            if len(all_ohlcv) > (self.days * 480 * 1.5): break 
            
        df_3m = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_3m['datetime'] = pd.to_datetime(df_3m['timestamp'], unit='ms')
        
        since_1h = self.exchange.milliseconds() - (self.days * 24 * 60 * 60 * 1000) - (200*3600*1000) 
        ohlcv_1h = []
        while since_1h < self.exchange.milliseconds():
             batch = self.exchange.fetch_ohlcv(self.symbol, '1h', since_1h, limit=1000)
             if not batch: break
             ohlcv_1h.extend(batch)
             since_1h = batch[-1][0] + 3600000 
             time.sleep(0.1)
             
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        return df_3m, df_1h

    def calculate_mdd(self, equity_curve):
        if len(equity_curve) < 2: return 0.0
        peak = np.maximum.accumulate(equity_curve)
        drawdown = (equity_curve - peak) / peak
        return drawdown.min() * 100 

    def calculate_consecutive_losses(self, df_subset):
        if df_subset.empty: return 0
        max_streak = 0
        current_streak = 0
        for pnl in df_subset['pnl']:
            if pnl < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        return max_streak

    def run_simulation(self, df_3m, df_1h, mode="BASELINE"):
        init_equity = 1000.0
        equity = init_equity
        equity_curve = [init_equity]
        
        position = None 
        trades_log = []
        
        start_idx = 200
        idx_1h = 0
        timestamps_1h = df_1h['timestamp'].values
        
        for i in range(start_idx, len(df_3m)):
            curr_3m = df_3m.iloc[i-200:i+1] # Window for engine
            curr_time = df_3m.iloc[i]['timestamp']
            
            idx_limit = np.searchsorted(timestamps_1h, curr_time, side='right')
            if idx_limit < 50: continue
            curr_1h_slice = df_1h.iloc[idx_limit-60:idx_limit]
            
            price = df_3m.iloc[i]['close']
            high = df_3m.iloc[i]['high']
            low = df_3m.iloc[i]['low']
            
            # --- Manage Position ---
            if position:
                side = position['type']
                entry = position['entry']
                amt = position['amount']
                regime = position['regime']
                
                # Check SL
                triggered_sl = False
                if side == 'long' and low <= position['sl']: triggered_sl = True
                elif side == 'short' and high >= position['sl']: triggered_sl = True
                
                if triggered_sl:
                    sl_price = position['sl'] 
                    pnl = (sl_price - entry) * amt if side == 'long' else (entry - sl_price) * amt
                    pnl -= (sl_price * amt * self.fee_rate)

                    equity += pnl
                    trades_log.append({
                        'timestamp': curr_time,
                        'market_regime': regime,
                        'risk_per_trade': position['risk_pct'],
                        'leverage': position['leverage'],
                        'pnl': pnl,
                        'equity': equity,
                        'result': 'loss'
                    })
                    position = None
                    equity_curve.append(equity)
                    continue

                # Check TP / Trail logic
                roi = (price - entry) / entry if side == 'long' else (entry - price) / entry
                
                # Separated Risk: SIDE Regime Logic
                if mode == "SEPARATED" and regime == "SIDE":
                    short_tp_roi = 0.008
                    
                    triggered_tp = False
                    if roi >= short_tp_roi: triggered_tp = True
                    
                    if triggered_tp:
                         tp_price = entry * (1 + short_tp_roi) if side == 'long' else entry * (1 - short_tp_roi)
                         pnl = (tp_price - entry) * amt if side == 'long' else (entry - tp_price) * amt
                         pnl -= (tp_price * amt * self.fee_rate)
                         equity += pnl
                         trades_log.append({
                            'timestamp': curr_time,
                            'market_regime': regime,
                            'risk_per_trade': position['risk_pct'],
                            'leverage': position['leverage'],
                            'pnl': pnl,
                            'equity': equity,
                            'result': 'win_side_fixed'
                        })
                         position = None
                         equity_curve.append(equity)
                         continue
                
                else: 
                    # BASELINE or TREND Logic
                    # 1. TP1 (50%)
                    if not position['tp1_done']:
                        tp1_trigger = 0.01
                        if roi >= tp1_trigger:
                            p_close = entry * 1.01 if side == 'long' else entry * 0.99
                            close_qty = amt * 0.5
                            pnl = (entry * roi) * close_qty 
                            pnl -= (p_close * close_qty * self.fee_rate)
                            
                            equity += pnl
                            position['amount'] -= close_qty
                            position['tp1_done'] = True
                            position['sl'] = entry 
                            
                            trades_log.append({
                                'timestamp': curr_time,
                                'market_regime': regime,
                                'risk_per_trade': position['risk_pct'],
                                'leverage': position['leverage'],
                                'pnl': pnl,
                                'equity': equity,
                                'result': 'tp1'
                            })
                            equity_curve.append(equity)

                    # 2. Trailing
                    trail_trigger = 0.015
                    trail_callback = 0.005
                    
                    if side == 'long': position['highest'] = max(position['highest'], high)
                    else: position['highest'] = min(position['highest'], low)
                    
                    if roi >= trail_trigger:
                        if side == 'long':
                            new_sl = position['highest'] * (1 - trail_callback)
                            if new_sl > position['sl']: position['sl'] = new_sl
                        else:
                            new_sl = position['highest'] * (1 + trail_callback)
                            if new_sl < position['sl']: position['sl'] = new_sl
                            
                        trail_hit = False
                        if side == 'long' and low <= position['sl']: trail_hit = True
                        elif side == 'short' and high >= position['sl']: trail_hit = True
                        
                        if trail_hit:
                             sl_price = position['sl']
                             pnl = (sl_price - entry) * amt if side == 'long' else (entry - sl_price) * amt
                             pnl -= (sl_price * amt * self.fee_rate)
                             equity += pnl
                             trades_log.append({
                                'timestamp': curr_time,
                                'market_regime': regime,
                                'risk_per_trade': position['risk_pct'],
                                'leverage': position['leverage'],
                                'pnl': pnl,
                                'equity': equity,
                                'result': 'win_trail'
                            })
                             position = None
                             equity_curve.append(equity)
                             continue

            # --- Open Position ---
            if not position:
                action, sl, tp, info = self.engine.analyze(curr_3m, curr_1h_slice)
                
                if action:
                    adx = info.get('adx', 0)
                    # [MODIFIED] Regime Definition for Stress Test
                    # Match the Engine Threshold
                    market_regime = "TREND" if adx >= 30 else "SIDE" 
                    
                    if mode == "BASELINE":
                        risk_pct = 0.013
                        leverage = 7
                    else: # SEPARATED
                        if market_regime == "SIDE":
                            risk_pct = 0.006
                            leverage = 4
                        else:
                            risk_pct = 0.013
                            leverage = 7
                            
                    dist = abs(price - sl)
                    if dist <= 0: continue
                    
                    risk_amt = equity * risk_pct
                    qty = risk_amt / dist
                    
                    max_qty = (equity * leverage) / price
                    qty = min(qty, max_qty)
                    
                    position = {
                        'type': action,
                        'amount': qty,
                        'entry': price,
                        'sl': sl,
                        'tp_target': tp,
                        'tp1_done': False,
                        'highest': price,
                        'regime': market_regime,
                        'risk_pct': risk_pct,
                        'leverage': leverage
                    }
                    equity -= (price * qty * self.fee_rate)

        return equity, trades_log, equity_curve

    def run_experiment(self):
        print(f"🚀 [Stress Test] 실험 시작: {self.symbol} ({self.days}일)")
        df_3m, df_1h = self.fetch_data()
        print(f"✅ 데이터 준비: 3m {len(df_3m)}개, 1h {len(df_1h)}개")
        
        # Run A: Baseline
        print("▶️ Baseline 실행 중...")
        eq_A, log_A, curve_A = self.run_simulation(df_3m, df_1h, mode="BASELINE")
        
        # Run B: Separated
        print("▶️ Separated 실행 중...")
        eq_B, log_B, curve_B = self.run_simulation(df_3m, df_1h, mode="SEPARATED")
        
        mdd_A = self.calculate_mdd(np.array(curve_A))
        mdd_B = self.calculate_mdd(np.array(curve_B))
        pnl_A = eq_A - 1000
        pnl_B = eq_B - 1000
        
        df_A = pd.DataFrame(log_A)
        df_B = pd.DataFrame(log_B)
        
        def analyze_regime(df, regime):
            if df.empty: return 0, 0, 0, 0, 0
            sub = df[df['market_regime'] == regime]
            if sub.empty: return 0, 0, 0, 0, 0
            
            loss_df = sub[sub['pnl'] < 0]
            total_loss = loss_df['pnl'].sum()
            avg_loss = loss_df['pnl'].mean() if not loss_df.empty else 0
            consec = self.calculate_consecutive_losses(sub)
            
            win_df = sub[sub['pnl'] > 0]
            max_win = win_df['pnl'].max() if not win_df.empty else 0
            top_avg = 0
            if not win_df.empty:
                top_count = int(len(win_df) * 0.2) + 1
                top_avg = win_df['pnl'].nlargest(top_count).mean()
                
            return total_loss, avg_loss, consec, max_win, top_avg

        s_loss_A, s_avg_A, s_con_A, s_max_A, s_top_A = analyze_regime(df_A, 'SIDE')
        s_loss_B, s_avg_B, s_con_B, s_max_B, s_top_B = analyze_regime(df_B, 'SIDE')
        
        t_loss_A, t_avg_A, t_con_A, t_max_A, t_top_A = analyze_regime(df_A, 'TREND')
        t_loss_B, t_avg_B, t_con_B, t_max_B, t_top_B = analyze_regime(df_B, 'TREND')

        print("\n📊 [스트레스 테스트 상세 결과]")
        print("=========================================")
        print(f"대상: {self.symbol} / 기간: {self.days}일")
        print("-----------------------------------------")
        print("1️⃣ Curve A (Baseline - 기존 리스크)")
        print(f"   - Final Equity: ${eq_A:.2f} (MDD: {mdd_A:.2f}%)")
        print(f"   - [SIDE] 총 손실: ${s_loss_A:.2f} (평균: ${s_avg_A:.2f})")
        print(f"   - [SIDE] 최대 연속 손실: {s_con_A}회")
        print(f"   - [TREND] 최대 수익: ${t_max_A:.2f} (Top20% Avg: ${t_top_A:.2f})")
        print("-----------------------------------------")
        print("2️⃣ Curve B (Separated - 방어 리스크)")
        print(f"   - Final Equity: ${eq_B:.2f} (MDD: {mdd_B:.2f}%)")
        print(f"   - [SIDE] 총 손실: ${s_loss_B:.2f} (평균: ${s_avg_B:.2f})")
        print(f"   - [SIDE] 최대 연속 손실: {s_con_B}회")
        print(f"   - [TREND] 최대 수익: ${t_max_B:.2f} (Top20% Avg: ${t_top_B:.2f})")
        print("=========================================")
        
        # Judgment
        points = 0
        reasons = []
        
        if abs(mdd_B) < abs(mdd_A):
            points += 1
            reasons.append("✅ MDD 감소 (방어 성공)")
            
        if abs(s_loss_B) < abs(s_loss_A):
             diff = abs(s_loss_A) - abs(s_loss_B)
             if diff > 1.0: # At least $1 difference
                 points += 1
                 reasons.append(f"✅ SIDE 손실 감소 (${s_loss_A:.2f} -> ${s_loss_B:.2f})")
             else:
                 reasons.append("⏺️ SIDE 손실 차이 미미")
        
        if s_con_B < s_con_A:
            reasons.append(f"✅ 연속 손실 감소 ({s_con_A} -> {s_con_B})")

        if points >= 1:
             print("🏆 판정: [성공] (분리 리스크가 손실을 줄였습니다)")
        else:
             print("🏆 판정: [실패/보류]")
             
        for r in reasons:
            print(r)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sym = sys.argv[1]
    else:
        sym = "DOGE/USDT"
    
    exp = ComparativeBacktester(sym, days=30)
    exp.run_experiment()
