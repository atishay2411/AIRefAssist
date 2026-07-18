import asyncio
from typing import Any, Dict, Optional
from cachetools import TTLCache
from .utils import DEFAULT_UA  # fixed import
try:
    import httpx
except Exception:
    httpx = None

class SourceClient:
    NAME: str = "base"
    def __init__(self, cfg, client=None, limiter=None, cache: Optional[TTLCache]=None):
        self.cfg = cfg
        self.client = client or (httpx.AsyncClient(timeout=self.cfg.timeout_s) if httpx is not None else None)
        self.limiter = limiter or asyncio.Semaphore(cfg.concurrency)
        self.cache = cache

    def _cache_get(self, key: str):
        # `is None`, not truthiness: an EMPTY TTLCache is falsy, and treating
        # it as absent silently drops every write to a fresh cache.
        if self.cache is None: return None
        return self.cache.get((self.NAME, key))

    def _cache_set(self, key: str, val: Dict[str, Any]):
        if self.cache is None: return
        self.cache[(self.NAME, key)] = val

    async def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        if self.client is None:
            raise RuntimeError("HTTP client unavailable.")
        hdrs = {"User-Agent": DEFAULT_UA}
        if headers: hdrs.update(headers)
        # Tight retry budget: sources are queried in parallel and consensus
        # tolerates a missing source, so failing fast beats long backoffs.
        # 429 is never retried — the window won't reset within one request,
        # and sleeping on it stalls the whole lookup round.
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self.limiter:
                    r = await self.client.get(url, params=params, headers=hdrs)
                if r.status_code in (500, 502, 503, 504) and attempt <= 2:
                    await asyncio.sleep(min(2 ** attempt, 3))
                    continue
                r.raise_for_status()
                ct = r.headers.get("content-type","")
                if "json" in ct: return r.json()
                return {"_raw": r.text}
            except httpx.HTTPStatusError:
                raise  # 4xx incl. 429: not retryable within this request
            except Exception:
                if attempt <= 2:
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise

    async def by_doi(self, doi: str): raise NotImplementedError
    async def by_title(self, title: str, author: Optional[str] = None): raise NotImplementedError
