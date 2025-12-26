import copy
import time
import pandas as pd
import ta as ta_lib  # Using ta library instead of pandas_ta
import numpy as np
from core.system_utils import LOGGER

class StrategyTuner:
    def __init__(self, config, enabled=True, tune_interval=300):
        """
        Initialize StrategyTuner.
        
        Args:
            config: Base configuration dictionary
            enabled: Whether tuner is enabled
            tune_interval: Seconds between tune calls (default 5 minutes)
        """
        self.base_cfg = copy.deepcopy(config)
        self.current_cfg = copy.deepcopy(config)
        self.enabled = enabled
        self.tune_interval = tune_interval
        self.last_tune_ts = 0
        self.mode = "Neutral"
        self.last_mode_change_ts = 0

    def get_market_regime(self, df):
        """시장 데이터 기반 장세 판단 (데이터 부족 방어 포함)"""
        if df is None or len(df) < 50:
            return "Neutral", 0, 0

        try:
            # 2. 지표 계산 (Missing Data Check & Calc)
            cols = df.columns
            # BB Check - using ta library
            if 'bb_upper' not in cols:
                # Calculate Bollinger Bands using ta library
                bb_indicator = ta_lib.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
                df['bb_upper'] = bb_indicator.bollinger_hband()
                df['bb_lower'] = bb_indicator.bollinger_lband()
            
            # ADX Check - using ta library
            if 'adx' not in cols:
                adx_indicator = ta_lib.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
                df['adx'] = adx_indicator.adx()

            if 'bb_upper' not in df.columns or 'adx' not in df.columns:
                 return "Neutral", 0, 0
                 
            bb_upper = df['bb_upper']
            bb_lower = df['bb_lower']
            bb_middle = (bb_upper + bb_lower) / 2
            
            # 0 나누기 방지
            bb_width = (bb_upper - bb_lower) / bb_middle.replace(0, np.nan)
            avg_width = bb_width.rolling(20).mean().iloc[-1]
            curr_width = bb_width.iloc[-1]
            
            adx = df['adx'].iloc[-1]
            
            if pd.isna(avg_width) or pd.isna(curr_width) or pd.isna(adx):
                return "Neutral", 0, 0

            # 3. 장세 판단 로직
            regime = "Neutral"
            if adx < 15 and curr_width < (avg_width * 0.8):
                regime = "Range" # 횡보장
            elif adx > 25 and curr_width > (avg_width * 1.2):
                regime = "Trend" # 추세장
            
            return regime, adx, curr_width

        except Exception as e:
            LOGGER.error(f"Regime Check Error: {e}")
            return "Neutral", 0, 0

    def tune(self, df, perf_stats):
        """
        주기적으로 호출되어 설정을 최적화
        """
        # 0. 튜너 꺼져있으면 기본값 복사본 리턴 (오염 방지)
        if not self.enabled:
            return copy.deepcopy(self.base_cfg)

        now = time.time()
        # 튜닝 주기 체크
        if now - self.last_tune_ts < self.tune_interval:
            return copy.deepcopy(self.current_cfg) # 현재 설정 복사본
        
        self.last_tune_ts = now
        
        # 1. 지표 수집
        regime, adx, bb_w = self.get_market_regime(df)
        tpd_24h = perf_stats.get('trades_last_24h', 0)
        cons_loss = perf_stats.get('consecutive_losses', 0)
        win_rate = perf_stats.get('win_rate_10', 0.5)

        # 2. 감옥 탈출 (Strict 모드 리셋)
        if self.mode == "Strict" and (now - self.last_mode_change_ts > 86400):
            if tpd_24h == 0:
                LOGGER.info("🔓 [Auto-Reset] 거래 부재로 Strict 해제")
                self._change_mode("Neutral", "Time-Reset", adx, bb_w, cons_loss, win_rate, tpd_24h)
                return copy.deepcopy(self.current_cfg)

        # 3. 목표 모드 결정
        target_mode = "Neutral"

        # (우선순위 1) 비상 제동
        if cons_loss >= 3 or win_rate < 0.2:
            target_mode = "Strict" # 상황 B
            
        # (우선순위 2) 시장 장세
        elif regime == "Range":
            target_mode = "Range_Ops" # 상황 C
        elif regime == "Trend":
            target_mode = "Trend_Follow" # 상황 A
        
        # 4. 모드 변경 적용 (쿨타임 1시간)
        if target_mode != self.mode and (now - self.last_mode_change_ts > 3600):
            self._change_mode(target_mode, regime, adx, bb_w, cons_loss, win_rate, tpd_24h)
            
        return copy.deepcopy(self.current_cfg)

    def _change_mode(self, mode, reason, adx, bb_w, loss, win_rate, tpd):
        """실제 설정을 변경하고 상세 로그 기록"""
        LOGGER.info(
            f"🎛️ [Tuner] Mode Change: {self.mode} -> {mode} | "
            f"Reason={reason}, ADX={adx:.1f}, BB_W={bb_w:.4f}, "
            f"Loss={loss}, Win10={win_rate:.2f}, T24h={tpd}"
        )

        self.mode = mode
        self.last_mode_change_ts = time.time()
        
        # Base에서 출발 (Deep Copy)
        new_cfg = copy.deepcopy(self.base_cfg)
        
        # Key 방어 (setdefault)
        weights = new_cfg.setdefault('weights', {})
        inds = new_cfg.setdefault('indicators', {})

        if mode == "Strict": # 상황 B
            weights['btc_ok'] = 10.0 # btc_filter renamed to btc_ok in settings
            weights['hammer'] = 4.0
            inds['bb'] = inds.get('bb', {})
            inds['bb']['length'] = 30
            LOGGER.info("   └ Action: BTC필터강화, 해머가중치↑, BB길이↑")

        elif mode == "Range_Ops": # 상황 C
            inds['rsi'] = inds.get('rsi', {})
            inds['rsi']['oversold'] = 45 # rsi_os -> rsi.oversold
            inds['bb'] = inds.get('bb', {})
            inds['bb']['std'] = 1.8
            inds['volume'] = inds.get('volume', {})
            inds['volume']['spike_factor'] = 1.2
            LOGGER.info("   └ Action: RSI완화(45), BB폭축소(1.8), 거래량완화")

        elif mode == "Trend_Follow": # 상황 A
            new_cfg['entry_threshold'] = 5.0
            weights['volume_spike'] = 2.0
            inds['rsi'] = inds.get('rsi', {})
            inds['rsi']['oversold'] = 40
            LOGGER.info("   └ Action: 진입점수하향(5.0), 거래량가중치↑")

        self.current_cfg = new_cfg
