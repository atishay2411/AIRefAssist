from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import List, Dict, Any, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader

# Embeddings
from langchain_openai import AzureOpenAIEmbeddings
try:
    from langchain_ollama import OllamaEmbeddings  # current package
except ImportError:
    from langchain_community.embeddings import OllamaEmbeddings  # deprecated fallback

from ..logging import logger

# Bundled IEEE style guide shipped with the package
_DEFAULT_STYLE_GUIDE = Path(__file__).resolve().parents[1] / "assets" / "Style_guide.pdf"


@dataclass(frozen=True)
class StyleGuideConfig:
    style_guide_path: str
    persist_dir: str = ".chroma_ieee_style"
    collection_name: str = "ieee_style"
    chunk_size: int = 1200
    chunk_overlap: int = 150
    top_k: int = 6

    @staticmethod
    def from_env() -> "StyleGuideConfig":
        return StyleGuideConfig(
            style_guide_path=os.getenv("IEEE_STYLE_GUIDE_PATH", str(_DEFAULT_STYLE_GUIDE)),
            persist_dir=os.getenv("IEEE_STYLE_PERSIST_DIR", ".chroma_ieee_style"),
            collection_name=os.getenv("IEEE_STYLE_COLLECTION", "ieee_style"),
            chunk_size=int(os.getenv("IEEE_STYLE_CHUNK_SIZE", "1200")),
            chunk_overlap=int(os.getenv("IEEE_STYLE_CHUNK_OVERLAP", "150")),
            top_k=int(os.getenv("IEEE_STYLE_TOP_K", "6")),
        )


class StyleGuideRAGService:
    """
    Style guide retrieval system with auto-provider switching.

    Provider logic:
        IEEE_REF_LLM=azure   → AzureOpenAIEmbeddings
        IEEE_REF_LLM=ollama  → OllamaEmbeddings
        IEEE_REF_LLM=auto    → Azure if configured, else Ollama fallback
    """

    def __init__(self, cfg: StyleGuideConfig):
        if not cfg.style_guide_path:
            raise ValueError("STYLE: IEEE_STYLE_GUIDE_PATH not set.")
        if not Path(cfg.style_guide_path).exists():
            raise FileNotFoundError(f"STYLE: style guide not found at {cfg.style_guide_path}")

        self.cfg = cfg
        provider = os.getenv("IEEE_REF_LLM", "auto").lower()

        # Auto mode: prefer Azure if API key + endpoint exist
        if provider == "auto":
            if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
                provider = "azure"
            else:
                provider = "ollama"

        # -------------------------
        # Embedding Model Selection
        # -------------------------
        if provider == "ollama":
            embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

            logger.info("[STYLE-RAG] Using Ollama embeddings → model=%s", embed_model)
            self._embeddings = OllamaEmbeddings(
                model=embed_model,
                base_url=base
            )

        else:
            from ..llms.adapter import azure_embedding_deployment
            deployment = azure_embedding_deployment()
            if not deployment:
                raise ValueError(
                    "Azure embeddings selected but no deployment configured "
                    "(set AZURE_EMBEDDING_DEPLOYMENT)."
                )
            logger.info("[STYLE-RAG] Using Azure OpenAI embeddings (deployment=%s)", deployment)
            self._embeddings = AzureOpenAIEmbeddings(
                azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                azure_deployment=deployment,
                openai_api_version=os.getenv(
                    "AZURE_OPENAI_API_VERSION",
                    os.getenv("OPENAI_API_VERSION", "2024-02-01"),
                ),
            )

        # Initialize persistent vector store
        self._vectorstore = self._init_vectorstore()

    # -------------------------------------------------------
    # Document handling
    # -------------------------------------------------------
    def _load_documents(self):
        path = self.cfg.style_guide_path

        if path.lower().endswith(".pdf"):
            return PyPDFLoader(path).load()

        return TextLoader(path, encoding="utf-8").load()

    def _split_documents(self, docs):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.cfg.chunk_size,
            chunk_overlap=self.cfg.chunk_overlap,
            separators=["\n\n", "Section ", "SECTION ", "\n"],
        )
        return splitter.split_documents(docs)

    # -------------------------------------------------------
    # Vectorstore initialization
    # -------------------------------------------------------
    def _init_vectorstore(self):
        vs = Chroma(
            collection_name=self.cfg.collection_name,
            embedding_function=self._embeddings,
            persist_directory=self.cfg.persist_dir,
        )

        if vs._collection.count() == 0:
            logger.info("[STYLE-RAG] Building vectorstore…")

            docs = self._load_documents()
            chunks = self._split_documents(docs)

            vs = Chroma.from_documents(
                documents=chunks,
                embedding=self._embeddings,
                collection_name=self.cfg.collection_name,
                persist_directory=self.cfg.persist_dir,
            )

            # Older chroma versions require an explicit persist
            if hasattr(vs, "_client") and hasattr(vs._client, "persist"):
                vs._client.persist()

            logger.info("[STYLE-RAG] Vectorstore ready at: %s", self.cfg.persist_dir)
        else:
            logger.debug("[STYLE-RAG] Loaded existing vectorstore from: %s", self.cfg.persist_dir)

        return vs

    # -------------------------------------------------------
    # Retrieval API
    # -------------------------------------------------------
    def retrieve_snippets(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        k = top_k or self.cfg.top_k

        retriever = self._vectorstore.as_retriever(search_kwargs={"k": k})

        try:
            results = retriever.invoke(query)
        except AttributeError:
            results = retriever.get_relevant_documents(query)

        snippets = []
        for i, d in enumerate(results):
            snippets.append({
                "rank": i + 1,
                "source": d.metadata.get("source", "unknown"),
                "page": d.metadata.get("page", "N/A"),
                "text": d.page_content.strip(),
            })

        return snippets


# -------------------------------------------------------
# Shared instances — building embeddings + a Chroma store per
# request is far too expensive; cache one service per config.
# -------------------------------------------------------
_SERVICES: Dict[StyleGuideConfig, StyleGuideRAGService] = {}
_SERVICES_LOCK = Lock()


def get_style_guide_service(cfg: Optional[StyleGuideConfig] = None) -> StyleGuideRAGService:
    cfg = cfg or StyleGuideConfig.from_env()
    with _SERVICES_LOCK:
        svc = _SERVICES.get(cfg)
        if svc is None:
            svc = StyleGuideRAGService(cfg)
            _SERVICES[cfg] = svc
        return svc


def build_query_from_state(state: Dict[str, Any]) -> str:
    ex = state.get("extracted", {}) or {}
    rtype = (state.get("type") or "other").lower()

    parts = [f"IEEE reference rules for type: {rtype}"]

    if ex:
        fields = ", ".join(f"{k}: {v}" for k, v in ex.items() if v)
        parts.append(fields)

    return " | ".join(parts)
