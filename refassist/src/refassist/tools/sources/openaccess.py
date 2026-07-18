"""Keyless open-scholarship sources: Europe PMC, Unpaywall, bioRxiv/medRxiv, DOAJ.

Each fills a coverage gap:
  - Europe PMC — biomedical + life sciences with far friendlier rate limits
    than NCBI eutils, and preprint indexing.
  - Unpaywall  — per-DOI verification plus the open-access location, which
    doubles as author-checkable evidence.
  - bioRxiv/medRxiv — preprints outside arXiv (10.1101/... DOIs), including
    the pointer to the published journal version.
  - DOAJ       — open-access journals, some of which lag in Crossref.
"""
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..http import SourceClient
from ..utils import DEFAULT_UA, CONTACT_EMAIL


class EuropePMCClient(SourceClient):
    NAME = "europepmc"
    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    async def _search(self, query: str, page_size: int) -> List[Dict[str, Any]]:
        data = await self._get_json(self.BASE_URL, params={
            "query": query, "format": "json", "pageSize": page_size,
        }, headers={"User-Agent": DEFAULT_UA})
        return (data or {}).get("resultList", {}).get("result", []) or []

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            res = await self._search(f'DOI:"{doi}"', 1)
            self._cache_set(key, res[0] if res else {})
            return res[0] if res else None
        except Exception:
            self._cache_set(key, {})
            return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            q = f'TITLE:"{title}"'
            if author:
                q += f' AND AUTH:"{author}"'
            res = await self._search(q, 5)
            self._cache_set(key, res)
            return res or None
        except Exception:
            self._cache_set(key, [])
            return None


class UnpaywallClient(SourceClient):
    """DOI-registry verification + open-access location. Keyless; requires an
    email parameter per their etiquette."""
    NAME = "unpaywall"
    BASE_URL = "https://api.unpaywall.org/v2"

    async def by_title(self, title: str, author: Optional[str] = None):
        return None  # per-DOI service only

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            data = await self._get_json(
                f"{self.BASE_URL}/{doi}",
                params={"email": CONTACT_EMAIL or "anonymous@refassist.app"},
                headers={"User-Agent": DEFAULT_UA})
            self._cache_set(key, data or {})
            return data or None
        except Exception:
            self._cache_set(key, {})
            return None


class BioRxivClient(SourceClient):
    """bioRxiv/medRxiv preprints — only their own 10.1101/... DOIs."""
    NAME = "biorxiv"
    BASE_URL = "https://api.biorxiv.org/details"

    async def by_title(self, title: str, author: Optional[str] = None):
        return None  # no usable title search API

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        doi = doi.lower().strip()
        if not doi.startswith("10.1101/"):
            return None
        key = f"doi:{doi}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            rec = {}
            for server in ("biorxiv", "medrxiv"):
                data = await self._get_json(f"{self.BASE_URL}/{server}/{doi}",
                                            headers={"User-Agent": DEFAULT_UA})
                coll = (data or {}).get("collection") or []
                if coll and coll[0].get("title"):
                    rec = {**coll[-1], "server": server}  # last = latest version
                    break
            self._cache_set(key, rec)
            return rec or None
        except Exception:
            self._cache_set(key, {})
            return None


class DOAJClient(SourceClient):
    NAME = "doaj"
    BASE_URL = "https://doaj.org/api/search/articles"

    async def _search(self, query: str) -> List[Dict[str, Any]]:
        data = await self._get_json(f"{self.BASE_URL}/{quote(query, safe='')}",
                                    params={"pageSize": 5},
                                    headers={"User-Agent": DEFAULT_UA})
        return [r.get("bibjson", {}) for r in (data or {}).get("results", []) or []]

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            res = await self._search(f'doi:"{doi}"')
            self._cache_set(key, res[0] if res else {})
            return res[0] if res else None
        except Exception:
            self._cache_set(key, {})
            return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            res = await self._search(f'title:"{title}"')
            self._cache_set(key, res)
            return res or None
        except Exception:
            self._cache_set(key, [])
            return None
