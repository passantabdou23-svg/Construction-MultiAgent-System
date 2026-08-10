import json
import tempfile
import unittest
from pathlib import Path

from database import database_counts
from release_ops import (
    ReleaseOperationError,
    create_database_backup,
    restore_database_backup,
    validate_database_backup,
)
from security import ROLE_PREPARER, create_user


PASSWORD = "Release-Backup-Test-Password"


class ReleaseOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "runtime.db"
        self.backup_directory = self.root / "backups"
        create_user(
            "first.user",
            "First User",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.database_path,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_online_backup_has_verified_manifest_and_audit_chain(self):
        result = create_database_backup(
            self.database_path,
            self.backup_directory,
            label="release",
        )
        backup = Path(result.backup_path)
        manifest = Path(result.manifest_path)

        self.assertTrue(backup.is_file())
        self.assertTrue(manifest.is_file())
        verified = validate_database_backup(backup)
        self.assertEqual(verified.audit_event_count, 1)
        self.assertEqual(verified.sha256, result.integrity.sha256)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["database_sha256"], verified.sha256)

    def test_tampered_manifest_is_rejected(self):
        result = create_database_backup(self.database_path, self.backup_directory)
        manifest_path = Path(result.manifest_path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["database_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ReleaseOperationError):
            validate_database_backup(result.backup_path)

    def test_restore_replaces_destination_and_preserves_pre_restore_backup(self):
        original = create_database_backup(self.database_path, self.backup_directory)
        create_user(
            "second.user",
            "Second User",
            ROLE_PREPARER,
            "Second-Release-Backup-Password",
            db_path=self.database_path,
        )
        self.assertEqual(database_counts(self.database_path)["active_users"], 2)

        result = restore_database_backup(
            original.backup_path,
            self.database_path,
            recovery_directory=self.root / "recovery",
        )

        self.assertEqual(database_counts(self.database_path)["active_users"], 1)
        self.assertIsNotNone(result.pre_restore_backup_path)
        self.assertTrue(Path(result.pre_restore_backup_path).is_file())
        self.assertEqual(result.integrity.audit_event_count, 1)


if __name__ == "__main__":
    unittest.main()
