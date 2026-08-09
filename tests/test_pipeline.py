import tempfile
import unittest
from pathlib import Path

from agent_pipeline import run_construction_agent_pipeline
from database import fetch_table
from validation import SiteNoteValidationError


class PipelineSafetyTests(unittest.TestCase):
    def test_irrelevant_note_is_rejected_and_audited_before_rag_or_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            with self.assertRaises(SiteNoteValidationError):
                run_construction_agent_pipeline(
                    "Hey team, do not forget the pizza party in the site trailer this Friday.",
                    db_path=db_path,
                )
            runs = fetch_table("pipeline_runs", db_path=db_path)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "REJECTED")
            self.assertEqual(fetch_table("design_revisions", db_path=db_path), [])


if __name__ == "__main__":
    unittest.main()
