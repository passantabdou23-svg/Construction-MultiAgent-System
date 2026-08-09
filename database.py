"""SQLite persistence with migrations, transactions, and foreign-key enforcement."""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from settings import settings


DB_NAME = settings.database_path


def connect_db(db_path: str | Path = DB_NAME) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction(db_path: str | Path = DB_NAME) -> Iterator[sqlite3.Connection]:
    connection = connect_db(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _existing_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    declaration: str,
) -> None:
    if column_name not in _existing_columns(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}")


def init_db(db_path: str | Path = DB_NAME) -> None:
    """Create or safely migrate the local audit database."""
    with transaction(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS design_revisions (
                revision_id TEXT PRIMARY KEY,
                affected_element TEXT NOT NULL,
                item_id TEXT,
                material_type TEXT,
                specification TEXT,
                quantity REAL,
                unit TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS material_requirements (
                revision_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                material_type TEXT NOT NULL,
                specification TEXT NOT NULL,
                quantity REAL NOT NULL CHECK(quantity > 0),
                unit TEXT NOT NULL,
                PRIMARY KEY (revision_id, item_id),
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS procurement_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                unit_cost REAL NOT NULL CHECK(unit_cost > 0),
                total_cost REAL NOT NULL CHECK(total_cost > 0),
                lead_time_days INTEGER NOT NULL CHECK(lead_time_days >= 0),
                earliest_delivery_date TEXT NOT NULL,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS schedule_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                is_critical_path BOOLEAN NOT NULL,
                delay_days INTEGER NOT NULL CHECK(delay_days >= 0),
                recommended_action TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                site_note TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('RUNNING', 'COMPLETED', 'REJECTED', 'FAILED')),
                revision_id TEXT,
                error_message TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE SET NULL
            );
            """
        )

        # Non-destructive migrations for databases created by earlier versions.
        for name, declaration in (
            ("source_note", "TEXT"),
            ("standard_reference", "TEXT"),
            ("validation_status", "TEXT NOT NULL DEFAULT 'VALIDATED'"),
            ("grounding_status", "TEXT NOT NULL DEFAULT 'UNVERIFIED_LEGACY'"),
            ("grounded_claims_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("citation_verification_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            _add_column_if_missing(connection, "design_revisions", name, declaration)

        for name, declaration in (
            ("quote_status", "TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION'"),
            ("source", "TEXT NOT NULL DEFAULT 'LLM_ESTIMATE_UNVERIFIED'"),
            ("created_at", "DATETIME"),
        ):
            _add_column_if_missing(connection, "procurement_records", name, declaration)
        connection.execute(
            "UPDATE procurement_records SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

        for name, declaration in (
            ("affected_task", "TEXT"),
            ("baseline_duration_days", "INTEGER"),
            ("projected_duration_days", "INTEGER"),
            ("projected_completion_date", "TEXT"),
        ):
            _add_column_if_missing(connection, "schedule_logs", name, declaration)

        # Preserve old single-material rows in the normalized child table.
        connection.execute(
            """
            INSERT OR IGNORE INTO material_requirements
                (revision_id, item_id, material_type, specification, quantity, unit)
            SELECT revision_id, item_id, material_type, specification, quantity, unit
            FROM design_revisions
            WHERE item_id IS NOT NULL
              AND material_type IS NOT NULL
              AND specification IS NOT NULL
              AND quantity > 0
              AND unit IS NOT NULL
            """
        )


def save_design_revision(
    data: dict[str, Any],
    *,
    source_note: str = "",
    standard_reference: str = "",
    grounding_status: str = "UNVERIFIED_LEGACY",
    grounded_claims: Sequence[dict[str, Any]] | None = None,
    citation_verification: dict[str, Any] | None = None,
    db_path: str | Path = DB_NAME,
) -> None:
    requirements = data["requirements"]
    first = requirements[0]
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO design_revisions (
                revision_id, affected_element, item_id, material_type, specification,
                quantity, unit, source_note, standard_reference, validation_status,
                grounding_status, grounded_claims_json, citation_verification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VALIDATED', ?, ?, ?)
            ON CONFLICT(revision_id) DO UPDATE SET
                affected_element = excluded.affected_element,
                item_id = excluded.item_id,
                material_type = excluded.material_type,
                specification = excluded.specification,
                quantity = excluded.quantity,
                unit = excluded.unit,
                source_note = excluded.source_note,
                standard_reference = excluded.standard_reference,
                validation_status = 'VALIDATED',
                grounding_status = excluded.grounding_status,
                grounded_claims_json = excluded.grounded_claims_json,
                citation_verification_json = excluded.citation_verification_json,
                timestamp = CURRENT_TIMESTAMP
            """,
            (
                data["revision_id"],
                data["affected_element"],
                first["item_id"],
                first["material_type"],
                first["specification"],
                first["quantity"],
                first["unit"],
                source_note,
                standard_reference,
                grounding_status,
                json.dumps(list(grounded_claims or []), sort_keys=True),
                json.dumps(citation_verification or {}, sort_keys=True),
            ),
        )
        connection.execute(
            "DELETE FROM material_requirements WHERE revision_id = ?",
            (data["revision_id"],),
        )
        connection.executemany(
            """
            INSERT INTO material_requirements
                (revision_id, item_id, material_type, specification, quantity, unit)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    data["revision_id"],
                    requirement["item_id"],
                    requirement["material_type"],
                    requirement["specification"],
                    requirement["quantity"],
                    requirement["unit"],
                )
                for requirement in requirements
            ],
        )


def get_material_requirements(
    revision_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> list[dict[str, Any]]:
    connection = connect_db(db_path)
    try:
        rows = connection.execute(
            """
            SELECT mr.*, dr.affected_element
            FROM material_requirements AS mr
            JOIN design_revisions AS dr USING (revision_id)
            WHERE mr.revision_id = ?
            ORDER BY mr.item_id
            """,
            (revision_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def save_procurement_records(
    revision_id: str,
    quotes: Sequence[dict[str, Any]],
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute("DELETE FROM procurement_records WHERE revision_id = ?", (revision_id,))
        connection.executemany(
            """
            INSERT INTO procurement_records (
                revision_id, item_id, supplier_name, unit_cost, total_cost,
                lead_time_days, earliest_delivery_date, quote_status, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                (
                    revision_id,
                    quote["item_id"],
                    quote["supplier_name"],
                    quote["unit_cost"],
                    quote["total_cost"],
                    quote["lead_time_days"],
                    str(quote["earliest_delivery_date"]),
                    quote["quote_status"],
                    quote["source"],
                )
                for quote in quotes
            ],
        )


def save_procurement_record(
    revision_id: str,
    data: dict[str, Any],
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    """Backward-compatible wrapper for one validated quote."""
    normalized = {
        "item_id": data["item_id"],
        "supplier_name": data["supplier_name"],
        "unit_cost": data["unit_cost"],
        "total_cost": data["total_cost"],
        "lead_time_days": data.get("lead_time_days", data.get("actual_lead_days")),
        "earliest_delivery_date": data["earliest_delivery_date"],
        "quote_status": data.get("quote_status", "PENDING_VERIFICATION"),
        "source": data.get("source", "LLM_ESTIMATE_UNVERIFIED"),
    }
    save_procurement_records(revision_id, [normalized], db_path=db_path)


def save_schedule_log(
    revision_id: str,
    data: dict[str, Any],
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute("DELETE FROM schedule_logs WHERE revision_id = ?", (revision_id,))
        connection.execute(
            """
            INSERT INTO schedule_logs (
                revision_id, task_id, is_critical_path, delay_days,
                recommended_action, affected_task, baseline_duration_days,
                projected_duration_days, projected_completion_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                data["task_id"],
                data["is_critical_path"],
                data["delay_days"],
                data["recommended_action"],
                data.get("affected_task"),
                data.get("baseline_duration_days"),
                data.get("projected_duration_days"),
                str(data.get("projected_completion_date", "")),
            ),
        )


def start_pipeline_run(
    run_id: str,
    site_note: str,
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            "INSERT INTO pipeline_runs (run_id, site_note, status) VALUES (?, ?, 'RUNNING')",
            (run_id, site_note),
        )


def finish_pipeline_run(
    run_id: str,
    status: str,
    *,
    revision_id: str | None = None,
    error_message: str | None = None,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, revision_id = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (status, revision_id, error_message, run_id),
        )


TABLES = {
    "design_revisions",
    "material_requirements",
    "procurement_records",
    "schedule_logs",
    "pipeline_runs",
}


def fetch_table(table_name: str, *, db_path: str | Path = DB_NAME) -> list[dict[str, Any]]:
    if table_name not in TABLES:
        raise ValueError(f"Unsupported table: {table_name}")
    connection = connect_db(db_path)
    try:
        rows = connection.execute(f"SELECT * FROM {table_name} ORDER BY rowid DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def database_counts(db_path: str | Path = DB_NAME) -> dict[str, int]:
    connection = connect_db(db_path)
    try:
        return {
            "revisions": connection.execute("SELECT COUNT(*) FROM design_revisions").fetchone()[0],
            "materials": connection.execute("SELECT COUNT(*) FROM material_requirements").fetchone()[0],
            "quotes": connection.execute("SELECT COUNT(*) FROM procurement_records").fetchone()[0],
            "schedule_impacts": connection.execute("SELECT COUNT(*) FROM schedule_logs").fetchone()[0],
            "rejected_runs": connection.execute(
                "SELECT COUNT(*) FROM pipeline_runs WHERE status IN ('REJECTED', 'FAILED')"
            ).fetchone()[0],
        }
    finally:
        connection.close()
