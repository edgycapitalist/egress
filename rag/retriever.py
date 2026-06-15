"""Local and Vertex Search retrieval for grounded analyst/critic context."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class CorpusSnippet(BaseModel):
    source: str
    title: str
    text: str
    score: float = 0.0


class Retriever(Protocol):
    name: str

    def retrieve(self, query: str, *, limit: int = 4) -> list[CorpusSnippet]:
        """Return source-labelled snippets relevant to ``query``."""


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if len(tok) > 2}


def _title_and_body(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        return lines[0].lstrip("#").strip(), text
    return path.stem.replace("_", " ").title(), text


class LocalCorpusRetriever:
    """Dependency-free lexical retrieval over committed ``docs/corpus`` files."""

    name = "local_bm25_light"

    def __init__(self, corpus_dir: str | Path = "docs/corpus") -> None:
        self.corpus_dir = Path(corpus_dir)

    def retrieve(self, query: str, *, limit: int = 4) -> list[CorpusSnippet]:
        query_terms = _tokens(query)
        if not query_terms or not self.corpus_dir.exists():
            return []
        snippets: list[CorpusSnippet] = []
        for path in sorted(self.corpus_dir.glob("*.md")):
            title, body = _title_and_body(path)
            terms = _tokens(body)
            overlap = query_terms & terms
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_terms))
            text = _best_excerpt(body, overlap)
            snippets.append(
                CorpusSnippet(source=str(path), title=title, text=text, score=round(score, 4))
            )
        return sorted(snippets, key=lambda snippet: snippet.score, reverse=True)[:limit]


def _best_excerpt(body: str, overlap: set[str], *, max_chars: int = 520) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if not paragraphs:
        return body[:max_chars]
    best = max(
        paragraphs,
        key=lambda paragraph: len(_tokens(paragraph) & overlap),
    )
    if len(best) <= max_chars:
        return best
    return best[: max_chars - 1].rsplit(" ", 1)[0] + "."


class VertexSearchRetriever:
    """Vertex AI Search / Discovery Engine adapter.

    Kept dynamic so local tests never require the cloud client. If the configured
    SDK surface is unavailable, callers receive a clear runtime error and can
    fall back to local retrieval.
    """

    name = "vertex_search"

    def __init__(
        self,
        *,
        datastore_id: str,
        location: str = "global",
        collection: str = "default_collection",
        project: str | None = None,
    ) -> None:
        self.datastore_id = datastore_id
        self.location = location
        self.collection = collection
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT", "")

    def retrieve(self, query: str, *, limit: int = 4) -> list[CorpusSnippet]:
        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
        except ImportError as exc:  # pragma: no cover - cloud only
            raise RuntimeError(
                "google-cloud-discoveryengine is required for Vertex Search"
            ) from exc
        if not self.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex Search")
        client = discoveryengine.SearchServiceClient()
        serving_config = client.serving_config_path(
            project=self.project,
            location=self.location,
            data_store=self.datastore_id,
            serving_config="default_config",
        )
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=limit,
        )
        response = client.search(request)
        snippets: list[CorpusSnippet] = []
        for result in response.results:
            document = result.document
            data: dict[str, Any] = dict(document.derived_struct_data or {})
            title = str(data.get("title") or document.name.rsplit("/", 1)[-1])
            text = str(data.get("snippet") or data.get("extractive_answers") or "")[:700]
            snippets.append(
                CorpusSnippet(source=document.name, title=title, text=text, score=1.0)
            )
        return snippets


def build_retriever() -> Retriever:
    datastore_id = os.getenv("VERTEX_SEARCH_DATASTORE_ID", "").strip()
    if datastore_id:
        return VertexSearchRetriever(
            datastore_id=datastore_id,
            location=os.getenv("VERTEX_SEARCH_LOCATION", "global"),
            collection=os.getenv("VERTEX_SEARCH_COLLECTION", "default_collection"),
        )
    return LocalCorpusRetriever(os.getenv("EGRESS_RAG_CORPUS_DIR", "docs/corpus"))


def retrieve_context(query: str, *, limit: int = 4) -> dict[str, Any]:
    retriever = build_retriever()
    try:
        snippets = retriever.retrieve(query, limit=limit)
        return {
            "backend": retriever.name,
            "snippets": [snippet.model_dump() for snippet in snippets],
        }
    except Exception as exc:
        fallback = LocalCorpusRetriever(os.getenv("EGRESS_RAG_CORPUS_DIR", "docs/corpus"))
        snippets = fallback.retrieve(query, limit=limit)
        return {
            "backend": f"{retriever.name}_fallback_local",
            "error": exc.__class__.__name__,
            "snippets": [snippet.model_dump() for snippet in snippets],
        }


def format_snippets(context: dict[str, Any] | list[CorpusSnippet]) -> str:
    raw_snippets: list[Any]
    backend = "local"
    if isinstance(context, dict):
        raw_snippets = list(context.get("snippets") or [])
        backend = str(context.get("backend") or backend)
    else:
        raw_snippets = list(context)
    snippets = [
        item if isinstance(item, CorpusSnippet) else CorpusSnippet.model_validate(item)
        for item in raw_snippets
    ]
    if not snippets:
        return "No retrieval snippets available."
    lines = [f"Retrieval backend: {backend}."]
    for idx, snippet in enumerate(snippets, start=1):
        lines.append(
            f"[{idx}] {snippet.title} ({snippet.source}, score={snippet.score}): {snippet.text}"
        )
    return "\n".join(lines)
