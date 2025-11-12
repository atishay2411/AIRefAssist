from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import is_plausible_year

class CrossrefClient(SourceClient):
    NAME = "crossref"
    BASE = "https://api.crossref.org/works"

    _SELECT = (
        "title,author,container-title,short-container-title,"
        "issued,DOI,page,volume,issue,published-print,published-online,created,deposited,type"
    )

    async def by_title(self, title: str, rows: int = 5) -> List[Dict[str, Any]]:
        params = {
            "query.title": title,
            "rows": rows,
            "select": self._SELECT,
        }
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
            if y:
                print(f"[CrossrefClient] OK: Year={y} Type={typ} for title='{t[:60]}...'")
            else:
                print(f"[CrossrefClient] WARN: No plausible year found for title='{t[:60]}...'")
            out.append(it)
        return out

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        url = f"{self.BASE}/{doi}"
        params = {"select": self._SELECT}
        data = await self._get_json(url, params=params)
        return (data or {}).get("message") or None
