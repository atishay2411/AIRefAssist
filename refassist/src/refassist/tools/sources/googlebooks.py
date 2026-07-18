import os
from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA


class GoogleBooksClient(SourceClient):
    """Book metadata via the Google Books API.

    Key-gated: activates only when GOOGLE_BOOKS_API_KEY is set (free key from
    the Google Cloud console). Complements Open Library for monographs.
    """
    NAME = "googlebooks"
    BASE_URL = "https://www.googleapis.com/books/v1/volumes"

    def __init__(self, cfg, client=None, limiter=None, cache=None):
        super().__init__(cfg, client=client, limiter=limiter, cache=cache)
        self.api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    def _enabled(self) -> bool:
        return bool(self.api_key)

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        if not self._enabled():
            return None
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            q = f'intitle:"{title}"'
            if author:
                q += f" inauthor:{author}"
            data = await self._get_json(self.BASE_URL, params={
                "q": q, "maxResults": 5, "key": self.api_key,
            }, headers={"User-Agent": DEFAULT_UA})
            out = [it.get("volumeInfo", {}) for it in (data or {}).get("items", []) or []]
            self._cache_set(key, out)
            return out or None
        except Exception:
            self._cache_set(key, [])
            return None
