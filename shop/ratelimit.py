"""Tiny in-memory sliding-window limiter (one per app, so restarts start clean)."""
import threading
import time


class RateLimiter:
    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._hits = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        recent = [t for t in self._hits.get(key, []) if now - t < self.window]
        if recent:
            self._hits[key] = recent
        else:
            self._hits.pop(key, None)
        return recent

    def blocked(self, key):
        with self._lock:
            return len(self._recent(key, time.time())) >= self.limit

    def record(self, key):
        with self._lock:
            now = time.time()
            self._recent(key, now)
            self._hits.setdefault(key, []).append(now)

    def allow(self, key):
        """Record a hit and say whether it was within the limit."""
        with self._lock:
            now = time.time()
            recent = self._recent(key, now)
            if len(recent) >= self.limit:
                return False
            self._hits.setdefault(key, []).append(now)
            return True

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)
