"""Persistent, source-traceable retrieval for controlled construction documents."""

from __future__ import annotations

import hashlib
import json
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
    page_number: int
    printed_page_label: str
    section: str
    clause: str
    text: str
    source_url: str
    distance: float
    similarity: float

    @property
    def citation_label(self) -> str:
        location = self.section or "Unsectioned content"
        if self.clause:
            location = f"{location}, clause {self.clause}"
        page = f"PDF p. {self.page_number}"
        if self.printed_page_label:
            page = f"printed p. {self.printed_page_label} ({page})"
        return f"{self.title} ({self.edition}), {location}, {page}"

    @property
    def citation(self) -> str:
        return (
            f"[{self.citation_label}; similarity={self.similarity:.3f}]\n"
            f"{self.text}\nSource: {self.source_url}"
        )


_CHUNK_METADATA_FIELDS = (
    "document_id",
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
        if not 0 <= self.minimum_similarity <= 1:
            raise ValueError("minimum_similarity must be between 0 and 1")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

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
        result = self.collection.query(
            query_texts=[normalized_query],
            n_results=min(requested, count),
            include=["documents", "metadatas", "distances"],
        )

        candidates: list[RetrievedStandard] = []
        for chunk_id, text, metadata, distance_value in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            if text is None or metadata is None or distance_value is None:
                raise RAGIndexError("Chroma returned an incomplete retrieval record")
            distance = float(distance_value)
            similarity = max(-1.0, min(1.0, 1.0 - distance))
            candidates.append(
                RetrievedStandard(
                    chunk_id=chunk_id,
                    document_id=str(metadata["document_id"]),
                    title=str(metadata["title"]),
                    edition=str(metadata["edition"]),
                    jurisdiction=str(metadata["jurisdiction"]),
                    page_number=int(metadata["page_number"]),
                    printed_page_label=str(metadata.get("printed_page_label", "")),
                    section=str(metadata["section"]),
                    clause=str(metadata["clause"]),
                    text=text,
                    source_url=str(metadata["source_url"]),
                    distance=distance,
                    similarity=similarity,
                )
            )
        return tuple(candidates)

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
