import time
from typing import Optional


class SentimentClient:
    """
    실제로는 뉴스/트위터/온체인 데이터를 붙여야 하지만
    지금은 템플릿 구조만 제공.
    """

    def __init__(self, enabled: bool, cache_minutes: int):
        self.enabled = enabled
        self.cache_minutes = cache_minutes
        self._last_fetch = 0.0
        self._last_score: Optional[float] = None

    def fetch_score(self) -> float:
        if not self.enabled:
            return 0.0

        now = time.time()
        if self._last_score is not None and now - self._last_fetch < self.cache_minutes * 60:
            return self._last_score

        # TODO: 외부 API(뉴스/트위터 등)로부터 점수 계산
        score = 0.0

        self._last_score = score
        self._last_fetch = now
        return score
