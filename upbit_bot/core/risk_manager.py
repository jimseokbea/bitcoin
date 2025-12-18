import logging
import datetime
import math
import pytz
from core.system_utils import load_state, save_state, KST, LOGGER

# --- 7. Risk Managers (방어 코드 추가) ---
class DailyRiskManager:
    def __init__(self, max_loss_pct=0.03):
        self.max_loss_pct = max_loss_pct
        state = load_state().get('daily_risk', {})
        self.start_equity = state.get('start_equity', None)
        self.curr_date = state.get('curr_date', None)
        self.is_stopped = state.get('is_stopped', False)

    def update(self, current_equity):
        if current_equity <= 0: return False # 자산 0이면 거래 불가

        now_date = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        # 날짜 변경 리셋
        if self.curr_date != now_date:
            LOGGER.info(f"📅 날짜 변경 ({now_date}) - Daily Risk 리셋")
            self.start_equity = current_equity
            self.curr_date = now_date
            self.is_stopped = False
            self.save_status() # 리셋 상태 저장
            return True

        if self.is_stopped: return False

        # 초기화
        if not self.start_equity or self.start_equity <= 0:
            self.start_equity = current_equity
            self.save_status() # 초기 상태 저장
        
        # 손실 체크
        if self.start_equity > 0:
            loss = (self.start_equity - current_equity) / self.start_equity
            if loss >= self.max_loss_pct:
                LOGGER.critical(f"⛔ 일일 손실 한도 초과! (-{loss*100:.2f}%)")
                self.is_stopped = True
                self.save_status()
                return False
        
        return True

    def save_status(self):
        full = load_state()
        full['daily_risk'] = {
            'start_equity': self.start_equity,
            'curr_date': self.curr_date,
            'is_stopped': self.is_stopped
        }
        save_state(full)

class PositionSizer:
    def __init__(self, base_pct=0.012, min_size=6000, max_cap=0.05, cash_buf=0.3):
        self.base_pct = base_pct
        self.min_size = min_size
        self.max_cap = max_cap
        self.cash_buf = cash_buf
    
    def get_size(self, total_equity, current_cash, current_exposure):
        # User defined fixed amount logic override allowed in Main, 
        # but here provides dynamic sizing logic.
        
        if total_equity <= 0: return 0 

        # 1. 전체 노출 체크 (예: 70% 이상 코인 보유시 추가 매수 금지)
        # However, bot config might allow 100%. Adjust if needed.
        # Let's keep generous for now or use config.
        # if (current_exposure / total_equity) >= 0.9: return 0

        # 2. 현금 버퍼
        # if current_cash < (total_equity * self.cash_buf): return 0

        # 3. 금액 계산
        size = total_equity * self.base_pct
        size = min(size, total_equity * self.max_cap) # 상한선
        
        # 4. 최소금액 & 절삭
        size = math.floor(size / 100) * 100
        
        if size < self.min_size:
            if current_cash >= self.min_size:
                size = self.min_size
            else:
                return 0
        
        # Safe check against cash
        if size > current_cash:
            size = math.floor(current_cash / 100) * 100
            
        return int(size)
