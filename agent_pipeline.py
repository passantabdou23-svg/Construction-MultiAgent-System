"""Orchestration for the validated local construction-agent workflow."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable
from uuid import uuid4

from cpm_solver import CPMSolver
from approval import (
    canonical_payload_json,
    calculate_payload_sha256,
    load_pending_approval,
    review_record_from_row,
)
from database import (
    DB_NAME,
    create_approval_request,
    finish_pipeline_run,
    get_approval_request,
    init_db,
    record_approval_decision,
    save_schedule_log,
    start_pipeline_run,
)
from design_agent import LocalLLMDesignAgent
from grounding import GroundingRefusalError
from procurement_agent import LocalLLMProcurementAgent
from rag_engine import ConstructionRAG
from schemas import (
    ApprovalPayload,
    PendingApprovalResult,
    PipelineResult,
    ProcurementResult,
    RejectedApprovalResult,
    RetrievedEvidence,
    ReviewDecisionInput,
    ScheduleImpact,
    VerifiedDesignResult,
)
from validation import SiteNoteValidationError, validate_site_note


ELEMENT_TASK_RULES = (
    (("excavation", "site prep", "site preparation", "groundwork"), "TASK-SITE-PREP"),
    (("foundation", "footing", "pile", "raft"), "TASK-FOUNDATION"),
    (("column", "columns", "vertical structure"), "TASK-COLUMNS"),
    (("slab", "beam", "floor", "deck", "reinforcement"), "TASK-SLAB"),
    (("finish", "finishing", "inspection", "paint", "cladding"), "TASK-FINISHING"),
)


def map_element_to_task(affected_element: str) -> str:
    normalized = affected_element.casefold()
    for terms, task_id in ELEMENT_TASK_RULES:
        if any(term in normalized for term in terms):
            return task_id
    raise ValueError(
        f"Cannot map affected element '{affected_element}' to the demonstration CPM schedule"
    )


class LocalLLMSchedulerAgent:
    def __init__(
        self,
        *,
        db_path: str = DB_NAME,
        cpm_engine: CPMSolver | None = None,
        today_provider: Callable[[], date] = date.today,
    ):
        self.db_path = db_path
        self.cpm_engine = cpm_engine or CPMSolver()
        self.today_provider = today_provider

    def execute(
        self,
        revision_id: str,
        affected_element: str,
        procurement_data: dict[str, Any],
    ) -> dict:
        procurement = ProcurementResult.model_validate(procurement_data)
        affected_task = map_element_to_task(affected_element)
        cpm_result = self.cpm_engine.calculate_cpm_impact(
            affected_task,
            lead_time_delay=procurement.maximum_lead_time_days,
        )
        completion_date = self.today_provider() + timedelta(
            days=cpm_result["total_project_duration_days"]
        )
        action = (
            f"Review the unverified {procurement.maximum_lead_time_days}-day procurement lead time "
            f"for {affected_task}. The calculated critical path is "
            f"{' -> '.join(cpm_result['critical_path_tasks'])}; obtain engineer and procurement "
            "approval before changing the baseline programme."
        )
        report = ScheduleImpact(
            revision_id=revision_id,
            affected_task=affected_task,
            task_id=f"IMPACT-{revision_id}-{affected_task.removeprefix('TASK-')}",
            is_critical_path=cpm_result["is_critical"],
            delay_days=cpm_result["delay_added"],
            baseline_duration_days=cpm_result["baseline_project_duration_days"],
            projected_duration_days=cpm_result["total_project_duration_days"],
            projected_completion_date=completion_date,
            recommended_action=action,
        )
        serialized = report.model_dump(mode="json")
        save_schedule_log(revision_id, serialized, db_path=self.db_path)
        return serialized


def run_construction_agent_pipeline(
    site_note: str | None = None,
    *,
    db_path: str = DB_NAME,
    rag: ConstructionRAG | None = None,
    design_agent: LocalLLMDesignAgent | None = None,
) -> dict:
    """Validate and ground a revision, then persist an immutable human-review package."""
    init_db(db_path)
    run_id = str(uuid4())
    raw_note = (site_note or "").strip()
    start_pipeline_run(run_id, raw_note or "<empty>", db_path=db_path)
    revision_id: str | None = None

    try:
        validated_note = validate_site_note(raw_note)
        rag_engine = rag or ConstructionRAG()
        standards = rag_engine.query_many(validated_note.text)
        standard_context = "\n\n".join(standard.citation for standard in standards)
        retrieved_evidence = [
            RetrievedEvidence(
                chunk_id=standard.chunk_id,
                document_id=standard.document_id,
                document_code=standard.document_code,
                title=standard.title,
                edition=standard.edition,
                status=standard.status,
                jurisdiction=standard.jurisdiction,
                page_number=standard.page_number,
                printed_page_label=standard.printed_page_label,
                section=standard.section,
                clause=standard.clause,
                similarity=standard.similarity,
                source_url=standard.source_url,
            )
            for standard in standards
        ]

        design_executor = design_agent or LocalLLMDesignAgent(db_path=db_path)
        verified_design_data = design_executor.execute(
            validated_note.text,
            evidence=standards,
            expected_revision_id=validated_note.revision_id,
        )
        verified_design = VerifiedDesignResult.model_validate(verified_design_data)
        design = verified_design.design
        revision_id = design.revision_id

        approval_payload = ApprovalPayload(
            site_note=validated_note.text,
            validation_message=(
                "Input, site-note facts, grounded claims, and citations passed deterministic "
                "verification and are awaiting a recorded human decision."
            ),
            retrieved_standard=standard_context,
            retrieved_evidence=retrieved_evidence,
            grounded_claims=verified_design.grounded_claims,
            grounding=verified_design.grounding,
            design=design,
        )
        payload_json = canonical_payload_json(approval_payload)
        payload_sha256 = calculate_payload_sha256(payload_json)
        review_id = str(uuid4())
        create_approval_request(
            review_id,
            run_id,
            design.revision_id,
            payload_json,
            payload_sha256,
            db_path=db_path,
        )
        review_row = get_approval_request(review_id, db_path=db_path)
        if review_row is None:
            raise RuntimeError("The approval request was not persisted")
        return PendingApprovalResult(
            **approval_payload.model_dump(mode="json"),
            run_id=run_id,
            status="AWAITING_APPROVAL",
            review=review_record_from_row(review_row),
        ).model_dump(mode="json")
    except (SiteNoteValidationError, GroundingRefusalError) as error:
        finish_pipeline_run(run_id, "REJECTED", error_message=str(error), db_path=db_path)
        raise
    except Exception as error:
        finish_pipeline_run(
            run_id,
            "FAILED",
            revision_id=revision_id,
            error_message=str(error),
            db_path=db_path,
        )
        raise


def review_construction_agent_pipeline(
    review_id: str,
    decision: ReviewDecisionInput | dict[str, Any],
    *,
    db_path: str = DB_NAME,
    procurement_agent: LocalLLMProcurementAgent | None = None,
    scheduler_agent: LocalLLMSchedulerAgent | None = None,
) -> dict:
    """Record one human decision and run downstream agents only after approval."""
    init_db(db_path)
    decision_input = ReviewDecisionInput.model_validate(decision)
    row, payload = load_pending_approval(review_id, db_path=db_path)
    decision_status = "APPROVED" if decision_input.decision == "APPROVE" else "REJECTED"
    record_approval_decision(
        review_id,
        status=decision_status,
        reviewer_name=decision_input.reviewer_name,
        reviewer_role=decision_input.reviewer_role,
        review_comment=decision_input.comment,
        expected_payload_sha256=row["payload_sha256"],
        db_path=db_path,
    )
    decided_row = get_approval_request(review_id, db_path=db_path)
    if decided_row is None:
        raise RuntimeError("The recorded approval decision could not be reloaded")
    review = review_record_from_row(decided_row)

    if decision_status == "REJECTED":
        return RejectedApprovalResult(
            run_id=row["run_id"],
            status="REJECTED",
            validation_message="The verified design package was rejected; no procurement or schedule agents ran.",
            review=review,
            design=payload.design,
        ).model_dump(mode="json")

    try:
        procurement_executor = procurement_agent or LocalLLMProcurementAgent(db_path=db_path)
        procurement_data = procurement_executor.execute(payload.design.revision_id)
        scheduler_executor = scheduler_agent or LocalLLMSchedulerAgent(db_path=db_path)
        schedule_data = scheduler_executor.execute(
            payload.design.revision_id,
            payload.design.affected_element,
            procurement_data,
        )
        result = PipelineResult(
            run_id=row["run_id"],
            status="COMPLETED",
            validation_message=(
                "The verified design package received a recorded human approval; downstream "
                "procurement planning and CPM impact analysis completed."
            ),
            review=review,
            retrieved_standard=payload.retrieved_standard,
            retrieved_evidence=payload.retrieved_evidence,
            grounded_claims=payload.grounded_claims,
            grounding=payload.grounding,
            design=payload.design,
            procurement=ProcurementResult.model_validate(procurement_data),
            schedule=ScheduleImpact.model_validate(schedule_data),
        )
        finish_pipeline_run(
            row["run_id"],
            "COMPLETED",
            revision_id=payload.design.revision_id,
            db_path=db_path,
        )
        return result.model_dump(mode="json")
    except Exception as error:
        finish_pipeline_run(
            row["run_id"],
            "FAILED",
            revision_id=payload.design.revision_id,
            error_message=str(error),
            db_path=db_path,
        )
        raise


if __name__ == "__main__":
    example = "Site update Rev-905: Need 200 m3 of C40 concrete for the ground slab."
    print(run_construction_agent_pipeline(example))
