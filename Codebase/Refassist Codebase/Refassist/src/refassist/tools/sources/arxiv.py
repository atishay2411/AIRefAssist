import re
from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import normalize_text, DEFAULT_UA

class ArxivClient(SourceClient):
    NAME = "arxiv"
    # Use canonical host casing and path
    BASE_URL = "https://export.arxiv.org/api/query"

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        return None

    async def by_title(self, title: str) -> Optional[Dict[str, Any]]:
        try:
            if self.client is None:
                return None
            async with self.limiter:
                r = await self.client.get(
                    self.BASE_URL,
                    params={"search_query": f"ti:\"{title}\"", "start": 0, "max_results": 1},
                    headers={"Accept": "application/atom+xml", "User-Agent": DEFAULT_UA},
                )
                r.raise_for_status()
                xml = r.text
                # Titles/authors/published fields are simple enough for a light regex read;
                # feedparser would be more robust, but we avoid adding a new dependency here.
                tmatch = re.search(r"<title>(.*?)</title>", xml, flags=re.DOTALL | re.IGNORECASE)
                if not tmatch:
                    return None
                title0 = normalize_text(re.sub(r"\s+", " ", tmatch.group(1)))
                auths = [normalize_text(a) for a in re.findall(r"<name>(.*?)</name>", xml, flags=re.IGNORECASE)]
                ymatch = re.search(r"<published>(\d{4})-", xml, flags=re.IGNORECASE)
                year0 = ymatch.group(1) if ymatch else ""
                return {"title": title0, "authors": auths, "journal_name": "arXiv", "year": year0, "doi": ""}
        except Exception:
            return None

    async def by_id(self, arx: str) -> Optional[Dict[str, Any]]:
        try:
            if self.client is None:
                return None
            async with self.limiter:
                r = await self.client.get(
                    self.BASE_URL,
                    params={"id_list": arx},
                    headers={"Accept": "application/atom+xml", "User-Agent": DEFAULT_UA},
                )
                r.raise_for_status()
                xml = r.text
                tmatch = re.search(r"<title>(.*?)</title>", xml, flags=re.DOTALL | re.IGNORECASE)
                if not tmatch:
                    return None
                title0 = normalize_text(re.sub(r"\s+", " ", tmatch.group(1)))
                auths = [normalize_text(a) for a in re.findall(r"<name>(.*?)</name>", xml, flags=re.IGNORECASE)]
                ymatch = re.search(r"<published>(\d{4})-", xml, flags=re.IGNORECASE)
                year0 = ymatch.group(1) if ymatch else ""
                return {"title": title0, "authors": auths, "journal_name": "arXiv", "year": year0, "doi": ""}
        except Exception:
            return None
