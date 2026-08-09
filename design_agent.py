"""Validated local-LLM design agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import ollama
from pydantic import ValidationError

from database import DB_NAME, save_design_revision
from grounding import (
    CitationVerificationError,
    EvidenceConflictError,
    EvidenceRecord,
    GroundingRefusalError,
    SiteFactVerificationError,
    align_citation_quotes,
    format_evidence_context,
    verify_design_against_site_note,
    verify_grounded_response,
)
from schemas import GroundedDesignResponse, VerifiedDesignResult
from settings import settings


class DesignAgentError(RuntimeError):
    pass


def _response_content(response: Any) -> str:
    if isinstance(response, dict):
        return response["message"]["content"]
    return response.message.content


class LocalLLMDesignAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        db_path: str = DB_NAME,
        client: Any = ollama,
    ):
        self.model = model or settings.ollama_model
        self.db_path = db_path
        self.client = client

    @staticmethod
    def _correction_prompt(error: Exception) -> str:
        return (
            "Your previous JSON was rejected by deterministic validation. Correct the JSON once "
            "without changing or adding site-note facts. If evidence_status is SUPPORTED, include "
            "at least one grounded claim with a supplied chunk_id and an exact verbatim quote. "
            "Do not copy evidence facts into the design requirements. Validation error: "
            f"{error}"
        )

    def execute(
        self,
        raw_unstructured_text: str,
        *,
        evidence: Sequence[EvidenceRecord],
        expected_revision_id: str,
    ) -> dict:
        standard_context = format_evidence_context(evidence)
        system_prompt = (
            "You are a source-grounded construction design-information extraction agent. "
            "Extract only facts explicitly present in the site note. Never invent a material, "
            "quantity, unit, affected element, or revision ID. Use one of the allowed "
            "material_type enum values. Technical guidance claims must rely only on the supplied "
            "EVIDENCE blocks. Every claim must cite a supplied chunk_id and include a short exact "
            "verbatim quote copied from that chunk. Do not introduce a number in claim_text unless "
            "the same number appears in its cited quote. If the evidence is insufficient or "
            "conflicting, return the corresponding refusal status with design=null and no claims. "
            "Never describe the output as regulatory approval or an engineering certificate. "
            "A SUPPORTED response must include at least one grounded technical-guidance claim. "
            "The design object must contain only site-note facts; never copy a material, standard, "
            "mix component, quantity, or specification from EVIDENCE into design. For example, a "
            "note requesting 25 m3 of C40 concrete must produce exactly one Concrete/C40/25/m3 "
            "requirement even if the evidence discusses cement or aggregate. "
            "Return JSON matching the supplied schema."
        )
        user_prompt = (
            f"Site note: {raw_unstructured_text}\n"
            f"The revision_id must be exactly {expected_revision_id}.\n"
            "Controlled reference evidence (guidance only, not a compliance decision):\n"
            f"{standard_context}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: GroundedDesignResponse | None = None
        verification = None
        site_fact_notes: list[str] = []
        quote_alignment_notes: list[str] = []

        for attempt in range(2):
            response_content = ""
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=messages,
                    format=GroundedDesignResponse.model_json_schema(),
                )
                response_content = _response_content(response)
            except Exception as error:
                raise DesignAgentError(
                    f"Design agent could not reach or execute the local model: {error}"
                ) from error

            try:
                payload = GroundedDesignResponse.model_validate_json(response_content)
                if payload.evidence_status != "SUPPORTED":
                    raise GroundingRefusalError(payload.evidence_status, payload.reason)
                if payload.design is None:
                    raise DesignAgentError("Supported design-agent output omitted the design payload")
                if payload.design.revision_id != expected_revision_id:
                    raise DesignAgentError(
                        "Design agent changed the revision ID from "
                        f"{expected_revision_id} to {payload.design.revision_id}"
                    )

                payload, quote_alignment_notes = align_citation_quotes(payload, evidence)
                verification = verify_grounded_response(payload, evidence)
                site_fact_notes = verify_design_against_site_note(
                    payload.design, raw_unstructured_text
                )
                break
            except GroundingRefusalError:
                raise
            except EvidenceConflictError as error:
                raise GroundingRefusalError("CONFLICTING_EVIDENCE", str(error)) from error
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                validation_error = DesignAgentError(
                    f"Design-agent output failed schema validation: {error}"
                )
            except CitationVerificationError as error:
                validation_error = DesignAgentError(
                    f"Design-agent grounding verification failed: {error}"
                )
            except SiteFactVerificationError as error:
                validation_error = DesignAgentError(
                    f"Design-agent site-fact verification failed: {error}"
                )
            except DesignAgentError as error:
                validation_error = error

            if attempt == 1:
                raise validation_error
            messages.extend(
                [
                    {"role": "assistant", "content": response_content},
                    {"role": "user", "content": self._correction_prompt(validation_error)},
                ]
            )

        if payload is None or payload.design is None or verification is None:
            raise DesignAgentError("Design agent did not produce a verified design")

        verification = verification.model_copy(
            update={
                "notes": [
                    *verification.notes,
                    *quote_alignment_notes,
                    *site_fact_notes,
                ]
            }
        )

        serialized = payload.design.model_dump(mode="json")
        grounded_claims = [claim.model_dump(mode="json") for claim in payload.claims]
        verification_data = verification.model_dump(mode="json")
        save_design_revision(
            serialized,
            source_note=raw_unstructured_text,
            standard_reference=standard_context,
            grounding_status=verification.status,
            grounded_claims=grounded_claims,
            citation_verification=verification_data,
            db_path=self.db_path,
        )
        return VerifiedDesignResult(
            design=payload.design,
            grounded_claims=payload.claims,
            grounding=verification,
        ).model_dump(mode="json")
