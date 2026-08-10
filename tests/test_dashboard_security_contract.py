import unittest
from pathlib import Path


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


class DashboardSecurityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_SOURCE.read_text(encoding="utf-8")

    def test_dashboard_uses_authenticated_identity_and_reauthentication(self):
        self.assertIn("authenticate_user(", self.source)
        self.assertIn("principal=principal", self.source)
        self.assertIn("reauthentication_password=reauthentication_password", self.source)
        self.assertNotIn('"Reviewer name"', self.source)

    def test_sensitive_password_columns_are_removed_before_display(self):
        self.assertIn('"password_hash"', self.source)
        self.assertIn('"password_salt"', self.source)
        self.assertIn("frame.drop(", self.source)

    def test_dashboard_avoids_unsafe_or_deprecated_rendering(self):
        self.assertNotIn("unsafe_allow_html", self.source)
        self.assertNotIn("use_container_width", self.source)


if __name__ == "__main__":
    unittest.main()
