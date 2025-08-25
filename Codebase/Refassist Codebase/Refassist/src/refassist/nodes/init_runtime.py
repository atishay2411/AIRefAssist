import asyncio
from cachetools import TTLCache
from ..config import PipelineConfig
from ..llms import LLMAdapter
from ..logging import logger
try:
    import httpx
except Exception:
    httpx=None

from ..state import PipelineState
from ..tools.sources import CrossrefClient, OpenAlexClient, SemanticScholarClient, PubMedClient, ArxivClient

async def init_runtime(state: PipelineState) -> PipelineState:
    cfg = state.get("_cfg") or PipelineConfig()
    llm = LLMAdapter(cfg)
    http = httpx.AsyncClient(timeout=httpx.Timeout(connect=cfg.timeout_s, read=cfg.timeout_s, write=cfg.timeout_s, pool=cfg.timeout_s)) if httpx is not None else None
    cache = TTLCache(maxsize=1000, ttl=cfg.cache_ttl_s)
    limiter = asyncio.Semaphore(cfg.concurrency)
    sources = [
        CrossrefClient(cfg, client=http, limiter=limiter, cache=cache),
        OpenAlexClient(cfg, client=http, limiter=limiter, cache=cache),
        SemanticScholarClient(cfg, client=http, limiter=limiter, cache=cache),
        PubMedClient(cfg, client=http, limiter=limiter, cache=cache),
        ArxivClient(cfg, client=http, limiter=limiter, cache=cache),
    ]
    state.update({
        "_cfg": cfg,
        "_llm": llm,
        "_http": http,
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

