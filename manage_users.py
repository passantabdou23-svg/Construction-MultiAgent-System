"""Interactive local account administration for the authenticated dashboard."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from database import DB_NAME
from security import (
    ROLES,
    UserManagementError,
    create_user,
    list_users,
    reset_user_password,
    set_user_active,
)


def _confirmed_password(prompt: str = "Password: ") -> str:
    password = getpass.getpass(prompt)
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise UserManagementError("Passwords do not match")
    return password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage local dashboard accounts. Shell access is treated as a trusted "
            "workstation-administrator boundary."
        )
    )
    parser.add_argument("--database", default=DB_NAME, help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a local account")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", required=True, choices=sorted(ROLES))

    commands.add_parser("list", help="List non-secret account metadata")

    deactivate = commands.add_parser("deactivate", help="Disable an account")
    deactivate.add_argument("--username", required=True)

    activate = commands.add_parser("activate", help="Enable an account")
    activate.add_argument("--username", required=True)

    reset = commands.add_parser("reset-password", help="Reset an account password")
    reset.add_argument("--username", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.database)
    try:
        if args.command == "create":
            principal = create_user(
                args.username,
                args.display_name,
                args.role,
                _confirmed_password(),
                db_path=db_path,
            )
            print(
                f"Created {principal.username} ({principal.display_name}) "
                f"with role {principal.role}."
            )
        elif args.command == "list":
            users = list_users(db_path=db_path)
            if not users:
                print("No local users exist.")
            for user in users:
                state = "ACTIVE" if user["is_active"] else "DISABLED"
                lock = f", locked until {user['locked_until']}" if user["locked_until"] else ""
                print(
                    f"{user['username']}: {user['display_name']} | {user['role']} | "
                    f"{state}{lock}"
                )
        elif args.command == "deactivate":
            set_user_active(args.username, False, db_path=db_path)
            print(f"Deactivated {args.username}.")
        elif args.command == "activate":
            set_user_active(args.username, True, db_path=db_path)
            print(f"Activated {args.username}.")
        elif args.command == "reset-password":
            reset_user_password(
                args.username,
                _confirmed_password("New password: "),
                db_path=db_path,
            )
            print(f"Password reset for {args.username}.")
    except UserManagementError as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
