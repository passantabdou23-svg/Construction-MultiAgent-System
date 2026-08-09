import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ingest_documents import (
    SourceIntegrityError,
    ingest_document,
    load_manifest,
    write_chunks_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "rag_documents" / "manifest.json"


class DocumentIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = load_manifest(MANIFEST_PATH)
        cls.documents_by_code = {document.document_code: document for document in cls.documents}
        cls.results_by_code = {
            code: ingest_document(document) for code, document in cls.documents_by_code.items()
        }
        cls.document = cls.documents_by_code["A"]
        cls.result = cls.results_by_code["A"]

    def test_four_controlled_official_documents_are_ingested(self):
        self.assertEqual(set(self.documents_by_code), {"A", "C", "K", "7"})
        expected = {
            "A": (54, 53, 147, 128),
            "C": (52, 50, 214, 196),
            "K": (68, 67, 163, 146),
            "7": (24, 20, 62, 56),
        }
        for code, (pages, text_pages, chunks, eligible) in expected.items():
            result = self.results_by_code[code]
            self.assertEqual(result.page_count, pages)
            self.assertEqual(result.nonempty_page_count, text_pages)
            self.assertEqual(len(result.chunks), chunks)
            self.assertEqual(sum(chunk.retrieval_eligible for chunk in result.chunks), eligible)

    def test_official_pdf_is_fully_searchable(self):
        self.assertEqual(self.result.page_count, 54)
        self.assertEqual(self.result.nonempty_page_count, 53)
        self.assertEqual(self.result.non_text_page_numbers, (52,))
        self.assertGreater(len(self.result.chunks), 50)

    def test_chunks_preserve_traceable_source_metadata(self):
        chunk_ids = {chunk.chunk_id for chunk in self.result.chunks}
        self.assertEqual(len(chunk_ids), len(self.result.chunks))
        for chunk in self.result.chunks:
            self.assertTrue(chunk.text.strip())
            self.assertGreaterEqual(chunk.page_number, 1)
            self.assertLessEqual(chunk.page_number, 54)
            self.assertEqual(chunk.document_id, self.document.document_id)
            self.assertEqual(chunk.source_sha256, self.document.expected_sha256)
            self.assertIn(f"PDF p. {chunk.page_number}", chunk.citation)
            self.assertEqual(chunk.source_url, self.document.source_url)
            self.assertEqual(chunk.document_code, "A")
            self.assertEqual(chunk.discipline, "structure")
            self.assertTrue(chunk.routing_keywords)
        sections = {chunk.section for chunk in self.result.chunks}
        self.assertNotIn("S = V × O × A", sections)
        self.assertFalse(any(section.casefold().startswith("bsi pd") for section in sections))

    def test_numbered_clause_is_detected(self):
        clauses = {chunk.clause for chunk in self.result.chunks}
        self.assertIn("0.1", clauses)
        clause_chunk = next(chunk for chunk in self.result.chunks if chunk.clause == "0.1")
        self.assertIn("requirements of A1 and A2", clause_chunk.text)
        self.assertIn("Secretary of State", clause_chunk.text)
        self.assertNotIn("Secr etary", clause_chunk.text)
        self.assertEqual(clause_chunk.page_number, 8)
        self.assertEqual(clause_chunk.printed_page_label, "6")
        self.assertIn("printed p. 6 (PDF p. 8)", clause_chunk.citation)

    def test_manifest_exclusions_keep_audit_chunks_out_of_retrieval(self):
        self.assertEqual(self.document.retrieval_excluded_pages, (1, 3, 4, 53, 54))
        for chunk in self.result.chunks:
            self.assertEqual(
                chunk.retrieval_eligible,
                chunk.page_number not in self.document.retrieval_excluded_pages,
            )

    def test_derived_printed_pages_cover_even_page_footers(self):
        for code in ("K", "7"):
            document = self.documents_by_code[code]
            result = self.results_by_code[code]
            for chunk in result.chunks:
                if document.printed_page_start <= chunk.page_number <= document.printed_page_end:
                    self.assertEqual(
                        chunk.printed_page_label,
                        str(chunk.page_number - document.printed_page_start + 1),
                    )

    def test_jsonl_output_is_deterministic_and_auditable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "chunks.jsonl"
            first_count = write_chunks_jsonl(self.result.chunks, output)
            first_bytes = output.read_bytes()
            second_count = write_chunks_jsonl(self.result.chunks, output)
            second_bytes = output.read_bytes()

            self.assertEqual(first_count, len(self.result.chunks))
            self.assertEqual(second_count, first_count)
            self.assertEqual(second_bytes, first_bytes)
            first_record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first_record["document_id"], self.document.document_id)
            self.assertTrue(first_record["citation"])
            self.assertTrue(first_record["source_url"].startswith("https://www.gov.uk/"))

    def test_checksum_mismatch_is_rejected_before_extraction(self):
        altered = replace(self.document, expected_sha256="0" * 64)
        with self.assertRaises(SourceIntegrityError):
            ingest_document(altered)


if __name__ == "__main__":
    unittest.main()
