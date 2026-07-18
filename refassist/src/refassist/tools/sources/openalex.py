from typing import Any, Dict, List, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA, CONTACT_EMAIL

class OpenAlexClient(SourceClient):
    NAME = "openalex"
    BASE_URL = "https://api.openalex.org/works"

    @staticmethod
    def _polite(params: Dict[str, Any]) -> Dict[str, Any]:
        # mailto moves requests into OpenAlex's "polite pool" — anonymous
        # search is aggressively rate-limited.
        if CONTACT_EMAIL:
            params["mailto"] = CONTACT_EMAIL
        return params

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)):
            return c
        try:
            data = await self._get_json(
                self.BASE_URL,
                params=self._polite({"filter": f"doi:{doi}"}),
                headers={"User-Agent": DEFAULT_UA}
            )
            items = data.get("results", [])
            it = items[0] if items else None
            if it:
                self._cache_set(key, it)
            return it
        except Exception:
            return None

    async def by_title(self, title: str, author: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        key = f"title:{title.lower()}|au:{(author or '').lower()}"
        if (c := self._cache_get(key)) is not None:
            return c
        try:
            # NOTE: OpenAlex uses `per_page`, not `per-page`
            flt = f"title.search:{title}"
            if author:
                flt += f",raw_author_name.search:{author}"
            data = await self._get_json(
                self.BASE_URL,
                params=self._polite({"filter": flt, "per_page": 5}),
                headers={"User-Agent": DEFAULT_UA}
            )
            out = (data.get("results") or [])[:5]
            self._cache_set(key, out)
            return out
        except Exception:
            self._cache_set(key, [])  # negative cache: don't re-hit a failing query this run
            return None
