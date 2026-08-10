import unittest

from generate_acceptance_evidence import build_markdown


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_markdown_reports_scope_and_aggregate_evidence(self):
        payload = {
            "overall_passed": True,
            "commit": "abc123",
            "generated_at_utc": "2026-08-10T00:00:00+00:00",
            "python": "3.12.0",
            "release_checks": [{"name": "python", "passed": True, "detail": "Python 3.12.0"}],
            "repository_tests": {"tests_run": 72, "failures": 0, "errors": 0, "passed": True},
            "stress_tests": {"tests_run": 3, "failures": 0, "errors": 0, "passed": True},
            "retrieval": {
                "top_k": 5,
                "threshold": 0.45,
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "metrics": {
                    "hit_at_k": 1.0,
                    "top_1_accuracy": 1.0,
                    "mean_reciprocal_rank": 1.0,
                    "routing_accuracy": 1.0,
                    "positive_acceptance_rate": 1.0,
                    "negative_rejection_rate": 1.0,
                },
            },
            "grounding": {"metrics": {"supported_acceptance_rate": 1.0, "guard_rejection_rate": 1.0}},
            "database": {
                "counts": {
                    "active_users": 3,
                    "revisions": 1,
                    "materials": 1,
                    "quotes": 1,
                    "schedule_impacts": 1,
                    "pending_approvals": 0,
                },
                "audit_valid": True,
                "audit_event_count": 18,
                "audit_head_hash": "deadbeef",
            },
            "backup": {"sha256": "abcdef", "size_bytes": 1024},
        }

        report = build_markdown(payload)

        self.assertIn("**Status:** PASS", report)
        self.assertIn("72 passed", report)
        self.assertIn("labelled controlled evaluation sets only", report)
        self.assertIn("No MFA", report)
        self.assertNotIn("site update rev-", report.lower())
        self.assertNotIn("local administrator", report.lower())


if __name__ == "__main__":
    unittest.main()
