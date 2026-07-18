# refassist/rag/__init__.py
"""
RAG (Retrieval-Augmented Generation) utilities for IEEE Style Guide retrieval.

This package provides:
- service.py  → Azure-based RAG backend (embeddings + retriever)
- __init__.py → makes `rag` a valid importable subpackage
"""

from .service import (
    StyleGuideConfig,
    StyleGuideRAGService,
    build_query_from_state,
    get_style_guide_service,
)

__all__ = [
    "StyleGuideConfig",
    "StyleGuideRAGService",
    "build_query_from_state",
    "get_style_guide_service",
]
