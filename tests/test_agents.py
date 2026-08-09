import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from database import fetch_table, init_db, save_design_revision
from design_agent import DesignAgentError, LocalLLMDesignAgent
from procurement_agent import LocalLLMProcurementAgent


class FakeOllamaClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self.payload)}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_directory.name) / "agents.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_design_agent_validates_and_saves_payload(self):
        client = FakeOllamaClient(
            {
                "revision_id": "Rev-102",
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
            }
        )
        agent = LocalLLMDesignAgent(db_path=self.db_path, client=client)
        result = agent.execute(
            "Rev-102: Need 25 m3 concrete for the ground slab.",
            standard_context="Slab demonstration summary",
            expected_revision_id="Rev-102",
        )
        self.assertEqual(result["revision_id"], "Rev-102")
        self.assertEqual(len(fetch_table("material_requirements", db_path=self.db_path)), 1)

    def test_design_agent_rejects_changed_revision_id(self):
        client = FakeOllamaClient(
            {
                "revision_id": "Rev-WRONG",
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
            }
        )
        agent = LocalLLMDesignAgent(db_path=self.db_path, client=client)
        with self.assertRaises(DesignAgentError):
            agent.execute(
                "Rev-102: Need 25 m3 concrete for the ground slab.",
                standard_context="Slab demonstration summary",
                expected_revision_id="Rev-102",
            )

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
