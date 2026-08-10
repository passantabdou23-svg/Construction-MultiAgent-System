import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit import AuditIntegrityError, verify_audit_chain
from database import connect_db, fetch_table
from security import (
    LOCKOUT_MINUTES,
    MAX_FAILED_ATTEMPTS,
    PERMISSION_CREATE_PACKAGE,
    PERMISSION_DECIDE_PACKAGE,
    ROLE_ADMIN,
    ROLE_DESIGN_REVIEWER,
    ROLE_PREPARER,
    AuthenticationError,
    AuthorizationError,
    UserManagementError,
    authenticate_user,
    create_user,
    require_permission,
    session_is_expired,
    set_user_active,
)


PASSWORD = "Correct-Horse-Battery-Security"


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "security.db"

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_passwords_use_unique_scrypt_salts_and_authenticate(self):
        first = create_user(
            "first.user",
            "First User",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.db_path,
        )
        create_user(
            "second.user",
            "Second User",
            ROLE_DESIGN_REVIEWER,
            PASSWORD,
            db_path=self.db_path,
        )
        users = fetch_table("users", db_path=self.db_path)
        self.assertEqual({row["password_algorithm"] for row in users}, {"scrypt"})
        self.assertEqual(len({row["password_salt"] for row in users}), 2)
        self.assertNotIn(PASSWORD, {row["password_hash"] for row in users})
        self.assertEqual(
            authenticate_user("FIRST.USER", PASSWORD, db_path=self.db_path),
            first,
        )

    def test_repeated_failures_lock_account_until_timeout(self):
        create_user(
            "locked.user",
            "Locked User",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.db_path,
        )
        start = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
        for attempt in range(MAX_FAILED_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                authenticate_user(
                    "locked.user",
                    f"Wrong-Password-{attempt}",
                    db_path=self.db_path,
                    now=start,
                )
        with self.assertRaises(AuthenticationError):
            authenticate_user(
                "locked.user",
                PASSWORD,
                db_path=self.db_path,
                now=start + timedelta(minutes=LOCKOUT_MINUTES - 1),
            )
        principal = authenticate_user(
            "locked.user",
            PASSWORD,
            db_path=self.db_path,
            now=start + timedelta(minutes=LOCKOUT_MINUTES + 1),
        )
        self.assertEqual(principal.username, "locked.user")

    def test_hostile_stored_scrypt_parameters_are_rejected_safely(self):
        create_user(
            "tampered.user",
            "Tampered User",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.db_path,
        )
        connection = connect_db(self.db_path)
        with connection:
            connection.execute(
                "UPDATE users SET password_parameters_json = ? WHERE username = ?",
                (
                    json.dumps(
                        {
                            "n": 2**30,
                            "r": 8,
                            "p": 3,
                            "dklen": 64,
                            "maxmem": 2**40,
                        }
                    ),
                    "tampered.user",
                ),
            )
        connection.close()

        with self.assertRaises(AuthenticationError):
            authenticate_user("tampered.user", PASSWORD, db_path=self.db_path)

    def test_role_permissions_are_server_side(self):
        preparer = create_user(
            "preparer",
            "Package Preparer",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.db_path,
        )
        reviewer = create_user(
            "reviewer",
            "Design Reviewer",
            ROLE_DESIGN_REVIEWER,
            PASSWORD,
            db_path=self.db_path,
        )
        require_permission(preparer, PERMISSION_CREATE_PACKAGE)
        require_permission(reviewer, PERMISSION_DECIDE_PACKAGE)
        with self.assertRaises(AuthorizationError):
            require_permission(preparer, PERMISSION_DECIDE_PACKAGE)
        with self.assertRaises(AuthorizationError):
            require_permission(reviewer, PERMISSION_CREATE_PACKAGE)

    def test_final_active_administrator_cannot_be_deactivated(self):
        create_user(
            "admin",
            "Local Administrator",
            ROLE_ADMIN,
            PASSWORD,
            db_path=self.db_path,
        )
        with self.assertRaises(UserManagementError):
            set_user_active("admin", False, db_path=self.db_path)

    def test_audit_chain_detects_modified_event(self):
        create_user(
            "audited.user",
            "Audited User",
            ROLE_PREPARER,
            PASSWORD,
            db_path=self.db_path,
        )
        report = verify_audit_chain(self.db_path)
        self.assertTrue(report["valid"])
        self.assertEqual(report["event_count"], 1)

        connection = connect_db(self.db_path)
        with connection:
            connection.execute(
                "UPDATE audit_events SET details_json = '{\"changed\":true}' WHERE sequence_number = 1"
            )
        connection.close()
        with self.assertRaises(AuditIntegrityError):
            verify_audit_chain(self.db_path)

    def test_idle_and_absolute_session_expiry(self):
        start = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        self.assertFalse(
            session_is_expired(
                start,
                start + timedelta(minutes=10),
                now=start + timedelta(minutes=20),
            )
        )
        self.assertTrue(
            session_is_expired(
                start,
                start + timedelta(minutes=10),
                now=start + timedelta(minutes=41),
            )
        )
        self.assertTrue(
            session_is_expired(
                start,
                start + timedelta(hours=7, minutes=50),
                now=start + timedelta(hours=8, minutes=1),
            )
        )


if __name__ == "__main__":
    unittest.main()
