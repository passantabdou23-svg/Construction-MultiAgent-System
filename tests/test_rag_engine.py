import gc
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from chromadb.api.client import SharedSystemClient
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Space

from rag_engine import (
    ConstructionRAG,
    LowConfidenceRetrievalError,
    RAGIndexCompatibilityError,
)


class KeywordEmbeddingFunction(EmbeddingFunction[Documents]):
    """Small deterministic embedding used only to keep unit tests offline and fast."""

    vocabulary = ("foundation", "wall", "roof", "collapse", "concrete", "price")

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: Embeddings = []
        for document in input:
            normalized = document.casefold()
            vector = [1.0]
            vector.extend(float(normalized.count(word)) for word in self.vocabulary)
            magnitude = math.sqrt(sum(component * component for component in vector))
            embeddings.append([component / magnitude for component in vector])
        return embeddings

    @staticmethod
    def name() -> str:
        return "test_keyword_embedding"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "KeywordEmbeddingFunction":
        return KeywordEmbeddingFunction()

    def default_space(self) -> Space:
        return Space.COSINE

    def supported_spaces(self) -> list[Space]:
        return [Space.COSINE]


def _record(
    chunk_id: str,
    text: str,
    section: str,
    page: int,
    *,
    code: str = "A",
    document_id: str = "approved-document-a",
    discipline: str = "structure",
    routing_keywords: str = "foundation | wall | roof | collapse",
) -> dict:
    title = f"Approved Document {code}"
    edition = "controlled test edition"
    citation = f"{title} ({edition}), {section}, printed p. {page - 2} (PDF p. {page})"
    return {
        "chunk_id": chunk_id,
        "text": text,
        "document_id": document_id,
        "document_code": code,
        "discipline": discipline,
        "authority": "Test authority",
        "status": "current",
        "source_checked_date": "2026-08-09",
        "effective_date": "",
        "routing_keywords": routing_keywords,
        "title": title,
        "edition": edition,
        "publication_date": "2024-03-01",
        "jurisdiction": "England",
        "page_number": page,
        "printed_page_label": str(page - 2),
        "section": section,
        "clause": "1.1",
        "source_url": "https://www.gov.uk/government/publications/structure-approved-document-a",
        "download_url": "https://assets.publishing.service.gov.uk/example.pdf",
        "license_name": "Open Government Licence v3.0",
        "license_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "source_sha256": "a" * 64,
        "citation": citation,
        "retrieval_eligible": True,
    }


class ConstructionRAGTests(unittest.TestCase):
    def setUp(self):
        self.temporary_root = Path(tempfile.mkdtemp(prefix="construction-rag-test-"))
        self.chunks_path = self.temporary_root / "chunks.jsonl"
        self.persist_path = self.temporary_root / "chroma"
        self.records = [
            _record(
                "foundation-1",
                "Strip foundation width must safely distribute wall loads to the ground.",
                "Foundations",
                38,
            ),
            _record(
                "ramp-1",
                "A ramp flight should satisfy the controlled gradient and going requirements.",
                "Ramps",
                25,
                code="K",
                document_id="approved-document-k",
                discipline="falling-collision-impact",
                routing_keywords="ramp | stair | guarding | handrail",
            ),
            _record(
                "roof-1",
                "Roof covering and roof structure must resist the relevant imposed loads.",
                "Roofs",
                42,
            ),
            _record(
                "collapse-1",
                "The building should reduce sensitivity to disproportionate collapse.",
                "Disproportionate Collapse",
                45,
            ),
        ]
        self._write_records(self.records)
        self.rag = self._new_rag()

    def tearDown(self):
        self.rag = None
        gc.collect()
        SharedSystemClient.clear_system_cache()
        shutil.rmtree(self.temporary_root, ignore_errors=True)

    def _write_records(self, records: list[dict]) -> None:
        with self.chunks_path.open("w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(json.dumps(record, sort_keys=True))
                output.write("\n")

    def _new_rag(self, **overrides) -> ConstructionRAG:
        options = {
            "collection_name": "test-construction-standards",
            "persist_path": self.persist_path,
            "chunks_path": self.chunks_path,
            "embedding_function": KeywordEmbeddingFunction(),
            "embedding_model_id": "test/keyword-v1",
            "embedding_model_sha256": "test-keyword-v1-sha256",
            "minimum_similarity": 0.60,
            "top_k": 3,
            "auto_index": False,
        }
        options.update(overrides)
        return ConstructionRAG(**options)

    def test_index_returns_traceable_ranked_context(self):
        summary = self.rag.index_chunks()
        results = self.rag.query_many("foundation wall", n_results=2)

        self.assertEqual(summary.indexed_chunks, 4)
        self.assertEqual(summary.eligible_chunks, 4)
        self.assertEqual(results[0].chunk_id, "foundation-1")
        self.assertEqual(results[0].printed_page_label, "36")
        self.assertIn("printed p. 36 (PDF p. 38)", results[0].citation)
        self.assertIn("Source: https://www.gov.uk/", results[0].citation)

    def test_persistent_index_reopens_without_reembedding(self):
        self.rag.index_chunks()
        self.rag = None
        gc.collect()
        SharedSystemClient.clear_system_cache()

        reopened = self._new_rag()
        self.rag = reopened
        self.assertEqual(reopened.collection.count(), 4)
        self.assertEqual(reopened.query("roof loads").chunk_id, "roof-1")

    def test_unchanged_source_is_not_reindexed(self):
        first = self.rag.index_chunks()
        second = self.rag.index_chunks()

        self.assertFalse(first.unchanged)
        self.assertTrue(second.unchanged)
        self.assertEqual(second.indexed_chunks, 4)

    def test_stale_chunks_are_removed_during_refresh(self):
        self.rag.index_chunks()
        self._write_records(self.records[:2])
        refreshed = self.rag.index_chunks()

        self.assertEqual(refreshed.deleted_chunks, 2)
        self.assertEqual(refreshed.indexed_chunks, 2)
        self.assertNotIn("collapse-1", self.rag.collection.get(include=[])["ids"])

    def test_low_confidence_query_is_rejected(self):
        self.rag.index_chunks()
        with self.assertRaises(LowConfidenceRetrievalError) as context:
            self.rag.query_many("supplier price", minimum_similarity=0.90)
        self.assertLess(context.exception.best_similarity, 0.90)

    def test_incompatible_embedding_contract_is_rejected(self):
        self.rag.index_chunks()
        with self.assertRaises(RAGIndexCompatibilityError):
            self._new_rag(embedding_model_id="test/keyword-v2")

    def test_empty_query_is_rejected(self):
        self.rag.index_chunks()
        with self.assertRaises(ValueError):
            self.rag.query("  ")

    def test_document_routing_limits_ramp_query_to_document_k(self):
        self.rag.index_chunks()
        routing = self.rag.route_query("What gradient should a ramp use?")
        results = self.rag.query_candidates("What gradient should a ramp use?", n_results=3)

        self.assertEqual(routing.document_codes, ("K",))
        self.assertTrue(results)
        self.assertTrue(all(result.document_code == "K" for result in results))
        self.assertIn("ramp", results[0].routing_reason)

    def test_unrouted_query_searches_all_controlled_documents(self):
        self.rag.index_chunks()
        routing = self.rag.route_query("general controlled requirement")
        self.assertEqual(routing.document_ids, ())
        self.assertIn("searched all", routing.reason)

    def test_scope_guard_reduces_unsupported_commercial_confidence(self):
        self.rag.index_chunks()
        routing = self.rag.route_query("foundation supplier price in Cairo")
        candidate = self.rag.query_candidates("foundation supplier price in Cairo", n_results=1)[0]

        self.assertTrue(routing.out_of_scope)
        self.assertLess(candidate.similarity, candidate.semantic_similarity)
        self.assertIn("scope guard", candidate.routing_reason)


if __name__ == "__main__":
    unittest.main()
