from .crossref import CrossrefClient
from .openalex import OpenAlexClient
from .semanticscholar import SemanticScholarClient
from .pubmed import PubMedClient
from .arxiv import ArxivClient
from .ieeexplore import IEEEXploreClient  # NEW

__all__ = [
    "CrossrefClient",
    "OpenAlexClient",
    "SemanticScholarClient",
    "PubMedClient",
    "ArxivClient",
    "IEEEXploreClient",   # NEW
]
