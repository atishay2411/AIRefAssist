import os
from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA

class SemanticScholarClient(SourceClient):
    NAME = "semanticscholar"; BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"
    S2_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

    def _headers(self):
        h = {"User-Agent": DEFAULT_UA}
        if self.S2_KEY: h["x-api-key"] = self.S2_KEY
        return h

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)): return c
        try:
            data = await self._get_json(
                f"{self.BASE_URL}/DOI:{doi}",
                params={"fields":"title,venue,year,authors,externalIds,publicationVenue,publicationTypes"},
                headers=self._headers()
            )
            if data and not data.get("error"): self._cache_set(key, data)
            return data if data and not data.get("error") else None
        except Exception: return None

    async def by_title(self, title: str) -> Optional[List[Dict[str, Any]]]:
        try:
            data = await self._get_json(
                f"{self.BASE_URL}/search",
                params={"query": title, "limit":5, "fields":"title,venue,year,authors,externalIds,publicationVenue,publicationTypes"},
                headers=self._headers()
            )
            return (data.get("data") or [])[:5]
        except Exception: return None
