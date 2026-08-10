"""Local authentication, authorization, session, and account-governance controls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from audit import append_audit_event
from database import DB_NAME, connect_db, init_db, transaction


ROLE_PREPARER: Final = "PREPARER"
ROLE_DESIGN_REVIEWER: Final = "DESIGN_REVIEWER"
ROLE_PROJECT_MANAGER: Final = "PROJECT_MANAGER"
ROLE_ADMIN: Final = "ADMIN"
ROLES: Final = {
    ROLE_PREPARER,
    ROLE_DESIGN_REVIEWER,
    ROLE_PROJECT_MANAGER,
    ROLE_ADMIN,
}

PERMISSION_CREATE_PACKAGE: Final = "PACKAGE_CREATE"
PERMISSION_DECIDE_PACKAGE: Final = "PACKAGE_DECIDE"
PERMISSION_VIEW_AUDIT: Final = "AUDIT_VIEW"
PERMISSION_MANAGE_USERS: Final = "USERS_MANAGE"
ROLE_PERMISSIONS: Final = {
    ROLE_PREPARER: {PERMISSION_CREATE_PACKAGE, PERMISSION_VIEW_AUDIT},
    ROLE_DESIGN_REVIEWER: {PERMISSION_DECIDE_PACKAGE, PERMISSION_VIEW_AUDIT},
    ROLE_PROJECT_MANAGER: {PERMISSION_DECIDE_PACKAGE, PERMISSION_VIEW_AUDIT},
    ROLE_ADMIN: {PERMISSION_MANAGE_USERS, PERMISSION_VIEW_AUDIT},
}

SCRYPT_N: Final = 2**15
SCRYPT_R: Final = 8
SCRYPT_P: Final = 3
SCRYPT_DKLEN: Final = 64
SCRYPT_MAXMEM: Final = 128 * 1024 * 1024
SCRYPT_MIN_N: Final = 2**14
SCRYPT_MAX_N: Final = 2**18
SCRYPT_MIN_MAXMEM: Final = 32 * 1024 * 1024
SCRYPT_MAX_MAXMEM: Final = 256 * 1024 * 1024
PASSWORD_MIN_LENGTH: Final = 12
PASSWORD_MAX_LENGTH: Final = 128
MAX_FAILED_ATTEMPTS: Final = 5
LOCKOUT_MINUTES: Final = 15
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
GENERIC_LOGIN_ERROR = "Invalid credentials or account unavailable"


class SecurityError(RuntimeError):
    """Base class for authentication and authorization failures."""


class AuthenticationError(SecurityError):
    pass


class AuthorizationError(SecurityError):
    pass


class UserManagementError(SecurityError):
    pass


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: str
    username: str
    display_name: str
    role: str

    def model_dump(self) -> dict[str, str]:
        return asdict(self)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise UserManagementError(
            "Username must be 3-64 characters using lowercase letters, numbers, '.', '_' or '-'"
        )
    return normalized


def validate_password(password: str, *, username: str = "") -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise UserManagementError(
            f"Password must contain at least {PASSWORD_MIN_LENGTH} characters"
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise UserManagementError(
            f"Password must contain no more than {PASSWORD_MAX_LENGTH} characters"
        )
    if username and password.casefold() == username.casefold():
        raise UserManagementError("Password must not match the username")


def _derive_password(password: str, salt: bytes, parameters: dict[str, int]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=parameters["n"],
        r=parameters["r"],
        p=parameters["p"],
        maxmem=parameters.get("maxmem", SCRYPT_MAXMEM),
        dklen=parameters["dklen"],
    )


def _validated_scrypt_parameters(value: Any) -> dict[str, int]:
    """Reject corrupted or hostile stored work factors before allocating memory."""
    if not isinstance(value, dict):
        raise ValueError("Invalid scrypt parameters")
    required = {"n", "r", "p", "dklen", "maxmem"}
    if set(value) != required:
        raise ValueError("Invalid scrypt parameter contract")
    if any(isinstance(value[key], bool) or not isinstance(value[key], int) for key in required):
        raise ValueError("Scrypt parameters must be integers")

    parameters = {key: int(value[key]) for key in required}
    n = parameters["n"]
    if n < SCRYPT_MIN_N or n > SCRYPT_MAX_N or n & (n - 1):
        raise ValueError("Invalid scrypt N parameter")
    if not 8 <= parameters["r"] <= 32:
        raise ValueError("Invalid scrypt r parameter")
    if not 1 <= parameters["p"] <= 10:
        raise ValueError("Invalid scrypt p parameter")
    if not 32 <= parameters["dklen"] <= 64:
        raise ValueError("Invalid scrypt output length")
    if not SCRYPT_MIN_MAXMEM <= parameters["maxmem"] <= SCRYPT_MAX_MAXMEM:
        raise ValueError("Invalid scrypt memory limit")
    return parameters


def hash_password(password: str, *, username: str = "") -> dict[str, str]:
    validate_password(password, username=username)
    salt = secrets.token_bytes(16)
    parameters = {
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "dklen": SCRYPT_DKLEN,
        "maxmem": SCRYPT_MAXMEM,
    }
    derived = _derive_password(password, salt, parameters)
    return {
        "password_algorithm": "scrypt",
        "password_salt": base64.b64encode(salt).decode("ascii"),
        "password_hash": base64.b64encode(derived).decode("ascii"),
        "password_parameters_json": json.dumps(parameters, sort_keys=True),
    }


def verify_password(password: str, user_row: dict[str, Any] | sqlite3.Row) -> bool:
    if user_row["password_algorithm"] != "scrypt":
        return False
    try:
        salt = base64.b64decode(user_row["password_salt"], validate=True)
        expected = base64.b64decode(user_row["password_hash"], validate=True)
        parameters = _validated_scrypt_parameters(
            json.loads(user_row["password_parameters_json"])
        )
        if len(salt) != 16 or len(expected) != parameters["dklen"]:
            return False
        actual = _derive_password(password, salt, parameters)
    except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return False
    return hmac.compare_digest(actual, expected)


def _dummy_password_check(password: str) -> None:
    parameters = {
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "dklen": SCRYPT_DKLEN,
        "maxmem": SCRYPT_MAXMEM,
    }
    _derive_password(password, b"construction-demo", parameters)


def _principal_from_row(row: dict[str, Any] | sqlite3.Row) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
    )


def principal_from_mapping(value: dict[str, str]) -> AuthenticatedPrincipal:
    principal = AuthenticatedPrincipal(**value)
    if principal.role not in ROLES:
        raise AuthenticationError(GENERIC_LOGIN_ERROR)
    return principal


def create_user(
    username: str,
    display_name: str,
    role: str,
    password: str,
    *,
    db_path: str | Path = DB_NAME,
    actor_user_id: str | None = None,
) -> AuthenticatedPrincipal:
    init_db(db_path)
    normalized = normalize_username(username)
    clean_display_name = display_name.strip()
    if len(clean_display_name) < 2 or len(clean_display_name) > 120:
        raise UserManagementError("Display name must contain 2-120 characters")
    if role not in ROLES:
        raise UserManagementError(f"Unsupported role: {role}")
    password_data = hash_password(password, username=normalized)
    user_id = str(uuid4())
    now = _timestamp(utc_now())
    try:
        with transaction(db_path) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, role, password_algorithm,
                    password_salt, password_hash, password_parameters_json,
                    is_active, created_at, updated_at, password_changed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized,
                    clean_display_name,
                    role,
                    password_data["password_algorithm"],
                    password_data["password_salt"],
                    password_data["password_hash"],
                    password_data["password_parameters_json"],
                    now,
                    now,
                    now,
                ),
            )
            append_audit_event(
                connection,
                actor_user_id=actor_user_id,
                event_type="USER_CREATED",
                target_type="USER",
                target_id=user_id,
                outcome="SUCCESS",
                occurred_at=now,
                details={"username": normalized, "role": role, "source": "LOCAL_CLI"},
            )
    except sqlite3.IntegrityError as error:
        raise UserManagementError("Username already exists") from error
    return AuthenticatedPrincipal(user_id, normalized, clean_display_name, role)


def count_users(*, db_path: str | Path = DB_NAME) -> int:
    init_db(db_path)
    connection = connect_db(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()


def list_users(*, db_path: str | Path = DB_NAME) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect_db(db_path)
    try:
        rows = connection.execute(
            """
            SELECT user_id, username, display_name, role, is_active, failed_attempts,
                   locked_until, last_login_at, created_at, updated_at
            FROM users ORDER BY username
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def set_user_active(
    username: str,
    is_active: bool,
    *,
    db_path: str | Path = DB_NAME,
    actor_user_id: str | None = None,
) -> None:
    init_db(db_path)
    normalized = normalize_username(username)
    now = _timestamp(utc_now())
    with transaction(db_path) as connection:
        row = connection.execute(
            "SELECT user_id, role, is_active FROM users WHERE username = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
        if row is None:
            raise UserManagementError("User was not found")
        if not is_active and row["role"] == ROLE_ADMIN and row["is_active"]:
            active_admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND is_active = 1"
            ).fetchone()[0]
            if active_admins <= 1:
                raise UserManagementError("Cannot deactivate the final active administrator")
        connection.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE user_id = ?",
            (int(is_active), now, row["user_id"]),
        )
        append_audit_event(
            connection,
            actor_user_id=actor_user_id,
            event_type="USER_ACTIVATED" if is_active else "USER_DEACTIVATED",
            target_type="USER",
            target_id=row["user_id"],
            outcome="SUCCESS",
            occurred_at=now,
            details={"username": normalized, "source": "LOCAL_CLI"},
        )


def reset_user_password(
    username: str,
    new_password: str,
    *,
    db_path: str | Path = DB_NAME,
    actor_user_id: str | None = None,
) -> None:
    init_db(db_path)
    normalized = normalize_username(username)
    password_data = hash_password(new_password, username=normalized)
    now = _timestamp(utc_now())
    with transaction(db_path) as connection:
        row = connection.execute(
            "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE",
            (normalized,),
        ).fetchone()
        if row is None:
            raise UserManagementError("User was not found")
        connection.execute(
            """
            UPDATE users
            SET password_algorithm = ?, password_salt = ?, password_hash = ?,
                password_parameters_json = ?, failed_attempts = 0, locked_until = NULL,
                password_changed_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (
                password_data["password_algorithm"],
                password_data["password_salt"],
                password_data["password_hash"],
                password_data["password_parameters_json"],
                now,
                now,
                row["user_id"],
            ),
        )
        append_audit_event(
            connection,
            actor_user_id=actor_user_id,
            event_type="PASSWORD_RESET",
            target_type="USER",
            target_id=row["user_id"],
            outcome="SUCCESS",
            occurred_at=now,
            details={"username": normalized, "source": "LOCAL_CLI"},
        )


def _authenticate(
    *,
    username: str | None = None,
    user_id: str | None = None,
    password: str,
    event_prefix: str,
    db_path: str | Path,
    now: datetime | None = None,
) -> AuthenticatedPrincipal:
    init_db(db_path)
    current_time = now or utc_now()
    now_text = _timestamp(current_time)
    principal: AuthenticatedPrincipal | None = None
    failure = False
    with transaction(db_path) as connection:
        if username is not None:
            try:
                normalized = normalize_username(username)
            except UserManagementError:
                normalized = username.strip().casefold()[:64]
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
            target_id = row["user_id"] if row is not None else normalized or "<empty>"
        else:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            target_id = user_id or "<missing>"

        locked_until = _parse_timestamp(row["locked_until"]) if row is not None else None
        is_locked = locked_until is not None and locked_until > current_time
        if row is None or not row["is_active"] or is_locked:
            _dummy_password_check(password)
            failure = True
            reason = "ACCOUNT_UNAVAILABLE"
        elif not verify_password(password, row):
            failed_attempts = row["failed_attempts"] + 1
            new_locked_until = None
            if failed_attempts >= MAX_FAILED_ATTEMPTS:
                new_locked_until = _timestamp(
                    current_time + timedelta(minutes=LOCKOUT_MINUTES)
                )
            connection.execute(
                """
                UPDATE users
                SET failed_attempts = ?, locked_until = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (failed_attempts, new_locked_until, now_text, row["user_id"]),
            )
            failure = True
            reason = "INVALID_CREDENTIALS"
        else:
            connection.execute(
                """
                UPDATE users
                SET failed_attempts = 0, locked_until = NULL, last_login_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (now_text, now_text, row["user_id"]),
            )
            principal = _principal_from_row(row)
            reason = "VERIFIED"

        append_audit_event(
            connection,
            actor_user_id=principal.user_id if principal else None,
            event_type=f"{event_prefix}_{'SUCCESS' if principal else 'FAILED'}",
            target_type="USER",
            target_id=target_id,
            outcome="SUCCESS" if principal else "DENIED",
            occurred_at=now_text,
            details={"reason_code": reason},
        )

    if failure or principal is None:
        raise AuthenticationError(GENERIC_LOGIN_ERROR)
    return principal


def authenticate_user(
    username: str,
    password: str,
    *,
    db_path: str | Path = DB_NAME,
    now: datetime | None = None,
) -> AuthenticatedPrincipal:
    return _authenticate(
        username=username,
        password=password,
        event_prefix="LOGIN",
        db_path=db_path,
        now=now,
    )


def reauthenticate_user(
    principal: AuthenticatedPrincipal,
    password: str,
    *,
    db_path: str | Path = DB_NAME,
    now: datetime | None = None,
) -> AuthenticatedPrincipal:
    verified = _authenticate(
        user_id=principal.user_id,
        password=password,
        event_prefix="REAUTH",
        db_path=db_path,
        now=now,
    )
    if verified != principal:
        raise AuthenticationError(GENERIC_LOGIN_ERROR)
    return verified


def get_active_principal(
    user_id: str,
    *,
    db_path: str | Path = DB_NAME,
) -> AuthenticatedPrincipal | None:
    init_db(db_path)
    connection = connect_db(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM users WHERE user_id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        return _principal_from_row(row) if row is not None else None
    finally:
        connection.close()


def require_permission(principal: AuthenticatedPrincipal, permission: str) -> None:
    if permission not in ROLE_PERMISSIONS.get(principal.role, set()):
        raise AuthorizationError(
            f"Role {principal.role} is not authorized for permission {permission}"
        )


def has_permission(principal: AuthenticatedPrincipal, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(principal.role, set())


def authorize_principal(
    principal: AuthenticatedPrincipal,
    permission: str,
    *,
    db_path: str | Path = DB_NAME,
) -> AuthenticatedPrincipal:
    current = get_active_principal(principal.user_id, db_path=db_path)
    if current is None or current != principal:
        raise AuthenticationError(GENERIC_LOGIN_ERROR)
    require_permission(current, permission)
    return current


def ensure_separation_of_duties(
    preparer_user_id: str | None,
    reviewer: AuthenticatedPrincipal,
) -> None:
    if not preparer_user_id:
        raise AuthorizationError(
            "Legacy review package has no authenticated preparer; recreate the package"
        )
    if preparer_user_id == reviewer.user_id:
        raise AuthorizationError("The package preparer cannot approve or reject their own package")


def session_is_expired(
    authenticated_at: datetime,
    last_activity_at: datetime,
    *,
    now: datetime | None = None,
    idle_timeout: timedelta = timedelta(minutes=30),
    absolute_timeout: timedelta = timedelta(hours=8),
) -> bool:
    current = now or utc_now()
    return (
        current - last_activity_at > idle_timeout
        or current - authenticated_at > absolute_timeout
    )


def record_logout(
    principal: AuthenticatedPrincipal,
    *,
    reason: str = "USER_REQUEST",
    db_path: str | Path = DB_NAME,
) -> None:
    now = _timestamp(utc_now())
    with transaction(db_path) as connection:
        append_audit_event(
            connection,
            actor_user_id=principal.user_id,
            event_type="LOGOUT",
            target_type="SESSION",
            target_id=principal.user_id,
            outcome="SUCCESS",
            occurred_at=now,
            details={"reason_code": reason},
        )
