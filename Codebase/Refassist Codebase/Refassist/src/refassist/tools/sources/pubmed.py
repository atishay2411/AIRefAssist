from typing import Any, Dict, Optional
from ..http import SourceClient

class PubMedClient(SourceClient):
    NAME = "pubmed"
    ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def by_title(self, title: str) -> Optional[Dict[str, Any]]:
        key = f"title:{title.lower()}"
        if (c := self._cache_get(key)): return c
        try:
            d = await self._get_json(self.ESEARCH, params={"db":"pubmed","term":title,"retmode":"json","retmax":"1","tool":"refassist","email":"you@example.com"})
            ids = d.get("esearchresult", {}).get("idlist", [])
            if not ids: return None
            pmid = ids[0]
            d2 = await self._get_json(self.ESUMMARY, params={"db":"pubmed","id":pmid,"retmode":"json","tool":"refassist","email":"you@example.com"})
            res = d2.get("result", {}).get(pmid)
            if res: self._cache_set(key, res)
            return res
        except Exception: return None
