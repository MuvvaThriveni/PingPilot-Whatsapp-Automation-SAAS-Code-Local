"""
run_migration.py  –  Execute migration_chatbot_redesign.sql against Neon DB
Usage:  python run_migration.py
"""
import os
import sys
import pathlib
import psycopg2

# ── Load .env manually (no python-dotenv needed) ─────────────────────────────
env_path = pathlib.Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌  DATABASE_URL not found in .env — aborting.")
    sys.exit(1)

# ── Read SQL file ─────────────────────────────────────────────────────────────
sql_path = pathlib.Path(__file__).parent / "migration_chatbot_redesign.sql"
if not sql_path.exists():
    print(f"❌  SQL file not found: {sql_path}")
    sys.exit(1)

sql = sql_path.read_text(encoding="utf-8")

# ── Run migration ─────────────────────────────────────────────────────────────
print("[*] Connecting to Neon DB ...")
try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False          # wrap everything in one transaction
    cur = conn.cursor()

    print(f"[>] Running {sql_path.name} ...")
    cur.execute(sql)
    conn.commit()

    print("[OK] Migration completed successfully!")

except Exception as e:
    if 'conn' in locals():
        conn.rollback()
    print(f"[FAIL] Migration FAILED - rolled back.\n    Error: {e}")
    sys.exit(1)

finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
