"""ごく単純なトークンバケット式レートリミッタ。

kabuステーションAPIは発注系・情報系ともに概ね秒間10リクエストの流量制限があるため、
安全マージンを取ったデフォルト(秒間8件)でクライアント側から自主規制する。
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, max_per_sec: float = 8.0) -> None:
        self._min_interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_call = now
