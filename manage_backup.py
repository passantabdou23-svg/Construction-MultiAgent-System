"""Create, validate, and restore authenticated-workflow SQLite backups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_ops import (
    ReleaseOperationError,
    create_database_backup,
    result_as_dict,
    restore_database_backup,
    validate_database_backup,
)
from settings import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage verified SQLite release backups")
    parser.add_argument("--database", default=settings.database_path)
    subcommands = parser.add_subparsers(dest="command", required=True)

    create = subcommands.add_parser("create", help="Create a consistent online backup")
    create.add_argument("--output-directory", default="backups")
    create.add_argument("--label", default="backup")

    validate = subcommands.add_parser("validate", help="Validate a backup and manifest")
    validate.add_argument("--backup", required=True)

    restore = subcommands.add_parser("restore", help="Restore a validated backup")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--recovery-directory", default="backups/recovery")
    restore.add_argument(
        "--confirm-replace",
        action="store_true",
        help="Required acknowledgement that the destination database will be replaced",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "create":
            result = create_database_backup(
                arguments.database,
                arguments.output_directory,
                label=arguments.label,
            )
            payload = result_as_dict(result)
        elif arguments.command == "validate":
            payload = result_as_dict(validate_database_backup(arguments.backup))
        else:
            if not arguments.confirm_replace:
                raise ReleaseOperationError(
                    "Restore requires --confirm-replace after Streamlit and database users are stopped"
                )
            result = restore_database_backup(
                arguments.backup,
                arguments.database,
                recovery_directory=arguments.recovery_directory,
            )
            payload = result_as_dict(result)
    except ReleaseOperationError as error:
        print(f"ERROR: {error}")
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
