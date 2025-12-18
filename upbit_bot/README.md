# 업비트 멀티 코인 자동매매 봇 (Upbit Multi-Coin Trading Bot)

## 1. 설치 및 설정 (Setup)

### 필수 패키지 설치
```bash
pip install -r requirements.txt
```

### 설정 파일 수정
`config/settings.yaml` 파일을 열어 업비트 API 키와 텔레그램 봇 토큰을 입력하세요.
```yaml
upbit:
  access_key: "YOUR_ACCESS_KEY"  # 업비트 Access Key
  secret_key: "YOUR_SECRET_KEY"  # 업비트 Secret Key

telegram:
  bot_token: "YOUR_TELEGRAM_BOT_TOKEN" # 텔레그램 봇 토큰
  chat_id: "YOUR_CHAT_ID"              # 텔레그램 채팅 ID
```

## 2. 데이터 수집 (백테스트용)
백테스트를 위해 과거 캔들 데이터를 다운로드합니다.
```bash
python scripts/upbit_candle_downloader_multi.py --markets KRW-BTC,KRW-ETH,KRW-XRP --unit 5 --from_date 2024-01-01 --to_date "2024-03-31 23:59:00"
```

## 3. 백테스트 실행 (Backtesting)
다운로드한 데이터로 전략을 검증합니다.
`config/settings.yaml` 파일의 `backtest` 섹션에서 CSV 파일 경로가 올바른지 확인하세요.
```bash
python main_backtest.py
```

## 4. 실거래 실행 (Live Trading)
봇을 실거래 모드로 실행합니다.
**주의**: 충분한 테스트 후 실행하시고, 처음에는 소액(`min_krw`)으로 시작하세요.
```bash
python main_multi_live.py
```
