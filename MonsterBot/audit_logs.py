import re
import sys
from datetime import datetime

LOG_FILE = "logs/bot.log"

def analyze_audit():
    print(f"🕵️ Analyzing {LOG_FILE} for Sniper Verification...")
    
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except:
        print("❌ Log file not found.")
        return

    # metrics
    entries = 0
    atomic_sl = 0
    sl_sync = 0
    ghost_guard = 0
    fuse_trigger = 0
    tp1_hit = 0
    daily_limit = False
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    for line in lines:
        if today not in line: continue # Today only
        
        if "✅ [Entry]" in line: entries += 1
        if "✅ [Atomic SL] replaced" in line: atomic_sl += 1
        if "🔄 [SL Sync]" in line: sl_sync += 1
        if "🧹 [Manage] Position closed externally" in line: ghost_guard += 1
        if "🛡️ [BTC FUSE]" in line: fuse_trigger += 1
        if "💰 [TP1]" in line: tp1_hit += 1
        if "Daily Loss Limit Hit" in line: daily_limit = True

    print(f"\n📊 [Daily Report: {today}]")
    print(f"1. Sniper Entries: {entries} (Target: 1-2) {'✅' if 0 < entries <= 3 else '⚠️ check'}")
    print(f"2. Atomic SL Ops:  {atomic_sl} (Live Updates) {'✅' if atomic_sl > 0 else 'waiting'}")
    print(f"3. SL Sync Events: {sl_sync} (Partial Close Sync) {'✅' if tp1_hit > 0 and sl_sync > 0 else 'waiting'}")
    print(f"4. Ghost Kills:    {ghost_guard} (Should be 0 ideally) {'✅' if ghost_guard == 0 else '⚠️ Detected'}")
    print(f"5. BTC Fuse:       {fuse_trigger} (Defense Count)")
    
    print("\n🧐 Final Verdict:")
    if entries > 3:
        print("❌ OVER-TRADING DETECTED! (entries > 3)")
    elif ghost_guard > 0:
        print("⚠️ GHOST POSITIONS DETECTED! Check latency.")
    else:
        print("✅ SYSTEM NORMAL. Proceed with observation.")

if __name__ == "__main__":
    analyze_audit()
