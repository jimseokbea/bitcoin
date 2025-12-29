"""
Candidate Scorer for Multi-Coin Regime Trading

Safeguards:
- (D) Hard Filter before scoring (spread, funding, ATR extreme)
- (F) BTC correlation cluster penalty
- (H) Comprehensive scoring logs
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
from .utils import get_logger

logger = get_logger()


class CandidateScorer:
    """
    Scores and ranks coins for trading eligibility.
    Flow: Hard Filter → Scoring → Overlap Penalty → Top N Selection
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # Universe config
        universe_cfg = config.get('universe', {})
        self.top_quote_volume_rank = universe_cfg.get('top_quote_volume_rank', 50)
        self.exclude_if_atr_pct_gt = universe_cfg.get('exclude_if_atr_pct_gt', 4.5)
        self.spread_filter_enabled = universe_cfg.get('spread_filter', True)
        self.funding_filter_enabled = universe_cfg.get('funding_filter', True)
        self.max_funding_rate = universe_cfg.get('max_funding_rate', 0.0003)  # 0.03%/8h
        self.max_spread_pct = universe_cfg.get('max_spread_pct', 0.1)  # 0.1%
        
        # Candidate selection config
        cand_cfg = config.get('candidate_selection', {})
        self.top_n_candidates = cand_cfg.get('top_n_candidates', 5)
        
        # Scoring weights
        scoring_cfg = cand_cfg.get('scoring', {})
        self.trend_weight = scoring_cfg.get('trend_weight', 0.35)
        self.direction_weight = scoring_cfg.get('direction_weight', 0.25)
        self.vol_suitability_weight = scoring_cfg.get('vol_suitability_weight', 0.20)
        self.liquidity_weight = scoring_cfg.get('liquidity_weight', 0.20)
        
        # Overlap penalty config (Safeguard F)
        penalty_cfg = cand_cfg.get('overlap_penalty', {})
        self.same_direction_penalty = penalty_cfg.get('same_direction_if_already_2_positions', 0.5)
        self.btc_corr_penalty = penalty_cfg.get('btc_corr_cluster_penalty', 0.7)
        
        # BTC correlated coins (Safeguard F)
        self.btc_corr_cluster = [
            'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 
            'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT',
            'MATIC/USDT', 'NEAR/USDT', 'APT/USDT', 'ARB/USDT'
        ]
        
        logger.info(f"📊 CandidateScorer initialized (Top {self.top_n_candidates}, "
                   f"ATR%_max: {self.exclude_if_atr_pct_gt})")
    
    def hard_filter(self, symbol: str, ticker_data: dict, 
                   funding_rate: Optional[float] = None,
                   atr_pct: Optional[float] = None) -> Tuple[bool, str]:
        """
        Hard filter before scoring (Safeguard D).
        Returns: (is_eligible, rejection_reason)
        """
        # 1. ATR% extreme filter
        if atr_pct is not None and atr_pct > self.exclude_if_atr_pct_gt:
            return False, f"ATR%_extreme ({atr_pct:.2f}% > {self.exclude_if_atr_pct_gt}%)"
        
        # 2. Spread filter
        if self.spread_filter_enabled and ticker_data:
            bid = ticker_data.get('bid', 0)
            ask = ticker_data.get('ask', 0)
            if bid > 0 and ask > 0:
                spread_pct = ((ask - bid) / bid) * 100
                if spread_pct > self.max_spread_pct:
                    return False, f"spread_too_wide ({spread_pct:.3f}% > {self.max_spread_pct}%)"
        
        # 3. Funding rate filter
        if self.funding_filter_enabled and funding_rate is not None:
            if abs(funding_rate) > self.max_funding_rate:
                return False, f"funding_extreme ({funding_rate:.4f})"
        
        # 4. Low volume filter (already covered by universe rank, but extra check)
        if ticker_data:
            quote_volume = ticker_data.get('quoteVolume', 0)
            if quote_volume and quote_volume < 1_000_000:  # $1M min
                return False, f"low_volume ({quote_volume/1e6:.2f}M)"
        
        return True, "ok"
    
    def calculate_score(self, symbol: str, 
                       adx: float, 
                       ema_diff_pct: float,  # (ema50-ema200)/ema200 * 100
                       atr_pct: float,
                       volume_rank: int,
                       total_coins: int) -> Dict:
        """
        Calculate candidate score.
        Returns: {score, components}
        """
        # 1. Trend Score: min(ADX, 40) normalized to 0-100
        trend_score = min(adx, 40) / 40 * 100
        
        # 2. Direction Score: EMA diff magnitude (0-100)
        # Strong direction = high score
        direction_score = min(abs(ema_diff_pct) * 10, 100)
        
        # 3. Volatility Suitability Score
        # Optimal ATR% range: 1.2% - 2.5%
        # Too low = no opportunity, too high = dangerous
        if 1.2 <= atr_pct <= 2.5:
            vol_score = 100
        elif atr_pct < 1.2:
            vol_score = max(0, atr_pct / 1.2 * 100)
        else:  # > 2.5
            vol_score = max(0, 100 - (atr_pct - 2.5) * 40)
        
        # 4. Liquidity Score: Based on volume rank
        # Rank 1 = 100, Rank 50 = 50, Rank 100 = 0
        liquidity_score = max(0, 100 - volume_rank * 2)
        
        # Weighted total
        total_score = (
            trend_score * self.trend_weight +
            direction_score * self.direction_weight +
            vol_score * self.vol_suitability_weight +
            liquidity_score * self.liquidity_weight
        )
        
        return {
            'symbol': symbol,
            'score': total_score,
            'components': {
                'trend': trend_score,
                'direction': direction_score,
                'volatility': vol_score,
                'liquidity': liquidity_score
            },
            'raw_values': {
                'adx': adx,
                'ema_diff_pct': ema_diff_pct,
                'atr_pct': atr_pct,
                'volume_rank': volume_rank
            }
        }
    
    def apply_overlap_penalty(self, scores: List[Dict], 
                             current_positions: List[Dict],
                             trade_direction: str) -> List[Dict]:
        """
        Apply overlap penalty based on existing positions (Safeguard F).
        
        Args:
            scores: List of score dicts
            current_positions: List of {symbol, side} dicts
            trade_direction: 'long' or 'short'
        
        Returns: Modified scores with penalties applied
        """
        # Count current positions by direction
        long_count = sum(1 for p in current_positions if p.get('side') == 'long')
        short_count = sum(1 for p in current_positions if p.get('side') == 'short')
        
        # Check if BTC-correlated position exists
        btc_cluster_long = any(
            p.get('symbol') in self.btc_corr_cluster and p.get('side') == 'long'
            for p in current_positions
        )
        btc_cluster_short = any(
            p.get('symbol') in self.btc_corr_cluster and p.get('side') == 'short'
            for p in current_positions
        )
        
        for s in scores:
            penalty = 1.0
            penalty_reasons = []
            
            # Same direction penalty
            if trade_direction == 'long' and long_count >= 2:
                penalty *= self.same_direction_penalty
                penalty_reasons.append(f"long_overload({long_count})")
            elif trade_direction == 'short' and short_count >= 2:
                penalty *= self.same_direction_penalty
                penalty_reasons.append(f"short_overload({short_count})")
            
            # BTC cluster correlation penalty (Safeguard F)
            if s['symbol'] in self.btc_corr_cluster:
                if trade_direction == 'long' and btc_cluster_long:
                    penalty *= self.btc_corr_penalty
                    penalty_reasons.append("btc_cluster_long")
                elif trade_direction == 'short' and btc_cluster_short:
                    penalty *= self.btc_corr_penalty
                    penalty_reasons.append("btc_cluster_short")
            
            s['penalty'] = penalty
            s['penalty_reasons'] = penalty_reasons
            s['final_score'] = s['score'] * penalty
        
        return scores
    
    def select_top_candidates(self, scores: List[Dict], 
                             n: Optional[int] = None) -> List[Dict]:
        """Select top N candidates by final score."""
        n = n or self.top_n_candidates
        
        # Sort by final_score (or score if no penalty applied)
        sorted_scores = sorted(
            scores, 
            key=lambda x: x.get('final_score', x['score']), 
            reverse=True
        )
        
        top_n = sorted_scores[:n]
        
        # Log selection (Safeguard H)
        if top_n:
            top_str = ", ".join([
                f"{s['symbol']}({s.get('final_score', s['score']):.0f})" 
                for s in top_n[:5]
            ])
            logger.info(f"📋 [Candidates] Top {n}: {top_str}")
        
        return top_n
    
    def score_and_select(self, 
                        candidates: List[Dict],  # {symbol, adx, ema_diff_pct, atr_pct, volume_rank, ticker}
                        current_positions: List[Dict],
                        trade_direction: str) -> List[Dict]:
        """
        Full pipeline: Hard Filter → Score → Penalty → Select
        """
        total_coins = len(candidates)
        scored = []
        filtered_out = []
        
        for c in candidates:
            # Hard filter first (Safeguard D)
            eligible, reason = self.hard_filter(
                c['symbol'],
                c.get('ticker', {}),
                c.get('funding_rate'),
                c.get('atr_pct')
            )
            
            if not eligible:
                filtered_out.append((c['symbol'], reason))
                continue
            
            # Calculate score
            score_data = self.calculate_score(
                symbol=c['symbol'],
                adx=c.get('adx', 0),
                ema_diff_pct=c.get('ema_diff_pct', 0),
                atr_pct=c.get('atr_pct', 0),
                volume_rank=c.get('volume_rank', 50),
                total_coins=total_coins
            )
            scored.append(score_data)
        
        if filtered_out:
            logger.debug(f"[Filter] Excluded: {[(s, r) for s, r in filtered_out[:5]]}")
        
        # Apply overlap penalty
        scored = self.apply_overlap_penalty(scored, current_positions, trade_direction)
        
        # Select top candidates
        return self.select_top_candidates(scored)
