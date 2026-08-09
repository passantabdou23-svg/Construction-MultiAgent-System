import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from database import fetch_table, init_db, save_design_revision
from design_agent import DesignAgentError, LocalLLMDesignAgent
from procurement_agent import LocalLLMProcurementAgent


class FakeOllamaClient:
    def __init__(self, payload):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls = []

    def chat(self, **kwargs):
        response_index = min(len(self.calls), len(self.payloads) - 1)
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self.payloads[response_index])}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_directory.name) / "agents.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_directory.cleanup()

    @staticmethod
    def evidence():
        return (
            SimpleNamespace(
                chunk_id="foundation-1",
                document_id="approved-document-a",
                document_code="A",
                title="Approved Document A: Structure",
                edition="controlled test edition",
                status="current",
                jurisdiction="England",
                page_number=38,
                printed_page_label="36",
                section="Foundations",
                clause="",
                text="Strip foundation width must safely distribute wall loads to the ground.",
                source_url="https://example.test/a",
                source_sha256="a" * 64,
                similarity=0.82,
            ),
        )

    @staticmethod
    def grounded_payload(revision_id="Rev-102"):
        return {
            "evidence_status": "SUPPORTED",
            "reason": "The retrieved structural passage is relevant to the foundation revision.",
            "design": {
                "revision_id": revision_id,
                "affected_element": "ground slab",
                "requirements": [
                    {
                        "item_id": "CONC-102",
                        "material_type": "Concrete",
                        "specification": "C40",
                        "quantity": 25,
                        "unit": "m3",
                    }
                ],
            },
            "claims": [
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
            ],
        }

    def test_design_agent_validates_and_saves_payload(self):
        client = FakeOllamaClient(self.grounded_payload())
        agent = LocalLLMDesignAgent(db_path=self.db_path, client=client)
        result = agent.execute(
            "Rev-102: Need 25 m3 of C40 concrete for the ground slab.",
            evidence=self.evidence(),
            expected_revision_id="Rev-102",
        )
        self.assertEqual(result["design"]["revision_id"], "Rev-102")
        self.assertEqual(result["grounding"]["status"], "VERIFIED")
        self.assertEqual(len(fetch_table("material_requirements", db_path=self.db_path)), 1)
        revision = fetch_table("design_revisions", db_path=self.db_path)[0]
        self.assertEqual(revision["grounding_status"], "VERIFIED")
        self.assertIn("foundation-1", revision["grounded_claims_json"])

    def test_design_agent_rejects_changed_revision_id(self):
        client = FakeOllamaClient(self.grounded_payload("Rev-WRONG"))
        agent = LocalLLMDesignAgent(db_path=self.db_path, client=client)
        with self.assertRaises(DesignAgentError):
            agent.execute(
                "Rev-102: Need 25 m3 concrete for the ground slab.",
                evidence=self.evidence(),
                expected_revision_id="Rev-102",
            )
        self.assertEqual(len(client.calls), 2)

    def test_design_agent_repairs_invalid_grounded_contract_once(self):
        invalid = self.grounded_payload()
        invalid["claims"] = []
        client = FakeOllamaClient([invalid, self.grounded_payload()])
        agent = LocalLLMDesignAgent(db_path=self.db_path, client=client)

        result = agent.execute(
            "Rev-102: Need 25 m3 of C40 concrete for the ground slab.",
            evidence=self.evidence(),
            expected_revision_id="Rev-102",
        )

        self.assertEqual(result["grounding"]["status"], "VERIFIED")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("deterministic validation", client.calls[1]["messages"][-1]["content"])

    def test_procurement_derives_total_and_future_date_locally(self):
        save_design_revision(
            {
                "revision_id": "Rev-200",
                "affected_element": "ground slab",
                "requirements": [
                    {
                        "item_id": "CONC-200",
                        "material_type": "Concrete",
                        "specification": "C40",
                        "quantity": 10,
                        "unit": "m3",
                    }
                ],
            },
            db_path=self.db_path,
        )
        client = FakeOllamaClient(
            {
                "item_id": "WRONG-ID",
                "supplier_name": "Planning supplier",
                "unit_cost": 120,
                "total_cost": 1,
                "lead_time_days": 5,
                "earliest_delivery_date": "2023-01-01",
            }
        )
        agent = LocalLLMProcurementAgent(
            db_path=self.db_path,
            client=client,
            today_provider=lambda: date(2026, 8, 9),
        )
        result = agent.execute("Rev-200")
        quote = result["quotes"][0]
        self.assertEqual(quote["item_id"], "CONC-200")
        self.assertEqual(quote["total_cost"], 1200.0)
        self.assertEqual(quote["earliest_delivery_date"], "2026-08-14")
        self.assertEqual(quote["quote_status"], "PENDING_VERIFICATION")


if __name__ == "__main__":
    unittest.main()
