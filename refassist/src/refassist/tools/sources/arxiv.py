import re
from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import normalize_text, DEFAULT_UA

_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL | re.IGNORECASE)


def _parse_first_entry(xml: str) -> Optional[Dict[str, Any]]:
    """
    Parse the first <entry> of an arXiv Atom feed.
    Parsing must be scoped to the entry — the feed itself has a top-level
    <title> ("ArXiv Query: ...") that would otherwise be picked up.
    """
    m = _ENTRY_RE.search(xml or "")
    if not m:
        return None
    entry = m.group(1)

    tmatch = re.search(r"<title>(.*?)</title>", entry, flags=re.DOTALL | re.IGNORECASE)
    if not tmatch:
        return None
    title = normalize_text(re.sub(r"\s+", " ", tmatch.group(1)))
    auths = [normalize_text(a) for a in re.findall(r"<name>(.*?)</name>", entry, flags=re.IGNORECASE)]
    ymatch = re.search(r"<published>(\d{4})-", entry, flags=re.IGNORECASE)
    year = ymatch.group(1) if ymatch else ""
    dmatch = re.search(r'<arxiv:doi[^>]*>(.*?)</arxiv:doi>', entry, flags=re.DOTALL | re.IGNORECASE)
    doi = normalize_text(dmatch.group(1)) if dmatch else ""
    return {"title": title, "authors": auths, "journal_name": "arXiv", "year": year, "doi": doi}


class ArxivClient(SourceClient):
    NAME = "arxiv"
    BASE_URL = "https://export.arxiv.org/api/query"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def _fetch(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.client is None:
            return None
        async with self.limiter:
            r = await self.client.get(
                self.BASE_URL,
                params=params,
                headers={"Accept": "application/atom+xml", "User-Agent": DEFAULT_UA},
            )
            r.raise_for_status()
            return _parse_first_entry(r.text)

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            q = f'ti:"{title}"'
            if author:
                q += f' AND au:"{author}"'
            out = await self._fetch({"search_query": q, "start": 0, "max_results": 1})
            self._cache_set(key, out or {})
            return out
        except Exception:
            self._cache_set(key, {})
            return None

    async def by_id(self, arx: str) -> Optional[Dict[str, Any]]:
        try:
            return await self._fetch({"id_list": arx})
        except Exception:
            return None
