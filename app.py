"""Professional Streamlit dashboard for the local construction-agent workflow."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agent_pipeline import run_construction_agent_pipeline
from database import TABLES, database_counts, fetch_table, init_db
from settings import settings
from validation import SiteNoteValidationError


st.set_page_config(
    page_title="Construction MAS control centre",
    page_icon=":material/construction:",
    layout="wide",
)

init_db(settings.database_path)
st.session_state.setdefault("last_pipeline_result", None)


def load_table(table_name: str) -> pd.DataFrame:
    if table_name not in TABLES:
        raise ValueError(f"Unsupported table: {table_name}")
    return pd.DataFrame(fetch_table(table_name, db_path=settings.database_path))


st.title("Construction multi-agent control centre")
st.caption(
    "Local Ollama workflow with deterministic validation, vector retrieval, "
    "procurement planning, CPM impact analysis, and SQLite audit lineage."
)

with st.sidebar:
    st.subheader("System configuration")
    st.badge("Local processing", icon=":material/lock:", color="green")
    st.write(f"**Model:** `{settings.ollama_model}`")
    st.write(f"**Database:** `{settings.database_path}`")
    st.caption("Configuration can be changed through CONSTRUCTION_* environment variables.")
    with st.expander("Decision boundaries", icon=":material/policy:"):
        st.markdown(
            "- Supplier and cost outputs are planning estimates requiring human verification.\n"
            "- Retrieved standards are demonstration summaries, not compliance certificates.\n"
            "- CPM uses the project demonstration schedule, not a live Primavera/MS Project file."
        )


counts = database_counts(settings.database_path)
with st.container(horizontal=True):
    st.metric("Design revisions", counts["revisions"], border=True)
    st.metric("Material requirements", counts["materials"], border=True)
    st.metric("Unverified quotes", counts["quotes"], border=True)
    st.metric("Schedule impacts", counts["schedule_impacts"], border=True)
    st.metric("Rejected or failed", counts["rejected_runs"], border=True)


left, right = st.columns([1.35, 1], gap="large")

with left:
    st.subheader("Process a controlled site revision")
    with st.form("site_revision_form", border=True):
        site_note = st.text_area(
            "Construction site note",
            height=155,
            placeholder=(
                "Site update Rev-102: Need 150 m3 of C60 concrete for the column pour."
            ),
            help=(
                "Include a Rev-ID, construction action, material, affected element, "
                "positive quantity, and unit. Ambiguous notes are rejected."
            ),
        )
        submitted = st.form_submit_button(
            "Run validated pipeline",
            type="primary",
            icon=":material/play_arrow:",
        )

    if submitted:
        try:
            with st.status(
                "Validating and coordinating local agents…",
                expanded=True,
            ) as workflow_status:
                st.write("Checking the note before contacting the model")
                result = run_construction_agent_pipeline(
                    site_note,
                    db_path=settings.database_path,
                )
                st.write("Design, procurement, and schedule outputs passed their contracts")
                workflow_status.update(
                    label="Workflow completed and recorded",
                    state="complete",
                    expanded=False,
                )
            st.session_state.last_pipeline_result = result
            st.success(
                "The run completed. Procurement values remain pending human verification.",
                icon=":material/check_circle:",
            )
        except SiteNoteValidationError as error:
            st.error("The request was rejected before the LLM was called.", icon=":material/block:")
            for issue in error.issues:
                st.write(f"- {issue}")
        except Exception as error:
            st.error(f"The workflow stopped safely: {error}", icon=":material/error:")

with right:
    st.subheader("Latest decision")
    latest = st.session_state.last_pipeline_result
    if latest:
        schedule = latest["schedule"]
        procurement = latest["procurement"]
        with st.container(border=True):
            st.badge("Completed", icon=":material/check:", color="green")
            st.write(f"**Revision:** {latest['design']['revision_id']}")
            st.write(f"**Affected element:** {latest['design']['affected_element']}")
            st.write(f"**Mapped CPM task:** {schedule['affected_task']}")
            st.write(f"**Calculated delay:** {schedule['delay_days']} days")
            st.write(f"**Projected completion:** {schedule['projected_completion_date']}")
            st.write(f"**Quotes awaiting verification:** {len(procurement['quotes'])}")
        with st.expander("Inspect complete validated result", icon=":material/data_object:"):
            st.json(latest)
    else:
        with st.container(border=True):
            st.info(
                "Run a valid revision to display its traceable decision summary here.",
                icon=":material/info:",
            )


st.header("Audit trail")
st.caption("Every table is read-only in this dashboard. Runtime records remain in local SQLite.")

tabs = st.tabs(
    [
        ":material/design_services: Revisions",
        ":material/inventory_2: Materials",
        ":material/request_quote: Procurement",
        ":material/account_tree: Schedule",
        ":material/history: Pipeline runs",
    ]
)

table_views = (
    (tabs[0], "design_revisions", "Validated design revisions"),
    (tabs[1], "material_requirements", "Normalized material requirements"),
    (tabs[2], "procurement_records", "Unverified procurement planning estimates"),
    (tabs[3], "schedule_logs", "Calculated CPM impacts"),
    (tabs[4], "pipeline_runs", "Execution and rejection history"),
)

for tab, table_name, title in table_views:
    with tab:
        st.subheader(title)
        frame = load_table(table_name)
        if frame.empty:
            st.info("No records are available yet.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch", key=f"audit-{table_name}")
