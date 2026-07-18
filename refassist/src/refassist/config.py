# src/refassist/config.py
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, find_dotenv

from .logging import logger

# ---------------------------------------------------------
# .env loading — look next to the project root, then fall back
# to the standard upward search. Never crash on a missing file.
# ---------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../Refassist
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
    logger.debug("[config] Loaded .env from %s", _ENV_FILE)
else:
    load_dotenv(find_dotenv())


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        logger.warning("[config] Invalid float for %s; using default %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        logger.warning("[config] Invalid int for %s; using default %s", name, default)
        return default


@dataclass
class PipelineConfig:
    """Central runtime configuration for the RefAssist pipeline.

    All fields use default_factory so environment variables are read when the
    config is instantiated, not once at import time.
    """

    # Pipeline settings
    timeout_s: float = field(default_factory=lambda: _env_float("IEEE_REF_TIMEOUT", 12.0))
    # Hard wall-clock cap per source query (incl. retries) — one slow source
    # must not stall the whole parallel lookup.
    source_timeout_s: float = field(default_factory=lambda: _env_float("IEEE_REF_SOURCE_TIMEOUT", 8.0))
    concurrency: int = field(default_factory=lambda: _env_int("IEEE_REF_CONCURRENCY", 16))
    cache_ttl_s: int = field(default_factory=lambda: _env_int("IEEE_REF_CACHE_TTL", 3600))
    max_correction_rounds: int = field(default_factory=lambda: _env_int("IEEE_REF_MAX_CORR", 3))
    max_hops: int = field(default_factory=lambda: _env_int("IEEE_REF_MAX_HOPS", 12))
    stagnation_patience: int = field(default_factory=lambda: _env_int("IEEE_REF_STAGNATION", 2))
    recursion_limit: int = field(default_factory=lambda: _env_int("IEEE_REF_RECURSION_LIMIT", 60))

    # LLM provider ("auto" picks the first configured provider)
    llm_provider: str = field(default_factory=lambda: os.getenv("IEEE_REF_LLM", "auto").lower())

    # Model settings
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2"))
    ollama_base: str = field(default_factory=lambda: os.getenv(
        "OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")))

    # Azure OpenAI (for LLM + embeddings + RAG)
    azure_endpoint: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT"))
    azure_api_key: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY"))
    azure_api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"))
    azure_chat_deployment: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_CHAT_DEPLOYMENT"))
    azure_embed_deployment: Optional[str] = field(default_factory=lambda: os.getenv("AZURE_EMBEDDING_DEPLOYMENT"))

    # RAG PDF and index settings
    style_pdf_path: str = field(default_factory=lambda: os.getenv("IEEE_STYLE_GUIDE_PATH", "assets/Style_guide.pdf"))
    style_index_dir: str = field(default_factory=lambda: os.getenv("IEEE_STYLE_PERSIST_DIR", ".chroma_ieee_style"))

    # RAG settings
    retriever_top_k: int = field(default_factory=lambda: _env_int("REFASSIST_RETRIEVER_TOP_K", 4))

    # Temperature — 0.0 for deterministic extraction and formatting
    temperature: float = field(default_factory=lambda: _env_float("REFASSIST_LLM_TEMPERATURE", 0.0))
