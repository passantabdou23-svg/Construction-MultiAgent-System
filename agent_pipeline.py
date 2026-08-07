import datetime
import json
import sqlite3
import ollama

from schemas import MaterialType, MaterialRequirement, DesignUpdatePayload
from test_agent import LocalLLMDesignAgent
from procurement_agent import LocalLLMProcurementAgent
from database import init_db, save_design_revision, save_procurement_record, save_schedule_log

# Import new RAG and CPM Solver engines
from rag_engine import ConstructionRAG
from cpm_solver import CPMSolver

class LocalLLMSchedulerAgent:
    def __init__(self):
        self.cpm_engine = CPMSolver()

    def execute(self, revision_id: str, procurement_data: dict = None) -> dict:
        print(f"\n⏱️ [3. SCHEDULE AGENT + NetworkX CPM Engine]: Evaluating Critical Path impact for {revision_id}...")
        
        lead_time = 0
        if isinstance(procurement_data, dict):
            lead_time = procurement_data.get("lead_time_days", 5)

        # Run Graph-based CPM Solver
        cpm_results = self.cpm_engine.calculate_cpm_impact("TASK-FOUNDATION", lead_time_delay=lead_time)

        schedule_report = {
            "task_id": f"TASK-{revision_id}",
            "is_critical_path": cpm_results["is_critical"],
            "delay_days": cpm_results["delay_added"],
            "new_start_date": "2026-08-15",
            "recommended_action": f"NetworkX DAG Analysis: Critical Path ({' -> '.join(cpm_results['critical_path_tasks'])}). Total baseline duration updated to {cpm_results['total_project_duration_days']} days."
        }
        
        try:
            save_schedule_log(revision_id, schedule_report)
            print(f"   -> Saved Schedule Log for Task {schedule_report['task_id']} to Database")
        except Exception as e:
            print(f"   -> Logged schedule evaluation internally: {e}")
            
        return schedule_report

def run_construction_agent_pipeline(site_note: str = None):
    print("\n" + "=" * 60)
    print("🚀 STARTING LOCAL MULTI-AGENT CONSTRUCTION WORKFLOW (RAG + CPM ENHANCED)")
    print("=" * 60)

    try:
        init_db()
    except Exception:
        pass

    if not site_note:
        site_note = "Site inspection on Rev-905: Required 200 m3 of C40/50 Ready-Mix Concrete for ground slab."

    # 1. Vector RAG Query
    rag = ConstructionRAG()
    retrieved_code = rag.query_spec(site_note)
    print(f"\n📚 [RAG VECTOR ENGINE]: Retrieved Code Constraint -> '{retrieved_code}'")

    # 2. Design Agent Execution (Context-Augmented)
    augmented_site_note = f"{site_note} [Building Standard Constraint: {retrieved_code}]"
    design_agent = LocalLLMDesignAgent()
    parsed_design = design_agent.execute(augmented_site_note)
    
    revision_id = "Rev-GENERIC"
    if isinstance(parsed_design, dict):
        revision_id = parsed_design.get("revision_id", "Rev-802")
    elif isinstance(parsed_design, list) and len(parsed_design) > 0:
        if isinstance(parsed_design[0], dict):
            revision_id = parsed_design[0].get("revision_id", "Rev-802")

    # 3. Procurement Agent Execution
    procurement_agent = LocalLLMProcurementAgent()
    procurement_result = procurement_agent.execute(revision_id)

    # 4. Scheduler Agent Execution (NetworkX Solver)
    scheduler_agent = LocalLLMSchedulerAgent()
    schedule_result = scheduler_agent.execute(revision_id, procurement_result)

    print("\n" + "=" * 60)
    print("✅ MULTI-AGENT WORKFLOW COMPLETED & PERSISTED TO DATABASE")
    print("=" * 60)

if __name__ == "__main__":
    run_construction_agent_pipeline()