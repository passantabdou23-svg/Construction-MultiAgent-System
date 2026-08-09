import unittest

from validation import SiteNoteValidationError, assess_site_note, validate_site_note


class SiteNoteValidationTests(unittest.TestCase):
    def test_valid_note_is_normalized(self):
        result = validate_site_note(
            "Site update rev 102: Need 150 m3 of C60 concrete for the column pour."
        )
        self.assertEqual(result.revision_id, "Rev-102")

    def test_empty_note_is_rejected_with_multiple_reasons(self):
        result, issues = assess_site_note("")
        self.assertIsNone(result)
        self.assertGreaterEqual(len(issues), 4)

    def test_note_without_revision_is_rejected(self):
        with self.assertRaisesRegex(SiteNoteValidationError, "revision ID"):
            validate_site_note("Need 25 m3 of C40 concrete for the ground slab.")

    def test_zero_quantity_is_rejected_before_agent_execution(self):
        with self.assertRaisesRegex(SiteNoteValidationError, "greater than zero"):
            validate_site_note("Rev-100: Need 0 m3 concrete for the foundation.")


if __name__ == "__main__":
    unittest.main()
