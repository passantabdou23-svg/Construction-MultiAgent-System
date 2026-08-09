"""Deterministic stress tests for unsafe and ambiguous site-note inputs."""

import unittest

from validation import SiteNoteValidationError, validate_site_note


class StressInputValidationTests(unittest.TestCase):
    def test_missing_units_and_ambiguous_quantity_is_rejected(self):
        with self.assertRaises(SiteNoteValidationError):
            validate_site_note(
                "Site update Rev-201: Need some steel rebar for the top slab, maybe 50 or 60 pieces."
            )

    def test_contradictory_material_specification_is_rejected(self):
        with self.assertRaises(SiteNoteValidationError):
            validate_site_note(
                "Rev-999: Change 25 m3 concrete for foundation columns to C50/60; "
                "strike that and make it C80/90."
            )

    def test_irrelevant_social_message_is_rejected(self):
        with self.assertRaises(SiteNoteValidationError):
            validate_site_note(
                "Hey team, do not forget the pizza party in the site trailer this Friday at noon."
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
