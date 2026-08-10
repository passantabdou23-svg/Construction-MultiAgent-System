"""SQLite persistence with migrations, transactions, and foreign-key enforcement."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from audit import append_audit_event
from settings import settings


DB_NAME = settings.database_path


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN (
                    'PREPARER', 'DESIGN_REVIEWER', 'PROJECT_MANAGER', 'ADMIN'
                )),
                password_algorithm TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_parameters_json TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
                locked_until TEXT,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                password_changed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_chain_head (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                event_count INTEGER NOT NULL CHECK(event_count >= 0),
                head_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                sequence_number INTEGER NOT NULL UNIQUE CHECK(sequence_number > 0),
                actor_user_id TEXT,
                event_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                details_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                FOREIGN KEY(actor_user_id) REFERENCES users(user_id)
                    ON UPDATE CASCADE ON DELETE SET NULL
            );

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
                created_by_user_id TEXT,
                error_message TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE SET NULL,
                FOREIGN KEY(created_by_user_id) REFERENCES users(user_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS approval_requests (
                review_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                revision_id TEXT NOT NULL,
                prepared_by_user_id TEXT,
                decided_by_user_id TEXT,
                status TEXT NOT NULL CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED')),
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                reviewer_name TEXT,
                reviewer_role TEXT,
                review_comment TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                decided_at DATETIME,
                FOREIGN KEY(run_id) REFERENCES pipeline_runs(run_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                FOREIGN KEY(revision_id) REFERENCES design_revisions(revision_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY(prepared_by_user_id) REFERENCES users(user_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY(decided_by_user_id) REFERENCES users(user_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
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

        _add_column_if_missing(
            connection,
            "pipeline_runs",
            "workflow_stage",
            "TEXT NOT NULL DEFAULT 'EXECUTING'",
        )
        _add_column_if_missing(connection, "pipeline_runs", "created_by_user_id", "TEXT")
        _add_column_if_missing(connection, "approval_requests", "prepared_by_user_id", "TEXT")
        _add_column_if_missing(connection, "approval_requests", "decided_by_user_id", "TEXT")
        connection.execute(
            """
            UPDATE pipeline_runs
            SET workflow_stage = 'TERMINAL'
            WHERE status IN ('COMPLETED', 'REJECTED', 'FAILED')
            """
        )

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
    created_by_user_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, site_note, status, workflow_stage, created_by_user_id
            ) VALUES (?, ?, 'RUNNING', 'EXECUTING', ?)
            """,
            (run_id, site_note, created_by_user_id),
        )
        append_audit_event(
            connection,
            actor_user_id=created_by_user_id,
            event_type="PIPELINE_STARTED",
            target_type="PIPELINE_RUN",
            target_id=run_id,
            outcome="SUCCESS",
            occurred_at=_utc_timestamp(),
            details={"workflow_stage": "EXECUTING"},
        )


def create_approval_request(
    review_id: str,
    run_id: str,
    revision_id: str,
    prepared_by_user_id: str,
    payload_json: str,
    payload_sha256: str,
    *,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO approval_requests (
                review_id, run_id, revision_id, prepared_by_user_id, status,
                payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (
                review_id,
                run_id,
                revision_id,
                prepared_by_user_id,
                payload_json,
                payload_sha256,
            ),
        )
        cursor = connection.execute(
            """
            UPDATE pipeline_runs
            SET workflow_stage = 'AWAITING_APPROVAL', revision_id = ?, error_message = NULL,
                completed_at = NULL
            WHERE run_id = ? AND status = 'RUNNING' AND workflow_stage = 'EXECUTING'
            """,
            (revision_id, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Pipeline run {run_id} is not ready for approval")
        append_audit_event(
            connection,
            actor_user_id=prepared_by_user_id,
            event_type="REVIEW_PACKAGE_CREATED",
            target_type="APPROVAL_REQUEST",
            target_id=review_id,
            outcome="SUCCESS",
            occurred_at=_utc_timestamp(),
            details={
                "run_id": run_id,
                "revision_id": revision_id,
                "payload_sha256": payload_sha256,
            },
        )


def get_approval_request(
    review_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> dict[str, Any] | None:
    connection = connect_db(db_path)
    try:
        row = connection.execute(
            """
            SELECT ar.*, pr.site_note,
                   preparer.username AS preparer_username,
                   preparer.display_name AS preparer_display_name,
                   decider.username AS decider_username,
                   decider.display_name AS decider_display_name
            FROM approval_requests AS ar
            JOIN pipeline_runs AS pr USING (run_id)
            LEFT JOIN users AS preparer ON preparer.user_id = ar.prepared_by_user_id
            LEFT JOIN users AS decider ON decider.user_id = ar.decided_by_user_id
            WHERE ar.review_id = ?
            """,
            (review_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def list_pending_approval_requests(
    *,
    db_path: str | Path = DB_NAME,
) -> list[dict[str, Any]]:
    connection = connect_db(db_path)
    try:
        rows = connection.execute(
            """
            SELECT ar.review_id, ar.run_id, ar.revision_id, ar.prepared_by_user_id,
                   ar.status, ar.payload_sha256, ar.requested_at,
                   users.username AS preparer_username,
                   users.display_name AS preparer_display_name
            FROM approval_requests AS ar
            LEFT JOIN users ON users.user_id = ar.prepared_by_user_id
            WHERE ar.status = 'PENDING'
            ORDER BY ar.requested_at, ar.review_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def record_approval_decision(
    review_id: str,
    *,
    status: str,
    decided_by_user_id: str,
    review_comment: str,
    expected_payload_sha256: str,
    db_path: str | Path = DB_NAME,
) -> None:
    if status not in {"APPROVED", "REJECTED"}:
        raise ValueError(f"Unsupported approval status: {status}")
    with transaction(db_path) as connection:
        state = connection.execute(
            """
            SELECT ar.status AS review_status, ar.payload_sha256,
                   ar.prepared_by_user_id, pr.status AS run_status, pr.workflow_stage,
                   users.display_name AS reviewer_name, users.role AS reviewer_role,
                   users.is_active AS reviewer_is_active
            FROM approval_requests AS ar
            JOIN pipeline_runs AS pr USING (run_id)
            JOIN users ON users.user_id = ?
            WHERE ar.review_id = ?
            """,
            (decided_by_user_id, review_id),
        ).fetchone()
        if (
            state is None
            or state["review_status"] != "PENDING"
            or state["payload_sha256"] != expected_payload_sha256
            or state["run_status"] != "RUNNING"
            or state["workflow_stage"] != "AWAITING_APPROVAL"
            or not state["reviewer_is_active"]
            or state["reviewer_role"] not in {"DESIGN_REVIEWER", "PROJECT_MANAGER"}
            or not state["prepared_by_user_id"]
            or state["prepared_by_user_id"] == decided_by_user_id
        ):
            raise ValueError(
                "Approval request is missing, changed, unauthorized, or violates separation of duties"
            )
        cursor = connection.execute(
            """
            UPDATE approval_requests
            SET status = ?, decided_by_user_id = ?, reviewer_name = ?, reviewer_role = ?,
                review_comment = ?, decided_at = CURRENT_TIMESTAMP
            WHERE review_id = ? AND status = 'PENDING' AND payload_sha256 = ?
            """,
            (
                status,
                decided_by_user_id,
                state["reviewer_name"],
                state["reviewer_role"],
                review_comment,
                review_id,
                expected_payload_sha256,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Approval request is missing, already decided, or has changed")
        append_audit_event(
            connection,
            actor_user_id=decided_by_user_id,
            event_type=f"REVIEW_{status}",
            target_type="APPROVAL_REQUEST",
            target_id=review_id,
            outcome="SUCCESS",
            occurred_at=_utc_timestamp(),
            details={
                "payload_sha256": expected_payload_sha256,
                "reviewer_role": state["reviewer_role"],
            },
        )
        if status == "REJECTED":
            run_cursor = connection.execute(
                """
                UPDATE pipeline_runs
                SET status = 'REJECTED', workflow_stage = 'TERMINAL', error_message = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE run_id = (
                    SELECT run_id FROM approval_requests WHERE review_id = ?
                ) AND status = 'RUNNING' AND workflow_stage = 'AWAITING_APPROVAL'
                """,
                (f"Rejected by reviewer: {review_comment}", review_id),
            )
            if run_cursor.rowcount != 1:
                raise ValueError("The pipeline run is no longer awaiting this decision")


def get_design_review_state(
    revision_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> dict[str, Any] | None:
    connection = connect_db(db_path)
    try:
        revision = connection.execute(
            "SELECT * FROM design_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if revision is None:
            return None
        requirements = connection.execute(
            """
            SELECT item_id, material_type, specification, quantity, unit
            FROM material_requirements
            WHERE revision_id = ?
            ORDER BY item_id
            """,
            (revision_id,),
        ).fetchall()
        return {
            "design": {
                "revision_id": revision["revision_id"],
                "affected_element": revision["affected_element"],
                "requirements": [dict(row) for row in requirements],
            },
            "grounded_claims": json.loads(revision["grounded_claims_json"] or "[]"),
            "grounding": json.loads(revision["citation_verification_json"] or "{}"),
        }
    finally:
        connection.close()


def finish_pipeline_run(
    run_id: str,
    status: str,
    *,
    revision_id: str | None = None,
    error_message: str | None = None,
    actor_user_id: str | None = None,
    db_path: str | Path = DB_NAME,
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, workflow_stage = 'TERMINAL', revision_id = ?, error_message = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (status, revision_id, error_message, run_id),
        )
        append_audit_event(
            connection,
            actor_user_id=actor_user_id,
            event_type=f"PIPELINE_{status}",
            target_type="PIPELINE_RUN",
            target_id=run_id,
            outcome="SUCCESS" if status == "COMPLETED" else "STOPPED",
            occurred_at=_utc_timestamp(),
            details={"revision_id": revision_id, "error_message": error_message},
        )


TABLES = {
    "users",
    "audit_events",
    "design_revisions",
    "material_requirements",
    "procurement_records",
    "schedule_logs",
    "pipeline_runs",
    "approval_requests",
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
            "active_users": connection.execute(
                "SELECT COUNT(*) FROM users WHERE is_active = 1"
            ).fetchone()[0],
            "audit_events": connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
            "revisions": connection.execute("SELECT COUNT(*) FROM design_revisions").fetchone()[0],
            "materials": connection.execute("SELECT COUNT(*) FROM material_requirements").fetchone()[0],
            "quotes": connection.execute("SELECT COUNT(*) FROM procurement_records").fetchone()[0],
            "schedule_impacts": connection.execute("SELECT COUNT(*) FROM schedule_logs").fetchone()[0],
            "rejected_runs": connection.execute(
                "SELECT COUNT(*) FROM pipeline_runs WHERE status IN ('REJECTED', 'FAILED')"
            ).fetchone()[0],
            "pending_approvals": connection.execute(
                "SELECT COUNT(*) FROM approval_requests WHERE status = 'PENDING'"
            ).fetchone()[0],
        }
    finally:
        connection.close()
