import unittest

from rag_engine import ConstructionRAG


class ConstructionRAGTests(unittest.TestCase):
    def test_vector_index_returns_traceable_context(self):
        result = ConstructionRAG(collection_name="test-standards").query(
            "ground slab concrete reinforcement mesh"
        )
        self.assertTrue(result.document_id)
        self.assertIn("demonstration summary", result.title)
        self.assertGreater(len(result.text), 30)
        self.assertGreaterEqual(result.distance, 0)

    def test_empty_query_is_rejected(self):
        rag = ConstructionRAG(collection_name="test-empty-query")
        with self.assertRaises(ValueError):
            rag.query("  ")


if __name__ == "__main__":
    unittest.main()
