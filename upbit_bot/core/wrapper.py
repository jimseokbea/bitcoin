import time
import pyupbit
import logging
from core.system_utils import LIMITER, LOGGER

class UpbitAPIWrapper:
    def __init__(self, access_key, secret_key):
        self.upbit = pyupbit.Upbit(access_key, secret_key)
        self.min_order_krw = 5000

    def get_ohlcv(self, ticker, interval, count=200):
        LIMITER.wait()
        try:
            return pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        except Exception as e:
            LOGGER.error(f"Data Fetch Error ({ticker}): {e}")
            return None

    def get_balance(self, ticker):
        """Ticker balance safely"""
        LIMITER.wait()
        try:
            return self.upbit.get_balance(ticker)
        except Exception as e:
            LOGGER.error(f"Get Balance Error ({ticker}): {e}")
            return 0.0

    def get_balances(self):
        """Get All Balances with details"""
        LIMITER.wait()
        try:
            return self.upbit.get_balances()
        except Exception as e:
             LOGGER.error(f"Get All Balances Error: {e}")
             return []

    def get_current_price(self, ticker):
        LIMITER.wait()
        try:
            return pyupbit.get_current_price(ticker)
        except Exception as e:
             LOGGER.error(f"Get Current Price Error ({ticker}): {e}")
             return None

    def compute_total_equity(self):
        """계좌 총 평가금액 계산 (단일화된 로직)"""
        try:
            # KRW Balance
            krw_bal = self.get_balance("KRW")
            
            # Balances
            balances = self.get_balances()
            
            total_coin_val = 0
            for b in balances:
                if b['currency'] == 'KRW': continue
                
                ticker = f"KRW-{b['currency']}"
                qty = float(b['balance']) + float(b['locked'])
                avg = float(b['avg_buy_price'])
                if qty * avg < 1000: continue # Dust skip
                
                # Use current price if possible, else use avg (fallback)
                price = self.get_current_price(ticker)
                if price:
                    total_coin_val += qty * price
                else:
                    total_coin_val += qty * avg
            
            return krw_bal + total_coin_val
        except Exception as e:
            LOGGER.error(f"Equity 계산 에러: {e}")
            return 0 

    def wait_fill(self, uuid, timeout=20, interval=0.5):
        """주문 체결 대기 (Polling)"""
        start = time.time()
        while time.time() - start < timeout:
            LIMITER.wait()
            try:
                order = self.upbit.get_order(uuid)
                if order is None: # Sometimes returns None immediately?
                     time.sleep(interval)
                     continue

                state = order.get('state')
                if state == 'done':
                    return order # 체결 완료
                elif state in ['cancel', 'fail']:
                    LOGGER.warning(f"주문 취소/실패: {uuid}")
                    return None
            except Exception as e:
                LOGGER.error(f"get_order 에러: {e}")
            time.sleep(interval)
        
        LOGGER.warning(f"⏰ 체결 대기 타임아웃 (UUID: {uuid})")
        return None 

    def place_order_safe(self, ticker, side, krw_amount_or_volume):
        """
        안전한 주문 실행 (Universal Method)
        """
        if side == "buy":
            return self.buy_market(ticker, krw_amount_or_volume)
        elif side == "sell":
            return self.sell_market_safe(ticker, krw_amount_or_volume)
        return None

    def buy_market(self, ticker, krw_amount):
        if krw_amount < self.min_order_krw:
            LOGGER.warning(f"주문 금액 미달: {krw_amount}")
            return None
        
        LIMITER.wait()
        try:
            resp = self.upbit.buy_market_order(ticker, krw_amount)
            if resp and 'uuid' in resp:
                LOGGER.info(f"✅ 매수 접수: {ticker} {krw_amount}원")
                filled = self.wait_fill(resp['uuid'])
                if filled:
                    LOGGER.info(f"🎉 체결 완료: {ticker}")
                    return filled
                else:
                    LOGGER.warning(f"⚠️ 체결 확인 실패 (미체결 가능성): {resp['uuid']}")
                    return resp
            else:
                LOGGER.error(f"❌ 매수 실패: {resp}")
                return None
        except Exception as e:
            LOGGER.error(f"매수 예외: {e}")
            return None

    def sell_market_safe(self, ticker, qty):
        LIMITER.wait()
        try:
            resp = self.upbit.sell_market_order(ticker, qty)
            if resp and 'uuid' in resp:
                 LOGGER.info(f"📉 매도 접수: {ticker}")
                 filled = self.wait_fill(resp['uuid'])
                 if filled:
                     return filled
                 else:
                     return resp # Return raw response if timeout
            return None
        except Exception as e:
            LOGGER.error(f"매도 예외: {e}")
            return None
