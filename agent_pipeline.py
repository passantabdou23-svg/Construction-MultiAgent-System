"""Orchestration for the validated local construction-agent workflow."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable
from uuid import uuid4

from cpm_solver import CPMSolver
from database import (
    DB_NAME,
    finish_pipeline_run,
    init_db,
    save_schedule_log,
    start_pipeline_run,
)
from design_agent import LocalLLMDesignAgent
from procurement_agent import LocalLLMProcurementAgent
from rag_engine import ConstructionRAG
from schemas import DesignUpdatePayload, PipelineResult, ProcurementResult, ScheduleImpact
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
    procurement_agent: LocalLLMProcurementAgent | None = None,
    scheduler_agent: LocalLLMSchedulerAgent | None = None,
) -> dict:
    """Validate, execute, persist, and return a traceable pipeline result."""
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

        design_executor = design_agent or LocalLLMDesignAgent(db_path=db_path)
        design_data = design_executor.execute(
            validated_note.text,
            standard_context=standard_context,
            expected_revision_id=validated_note.revision_id,
        )
        design = DesignUpdatePayload.model_validate(design_data)
        revision_id = design.revision_id

        procurement_executor = procurement_agent or LocalLLMProcurementAgent(db_path=db_path)
        procurement_data = procurement_executor.execute(design.revision_id)

        scheduler_executor = scheduler_agent or LocalLLMSchedulerAgent(db_path=db_path)
        schedule_data = scheduler_executor.execute(
            design.revision_id,
            design.affected_element,
            procurement_data,
        )

        result = PipelineResult(
            run_id=run_id,
            status="COMPLETED",
            validation_message="Input and all agent outputs passed deterministic schema checks.",
            retrieved_standard=standard_context,
            design=design,
            procurement=ProcurementResult.model_validate(procurement_data),
            schedule=ScheduleImpact.model_validate(schedule_data),
        )
        finish_pipeline_run(
            run_id,
            "COMPLETED",
            revision_id=design.revision_id,
            db_path=db_path,
        )
        return result.model_dump(mode="json")
    except SiteNoteValidationError as error:
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


if __name__ == "__main__":
    example = "Site update Rev-905: Need 200 m3 of C40 concrete for the ground slab."
    print(run_construction_agent_pipeline(example))
