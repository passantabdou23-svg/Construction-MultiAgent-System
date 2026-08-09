"""Evaluate deterministic grounding guards against real controlled RAG evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grounding import GroundingError, verify_design_against_site_note, verify_grounded_response
from rag_engine import ConstructionRAG
from schemas import DesignUpdatePayload, GroundedDesignResponse


class GroundingEvaluationError(ValueError):
    """Raised when the grounding evaluation definition is invalid."""


def load_cases(path: str | Path) -> dict[str, Any]:
    evaluation_path = Path(path).resolve()
    try:
        payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise GroundingEvaluationError(f"Invalid grounding evaluation file: {evaluation_path}") from error
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if payload.get("schema_version") != 1 or not isinstance(cases, list) or not cases:
        raise GroundingEvaluationError("Grounding evaluation requires schema_version 1 and cases")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise GroundingEvaluationError("Every grounding case requires a non-empty id")
    if len(ids) != len(set(ids)):
        raise GroundingEvaluationError("Grounding case ids must be unique")
    return payload


def _supported_response(case: dict[str, Any], chunk_id: str) -> GroundedDesignResponse:
    return GroundedDesignResponse.model_validate(
        {
            "evidence_status": "SUPPORTED",
            "reason": "The controlled passage supports this limited technical guidance claim.",
            "design": {
                "revision_id": "Rev-EVAL",
                "affected_element": "evaluation element",
                "requirements": [
                    {
                        "item_id": "EVAL-ITEM",
                        "material_type": "Other",
                        "specification": "Evaluation only",
                        "quantity": 1,
                        "unit": "item",
                    }
                ],
            },
            "claims": [
                {
                    "claim_id": "CLAIM-EVAL",
                    "claim_text": case["claim_text"],
                    "citations": [
                        {"chunk_id": chunk_id, "evidence_quote": case["evidence_quote"]}
                    ],
                }
            ],
        }
    )


def evaluate_cases(rag: ConstructionRAG, evaluation: dict[str, Any]) -> dict[str, Any]:
    definitions = {case["id"]: case for case in evaluation["cases"]}
    supported = {case_id: case for case_id, case in definitions.items() if case["kind"] == "supported"}
    if not supported:
        raise GroundingEvaluationError("At least one supported grounding case is required")
    resolved: dict[str, tuple[dict[str, Any], tuple[Any, ...], Any]] = {}
    results: list[dict[str, Any]] = []
    supported_passes: list[bool] = []
    guard_passes: list[bool] = []

    for case_id, case in supported.items():
        evidence = rag.query_candidates(case["query"], n_results=5)
        normalized_quote = " ".join(case["evidence_quote"].split()).casefold()
        record = next(
            (
                candidate
                for candidate in evidence
                if candidate.document_code == case["expected_document_code"]
                and normalized_quote in " ".join(candidate.text.split()).casefold()
            ),
            None,
        )
        if record is None:
            passed = False
            error = "Expected verbatim passage was not retrieved in the top five candidates"
        else:
            response = _supported_response(case, record.chunk_id)
            try:
                verify_grounded_response(response, evidence)
                passed, error = True, ""
            except GroundingError as grounding_error:
                passed, error = False, str(grounding_error)
            resolved[case_id] = (case, evidence, record)
        supported_passes.append(passed)
        results.append({"id": case_id, "kind": "supported", "passed": passed, "error": error})

    for case in (item for item in evaluation["cases"] if item["kind"] == "guard_rejection"):
        base_case, evidence, record = resolved[case["base_case"]]
        mutation = case["mutation"]
        response = _supported_response(base_case, record.chunk_id)
        mutated_evidence = evidence
        if mutation == "unknown_chunk":
            response.claims[0].citations[0].chunk_id = "unknown-evaluation-chunk"
        elif mutation == "altered_quote":
            response.claims[0].citations[0].evidence_quote = "This quote was fabricated for testing."
        elif mutation == "unsupported_number":
            response.claims[0].claim_text += " The mandatory width is 9999mm."
        elif mutation == "superseded_source":
            mutated_evidence = tuple(
                replace(candidate, status="superseded") if candidate.chunk_id == record.chunk_id else candidate
                for candidate in evidence
            )
        elif mutation == "conflicting_version":
            conflicting = replace(
                record,
                chunk_id=f"{record.chunk_id}-conflict",
                edition=f"{record.edition} conflicting copy",
                source_sha256="f" * 64,
            )
            mutated_evidence = (*evidence, conflicting)
        elif mutation == "model_refusal":
            response = GroundedDesignResponse(
                evidence_status="INSUFFICIENT_EVIDENCE",
                reason="The controlled evidence does not support a technical answer.",
            )
        elif mutation == "evidence_contaminated_design":
            response.design = DesignUpdatePayload.model_validate(
                {
                    "revision_id": "Rev-EVAL",
                    "affected_element": "foundation",
                    "requirements": [
                        {
                            "item_id": "CONCRETE-EVAL",
                            "material_type": "Concrete",
                            "specification": "C40",
                            "quantity": 25,
                            "unit": "m3",
                        },
                        {
                            "item_id": "CEMENT-FROM-EVIDENCE",
                            "material_type": "Cement",
                            "specification": "BS EN 197-1",
                            "quantity": 50,
                            "unit": "kg",
                        },
                    ],
                }
            )
        else:
            raise GroundingEvaluationError(f"Unsupported mutation: {mutation}")
        try:
            verify_grounded_response(response, mutated_evidence)
            if mutation == "evidence_contaminated_design":
                verify_design_against_site_note(
                    response.design,
                    "Site update Rev-EVAL: Need 25 m3 of C40 concrete for the foundation pour.",
                )
            passed, error = False, "Guard unexpectedly accepted the mutated response"
        except GroundingError as grounding_error:
            passed, error = True, str(grounding_error)
        guard_passes.append(passed)
        results.append(
            {"id": case["id"], "kind": "guard_rejection", "mutation": mutation, "passed": passed, "error": error}
        )

    metrics = {
        "supported_cases": len(supported_passes),
        "guard_rejection_cases": len(guard_passes),
        "supported_acceptance_rate": sum(supported_passes) / len(supported_passes),
        "guard_rejection_rate": sum(guard_passes) / len(guard_passes),
    }
    targets = evaluation.get("targets", {})
    targets_met = (
        metrics["supported_acceptance_rate"] >= float(targets.get("minimum_supported_acceptance_rate", 0))
        and metrics["guard_rejection_rate"] >= float(targets.get("minimum_guard_rejection_rate", 0))
    )
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "targets": targets,
        "targets_met": targets_met,
        "metrics": metrics,
        "results": results,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate grounded claim and citation guards.")
    parser.add_argument("--cases", type=Path, default=Path("grounding_evaluation/cases.json"))
    parser.add_argument("--output", type=Path, default=Path("rag_data/grounding_evaluation.json"))
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = evaluate_cases(
        ConstructionRAG(auto_index=False),
        load_cases(arguments.cases),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics = report["metrics"]
    print("Grounding guard evaluation complete")
    print(f"  Supported acceptance: {metrics['supported_acceptance_rate']:.1%}")
    print(f"  Guard rejection: {metrics['guard_rejection_rate']:.1%}")
    print(f"  Targets met: {report['targets_met']}")
    print(f"  Report: {arguments.output.resolve()}")
    if not report["targets_met"]:
        raise SystemExit("Grounding evaluation targets were not met")


if __name__ == "__main__":
    main()
