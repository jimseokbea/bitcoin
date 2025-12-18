import time
from .system_utils import LOGGER

class MarketScanner:
    def __init__(self, executor):
        self.ex = executor # Reuse existing executor
        self.ignore_list = [
            'USDC/USDT', 'BUSD/USDT', 'TUSD/USDT', 'DAI/USDT', # Stables
            'BTCDOM/USDT', 'DEFI/USDT', # Indexes
            'LUNA/USDT' # PTSD
        ]

    def get_top_volatile_coins(self, limit=10):
        """
        Scans all tickers to find High Volume + High Volatility targets.
        """
        try:
            # 1. Fetch Tickers (Lightweight)
            # self.ex.limiter.consume(5) # Consumes weight
            tickers = self.ex.exchange.fetch_tickers()
            
            # 2. Filter & Sort by Liquidity (QuoteVolume)
            # Only USDT pairs
            candidates = []
            for symbol, data in tickers.items():
                if '/USDT' in symbol and symbol not in self.ignore_list:
                    candidates.append(data)

            # Sort by Money Flow (QuoteVolume) - High to Low
            # Take Top 30 to ensure we don't trade illiquid shitcoins
            top_liquid = sorted(
                candidates,
                key=lambda x: float(x['quoteVolume']) if x['quoteVolume'] else 0,
                reverse=True
            )[:35]
            
            # 3. Sort Top 30 by Volatility (Percentage Change)
            # We want movement (Up or Down)
            top_volatile = sorted(
                top_liquid,
                key=lambda x: abs(float(x['percentage'])) if x['percentage'] else 0,
                reverse=True
            )
            
            # Return Symbols
            results = [c['symbol'] for c in top_volatile[:limit]]
            # LOGGER.info(f"🔭 Scanner Found: {results}")
            return results

        except Exception as e:
            LOGGER.error(f"스캐너 에러: {e}")
            return []
