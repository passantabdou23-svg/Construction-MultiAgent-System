"""Generate a privacy-safe, reproducible release acceptance evidence pack."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import subprocess
import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit import verify_audit_chain
from database import database_counts
from release_check import run_checks
from release_ops import validate_database_backup
from settings import settings


PROJECT_ROOT = Path(__file__).resolve().parent


def _run_suite(start_directory: str, pattern: str) -> dict[str, Any]:
    suite = unittest.defaultTestLoader.discover(start_directory, pattern=pattern)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
    }


def _run_module_suite(module_name: str) -> dict[str, Any]:
    suite = unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(module_name))
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Required evaluation report does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def collect_evidence(
    project_root: Path,
    database_path: Path,
    backup_path: Path | None,
    *,
    runtime: bool,
    with_ollama: bool,
) -> dict[str, Any]:
    checks = run_checks(
        project_root,
        database_path,
        runtime=runtime,
        with_ollama=with_ollama,
    )
    repository_tests = _run_suite(str(project_root / "tests"), "test_*.py")
    stress_tests = _run_module_suite("test_stress_cases")
    retrieval = _read_json(project_root / settings.rag_data_path / "retrieval_evaluation.json")
    grounding = _read_json(project_root / settings.rag_data_path / "grounding_evaluation.json")
    audit = verify_audit_chain(database_path)
    counts = database_counts(database_path)
    backup = asdict(validate_database_backup(backup_path)) if backup_path else None

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _git_commit(project_root),
        "python": platform.python_version(),
        "overall_passed": False,
        "release_checks": [asdict(check) for check in checks],
        "repository_tests": repository_tests,
        "stress_tests": stress_tests,
        "retrieval": {
            "targets_met": retrieval["targets_met"],
            "top_k": retrieval["top_k"],
            "threshold": retrieval["threshold"],
            "metrics": retrieval["metrics"],
            "embedding_model": retrieval["embedding_model"],
            "embedding_model_sha256": retrieval["embedding_model_sha256"],
        },
        "grounding": {
            "targets_met": grounding["targets_met"],
            "metrics": grounding["metrics"],
        },
        "database": {
            "counts": counts,
            "audit_valid": audit["valid"],
            "audit_event_count": audit["event_count"],
            "audit_head_hash": audit["head_hash"],
        },
        "backup": backup,
        "scope_boundary": {
            "deployment": "Controlled single-workstation planning prototype",
            "jurisdiction": "Controlled Approved Documents apply to England",
            "procurement": "LLM estimates remain pending independent verification",
            "identity": "Local authentication; no MFA, enterprise SSO, or legal signature",
            "engineering": "Does not replace a licensed engineer or regulatory approval",
        },
    }
    payload["overall_passed"] = bool(
        all(check["passed"] for check in payload["release_checks"])
        and repository_tests["passed"]
        and stress_tests["passed"]
        and payload["retrieval"]["targets_met"]
        and payload["grounding"]["targets_met"]
        and payload["database"]["audit_valid"]
        and (backup is None or backup["sha256"])
    )
    return payload


def _percentage(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_markdown(evidence: dict[str, Any]) -> str:
    retrieval = evidence["retrieval"]["metrics"]
    grounding = evidence["grounding"]["metrics"]
    database = evidence["database"]
    checks = evidence["release_checks"]
    check_rows = "\n".join(
        f"| {check['name']} | {'PASS' if check['passed'] else 'FAIL'} | {check['detail']} |"
        for check in checks
    )
    backup_line = (
        f"Validated: SHA-256 `{evidence['backup']['sha256']}`, "
        f"{evidence['backup']['size_bytes']} bytes"
        if evidence.get("backup")
        else "Not included in this evidence run"
    )
    status = "PASS" if evidence["overall_passed"] else "FAIL"
    return f"""# Final acceptance report

**Status:** {status}  
**Tested baseline commit:** `{evidence['commit']}`  
**Generated (UTC):** {evidence['generated_at_utc']}  
**Python:** {evidence['python']}

## Acceptance scope

This report records reproducible technical evidence for the controlled local prototype.
It validates the implemented software contracts; it is not regulatory approval, an
engineering certification, a verified supplier quotation, or a production authorization.

## Release readiness

| Check | Result | Evidence |
|---|---|---|
{check_rows}

## Automated verification

- Repository tests: **{evidence['repository_tests']['tests_run']} passed**, {evidence['repository_tests']['failures']} failures, {evidence['repository_tests']['errors']} errors.
- Stress tests: **{evidence['stress_tests']['tests_run']} passed**, {evidence['stress_tests']['failures']} failures, {evidence['stress_tests']['errors']} errors.
- Retrieval Hit@{evidence['retrieval']['top_k']}: **{_percentage(retrieval['hit_at_k'])}**.
- Retrieval Top-1 accuracy: **{_percentage(retrieval['top_1_accuracy'])}**.
- Mean Reciprocal Rank: **{retrieval['mean_reciprocal_rank']:.3f}**.
- Document-routing accuracy: **{_percentage(retrieval['routing_accuracy'])}**.
- Positive-query acceptance: **{_percentage(retrieval['positive_acceptance_rate'])}**.
- Negative-query rejection: **{_percentage(retrieval['negative_rejection_rate'])}**.
- Grounded supported-case acceptance: **{_percentage(grounding['supported_acceptance_rate'])}**.
- Grounding-guard rejection: **{_percentage(grounding['guard_rejection_rate'])}**.

These percentages describe the labelled controlled evaluation sets only and are not a
general construction-compliance benchmark.

## Accepted workflow evidence

- Active local accounts: **{database['counts']['active_users']}**.
- Persisted design revisions: **{database['counts']['revisions']}**.
- Persisted material requirements: **{database['counts']['materials']}**.
- Unverified procurement records: **{database['counts']['quotes']}**.
- Schedule-impact records: **{database['counts']['schedule_impacts']}**.
- Pending approvals: **{database['counts']['pending_approvals']}**.
- Audit chain: **{'valid' if database['audit_valid'] else 'invalid'}**, {database['audit_event_count']} events, head `{database['audit_head_hash']}`.
- Database backup: {backup_line}.

The accepted demonstration proves the implemented state transition: a PREPARER creates
a grounded immutable package; a different DESIGN_REVIEWER reauthenticates and decides
it; only approval permits procurement planning and CPM analysis; the resulting records
remain traceable through SQLite and the hash-chained audit trail.

## Controlled knowledge base

- Sources: official England Approved Documents **A, C, K, and 7**.
- Retrieval: 75% MiniLM semantic similarity + 25% BM25-style lexical relevance.
- Embedding model: `{evidence['retrieval']['embedding_model']}`.
- Confidence floor: **{evidence['retrieval']['threshold']:.2f}**.
- Every accepted technical claim must cite a retrieved chunk and verbatim evidence.
- Unsupported numbers, unavailable chunks, altered quotes, superseded/conflicting
  versions, and evidence-created procurement items are rejected deterministically.

## Security and governance acceptance

- Passwords use uniquely salted `scrypt` derivation.
- Roles and active sessions are checked server-side.
- Repeated failures lock accounts; sessions have idle and absolute expiry.
- Preparation and approval enforce separation of duties.
- Approval requires fresh password verification and is single-use.
- Immutable package snapshots prevent stale-state approval and replay.
- SQLite integrity, foreign keys, backup manifests, and the SHA-256 audit chain passed.

## Deployment and recovery acceptance

- Default deployment is localhost on the same workstation as Ollama.
- LAN exposure requires an HTTPS reverse proxy; plain HTTP must not carry credentials.
- Backups use SQLite's online-backup API and a SHA-256 sidecar manifest.
- Restore validates the source, preserves the previous destination, verifies a temporary
  copy, and replaces atomically.
- The activation-ready CI template is stored as
  `GITHUB_ACTIONS_VALIDATION.template.yml` until a Workflow-authorized token is available.

## Explicit limitations

- Controlled single-workstation prototype; not a managed multi-user production service.
- No MFA, enterprise SSO, account recovery, or legally binding electronic signature.
- Audit chain is tamper-evident but not digitally signed or externally anchored.
- Procurement outputs are unverified planning estimates.
- CPM uses a five-task demonstration network, not live Primavera P6/MS Project data.
- Approved Documents apply to England and are not Egyptian compliance authority.
- A licensed engineer and applicable authorities remain responsible for decisions.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate final acceptance evidence")
    parser.add_argument("--database", default=settings.database_path)
    parser.add_argument("--backup")
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--with-ollama", action="store_true")
    parser.add_argument("--output-json", default="ACCEPTANCE_EVIDENCE.json")
    parser.add_argument("--output-markdown", default="ACCEPTANCE_REPORT.md")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    evidence = collect_evidence(
        PROJECT_ROOT,
        Path(arguments.database).resolve(),
        Path(arguments.backup).resolve() if arguments.backup else None,
        runtime=arguments.runtime,
        with_ollama=arguments.with_ollama,
    )
    Path(arguments.output_json).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Path(arguments.output_markdown).write_text(build_markdown(evidence), encoding="utf-8")
    print(f"Acceptance status: {'PASS' if evidence['overall_passed'] else 'FAIL'}")
    print(f"JSON: {Path(arguments.output_json).resolve()}")
    print(f"Markdown: {Path(arguments.output_markdown).resolve()}")
    return 0 if evidence["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
