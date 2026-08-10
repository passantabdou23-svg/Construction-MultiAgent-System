"""Deterministic release and runtime readiness checks for the local prototype."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from audit import verify_audit_chain
from database import connect_db, init_db
from ingest_documents import load_manifest, verify_source
from pypdf import PdfReader
from settings import settings


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


REQUIRED_RELEASE_FILES = (
    ".env.example",
    ".gitignore",
    "DEPLOYMENT.md",
    "README.md",
    "RELEASE_CHECKLIST.md",
    "SECURITY.md",
    "GITHUB_ACTIONS_VALIDATION.template.yml",
    "app.py",
    "manage_backup.py",
    "manage_users.py",
    "requirements.txt",
)

REQUIRED_IGNORE_RULES = (
    "*.db",
    "*.db-shm",
    "*.db-wal",
    ".env",
    ".streamlit/secrets.toml",
    "backups/",
    "rag_data/",
    "release_reports/",
)


def _attempt(name: str, operation: Callable[[], str]) -> CheckResult:
    try:
        detail = operation()
    except Exception as error:  # The report must include every independent release check.
        return CheckResult(name=name, passed=False, detail=f"{type(error).__name__}: {error}")
    return CheckResult(name=name, passed=True, detail=detail)


def _check_python() -> str:
    current = sys.version_info[:2]
    if current != (3, 12):
        raise RuntimeError(f"Python 3.12 is required for the tested release; found {platform.python_version()}")
    return f"Python {platform.python_version()}"


def _requirements(project_root: Path) -> tuple[tuple[str, str], ...]:
    contract: list[tuple[str, str]] = []
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s]+)$")
    for line_number, raw_line in enumerate(
        (project_root / "requirements.txt").read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise RuntimeError(f"Unpinned requirement on line {line_number}: {line}")
        contract.append((match.group(1), match.group(2)))
    if not contract:
        raise RuntimeError("requirements.txt contains no pinned packages")
    return tuple(contract)


def _check_dependencies(project_root: Path) -> str:
    mismatches: list[str] = []
    for distribution, expected in _requirements(project_root):
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{distribution}=missing (required {expected})")
            continue
        if actual != expected:
            mismatches.append(f"{distribution}={actual} (required {expected})")
    if mismatches:
        raise RuntimeError("Dependency contract mismatch: " + "; ".join(mismatches))
    return f"{len(_requirements(project_root))} exact dependency pins installed"


def _check_release_files(project_root: Path) -> str:
    missing = [name for name in REQUIRED_RELEASE_FILES if not (project_root / name).is_file()]
    if missing:
        raise RuntimeError("Missing release files: " + ", ".join(missing))
    compile((project_root / "app.py").read_text(encoding="utf-8"), "app.py", "exec")
    return f"{len(REQUIRED_RELEASE_FILES)} release files present; app.py compiles"


def _check_ignore_contract(project_root: Path) -> str:
    lines = {
        line.strip()
        for line in (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = [rule for rule in REQUIRED_IGNORE_RULES if rule not in lines]
    if missing:
        raise RuntimeError("Missing ignore rules: " + ", ".join(missing))
    return f"{len(REQUIRED_IGNORE_RULES)} runtime/secret ignore rules present"


def _check_controlled_sources(project_root: Path) -> str:
    manifest_path = project_root / settings.rag_documents_path / "manifest.json"
    documents = load_manifest(manifest_path)
    total_pages = 0
    for document in documents:
        verify_source(document)
        pages = len(PdfReader(document.file_path).pages)
        if pages != document.expected_pages:
            raise RuntimeError(
                f"Page-count mismatch for {document.document_id}: expected {document.expected_pages}, found {pages}"
            )
        total_pages += pages
    return f"{len(documents)} controlled PDFs verified ({total_pages} pages)"


def _check_database(database_path: Path) -> str:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(database_path)
    connection = connect_db(database_path)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if integrity != ["ok"]:
        raise RuntimeError(f"SQLite integrity failed: {integrity}")
    if foreign_keys:
        raise RuntimeError(f"SQLite foreign keys failed: {foreign_keys}")
    audit = verify_audit_chain(database_path)
    return (
        f"SQLite integrity/foreign keys valid; audit events={audit['event_count']}; "
        f"head={audit['head_hash']}"
    )


def _check_runtime_rag(project_root: Path) -> str:
    from rag_engine import ConstructionRAG, load_chunk_records

    chunks_path = (project_root / settings.rag_chunks_path).resolve()
    index_path = (project_root / settings.rag_index_path).resolve()
    records = load_chunk_records(chunks_path)
    eligible = sum(record.metadata["retrieval_eligible"] is True for record in records)
    rag = ConstructionRAG(
        persist_path=index_path,
        chunks_path=chunks_path,
        auto_index=False,
    )
    indexed = rag.collection.count()
    if indexed != eligible:
        raise RuntimeError(f"RAG index count mismatch: expected {eligible}, found {indexed}")
    return f"{len(records)} auditable chunks; {indexed} retrieval-eligible vectors"


def _check_ollama() -> str:
    ollama = importlib.import_module("ollama")
    response = ollama.list()
    models = {
        str(getattr(model, "model", "") or getattr(model, "name", ""))
        for model in getattr(response, "models", [])
    }
    configured = settings.ollama_model
    if not any(name == configured or name.startswith(f"{configured}:") for name in models):
        raise RuntimeError(f"Configured Ollama model {configured!r} is not installed; available={sorted(models)}")
    return f"Ollama reachable; configured model {configured!r} installed"


def run_checks(
    project_root: str | Path,
    database_path: str | Path,
    *,
    runtime: bool = False,
    with_ollama: bool = False,
) -> list[CheckResult]:
    root = Path(project_root).resolve()
    database = Path(database_path).resolve()
    operations: list[tuple[str, Callable[[], str]]] = [
        ("python", _check_python),
        ("dependencies", lambda: _check_dependencies(root)),
        ("release_files", lambda: _check_release_files(root)),
        ("ignore_contract", lambda: _check_ignore_contract(root)),
        ("controlled_sources", lambda: _check_controlled_sources(root)),
        ("database", lambda: _check_database(database)),
    ]
    if runtime:
        operations.append(("runtime_rag", lambda: _check_runtime_rag(root)))
    if with_ollama:
        operations.append(("ollama", _check_ollama))
    return [_attempt(name, operation) for name, operation in operations]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate release and local runtime readiness")
    parser.add_argument("--project-root", default=Path(__file__).resolve().parent)
    parser.add_argument("--database", default=settings.database_path)
    parser.add_argument("--runtime", action="store_true", help="Require generated chunks and ChromaDB")
    parser.add_argument("--with-ollama", action="store_true", help="Require Ollama and configured model")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    results = run_checks(
        arguments.project_root,
        arguments.database,
        runtime=arguments.runtime,
        with_ollama=arguments.with_ollama,
    )
    if arguments.json:
        print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    else:
        print("Release readiness")
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"[{marker}] {result.name}: {result.detail}")
        print(f"Overall: {'PASS' if all(result.passed for result in results) else 'FAIL'}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
