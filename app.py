"""Professional Streamlit dashboard for the local construction-agent workflow."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from agent_pipeline import review_construction_agent_pipeline, run_construction_agent_pipeline
from approval import ApprovalError
from audit import AuditIntegrityError, verify_audit_chain
from database import (
    TABLES,
    database_counts,
    fetch_table,
    get_approval_request,
    init_db,
    list_pending_approval_requests,
)
from settings import settings
from security import (
    PERMISSION_CREATE_PACKAGE,
    PERMISSION_DECIDE_PACKAGE,
    AuthenticationError,
    AuthenticatedPrincipal,
    SecurityError,
    authenticate_user,
    count_users,
    get_active_principal,
    has_permission,
    principal_from_mapping,
    record_logout,
    session_is_expired,
    utc_now,
)
from validation import SiteNoteValidationError


st.set_page_config(
    page_title="Construction MAS control centre",
    page_icon=":material/construction:",
    layout="wide",
)

init_db(settings.database_path)
st.session_state.setdefault("last_pipeline_result", None)
st.session_state.setdefault("last_review_package", None)
st.session_state.setdefault("review_flash", None)
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("authenticated_at", None)
st.session_state.setdefault("last_activity_at", None)


def clear_authenticated_session() -> None:
    for key in (
        "auth_user",
        "authenticated_at",
        "last_activity_at",
        "last_pipeline_result",
        "last_review_package",
        "review_flash",
    ):
        st.session_state[key] = None


def render_login() -> None:
    st.title("Construction multi-agent control centre")
    st.caption("Authenticated local governance for the controlled planning workflow.")
    if count_users(db_path=settings.database_path) == 0:
        st.warning(
            "No local accounts exist. Create separate preparer and reviewer accounts "
            "from the trusted workstation terminal before signing in.",
            icon=":material/admin_panel_settings:",
        )
        st.code(
            "\n".join(
                (
                    "python manage_users.py create --username preparer --display-name \"Package Preparer\" --role PREPARER",
                    "python manage_users.py create --username reviewer --display-name \"Design Reviewer\" --role DESIGN_REVIEWER",
                    "python manage_users.py create --username admin --display-name \"Local Administrator\" --role ADMIN",
                )
            ),
            language="powershell",
        )
        st.stop()

    with st.form("login_form", border=True):
        st.subheader("Sign in")
        username = st.text_input(
            "Username",
            autocomplete="username",
            max_chars=64,
            icon=":material/person:",
        )
        password = st.text_input(
            "Password",
            type="password",
            autocomplete="current-password",
            max_chars=128,
            icon=":material/password:",
        )
        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
            icon=":material/login:",
        )
    if submitted:
        try:
            principal = authenticate_user(
                username,
                password,
                db_path=settings.database_path,
            )
        except AuthenticationError:
            st.error("Invalid credentials or account unavailable.", icon=":material/lock:")
        else:
            now = utc_now()
            st.session_state.auth_user = principal.model_dump()
            st.session_state.authenticated_at = now
            st.session_state.last_activity_at = now
            st.rerun()
    st.stop()


if st.session_state.auth_user is None:
    render_login()

principal = principal_from_mapping(st.session_state.auth_user)
current_principal = get_active_principal(principal.user_id, db_path=settings.database_path)
authenticated_at = st.session_state.authenticated_at
last_activity_at = st.session_state.last_activity_at
if (
    current_principal is None
    or current_principal != principal
    or not isinstance(authenticated_at, datetime)
    or not isinstance(last_activity_at, datetime)
    or session_is_expired(authenticated_at, last_activity_at)
):
    if current_principal is not None:
        record_logout(current_principal, reason="SESSION_EXPIRED", db_path=settings.database_path)
    clear_authenticated_session()
    st.warning("Your session expired or the account changed. Sign in again.")
    st.rerun()

principal = current_principal
st.session_state.last_activity_at = utc_now()


def load_table(table_name: str) -> pd.DataFrame:
    if table_name not in TABLES:
        raise ValueError(f"Unsupported table: {table_name}")
    frame = pd.DataFrame(fetch_table(table_name, db_path=settings.database_path))
    if table_name == "approval_requests" and "payload_json" in frame.columns:
        frame = frame.drop(columns=["payload_json"])
    if table_name == "users":
        secret_columns = [
            "password_hash",
            "password_salt",
            "password_algorithm",
            "password_parameters_json",
        ]
        frame = frame.drop(
            columns=[column for column in secret_columns if column in frame.columns]
        )
    return frame


def render_verified_evidence(package: dict) -> None:
    claim_rows = [
        {
            "Claim": claim["claim_text"],
            "Chunk IDs": [citation["chunk_id"] for citation in claim["citations"]],
            "Verbatim evidence": [
                citation["evidence_quote"] for citation in claim["citations"]
            ],
        }
        for claim in package["grounded_claims"]
    ]
    st.markdown("**Claim-level verification**")
    st.dataframe(pd.DataFrame(claim_rows), hide_index=True, width="stretch")

    evidence_frame = pd.DataFrame(package["retrieved_evidence"])
    evidence_columns = [
        "document_code",
        "section",
        "clause",
        "printed_page_label",
        "page_number",
        "similarity",
        "status",
        "edition",
        "source_url",
        "chunk_id",
    ]
    st.markdown("**Retrieved source register**")
    st.dataframe(
        evidence_frame[evidence_columns],
        column_config={
            "source_url": st.column_config.LinkColumn("Official source"),
            "similarity": st.column_config.ProgressColumn(
                "Hybrid score", min_value=0.0, max_value=1.0, format="%.3f"
            ),
        },
        hide_index=True,
        width="stretch",
    )


st.title("Construction multi-agent control centre")
st.caption(
    "Local Ollama workflow with deterministic validation, routed hybrid retrieval, "
    "authenticated human approval, procurement planning, CPM impact analysis, and "
    "hash-chained SQLite audit lineage."
)

with st.sidebar:
    st.subheader("Signed-in user")
    st.badge(principal.role, icon=":material/verified_user:", color="blue")
    st.write(f"**{principal.display_name}**")
    st.caption(f"`{principal.username}`")
    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        record_logout(principal, db_path=settings.database_path)
        clear_authenticated_session()
        st.rerun()
    st.divider()
    st.subheader("System configuration")
    st.badge("Local processing", icon=":material/lock:", color="green")
    st.write(f"**Model:** `{settings.ollama_model}`")
    st.write(f"**Database:** `{settings.database_path}`")
    st.write("**Embeddings:** `all-MiniLM-L6-v2` (local)")
    st.write("**Controlled corpus:** `A / C / K / 7` (England)")
    st.write(
        f"**Retrieval blend:** `{settings.rag_semantic_weight:.0%}` semantic / "
        f"`{settings.rag_lexical_weight:.0%}` lexical"
    )
    st.write(f"**RAG index:** `{settings.rag_index_path}`")
    st.write(f"**RAG confidence floor:** `{settings.rag_minimum_similarity:.2f}`")
    st.caption("Configuration can be changed through CONSTRUCTION_* environment variables.")
    with st.expander("Decision boundaries", icon=":material/policy:"):
        st.markdown(
            "- Supplier and cost outputs are planning estimates requiring human verification.\n"
            "- Retrieved passages come from controlled documents with page citations; they are not compliance certificates.\n"
            "- Document routing is discipline-aware; unsupported commercial and non-England compliance requests are rejected.\n"
            "- Procurement and scheduling cannot run until a pending package receives one authenticated decision.\n"
            "- PREPARER users cannot decide their own packages; reviewer roles are enforced server-side.\n"
            "- Approval requires password reauthentication and is recorded in the hash-chained audit log.\n"
            "- CPM uses the project demonstration schedule, not a live Primavera/MS Project file."
        )


counts = database_counts(settings.database_path)
with st.container(horizontal=True):
    st.metric("Active users", counts["active_users"], border=True)
    st.metric("Audit events", counts["audit_events"], border=True)
    st.metric("Design revisions", counts["revisions"], border=True)
    st.metric("Material requirements", counts["materials"], border=True)
    st.metric("Unverified quotes", counts["quotes"], border=True)
    st.metric("Schedule impacts", counts["schedule_impacts"], border=True)
    st.metric("Pending approvals", counts["pending_approvals"], border=True)
    st.metric("Rejected or failed", counts["rejected_runs"], border=True)


left, right = st.columns([1.35, 1], gap="large")
can_prepare = has_permission(principal, PERMISSION_CREATE_PACKAGE)
can_decide = has_permission(principal, PERMISSION_DECIDE_PACKAGE)

with left:
    st.subheader("Process a controlled site revision")
    if not can_prepare:
        st.info(
            "Your role can inspect the audit trail but cannot prepare review packages.",
            icon=":material/policy:",
        )
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
            disabled=not can_prepare,
        )
        submitted = st.form_submit_button(
            "Prepare review package",
            type="primary",
            icon=":material/fact_check:",
            disabled=not can_prepare,
        )

    if submitted:
        try:
            with st.status(
                "Validating and preparing the review package...",
                expanded=True,
            ) as workflow_status:
                st.write("Checking the note before contacting the model")
                result = run_construction_agent_pipeline(
                    site_note,
                    principal=principal,
                    db_path=settings.database_path,
                )
                st.write("Design facts, grounded claims, and citations passed verification")
                workflow_status.update(
                    label="Review package recorded",
                    state="complete",
                    expanded=False,
                )
            st.session_state.last_review_package = result
            st.success(
                "The package awaits a decision. Procurement and scheduling have not run.",
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
            st.write(f"**Grounding status:** `{latest['grounding']['status']}`")
            st.write(
                f"**Verified technical claims:** {latest['grounding']['verified_claim_count']} / "
                f"**Verified citations:** {latest['grounding']['verified_citation_count']}"
            )
            st.write(f"**Approved by:** {latest['review']['reviewer_name']}")
        with st.expander("Verified claims and source evidence", icon=":material/fact_check:"):
            render_verified_evidence(latest)
        with st.expander("Inspect complete validated result", icon=":material/data_object:"):
            st.json(latest)
    elif st.session_state.last_review_package:
        pending_latest = st.session_state.last_review_package
        with st.container(border=True):
            st.badge("Awaiting approval", icon=":material/pending_actions:", color="orange")
            st.write(f"**Revision:** {pending_latest['design']['revision_id']}")
            st.write(f"**Affected element:** {pending_latest['design']['affected_element']}")
            st.write(f"**Grounding status:** `{pending_latest['grounding']['status']}`")
            st.write(
                f"**Verified technical claims:** "
                f"{pending_latest['grounding']['verified_claim_count']} / "
                f"**Verified citations:** {pending_latest['grounding']['verified_citation_count']}"
            )
            st.caption("No procurement or schedule agent has run for this package.")
        with st.expander("Verified claims and source evidence", icon=":material/fact_check:"):
            render_verified_evidence(pending_latest)
    else:
        with st.container(border=True):
            st.info(
                "Prepare a valid revision to display its review status here.",
                icon=":material/info:",
            )


st.header("Human approval queue")
st.caption(
    "Review the site note, extracted design, and cited evidence before one irreversible decision. "
    "Identity and role come from the authenticated account; password reauthentication and "
    "separation of duties are enforced before the decision is recorded."
)

if not can_decide:
    st.info(
        "Your role may inspect pending packages but cannot approve or reject them.",
        icon=":material/policy:",
    )

if st.session_state.review_flash:
    flash_kind, flash_message = st.session_state.review_flash
    if flash_kind == "success":
        st.success(flash_message, icon=":material/check_circle:")
    else:
        st.warning(flash_message, icon=":material/block:")
    st.session_state.review_flash = None

pending_reviews = list_pending_approval_requests(db_path=settings.database_path)
if not pending_reviews:
    st.info("No review packages are awaiting a decision.", icon=":material/inbox:")
else:
    review_options = {row["review_id"]: row for row in pending_reviews}
    selected_review_id = st.selectbox(
        "Review package",
        options=list(review_options),
        format_func=lambda review_id: (
            f"{review_options[review_id]['revision_id']} / "
            f"prepared by {review_options[review_id]['preparer_username'] or 'legacy user'} / "
            f"requested {review_options[review_id]['requested_at']}"
        ),
        key="selected_review_id",
    )
    selected_row = get_approval_request(selected_review_id, db_path=settings.database_path)
    if selected_row is None:
        st.error("The selected review package is no longer available.", icon=":material/error:")
    else:
        review_package = json.loads(selected_row["payload_json"])
        with st.container(border=True):
            st.badge("Pending", icon=":material/pending_actions:", color="orange")
            st.write(f"**Review ID:** `{selected_review_id}`")
            st.write(f"**Revision:** `{selected_row['revision_id']}`")
            st.write(
                f"**Prepared by:** "
                f"{selected_row['preparer_display_name'] or 'Unauthenticated legacy package'}"
            )
            st.write(f"**Site note:** {review_package['site_note']}")
            st.write(f"**Affected element:** {review_package['design']['affected_element']}")
            st.write(f"**Snapshot SHA-256:** `{selected_row['payload_sha256']}`")
            st.dataframe(
                pd.DataFrame(review_package["design"]["requirements"]),
                hide_index=True,
                width="stretch",
            )

        with st.expander("Inspect claims and evidence before deciding", icon=":material/source:"):
            render_verified_evidence(review_package)

        separation_ok = bool(selected_row["prepared_by_user_id"]) and (
            selected_row["prepared_by_user_id"] != principal.user_id
        )
        decision_allowed = can_decide and separation_ok
        if selected_row["prepared_by_user_id"] == principal.user_id:
            st.warning(
                "Separation of duties blocks you from deciding a package you prepared.",
                icon=":material/shield_lock:",
            )
        elif not selected_row["prepared_by_user_id"]:
            st.warning(
                "This legacy package has no authenticated preparer. Recreate it before review.",
                icon=":material/history:",
            )

        with st.form(f"approval_form_{selected_review_id}", border=True):
            st.subheader("Record review decision")
            st.write(f"**Authenticated reviewer:** {principal.display_name}")
            st.write(f"**Authorized role:** `{principal.role}`")
            reauthentication_password = st.text_input(
                "Confirm your password",
                type="password",
                autocomplete="current-password",
                max_chars=128,
                disabled=not decision_allowed,
                help="A fresh credential check is required for this high-impact decision.",
                icon=":material/password:",
            )
            decision = st.segmented_control(
                "Decision",
                ["APPROVE", "REJECT"],
                selection_mode="single",
                default=None,
                disabled=not decision_allowed,
            )
            review_comment = st.text_area(
                "Review comment",
                max_chars=2_000,
                help="A rejection requires a reason of at least 10 characters.",
                disabled=not decision_allowed,
            )
            decision_submitted = st.form_submit_button(
                "Record decision",
                type="primary",
                icon=":material/gavel:",
                disabled=not decision_allowed,
            )

        if decision_submitted:
            if decision is None:
                st.error("Select APPROVE or REJECT before submitting.")
            else:
                try:
                    with st.status("Recording decision and enforcing the gate...") as review_status:
                        outcome = review_construction_agent_pipeline(
                            selected_review_id,
                            {
                                "decision": decision,
                                "comment": review_comment,
                            },
                            principal=principal,
                            reauthentication_password=reauthentication_password,
                            db_path=settings.database_path,
                        )
                        if outcome["status"] == "COMPLETED":
                            review_status.update(
                                label="Approved workflow completed",
                                state="complete",
                            )
                            st.session_state.last_pipeline_result = outcome
                            st.session_state.review_flash = (
                                "success",
                                "Approval recorded; procurement planning and CPM analysis completed.",
                            )
                        else:
                            review_status.update(
                                label="Package rejected and downstream agents blocked",
                                state="complete",
                            )
                            st.session_state.review_flash = (
                                "warning",
                                "Rejection recorded; procurement and scheduling did not run.",
                            )
                        st.session_state.last_review_package = None
                    st.rerun()
                except ApprovalError as error:
                    st.error(f"The approval gate stopped safely: {error}")
                except SecurityError as error:
                    st.error(f"Authorization stopped the decision: {error}")
                except Exception as error:
                    st.error(f"The decision could not be completed safely: {error}")


st.header("Audit trail")
st.caption(
    "Every table is read-only in this dashboard. Runtime records remain in local SQLite. "
    "The SHA-256 event chain detects modified, missing, or reordered stored events."
)
try:
    audit_integrity = verify_audit_chain(settings.database_path)
except AuditIntegrityError as error:
    st.error(f"Audit-chain verification failed: {error}", icon=":material/gpp_bad:")
else:
    st.success(
        f"Audit chain verified: {audit_integrity['event_count']} event(s), "
        f"head `{audit_integrity['head_hash']}`",
        icon=":material/verified:",
    )

tabs = st.tabs(
    [
        ":material/design_services: Revisions",
        ":material/inventory_2: Materials",
        ":material/approval: Approvals",
        ":material/request_quote: Procurement",
        ":material/account_tree: Schedule",
        ":material/history: Pipeline runs",
        ":material/group: Users",
        ":material/security: Security events",
    ]
)

table_views = (
    (tabs[0], "design_revisions", "Validated design revisions"),
    (tabs[1], "material_requirements", "Normalized material requirements"),
    (tabs[2], "approval_requests", "Recorded human-review decisions"),
    (tabs[3], "procurement_records", "Unverified procurement planning estimates"),
    (tabs[4], "schedule_logs", "Calculated CPM impacts"),
    (tabs[5], "pipeline_runs", "Execution and rejection history"),
    (tabs[6], "users", "Local account metadata"),
    (tabs[7], "audit_events", "Hash-chained authentication and governance events"),
)

for tab, table_name, title in table_views:
    with tab:
        st.subheader(title)
        frame = load_table(table_name)
        if frame.empty:
            st.info("No records are available yet.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch", key=f"audit-{table_name}")
