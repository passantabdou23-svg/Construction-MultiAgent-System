import sqlite3

# Connect to your local database
conn = sqlite3.connect("construction_mas.db")
cursor = conn.cursor()

# Get table names to make sure we hit the right one
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print(f"Tables in database: {tables}")

# Check records in design_revisions
try:
    cursor.execute("SELECT * FROM design_revisions")
    rows = cursor.fetchall()

    print("\n--- DESIGN REVISIONS RECORDS ---")
    if not rows:
        print("No records found in design_revisions!")
    else:
        for row in rows:
            print(row)
except sqlite3.OperationalError as e:
    print(f"\nError reading table: {e}")

conn.close()