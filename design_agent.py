"""Validated local-LLM design agent."""

from __future__ import annotations

from typing import Any

import ollama
from pydantic import ValidationError

from database import DB_NAME, save_design_revision
from schemas import DesignUpdatePayload
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

    def execute(
        self,
        raw_unstructured_text: str,
        *,
        standard_context: str,
        expected_revision_id: str,
    ) -> dict:
        system_prompt = (
            "You are a construction design-information extraction agent. "
            "Extract only facts explicitly present in the site note. Never invent a material, "
            "quantity, unit, affected element, or revision ID. Use one of the allowed "
            "material_type enum values. Return JSON matching the supplied schema."
        )
        user_prompt = (
            f"Site note: {raw_unstructured_text}\n"
            f"Reference context (demonstration summary, not a compliance decision): {standard_context}\n"
            f"The revision_id must be exactly {expected_revision_id}."
        )

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=DesignUpdatePayload.model_json_schema(),
            )
            payload = DesignUpdatePayload.model_validate_json(_response_content(response))
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise DesignAgentError(f"Design-agent output failed schema validation: {error}") from error
        except Exception as error:
            raise DesignAgentError(f"Design agent could not reach or execute the local model: {error}") from error

        if payload.revision_id != expected_revision_id:
            raise DesignAgentError(
                f"Design agent changed the revision ID from {expected_revision_id} to {payload.revision_id}"
            )

        serialized = payload.model_dump(mode="json")
        save_design_revision(
            serialized,
            source_note=raw_unstructured_text,
            standard_reference=standard_context,
            db_path=self.db_path,
        )
        return serialized
