import os
from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import normalize_text, DEFAULT_UA

class IEEEXploreClient(SourceClient):
    """
    Optional IEEE Xplore source (high authority for IEEE venues).

    Enable by setting environment variable:
      IEEE_API_KEY=<your key>

    Notes:
      - API: https://developer.ieee.org/
      - We only use /search/articles with restrictive filters
      - We keep this conservative: prefer by_doi; by_title returns up to 3
    """
    NAME = "ieeexplore"
    BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

    def __init__(self, cfg, client=None, limiter=None, cache=None):
        super().__init__(cfg, client=client, limiter=limiter, cache=cache)
        self.api_key = os.getenv("IEEE_API_KEY")

    def _enabled(self) -> bool:
        return bool(self.api_key)

    async def _search(self, params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        if not self._enabled() or self.client is None:
            return None
        q = dict(params)
        q.update({
            "apikey": self.api_key,
            "format": "json",
            "max_records": "3",
            "start_record": "1",
        })
        try:
            async with self.limiter:
                r = await self.client.get(self.BASE_URL, params=q, headers={"User-Agent": DEFAULT_UA})
            r.raise_for_status()
            data = r.json() if "json" in (r.headers.get("content-type","")) else {}
            arts = (data.get("articles") or [])[:3]
            return arts or None
        except Exception:
            return None

    def _norm(self, art: Dict[str, Any]) -> Dict[str, Any]:
        # Normalize IEEE Xplore record to our candidate schema
        title = normalize_text(art.get("title") or "")
        # authors list is like [{"full_name":"A B Last"}, ...]
        authors = []
        for a in (art.get("authors", {}).get("authors") or []):
            nm = a.get("full_name")
            if nm:
                authors.append(normalize_text(nm))
        journal = normalize_text(art.get("publication_title") or "")
        vol = normalize_text(art.get("volume") or "")
        issue = normalize_text(art.get("issue") or "")
        # pages can be start_page / end_page
        sp = normalize_text(art.get("start_page") or "")
        ep = normalize_text(art.get("end_page") or "")
        pages = f"{sp}-{ep}" if sp and ep else (sp or ep or "")
        year = normalize_text(art.get("publication_year") or "")
        month = ""  # Xplore rarely provides month consistently
        doi = normalize_text(art.get("doi") or "")
        return {
            "source": self.NAME,
            "title": title,
            "authors": authors,
            "journal_name": journal,
            "journal_abbrev": "",
            "volume": vol,
            "issue": issue,
            "pages": pages,
            "doi": doi,
            "year": year,
            "month": month,
        }

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        if not self._enabled():
            return None
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)): return c
        res = await self._search({"doi": doi})
        if not res:
            return None
        it = self._norm(res[0])
        self._cache_set(key, it)
        return it

    async def by_title(self, title: str) -> Optional[List[Dict[str, Any]]]:
        if not self._enabled():
            return None
        res = await self._search({"article_title": title})
        if not res:
            return None
        return [self._norm(a) for a in res]
