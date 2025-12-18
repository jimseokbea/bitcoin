import re
from collections import defaultdict

LOG_FILE = "bot_final.log"          # 실제 파일명에 맞게
MAX_TRADES_PER_DAY = 2        # sniper daily cap

# 1) Gate accept pattern
PAT_ACCEPT = re.compile(
    r"\[Gate\]\s+ENTRY ACCEPTED\s+\|\s+trades_today=(\d+)/(\d+)\s+\|\s+symbol=([A-Z0-9/]+)\s+\|\s+setup=([A-Za-z0-9:_-]+)"
)

# 2) Timestamp pattern: adjust if your logger format differs
# Example: 2025-12-13 10:12:01,123 [INFO] ...
# Using standard ISO-like date match at start of line
PAT_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s")

# Optional: detect blocks to verify gate is actively working
PAT_BLOCK = re.compile(r"\[Gate\]\s+Entry Blocked:\s+([A-Z0-9/]+)\s+\((.+)\)")

def audit():
    per_day_accept = defaultdict(list)
    per_day_block = defaultdict(list)
    total_lines = 0

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                total_lines += 1
                mdate = PAT_DATE.search(line)
                date = mdate.group(1) if mdate else "UNKNOWN_DATE"

                m = PAT_ACCEPT.search(line)
                if m:
                    trades_today, cap, symbol, setup = m.groups()
                    per_day_accept[date].append({
                        "symbol": symbol,
                        "setup": setup,
                        "trades_today": int(trades_today),
                        "cap": int(cap),
                        "line": line.strip()
                    })
                    continue

                mb = PAT_BLOCK.search(line)
                if mb:
                    sym, reason = mb.groups()
                    per_day_block[date].append((sym, reason))

    except FileNotFoundError:
        print(f"❌ Log file not found: {LOG_FILE}")
        return

    # Report
    print("🔍 [Sniper Audit] Daily launch limit verification\n")
    print(f"- Log file: {LOG_FILE}")
    print(f"- Total lines scanned: {total_lines}")
    print(f"- Max trades/day (expected): {MAX_TRADES_PER_DAY}\n")

    all_ok = True
    if not per_day_accept:
        print("❌ No 'ENTRY ACCEPTED' records found. Gate may not be wired or no trades occurred.")
        # Determine if this is a fail or just no trades
        # If no trades but blocks exist, gate is working.
        if per_day_block:
             print("   (However, BLOCKED entries were found, so Gate is active.)")
        return

    for day in sorted(per_day_accept.keys()):
        accepts = per_day_accept[day]
        n = len(accepts)
        cap_seen = accepts[-1]["cap"] if accepts else MAX_TRADES_PER_DAY

        status = "✅ PASS" if n <= MAX_TRADES_PER_DAY else "❌ FAIL"
        if n > MAX_TRADES_PER_DAY:
            all_ok = False

        print(f"[{day}] ACCEPTED={n} (cap_seen={cap_seen}) => {status}")

        # Show brief details if failed or for transparency
        if n > MAX_TRADES_PER_DAY or True: # Always show for now to confirm
            print("  └ Trades:")
            for a in accepts:
                print(f"     - {a['symbol']} | {a['setup']} | count={a['trades_today']}/{a['cap']}")

        # Show how often blocks happened (gate is active)
        blocks = per_day_block.get(day, [])
        if blocks:
            reasons = defaultdict(int)
            for _, r in blocks:
                reasons[r] += 1
            top_reasons = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"  └ BLOCKED={len(blocks)} top_reasons={top_reasons}")

    print("\n" + "="*50)
    if all_ok:
        print("🎉 RESULT: Daily launch limit adhered to across all days.")
    else:
        print("🚨 RESULT: Daily launch limit violated. Check SignalGate wiring or mark_entry placement.")
        print("   - Common causes:")
        print("     1) mark_entry() not called immediately after successful entry")
        print("     2) multiple bots running (duplicate execution)")
        print("     3) entry order failure but gate marked anyway, or vice versa")

if __name__ == "__main__":
    audit()
