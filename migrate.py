"""
Database migration script.
Run this after pulling updates to apply any schema changes safely.
Usage: python3 migrate.py
"""
from app import app, db
from sqlalchemy import text

MIGRATIONS = [
    # (description, SQL statement)
    ("Add total_bag_count to lego_set",
     "ALTER TABLE lego_set ADD COLUMN total_bag_count INTEGER"),
    ("Add user table", """CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
)"""),
    ("Add user_id to profile", "ALTER TABLE profile ADD COLUMN user_id INTEGER REFERENCES user(id)"),
    ("Add user_id to weight_log", "ALTER TABLE weight_log ADD COLUMN user_id INTEGER REFERENCES user(id)"),
    ("Add user_id to lego_set", "ALTER TABLE lego_set ADD COLUMN user_id INTEGER REFERENCES user(id)"),
    ("Add user_id to session", "ALTER TABLE session ADD COLUMN user_id INTEGER REFERENCES user(id)"),
    ("Add is_admin to user", "ALTER TABLE user ADD COLUMN is_admin BOOLEAN DEFAULT 0"),
    ("Add invite_code table", """CREATE TABLE IF NOT EXISTS invite_code (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(32) NOT NULL UNIQUE,
    created_by_id INTEGER NOT NULL REFERENCES user(id),
    used_by_id INTEGER REFERENCES user(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    used_at DATETIME
)"""),
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

        # After migrations, assign existing data to a default user
        with db.engine.connect() as conn:
            existing = conn.execute(text("SELECT id FROM user LIMIT 1")).fetchone()
            if not existing:
                from werkzeug.security import generate_password_hash
                conn.execute(text("""
                    INSERT INTO user (email, display_name, password_hash)
                    VALUES ('admin@studstep.local', 'Admin', :hash)
                """), {"hash": generate_password_hash("studstep123", method="pbkdf2:sha256")})
                conn.commit()
                print("  [**] Default user created: admin@studstep.local / studstep123")

            user_row = conn.execute(text("SELECT id FROM user LIMIT 1")).fetchone()
            uid = user_row[0]
            for table in ("profile", "weight_log", "lego_set", "session"):
                conn.execute(text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"), {"uid": uid})
            conn.commit()
            print(f"  [OK] Existing data assigned to user id={uid}")

        # Ensure the designated admin account has is_admin=1
        with db.engine.connect() as conn:
            conn.execute(text(
                "UPDATE user SET is_admin = 1 WHERE email = 'christian.goff@gmail.com'"
            ))
            conn.commit()
            print("  [OK] Admin flag set for christian.goff@gmail.com")

        print("Migration complete.")


if __name__ == "__main__":
    print("Running migrations...")
    run_migrations()
