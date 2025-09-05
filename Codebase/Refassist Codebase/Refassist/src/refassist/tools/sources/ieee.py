import os
from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA

class IEEEXploreClient(SourceClient):
    NAME = "ieee"
    BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(self, cfg, client=None, limiter=None, cache=None):
        super().__init__(cfg, client, limiter, cache)
        self.api_key = os.getenv("IEEE_API_KEY")

    def _enabled(self) -> bool:
        return bool(self.api_key)

    async def _query(self, params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        if not self._enabled() or self.client is None:
            return None
        q = dict(params)
        q.update({
            "apikey": self.api_key,
            "format": "json",
            "max_records": 5,
            "start_record": 1,
        })
        try:
            async with self.limiter:
                r = await self.client.get(self.BASE_URL, params=q, headers={"User-Agent": DEFAULT_UA})
                r.raise_for_status()
                data = r.json()
            return data.get("articles") or []
        except Exception:
            return None

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        items = await self._query({"doi": doi})
        return (items or [None])[0] if items else None

    async def by_title(self, title: str) -> Optional[List[Dict[str, Any]]]:
        # Use exact phrase bias; IEEE API uses querytext
        items = await self._query({"querytext": f"\"{title}\""})
        return items or None
