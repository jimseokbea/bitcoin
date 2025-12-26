import pandas as pd
# import pandas_ta as ta (Lazy import inside methods to avoid dependency issues)
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
        # ta.volatility.BollingerBands
        from ta.volatility import BollingerBands, AverageTrueRange
        from ta.momentum import RSIIndicator
        from ta.volume import MFIIndicator
        
        # BB
        bb_indicator = BollingerBands(close=df['close'], window=self.ind_cfg['bb']['length'], window_dev=self.ind_cfg['bb']['std'])
        df['bb_lower'] = bb_indicator.bollinger_lband()
        
        # 2. RSI
        rsi_indicator = RSIIndicator(close=df['close'], window=self.ind_cfg['rsi']['length'])
        df['rsi'] = rsi_indicator.rsi()
        
        # 3. MFI
        mfi_indicator = MFIIndicator(high=df['high'], low=df['low'], close=df['close'], volume=df['volume'], window=self.ind_cfg['mfi']['length'])
        df['mfi'] = mfi_indicator.money_flow_index()
        
        # 4. ATR (손절용)
        atr_indicator = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['atr'] = atr_indicator.average_true_range()

        # 5. ADX (추세 강도용 - Side Mode) using config
        adx_len = self.cfg.get('safety_pins', {}).get('side_mode', {}).get('adx_period', 14)
        from ta.trend import ADXIndicator
        # ADXIndicator returns series by default accessors
        adx_ind = ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=adx_len)
        df['adx'] = adx_ind.adx()

        # 6. 거래량 평균 (20개)
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

        # (6) BTC 필터 (만약 Gate 모드가 아닌 Score 모드일 경우 가산점)
        # Gate Mode는 analyze 함수 레벨에서 Hard Cut 하므로 여기선 "BTC OK" 상태면 점수 줌 (Score 모드 호환)
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

        # --- A. Min Volatility Filter ---
        min_vol_pct = self.cfg.get('risk', {}).get('min_volatility_pct', 0.0)
        current_price = row['close']
        atr = row['atr']
        
        if (atr / current_price) < min_vol_pct:
            return False, 0.0, 0.0, {
                "score": 0, "details": ["LowVolatility"], 
                "atr": atr, "current_price": current_price, "adx": row.get('adx', 0)
            }

        # --- B. BTC Gate Filter Check ---
        # config에 'btc_filter'가 있고 mode가 'gate'인데 btc_ok가 False면 -> 무조건 False 리턴
        btc_filter_cfg = self.cfg.get('btc_filter', {})
        if btc_filter_cfg.get('enabled', False) and btc_filter_cfg.get('mode') == 'gate':
            if not btc_ok:
                return False, 0.0, 0.0, {
                    "score": 0, "details": ["BTC_Gate_Block"], 
                    "atr": atr, "current_price": current_price, "adx": row.get('adx', 0)
                }
        
        score, details = self.calculate_score(row, btc_ok)
        
        is_buy = (score >= self.entry_threshold)
        
        risk_cfg = self.cfg['risk']
        
        # SL = MAX(1.8%, 0.7 * ATR)
        sl_amt = max(current_price * risk_cfg['sl_min_pct'], atr * risk_cfg['sl_atr_mult'])
        sl_price = current_price - sl_amt
        
        # TP = 진입가 + (손절폭 * 1.5) or fixed 1.2%
        tp_price = current_price * (1 + risk_cfg['tp_target'])
        
        info = {
            "score": score,
            "details": details,
            "atr": atr,
            "current_price": current_price,
            "adx": row.get('adx', 0) # ADX 전달
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
