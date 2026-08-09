"""Validated local-LLM procurement planning agent."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import ollama
from pydantic import ValidationError

from database import DB_NAME, get_material_requirements, save_procurement_records
from schemas import ProcurementQuote, ProcurementResult
from settings import settings


class ProcurementAgentError(RuntimeError):
    pass


def _response_content(response: Any) -> str:
    if isinstance(response, dict):
        return response["message"]["content"]
    return response.message.content


class LocalLLMProcurementAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        db_path: str = DB_NAME,
        client: Any = ollama,
        today_provider: Callable[[], date] = date.today,
    ):
        self.model = model or settings.ollama_model
        self.db_path = db_path
        self.client = client
        self.today_provider = today_provider

    def _quote_material(self, revision_id: str, material: dict[str, Any]) -> ProcurementQuote:
        today = self.today_provider()
        prompt = (
            "You are a construction procurement planning agent. Produce an UNVERIFIED planning "
            "estimate, not a real supplier quotation. Return JSON matching the supplied schema.\n"
            f"Current date: {today.isoformat()}\n"
            f"Revision: {revision_id}\n"
            f"Item ID: {material['item_id']}\n"
            f"Material: {material['material_type']}\n"
            f"Specification: {material['specification']}\n"
            f"Quantity: {material['quantity']} {material['unit']}\n"
            f"Affected element: {material['affected_element']}\n"
            "unit_cost must be positive and lead_time_days must be between 0 and 365."
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                format=ProcurementQuote.model_json_schema(),
            )
            quote = ProcurementQuote.model_validate_json(_response_content(response))
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ProcurementAgentError(
                f"Procurement output for {material['item_id']} failed validation: {error}"
            ) from error
        except Exception as error:
            raise ProcurementAgentError(
                f"Procurement agent could not estimate {material['item_id']}: {error}"
            ) from error

        # Trusted arithmetic and dates are derived locally, not accepted from the LLM.
        unit_cost = round(float(quote.unit_cost), 2)
        total_cost = round(unit_cost * float(material["quantity"]), 2)
        delivery_date = today + timedelta(days=quote.lead_time_days)
        return quote.model_copy(
            update={
                "item_id": material["item_id"],
                "unit_cost": unit_cost,
                "total_cost": total_cost,
                "earliest_delivery_date": delivery_date,
                "quote_status": "PENDING_VERIFICATION",
                "source": "LLM_ESTIMATE_UNVERIFIED",
            }
        )

    def execute(self, revision_id: str) -> dict:
        materials = get_material_requirements(revision_id, db_path=self.db_path)
        if not materials:
            raise ProcurementAgentError(f"No validated materials exist for revision {revision_id}")

        quotes = [self._quote_material(revision_id, material) for material in materials]
        serialized_quotes = [quote.model_dump(mode="json") for quote in quotes]
        save_procurement_records(revision_id, serialized_quotes, db_path=self.db_path)

        result = ProcurementResult(
            revision_id=revision_id,
            status="PENDING_VERIFICATION",
            quotes=quotes,
            maximum_lead_time_days=max(quote.lead_time_days for quote in quotes),
        )
        return result.model_dump(mode="json")
