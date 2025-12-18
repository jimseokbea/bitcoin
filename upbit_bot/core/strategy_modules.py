import pandas as pd
import pandas_ta as ta
import logging
from typing import Dict, Any, Tuple, Optional

# ---------------------------------------------------------
# 3. Market Filter (BTC 감시)
# ---------------------------------------------------------
class MarketFilter:
    def __init__(self, wrapper, btc_ticker="KRW-BTC"):
        self.wrapper = wrapper
        self.btc_ticker = btc_ticker

    def is_market_ok(self):
        """BTC가 1시간 전 대비 -0.5% 이상 폭락 중이면 False"""
        try:
            # 1시간 전 데이터를 비교하기 위해 60분봉 2개를 가져오거나, 5분봉 12개를 볼 수 있음.
            # 여기서는 60분봉 2개를 요청하여 비교
            df = self.wrapper.get_ohlcv(self.btc_ticker, interval="minute60", count=2)
            if df is None: 
                # 데이터가 없으면 보수적으로 False? 아니면 일단 통과?
                # User preference: "No -> 대기" aka False
                return False 
            
            # 현재가 vs 1시간 전 종가 (row index -1 is current partial candle, -2 is previous completed candle)
            # But get_ohlcv returns completed candles? No, pyupbit returns current progressing candle as last.
            curr = df['close'].iloc[-1]
            prev = df['close'].iloc[-2]
            change = (curr - prev) / prev
            
            if change <= -0.005: # -0.5% 이하로 하락시
                 logging.info(f"[MarketFilter] BTC Drop Warning: {change*100:.2f}%")
                 return False
            
            return True
        except Exception as e:
            logging.error(f"Market Filter Error: {e}")
            return True # 에러 시에는 멈추기보다 로그 남기고 통과하는게 일반적이지만, 안전 제일이면 False

# ---------------------------------------------------------
# 4. Signal Engine (가중치 점수제)
# ---------------------------------------------------------
class SignalEngine:
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        self.w = config['weights']
        self.ind_cfg = config['indicators']
        self.entry_threshold = config['entry_threshold']

    def _add_indicators(self, df):
        if len(df) < 30: return df

        # 1. Bollinger Bands
        bb = ta.bbands(df['close'], length=self.ind_cfg['bb']['length'], std=self.ind_cfg['bb']['std'])
        if bb is not None:
             # Find column starting with BBL (Robust way)
             bbl_cols = [c for c in bb.columns if c.startswith("BBL")]
             if bbl_cols:
                 df['bb_lower'] = bb[bbl_cols[0]]
             else:
                 df['bb_lower'] = 0
        else:
             df['bb_lower'] = 0
        
        # 2. RSI
        df['rsi'] = ta.rsi(df['close'], length=self.ind_cfg['rsi']['length'])
        
        # 3. MFI
        df['mfi'] = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=self.ind_cfg['mfi']['length'])
        
        # 4. ATR (손절용)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        # 5. 거래량 평균 (20개)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        # 6. Hammer 패턴 인식 
        # (row별 연산 대신 벡터 연산 후 shift하여 확인)
        body = (df['close'] - df['open']).abs()
        lower_shadow = df[['open', 'close']].min(axis=1) - df['low']
        
        # 해머 조건: 양봉 & 꼬리가 몸통 2배 & 몸통 존재
        df['is_hammer'] = (
            (df['close'] > df['open']) & 
            (lower_shadow > body * 2) & 
            (body > 0)
        )
        
        # 직전이 해머였는지 확인하기 위해 shift
        df['prev_is_hammer'] = df['is_hammer'].shift(1)
        df['prev_high'] = df['high'].shift(1)
        df['prev_low'] = df['low'].shift(1)
        df['prev_bb_lower'] = df['bb_lower'].shift(1)
        df['prev_rsi'] = df['rsi'].shift(1)
        df['prev_mfi'] = df['mfi'].shift(1)
        df['prev_volume'] = df['volume'].shift(1)
        
        return df

    def calculate_score(self, current_row, btc_ok):
        """
        실시간 분석을 위해 current_row (iloc[-1]) 사용.
        하지만 전략 로직상 '직전 캔들' 정보가 필요함.
        _add_indicators에서 shift(1) 된 컬럼들(prev_*)이 "직전 완성 캔들"의 정보임.
        current_row['close'] 등은 "현재 진행중인 캔들"의 정보임.
        """
        score = 0.0
        details = []

        # (1) BB 터치 (직전 저가가 BB 하단 근처)
        # prev_low <= prev_bb_lower * 1.005
        if current_row['prev_low'] <= current_row['prev_bb_lower'] * 1.005: 
            score += self.w.get('bb_touch', 0)
            details.append("BB")

        # (2) RSI 과매도 (직전 캔들 기준)
        if current_row['prev_rsi'] < self.ind_cfg['rsi']['oversold']:
            score += self.w.get('rsi_oversold', 0)
            details.append("RSI")
            
        # (3) MFI 과매도 (직전 캔들 기준)
        if current_row['prev_mfi'] < self.ind_cfg['mfi']['oversold']:
            score += self.w.get('mfi_oversold', 0)
            details.append("MFI")

        # (4) Hammer Confirm (직전이 해머였고, 현재가가 직전 고점을 뚫었는가?)
        # current_price > prev_high
        is_confirmed = False
        if current_row['prev_is_hammer'] and (current_row['close'] > current_row['prev_high']):
            is_confirmed = True
            score += self.w.get('hammer', 0)
            details.append("Hammer+Conf")

        # (5) 거래량 스파이크 (직전 캔들 거래량이 평균 대비 급등했었는지?)
        if current_row['prev_volume'] > current_row['vol_ma'] * self.ind_cfg['volume']['spike_factor']:
            score += self.w.get('volume_spike', 0)
            details.append("Vol")

        # (6) BTC 필터
        if btc_ok:
            score += self.w.get('btc_ok', 0)
            details.append("BTC_OK")
        
        return score, details

    def analyze(self, df: pd.DataFrame, btc_ok=True) -> Tuple[bool, float, float, Dict]:
        """
        return: (Signal(Bool), SL Price, TP Price, InfoDict)
        """
        df = self._add_indicators(df.copy())
        
        # 마지막 캔들(현재 진행중)만 확인
        row = df.iloc[-1]
        
        score, details = self.calculate_score(row, btc_ok)
        
        is_buy = (score >= self.entry_threshold)
        
        # SL/TP 계산 (ATR 기반)
        # Entry price는 현재가(시장가)로 가정
        current_price = row['close']
        atr = row['atr']
        
        risk_cfg = self.cfg['risk']
        
        # SL = MAX(1.8%, 0.7 * ATR)
        sl_amt = max(current_price * risk_cfg['sl_min_pct'], atr * risk_cfg['sl_atr_mult'])
        sl_price = current_price - sl_amt
        
        # TP = 진입가 + (손절폭 * 1.5) or fixed 1.2%
        # User requested 1.2% target in example or RR based
        # Using snippet: tp_target: 0.012
        tp_price = current_price * (1 + risk_cfg['tp_target'])
        
        info = {
            "score": score,
            "details": details,
            "atr": atr,
            "current_price": current_price
        }
        
        return is_buy, sl_price, tp_price, info

# ---------------------------------------------------------
# 5. Risk Engine (진입 후 관리)
# ---------------------------------------------------------
class RiskEngine:
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config['risk']

    def check_exit(self, current_price, position_data: Dict[str, Any]):
        """
        position_data: { 'entry_price': float, 'sl': float, 'tp': float, 'entry_time': datetime }
        return: (Boolean 탈출여부, 사유)
        """
        # 1. Stop Loss
        if current_price <= position_data['sl']:
            return True, "StopLoss"

        # 2. Take Profit
        if current_price >= position_data['tp']:
            return True, "TakeProfit"

        # 3. Time Cut
        # Check happens in main loop utilizing timestamp
        
        return False, None
