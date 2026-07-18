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
    IEEEXploreClient, OpenLibraryClient, DBLPClient, DataCiteClient,
    EuropePMCClient, UnpaywallClient, BioRxivClient, DOAJClient, GoogleBooksClient,
)

# ------------------------------
# Shared resources, keyed by the config values they depend on so a
# run_one() call with a different config gets matching resources
# instead of silently reusing the first config's.
# ------------------------------
_HTTP_BY_KEY = {}      # timeout -> httpx.AsyncClient
_CACHE_BY_KEY = {}     # ttl -> TTLCache
_LIMITER_BY_KEY = {}   # concurrency -> asyncio.Semaphore
_LLM_BY_KEY = {}       # provider/model settings -> LLMAdapter

def _get_shared_resources(cfg: PipelineConfig):
    cache = _CACHE_BY_KEY.get(cfg.cache_ttl_s)
    if cache is None:
        cache = _CACHE_BY_KEY[cfg.cache_ttl_s] = TTLCache(maxsize=1000, ttl=cfg.cache_ttl_s)

    limiter = _LIMITER_BY_KEY.get(cfg.concurrency)
    if limiter is None:
        limiter = _LIMITER_BY_KEY[cfg.concurrency] = asyncio.Semaphore(cfg.concurrency)

    http = _HTTP_BY_KEY.get(cfg.timeout_s)
    if http is None and httpx is not None:
        http = _HTTP_BY_KEY[cfg.timeout_s] = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=cfg.timeout_s,
                read=cfg.timeout_s,
                write=cfg.timeout_s,
                pool=cfg.timeout_s
            )
        )
    return http, cache, limiter

def _get_shared_llm(cfg: PipelineConfig) -> LLMAdapter:
    key = (cfg.llm_provider, cfg.openai_model, cfg.anthropic_model,
           cfg.ollama_model, cfg.ollama_base)
    llm = _LLM_BY_KEY.get(key)
    if llm is None:
        llm = _LLM_BY_KEY[key] = LLMAdapter(cfg)
    return llm

async def init_runtime(state: PipelineState) -> PipelineState:
    import time
    state["_started_at"] = time.time()
    cfg = state.get("_cfg") or PipelineConfig()
    llm = _get_shared_llm(cfg)

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
        OpenLibraryClient(cfg, client=http, limiter=limiter, cache=cache),  # books only
        DBLPClient(cfg, client=http, limiter=limiter, cache=cache),         # CS venues
        DataCiteClient(cfg, client=http, limiter=limiter, cache=cache),     # dataset/software DOIs
        EuropePMCClient(cfg, client=http, limiter=limiter, cache=cache),    # biomedical + preprints
        UnpaywallClient(cfg, client=http, limiter=limiter, cache=cache),    # per-DOI + OA links
        BioRxivClient(cfg, client=http, limiter=limiter, cache=cache),      # 10.1101/* preprints
        DOAJClient(cfg, client=http, limiter=limiter, cache=cache),         # OA journals
        GoogleBooksClient(cfg, client=http, limiter=limiter, cache=cache),  # books (key-gated)
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
