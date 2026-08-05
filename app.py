import streamlit as st
import sqlite3
import pandas as pd
from agent_pipeline import run_construction_agent_pipeline

DB_NAME = "construction_mas.db"

# Page Configuration
st.set_page_config(
    page_title="Private Construction MAS Dashboard",
    page_icon="🏗️",
    layout="wide"
)

# Title & Header
st.title("🏗️ Multi-Agent Construction Project System")
st.caption("100% Private, Local LLM-Driven Decision Engine (Ollama / Llama 3.1 + SQLite)")

st.divider()

# Left Column: Input Panel | Right Column: Quick Stats
col_input, col_stats = st.columns([2, 1])

with col_input:
    st.subheader("📝 Live Site Input")
    site_note_input = st.text_area(
        "Enter unstructured site note, inspection record, or revision request:",
        height=120,
        placeholder="e.g., Site update Rev-102: Need 150 m3 of C60 concrete for column pour by next Tuesday."
    )
    
    run_button = st.button("🚀 Process via Agent Pipeline", type="primary")

with col_stats:
    st.subheader("📊 System Database Stats")
    try:
        conn = sqlite3.connect(DB_NAME)
        rev_count = conn.execute("SELECT COUNT(*) FROM design_revisions").fetchone()[0]
        proc_count = conn.execute("SELECT COUNT(*) FROM procurement_records").fetchone()[0]
        sched_count = conn.execute("SELECT COUNT(*) FROM schedule_logs").fetchone()[0]
        conn.close()
        
        st.metric("Design Revisions Logged", rev_count)
        st.metric("Procurement Records", proc_count)
        st.metric("Schedule Impact Logs", sched_count)
    except Exception:
        st.info("Database initializing upon first execution.")

st.divider()

# Pipeline Execution Trigger
if run_button:
    if not site_note_input.strip():
        st.warning("⚠️ Please enter a site note before running the pipeline.")
    else:
        with st.spinner("🤖 Local Agents collaborating... (Design ➔ Procurement ➔ Schedule)"):
            try:
                run_construction_agent_pipeline(site_note_input)
                st.success("✅ Multi-Agent Workflow Completed & Logged to Database!")
                st.rerun()  # Instantly refresh table counts and UI
            except Exception as e:
                st.error(f"❌ Pipeline Execution Failed: {e}")

# Database Audit Section
st.header("🔍 Database Audit & Agent State Lineage")

tab1, tab2, tab3 = st.tabs(["🏗️ Design Revisions", "📦 Procurement Log", "⏱️ Critical Path Schedule Log"])

def load_table(table_name):
    try:
        conn = sqlite3.connect(DB_NAME)
        # Querying full table directly
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        conn.close()
        return df
    except Exception as e:
        return pd.DataFrame()

with tab1:
    st.subheader("Design Agent Output")
    df_design = load_table("design_revisions")
    if not df_design.empty:
        st.dataframe(df_design)
    else:
        st.info("No design records found in database.")

with tab2:
    st.subheader("Procurement Agent Output")
    df_proc = load_table("procurement_records")
    if not df_proc.empty:
        st.dataframe(df_proc)
    else:
        st.info("No procurement records found in database.")

with tab3:
    st.subheader("Scheduler Agent Output")
    df_sched = load_table("schedule_logs")
    if not df_sched.empty:
        st.dataframe(df_sched)
    else:
        st.info("No schedule impact logs found in database.")