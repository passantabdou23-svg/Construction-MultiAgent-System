import tempfile
import unittest
from pathlib import Path

from agent_pipeline import run_construction_agent_pipeline
from database import fetch_table, save_design_revision
from validation import SiteNoteValidationError


class FakeRAG:
    def __init__(self):
        self.queries = []

    def query_many(self, query):
        self.queries.append(query)
        return (
            type("Standard", (), {"citation": "[Approved Document A, PDF p. 38]\nFoundation passage"})(),
            type("Standard", (), {"citation": "[Approved Document A, PDF p. 39]\nSecond passage"})(),
        )


class FakeDesignAgent:
    def __init__(self, db_path):
        self.db_path = db_path
        self.standard_context = None

    def execute(self, note, *, standard_context, expected_revision_id):
        self.standard_context = standard_context
        payload = {
            "revision_id": expected_revision_id,
            "affected_element": "foundation",
            "requirements": [
                {
                    "item_id": "CONC-401",
                    "material_type": "Concrete",
                    "specification": "C40",
                    "quantity": 25,
                    "unit": "m3",
                }
            ],
        }
        save_design_revision(payload, db_path=self.db_path)
        return payload


class FakeProcurementAgent:
    def execute(self, revision_id):
        return {
            "revision_id": revision_id,
            "status": "PENDING_VERIFICATION",
            "quotes": [
                {
                    "item_id": "CONC-401",
                    "supplier_name": "Unverified planning supplier",
                    "unit_cost": 100,
                    "total_cost": 2500,
                    "lead_time_days": 3,
                    "earliest_delivery_date": "2026-08-12",
                    "quote_status": "PENDING_VERIFICATION",
                    "source": "LLM_ESTIMATE_UNVERIFIED",
                }
            ],
            "maximum_lead_time_days": 3,
        }


class FakeSchedulerAgent:
    def execute(self, revision_id, affected_element, procurement_data):
        return {
            "revision_id": revision_id,
            "affected_task": "TASK-FOUNDATION",
            "task_id": f"IMPACT-{revision_id}-FOUNDATION",
            "is_critical_path": True,
            "delay_days": 3,
            "baseline_duration_days": 38,
            "projected_duration_days": 41,
            "projected_completion_date": "2026-09-19",
            "recommended_action": "Human review required.",
        }


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

    def test_top_k_citations_are_passed_to_the_design_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            rag = FakeRAG()
            design_agent = FakeDesignAgent(db_path)

            result = run_construction_agent_pipeline(
                "Site update Rev-401: Need 25 m3 of C40 concrete for the foundation pour.",
                db_path=db_path,
                rag=rag,
                design_agent=design_agent,
                procurement_agent=FakeProcurementAgent(),
                scheduler_agent=FakeSchedulerAgent(),
            )

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(len(rag.queries), 1)
            self.assertIn("PDF p. 38", result["retrieved_standard"])
            self.assertIn("PDF p. 39", result["retrieved_standard"])
            self.assertEqual(design_agent.standard_context, result["retrieved_standard"])
            runs = fetch_table("pipeline_runs", db_path=db_path)
            self.assertEqual(runs[0]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
