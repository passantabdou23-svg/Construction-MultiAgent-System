import unittest
from dataclasses import replace

from grounding import (
    CitationVerificationError,
    EvidenceConflictError,
    GroundingRefusalError,
    SiteFactVerificationError,
    align_citation_quotes,
    format_evidence_context,
    verify_design_against_site_note,
    verify_grounded_response,
)
from rag_engine import RetrievedStandard
from schemas import GroundedDesignResponse, MaterialRequirement, MaterialType


def _evidence(**changes) -> RetrievedStandard:
    base = RetrievedStandard(
        chunk_id="foundation-1",
        document_id="approved-document-a",
        title="Approved Document A: Structure",
        edition="controlled test edition",
        jurisdiction="England",
        document_code="A",
        discipline="structure",
        authority="Test authority",
        status="current",
        source_checked_date="2026-08-09",
        effective_date="",
        page_number=38,
        printed_page_label="36",
        section="Foundations",
        clause="",
        text="Strip foundation width must safely distribute wall loads to the ground.",
        source_url="https://example.test/a",
        source_sha256="a" * 64,
        distance=0.18,
        similarity=0.82,
        semantic_similarity=0.78,
        lexical_similarity=0.94,
        routing_reason="Routed by keywords: foundation",
    )
    return replace(base, **changes)


def _response(**changes) -> GroundedDesignResponse:
    payload = {
        "evidence_status": "SUPPORTED",
        "reason": "The source passage supports a limited foundation guidance claim.",
        "design": {
            "revision_id": "Rev-101",
            "affected_element": "foundation",
            "requirements": [
                {
                    "item_id": "CONC-101",
                    "material_type": "Concrete",
                    "specification": "C40",
                    "quantity": 25,
                    "unit": "m3",
                }
            ],
        },
        "claims": [
            {
                "claim_id": "CLAIM-1",
                "claim_text": "Foundation width should safely distribute wall loads.",
                "citations": [
                    {
                        "chunk_id": "foundation-1",
                        "evidence_quote": "Strip foundation width must safely distribute wall loads to the ground.",
                    }
                ],
            }
        ],
    }
    payload.update(changes)
    return GroundedDesignResponse.model_validate(payload)


class GroundingVerificationTests(unittest.TestCase):
    def test_design_facts_are_verified_against_site_note(self):
        notes = verify_design_against_site_note(
            _response().design,
            "Site update Rev-101: Need 25 m3 of C40 concrete for the foundation pour.",
        )
        self.assertEqual(len(notes), 2)

    def test_evidence_cannot_create_additional_procurement_requirement(self):
        response = _response()
        response.design.requirements.append(
            MaterialRequirement(
                item_id="AGG-101",
                material_type=MaterialType.AGGREGATE,
                specification="BS EN 12620",
                quantity=0.2,
                unit="m3",
            )
        )
        with self.assertRaises(SiteFactVerificationError):
            verify_design_against_site_note(
                response.design,
                "Site update Rev-101: Need 25 m3 of C40 concrete for the foundation pour.",
            )

    def test_evidence_cannot_replace_note_material_or_specification(self):
        response = _response()
        response.design.requirements[0].material_type = MaterialType.CEMENT
        response.design.requirements[0].specification = "BS EN 197-1"
        with self.assertRaises(SiteFactVerificationError):
            verify_design_against_site_note(
                response.design,
                "Site update Rev-101: Need 25 m3 of C40 concrete for the foundation pour.",
            )

    def test_verified_claim_requires_available_verbatim_evidence(self):
        report = verify_grounded_response(_response(), (_evidence(),))
        self.assertEqual(report.status, "VERIFIED")
        self.assertEqual(report.verified_claim_count, 1)
        self.assertEqual(report.cited_chunk_ids, ["foundation-1"])

    def test_prompt_context_exposes_chunk_governance_and_text(self):
        context = format_evidence_context((_evidence(),))
        self.assertIn("chunk_id=foundation-1", context)
        self.assertIn("status=current", context)
        self.assertIn("PDF p. 38", context)
        self.assertIn("Strip foundation width", context)

    def test_unknown_chunk_id_is_rejected(self):
        response = _response()
        response.claims[0].citations[0].chunk_id = "missing-chunk"
        with self.assertRaises(CitationVerificationError):
            verify_grounded_response(response, (_evidence(),))

    def test_non_verbatim_quote_is_rejected(self):
        response = _response()
        response.claims[0].citations[0].evidence_quote = "This sentence was invented by the model."
        with self.assertRaises(CitationVerificationError):
            verify_grounded_response(response, (_evidence(),))

    def test_non_verbatim_model_quote_can_be_aligned_to_supported_exact_sentence(self):
        response = _response()
        response.claims[0].citations[0].evidence_quote = (
            "Foundation width can distribute the loads safely."
        )

        aligned, notes = align_citation_quotes(response, (_evidence(),))
        report = verify_grounded_response(aligned, (_evidence(),))

        self.assertEqual(report.status, "VERIFIED")
        self.assertEqual(
            aligned.claims[0].citations[0].evidence_quote,
            _evidence().text,
        )
        self.assertEqual(len(notes), 1)

    def test_quote_alignment_does_not_rescue_unsupported_number(self):
        response = _response()
        response.claims[0].claim_text = (
            "Foundation width must be at least 900mm to distribute wall loads."
        )
        response.claims[0].citations[0].evidence_quote = "Foundation guidance."

        aligned, notes = align_citation_quotes(response, (_evidence(),))

        self.assertEqual(notes, [])
        with self.assertRaises(CitationVerificationError):
            verify_grounded_response(aligned, (_evidence(),))

    def test_unsupported_numeric_claim_is_rejected(self):
        response = _response()
        response.claims[0].claim_text = "Foundation width must be at least 900mm to distribute wall loads."
        with self.assertRaises(CitationVerificationError):
            verify_grounded_response(response, (_evidence(),))

    def test_non_current_source_is_rejected(self):
        with self.assertRaises(EvidenceConflictError):
            verify_grounded_response(_response(), (_evidence(status="superseded"),))

    def test_two_versions_of_same_document_are_rejected(self):
        second = _evidence(
            chunk_id="foundation-2",
            edition="different edition",
            source_sha256="b" * 64,
        )
        with self.assertRaises(EvidenceConflictError):
            verify_grounded_response(_response(), (_evidence(), second))

    def test_model_refusal_is_propagated(self):
        refusal = GroundedDesignResponse(
            evidence_status="INSUFFICIENT_EVIDENCE",
            reason="The retrieved evidence does not address this technical question.",
        )
        with self.assertRaises(GroundingRefusalError):
            verify_grounded_response(refusal, (_evidence(),))


if __name__ == "__main__":
    unittest.main()
