"""Persistent, source-traceable retrieval for controlled construction documents."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction
from chromadb.errors import NotFoundError
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from settings import settings


RAG_SCHEMA_VERSION = 1
EMBEDDING_FUNCTION_NAME = "onnx_mini_lm_l6_v2"
EMBEDDING_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
DISTANCE_METRIC = "cosine"
DEFAULT_BATCH_SIZE = 64


class RAGIndexError(RuntimeError):
    """Base error for an invalid, missing, or incompatible local RAG index."""


class RAGIndexNotReadyError(RAGIndexError):
    """Raised when retrieval is requested before controlled chunks are indexed."""


class RAGIndexCompatibilityError(RAGIndexError):
    """Raised when persisted vectors were built with a different contract."""


class LowConfidenceRetrievalError(RAGIndexError):
    """Raised when no retrieved chunk reaches the configured similarity threshold."""

    def __init__(self, query: str, best_similarity: float, threshold: float):
        self.query = query
        self.best_similarity = best_similarity
        self.threshold = threshold
        super().__init__(
            f"No controlled source met the retrieval threshold for '{query}'. "
            f"Best similarity was {best_similarity:.3f}; required {threshold:.3f}."
        )


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    text: str
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class IndexSummary:
    source_path: Path
    source_sha256: str
    input_chunks: int
    eligible_chunks: int
    deleted_chunks: int
    indexed_chunks: int
    unchanged: bool


@dataclass(frozen=True)
class RetrievedStandard:
    chunk_id: str
    document_id: str
    title: str
    edition: str
    jurisdiction: str
    document_code: str
    discipline: str
    authority: str
    status: str
    source_checked_date: str
    effective_date: str
    page_number: int
    printed_page_label: str
    section: str
    clause: str
    text: str
    source_url: str
    distance: float
    similarity: float
    semantic_similarity: float
    lexical_similarity: float
    routing_reason: str

    @property
    def citation_label(self) -> str:
        location = self.section or "Unsectioned content"
        if self.clause:
            location = f"{location}, clause {self.clause}"
        page = f"PDF p. {self.page_number}"
        if self.printed_page_label:
            page = f"printed p. {self.printed_page_label} ({page})"
        governance = f"{self.edition}; {self.jurisdiction}; status={self.status}"
        if self.effective_date:
            governance = f"{governance}; effective={self.effective_date}"
        return f"{self.title} ({governance}), {location}, {page}"

    @property
    def citation(self) -> str:
        return (
            f"[{self.citation_label}; hybrid={self.similarity:.3f}; "
            f"semantic={self.semantic_similarity:.3f}; lexical={self.lexical_similarity:.3f}]\n"
            f"{self.text}\nSource: {self.source_url}"
        )


_CHUNK_METADATA_FIELDS = (
    "document_id",
    "document_code",
    "discipline",
    "authority",
    "status",
    "source_checked_date",
    "effective_date",
    "routing_keywords",
    "title",
    "edition",
    "publication_date",
    "jurisdiction",
    "page_number",
    "printed_page_label",
    "section",
    "clause",
    "source_url",
    "download_url",
    "license_name",
    "license_url",
    "source_sha256",
    "citation",
    "retrieval_eligible",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    "a an and are as at be by for from how in into is it of on or should the to what when where which with".split()
)


@dataclass(frozen=True)
class RoutingDecision:
    document_ids: tuple[str, ...]
    document_codes: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    reason: str
    out_of_scope: bool = False


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in _TOKEN_RE.findall(text.casefold()) if token not in _STOP_WORDS)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return float(max(-1.0, min(1.0, numerator / (left_norm * right_norm))))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_metadata(payload: dict[str, Any], line_number: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in _CHUNK_METADATA_FIELDS:
        value = payload.get(field, "" if field == "printed_page_label" else None)
        if field == "page_number":
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise RAGIndexError(
                    f"Chunk line {line_number} has an invalid positive page_number"
                )
        elif field == "retrieval_eligible":
            if not isinstance(value, bool):
                raise RAGIndexError(
                    f"Chunk line {line_number} has an invalid retrieval_eligible flag"
                )
        elif not isinstance(value, str):
            raise RAGIndexError(f"Chunk line {line_number} has an invalid '{field}' value")
        metadata[field] = value
    return metadata


def load_chunk_records(chunks_path: str | Path) -> tuple[ChunkRecord, ...]:
    """Load deterministic ingestion output and reject incomplete or duplicate records."""
    path = Path(chunks_path).resolve()
    if not path.is_file():
        raise RAGIndexNotReadyError(
            f"Citation-ready chunks do not exist at {path}. Run 'python ingest_documents.py' first."
        )

    records: list[ChunkRecord] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise RAGIndexError(f"Invalid JSON on chunk line {line_number}") from error
        if not isinstance(payload, dict):
            raise RAGIndexError(f"Chunk line {line_number} must be a JSON object")
        chunk_id = payload.get("chunk_id")
        text = payload.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise RAGIndexError(f"Chunk line {line_number} has no stable chunk_id")
        if chunk_id in seen_ids:
            raise RAGIndexError(f"Duplicate chunk_id on line {line_number}: {chunk_id}")
        if not isinstance(text, str) or not text.strip():
            raise RAGIndexError(f"Chunk line {line_number} has no searchable text")
        seen_ids.add(chunk_id)
        records.append(
            ChunkRecord(
                chunk_id=chunk_id,
                text=text.strip(),
                metadata=_validated_metadata(payload, line_number),
            )
        )
    if not records:
        raise RAGIndexError(f"No chunks were loaded from {path}")
    return tuple(records)


def _batches(values: tuple[ChunkRecord, ...], batch_size: int) -> Iterable[tuple[ChunkRecord, ...]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


class ConstructionRAG:
    """A persistent local Chroma index using trained MiniLM sentence embeddings."""

    def __init__(
        self,
        collection_name: str | None = None,
        *,
        persist_path: str | Path | None = None,
        chunks_path: str | Path | None = None,
        embedding_function: EmbeddingFunction[Documents] | None = None,
        embedding_model_id: str = EMBEDDING_MODEL_ID,
        embedding_model_sha256: str = EMBEDDING_MODEL_SHA256,
        minimum_similarity: float | None = None,
        top_k: int | None = None,
        semantic_weight: float | None = None,
        lexical_weight: float | None = None,
        auto_index: bool = True,
    ):
        self.collection_name = collection_name or settings.rag_collection_name
        self.persist_path = Path(persist_path or settings.rag_index_path).resolve()
        self.chunks_path = Path(chunks_path or settings.rag_chunks_path).resolve()
        self.minimum_similarity = (
            settings.rag_minimum_similarity
            if minimum_similarity is None
            else minimum_similarity
        )
        self.top_k = settings.rag_top_k if top_k is None else top_k
        self.semantic_weight = settings.rag_semantic_weight if semantic_weight is None else semantic_weight
        self.lexical_weight = settings.rag_lexical_weight if lexical_weight is None else lexical_weight
        if not 0 <= self.minimum_similarity <= 1:
            raise ValueError("minimum_similarity must be between 0 and 1")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.semantic_weight < 0 or self.lexical_weight < 0:
            raise ValueError("retrieval weights cannot be negative")
        if not math.isclose(self.semantic_weight + self.lexical_weight, 1.0, abs_tol=1e-9):
            raise ValueError("semantic_weight and lexical_weight must sum to 1")

        self.embedding_function = embedding_function or ONNXMiniLM_L6_V2()
        self.embedding_function_name = self.embedding_function.name()
        self.embedding_model_id = embedding_model_id
        self.embedding_model_sha256 = embedding_model_sha256
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_path))
        self.collection = self._open_collection()
        if auto_index and self.collection.count() == 0:
            self.index_chunks(self.chunks_path)

    @property
    def _base_collection_metadata(self) -> dict[str, str | int]:
        return {
            "rag_schema_version": RAG_SCHEMA_VERSION,
            "embedding_function": self.embedding_function_name,
            "embedding_model": self.embedding_model_id,
            "embedding_model_sha256": self.embedding_model_sha256,
            "distance_metric": DISTANCE_METRIC,
        }

    def _open_collection(self):
        try:
            collection = self.client.get_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function,
            )
        except NotFoundError:
            return self.client.create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function,
                metadata=self._base_collection_metadata,
                configuration={"hnsw": {"space": DISTANCE_METRIC}},
            )

        metadata = collection.metadata or {}
        mismatches = {
            key: (metadata.get(key), expected)
            for key, expected in self._base_collection_metadata.items()
            if metadata.get(key) != expected
        }
        if mismatches:
            details = ", ".join(
                f"{key}: stored={stored!r}, required={required!r}"
                for key, (stored, required) in mismatches.items()
            )
            raise RAGIndexCompatibilityError(
                f"Collection '{self.collection_name}' is incompatible ({details}). "
                "Use a new collection name or remove the local index after backing it up."
            )
        return collection

    def index_chunks(
        self,
        chunks_path: str | Path | None = None,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> IndexSummary:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        source_path = Path(chunks_path or self.chunks_path).resolve()
        source_records = load_chunk_records(source_path)
        records = tuple(
            record for record in source_records if record.metadata["retrieval_eligible"] is True
        )
        if not records:
            raise RAGIndexError(f"No retrieval-eligible chunks were loaded from {source_path}")
        source_sha256 = _file_sha256(source_path)
        metadata = self.collection.metadata or {}
        if (
            metadata.get("index_content_sha256") == source_sha256
            and self.collection.count() == len(records)
        ):
            return IndexSummary(
                source_path=source_path,
                source_sha256=source_sha256,
                input_chunks=len(source_records),
                eligible_chunks=len(records),
                deleted_chunks=0,
                indexed_chunks=self.collection.count(),
                unchanged=True,
            )

        incoming_ids = {record.chunk_id for record in records}
        existing = set(self.collection.get(include=[])["ids"])
        stale_ids = sorted(existing - incoming_ids)
        if stale_ids:
            self.collection.delete(ids=stale_ids)

        for batch in _batches(records, batch_size):
            self.collection.upsert(
                ids=[record.chunk_id for record in batch],
                documents=[record.text for record in batch],
                metadatas=[record.metadata for record in batch],
            )

        indexed_count = self.collection.count()
        if indexed_count != len(records):
            raise RAGIndexError(
                f"Index count mismatch: expected {len(records)}, received {indexed_count}"
            )
        self.collection.modify(
            metadata={
                **self._base_collection_metadata,
                "index_content_sha256": source_sha256,
                "indexed_chunk_count": indexed_count,
            }
        )
        return IndexSummary(
            source_path=source_path,
            source_sha256=source_sha256,
            input_chunks=len(source_records),
            eligible_chunks=len(records),
            deleted_chunks=len(stale_ids),
            indexed_chunks=indexed_count,
            unchanged=False,
        )

    def route_query(self, query: str) -> RoutingDecision:
        normalized_query = (query or "").strip().casefold()
        if not normalized_query:
            raise ValueError("RAG query cannot be empty")
        out_of_scope_patterns = (
            r"\b(?:price|cost|supplier|vendor|phone|telephone|weather)\b",
            r"\b(?:permit|licen[cs]e)\s+fees?\b",
            r"\b(?:egypt|egyptian|cairo)\b",
        )
        out_of_scope = any(re.search(pattern, normalized_query) for pattern in out_of_scope_patterns)
        records = self.collection.get(include=["metadatas"])
        documents: dict[str, tuple[str, tuple[str, ...]]] = {}
        for metadata in records["metadatas"]:
            if metadata is None:
                continue
            document_id = str(metadata["document_id"])
            keywords = tuple(
                keyword.strip().casefold()
                for keyword in str(metadata.get("routing_keywords", "")).split("|")
                if keyword.strip()
            )
            documents[document_id] = (str(metadata.get("document_code", "")), keywords)
        matched: dict[str, set[str]] = {}
        for document_id, (_, keywords) in documents.items():
            for keyword in keywords:
                pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
                if re.search(pattern, normalized_query):
                    matched.setdefault(document_id, set()).add(keyword)
        if not matched:
            reason = "No discipline keyword matched; searched all controlled documents"
            if out_of_scope:
                reason = "Query contains commercial or out-of-jurisdiction terms outside the controlled corpus"
            return RoutingDecision((), (), (), reason, out_of_scope)
        route_strength = {
            document_id: sum(max(1, len(_tokens(keyword))) for keyword in keywords)
            for document_id, keywords in matched.items()
        }
        strongest = max(route_strength.values())
        document_ids = tuple(
            sorted(document_id for document_id, strength in route_strength.items() if strength == strongest)
        )
        codes = tuple(documents[document_id][0] for document_id in document_ids)
        keywords = tuple(sorted({keyword for document_id in document_ids for keyword in matched[document_id]}))
        reason = f"Routed by keywords: {', '.join(keywords)}"
        if out_of_scope:
            reason = f"{reason}; scope guard detected unsupported commercial or jurisdictional intent"
        return RoutingDecision(document_ids, codes, keywords, reason, out_of_scope)

    @staticmethod
    def _bm25_scores(query: str, documents: list[str]) -> list[float]:
        query_terms = tuple(dict.fromkeys(_tokens(query)))
        if not query_terms or not documents:
            return [0.0] * len(documents)
        tokenized = [_tokens(document) for document in documents]
        average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized) or 1.0
        document_frequency = {
            term: sum(term in set(tokens) for tokens in tokenized) for term in query_terms
        }
        raw_scores: list[float] = []
        k1, b = 1.5, 0.75
        for tokens in tokenized:
            counts = Counter(tokens)
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if not frequency:
                    continue
                frequency_documents = document_frequency[term]
                inverse_frequency = math.log(
                    1 + (len(tokenized) - frequency_documents + 0.5) / (frequency_documents + 0.5)
                )
                denominator = frequency + k1 * (1 - b + b * len(tokens) / average_length)
                score += inverse_frequency * frequency * (k1 + 1) / denominator
            raw_scores.append(score)
        maximum = max(raw_scores, default=0.0)
        return [score / maximum if maximum else 0.0 for score in raw_scores]

    def query_candidates(self, query: str, n_results: int | None = None) -> tuple[RetrievedStandard, ...]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValueError("RAG query cannot be empty")
        count = self.collection.count()
        if count == 0:
            raise RAGIndexNotReadyError(
                "The persistent RAG collection is empty. Run 'python index_documents.py' first."
            )
        requested = self.top_k if n_results is None else n_results
        if not 1 <= requested <= 20:
            raise ValueError("n_results must be between 1 and 20")
        routing = self.route_query(normalized_query)
        result = self.collection.get(include=["documents", "metadatas", "embeddings"])
        rows = [
            (chunk_id, text, metadata, embedding)
            for chunk_id, text, metadata, embedding in zip(
                result["ids"], result["documents"], result["metadatas"], result["embeddings"]
            )
            if metadata is not None
            and (not routing.document_ids or str(metadata["document_id"]) in routing.document_ids)
        ]
        if not rows:
            raise RAGIndexError("Document routing produced no indexed candidates")
        query_embedding = list(self.embedding_function([normalized_query])[0])
        lexical_scores = self._bm25_scores(normalized_query, [str(row[1]) for row in rows])

        candidates: list[RetrievedStandard] = []
        for (chunk_id, text, metadata, embedding), lexical_similarity in zip(rows, lexical_scores):
            if text is None or metadata is None or embedding is None:
                raise RAGIndexError("Chroma returned an incomplete retrieval record")
            semantic_similarity = _cosine_similarity(query_embedding, list(embedding))
            distance = 1.0 - semantic_similarity
            similarity = float(
                self.semantic_weight * max(0.0, semantic_similarity)
                + self.lexical_weight * lexical_similarity
            )
            if not routing.document_ids:
                similarity *= 0.75
            if routing.out_of_scope:
                similarity *= 0.50
            candidates.append(
                RetrievedStandard(
                    chunk_id=chunk_id,
                    document_id=str(metadata["document_id"]),
                    title=str(metadata["title"]),
                    edition=str(metadata["edition"]),
                    jurisdiction=str(metadata["jurisdiction"]),
                    document_code=str(metadata["document_code"]),
                    discipline=str(metadata["discipline"]),
                    authority=str(metadata["authority"]),
                    status=str(metadata["status"]),
                    source_checked_date=str(metadata["source_checked_date"]),
                    effective_date=str(metadata["effective_date"]),
                    page_number=int(metadata["page_number"]),
                    printed_page_label=str(metadata.get("printed_page_label", "")),
                    section=str(metadata["section"]),
                    clause=str(metadata["clause"]),
                    text=text,
                    source_url=str(metadata["source_url"]),
                    distance=distance,
                    similarity=similarity,
                    semantic_similarity=semantic_similarity,
                    lexical_similarity=lexical_similarity,
                    routing_reason=routing.reason,
                )
            )
        candidates.sort(key=lambda candidate: (-candidate.similarity, candidate.chunk_id))
        return tuple(candidates[:requested])

    def query_many(
        self,
        query: str,
        n_results: int | None = None,
        *,
        minimum_similarity: float | None = None,
    ) -> tuple[RetrievedStandard, ...]:
        threshold = self.minimum_similarity if minimum_similarity is None else minimum_similarity
        if not 0 <= threshold <= 1:
            raise ValueError("minimum_similarity must be between 0 and 1")
        candidates = self.query_candidates(query, n_results=n_results)
        qualified = tuple(candidate for candidate in candidates if candidate.similarity >= threshold)
        if not qualified:
            best_similarity = candidates[0].similarity if candidates else -1.0
            raise LowConfidenceRetrievalError(query, best_similarity, threshold)
        return qualified

    def query(self, query: str) -> RetrievedStandard:
        return self.query_many(query, n_results=1)[0]

    def query_spec(self, query: str, n_results: int = 1) -> str:
        """Backward-compatible string API containing traceable source context."""
        return "\n\n".join(
            result.citation for result in self.query_many(query, n_results=n_results)
        )


if __name__ == "__main__":
    rag = ConstructionRAG()
    print(rag.query_spec("minimum width of strip foundations", n_results=3))
