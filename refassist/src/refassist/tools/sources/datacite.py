from typing import Any, Dict, Optional
from ..http import SourceClient
from ..utils import DEFAULT_UA


class DataCiteClient(SourceClient):
    """
    DataCite DOI registry (keyless) — datasets and software (Zenodo's
    10.5281/…, figshare, Dryad, …) are registered here, not with Crossref,
    so Crossref-only DOI lookups silently fail for that whole category.
    """
    NAME = "datacite"
    BASE_URL = "https://api.datacite.org/dois"

    async def by_title(self, title: str, author: Optional[str] = None):
        return None  # DOI registry lookup only; title search adds noise

    async def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        key = f"doi:{doi.lower().strip()}"
        if (c := self._cache_get(key)) is not None:
            return c or None
        try:
            data = await self._get_json(
                f"{self.BASE_URL}/{doi}",
                headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
            )
            attrs = ((data or {}).get("data") or {}).get("attributes") or {}
            self._cache_set(key, attrs)
            return attrs or None
        except Exception:
            # Most DOIs are Crossref-registered → DataCite 404s are the norm
            self._cache_set(key, {})
            return None
