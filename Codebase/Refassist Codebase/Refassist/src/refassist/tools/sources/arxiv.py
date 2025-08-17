import re
from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import normalize_text, DEFAULT_UA

class ArxivClient(SourceClient):
    NAME = "arxiv"; BASE_URL = "https://export.arXiv.org/api/query"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def by_title(self, title: str) -> Optional[Dict[str, Any]]:
        try:
            if self.client is None: return None
            async with self.limiter:
                r = await self.client.get(self.BASE_URL, params={"search_query": f"ti:\"{title}\"", "start":0, "max_results":1}, headers={"Accept":"application/atom+xml","User-Agent":DEFAULT_UA})
                r.raise_for_status()
                xml = r.text
                tmatch = re.search(r"<title>(.*?)</title>", xml, flags=re.DOTALL)
                if not tmatch: return None
                title0 = normalize_text(re.sub(r"\s+", " ", tmatch.group(1)))
                auths = [normalize_text(a) for a in re.findall(r"<name>(.*?)</name>", xml)]
                ymatch = re.search(r"<published>(\d{4})-", xml)
                year0 = ymatch.group(1) if ymatch else ""
                return {"title": title0, "authors": auths, "journal_name":"arXiv", "year": year0, "doi":""}
        except Exception:
            return None

    async def by_id(self, arx: str) -> Optional[Dict[str, Any]]:
        try:
            if self.client is None: return None
            async with self.limiter:
                r = await self.client.get(self.BASE_URL, params={"id_list": arx}, headers={"Accept":"application/atom+xml","User-Agent":DEFAULT_UA})
                r.raise_for_status()
                xml = r.text
                tmatch = re.search(r"<title>(.*?)</title>", xml, flags=re.DOTALL)
                if not tmatch: return None
                title0 = normalize_text(re.sub(r"\s+", " ", tmatch.group(1)))
                auths = [normalize_text(a) for a in re.findall(r"<name>(.*?)</name>", xml)]
                ymatch = re.search(r"<published>(\d{4})-", xml)
                year0 = ymatch.group(1) if ymatch else ""
                return {"title": title0, "authors": auths, "journal_name":"arXiv", "year": year0, "doi":""}
        except Exception:
            return None
