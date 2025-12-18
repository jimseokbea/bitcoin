from dataclasses import dataclass
from datetime import date
from typing import Dict, Any, Optional, List

import time

from .upbit_client import UpbitClient
from .advanced_strategy import AdvancedStrategy, AdvancedStrategyConfig
from .sentiment_client import SentimentClient
from .telegram_notifier import TelegramNotifier
from .market_data_helpers import filter_intraday_candles_utc, get_previous_day_candle


@dataclass
class MarketState:
    market: str
    strategy: AdvancedStrategy
    position: Optional[Dict[str, Any]] = None
    daily_trades: int = 0
    consecutive_losses: int = 0


class UpbitTraderMulti:
    def __init__(self, client: UpbitClient, notifier: TelegramNotifier, settings: Dict[str, Any]):
        self.client = client
        self.notifier = notifier
        self.settings = settings

        self.bot_cfg = settings["bot"]
        self.risk_cfg = settings["risk"]

        markets: List[str] = self.bot_cfg["markets"]
        self.unit: int = self.bot_cfg["unit"]
        self.loop_interval: int = self.bot_cfg["loop_interval"]

        adv_cfg = AdvancedStrategyConfig.from_yaml(settings["strategy_advanced"])
        sentiment_cfg = adv_cfg.sentiment
        self.sentiment_client = SentimentClient(
            enabled=sentiment_cfg.enabled,
            cache_minutes=sentiment_cfg.cache_minutes,
        )

        self.market_states: Dict[str, MarketState] = {}
        for m in markets:
            strat = AdvancedStrategy(adv_cfg, self.sentiment_client)
            self.market_states[m] = MarketState(market=m, strategy=strat)

        self.current_date: date = date.today()
        self.daily_start_equity: Optional[float] = None
        self.daily_loss: float = 0.0
        self.daily_trades_total: int = 0
        self.loop_count: int = 0

    # ---------- balance helpers ----------
    def _get_currency_from_market(self, market: str) -> str:
        return market.split("-")[1]

    def _get_coin_balance(self, currency: str) -> float:
        accounts = self.client.get_accounts()
        for acc in accounts:
            if acc["currency"] == currency:
                return float(acc["balance"])
        return 0.0

    def _calc_total_equity(self, last_prices: Dict[str, float]) -> float:
        accounts = self.client.get_accounts()
        krw = 0.0
        coin_value = 0.0
        for acc in accounts:
            cur = acc["currency"]
            bal = float(acc["balance"])
            if cur == "KRW":
                krw += bal
            else:
                mkt = f"KRW-{cur}"
                price = last_prices.get(mkt)
                if price is not None:
                    coin_value += bal * price
        return krw + coin_value

    # ---------- daily reset & risk ----------
    def _reset_daily_state_if_needed(self, equity_now: float):
        today = date.today()
        if today != self.current_date:
            self.current_date = today
            self.daily_start_equity = equity_now
            self.daily_loss = 0.0
            self.daily_trades_total = 0
            for ms in self.market_states.values():
                ms.daily_trades = 0
                ms.consecutive_losses = 0

    def _check_global_risk(self, equity_now: float) -> bool:
        if self.daily_start_equity is None:
            self.daily_start_equity = equity_now
        self.daily_loss = (equity_now - self.daily_start_equity) / self.daily_start_equity

        if self.daily_loss <= -self.risk_cfg["max_daily_loss"]:
            self.notifier.send("⛔️ Global max daily loss reached. Stop trading.")
            return False

        if self.daily_trades_total >= self.risk_cfg["max_daily_trades_total"]:
            self.notifier.send("⚠️ Max daily trades reached. No more entries today.")
            return False

        return True

    # ---------- main loop ----------
    def run(self):
        self.notifier.send("🚀 UpbitTraderMulti started.")
        while True:
            try:
                last_prices: Dict[str, float] = {}
                candles_by_market: Dict[str, List[Dict[str, Any]]] = {}
                daily_prev_by_market: Dict[str, Optional[Dict[str, Any]]] = {}
                intraday_by_market: Dict[str, List[Dict[str, Any]]] = {}

                hourly_by_market: Dict[str, List[Dict[str, Any]]] = {}

                # 1) 각 마켓 분봉 + 전일 일봉 준비
                for market in self.market_states.keys():
                    # 분봉
                    candles = self.client.get_candles(market, self.unit, 200)
                    candles = list(reversed(candles))  # 과거→현재
                    candles_by_market[market] = candles
                    last_prices[market] = float(candles[-1]["trade_price"])

                    # 1시간봉 (Trend 확인용)
                    hourly = self.client.get_candles(market, 60, 50)
                    hourly = list(reversed(hourly))
                    hourly_by_market[market] = hourly

                    # 당일 분봉 (VWAP 용)
                    intraday = filter_intraday_candles_utc(candles)
                    intraday_by_market[market] = intraday

                    # 전일 일봉 (변동성 돌파용)
                    daily_prev = get_previous_day_candle(self.client, market)
                    daily_prev_by_market[market] = daily_prev

                    time.sleep(0.1)  # API 호출 속도 완화

                # 2) 전체 equity + 리스크 체크
                total_equity = self._calc_total_equity(last_prices)
                self._reset_daily_state_if_needed(total_equity)
                if not self._check_global_risk(total_equity):
                    break

                # 3) 마켓별 시그널 & 주문
                for market, ms in self.market_states.items():
                    if ms.daily_trades >= self.risk_cfg["max_daily_trades_per_market"]:
                        continue
                    if ms.consecutive_losses >= self.risk_cfg["max_consecutive_losses"]:
                        continue

                    candles = candles_by_market[market]
                    if len(candles) < 3:
                        continue

                    last_candle = candles[-1]
                    prev_candle = candles[-2]
                    closes = [float(c["trade_price"]) for c in candles]

                    daily_prev = daily_prev_by_market[market]
                    intraday_candles = intraday_by_market[market]
                    hourly_candles = hourly_by_market[market]

                    ms.strategy.position = ms.position
                    signal, reason = ms.strategy.generate_signal(
                        closes=closes,
                        current_candle=last_candle,
                        prev_candle=prev_candle,
                        daily_prev=daily_prev,
                        intraday_candles=intraday_candles,
                        hourly_candles=hourly_candles,
                    )

                    # [DEBUG] Print status for user reassurance (every 10 loops)
                    if self.loop_count % 12 == 0: # Approx 1 min
                        print(f"[{market}] {signal}: {reason}")

                    if signal == "BUY" and ms.position is None:
                        self._handle_buy(market, ms, last_prices[market])
                    elif signal == "SELL" and ms.position is not None:
                        self._handle_sell(market, ms, last_prices[market], reason=reason)
                    elif signal == "SELL_PARTIAL" and ms.position is not None:
                        self._handle_sell(market, ms, last_prices[market], fraction=0.5, reason=reason)

                self.loop_count += 1
                time.sleep(self.loop_interval)

            except Exception as e:
                msg = f"[ERROR] Multi loop error: {e}"
                print(msg)
                self.notifier.send(msg)
                time.sleep(self.loop_interval)

    # ---------- BUY ----------
    def _handle_buy(self, market: str, ms: MarketState, last_price: float):
        krw_balance = self.client.get_krw_balance()
        if krw_balance < self.risk_cfg["min_krw"]:
            return

        # [Optimized] Max Exposure Check
        total_equity = self._calc_total_equity({market: last_price}) # Approx
        current_exposure = 0.0
        for m, state in self.market_states.items():
            if state.position:
                current_exposure += state.position.get("krw_used", 0.0)
        
        max_exposure_amount = total_equity * self.risk_cfg.get("max_exposure", 0.3)
        
        capital_ratio = self.risk_cfg["capital_per_trade"]
        use_krw = max(self.risk_cfg["min_krw"], krw_balance * capital_ratio)
        
        if current_exposure + use_krw > max_exposure_amount:
            print(f"[{market}] Skip BUY: Max Exposure Reached ({current_exposure:.0f} + {use_krw:.0f} > {max_exposure_amount:.0f})")
            return

        if use_krw > krw_balance:
            use_krw = krw_balance

        order = self.client.create_order(
            market=market,
            side="bid",
            ord_type="price",
            volume=None,
            price=str(int(use_krw)),
        )

        entry_price = last_price  # 단순 버전 (필요시 체결가 조회로 개선)
        ms.position = {
            "side": "LONG",
            "entry_price": entry_price,
            "krw_used": use_krw,
        }
        ms.strategy.position = ms.position
        ms.strategy.highest_price_after_entry = entry_price

        ms.daily_trades += 1
        self.daily_trades_total += 1

        self.notifier.send(
            f"[BUY] {market} @ {entry_price:.0f}KRW, used {int(use_krw)}KRW "
            f"(capital_ratio={capital_ratio:.2f})"
        )

    # ---------- SELL ----------
    def _handle_sell(self, market: str, ms: MarketState, last_price: float, fraction: float = 1.0, reason: str = ""):
        cur = self._get_currency_from_market(market)
        volume = self._get_coin_balance(cur)
        
        if volume <= 0:
            ms.position = None
            ms.strategy.position = None
            self.notifier.send(f"[WARN] {market}: no balance on SELL. Position reset.")
            return

        volume_to_sell = volume * fraction
        
        # If volume is too small, sell all
        if volume_to_sell * last_price < 5000:
            volume_to_sell = volume
            fraction = 1.0

        order = self.client.create_order(
            market=market,
            side="ask",
            ord_type="market",
            volume=str(volume_to_sell),
            price=None,
        )

        entry_price = ms.position["entry_price"]
        pnl = (last_price - entry_price) / entry_price
        
        if fraction == 1.0:
            if pnl > 0:
                ms.consecutive_losses = 0
            else:
                ms.consecutive_losses += 1
            
            ms.position = None
            ms.strategy.position = None
            
            self.notifier.send(
                f"[SELL] {market} @ {last_price:.0f}KRW, PnL: {pnl*100:.2f}% ({reason}) "
                f"(consecutive_losses={ms.consecutive_losses})"
            )
        else:
            # Partial Sell
            self.notifier.send(
                f"[SELL PARTIAL] {market} @ {last_price:.0f}KRW, PnL: {pnl*100:.2f}% ({reason}) "
                f"Sold {fraction*100:.0f}%"
            )
            # Update krw_used to reflect partial sell (approx)
            if ms.position:
                ms.position["krw_used"] *= (1 - fraction)
