"""Release-time SQLite integrity, backup, and recovery utilities."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from audit import verify_audit_chain


BACKUP_MANIFEST_SCHEMA_VERSION = 1


class ReleaseOperationError(RuntimeError):
    """Raised when a backup, restore, or integrity contract is not satisfied."""


@dataclass(frozen=True)
class DatabaseIntegrity:
    database_path: str
    size_bytes: int
    sha256: str
    audit_event_count: int
    audit_head_hash: str


@dataclass(frozen=True)
class BackupResult:
    backup_path: str
    manifest_path: str
    source_path: str
    integrity: DatabaseIntegrity


@dataclass(frozen=True)
class RestoreResult:
    restored_database_path: str
    source_backup_path: str
    pre_restore_backup_path: str | None
    integrity: DatabaseIntegrity


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_integrity(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            raise ReleaseOperationError(
                f"SQLite integrity check failed for {path}: {integrity_rows}"
            )
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise ReleaseOperationError(
                f"SQLite foreign-key check failed for {path}: {foreign_key_rows}"
            )
    finally:
        connection.close()


def inspect_database(path: str | Path) -> DatabaseIntegrity:
    database_path = Path(path).resolve()
    if not database_path.is_file():
        raise ReleaseOperationError(f"Database does not exist: {database_path}")
    _sqlite_integrity(database_path)
    try:
        audit = verify_audit_chain(database_path)
    except (sqlite3.DatabaseError, RuntimeError) as error:
        raise ReleaseOperationError(
            f"Audit chain verification failed for {database_path}: {error}"
        ) from error
    return DatabaseIntegrity(
        database_path=str(database_path),
        size_bytes=database_path.stat().st_size,
        sha256=file_sha256(database_path),
        audit_event_count=int(audit["event_count"]),
        audit_head_hash=str(audit["head_hash"]),
    )


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_suffix(f"{backup_path.suffix}.manifest.json")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_database_backup(
    source_path: str | Path,
    output_directory: str | Path,
    *,
    label: str = "backup",
) -> BackupResult:
    source = Path(source_path).resolve()
    source_integrity = inspect_database(source)
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = "".join(character for character in label if character.isalnum() or character in "-_")
    if not safe_label:
        raise ReleaseOperationError("Backup label must contain a letter or number")
    backup_path = output / f"{source.stem}_{safe_label}_{timestamp}_{uuid4().hex[:8]}.db"
    partial_path = output / f".{backup_path.name}.partial"

    source_connection = sqlite3.connect(str(source))
    destination_connection = sqlite3.connect(str(partial_path))
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    except sqlite3.DatabaseError as error:
        raise ReleaseOperationError(f"SQLite online backup failed: {error}") from error
    finally:
        destination_connection.close()
        source_connection.close()

    try:
        partial_integrity = inspect_database(partial_path)
        os.replace(partial_path, backup_path)
    finally:
        partial_path.unlink(missing_ok=True)

    backup_integrity = DatabaseIntegrity(
        database_path=str(backup_path),
        size_bytes=backup_path.stat().st_size,
        sha256=file_sha256(backup_path),
        audit_event_count=partial_integrity.audit_event_count,
        audit_head_hash=partial_integrity.audit_head_hash,
    )
    manifest_path = _manifest_path(backup_path)
    _write_json_atomic(
        manifest_path,
        {
            "schema_version": BACKUP_MANIFEST_SCHEMA_VERSION,
            "created_at": utc_timestamp(),
            "source_database_name": source.name,
            "backup_file_name": backup_path.name,
            "database_size_bytes": backup_integrity.size_bytes,
            "database_sha256": backup_integrity.sha256,
            "audit_event_count": backup_integrity.audit_event_count,
            "audit_head_hash": backup_integrity.audit_head_hash,
            "source_database_sha256_at_backup": source_integrity.sha256,
        },
    )
    return BackupResult(
        backup_path=str(backup_path),
        manifest_path=str(manifest_path),
        source_path=str(source),
        integrity=backup_integrity,
    )


def validate_database_backup(backup_path: str | Path) -> DatabaseIntegrity:
    backup = Path(backup_path).resolve()
    manifest_path = _manifest_path(backup)
    if not backup.is_file():
        raise ReleaseOperationError(f"Backup does not exist: {backup}")
    if not manifest_path.is_file():
        raise ReleaseOperationError(f"Backup manifest does not exist: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReleaseOperationError(f"Backup manifest is invalid JSON: {manifest_path}") from error
    if manifest.get("schema_version") != BACKUP_MANIFEST_SCHEMA_VERSION:
        raise ReleaseOperationError("Unsupported backup manifest schema")
    if manifest.get("backup_file_name") != backup.name:
        raise ReleaseOperationError("Backup manifest filename does not match the database")

    integrity = inspect_database(backup)
    expected = {
        "database_size_bytes": integrity.size_bytes,
        "database_sha256": integrity.sha256,
        "audit_event_count": integrity.audit_event_count,
        "audit_head_hash": integrity.audit_head_hash,
    }
    mismatches = {
        key: (manifest.get(key), value)
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    if mismatches:
        details = ", ".join(
            f"{key}: manifest={stored!r}, actual={actual!r}"
            for key, (stored, actual) in mismatches.items()
        )
        raise ReleaseOperationError(f"Backup manifest verification failed ({details})")
    return integrity


def restore_database_backup(
    backup_path: str | Path,
    destination_path: str | Path,
    *,
    recovery_directory: str | Path,
) -> RestoreResult:
    backup = Path(backup_path).resolve()
    destination = Path(destination_path).resolve()
    if backup == destination:
        raise ReleaseOperationError("Backup and restore destination must be different files")
    backup_integrity = validate_database_backup(backup)
    destination.parent.mkdir(parents=True, exist_ok=True)

    pre_restore: BackupResult | None = None
    if destination.exists():
        pre_restore = create_database_backup(
            destination,
            recovery_directory,
            label="pre-restore",
        )

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.restore")
    try:
        shutil.copy2(backup, temporary)
        copied_integrity = inspect_database(temporary)
        if copied_integrity.sha256 != backup_integrity.sha256:
            raise ReleaseOperationError("Restored temporary copy does not match the validated backup")
        os.replace(temporary, destination)
    except OSError as error:
        raise ReleaseOperationError(
            "Database restore failed. Stop Streamlit and other database users, then retry. "
            f"The destination was not replaced: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)

    restored_integrity = inspect_database(destination)
    return RestoreResult(
        restored_database_path=str(destination),
        source_backup_path=str(backup),
        pre_restore_backup_path=(pre_restore.backup_path if pre_restore else None),
        integrity=restored_integrity,
    )


def result_as_dict(result: BackupResult | RestoreResult | DatabaseIntegrity) -> dict[str, Any]:
    return asdict(result)
