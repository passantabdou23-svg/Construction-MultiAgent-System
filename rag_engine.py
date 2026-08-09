"""Offline vector retrieval for the project's demonstration standards library."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

import chromadb

from settings import settings


@dataclass(frozen=True)
class StandardDocument:
    document_id: str
    title: str
    text: str


@dataclass(frozen=True)
class RetrievedStandard:
    document_id: str
    title: str
    text: str
    distance: float

    @property
    def citation(self) -> str:
        return f"{self.title}: {self.text}"


STANDARD_DOCUMENTS = (
    StandardDocument(
        "aci-318-concrete",
        "ACI 318 demonstration summary",
        "Concrete foundations and columns require the specified strength class, placement controls, curing checks, and verification before loading.",
    ),
    StandardDocument(
        "astm-a36-steel",
        "ASTM A36 demonstration summary",
        "Structural steel plates and compatible steelwork require material identification, inspection, and project-specific corrosion protection where exposure demands it.",
    ),
    StandardDocument(
        "slab-reinforcement",
        "Slab reinforcement demonstration summary",
        "Ground and suspended slabs require the engineer-approved concrete class, reinforcement or mesh schedule, cover, curing, and strength verification.",
    ),
    StandardDocument(
        "eurocode-2-concrete",
        "Eurocode 2 demonstration summary",
        "Structural concrete design requires verification of material properties, durability, reinforcement detailing, and construction-stage loading.",
    ),
    StandardDocument(
        "site-excavation",
        "Site preparation demonstration summary",
        "Excavation and site preparation require approved dimensions, ground-condition checks, safe access, and inspection before foundation work proceeds.",
    ),
)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def _hash_embedding(text: str, dimensions: int = 128) -> list[float]:
    """Create a deterministic local feature-hashing vector without downloads."""
    vector = [0.0] * dimensions
    for token in _tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]


class ConstructionRAG:
    """A small ChromaDB-backed offline vector index for demo references."""

    def __init__(self, collection_name: str | None = None):
        self.client = chromadb.EphemeralClient()
        self.collection = self.client.get_or_create_collection(
            name=collection_name or settings.rag_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        if self.collection.count() == 0:
            self.collection.add(
                ids=[document.document_id for document in STANDARD_DOCUMENTS],
                documents=[document.text for document in STANDARD_DOCUMENTS],
                metadatas=[{"title": document.title} for document in STANDARD_DOCUMENTS],
                embeddings=[_hash_embedding(document.text) for document in STANDARD_DOCUMENTS],
            )

    def query(self, query: str) -> RetrievedStandard:
        if not query or not query.strip():
            raise ValueError("RAG query cannot be empty")
        result = self.collection.query(
            query_embeddings=[_hash_embedding(query)],
            n_results=1,
            include=["documents", "metadatas", "distances"],
        )
        return RetrievedStandard(
            document_id=result["ids"][0][0],
            title=result["metadatas"][0][0]["title"],
            text=result["documents"][0][0],
            distance=float(result["distances"][0][0]),
        )

    def query_spec(self, query: str, n_results: int = 1) -> str:
        """Backward-compatible string API used by older callers."""
        if n_results != 1:
            raise ValueError("This project currently exposes one verified context result per query")
        return self.query(query).citation


if __name__ == "__main__":
    rag = ConstructionRAG()
    print(rag.query_spec("foundation column concrete grade"))
