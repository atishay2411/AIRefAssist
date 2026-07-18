from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import is_plausible_year
from ...logging import logger

class CrossrefClient(SourceClient):
    NAME = "crossref"
    BASE = "https://api.crossref.org/works"

    _SELECT = (
        "title,author,container-title,short-container-title,"
        "issued,DOI,page,volume,issue,published-print,published-online,"
        "created,deposited,type,updated-by"
    )

    # rows=8: Crossref search ranking varies between backend replicas under
    # load; a wider net keeps the right record in the candidate set.
    async def by_title(self, title: str, rows: int = 8, author: str | None = None) -> List[Dict[str, Any]]:
        cache_key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(cache_key)) is not None:
            return c
        params = {
            "query.title": title,
            "rows": rows,
            "select": self._SELECT,
        }
        if author:
            # Ranking hint: title-only search often buries the right record
            # for common titles ("Deep Learning", ...).
            params["query.author"] = author
        data = await self._get_json(self.BASE, params=params)
        items = (data or {}).get("message", {}).get("items", []) or []
        out = []
        for it in items:
            # Best-effort year extraction here so logs show something meaningful
            y = None
            for key in ("published-print", "published-online", "issued", "created", "deposited"):
                dp = (it.get(key) or {}).get("date-parts") or []
                if dp and dp[0]:
                    y = str(dp[0][0]); break
            y = y if is_plausible_year(y or "") else ""
            t = (it.get("title") or [""])[0] if it.get("title") else ""
            typ = it.get("type")
            if not y:
                logger.debug("[CrossrefClient] No plausible year for title='%s' type=%s", t[:60], typ)
            out.append(it)
        self._cache_set(cache_key, out)
        return out

    async def by_biblio(self, citation: str, rows: int = 5) -> List[Dict[str, Any]]:
        """
        Citation-matching search over the full raw reference string.
        query.bibliographic scores against ALL tokens (authors, venue, volume,
        pages, year), so it finds the right record even when the title has a
        typo that a title-only search cannot match.
        """
        cache_key = f"biblio:{citation.lower()[:300]}"
        if (c := self._cache_get(cache_key)) is not None:
            return c
        params = {
            "query.bibliographic": citation,
            "rows": rows,
            "select": self._SELECT,
        }
        data = await self._get_json(self.BASE, params=params)
        out = (data or {}).get("message", {}).get("items", []) or []
        self._cache_set(cache_key, out)
        return out

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        cache_key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(cache_key)) is not None:
            return c
        url = f"{self.BASE}/{doi}"
        # NOTE: no `select` here — the /works/{doi} route rejects it with a 400.
        # The full record also carries `updated-by`, which flags retractions.
        data = await self._get_json(url)
        msg = (data or {}).get("message") or None
        if msg:
            self._cache_set(cache_key, msg)
        return msg
