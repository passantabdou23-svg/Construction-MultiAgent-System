import tempfile
import unittest
from pathlib import Path

from release_check import run_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReleaseCheckTests(unittest.TestCase):
    def test_ci_release_contract_passes_with_fresh_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "fresh-release.db"
            results = run_checks(PROJECT_ROOT, database_path)

        failures = [result for result in results if not result.passed]
        self.assertEqual(failures, [], failures)
        self.assertTrue(any(result.name == "database" for result in results))
        self.assertTrue(any(result.name == "controlled_sources" for result in results))


if __name__ == "__main__":
    unittest.main()
