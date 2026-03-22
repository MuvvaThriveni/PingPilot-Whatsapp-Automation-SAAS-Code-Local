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

    base_dir = os.path.dirname(__file__)
    schema_files = ["schema.sql", "retention_schema.sql"]

    for schema_file in schema_files:
        schema_path = os.path.join(base_dir, schema_file)
        if not os.path.exists(schema_path):
            if schema_file == "schema.sql":
                print(f"{schema_file} not found at: {schema_path}", file=sys.stderr)
                return 2
            # retention_schema.sql is optional during transition
            print(f"[SKIP] {schema_file} not found — skipping")
            continue

        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()

        if not sql.strip():
            if schema_file == "schema.sql":
                print("schema.sql is empty", file=sys.stderr)
                return 2
            continue

        try:
            with psycopg.connect(database_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
            print(f"{schema_file} applied successfully")
        except Exception as e:
            print(f"Failed to apply {schema_file}: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
