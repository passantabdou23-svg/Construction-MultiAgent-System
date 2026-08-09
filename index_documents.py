"""Build or refresh the persistent Chroma index from controlled JSONL chunks."""

from __future__ import annotations

import argparse
from pathlib import Path

from rag_engine import ConstructionRAG
from settings import settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed citation-ready chunks and persist them in local ChromaDB."
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(settings.rag_chunks_path),
        help="Ingestion JSONL path (default: rag_data/chunks.jsonl)",
    )
    parser.add_argument(
        "--persist-path",
        type=Path,
        default=Path(settings.rag_index_path),
        help="Persistent Chroma directory (default: rag_data/chroma)",
    )
    parser.add_argument(
        "--collection",
        default=settings.rag_collection_name,
        help="Chroma collection name",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    rag = ConstructionRAG(
        collection_name=arguments.collection,
        persist_path=arguments.persist_path,
        chunks_path=arguments.chunks,
        auto_index=False,
    )
    summary = rag.index_chunks(arguments.chunks, batch_size=arguments.batch_size)
    print("Persistent RAG index ready")
    print(f"  Embedding model: {rag.embedding_model_id}")
    print(f"  Model SHA-256: {rag.embedding_model_sha256}")
    print(f"  Distance metric: cosine")
    print(f"  Source chunks: {summary.input_chunks}")
    print(f"  Retrieval-eligible chunks: {summary.eligible_chunks}")
    print(f"  Indexed chunks: {summary.indexed_chunks}")
    print(f"  Removed stale chunks: {summary.deleted_chunks}")
    print(f"  Index unchanged: {summary.unchanged}")
    print(f"  Chroma path: {rag.persist_path}")
    print(f"  Source SHA-256: {summary.source_sha256}")


if __name__ == "__main__":
    main()
