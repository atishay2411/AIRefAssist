from typing import Any, Dict, List, Optional
from ..http import SourceClient

class CrossrefClient(SourceClient):
    NAME = "crossref"; BASE_URL = "https://api.crossref.org/works"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)): return c
        try:
            data = await self._get_json(f"{self.BASE_URL}/{doi}")
            msg = data.get("message")
            if msg: self._cache_set(key, msg)
            return msg
        except Exception: return None

    async def by_title(self, title: str) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}"
        params = {
            "query.title": title,
            "rows": 5,
            "select": "title,author,container-title,short-container-title,issued,DOI,page,volume,issue,published-print,published-online,type"
        }
        try:
            data = await self._get_json(self.BASE_URL, params=params)
            items = data.get("message", {}).get("items", [])[:5]
            if items: self._cache_set(key, items[0])
            return items
        except Exception: return None
