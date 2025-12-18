import logging

def get_logger():
    return logging.getLogger()

def resolve_symbol(exchange, target_symbol):
    """
    Resolves user input symbol (e.g. PEPE/USDT) to actual exchange symbol (e.g. 1000PEPE/USDT:USDT).
    """
    try:
        markets = exchange.markets
        if not markets:
            exchange.load_markets()
            markets = exchange.markets
            
        # 1. Exact match
        if target_symbol in markets:
            return target_symbol
            
        # 2. '1000' prefix (PEPE -> 1000PEPE)
        target_coin = target_symbol.split('/')[0]
        thousand_symbol = f"1000{target_coin}/USDT"
        if thousand_symbol in markets:
            return thousand_symbol
            
        # 3. Futures naming standard (PEPE/USDT:USDT)
        swap_symbol = f"{target_symbol}:USDT"
        if swap_symbol in markets:
            return swap_symbol
            
        # 4. Standard match (if passed symbol was missing suffix)
        # Often ccxt uses 'BTC/USDT' for linear Swap.
        
        return None
    except Exception:
        return None
