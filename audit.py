"""Append-only SHA-256 audit-chain utilities for local governance events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


ZERO_HASH = "0" * 64


class AuditIntegrityError(RuntimeError):
    """Raised when the stored audit chain no longer verifies."""


def _canonical_event_json(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_event_json(event).encode("utf-8")).hexdigest()


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    target_type: str,
    target_id: str,
    outcome: str,
    occurred_at: str,
    actor_user_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one event and update the chain head inside the caller's transaction."""
    head = connection.execute(
        "SELECT event_count, head_hash FROM audit_chain_head WHERE singleton_id = 1"
    ).fetchone()
    previous_hash = head["head_hash"] if head is not None else ZERO_HASH
    sequence_number = (head["event_count"] if head is not None else 0) + 1
    details_json = json.dumps(
        details or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    event = {
        "event_id": str(uuid4()),
        "sequence_number": sequence_number,
        "actor_user_id": actor_user_id,
        "event_type": event_type,
        "target_type": target_type,
        "target_id": target_id,
        "outcome": outcome,
        "details_json": details_json,
        "occurred_at": occurred_at,
        "previous_hash": previous_hash,
    }
    event_hash = calculate_event_hash(event)
    connection.execute(
        """
        INSERT INTO audit_events (
            event_id, sequence_number, actor_user_id, event_type, target_type,
            target_id, outcome, details_json, occurred_at, previous_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            sequence_number,
            actor_user_id,
            event_type,
            target_type,
            target_id,
            outcome,
            details_json,
            occurred_at,
            previous_hash,
            event_hash,
        ),
    )
    connection.execute(
        """
        INSERT INTO audit_chain_head (singleton_id, event_count, head_hash, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(singleton_id) DO UPDATE SET
            event_count = excluded.event_count,
            head_hash = excluded.head_hash,
            updated_at = excluded.updated_at
        """,
        (sequence_number, event_hash, occurred_at),
    )
    return {**event, "event_hash": event_hash}


def verify_audit_chain(db_path: str | Path) -> dict[str, Any]:
    """Verify event ordering, links, event hashes, and the persisted chain head."""
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM audit_events ORDER BY sequence_number"
        ).fetchall()
        head = connection.execute(
            "SELECT event_count, head_hash FROM audit_chain_head WHERE singleton_id = 1"
        ).fetchone()
    finally:
        connection.close()

    previous_hash = ZERO_HASH
    for expected_sequence, row in enumerate(rows, start=1):
        if row["sequence_number"] != expected_sequence:
            raise AuditIntegrityError("Audit event sequence is missing or out of order")
        if row["previous_hash"] != previous_hash:
            raise AuditIntegrityError(
                f"Audit event {row['event_id']} does not link to the previous event"
            )
        event = {
            "event_id": row["event_id"],
            "sequence_number": row["sequence_number"],
            "actor_user_id": row["actor_user_id"],
            "event_type": row["event_type"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "outcome": row["outcome"],
            "details_json": row["details_json"],
            "occurred_at": row["occurred_at"],
            "previous_hash": row["previous_hash"],
        }
        if calculate_event_hash(event) != row["event_hash"]:
            raise AuditIntegrityError(f"Audit event {row['event_id']} was modified")
        previous_hash = row["event_hash"]

    expected_count = len(rows)
    expected_head = previous_hash
    if head is None:
        if expected_count:
            raise AuditIntegrityError("Audit chain head is missing")
    elif head["event_count"] != expected_count or head["head_hash"] != expected_head:
        raise AuditIntegrityError("Audit chain head does not match the stored events")

    return {
        "valid": True,
        "event_count": expected_count,
        "head_hash": expected_head,
    }
