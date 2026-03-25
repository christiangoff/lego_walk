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
    ("Add friendship table", """CREATE TABLE IF NOT EXISTS friendship (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER NOT NULL REFERENCES user(id),
    addressee_id INTEGER NOT NULL REFERENCES user(id),
    status VARCHAR(16) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)"""),
    ("Add high_five table", """CREATE TABLE IF NOT EXISTS high_five (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES user(id),
    to_user_id INTEGER NOT NULL REFERENCES user(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)"""),
    ("Add password_reset_token table", """CREATE TABLE IF NOT EXISTS password_reset_token (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token VARCHAR(64) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES user(id),
    expires_at DATETIME NOT NULL,
    used BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)"""),
    ("Add location to profile", "ALTER TABLE profile ADD COLUMN location VARCHAR(100)"),
    ("Add avatar_filename to profile", "ALTER TABLE profile ADD COLUMN avatar_filename VARCHAR(255)"),
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

        ADMIN_EMAIL = "christian.goff@gmail.com"
        ADMIN_NAME = "Christian"
        ADMIN_TEMP_PASSWORD = "studstep123"

        # Ensure the admin account exists and is marked as admin
        with db.engine.connect() as conn:
            from werkzeug.security import generate_password_hash
            admin_row = conn.execute(
                text("SELECT id FROM user WHERE email = :email"), {"email": ADMIN_EMAIL}
            ).fetchone()

            if not admin_row:
                conn.execute(text("""
                    INSERT INTO user (email, display_name, password_hash, is_admin)
                    VALUES (:email, :name, :hash, 1)
                """), {
                    "email": ADMIN_EMAIL,
                    "name": ADMIN_NAME,
                    "hash": generate_password_hash(ADMIN_TEMP_PASSWORD, method="pbkdf2:sha256"),
                })
                conn.commit()
                print(f"  [**] Admin account created: {ADMIN_EMAIL} / {ADMIN_TEMP_PASSWORD}")
            else:
                conn.execute(text(
                    "UPDATE user SET is_admin = 1 WHERE email = :email"
                ), {"email": ADMIN_EMAIL})
                conn.commit()
                print(f"  [OK] Admin flag set for {ADMIN_EMAIL}")

            # Get admin user id
            admin_row = conn.execute(
                text("SELECT id FROM user WHERE email = :email"), {"email": ADMIN_EMAIL}
            ).fetchone()
            uid = admin_row[0]

            # Assign any unowned data to the admin account
            for table in ("profile", "weight_log", "lego_set", "session"):
                conn.execute(text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"), {"uid": uid})
            conn.commit()
            print(f"  [OK] Unowned data assigned to {ADMIN_EMAIL} (id={uid})")

        print("Migration complete.")


if __name__ == "__main__":
    print("Running migrations...")
    run_migrations()
