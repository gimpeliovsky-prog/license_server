import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            bucket = self._requests.setdefault(key, deque())
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True
