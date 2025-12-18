from .utils import get_logger

logger = get_logger()

class MarketScanner:
    def __init__(self, executor, config):
        self.ex = executor
        self.config = config
        if 'universe' in self.config and 'blacklist' in self.config['universe']:
            self.ignore_list = self.config['universe']['blacklist']
        else:
             self.ignore_list = self.config.get('scanner', {}).get('blacklist', [])
        
        self.top_k = self.config['scanner'].get('top_k', 3)
        
        # [Secret Weapon 2] Sector Guard
        self.SECTORS = {
            'DOGE/USDT': 'Meme', 'SHIB/USDT': 'Meme', 'PEPE/USDT': 'Meme', 'FLOKI/USDT': 'Meme',
            'SOL/USDT': 'L1', 'ETH/USDT': 'L1', 'AVAX/USDT': 'L1', 'ADA/USDT': 'L1',
            'RENDER/USDT': 'AI', 'FET/USDT': 'AI', 'WLD/USDT': 'AI', 'AGIX/USDT': 'AI',
            'OP/USDT': 'L2', 'ARB/USDT': 'L2', 'MATIC/USDT': 'L2'
        }

    def check_sector_conflict(self, new_symbol):
        """
        Checks if we already hold a coin in the same sector.
        """
        try:
            # Get all open positions
            positions = self.ex.exchange.fetch_positions()
            active_symbols = [p['symbol'] for p in positions if float(p['contracts']) > 0]
            
            new_sector = self.SECTORS.get(new_symbol, 'Others')
            if new_sector == 'Others': return False
            
            for s in active_symbols:
                existing_sector = self.SECTORS.get(s, 'Others')
                if new_sector == existing_sector:
                    logger.info(f"🛡️ Sector Guard: Rejecting {new_symbol} (Conflict with {s} in {new_sector})")
                    return True
            return False
        except Exception as e:
            logger.error(f"Sector Check Error: {e}")
            return False

    def get_sector(self, symbol):
        return self.SECTORS.get(symbol, 'Others')

    def find_best_targets(self):
        """
        Scans and returns Top-K targets respecting Sector Quotas.
        """
        try:
            # 1. Fetch Tickers
            tickers = self.ex.exchange.fetch_tickers()
            
            # 2. Filter & Sort by Liquidity
            candidates = []
            for symbol, data in tickers.items():
                if '/USDT' in symbol and symbol not in self.ignore_list:
                    candidates.append(data)

            # Sort by Volatility (Top 50 Liquid)
            # Pre-sort by Volume to filter junk
            candidates.sort(key=lambda x: float(x['quoteVolume']) if x['quoteVolume'] else 0, reverse=True)
            top_liquid = candidates[:50]
            
            # Sort by Volatility %
            top_volatile = sorted(
                top_liquid,
                key=lambda x: abs(float(x['percentage'])) if x['percentage'] else 0,
                reverse=True
            )
            
            # 3. Selection with Sector Quota
            final_picks = []
            sector_counts = {} 
            
            # Count current positions sectors
            try:
                positions = self.ex.exchange.fetch_positions()
                active = [p for p in positions if float(p['contracts']) > 0]
                for p in active:
                    sec = self.get_sector(p['symbol'])
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
            except: pass

            for cand in top_volatile:
                sym = cand['symbol']
                sec = self.get_sector(sym)
                
                # [Log Check] Verification for User
                # logger.info(f"🔎 Scanning Candidate: {sym} (Sector: {sec})")

                # Quota: Meme/AI max 1, Others max 2
                limit = 1 if sec in ['Meme', 'AI'] else 2
                
                if sector_counts.get(sec, 0) >= limit:
                    # Log rejection for verification
                    logger.info(f"[Sector Guard] Rejecting {sym} (Sector {sec} Full: {sector_counts.get(sec,0)}/{limit})")
                    continue 
                    
                final_picks.append(sym)
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                
                if len(final_picks) >= self.top_k:
                    break
            
            if final_picks:
                logger.info(f"[Scanner Selected] {final_picks}")
            return final_picks

        except Exception as e:
            logger.error(f"Scanner Error: {e}")
            return []
