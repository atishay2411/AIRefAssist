# src/refassist/config.py
from dataclasses import dataclass
import os
from dotenv import load_dotenv
from pathlib import Path

# -------------------------
# Load environment variables
# -------------------------
# Try to find .env relative to project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # -> Refassist Codebase/
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    # fallback to load from current working directory
    load_dotenv()
    print(f"[config] ⚠️ .env not found at {env_path}, using default env")

@dataclass
class PipelineConfig:
    """Central runtime configuration for the RefAssist pipeline."""

    # -------------------------
    # Runtime / pipeline tuning
    # -------------------------
    timeout_s: float = float(os.getenv("IEEE_REF_TIMEOUT", "12"))
    concurrency: int = int(os.getenv("IEEE_REF_CONCURRENCY", "8"))
    cache_ttl_s: int = int(os.getenv("IEEE_REF_CACHE_TTL", "3600"))
    max_correction_rounds: int = int(os.getenv("IEEE_REF_MAX_CORR", "3"))
    max_hops: int = int(os.getenv("IEEE_REF_MAX_HOPS", "12"))
    stagnation_patience: int = int(os.getenv("IEEE_REF_STAGNATION", "2"))
    agent_threads: int = int(os.getenv("IEEE_REF_AGENT_THREADS", "6"))
    recursion_limit: int = int(os.getenv("IEEE_REF_RECURSION_LIMIT", "60"))

    # -------------------------
    # LLM / provider settings
    # -------------------------
    llm_provider: str = os.getenv("IEEE_REF_LLM", "azure")  # auto | azure | openai | ollama
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    ollama_base: str = os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434"))

    # Azure OpenAI (for LLM + embeddings + RAG)
    azure_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_api_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
    azure_chat_deployment: str = os.getenv("AZURE_CHAT_DEPLOYMENT", "")
    azure_embed_deployment: str = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")

    # -------------------------
    # Reference-style RAG config
    # -------------------------
    style_pdf_path: str = os.getenv("REFASSIST_STYLE_GUIDE_PDF", "assets/Style_guide.pdf")
    style_index_dir: str = os.getenv("REFASSIST_STYLE_INDEX_DIR", "assets/style_index")
    retriever_top_k: int = int(os.getenv("REFASSIST_RETRIEVER_TOP_K", "4"))

    # -------------------------
    # Model tuning / temperature
    # -------------------------
    temperature: float = float(os.getenv("REFASSIST_LLM_TEMPERATURE", "0.1"))
