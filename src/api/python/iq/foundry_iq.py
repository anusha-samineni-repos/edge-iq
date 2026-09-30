"""
Foundry IQ - the knowledge brain.

Grounds every Edge IQ answer in governed, citable institutional knowledge:
SOPs, O&M manuals, regulatory texts (SDWA / EPA / DWI), vendor bulletins and
incident post-mortems. Backed by an Azure AI Search hybrid (vector + BM25 +
semantic reranker) index built by infra/scripts/seed/seed-search-index.

Design notes
------------
* Retrieval is *governed*: every chunk carries ``classification`` and
  ``effective_date`` so the orchestrator can refuse stale or restricted content.
* Results are returned as ``KnowledgeChunk`` objects carrying a stable
  ``citation_id``; the orchestrator renders these as [1], [2], ... in answers.
* When Search is unreachable or unconfigured, this falls back to the bundled
  local corpus under ``documents/knowledge-base`` so demos always work.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import FoundryIQSettings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]

_STOPWORDS = {
    "the", "and", "for", "what", "which", "who", "why", "how", "when", "where", "are", "was",
    "were", "with", "that", "this", "there", "their", "from", "into", "about", "any", "all",
    "has", "have", "had", "can", "could", "should", "would", "will", "does", "did", "our",
    "you", "your", "its", "not", "but", "yet", "been", "being", "next", "last", "days",
    "day", "hours", "week", "today", "now", "show", "tell", "give", "list", "please", "is",
    "of", "in", "on", "a", "an", "to", "me", "my", "we", "us", "it", "be", "do",
}
_LOCAL_CORPUS = _REPO_ROOT / "documents" / "knowledge-base"


@dataclass
class KnowledgeChunk:
    citation_id: str
    title: str
    content: str
    source: str
    doc_type: str = "sop"
    classification: str = "internal"
    effective_date: str = ""
    score: float = 0.0
    asset_classes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class FoundryIQ:
    """Knowledge retrieval and grounding for the Edge IQ orchestrator."""

    def __init__(self, settings: FoundryIQSettings, credential=None):
        self.settings = settings
        self._credential = credential
        self._client = None
        self._local_cache: list[KnowledgeChunk] | None = None

    # ------------------------------------------------------------------ client
    async def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.settings.search_endpoint:
            return None
        try:
            from azure.search.documents.aio import SearchClient

            self._client = SearchClient(
                endpoint=self.settings.search_endpoint,
                index_name=self.settings.search_index,
                credential=self._credential,
            )
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Foundry IQ search client unavailable, using local corpus: %s", exc)
            self._client = None
        return self._client

    # --------------------------------------------------------------- retrieval
    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        doc_types: list[str] | None = None,
        asset_class: str | None = None,
        max_classification: str = "internal",
    ) -> list[KnowledgeChunk]:
        """
        Hybrid-retrieve knowledge chunks for *query*.

        ``max_classification`` enforces the governance boundary: a chunk whose
        classification ranks above the caller's clearance is never returned.
        """
        top_k = top_k or self.settings.top_k
        client = await self._get_client()
        if client is None:
            return self._retrieve_local(query, top_k, doc_types, asset_class, max_classification)

        filters: list[str] = []
        if doc_types:
            quoted = ",".join(doc_types)
            filters.append(f"search.in(docType, '{quoted}', ',')")
        if asset_class:
            filters.append(f"assetClasses/any(a: a eq '{asset_class}')")
        filters.append(f"search.in(classification, '{_allowed_classifications(max_classification)}', ',')")

        try:
            from azure.search.documents.models import VectorizableTextQuery

            results = await client.search(
                search_text=query,
                filter=" and ".join(filters) if filters else None,
                vector_queries=[
                    VectorizableTextQuery(text=query, k_nearest_neighbors=top_k * 3, fields="contentVector")
                ],
                query_type="semantic",
                semantic_configuration_name=self.settings.search_semantic_config,
                top=top_k,
                select=[
                    "id",
                    "title",
                    "content",
                    "source",
                    "docType",
                    "classification",
                    "effectiveDate",
                    "assetClasses",
                ],
            )
            chunks: list[KnowledgeChunk] = []
            async for doc in results:
                reranker = doc.get("@search.reranker_score") or 0.0
                if reranker and reranker < self.settings.reranker_threshold:
                    continue
                chunks.append(
                    KnowledgeChunk(
                        citation_id=doc.get("id", ""),
                        title=doc.get("title", ""),
                        content=doc.get("content", ""),
                        source=doc.get("source", ""),
                        doc_type=doc.get("docType", "sop"),
                        classification=doc.get("classification", "internal"),
                        effective_date=doc.get("effectiveDate", ""),
                        score=reranker or doc.get("@search.score", 0.0),
                        asset_classes=doc.get("assetClasses", []) or [],
                    )
                )
            return chunks
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("Foundry IQ retrieval failed (%s); falling back to local corpus", exc)
            return self._retrieve_local(query, top_k, doc_types, asset_class, max_classification)

    # ----------------------------------------------------------- local fallback
    def _load_local(self) -> list[KnowledgeChunk]:
        if self._local_cache is not None:
            return self._local_cache
        chunks: list[KnowledgeChunk] = []
        if _LOCAL_CORPUS.exists():
            for path in sorted(_LOCAL_CORPUS.rglob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                for item in payload if isinstance(payload, list) else [payload]:
                    chunks.append(
                        KnowledgeChunk(
                            citation_id=item.get("id", path.stem),
                            title=item.get("title", path.stem),
                            content=item.get("content", ""),
                            source=item.get("source", str(path.relative_to(_REPO_ROOT))),
                            doc_type=item.get("docType", "sop"),
                            classification=item.get("classification", "internal"),
                            effective_date=item.get("effectiveDate", ""),
                            asset_classes=item.get("assetClasses", []) or [],
                        )
                    )
        self._local_cache = chunks
        return chunks

    def _retrieve_local(
        self,
        query: str,
        top_k: int,
        doc_types: list[str] | None,
        asset_class: str | None,
        max_classification: str,
    ) -> list[KnowledgeChunk]:
        allowed = set(_allowed_classifications(max_classification).split(","))
        self.last_backend = "local"
        terms = [
            t for t in re.split(r"\W+", query.lower())
            if len(t) > 2 and t not in _STOPWORDS
        ]
        if not terms:
            return []
        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self._load_local():
            if chunk.classification not in allowed:
                continue
            if doc_types and chunk.doc_type not in doc_types:
                continue
            if asset_class and chunk.asset_classes and asset_class not in chunk.asset_classes:
                continue
            haystack = f"{chunk.title} {chunk.content}".lower()
            matched_terms = [term for term in terms if term in haystack]
            # Require at least two distinct query terms (or one for short queries) to count as relevant.
            if len(matched_terms) < min(2, len(terms)):
                continue
            score = sum(haystack.count(term) for term in matched_terms)
            # Title hits are worth far more than body hits.
            score += 5 * sum(1 for term in terms if term in chunk.title.lower())
            if score:
                scored.append((float(score), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored:
            # Drop weak tail matches relative to the best hit.
            floor = scored[0][0] * 0.25
            scored = [pair for pair in scored if pair[0] >= floor]
        out = []
        for score, chunk in scored[:top_k]:
            chunk.score = score
            out.append(chunk)
        return out

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


_CLASSIFICATION_ORDER = ["public", "internal", "confidential", "restricted"]


def _allowed_classifications(max_classification: str) -> str:
    try:
        ceiling = _CLASSIFICATION_ORDER.index(max_classification)
    except ValueError:
        ceiling = 1
    return ",".join(_CLASSIFICATION_ORDER[: ceiling + 1])
