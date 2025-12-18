import uuid
import jwt
import hashlib
import requests
from urllib.parse import urlencode
from typing import Any, Dict, List, Optional


class UpbitClient:
    BASE_URL = "https://api.upbit.com/v1"

    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key

    def _make_headers(self, query: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        if query:
            query_string = urlencode(query).encode()
            m = hashlib.sha512()
            m.update(query_string)
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        jwt_token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {jwt_token}"}

    # -------- 분봉 캔들 --------
    def get_candles(self, market: str, unit: int = 5, count: int = 200) -> List[Dict[str, Any]]:
        """
        분봉 캔들
        - 결과: 최신이 0번 인덱스
        """
        url = f"{self.BASE_URL}/candles/minutes/{unit}"
        params = {"market": market, "count": count}
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json()

    # -------- 일봉 캔들 --------
    def get_day_candles(self, market: str, count: int = 2) -> List[Dict[str, Any]]:
        """
        일봉 캔들
        - 결과: 최신이 0번 인덱스
        - 변동성 돌파용으로 전일 캔들을 얻기 위해 count=2 요청 후 [1] 사용
        """
        url = f"{self.BASE_URL}/candles/days"
        params = {"market": market, "count": count}
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json()

    # 계좌
    def get_accounts(self) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/accounts"
        headers = self._make_headers()
        r = requests.get(url, headers=headers, timeout=5)
        r.raise_for_status()
        return r.json()

    def get_krw_balance(self) -> float:
        for acc in self.get_accounts():
            if acc["currency"] == "KRW":
                return float(acc["balance"])
        return 0.0

    # 주문
    def create_order(
        self,
        market: str,
        side: str,
        volume: Optional[str],
        price: Optional[str],
        ord_type: str,
    ) -> Dict[str, Any]:
        """
        시장가 매수: side=bid, ord_type=price, price=투입KRW, volume=None
        시장가 매도: side=ask, ord_type=market, volume=코인수량, price=None
        """
        url = f"{self.BASE_URL}/orders"
        query: Dict[str, Any] = {
            "market": market,
            "side": side,
            "ord_type": ord_type,
        }
        if volume is not None:
            query["volume"] = volume
        if price is not None:
            query["price"] = price

        headers = self._make_headers(query)
        r = requests.post(url, headers=headers, params=query, timeout=5)
        r.raise_for_status()
        return r.json()
