"""Read-only command-line database health summary."""

from audit import verify_audit_chain
from database import TABLES, database_counts, fetch_table, init_db
from settings import settings


def main() -> None:
    init_db(settings.database_path)
    print("Database counts:")
    for label, count in database_counts(settings.database_path).items():
        print(f"- {label}: {count}")

    print("\nTables:")
    for table_name in sorted(TABLES):
        print(f"- {table_name}: {len(fetch_table(table_name, db_path=settings.database_path))} rows")

    integrity = verify_audit_chain(settings.database_path)
    print("\nAudit chain:")
    print(f"- valid: {integrity['valid']}")
    print(f"- events: {integrity['event_count']}")
    print(f"- head: {integrity['head_hash']}")


if __name__ == "__main__":
    main()
