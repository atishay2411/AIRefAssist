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
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            data = await self._get_json(
                f"{self.BASE_URL}/DOI:{doi}",
                params={"fields":"title,venue,year,authors,externalIds,publicationVenue,publicationTypes"},
                headers=self._headers()
            )
            ok = data and not data.get("error")
            self._cache_set(key, data if ok else {})
            return data if ok else None
        except Exception:
            self._cache_set(key, {})  # negative cache: S2 rate-limits persist within a run
            return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c
        try:
            query = f"{title} {author}" if author else title
            data = await self._get_json(
                f"{self.BASE_URL}/search",
                params={"query": query, "limit":5, "fields":"title,venue,year,authors,externalIds,publicationVenue,publicationTypes"},
                headers=self._headers()
            )
            out = (data.get("data") or [])[:5]
            self._cache_set(key, out)
            return out
        except Exception:
            self._cache_set(key, [])  # negative cache (S2 rate-limits aggressively without a key)
            return None
