#!/usr/bin/env python3
"""
Build the Foundry IQ knowledge index in Azure AI Search.

Foundry IQ is the *governed knowledge* brain. It holds procedures, runbooks,
FMEA libraries and regulatory extracts - the documents that turn a raw
measurement into a defensible decision. Two properties matter more than
retrieval quality:

  1. Every chunk carries its `classification`. The retrieval layer filters on
     it (see iq/foundry_iq.py::_allowed_classifications) so a caller entitled
     only to `public` can never be shown a `confidential` runbook, even if it
     is the best semantic match.

  2. Every chunk carries `effectiveDate` and `source`. An answer that cites a
     superseded procedure is worse than no answer, so the date is retrievable
     and rendered in every citation.

Chunking is by heading within a document. Procedures are already written in
discrete numbered steps; splitting on a fixed token window would cut steps in
half and produce citations that point at an instruction fragment.

Usage
-----
    python seed_search_index.py --dry-run       # shape + report, no Azure
    python seed_search_index.py                 # create index + upload
    python seed_search_index.py --recreate      # drop and rebuild the index
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
KB_DIR = REPO_ROOT / "documents" / "knowledge-base"

# Chunk sizing. Procedures are short and written as discrete labelled sections,
# so we never split a section - we only merge adjacent ones up to the ceiling.
# A chunk below TARGET_CHARS lacks the surrounding context needed to act on it
# safely ("ACTION BY ZONE" is useless without "ZONE BOUNDARIES"), so sections
# are coalesced until they reach it.
MAX_CHARS = 2400
TARGET_CHARS = 900
MIN_CHARS = 200


def _coalesce(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge adjacent sections until each chunk reaches TARGET_CHARS.

    Headings are joined with ' / ' so the citation still names every section
    the text came from.
    """
    merged: list[tuple[str, str]] = []
    heads: list[str] = []
    body: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal heads, body, size
        if body:
            merged.append((" / ".join(h for h in heads if h), "\n\n".join(body)))
        heads, body, size = [], [], 0

    for heading, text in sections:
        if size and size + len(text) > MAX_CHARS:
            flush()
        heads.append(heading)
        body.append(text)
        size += len(text) + 2
        if size >= TARGET_CHARS:
            flush()
    flush()
    return merged


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #

_HEADING = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*$", re.MULTILINE)

# The knowledge base is written in operational-procedure style: sections are
# introduced by an ALLCAPS label at the start of a paragraph, e.g.
#   "ZONE BOUNDARIES, GROUP 2 (15 kW to 75 kW), RIGID MOUNTING. A/B 2.3 mm/s..."
# Recognising these gives every chunk a real section name, so a citation reads
# "SOP-ROT-001 - ZONE MEANING" instead of "SOP-ROT-001 - chunk 3".
_CAPS_LABEL = re.compile(
    r"^(?P<label>[A-Z][A-Z0-9 /&()\-,.']{4,80}?)\.\s+(?=[A-Z0-9])",
    re.MULTILINE,
)


def _split_by_caps_label(content: str) -> list[tuple[str, str]]:
    matches = list(_CAPS_LABEL.finditer(content))
    if len(matches) < 2:
        return []
    sections: list[tuple[str, str]] = []
    preamble = content[: matches[0].start()].strip()
    if len(preamble) >= MIN_CHARS:
        sections.append(("", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[m.start():end].strip()
        if body:
            sections.append((m.group("label").strip().rstrip("."), body))
    return sections


def _split_by_heading(content: str) -> list[tuple[str, str]]:
    """Split markdown-ish content into (heading, body) sections.

    Falls back to a single unnamed section when the document has no headings.
    """
    matches = list(_HEADING.finditer(content))
    if not matches:
        # No markdown headings - try the ALLCAPS procedure convention before
        # giving up and treating the whole document as one chunk.
        caps = _split_by_caps_label(content)
        return caps if caps else [("", content.strip())]

    sections: list[tuple[str, str]] = []
    preamble = content[: matches[0].start()].strip()
    if len(preamble) >= MIN_CHARS:
        sections.append(("", preamble))

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if body:
            sections.append((m.group(2).strip(), body))
    return sections


def _pack(heading: str, body: str) -> Iterator[str]:
    """Emit one chunk per section, splitting on blank lines only if oversized.

    Splitting mid-step would produce a citation that points at half an
    instruction, so we only ever split on paragraph boundaries.
    """
    if len(body) <= MAX_CHARS:
        yield body
        return
    buf: list[str] = []
    size = 0
    for para in body.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        if size + len(p) > MAX_CHARS and buf:
            yield "\n\n".join(buf)
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 2
    if buf:
        yield "\n\n".join(buf)


def _doc_id(*parts: str) -> str:
    # Search keys must be URL-safe; document ids contain characters that aren't.
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def build_chunks() -> list[dict[str, Any]]:
    if not KB_DIR.exists():
        raise SystemExit(f"Knowledge base not found at {KB_DIR}")

    chunks: list[dict[str, Any]] = []
    files = sorted(KB_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"No knowledge base JSON files in {KB_DIR}")

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        # KB files may be a bare array of documents or a wrapper object.
        if isinstance(payload, list):
            documents = payload
        else:
            documents = payload.get("documents", [])
        for doc in documents:
            content = doc.get("content", "") or ""
            if not content.strip():
                continue
            sections = _coalesce(_split_by_heading(content))
            ordinal = 0
            for heading, body in sections:
                for piece in _pack(heading, body):
                    if len(piece) < MIN_CHARS and ordinal > 0:
                        continue
                    chunks.append({
                        "chunkId": _doc_id(doc["id"], str(ordinal)),
                        "documentId": doc["id"],
                        "title": doc.get("title", doc["id"]),
                        "heading": heading,
                        "content": piece,
                        "docType": doc.get("docType", "reference"),
                        "classification": doc.get("classification", "internal"),
                        "effectiveDate": doc.get("effectiveDate"),
                        "source": doc.get("source", path.name),
                        "assetClasses": doc.get("assetClasses", []),
                        "useCases": doc.get("useCases", []),
                        "sourceFile": path.name,
                        "ordinal": ordinal,
                    })
                    ordinal += 1
    return chunks


# --------------------------------------------------------------------------- #
# index definition
# --------------------------------------------------------------------------- #

def build_index(name: str, semantic_config: str, vector_dims: int,
                aoai_endpoint: str, embedding_deployment: str) -> Any:
    from azure.search.documents.indexes.models import (  # type: ignore
        AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, HnswAlgorithmConfiguration,
        SearchableField, SearchField, SearchFieldDataType, SearchIndex, SemanticConfiguration,
        SemanticField, SemanticPrioritizedFields, SemanticSearch, SimpleField,
        VectorSearch, VectorSearchProfile,
    )

    fields = [
        SimpleField(name="chunkId", type=SearchFieldDataType.String, key=True),
        SimpleField(name="documentId", type=SearchFieldDataType.String,
                    filterable=True, facetable=True),
        SearchableField(name="title", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="heading", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        # Filterable + facetable so the governance filter is a query-time
        # predicate rather than a post-retrieval scrub. Filtering after the
        # fact would mean confidential text had already left the index.
        SimpleField(name="classification", type=SearchFieldDataType.String,
                    filterable=True, facetable=True),
        SimpleField(name="docType", type=SearchFieldDataType.String,
                    filterable=True, facetable=True),
        SimpleField(name="effectiveDate", type=SearchFieldDataType.String,
                    filterable=True, sortable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="sourceFile", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="ordinal", type=SearchFieldDataType.Int32, sortable=True),
        SearchField(name="assetClasses",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    filterable=True, facetable=True, searchable=True),
        SearchField(name="useCases",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                    filterable=True, facetable=True, searchable=True),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=vector_dims,
            vector_search_profile_name="edgeiq-vector-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="edgeiq-hnsw")],
        profiles=[VectorSearchProfile(
            name="edgeiq-vector-profile",
            algorithm_configuration_name="edgeiq-hnsw",
            vectorizer_name="edgeiq-vectorizer" if aoai_endpoint else None,
        )],
        vectorizers=[AzureOpenAIVectorizer(
            vectorizer_name="edgeiq-vectorizer",
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=aoai_endpoint,
                deployment_name=embedding_deployment,
                model_name=embedding_deployment,
            ),
        )] if aoai_endpoint else None,
    )

    semantic = SemanticSearch(configurations=[SemanticConfiguration(
        name=semantic_config,
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="content")],
            keywords_fields=[SemanticField(field_name="heading"),
                             SemanticField(field_name="docType")],
        ),
    )])

    return SearchIndex(name=name, fields=fields,
                       vector_search=vector_search, semantic_search=semantic)


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Foundry IQ knowledge index.")
    ap.add_argument("--endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT", ""))
    ap.add_argument("--index", default=os.getenv("AZURE_SEARCH_INDEX", "edgeiq-knowledge"))
    ap.add_argument("--semantic-config",
                    default=os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "edgeiq-semantic"))
    ap.add_argument("--aoai-endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    ap.add_argument("--embedding-deployment",
                    default=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
                                      "text-embedding-3-large"))
    ap.add_argument("--vector-dims", type=int, default=3072,
                    help="3072 for text-embedding-3-large, 1536 for -small/ada-002.")
    ap.add_argument("--recreate", action="store_true", help="Delete the index first.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--emit", default="", help="Write chunks to this JSONL path.")
    args = ap.parse_args()

    chunks = build_chunks()

    by_class: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for c in chunks:
        by_class[c["classification"]] = by_class.get(c["classification"], 0) + 1
        by_type[c["docType"]] = by_type.get(c["docType"], 0) + 1

    docs = len({c["documentId"] for c in chunks})
    print("Edge IQ - Foundry IQ knowledge index")
    print(f"  source   {KB_DIR}")
    print(f"  index    {args.index}")
    print(f"  chunks   {len(chunks)} from {docs} documents")
    print(f"  by class {by_class}")
    print(f"  by type  {by_type}")
    avg = sum(len(c["content"]) for c in chunks) / max(len(chunks), 1)
    print(f"  avg size {avg:.0f} chars")

    if "confidential" not in by_class:
        print("  NOTE: no confidential chunks - the governance filter test "
              "in tests/smoke_iq.py expects at least one.")

    if args.emit:
        p = Path(args.emit)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(json.dumps(c, separators=(",", ":")) + "\n")
        print(f"  wrote {p}")

    if args.dry_run:
        print("\nDry run - nothing sent to Azure AI Search.")
        return 0

    if not args.endpoint:
        print(
            "\nNo Search endpoint.\n"
            "  Set AZURE_SEARCH_ENDPOINT or pass --endpoint.\n"
            "  Edge IQ falls back to the local JSON corpus in demo mode, so "
            "this is only needed for a dev/prod deployment."
        )
        return 0

    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.search.documents import SearchClient  # type: ignore
        from azure.search.documents.indexes import SearchIndexClient  # type: ignore
    except ImportError:
        raise SystemExit(
            "azure-search-documents and azure-identity are required.\n"
            "  pip install azure-search-documents azure-identity"
        )

    cred = DefaultAzureCredential()
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=cred)

    if args.recreate:
        try:
            index_client.delete_index(args.index)
            print(f"  deleted existing index '{args.index}'")
        except Exception:
            pass

    index_client.create_or_update_index(build_index(
        args.index, args.semantic_config, args.vector_dims,
        args.aoai_endpoint, args.embedding_deployment,
    ))
    print(f"  index '{args.index}' created/updated")

    search_client = SearchClient(endpoint=args.endpoint, index_name=args.index,
                                 credential=cred)
    BATCH = 100
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        search_client.upload_documents(documents=batch)
        print(f"    uploaded {min(i + BATCH, len(chunks))}/{len(chunks)}")

    print(f"\nDone. {len(chunks)} chunks indexed into '{args.index}'.")
    print("Integrated vectorization fills contentVector on ingest when an "
          "Azure OpenAI embedding deployment is configured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
