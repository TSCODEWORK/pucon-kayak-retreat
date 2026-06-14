"""
Shared utilities — imported by db.py, sheets.py, and app.py.

Kept here so _parse_dt and Cache are defined exactly once.
"""

import time
from datetime import datetime


def _parse_dt(s):
    """Parse a datetime string in any of our canonical formats.
    Returns a datetime object, or None if the string is empty or unparseable.
    """
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class Cache:
    """Tiny TTL cache used by DatabaseClient and SheetsClient.

    Usage:
        self._cache = Cache(ttl=5)
        data = self._cache.get("key", fetch_fn, force_refresh=False)
        self._cache.clear()
    """

    def __init__(self, ttl: float = 5):
        self._store: dict = {}
        self._ts: dict = {}
        self._ttl = ttl

    def get(self, key: str, fetch_fn, force_refresh: bool = False):
        if not force_refresh and key in self._store:
            if time.time() - self._ts.get(key, 0) < self._ttl:
                return self._store[key]
        data = fetch_fn()
        self._store[key] = data
        self._ts[key] = time.time()
        return data

    def clear(self):
        self._store.clear()
        self._ts.clear()
