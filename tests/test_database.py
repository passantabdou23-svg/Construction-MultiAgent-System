import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import (
    connect_db,
    database_counts,
    fetch_table,
    get_material_requirements,
    init_db,
    save_design_revision,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp_directory.cleanup()

    @staticmethod
    def design_payload():
        return {
            "revision_id": "Rev-DB-1",
            "affected_element": "ground slab",
            "requirements": [
                {
                    "item_id": "CONC-01",
                    "material_type": "Concrete",
                    "specification": "C40",
                    "quantity": 25.0,
                    "unit": "m3",
                },
                {
                    "item_id": "REBAR-01",
                    "material_type": "Rebar",
                    "specification": "B500B",
                    "quantity": 2.0,
                    "unit": "tonnes",
                },
            ],
        }

    def test_foreign_keys_are_enabled_on_every_connection(self):
        connection = connect_db(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            connection.close()

    def test_all_material_requirements_are_persisted(self):
        save_design_revision(self.design_payload(), db_path=self.db_path)
        rows = get_material_requirements("Rev-DB-1", db_path=self.db_path)
        self.assertEqual({row["item_id"] for row in rows}, {"CONC-01", "REBAR-01"})
        self.assertEqual(database_counts(self.db_path)["materials"], 2)

    def test_invalid_foreign_key_is_rejected(self):
        connection = connect_db(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError), connection:
                connection.execute(
                    """
                    INSERT INTO material_requirements
                        (revision_id, item_id, material_type, specification, quantity, unit)
                    VALUES ('Rev-MISSING', 'X', 'Concrete', 'C40', 1, 'm3')
                    """
                )
        finally:
            connection.close()

    def test_populated_legacy_database_migrates_without_data_loss(self):
        legacy_path = Path(self.temp_directory.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE design_revisions (
                revision_id TEXT PRIMARY KEY,
                affected_element TEXT,
                item_id TEXT,
                material_type TEXT,
                specification TEXT,
                quantity REAL,
                unit TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE procurement_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT,
                item_id TEXT,
                supplier_name TEXT,
                unit_cost REAL,
                total_cost REAL,
                lead_time_days INTEGER,
                earliest_delivery_date TEXT,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
            );
            CREATE TABLE schedule_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT,
                task_id TEXT,
                is_critical_path BOOLEAN,
                delay_days INTEGER,
                recommended_action TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
            );
            CREATE TABLE pipeline_runs (
                run_id TEXT PRIMARY KEY,
                site_note TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('RUNNING', 'COMPLETED', 'REJECTED', 'FAILED')),
                revision_id TEXT,
                error_message TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            );
            INSERT INTO design_revisions
                (revision_id, affected_element, item_id, material_type, specification, quantity, unit)
            VALUES ('Rev-OLD', 'foundation', 'CONC-OLD', 'Concrete', 'C40', 5, 'm3');
            INSERT INTO procurement_records
                (revision_id, item_id, supplier_name, unit_cost, total_cost, lead_time_days, earliest_delivery_date)
            VALUES ('Rev-OLD', 'CONC-OLD', 'Legacy supplier', 100, 500, 5, '2026-08-14');
            INSERT INTO pipeline_runs
                (run_id, site_note, status, revision_id, completed_at)
            VALUES ('legacy-run', 'Legacy validated site note', 'COMPLETED', 'Rev-OLD', CURRENT_TIMESTAMP);
            """
        )
        connection.commit()
        connection.close()

        init_db(legacy_path)

        self.assertEqual(database_counts(legacy_path)["revisions"], 1)
        self.assertEqual(database_counts(legacy_path)["materials"], 1)
        migrated = fetch_table("procurement_records", db_path=legacy_path)[0]
        self.assertEqual(migrated["quote_status"], "PENDING_VERIFICATION")
        self.assertIsNotNone(migrated["created_at"])
        revision = fetch_table("design_revisions", db_path=legacy_path)[0]
        self.assertEqual(revision["grounding_status"], "UNVERIFIED_LEGACY")
        self.assertEqual(revision["grounded_claims_json"], "[]")
        self.assertEqual(revision["citation_verification_json"], "{}")
        legacy_run = fetch_table("pipeline_runs", db_path=legacy_path)[0]
        self.assertEqual(legacy_run["run_id"], "legacy-run")
        self.assertEqual(legacy_run["workflow_stage"], "TERMINAL")
        self.assertEqual(fetch_table("approval_requests", db_path=legacy_path), [])


if __name__ == "__main__":
    unittest.main()
