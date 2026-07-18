from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA

# DBLP publication type → our canonical reference type
_DBLP_TYPES = {
    "Conference and Workshop Papers": "conference paper",
    "Journal Articles": "journal article",
    "Informal and Other Publications": "preprint",   # CoRR/arXiv mirrors
    "Informal Publications": "preprint",
    "Books and Theses": "book",
    "Parts in Books or Collections": "book chapter",
}


class DBLPClient(SourceClient):
    """
    Keyless computer-science bibliography (dblp.org).

    Fills a measured Crossref gap: NeurIPS and many CS proceedings are not
    DOI-registered with Crossref at all, but DBLP has essentially every CS
    conference paper with authoritative venue/year/author metadata.
    """
    NAME = "dblp"
    BASE_URL = "https://dblp.org/search/publ/api"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None  # DBLP has no DOI lookup route; title search covers it

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            q = f"{title} {author}" if author else title
            data = await self._get_json(
                self.BASE_URL,
                params={"q": q, "format": "json", "h": 5},
                headers={"User-Agent": DEFAULT_UA},
            )
            hits = ((data or {}).get("result", {}).get("hits", {}).get("hit")) or []
            out = [h.get("info", {}) for h in hits if h.get("info")]
            self._cache_set(key, out)
            return out or None
        except Exception:
            self._cache_set(key, [])
            return None


def dblp_type(info_type: str) -> str:
    return _DBLP_TYPES.get(info_type or "", "")
