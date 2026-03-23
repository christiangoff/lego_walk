"""
Database migration script.
Run this after pulling updates to apply any schema changes safely.
Usage: python3 migrate.py
"""
from app import app, db
from sqlalchemy import inspect, text

MIGRATIONS = [
    # (description, SQL statement)
    ("Add total_bag_count to lego_set",
     "ALTER TABLE lego_set ADD COLUMN total_bag_count INTEGER"),
]


def get_columns(conn, table):
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result}


def run_migrations():
    with app.app_context():
        db.create_all()  # Create any brand new tables

        with db.engine.connect() as conn:
            for description, sql in MIGRATIONS:
                try:
                    conn.execute(text(sql))
                    conn.commit()
                    print(f"  [OK] {description}")
                except Exception as e:
                    if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                        print(f"  [--] {description} (already applied)")
                    else:
                        print(f"  [!!] {description} FAILED: {e}")

        print("Migration complete.")


if __name__ == "__main__":
    print("Running migrations...")
    run_migrations()
