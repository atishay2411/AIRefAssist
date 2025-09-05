import asyncio
from cachetools import TTLCache
from ..config import PipelineConfig
from ..llms import LLMAdapter
from ..logging import logger
try:
    import httpx
except Exception:
    httpx = None

from ..state import PipelineState
from ..tools.sources import (
    CrossrefClient, OpenAlexClient, SemanticScholarClient, PubMedClient, ArxivClient,
    IEEEXploreClient,  # NEW
)

# ------------------------------
# Shared resources (singleton-ish)
# ------------------------------
_SHARED_HTTP = None         # httpx.AsyncClient
_SHARED_CACHE = None        # TTLCache
_SHARED_LIMITER = None      # asyncio.Semaphore

def _get_shared_resources(cfg: PipelineConfig):
    global _SHARED_HTTP, _SHARED_CACHE, _SHARED_LIMITER
    if _SHARED_CACHE is None:
        _SHARED_CACHE = TTLCache(maxsize=1000, ttl=cfg.cache_ttl_s)
    if _SHARED_LIMITER is None:
        _SHARED_LIMITER = asyncio.Semaphore(cfg.concurrency)
    if _SHARED_HTTP is None and httpx is not None:
        _SHARED_HTTP = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=cfg.timeout_s,
                read=cfg.timeout_s,
                write=cfg.timeout_s,
                pool=cfg.timeout_s
            )
        )
    return _SHARED_HTTP, _SHARED_CACHE, _SHARED_LIMITER

async def init_runtime(state: PipelineState) -> PipelineState:
    cfg = state.get("_cfg") or PipelineConfig()
    llm = LLMAdapter(cfg)

    # Obtain (or create) shared async HTTP client, cache, limiter
    http, cache, limiter = _get_shared_resources(cfg)

    sources = [
        # Order matters: earlier sources have higher authority weight in consensus
        CrossrefClient(cfg, client=http, limiter=limiter, cache=cache),          # DOI registry (authoritative)
        IEEEXploreClient(cfg, client=http, limiter=limiter, cache=cache),        # NEW: IEEE venue authority
        OpenAlexClient(cfg, client=http, limiter=limiter, cache=cache),
        SemanticScholarClient(cfg, client=http, limiter=limiter, cache=cache),
        PubMedClient(cfg, client=http, limiter=limiter, cache=cache),
        ArxivClient(cfg, client=http, limiter=limiter, cache=cache),
    ]

    # _owns_http=False because we are using a shared client; cleanup must not close it
    state.update({
        "_cfg": cfg,
        "_llm": llm,
        "_http": http,
        "_owns_http": False,
        "_cache": cache,
        "_limiter": limiter,
        "_sources": sources,
        "hops": state.get("hops", 0),
        "attempts": state.get("attempts", 0),
        "_ver_score": state.get("_ver_score", -1),
        "_stagnation": state.get("_stagnation", 0),
        "_fp": state.get("_fp", ""),
        "_fp_history": state.get("_fp_history", set()),
        "_loop_detected": False,
        "_made_changes_last_cycle": False,
        # NEW KEYS for reference verification
        "_skip_pipeline": False,
        "verification_message": "",
    })
    return state
