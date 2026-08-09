import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from agent_pipeline import review_construction_agent_pipeline, run_construction_agent_pipeline
from approval import ApprovalIntegrityError, ApprovalStateError
from database import connect_db, fetch_table, save_design_revision
from grounding import GroundingRefusalError
from schemas import ReviewDecisionInput
from validation import SiteNoteValidationError


class FakeRAG:
    def __init__(self):
        self.queries = []

    def query_many(self, query):
        self.queries.append(query)
        return (
            SimpleNamespace(
                chunk_id="foundation-1", document_id="approved-a", document_code="A",
                title="Approved Document A", edition="test edition", status="current",
                jurisdiction="England", page_number=38, printed_page_label="36",
                section="Foundations", clause="", similarity=0.82,
                source_url="https://example.test/a", source_sha256="a" * 64,
                text="Strip foundation width must safely distribute wall loads to the ground.",
                citation="[Approved Document A, PDF p. 38]\nFoundation passage",
            ),
            SimpleNamespace(
                chunk_id="foundation-2", document_id="approved-a", document_code="A",
                title="Approved Document A", edition="test edition", status="current",
                jurisdiction="England", page_number=39, printed_page_label="37",
                section="Foundations", clause="", similarity=0.75,
                source_url="https://example.test/a", source_sha256="a" * 64,
                text="Foundation design should account for the imposed building loads.",
                citation="[Approved Document A, PDF p. 39]\nSecond passage",
            ),
        )


class FakeDesignAgent:
    def __init__(self, db_path):
        self.db_path = db_path
        self.evidence = None

    def execute(self, note, *, evidence, expected_revision_id):
        self.evidence = evidence
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
        grounded_claims = [
            {
                "claim_id": "CLAIM-1",
                "claim_text": "Foundation width should safely distribute wall loads.",
                "citations": [
                    {
                        "chunk_id": "foundation-1",
                        "evidence_quote": "Strip foundation width must safely distribute wall loads to the ground.",
                    }
                ],
            }
        ]
        grounding = {
            "status": "VERIFIED",
            "verified_claim_count": 1,
            "verified_citation_count": 1,
            "cited_chunk_ids": ["foundation-1"],
            "notes": ["Fake grounded result for orchestration testing."],
        }
        save_design_revision(
            payload,
            grounding_status="VERIFIED",
            grounded_claims=grounded_claims,
            citation_verification=grounding,
            db_path=self.db_path,
        )
        return {
            "design": payload,
            "grounded_claims": grounded_claims,
            "grounding": grounding,
        }


class FakeProcurementAgent:
    def __init__(self):
        self.calls = []

    def execute(self, revision_id):
        self.calls.append(revision_id)
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
    def __init__(self):
        self.calls = []

    def execute(self, revision_id, affected_element, procurement_data):
        self.calls.append((revision_id, affected_element))
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


class RefusingDesignAgent:
    def execute(self, note, *, evidence, expected_revision_id):
        raise GroundingRefusalError(
            "INSUFFICIENT_EVIDENCE",
            "The retrieved passages do not support a technical design claim.",
        )


class PipelineSafetyTests(unittest.TestCase):
    def test_review_decision_contract_requires_valid_role_and_rejection_reason(self):
        with self.assertRaises(ValidationError):
            ReviewDecisionInput(
                reviewer_name="Dr Reviewer",
                reviewer_role="Anonymous",
                decision="APPROVE",
            )
        with self.assertRaises(ValidationError):
            ReviewDecisionInput(
                reviewer_name="Dr Reviewer",
                reviewer_role="Design engineer",
                decision="REJECT",
                comment="No",
            )

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

    def test_verified_package_waits_for_human_approval_before_downstream_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            rag = FakeRAG()
            design_agent = FakeDesignAgent(db_path)

            pending = run_construction_agent_pipeline(
                "Site update Rev-401: Need 25 m3 of C40 concrete for the foundation pour.",
                db_path=db_path,
                rag=rag,
                design_agent=design_agent,
            )

            self.assertEqual(pending["status"], "AWAITING_APPROVAL")
            self.assertEqual(len(rag.queries), 1)
            self.assertIn("PDF p. 38", pending["retrieved_standard"])
            self.assertIn("PDF p. 39", pending["retrieved_standard"])
            self.assertEqual(len(design_agent.evidence), 2)
            self.assertEqual(pending["grounding"]["status"], "VERIFIED")
            self.assertEqual(
                pending["grounded_claims"][0]["citations"][0]["chunk_id"],
                "foundation-1",
            )
            runs = fetch_table("pipeline_runs", db_path=db_path)
            self.assertEqual(runs[0]["status"], "RUNNING")
            self.assertEqual(runs[0]["workflow_stage"], "AWAITING_APPROVAL")
            self.assertEqual(len(fetch_table("approval_requests", db_path=db_path)), 1)
            self.assertEqual(fetch_table("procurement_records", db_path=db_path), [])
            self.assertEqual(fetch_table("schedule_logs", db_path=db_path), [])

            procurement = FakeProcurementAgent()
            scheduler = FakeSchedulerAgent()
            completed = review_construction_agent_pipeline(
                pending["review"]["review_id"],
                {
                    "reviewer_name": "Dr Reviewer",
                    "reviewer_role": "Design engineer",
                    "decision": "APPROVE",
                    "comment": "Reviewed against the site note and cited evidence.",
                },
                db_path=db_path,
                procurement_agent=procurement,
                scheduler_agent=scheduler,
            )

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["review"]["status"], "APPROVED")
            self.assertEqual(procurement.calls, ["Rev-401"])
            self.assertEqual(scheduler.calls, [("Rev-401", "foundation")])
            self.assertEqual(
                fetch_table("pipeline_runs", db_path=db_path)[0]["status"],
                "COMPLETED",
            )

    def test_rejection_is_terminal_and_never_runs_procurement_or_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            pending = run_construction_agent_pipeline(
                "Site update Rev-403: Need 25 m3 of C40 concrete for the foundation pour.",
                db_path=db_path,
                rag=FakeRAG(),
                design_agent=FakeDesignAgent(db_path),
            )
            procurement = FakeProcurementAgent()
            scheduler = FakeSchedulerAgent()

            rejected = review_construction_agent_pipeline(
                pending["review"]["review_id"],
                {
                    "reviewer_name": "Dr Reviewer",
                    "reviewer_role": "Design engineer",
                    "decision": "REJECT",
                    "comment": "The site note requires clarification before procurement.",
                },
                db_path=db_path,
                procurement_agent=procurement,
                scheduler_agent=scheduler,
            )

            self.assertEqual(rejected["status"], "REJECTED")
            self.assertEqual(rejected["review"]["status"], "REJECTED")
            self.assertEqual(procurement.calls, [])
            self.assertEqual(scheduler.calls, [])
            self.assertEqual(
                fetch_table("pipeline_runs", db_path=db_path)[0]["status"],
                "REJECTED",
            )

            with self.assertRaises(ApprovalStateError):
                review_construction_agent_pipeline(
                    pending["review"]["review_id"],
                    {
                        "reviewer_name": "Second Reviewer",
                        "reviewer_role": "Project manager",
                        "decision": "APPROVE",
                    },
                    db_path=db_path,
                    procurement_agent=procurement,
                    scheduler_agent=scheduler,
                )

    def test_changed_design_is_blocked_before_recording_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            pending = run_construction_agent_pipeline(
                "Site update Rev-404: Need 25 m3 of C40 concrete for the foundation pour.",
                db_path=db_path,
                rag=FakeRAG(),
                design_agent=FakeDesignAgent(db_path),
            )
            connection = connect_db(db_path)
            with connection:
                connection.execute(
                    "UPDATE material_requirements SET quantity = 99 WHERE revision_id = 'Rev-404'"
                )
            connection.close()

            with self.assertRaises(ApprovalIntegrityError):
                review_construction_agent_pipeline(
                    pending["review"]["review_id"],
                    {
                        "reviewer_name": "Dr Reviewer",
                        "reviewer_role": "Design engineer",
                        "decision": "APPROVE",
                    },
                    db_path=db_path,
                    procurement_agent=FakeProcurementAgent(),
                    scheduler_agent=FakeSchedulerAgent(),
                )
            review = fetch_table("approval_requests", db_path=db_path)[0]
            self.assertEqual(review["status"], "PENDING")

    def test_changed_review_payload_is_blocked_before_recording_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            pending = run_construction_agent_pipeline(
                "Site update Rev-405: Need 25 m3 of C40 concrete for the foundation pour.",
                db_path=db_path,
                rag=FakeRAG(),
                design_agent=FakeDesignAgent(db_path),
            )
            connection = connect_db(db_path)
            with connection:
                connection.execute(
                    "UPDATE approval_requests SET payload_json = payload_json || ' '"
                )
            connection.close()

            with self.assertRaises(ApprovalIntegrityError):
                review_construction_agent_pipeline(
                    pending["review"]["review_id"],
                    {
                        "reviewer_name": "Dr Reviewer",
                        "reviewer_role": "Design engineer",
                        "decision": "APPROVE",
                    },
                    db_path=db_path,
                    procurement_agent=FakeProcurementAgent(),
                    scheduler_agent=FakeSchedulerAgent(),
                )
            self.assertEqual(
                fetch_table("approval_requests", db_path=db_path)[0]["status"],
                "PENDING",
            )

    def test_grounding_refusal_stops_procurement_and_is_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "pipeline.db")
            with self.assertRaises(GroundingRefusalError):
                run_construction_agent_pipeline(
                    "Site update Rev-402: Need 25 m3 of C40 concrete for the foundation pour.",
                    db_path=db_path,
                    rag=FakeRAG(),
                    design_agent=RefusingDesignAgent(),
                )

            runs = fetch_table("pipeline_runs", db_path=db_path)
            self.assertEqual(runs[0]["status"], "REJECTED")
            self.assertIn("INSUFFICIENT_EVIDENCE", runs[0]["error_message"])
            self.assertEqual(fetch_table("design_revisions", db_path=db_path), [])
            self.assertEqual(fetch_table("procurement_records", db_path=db_path), [])


if __name__ == "__main__":
    unittest.main()
