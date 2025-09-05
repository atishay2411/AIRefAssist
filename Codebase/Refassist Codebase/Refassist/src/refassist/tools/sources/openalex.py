from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA

class OpenAlexClient(SourceClient):
    NAME = "openalex"
    BASE_URL = "https://api.openalex.org/works"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)):
            return c
        try:
            data = await self._get_json(
                self.BASE_URL,
                params={"filter": f"doi:{doi}"},
                headers={"User-Agent": DEFAULT_UA}
            )
            items = data.get("results", [])
            it = items[0] if items else None
            if it:
                self._cache_set(key, it)
            return it
        except Exception:
            return None

    async def by_title(self, title: str) -> Optional[List[Dict[str, Any]]]:
        try:
            # NOTE: OpenAlex uses `per_page`, not `per-page`
            data = await self._get_json(
                self.BASE_URL,
                params={"filter": f"title.search:{title}", "per_page": 5},
                headers={"User-Agent": DEFAULT_UA}
            )
            return (data.get("results") or [])[:5]
        except Exception:
            return None
