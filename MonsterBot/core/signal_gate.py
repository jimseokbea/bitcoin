import time
import datetime
import pytz
import json
import os

class SignalGate:
    def __init__(self, cfg, logger, state_store=None):
        self.cfg = cfg
        self.logger = logger
        # Default to Seoul if not specified, handle timezone string safety
        tz_name = cfg.get("system", {}).get("timezone", "Asia/Seoul")
        try:
            self.kst = pytz.timezone(tz_name)
        except:
            self.kst = pytz.timezone("Asia/Seoul")

        gate = cfg.get("gate", {})
        self.enabled = gate.get("enabled", True)
        self.max_trades_per_day = int(gate.get("max_trades_per_day", 2))
        self.min_minutes_between = int(gate.get("min_minutes_between_entries", 360))
        self.require_new_setup = bool(gate.get("require_new_setup", True))

        self.session_filter = gate.get("session_filter", {})
        self.allow_hours = set(self.session_filter.get("allow_hours_kst", list(range(24))))

        # Simple file-based state store if none provided
        self.state_file = "gate_state.json"
        
        self._load()

    def _today_key(self):
        return datetime.datetime.now(self.kst).strftime("%Y-%m-%d")

    def _load(self):
        self.day = self._today_key()
        self.trades_today = 0
        self.last_entry_ts = 0
        self.last_symbol = None
        self.last_setup_fingerprint = None

        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    s = json.load(f)
                    if s.get("day") == self.day:
                        self.trades_today = int(s.get("trades_today", 0))
                        self.last_entry_ts = float(s.get("last_entry_ts", 0))
                        self.last_symbol = s.get("last_symbol")
                        self.last_setup_fingerprint = s.get("last_setup_fingerprint")
        except Exception as e:
            self.logger.error(f"Gate Load Error: {e}")

    def _save(self):
        try:
            data = {
                "day": self.day,
                "trades_today": self.trades_today,
                "last_entry_ts": self.last_entry_ts,
                "last_symbol": self.last_symbol,
                "last_setup_fingerprint": self.last_setup_fingerprint,
            }
            with open(self.state_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.error(f"Gate Save Error: {e}")

    def _rollover_if_new_day(self):
        today = self._today_key()
        if today != self.day:
            self.logger.info(f"📅 [Gate] New day rollover: {self.day} -> {today}")
            self.day = today
            self.trades_today = 0
            self.last_entry_ts = 0
            self.last_symbol = None
            self.last_setup_fingerprint = None
            # Do NOT save here immediately, wait for action or explicit save, 
            # but to be safe we save reset state
            self._save()

    def allow_entry(self, symbol: str, setup_fingerprint: str = None):
        """
        setup_fingerprint: Unique ID for the setup (e.g. timestamp of signal)
        """
        if not self.enabled:
            return True, "gate_disabled"

        self._rollover_if_new_day()

        now = datetime.datetime.now(self.kst)
        if now.hour not in self.allow_hours:
            return False, f"session_blocked_hour={now.hour}"

        # 1) Daily Limit
        if self.trades_today >= self.max_trades_per_day:
            return False, f"max_trades_per_day_hit={self.max_trades_per_day}"

        # 2) Cooldown
        min_sec = self.min_minutes_between * 60
        if self.last_entry_ts and (time.time() - self.last_entry_ts) < min_sec:
            remain = int((min_sec - (time.time() - self.last_entry_ts)) / 60)
            return False, f"cooldown_active_remain_min={remain}"

        # 3) Symbol Re-entry
        if self.require_new_setup and symbol == self.last_symbol:
            # If no fingerprint provided, block by default if same symbol
            if setup_fingerprint and (setup_fingerprint != self.last_setup_fingerprint):
                return True, "same_symbol_new_setup_ok"
            return False, "same_symbol_blocked_need_new_setup"

        return True, "ok"

    def mark_entry(self, symbol: str, setup_fingerprint: str = None):
        self._rollover_if_new_day()
        self.trades_today += 1
        self.last_entry_ts = time.time()
        self.last_symbol = symbol
        self.last_setup_fingerprint = setup_fingerprint
        self._save()
        self.logger.info(
            f"🎯 [Gate] ENTRY ACCEPTED | trades_today={self.trades_today}/{self.max_trades_per_day} "
            f"| symbol={symbol} | setup={setup_fingerprint}"
        )
