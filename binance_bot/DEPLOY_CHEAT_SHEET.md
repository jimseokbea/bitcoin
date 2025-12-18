# 🚀 AWS 봇 업데이트 및 재시작 가이드 (치트시트)

AWS EC2(Termius)에서 `pm2`로 봇을 운영 중일 때, 코드를 수정하고 적용하는 방법입니다.

## 1단계: 내 컴퓨터(로컬)에서 올리기
VS Code 터미널에서 다음 명령어를 순서대로 입력하여 수정된 코드를 GitHub에 저장합니다.

```bash
git add .
git commit -m "코드 수정 완료"
git push
```

---

## 2단계: AWS 서버(Termius)에서 받기
Termius를 통해 AWS 서버에 접속한 후, 봇 폴더로 이동하여 최신 코드를 당겨옵니다.

```bash
# 봇 폴더로 이동 (폴더명이 다르면 수정 필요)
cd bitcoin/binance_bot

# 최신 코드 받기
git pull
```

---

## 3단계: 봇 재시작 (PM2)
변경된 코드를 적용하기 위해 관리자(pm2)에게 재시작을 요청합니다.

```bash
# 모든 프로세스 재시작
pm2 restart all

# (선택사항) 로그 확인
pm2 logs
# 로그 창 나가기: Ctrl + C
```

---

## 💡 자주 쓰는 PM2 명령어 모음

- **상태 확인**: `pm2 status` or `pm2 list` (현재 봇이 켜져 있는지 확인)
- **로그 보기**: `pm2 logs` (실시간 로그 확인)
- **봇 끄기**: `pm2 stop all`
- **봇 켜기**: `pm2 start run_bot.bat --name binance_bot` (처음 켤 때만)
