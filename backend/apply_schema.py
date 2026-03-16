import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        print(f"schema.sql not found at: {schema_path}", file=sys.stderr)
        return 2

    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    if not sql.strip():
        print("schema.sql is empty", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print("Schema applied successfully")
        return 0
    except Exception as e:
        print(f"Failed to apply schema: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
