import unittest

from evaluate_rag import RAGEvaluationError, evaluate_cases
from rag_engine import RetrievedStandard


def _candidate(page: int, similarity: float) -> RetrievedStandard:
    return RetrievedStandard(
        chunk_id=f"chunk-{page}",
        document_id="approved-a",
        title="Approved Document A",
        edition="test edition",
        jurisdiction="England",
        document_code="A",
        discipline="structure",
        authority="Test authority",
        status="current",
        source_checked_date="2026-08-09",
        effective_date="",
        page_number=page,
        printed_page_label=str(page - 2),
        section="Foundations",
        clause="",
        text="Controlled test passage.",
        source_url="https://example.test/source",
        distance=1 - similarity,
        similarity=similarity,
        semantic_similarity=similarity,
        lexical_similarity=0.0,
        routing_reason="Test route",
    )


class _FakeRAG:
    embedding_model_id = "test/model"
    embedding_model_sha256 = "test-model-sha256"
    collection_name = "test-collection"

    def query_candidates(self, query: str, n_results: int):
        if query == "in scope":
            return (_candidate(38, 0.72), _candidate(10, 0.50))[:n_results]
        return (_candidate(10, 0.20), _candidate(38, 0.10))[:n_results]


class RAGEvaluationTests(unittest.TestCase):
    def test_metrics_distinguish_relevant_and_rejected_queries(self):
        evaluation = {
            "targets": {
                "minimum_hit_at_k": 1.0,
                "minimum_positive_acceptance_rate": 1.0,
                "minimum_negative_rejection_rate": 1.0,
            },
            "cases": [
                {
                    "id": "positive",
                    "kind": "positive",
                    "query": "in scope",
                    "expected_document_id": "approved-a",
                    "expected_document_code": "A",
                    "expected_pdf_pages": [38],
                },
                {"id": "negative", "kind": "negative", "query": "out of scope"},
            ],
        }

        report = evaluate_cases(_FakeRAG(), evaluation, top_k=2, threshold=0.45)

        self.assertTrue(report["targets_met"])
        self.assertEqual(report["metrics"]["hit_at_k"], 1.0)
        self.assertEqual(report["metrics"]["top_1_accuracy"], 1.0)
        self.assertEqual(report["metrics"]["negative_rejection_rate"], 1.0)
        self.assertEqual(report["metrics"]["routing_accuracy"], 1.0)

    def test_both_positive_and_negative_cases_are_required(self):
        evaluation = {
            "cases": [
                {
                    "id": "positive",
                    "kind": "positive",
                    "query": "in scope",
                    "expected_document_id": "approved-a",
                    "expected_document_code": "A",
                    "expected_pdf_pages": [38],
                }
            ]
        }
        with self.assertRaises(RAGEvaluationError):
            evaluate_cases(_FakeRAG(), evaluation, top_k=2, threshold=0.45)


if __name__ == "__main__":
    unittest.main()
