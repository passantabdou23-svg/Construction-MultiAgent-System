"""Evaluate persistent retrieval against labelled in-scope and out-of-scope questions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_engine import ConstructionRAG, RetrievedStandard
from settings import settings


class RAGEvaluationError(ValueError):
    """Raised when the labelled evaluation set is malformed."""


def load_evaluation_set(path: str | Path) -> dict[str, Any]:
    evaluation_path = Path(path).resolve()
    try:
        payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RAGEvaluationError(f"Evaluation set does not exist: {evaluation_path}") from error
    except json.JSONDecodeError as error:
        raise RAGEvaluationError(f"Evaluation set is not valid JSON: {evaluation_path}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RAGEvaluationError("Only evaluation schema_version 1 is supported")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RAGEvaluationError("Evaluation set must contain at least one case")
    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise RAGEvaluationError("Every evaluation case must be an object")
        case_id = case.get("id")
        query = case.get("query")
        kind = case.get("kind")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
            raise RAGEvaluationError("Every evaluation case needs a unique non-empty id")
        if not isinstance(query, str) or not query.strip():
            raise RAGEvaluationError(f"Evaluation case '{case_id}' needs a non-empty query")
        if kind not in {"positive", "negative"}:
            raise RAGEvaluationError(f"Evaluation case '{case_id}' has an invalid kind")
        if kind == "positive":
            pages = case.get("expected_pdf_pages")
            document_id = case.get("expected_document_id")
            if (
                not isinstance(document_id, str)
                or not document_id
                or not isinstance(pages, list)
                or not pages
                or any(not isinstance(page, int) or page < 1 for page in pages)
            ):
                raise RAGEvaluationError(
                    f"Positive case '{case_id}' needs a document id and positive PDF pages"
                )
        seen_ids.add(case_id)
    return payload


def _is_relevant(case: dict[str, Any], candidate: RetrievedStandard) -> bool:
    return (
        candidate.document_id == case["expected_document_id"]
        and candidate.page_number in case["expected_pdf_pages"]
    )


def evaluate_cases(
    rag: ConstructionRAG,
    evaluation: dict[str, Any],
    *,
    top_k: int,
    threshold: float,
) -> dict[str, Any]:
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    results: list[dict[str, Any]] = []
    positive_ranks: list[int | None] = []
    positive_acceptances: list[bool] = []
    negative_rejections: list[bool] = []
    for case in evaluation["cases"]:
        candidates = rag.query_candidates(case["query"], n_results=top_k)
        top_similarity = candidates[0].similarity
        accepted = top_similarity >= threshold
        rank: int | None = None
        if case["kind"] == "positive":
            rank = next(
                (position for position, candidate in enumerate(candidates, start=1) if _is_relevant(case, candidate)),
                None,
            )
            positive_ranks.append(rank)
            positive_acceptances.append(accepted)
            passed = rank is not None and accepted
        else:
            rejected = not accepted
            negative_rejections.append(rejected)
            passed = rejected
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "query": case["query"],
                "passed": passed,
                "accepted": accepted,
                "relevant_rank": rank,
                "top_similarity": round(top_similarity, 6),
                "candidates": [
                    {
                        "rank": position,
                        "chunk_id": candidate.chunk_id,
                        "document_id": candidate.document_id,
                        "pdf_page": candidate.page_number,
                        "printed_page": candidate.printed_page_label,
                        "section": candidate.section,
                        "similarity": round(candidate.similarity, 6),
                        "source_url": candidate.source_url,
                    }
                    for position, candidate in enumerate(candidates, start=1)
                ],
            }
        )

    positive_count = len(positive_ranks)
    negative_count = len(negative_rejections)
    if positive_count == 0 or negative_count == 0:
        raise RAGEvaluationError("Evaluation requires both positive and negative cases")
    metrics = {
        "positive_cases": positive_count,
        "negative_cases": negative_count,
        "hit_at_k": sum(rank is not None for rank in positive_ranks) / positive_count,
        "top_1_accuracy": sum(rank == 1 for rank in positive_ranks) / positive_count,
        "mean_reciprocal_rank": sum(0 if rank is None else 1 / rank for rank in positive_ranks) / positive_count,
        "positive_acceptance_rate": sum(positive_acceptances) / positive_count,
        "negative_rejection_rate": sum(negative_rejections) / negative_count,
    }
    targets = evaluation.get("targets", {})
    targets_met = (
        metrics["hit_at_k"] >= float(targets.get("minimum_hit_at_k", 0))
        and metrics["positive_acceptance_rate"] >= float(targets.get("minimum_positive_acceptance_rate", 0))
        and metrics["negative_rejection_rate"] >= float(targets.get("minimum_negative_rejection_rate", 0))
    )
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_model": rag.embedding_model_id,
        "embedding_model_sha256": rag.embedding_model_sha256,
        "collection_name": rag.collection_name,
        "distance_metric": "cosine",
        "threshold": threshold,
        "top_k": top_k,
        "targets": targets,
        "targets_met": targets_met,
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "results": results,
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the persistent construction RAG index.")
    parser.add_argument("--questions", type=Path, default=Path("rag_evaluation/questions.json"))
    parser.add_argument("--output", type=Path, default=Path("rag_data/retrieval_evaluation.json"))
    parser.add_argument("--top-k", type=int, default=settings.rag_top_k)
    parser.add_argument("--threshold", type=float, default=settings.rag_minimum_similarity)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    rag = ConstructionRAG(auto_index=False, minimum_similarity=arguments.threshold)
    evaluation = load_evaluation_set(arguments.questions)
    report = evaluate_cases(rag, evaluation, top_k=arguments.top_k, threshold=arguments.threshold)
    output_path = write_report(report, arguments.output)
    metrics = report["metrics"]
    print("Persistent RAG evaluation complete")
    print(f"  Hit@{arguments.top_k}: {metrics['hit_at_k']:.1%}")
    print(f"  Top-1 accuracy: {metrics['top_1_accuracy']:.1%}")
    print(f"  Mean reciprocal rank: {metrics['mean_reciprocal_rank']:.3f}")
    print(f"  Positive acceptance: {metrics['positive_acceptance_rate']:.1%}")
    print(f"  Negative rejection: {metrics['negative_rejection_rate']:.1%}")
    print(f"  Threshold: {arguments.threshold:.3f}")
    print(f"  Targets met: {report['targets_met']}")
    print(f"  Report: {output_path}")
    if not report["targets_met"]:
        raise SystemExit("Retrieval evaluation targets were not met")


if __name__ == "__main__":
    main()
