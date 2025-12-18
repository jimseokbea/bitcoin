# Zombie Bot Verification Checklist
# ==================================
# 48-hour Testnet with FORCED Events

## 🚨 CRITICAL: Don't just "run and hope"!
## Force all 3 failure scenarios to prove resilience.

---

## Test #1: Network Disconnection (FORCE IT)
### How to force:
```powershell
# Method 1: Disable adapter for 30 seconds
netsh interface set interface "Wi-Fi" disable
# Wait 30 seconds...
netsh interface set interface "Wi-Fi" enable

# Method 2: Block Binance IPs (less intrusive)
# Add firewall rule temporarily
```

### Expected behavior:
- Bot logs `⚠️ 메인 루프 에러: ...` (network error)
- Bot does NOT crash
- After reconnect: `state_snapshot.json` matches exchange position
- Log shows recovery/resync

### Pass criteria in logs:
```
✅ "메인 루프 에러" followed by normal operation
✅ "State snapshot saved" after recovery
✅ No duplicate positions created
```

---

## Test #2: Ghost Drift (FORCE IT)
### How to force:
1. Start bot, wait for position entry
2. On Binance app/web: Manually close position with market order
3. Watch bot detect position=0

### Expected behavior:
- Bot detects `position=0` on next loop
- Bot calls `cleanup_orphans()`
- Orphan SL/TP orders are cancelled
- Bot does NOT try to "close" an already-closed position

### Pass criteria in logs:
```
✅ "Orphan orders detected" or "Orphan cleanup done"
✅ NO "❌ Close Fail" errors after position=0
✅ Bot continues scanning for new entries
```

---

## Test #3: TP/SL Remnant (FORCE IT)
### How to force:
1. Start bot, wait for position entry with SL
2. Price hits TP naturally (or manipulate with limit order on app)
3. Watch what happens to the SL order

### Expected behavior:
- Position closes via TP
- SL order is detected as orphan
- SL order is cancelled automatically
- NO new position created by orphan SL triggering

### Pass criteria in logs:
```
✅ "거래 종료" log appears
✅ "Orphan cleanup done" appears
✅ NO unexpected position entries in next 5 minutes
```

---

## 📋 AUTOMATED AUDIT (After 48 hours)
```bash
python audit_zombie_test.py bot_final.log
```

### Audit checks:
- [ ] Market cache reloaded (every 6 hours)
- [ ] SL sync guard triggered (if partial close happened)
- [ ] Orphan cleanup executed (at least once)
- [ ] Kill switch recorded errors (proves it's working)
- [ ] Fingerprints logged (order traceability)

---

## 🎯 PASS CRITERIA FOR LIVE DEPLOYMENT

### All 3 forced tests must show:
1. ✅ Bot survived network disconnect
2. ✅ Ghost position handled cleanly
3. ✅ Orphan orders cleaned up

### Audit script must show:
```
🏆 FINAL VERDICT: ✅ ALL TESTS PASSED
```

---

## 🚀 STAGED DEPLOYMENT (After Testnet Pass)

### Phase 1: First 24 hours
```python
# In main_binance.py:
leverage = 4          # Half of 7
risk_per_trade = 0.007  # Half of 0.013
```
Symbols: BTC/USDT, ETH/USDT only

### Phase 2: After 24h success
- Increase to normal leverage (7x)
- Keep reduced risk (0.01)
- Add SOL/USDT

### Phase 3: Full deployment
- Normal parameters
- Full symbol universe

