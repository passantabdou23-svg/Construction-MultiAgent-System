"""Integrity checks and typed contracts for the local human-review gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from database import DB_NAME, get_approval_request, get_design_review_state
from schemas import (
    ApprovalPayload,
    ApprovalReviewRecord,
    DesignUpdatePayload,
    GroundedClaim,
    GroundingVerification,
)


class ApprovalError(RuntimeError):
    """Base error for missing, stale, replayed, or tampered approval requests."""


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalStateError(ApprovalError):
    pass


class ApprovalIntegrityError(ApprovalError):
    pass


def canonical_payload_json(payload: ApprovalPayload | dict[str, Any]) -> str:
    if isinstance(payload, ApprovalPayload):
        value = payload.model_dump(mode="json")
    else:
        value = ApprovalPayload.model_validate(payload).model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_payload_sha256(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def review_record_from_row(row: dict[str, Any]) -> ApprovalReviewRecord:
    return ApprovalReviewRecord.model_validate(
        {key: value for key, value in row.items() if key not in {"payload_json", "site_note"}}
    )


def _verified_state_contract(payload: ApprovalPayload) -> str:
    value = {
        "design": payload.design.model_dump(mode="json"),
        "grounded_claims": [claim.model_dump(mode="json") for claim in payload.grounded_claims],
        "grounding": payload.grounding.model_dump(mode="json"),
    }
    value["design"]["requirements"] = sorted(
        value["design"]["requirements"], key=lambda requirement: requirement["item_id"]
    )
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_pending_approval(
    review_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> tuple[dict[str, Any], ApprovalPayload]:
    row = get_approval_request(review_id, db_path=db_path)
    if row is None:
        raise ApprovalNotFoundError(f"Approval request {review_id} was not found")
    if row["status"] != "PENDING":
        raise ApprovalStateError(
            f"Approval request {review_id} has already been decided as {row['status']}"
        )

    payload_json = row["payload_json"]
    actual_hash = calculate_payload_sha256(payload_json)
    if actual_hash != row["payload_sha256"]:
        raise ApprovalIntegrityError("The stored approval payload no longer matches its SHA-256")
    try:
        payload = ApprovalPayload.model_validate_json(payload_json)
    except (TypeError, ValueError) as error:
        raise ApprovalIntegrityError("The stored approval payload is not a valid contract") from error
    if row["site_note"] != payload.site_note:
        raise ApprovalIntegrityError("The site note changed after the review was requested")

    current = get_design_review_state(row["revision_id"], db_path=db_path)
    if current is None:
        raise ApprovalIntegrityError("The design revision referenced by the review no longer exists")
    try:
        current_payload = ApprovalPayload(
            site_note=payload.site_note,
            validation_message=payload.validation_message,
            retrieved_standard=payload.retrieved_standard,
            retrieved_evidence=payload.retrieved_evidence,
            design=DesignUpdatePayload.model_validate(current["design"]),
            grounded_claims=[GroundedClaim.model_validate(item) for item in current["grounded_claims"]],
            grounding=GroundingVerification.model_validate(current["grounding"]),
        )
    except (TypeError, ValueError) as error:
        raise ApprovalIntegrityError("The current design state is no longer valid") from error
    if _verified_state_contract(current_payload) != _verified_state_contract(payload):
        raise ApprovalIntegrityError(
            "The design, claims, or grounding record changed after the review was requested"
        )
    return row, payload
