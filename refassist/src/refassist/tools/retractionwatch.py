"""Retraction Watch database integration.

The Retraction Watch Database (the authoritative registry of retractions,
expressions of concern, and corrections) is distributed openly by Crossref as
a CSV dataset — there is no per-DOI API. This module keeps a local copy:

  - downloaded in the background (never blocks a user's request),
  - cached on disk and refreshed every REFASSIST_RW_REFRESH_DAYS,
  - indexed in memory by DOI for instant lookups.

Beyond a yes/no retraction flag, it supplies what the metadata signals
(Crossref `updated-by`, OpenAlex `is_retracted`, PubMed pubtype) cannot:
the retraction NATURE, DATE, and REASONS.
"""
import asyncio
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging import logger

RW_DATASET_URL = os.getenv(
    "REFASSIST_RW_DATASET_URL",
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv",
)
RW_CACHE_PATH = Path(os.getenv("REFASSIST_RW_CACHE", ".rw_cache/retraction_watch.csv"))
RW_REFRESH_DAYS = max(1, int(os.getenv("REFASSIST_RW_REFRESH_DAYS", "7") or 7))
_RETRY_BACKOFF_S = 600  # after a failed download, wait before retrying

# When one DOI has several notices, keep the most severe.
_SEVERITY = {"retraction": 3, "expression of concern": 2, "correction": 1}


def _norm_doi(doi: Any) -> str:
    return str(doi or "").strip().lower().removeprefix("doi:").strip()


def _parse_csv(path: Path) -> Dict[str, Dict[str, Any]]:
    """Index the dataset by original-paper DOI (CPU-bound; run in a thread)."""
    csv.field_size_limit(sys.maxsize)
    index: Dict[str, Dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            doi = _norm_doi(row.get("OriginalPaperDOI"))
            if not doi.startswith("10."):
                continue
            nature = (row.get("RetractionNature") or "").strip()
            rec = {
                "nature": nature,
                "date": (row.get("RetractionDate") or "").split(" ")[0],
                "reasons": [r.strip(" +") for r in (row.get("Reason") or "").split(";")
                            if r.strip(" +")],
                "notice_doi": (row.get("RetractionDOI") or "").strip(),
            }
            prev = index.get(doi)
            if prev is None or (_SEVERITY.get(nature.lower(), 0)
                                > _SEVERITY.get(prev["nature"].lower(), 0)):
                index[doi] = rec
    return index


class RetractionWatchDB:
    def __init__(self):
        self._index: Optional[Dict[str, Dict[str, Any]]] = None
        self._load_task: Optional[asyncio.Task] = None
        self._failed_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self._index is not None

    @property
    def count(self) -> int:
        return len(self._index) if self._index is not None else 0

    def lookup(self, doi: Any) -> Optional[Dict[str, Any]]:
        """Instant in-memory lookup. Returns None when the DOI is clean OR the
        dataset isn't loaded yet — callers must treat None as 'no signal',
        never as 'verified clean'. Triggers a background load on first use."""
        if self._index is None:
            self.start_background_load()
            return None
        return self._index.get(_norm_doi(doi))

    def start_background_load(self) -> None:
        if self._index is not None:
            return
        if self._load_task is not None and not self._load_task.done():
            return
        if self._failed_at and time.time() - self._failed_at < _RETRY_BACKOFF_S:
            return
        try:
            self._load_task = asyncio.get_running_loop().create_task(self._load())
        except RuntimeError:
            pass  # no running loop (sync/test context) — next async caller retries

    async def ensure_loaded(self) -> bool:
        """Await a full load (used by tests/CLI warmup, not the request path)."""
        self.start_background_load()
        if self._load_task is not None:
            await self._load_task
        return self.ready

    def _cache_fresh(self) -> bool:
        try:
            age = time.time() - RW_CACHE_PATH.stat().st_mtime
            return age < RW_REFRESH_DAYS * 86400 and RW_CACHE_PATH.stat().st_size > 1024
        except OSError:
            return False

    async def _download(self) -> None:
        import httpx
        RW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = RW_CACHE_PATH.with_suffix(".tmp")
        logger.info("[retractionwatch] Downloading dataset from %s", RW_DATASET_URL)
        async with httpx.AsyncClient(follow_redirects=True, timeout=180.0) as client:
            async with client.stream("GET", RW_DATASET_URL) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
        tmp.replace(RW_CACHE_PATH)  # atomic: readers never see a partial file

    async def _load(self) -> None:
        try:
            if not self._cache_fresh():
                await self._download()
            t0 = time.time()
            self._index = await asyncio.to_thread(_parse_csv, RW_CACHE_PATH)
            logger.info("[retractionwatch] Loaded %d records in %.1fs",
                        len(self._index), time.time() - t0)
        except Exception as e:
            self._failed_at = time.time()
            logger.warning("[retractionwatch] Dataset unavailable (%s) — "
                           "falling back to metadata-only retraction signals", e)


_DB = RetractionWatchDB()


def get_rw_db() -> RetractionWatchDB:
    return _DB
