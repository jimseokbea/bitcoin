import streamlit as st
import pandas as pd
import yaml
import time
import os
import sqlite3

st.set_page_config(page_title="🦖 Monster Bot HQ", layout="wide")

st.title("🦖 Monster Hunter Bot : Command Center")

# 로그 파일 실시간 읽기
def load_logs():
    try:
        if os.path.exists('bot_final.log'):
            with open('bot_final.log', 'r') as f:
                lines = f.readlines()
                return lines[-20:] # 최근 20줄만
        return ["로그 파일이 아직 생성되지 않았습니다."]
    except:
        return ["로그 파일 읽기 실패"]

# 데이터베이스 읽기
def load_trades():
    try:
        conn = sqlite3.connect('monster_records.db')
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

# 사이드바 설정
st.sidebar.header("Status Control")
if st.sidebar.button("새로고침"):
    # st.experimental_rerun() is deprecated in newer streamlit, using rerun() if available or just pass
    try:
        st.rerun()
    except:
        pass

# 메인 화면 구성
col1, col2 = st.columns(2)

with col1:
    st.subheader("📡 Live Logs")
    logs = load_logs()
    # Reverse to show newest first
    for log in reversed(logs):
        st.text(log.strip())

with col2:
    st.subheader("💰 Asset Trend")
    df_trades = load_trades()
    
    if not df_trades.empty:
        # PnL Chart
        df_trades['cumulative_pnl'] = df_trades['pnl'].cumsum()
        st.line_chart(df_trades['cumulative_pnl'])
        
        # Recent History Table
        st.write("Recent Trades")
        st.dataframe(df_trades[['timestamp', 'symbol', 'side', 'pnl', 'strategy_type']].head(10))
    else:
        st.info("데이터 수집 중... (거래 내역이 쌓이면 차트가 표시됩니다)")

st.write("---")
st.warning("⚠️ 봇을 강제로 종료하려면 서버 터미널을 이용하세요.")
