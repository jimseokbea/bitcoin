"""
Dry-Run Validation Reporter for Multi-Coin Regime System

Tracks and reports the 7 key metrics for dry-run validation:
1. 시간당 신규 진입 횟수 분포
2. Regime별 체류시간 평균/분산
3. RANGE_HIGHVOL 거래 0건 여부
4. BTC Panic 시 신규 롱 0건 + 기존 롱 관리 실행 여부
5. TopN 후보의 평균 점수/페널티 구성
6. 포지션 동시 보유 수 분포
7. 거절 사유 코드 랭킹
"""
import json
import os
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional
from .utils import get_logger

logger = get_logger()


class DryRunReporter:
    """
    Collects and validates dry-run metrics.
    Generates reports to verify safeguard compliance.
    """
    
    def __init__(self, report_file: str = "dryrun_report.json"):
        self.report_file = report_file
        
        # Metric 1: Entries per hour
        self.entries_per_bar = defaultdict(int)  # bar_ts -> count
        
        # Metric 2: Regime duration tracking
        self.regime_history = defaultdict(list)  # symbol -> [(regime, start_bar, end_bar)]
        self._current_regime = {}  # symbol -> (regime, start_bar)
        
        # Metric 3: RANGE_HIGHVOL trade attempts
        self.range_highvol_trades = 0
        self.range_highvol_blocked = 0
        
        # Metric 4: Panic gate tracking
        self.panic_events = []  # [{timestamp, new_long_blocked, existing_long_managed}]
        
        # Metric 5: Candidate scoring
        self.candidate_scores = []  # [{symbol, score, components, penalties}]
        
        # Metric 6: Position count distribution
        self.position_counts = defaultdict(int)  # count -> frequency
        
        # Metric 7: Rejection reasons
        self.rejection_reasons = defaultdict(int)  # reason_code -> count
        
        # Validation flags
        self.violations = []  # List of rule violations
        
        self._bar_count = 0
        
        logger.info("📊 DryRunReporter initialized")
    
    # ============================================================
    # Event Recording Methods
    # ============================================================
    
    def record_new_bar(self, bar_timestamp):
        """Record start of new bar."""
        self._bar_count += 1
        self.entries_per_bar[str(bar_timestamp)] = 0
    
    def record_entry(self, bar_timestamp: str, symbol: str, direction: str, regime: str):
        """Record an entry attempt."""
        self.entries_per_bar[bar_timestamp] += 1
        
        # Check for multiple entries per bar (violation)
        if self.entries_per_bar[bar_timestamp] > 1:
            self.violations.append({
                'type': 'MULTI_ENTRY_PER_BAR',
                'bar': bar_timestamp,
                'count': self.entries_per_bar[bar_timestamp]
            })
            logger.error(f"❌ [VIOLATION] Multiple entries in bar {bar_timestamp}")
    
    def record_entry_blocked(self, reason: str, symbol: str = None, regime: str = None):
        """Record a blocked entry with reason code."""
        self.rejection_reasons[reason] += 1
        
        # Special tracking for RANGE_HIGHVOL
        if regime == "RANGE_HIGHVOL":
            self.range_highvol_blocked += 1
    
    def record_regime_change(self, symbol: str, new_regime: str, bar_count: int):
        """Record regime transition."""
        if symbol in self._current_regime:
            old_regime, start_bar = self._current_regime[symbol]
            duration = bar_count - start_bar
            self.regime_history[symbol].append({
                'regime': old_regime,
                'duration_bars': duration,
                'start': start_bar,
                'end': bar_count
            })
            
            # Check min_hold_bars violation (should be >= 6)
            if duration < 6 and old_regime != "DOWNTREND_HIGHVOL":
                self.violations.append({
                    'type': 'MIN_HOLD_VIOLATION',
                    'symbol': symbol,
                    'regime': old_regime,
                    'duration': duration
                })
                logger.warning(f"⚠️ [VIOLATION] {symbol} regime {old_regime} held only {duration} bars < 6")
        
        self._current_regime[symbol] = (new_regime, bar_count)
    
    def record_panic_event(self, new_long_blocked: bool, existing_long_managed: bool,
                          existing_long_action: str = None):
        """Record BTC Panic Gate activation."""
        self.panic_events.append({
            'timestamp': datetime.now().isoformat(),
            'bar': self._bar_count,
            'new_long_blocked': new_long_blocked,
            'existing_long_managed': existing_long_managed,
            'action': existing_long_action
        })
        
        # Validation: existing long should be managed
        if not existing_long_managed:
            self.violations.append({
                'type': 'PANIC_NO_EXISTING_MANAGEMENT',
                'bar': self._bar_count
            })
            logger.warning(f"⚠️ [VIOLATION] Panic gate but no existing long management")
    
    def record_candidate_scores(self, top_candidates: List[Dict]):
        """Record candidate scoring results."""
        for c in top_candidates:
            self.candidate_scores.append({
                'bar': self._bar_count,
                'symbol': c.get('symbol'),
                'score': c.get('score'),
                'final_score': c.get('final_score'),
                'penalty': c.get('penalty', 1.0),
                'penalty_reasons': c.get('penalty_reasons', [])
            })
    
    def record_position_count(self, count: int):
        """Record current position count."""
        self.position_counts[count] += 1
        
        # Validation: max 2 positions
        if count > 2:
            self.violations.append({
                'type': 'MAX_POSITIONS_EXCEEDED',
                'count': count,
                'bar': self._bar_count
            })
            logger.error(f"❌ [VIOLATION] Position count {count} > 2")
    
    def record_range_highvol_trade(self):
        """Record trade attempt in RANGE_HIGHVOL (should never happen)."""
        self.range_highvol_trades += 1
        self.violations.append({
            'type': 'RANGE_HIGHVOL_TRADE',
            'bar': self._bar_count
        })
        logger.error(f"❌ [VIOLATION] Trade executed in RANGE_HIGHVOL regime")
    
    # ============================================================
    # Report Generation
    # ============================================================
    
    def generate_report(self) -> Dict:
        """Generate comprehensive validation report."""
        report = {
            'generated_at': datetime.now().isoformat(),
            'total_bars': self._bar_count,
            'validation_passed': len(self.violations) == 0,
            
            # Metric 1: Entries per bar distribution
            'entries_per_bar': {
                'distribution': dict(self._calc_entry_distribution()),
                'total_entries': sum(self.entries_per_bar.values()),
                'max_per_bar': max(self.entries_per_bar.values()) if self.entries_per_bar else 0
            },
            
            # Metric 2: Regime duration
            'regime_durations': self._calc_regime_durations(),
            
            # Metric 3: RANGE_HIGHVOL
            'range_highvol': {
                'trades_executed': self.range_highvol_trades,
                'trades_blocked': self.range_highvol_blocked,
                'passed': self.range_highvol_trades == 0
            },
            
            # Metric 4: Panic events
            'panic_events': {
                'count': len(self.panic_events),
                'all_longs_blocked': all(e['new_long_blocked'] for e in self.panic_events) if self.panic_events else True,
                'existing_managed': sum(1 for e in self.panic_events if e['existing_long_managed']),
                'events': self.panic_events[-10:]  # Last 10
            },
            
            # Metric 5: Candidate scoring
            'candidate_scoring': self._calc_scoring_stats(),
            
            # Metric 6: Position counts
            'position_distribution': dict(self.position_counts),
            
            # Metric 7: Rejection reasons
            'rejection_reasons': dict(sorted(
                self.rejection_reasons.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:20]),
            
            # Violations
            'violations': {
                'count': len(self.violations),
                'items': self.violations[-50:]  # Last 50
            }
        }
        
        return report
    
    def _calc_entry_distribution(self) -> Dict[int, int]:
        """Calculate entries per bar distribution."""
        dist = defaultdict(int)
        for count in self.entries_per_bar.values():
            dist[count] += 1
        return dist
    
    def _calc_regime_durations(self) -> Dict:
        """Calculate regime duration statistics."""
        all_durations = defaultdict(list)
        
        for symbol, history in self.regime_history.items():
            for entry in history:
                all_durations[entry['regime']].append(entry['duration_bars'])
        
        stats = {}
        for regime, durations in all_durations.items():
            if durations:
                stats[regime] = {
                    'avg_duration': sum(durations) / len(durations),
                    'min_duration': min(durations),
                    'max_duration': max(durations),
                    'count': len(durations),
                    'below_min_hold': sum(1 for d in durations if d < 6)
                }
        
        return stats
    
    def _calc_scoring_stats(self) -> Dict:
        """Calculate candidate scoring statistics."""
        if not self.candidate_scores:
            return {'avg_score': 0, 'penalty_applied_pct': 0}
        
        scores = [c['score'] for c in self.candidate_scores]
        penalties = [c for c in self.candidate_scores if c['penalty'] < 1.0]
        
        return {
            'avg_score': sum(scores) / len(scores),
            'avg_final_score': sum(c['final_score'] for c in self.candidate_scores) / len(self.candidate_scores),
            'penalty_applied_pct': len(penalties) / len(self.candidate_scores) * 100,
            'common_penalties': self._count_penalty_reasons()
        }
    
    def _count_penalty_reasons(self) -> Dict[str, int]:
        """Count penalty reasons across all candidates."""
        counts = defaultdict(int)
        for c in self.candidate_scores:
            for reason in c.get('penalty_reasons', []):
                counts[reason] += 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])
    
    def save_report(self):
        """Save report to file."""
        report = self.generate_report()
        
        with open(self.report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"📊 Report saved to {self.report_file}")
        
        # Log summary
        logger.info("=" * 60)
        logger.info("📊 DRY-RUN VALIDATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Bars: {report['total_bars']}")
        logger.info(f"Total Entries: {report['entries_per_bar']['total_entries']}")
        logger.info(f"Violations: {report['violations']['count']}")
        logger.info(f"RANGE_HIGHVOL Trades: {report['range_highvol']['trades_executed']} (should be 0)")
        logger.info(f"Panic Events: {report['panic_events']['count']}")
        logger.info(f"Validation: {'✅ PASSED' if report['validation_passed'] else '❌ FAILED'}")
        logger.info("=" * 60)
        
        return report
    
    def print_summary(self):
        """Print human-readable summary to console."""
        report = self.generate_report()
        
        print("\n" + "=" * 60)
        print("📊 DRY-RUN VALIDATION REPORT")
        print("=" * 60)
        
        print(f"\n✅ Overall: {'PASSED' if report['validation_passed'] else '❌ FAILED'}")
        print(f"   Total Bars Analyzed: {report['total_bars']}")
        print(f"   Total Entries: {report['entries_per_bar']['total_entries']}")
        print(f"   Violations: {report['violations']['count']}")
        
        print("\n📈 Entries Per Bar Distribution:")
        for count, freq in sorted(report['entries_per_bar']['distribution'].items()):
            print(f"   {count} entries: {freq} bars")
        
        print("\n⏱️ Regime Duration (bars):")
        for regime, stats in report['regime_durations'].items():
            print(f"   {regime}: avg={stats['avg_duration']:.1f}, min={stats['min_duration']}, violations={stats['below_min_hold']}")
        
        print("\n🚫 Rejection Reasons (Top 10):")
        for reason, count in list(report['rejection_reasons'].items())[:10]:
            print(f"   {reason}: {count}")
        
        print("\n" + "=" * 60)
