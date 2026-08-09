"""Deterministic safety gate for unstructured construction site notes."""

from __future__ import annotations

import re
from dataclasses import dataclass


REVISION_PATTERN = re.compile(
    r"\b(?:rev(?:ision)?)[\s:_-]*([a-z0-9][a-z0-9-]{0,63})\b",
    re.IGNORECASE,
)
QUANTITY_UNIT_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(?:m3|m³|cubic\s+meters?|kg|kilograms?|t|tons?|tonnes?|m|meters?|mm|cm|pieces?|pcs|units?|bags?|sheets?)\b",
    re.IGNORECASE,
)

MATERIAL_TERMS = {
    "concrete",
    "cement",
    "rebar",
    "steel",
    "formwork",
    "timber",
    "aggregate",
    "masonry",
    "brick",
    "blocks",
    "mesh",
}
ELEMENT_TERMS = {
    "foundation",
    "footing",
    "column",
    "columns",
    "slab",
    "beam",
    "wall",
    "roof",
    "excavation",
    "site",
    "finishing",
}
ACTION_TERMS = {
    "need",
    "required",
    "require",
    "change",
    "replace",
    "order",
    "procure",
    "install",
    "pour",
    "supply",
}
AMBIGUOUS_PATTERNS = (
    re.compile(r"\b(?:some|several|few|many)\b", re.IGNORECASE),
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?\s+or\s+\d+(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\b(?:strike that|ignore that|not sure|either)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ValidatedSiteNote:
    text: str
    revision_id: str


class SiteNoteValidationError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def _contains_any(text: str, terms: set[str]) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words.intersection(terms))


def assess_site_note(site_note: str | None) -> tuple[ValidatedSiteNote | None, list[str]]:
    text = (site_note or "").strip()
    issues: list[str] = []

    if len(text) < 15:
        issues.append("Enter a complete construction revision note (at least 15 characters).")
    if len(text) > 4_000:
        issues.append("The site note exceeds the 4,000-character safety limit.")

    revision_match = REVISION_PATTERN.search(text)
    revision_id = ""
    if revision_match:
        revision_id = f"Rev-{revision_match.group(1).upper()}"
    else:
        issues.append("Include a traceable revision ID such as Rev-102.")

    if not _contains_any(text, MATERIAL_TERMS):
        issues.append("Specify a supported construction material.")
    if not _contains_any(text, ELEMENT_TERMS):
        issues.append("Specify the affected construction element or activity.")
    if not _contains_any(text, ACTION_TERMS):
        issues.append("Specify a construction action such as need, change, order, or install.")
    quantity_match = QUANTITY_UNIT_PATTERN.search(text)
    if not quantity_match:
        issues.append("Provide one explicit positive quantity with a unit (for example, 25 m3).")
    elif float(quantity_match.group(1)) <= 0:
        issues.append("The requested quantity must be greater than zero.")

    if any(pattern.search(text) for pattern in AMBIGUOUS_PATTERNS):
        issues.append("Resolve ambiguous or contradictory wording before processing the request.")

    if issues:
        return None, issues
    return ValidatedSiteNote(text=text, revision_id=revision_id), []


def validate_site_note(site_note: str | None) -> ValidatedSiteNote:
    validated, issues = assess_site_note(site_note)
    if validated is None:
        raise SiteNoteValidationError(issues)
    return validated
