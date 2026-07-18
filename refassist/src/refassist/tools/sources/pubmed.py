from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import CONTACT_EMAIL

def _eutils_params(**kw) -> Dict[str, Any]:
    p = {"retmode": "json", "tool": "refassist", **kw}
    if CONTACT_EMAIL:
        p["email"] = CONTACT_EMAIL
    return p

class PubMedClient(SourceClient):
    NAME = "pubmed"
    ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    async def _summary_for_term(self, term: str):
        d = await self._get_json(self.ESEARCH, params=_eutils_params(db="pubmed", term=term, retmax="1"))
        ids = d.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None
        d2 = await self._get_json(self.ESUMMARY, params=_eutils_params(db="pubmed", id=ids[0]))
        return d2.get("result", {}).get(ids[0])

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            res = await self._summary_for_term(f'"{doi}"[DOI]')
            self._cache_set(key, res or {})
            return res
        except Exception:
            self._cache_set(key, {})
            return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = f"title:{title.lower()}"
        if (c := self._cache_get(key)): return c
        try:
            d = await self._get_json(self.ESEARCH, params=_eutils_params(db="pubmed", term=title, retmax="1"))
            ids = d.get("esearchresult", {}).get("idlist", [])
            if not ids: return None
            pmid = ids[0]
            d2 = await self._get_json(self.ESUMMARY, params=_eutils_params(db="pubmed", id=pmid))
            res = d2.get("result", {}).get(pmid)
            if res: self._cache_set(key, res)
            return res
        except Exception: return None
