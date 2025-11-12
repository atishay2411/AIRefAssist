# from __future__ import annotations

# import os
# from dataclasses import dataclass
# from typing import List, Dict, Any, Optional
# from langchain_text_splitters import RecursiveCharacterTextSplitter
# from langchain_chroma import Chroma
# from langchain_community.document_loaders import PyPDFLoader, TextLoader
# from langchain_openai import AzureOpenAIEmbeddings


# @dataclass
# class StyleGuideConfig:
#     style_guide_path: str
#     persist_dir: str = ".chroma_ieee_style"
#     collection_name: str = "ieee_style"
#     chunk_size: int = 1200
#     chunk_overlap: int = 150
#     top_k: int = 6

#     @staticmethod
#     def from_env() -> "StyleGuideConfig":
#         return StyleGuideConfig(
#             style_guide_path=os.getenv("IEEE_STYLE_GUIDE_PATH", ""),
#             persist_dir=os.getenv("IEEE_STYLE_PERSIST_DIR", ".chroma_ieee_style"),
#             collection_name=os.getenv("IEEE_STYLE_COLLECTION", "ieee_style"),
#             chunk_size=int(os.getenv("IEEE_STYLE_CHUNK_SIZE", "1200")),
#             chunk_overlap=int(os.getenv("IEEE_STYLE_CHUNK_OVERLAP", "150")),
#             top_k=int(os.getenv("IEEE_STYLE_TOP_K", "6")),
#         )

# class StyleGuideRAGService:
#     """
#     Azure-based style-guide retriever, using latest LangChain community modules.
#     """

#     def __init__(self, cfg: StyleGuideConfig):
#         if not cfg.style_guide_path:
#             raise ValueError("STYLE: IEEE_STYLE_GUIDE_PATH not set.")
#         self.cfg = cfg
#         self._embeddings = AzureOpenAIEmbeddings(
#             azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
#             api_key=os.getenv("AZURE_OPENAI_API_KEY"),
#             azure_deployment=os.getenv("AZURE_EMBEDDINGS_DEPLOYMENT"),
#             openai_api_version="2024-02-01"
#         )
#         self._vectorstore = self._init_vectorstore()

#     def _load_documents(self):
#         path = self.cfg.style_guide_path
#         if path.lower().endswith(".pdf"):
#             return PyPDFLoader(path).load()
#         else:
#             return TextLoader(path, encoding="utf-8").load()

#     def _split_documents(self, docs):
#         splitter = RecursiveCharacterTextSplitter(
#             chunk_size=self.cfg.chunk_size,
#             chunk_overlap=self.cfg.chunk_overlap,
#             separators=["\n\n","Section ","SECTION ","\n"]
#         )
#         return splitter.split_documents(docs)

#     def _init_vectorstore(self):
#         vs = Chroma(
#             collection_name=self.cfg.collection_name,
#             embedding_function=self._embeddings,
#             persist_directory=self.cfg.persist_dir,
#         )
#         if vs._collection.count() == 0:
#             docs = self._load_documents()
#             chunks = self._split_documents(docs)
#             vs = Chroma.from_documents(
#                 documents=chunks,
#                 embedding=self._embeddings,
#                 collection_name=self.cfg.collection_name,
#                 persist_directory=self.cfg.persist_dir,
#             )
#             vs.persist()
#         return vs

#     def retrieve_snippets(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
#         """
#         Retrieves top-k IEEE style guide snippets for a query.
#         Compatible with both pre-1.0 and 1.x LangChain retriever APIs.
#         """
#         k = top_k or self.cfg.top_k
#         retriever = self._vectorstore.as_retriever(search_kwargs={"k": k})
#         try:
#             results = retriever.invoke(query)
#         except AttributeError:
#             results = retriever.get_relevant_documents(query)
#         return [
#             {"rank": i + 1, "metadata": dict(d.metadata or {}), "text": d.page_content.strip()}
#             for i, d in enumerate(results)
#         ]

# def build_query_from_state(state: Dict[str, Any]) -> str:
#     ex = state.get("extracted", {}) or {}
#     rtype = (state.get("type") or "other").lower()
#     parts = [f"IEEE reference rules for type: {rtype}"]
#     if ex:
#         fields = ", ".join(f"{k}: {v}" for k,v in ex.items() if v)
#         parts.append(fields)
#     return " | ".join(parts)


from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_openai import AzureOpenAIEmbeddings


# class StyleGuideConfig:
#     def __init__(self, style_guide_path, persist_dir, collection_name, chunk_size, chunk_overlap, top_k):
#         self.style_guide_path = style_guide_path
#         self.persist_dir = persist_dir
#         self.collection_name = collection_name
#         self.chunk_size = chunk_size
#         self.chunk_overlap = chunk_overlap
#         self.top_k = top_k

#     def __repr__(self):
#         return f"StyleGuideConfig(style_guide_path={self.style_guide_path!r}, ...)"

@dataclass
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
            style_guide_path=os.getenv("IEEE_STYLE_GUIDE_PATH", ""),
            persist_dir=os.getenv("IEEE_STYLE_PERSIST_DIR", ".chroma_ieee_style"),
            collection_name=os.getenv("IEEE_STYLE_COLLECTION", "ieee_style"),
            chunk_size=int(os.getenv("IEEE_STYLE_CHUNK_SIZE", "1200")),
            chunk_overlap=int(os.getenv("IEEE_STYLE_CHUNK_OVERLAP", "150")),
            top_k=int(os.getenv("IEEE_STYLE_TOP_K", "6")),
        )


class StyleGuideRAGService:
    """
    Azure-based style guide retriever using LangChain and Chroma vector store.
    Retrieves IEEE style rules from a local or PDF-based guide and prints
    source + page + snippet in the terminal.
    """

    def __init__(self, cfg: StyleGuideConfig):
        if not cfg.style_guide_path:
            raise ValueError("STYLE: IEEE_STYLE_GUIDE_PATH not set.")
        self.cfg = cfg

        # Initialize embedding model
        self._embeddings = AzureOpenAIEmbeddings(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_deployment=os.getenv("AZURE_EMBEDDINGS_DEPLOYMENT"),
            openai_api_version="2024-02-01",
        )

        # Initialize or rebuild Chroma vectorstore
        self._vectorstore = self._init_vectorstore()

    def _load_documents(self):
        """Load style guide from PDF or text."""
        path = self.cfg.style_guide_path
        if path.lower().endswith(".pdf"):
            return PyPDFLoader(path).load()
        else:
            return TextLoader(path, encoding="utf-8").load()

    def _split_documents(self, docs):
        """Split documents into overlapping chunks."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.cfg.chunk_size,
            chunk_overlap=self.cfg.chunk_overlap,
            separators=["\n\n", "Section ", "SECTION ", "\n"],
        )
        return splitter.split_documents(docs)

    def _init_vectorstore(self):
        """Initialize Chroma vectorstore and persist if empty."""
        vs = Chroma(
            collection_name=self.cfg.collection_name,
            embedding_function=self._embeddings,
            persist_directory=self.cfg.persist_dir,
        )

        if vs._collection.count() == 0:
            print("[STYLE-RAG] Building vectorstore for style guide...")
            docs = self._load_documents()
            chunks = self._split_documents(docs)
            vs = Chroma.from_documents(
                documents=chunks,
                embedding=self._embeddings,
                collection_name=self.cfg.collection_name,
                persist_directory=self.cfg.persist_dir,
            )
            vs.persist()
            print(f"[STYLE-RAG] Vectorstore built and persisted at {self.cfg.persist_dir}")
        else:
            print(f"[STYLE-RAG] Loaded existing vectorstore from {self.cfg.persist_dir}")
        return vs

    def retrieve_snippets(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves top-k IEEE style guide snippets for a query.
        Prints source, page, and snippet text to terminal.
        """
        k = top_k or self.cfg.top_k
        retriever = self._vectorstore.as_retriever(search_kwargs={"k": k})

        try:
            results = retriever.invoke(query)
        except AttributeError:
            results = retriever.get_relevant_documents(query)

        formatted_results = []
        print("\n=== Retrieved IEEE Style Guide Snippets ===")
        for i, d in enumerate(results):
            meta = dict(d.metadata or {})
            source = meta.get("source", "unknown")
            page = meta.get("page", "N/A")
            text = d.page_content.strip()

            print(f"[{i + 1}] Source: {os.path.basename(source)} | Page: {page}")
            print(f"    {text[:250]}{'...' if len(text) > 250 else ''}\n")

            formatted_results.append({
                "rank": i + 1,
                "source": source,
                "page": page,
                "text": text,
            })

        print("===========================================\n")
        return formatted_results


def build_query_from_state(state: Dict[str, Any]) -> str:
    """
    Builds a descriptive query string for the RAG retriever from citation context.
    """
    ex = state.get("extracted", {}) or {}
    rtype = (state.get("type") or "other").lower()
    parts = [f"IEEE reference rules for type: {rtype}"]
    if ex:
        fields = ", ".join(f"{k}: {v}" for k, v in ex.items() if v)
        parts.append(fields)
    return " | ".join(parts)


if __name__ == "__main__":
    cfg = StyleGuideConfig.from_env()
    svc = StyleGuideRAGService(cfg)
    query = "How to cite a book in IEEE reference style"
    svc.retrieve_snippets(query)
