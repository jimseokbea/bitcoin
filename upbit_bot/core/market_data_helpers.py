from datetime import datetime
from typing import List, Dict, Any, Optional

from .upbit_client import UpbitClient


def filter_intraday_candles_utc(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    분봉 리스트(Upbit 원본, 과거→현재)에서
    가장 최신 캔들의 UTC 날짜와 같은 것만 골라 반환.
    """
    if not candles:
        return []
    # Upbit 분봉: candle_date_time_utc = "YYYY-MM-DDTHH:MM:SS"
    last_date_str = candles[-1]["candle_date_time_utc"][:10]  # "YYYY-MM-DD"
    intraday = [
        c for c in candles
        if c["candle_date_time_utc"].startswith(last_date_str)
    ]
    return intraday


def get_previous_day_candle(client: UpbitClient, market: str) -> Optional[Dict[str, Any]]:
    """
    Upbit 일봉 API에서 전일 캔들을 반환.
    - days[0]: 최신 일봉 (오늘 또는 마지막 거래일)
    - days[1]: 그 전 날
    """
    days = client.get_day_candles(market, count=2)
    if len(days) < 2:
        return None
    return days[1]
