import streamlit as st
import pandas as pd
import yaml
import time
import os
import subprocess
import sys
import signal

# --- Constants ---
CONFIG_FILE = 'config.yaml'
LOG_FILE = 'bot_final.log'
PID_FILE = 'bot.pid'
DB_FILE = 'monster_records.db'

st.set_page_config(
    page_title="🦖 Monster Bot HQ", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Helper Functions ---

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

def save_config(config_data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

def get_bot_status():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            
            # Check if process exists (Windows compatible)
            # Using simple tasklist check as psutil might not be installed
            # or just assume it's running if PID file exists, logic to be refined
            # For robustness, we try sending signal 0
            try:
                os.kill(pid, 0)
                return True, pid
            except OSError:
                return False, None
        except:
            return False, None
    return False, None

def start_bot():
    if os.path.exists("main.py"):
        # Run main.py as a separate process
        # Windows requires shell=True sometimes or specific creation flags
        if sys.platform == "win32":
            proc = subprocess.Popen([sys.executable, "main.py"], 
                                    creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            proc = subprocess.Popen([sys.executable, "main.py"])
        
        with open(PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        return True
    return False

def stop_bot():
    status, pid = get_bot_status()
    if status and pid:
        try:
            os.kill(pid, signal.SIGTERM)
            # Give it a second
            time.sleep(1)
            # Force kill if needed (SIGKILL on unix, Terminate on windows)
            # Windows os.kill with SIGTERM might not be enough, but let's try
            # If still running, try force
            try:
                os.kill(pid, signal.SIGTERM) 
            except: 
                pass
        except:
            pass
        
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return True
    
    # Cleanup stale PID file
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    return False

def load_logs(lines_to_read=50):
    try:
        if os.path.exists(LOG_FILE):
            # Read last N lines efficiently?
            # For simplicity, read all and slice, optimize later if huge
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                # Seek to end and backup? Or just readlines
                lines = f.readlines()
                return lines[-lines_to_read:]
        return ["Waiting for logs..."]
    except Exception as e:
        return [f"Error reading log: {e}"]

# --- Sidebar: Control Panel ---
st.sidebar.title("🦖 Control Panel")

is_running, current_pid = get_bot_status()

if is_running:
    st.sidebar.success(f"🟢 Running (PID: {current_pid})")
    if st.sidebar.button("🛑 Stop Bot", type="primary"):
        stop_bot()
        st.rerun()
else:
    st.sidebar.error("🔴 Stopped")
    if st.sidebar.button("▶️ Start Bot", type="primary"):
        start_bot()
        st.rerun()

st.sidebar.divider()
st.sidebar.info("UI Auto-refreshes every 10s")
if st.sidebar.button("🔄 Force Refresh"):
    st.rerun()

# --- Main Content ---
st.title("🦖 Monster Hunter Bot Dashboard")

tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "⚙️ Settings", "📝 Logs"])

# Tab 1: Dashboard
with tab1:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Asset Performance")
        try:
            conn = sqlite3.connect(DB_FILE)
            df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp DESC", conn)
            conn.close()
            
            if not df.empty:
                # Calculate Cumulative PnL
                if 'pnl' in df.columns:
                    df['cumulative_pnl'] = df['pnl'].cumsum()
                    st.line_chart(df.set_index('timestamp')['cumulative_pnl'])
                
                st.subheader("Recent Trades")
                st.dataframe(df.head(10), use_container_width=True)
            else:
                st.info("No trade data available yet.")
        except Exception as e:
            st.warning(f"Could not load trade database: {e}")

    with col2:
        st.subheader("Active Stats")
        # Here we could read a 'state.json' if the bot produces one
        # For now, just show config summary
        config = load_config()
        if 'risk' in config:
            st.metric("Risk per Trade", f"{config['risk'].get('risk_per_trade_pct', 0.01)*100}%")
            st.metric("Max Leverage", f"{config['risk'].get('max_leverage', 1)}x")
        
        st.metric("Bot Status", "Running" if is_running else "Stopped")


# Tab 2: Settings
with tab2:
    st.header("Configuration Editor")
    config = load_config()
    
    if config:
        with st.form("config_form"):
            # Universe
            st.subheader("Universe (Whitelist)")
            whitelist_txt = st.text_area("Symbols (One per line)", 
                                         value="\n".join(config.get('universe', {}).get('whitelist', [])))
            
            # Risk
            st.subheader("Risk Management")
            risk_pct = st.number_input("Risk per Trade (%)", 
                                       value=float(config.get('risk', {}).get('risk_per_trade_pct', 0.01))*100,
                                       step=0.1)
            leverage = st.number_input("Max Leverage", 
                                       value=int(config.get('risk', {}).get('max_leverage', 1)),
                                       step=1)
            
            # Strategy
            st.subheader("Strategy Parameters")
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                tf = st.selectbox("Timeframe", ["1m", "3m", "5m", "15m", "1h"], 
                                  index=["1m", "3m", "5m", "15m", "1h"].index(config.get('strategy', {}).get('tf', '5m')))
                rsi_period = st.number_input("RSI Period", 
                                             value=int(config.get('strategy', {}).get('rsi_period', 14)))
            with col_s2:
                bb_len = st.number_input("Bollinger Length", 
                                         value=int(config.get('strategy', {}).get('bb_len', 20)))
                bb_std = st.number_input("Bollinger Std Dev", 
                                         value=float(config.get('strategy', {}).get('bb_std', 2.0)))
            
            submitted = st.form_submit_button("💾 Save Changes")
            
            if submitted:
                # Update Config Dict
                config['universe']['whitelist'] = [s.strip() for s in whitelist_txt.split('\n') if s.strip()]
                config['risk']['risk_per_trade_pct'] = risk_pct / 100.0
                config['risk']['max_leverage'] = int(leverage)
                
                # Check if strategy section exists
                if 'strategy' not in config: config['strategy'] = {}
                config['strategy']['tf'] = tf
                config['strategy']['rsi_period'] = int(rsi_period)
                config['strategy']['bb_len'] = int(bb_len)
                config['strategy']['bb_std'] = float(bb_std)
                
                save_config(config)
                st.success("Configuration saved! Restart bot to apply changes.")
    else:
        st.error("Could not load config.yaml")

# Tab 3: Logs
with tab3:
    st.header("Live System Logs")
    if st.button("Refresh Logs"):
        st.rerun()
    
    log_lines = load_logs(100)
    # Reverse for latest on top
    log_text = "".join(reversed(log_lines))
    st.code(log_text, language="log")

# Auto-refresh logic (using query params trigger or similar is outdated, just simple sleep loop in main is bad)
# Streamlit has st.empty() loops but that blocks UI.
# We will rely on user refresh or sidebar auto-refresh hint
# Modern streamlit: st.empty with loop
