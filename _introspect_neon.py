"""Read-only introspection of the live Neon schema.

Prints every public table with its columns, Postgres types and row count so the
MongoDB migration can be written against the real schema instead of guesses.
Nothing here writes to Neon.
"""
import os
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit("DATABASE_URL not set")

# Neon hands out channel_binding=require; older libpq builds reject the kwarg.
if "channel_binding" in url:
    url = url.split("channel_binding")[0].rstrip("&?")

conn = psycopg2.connect(url)
conn.set_session(readonly=True)

with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    tables = [r["table_name"] for r in cur.fetchall()]

    cur.execute("SELECT current_database(), version()")
    meta = cur.fetchone()
    print(f"database: {meta['current_database']}")
    print(f"server  : {meta['version'].split(',')[0]}")
    print(f"tables  : {len(tables)}\n")

    for t in tables:
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (t,))
        cols = cur.fetchall()

        cur.execute(f'SELECT COUNT(*) AS n FROM "{t}"')
        n = cur.fetchone()["n"]

        print(f"=== {t}  ({n} rows) ===")
        for c in cols:
            default = f"  default={c['column_default']}" if c["column_default"] else ""
            print(f"    {c['column_name']:<24} {c['data_type']:<28} "
                  f"{'NULL' if c['is_nullable'] == 'YES' else 'NOT NULL'}{default}")
        print()

conn.close()
