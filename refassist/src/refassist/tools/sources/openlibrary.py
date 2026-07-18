from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA

class OpenLibraryClient(SourceClient):
    """
    Keyless book metadata source (openlibrary.org). Only queried for
    book-like references — Crossref/OpenAlex coverage of monographs is poor
    (e.g. Goodfellow's "Deep Learning" has no Crossref DOI at all).
    """
    NAME = "openlibrary"
    BASE_URL = "https://openlibrary.org/search.json"
    _FIELDS = "title,author_name,first_publish_year,publisher,isbn,number_of_pages_median"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            params: Dict[str, Any] = {"title": title, "limit": 3, "fields": self._FIELDS}
            if author:
                params["author"] = author
            data = await self._get_json(self.BASE_URL, params=params,
                                        headers={"User-Agent": DEFAULT_UA})
            docs = (data or {}).get("docs") or []
            doc = docs[0] if docs else {}
            self._cache_set(key, doc)
            return doc or None
        except Exception:
            self._cache_set(key, {})
            return None
