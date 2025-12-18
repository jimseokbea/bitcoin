@echo off
chcp 65001
echo 🛑 몬스터 헌터 봇을 종료합니다...

:: 1. 제목으로 배치 파일 창 종료
taskkill /FI "WINDOWTITLE eq Monster Hunter Bot (Auto-Restart)" /F

:: 2. 혹시 남아있는 내 봇의 Python 프로세스 종료 (주의: 다른 파이썬도 꺼질 수 있음)
:: 안전을 위해 제목 기반 종료가 우선입니다.
:: taskkill /IM python.exe /F

echo.
echo ✅ 종료 명령을 보냈습니다.
echo 만약 검은색 창이 아직 떠있다면, 직접 X를 눌러 닫아주세요.
pause
