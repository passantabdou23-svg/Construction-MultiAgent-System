"""Deterministic claim-to-source verification for local grounded generation."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Protocol

from schemas import DesignUpdatePayload, GroundedDesignResponse, GroundingVerification


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"(?<![a-z])\d+(?:\.\d+)?(?:%|mm|cm|m2|m3|kg|kn)?", re.IGNORECASE)
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in into is it of on or should that the to was were which with".split()
)
_QUANTITY_UNIT_RE = re.compile(
    r"\b(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>m3|m³|cubic\s+meters?|kg|kilograms?|t|tons?|tonnes?|m|meters?|mm|cm|pieces?|pcs|units?|bags?|sheets?)\b",
    re.IGNORECASE,
)
_MATERIAL_ALIASES = {
    "concrete": {"concrete"},
    "steel": {"steel"},
    "rebar": {"rebar", "reinforcement", "reinforcing"},
    "formwork": {"formwork"},
    "masonry": {"masonry", "brick", "bricks", "block", "blocks"},
    "timber": {"timber"},
    "aggregate": {"aggregate", "aggregates"},
    "cement": {"cement"},
}
_UNSPECIFIED_VALUES = {"not specified", "unspecified", "not stated"}
MINIMUM_CLAIM_TOKEN_COVERAGE = 0.20


class EvidenceRecord(Protocol):
    chunk_id: str
    document_id: str
    document_code: str
    title: str
    edition: str
    status: str
    jurisdiction: str
    page_number: int
    printed_page_label: str
    section: str
    clause: str
    text: str
    source_url: str
    source_sha256: str
    similarity: float


class GroundingError(RuntimeError):
    """Base error for refused, conflicting, or unverifiable grounded output."""


class GroundingRefusalError(GroundingError):
    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"Grounded design refused ({status}): {reason}")


class EvidenceConflictError(GroundingError):
    """Raised when retrieved governance metadata contains incompatible versions."""


class CitationVerificationError(GroundingError):
    """Raised when a claim cites unavailable or textually unsupported evidence."""


class SiteFactVerificationError(GroundingError):
    """Raised when retrieved evidence contaminates facts extracted from the site note."""


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _content_tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(value.casefold())
        if token not in _STOP_WORDS and len(token) > 2
    }


def _canonical_unit(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.casefold()).strip()
    aliases = {
        "m³": "m3",
        "cubic meter": "m3",
        "cubic meters": "m3",
        "kilogram": "kg",
        "kilograms": "kg",
        "ton": "t",
        "tons": "t",
        "tonne": "t",
        "tonnes": "t",
        "meter": "m",
        "meters": "m",
        "piece": "pcs",
        "pieces": "pcs",
        "unit": "units",
    }
    return aliases.get(normalized, normalized)


def verify_design_against_site_note(
    design: DesignUpdatePayload,
    site_note: str,
) -> list[str]:
    """Ensure design/procurement facts originate only from explicit note content."""
    normalized_note = _normalized_text(site_note)
    note_tokens = _content_tokens(site_note)
    explicit_quantities = [
        (float(match.group("quantity")), _canonical_unit(match.group("unit")))
        for match in _QUANTITY_UNIT_RE.finditer(site_note)
    ]
    unmatched_quantities = list(explicit_quantities)
    if len(design.requirements) > len(explicit_quantities):
        raise SiteFactVerificationError(
            "Design output contains more material requirements than explicit quantity-unit pairs in the site note"
        )

    element_tokens = _content_tokens(design.affected_element)
    if not element_tokens or not element_tokens.intersection(note_tokens):
        raise SiteFactVerificationError(
            f"Affected element '{design.affected_element}' is not supported by the site note"
        )

    for requirement in design.requirements:
        unit = _canonical_unit(requirement.unit)
        match_index = next(
            (
                index
                for index, (quantity, note_unit) in enumerate(unmatched_quantities)
                if abs(quantity - requirement.quantity) < 1e-9 and note_unit == unit
            ),
            None,
        )
        if match_index is None:
            raise SiteFactVerificationError(
                f"Requirement '{requirement.item_id}' uses quantity/unit "
                f"{requirement.quantity:g} {requirement.unit} not explicitly present in the site note"
            )
        unmatched_quantities.pop(match_index)

        material = requirement.material_type.value.casefold()
        aliases = _MATERIAL_ALIASES.get(material)
        if aliases is None or not aliases.intersection(note_tokens):
            raise SiteFactVerificationError(
                f"Requirement '{requirement.item_id}' uses material '{requirement.material_type.value}' "
                "not explicitly present in the site note"
            )

        specification = _normalized_text(requirement.specification)
        if specification not in _UNSPECIFIED_VALUES and specification not in normalized_note:
            raise SiteFactVerificationError(
                f"Requirement '{requirement.item_id}' uses specification "
                f"'{requirement.specification}' not explicitly present in the site note"
            )

    return [
        "Every design material, specification, quantity, and unit was verified against the site note.",
        "Retrieved technical evidence did not create additional procurement requirements.",
    ]


def format_evidence_context(evidence: Sequence[EvidenceRecord]) -> str:
    """Create a prompt context whose immutable chunk IDs can be cited by the model."""
    if not evidence:
        raise CitationVerificationError("No retrieved evidence was supplied")
    blocks = []
    for record in evidence:
        location = record.section or "Unsectioned content"
        if record.clause:
            location = f"{location}, clause {record.clause}"
        page = f"PDF p. {record.page_number}"
        if record.printed_page_label:
            page = f"printed p. {record.printed_page_label} ({page})"
        blocks.append(
            f"[EVIDENCE chunk_id={record.chunk_id}; document={record.document_code}; "
            f"edition={record.edition}; status={record.status}; jurisdiction={record.jurisdiction}; "
            f"location={location}; page={page}]\n{record.text}\nSource: {record.source_url}"
        )
    return "\n\n".join(blocks)


def validate_evidence_governance(evidence: Sequence[EvidenceRecord]) -> None:
    """Reject superseded or internally incompatible source versions before generation."""
    if not evidence:
        raise CitationVerificationError("No retrieved evidence was supplied")
    versions: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in evidence:
        if record.status.casefold() != "current":
            raise EvidenceConflictError(
                f"Evidence chunk {record.chunk_id} has non-current status '{record.status}'"
            )
        versions[record.document_code].add((record.edition, record.source_sha256))
    conflicting_codes = sorted(code for code, contracts in versions.items() if len(contracts) > 1)
    if conflicting_codes:
        raise EvidenceConflictError(
            "Multiple incompatible source versions were retrieved for document code(s): "
            + ", ".join(conflicting_codes)
        )


def align_citation_quotes(
    response: GroundedDesignResponse,
    evidence: Sequence[EvidenceRecord],
) -> tuple[GroundedDesignResponse, list[str]]:
    """Replace non-verbatim model quotes with the best exact sentence from the cited chunk.

    Alignment is intentionally narrow: the model must cite an available chunk, the exact source
    sentence must support at least the normal claim-token threshold, and every claim number must
    occur in that sentence. The strict verifier still runs afterwards.
    """
    available = {record.chunk_id: record for record in evidence}
    aligned_claims = []
    aligned_count = 0

    for claim in response.claims:
        claim_tokens = _content_tokens(claim.claim_text)
        claim_numbers = set(_NUMBER_RE.findall(claim.claim_text.casefold()))
        aligned_citations = []
        for citation in claim.citations:
            record = available.get(citation.chunk_id)
            if record is None or _normalized_text(citation.evidence_quote) in _normalized_text(
                record.text
            ):
                aligned_citations.append(citation)
                continue

            candidates = [
                candidate.strip()
                for candidate in re.split(r"(?<=[.!?;:])\s+|[\r\n]+", record.text)
                if 12 <= len(candidate.strip()) <= 600
            ]
            supported: list[tuple[float, int, str]] = []
            for candidate in candidates:
                candidate_tokens = _content_tokens(candidate)
                coverage = len(claim_tokens & candidate_tokens) / max(1, len(claim_tokens))
                candidate_numbers = set(_NUMBER_RE.findall(candidate.casefold()))
                if coverage >= MINIMUM_CLAIM_TOKEN_COVERAGE and claim_numbers <= candidate_numbers:
                    supported.append((coverage, -len(candidate), candidate))

            if not supported:
                aligned_citations.append(citation)
                continue

            exact_quote = max(supported)[2]
            aligned_citations.append(citation.model_copy(update={"evidence_quote": exact_quote}))
            aligned_count += 1

        aligned_claims.append(claim.model_copy(update={"citations": aligned_citations}))

    notes = []
    if aligned_count:
        notes.append(
            f"{aligned_count} model citation quote(s) were deterministically aligned to exact "
            "sentences in their cited chunks before strict verification."
        )
    return response.model_copy(update={"claims": aligned_claims}), notes


def verify_grounded_response(
    response: GroundedDesignResponse,
    evidence: Sequence[EvidenceRecord],
) -> GroundingVerification:
    """Verify IDs, exact quotes, lexical support, numeric support, and source governance."""
    if response.evidence_status != "SUPPORTED":
        raise GroundingRefusalError(response.evidence_status, response.reason)
    validate_evidence_governance(evidence)
    available = {record.chunk_id: record for record in evidence}
    verified_citations = 0
    cited_chunk_ids: list[str] = []

    for claim in response.claims:
        claim_tokens = _content_tokens(claim.claim_text)
        claim_numbers = set(_NUMBER_RE.findall(claim.claim_text.casefold()))
        quote_tokens: set[str] = set()
        quote_numbers: set[str] = set()
        for citation in claim.citations:
            record = available.get(citation.chunk_id)
            if record is None:
                raise CitationVerificationError(
                    f"Claim {claim.claim_id} cites unavailable chunk '{citation.chunk_id}'"
                )
            normalized_quote = _normalized_text(citation.evidence_quote)
            if normalized_quote not in _normalized_text(record.text):
                raise CitationVerificationError(
                    f"Claim {claim.claim_id} contains a quote not found in chunk '{citation.chunk_id}'"
                )
            quote_tokens.update(_content_tokens(citation.evidence_quote))
            quote_numbers.update(_NUMBER_RE.findall(citation.evidence_quote.casefold()))
            if citation.chunk_id not in cited_chunk_ids:
                cited_chunk_ids.append(citation.chunk_id)
            verified_citations += 1

        coverage = len(claim_tokens & quote_tokens) / max(1, len(claim_tokens))
        if coverage < MINIMUM_CLAIM_TOKEN_COVERAGE:
            raise CitationVerificationError(
                f"Claim {claim.claim_id} has insufficient textual support ({coverage:.1%})"
            )
        unsupported_numbers = sorted(claim_numbers - quote_numbers)
        if unsupported_numbers:
            raise CitationVerificationError(
                f"Claim {claim.claim_id} introduces unsupported numeric value(s): "
                + ", ".join(unsupported_numbers)
            )

    return GroundingVerification(
        verified_claim_count=len(response.claims),
        verified_citation_count=verified_citations,
        cited_chunk_ids=cited_chunk_ids,
        notes=[
            "Every cited chunk ID was present in the retrieved evidence set.",
            "Every evidence quote was verified as verbatim source text.",
            "Claim token coverage and numeric-value support passed deterministic checks.",
            "All cited document versions were current and governance-compatible.",
        ],
    )
